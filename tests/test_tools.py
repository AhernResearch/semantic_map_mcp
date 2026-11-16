import base64
import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image
from jsonschema import ValidationError

from sementic_map_mcp.agent import model_context, run_agent
from sementic_map_mcp.service import MapTools
from sementic_map_mcp.store import SceneStore, digest, write_json

ROOT = Path(__file__).resolve().parents[1]


class FixedEncoder:
    """Controlled vectors test retrieval math, not real language understanding."""
    space_id = 'test-space'
    fingerprint = 'test-weights'

    def encode(self, texts):
        return np.array([[1., 0.] for _ in texts])


class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scene = json.loads((ROOT / 'examples/scene.json').read_text())
        self.scene['rooms'].append(dict(self.scene['rooms'][0], room_id='R02'))
        prototype = self.scene['objects'][0]
        self.scene['objects'] = []
        for i, (room, valid) in enumerate([('R01', True), ('R01', True), ('R02', True), ('R01', False)]):
            item = copy.deepcopy(prototype)
            item.update(object_id=f'o{i}', source_id=f'o{i}', label=f'fixture-{i}', room_ids=[room],
                        feature_refs=[dict(space_id='test-space', row=i, valid=valid)])
            self.scene['objects'].append(item)
        np.save(self.root / 'vectors.npy', np.array([[0, 1], [.8, .6], [1, 0], [1, 0]], np.float32))
        Image.new('RGB', (80, 60), '#ffffff').save(self.root / 'image.png')
        self.scene['assets'] = [
            dict(asset_id='vectors', path='vectors.npy', sha256=digest(self.root / 'vectors.npy'),
                 kind='feature_matrix', metadata=dict(shape=[4, 2], dtype='float32', feature_space_id='test-space')),
            dict(asset_id='image', path='image.png', sha256=digest(self.root / 'image.png'),
                 kind='image', metadata=dict(width=80, height=60))]
        self.scene['feature_spaces'] = [dict(space_id='test-space', encoder='test',
            weights_fingerprint='test-weights', preprocess_fingerprint='test', dim=2,
            normalized=True, matrix_asset_id='vectors')]
        self.scene['observations'] = [dict(observation_id='v0', image_asset_id='image', width=80,
            height=60, source_kind='real_camera', reconstruction_id=None, captured_at=None,
            rendered_at=None, K=None, camera_to_world=None, camera_convention='unknown', provenance=['fixture'])]
        self.scene['objects'][0]['observation_links'] = [dict(observation_id='v0',
            crop_xyxy=[10, 10, 40, 50], target_support='valid')]
        self.save()

    def test_embedded_descriptions_preserve_sources_and_work_without_sidecar(self):
        from sementic_map_mcp.descriptions import sources, description_id, embed_descriptions
        from sementic_map_mcp.preview import call_for_model

        self.scene['objects'][0]['description'] = '原始OCR文字'
        self.save()
        store = SceneStore(self.root)
        original_scene = (self.root / 'scene.json').read_bytes()
        rows = []
        for oid, item in store.objects.items():
            row = dict(object_id=oid,status='ok' if oid == 'o0' else 'unavailable',
                text='A white cabinet beside a desk.' if oid == 'o0' else None,
                sources=sources(store,item),provenance=dict(model='fixture-vlm'),error=None)
            row['description_id'] = description_id(row)
            rows.append(row)
        cache = dict(schema_version='1.0.0',scene_id=store.scene['scene_id'],
            snapshot_id=store.scene['snapshot_id'],scene_sha256=digest(self.root/'scene.json'),
            model='fixture-vlm',endpoint='fixture',generation=dict(temperature=0),complete=True,records=rows)
        path=self.root/'descriptions.json';write_json(path,cache)
        output=self.root.parent/(self.root.name+'-embedded')
        self.addCleanup(shutil.rmtree,output,True)
        embed_descriptions(self.root,path,output,'derived-snapshot')
        derived=SceneStore(output)
        self.assertEqual((self.root/'scene.json').read_bytes(),original_scene)
        self.assertEqual(derived.scene['snapshot_id'],'derived-snapshot')
        self.assertEqual(derived.objects['o0']['description'],rows[0]['text'])
        self.assertEqual(derived.objects['o0']['description_metadata']['original_description'],'原始OCR文字')
        self.assertEqual(derived.descriptions.records['o0'],rows[0])
        self.assertEqual(derived.objects['o1']['description_metadata']['status'],'unavailable')
        for oid in store.objects:
            self.assertEqual(derived.objects[oid]['label'],store.objects[oid]['label'])
        for aid in store.assets:
            self.assertEqual(derived.path(aid).read_bytes(),store.path(aid).read_bytes())
        service=MapTools(derived,self.root/'embedded-media')
        raw,context,media,audit=call_for_model(service,'search_candidates',dict(queries=['white cabinet']))
        self.assertEqual(media,())
        self.assertEqual(context['data']['candidates'][0]['visual_description']['text'],rows[0]['text'])
        self.assertEqual(raw.envelope['data']['candidates'][0]['object_id'],'o0')
        original=service.search_candidates(['原始OCR文字'])
        self.assertEqual(original.envelope['data']['candidates'][0]['object_id'],'o0')
        with self.assertRaisesRegex(ValueError,'override'):
            MapTools(derived,self.root/'override',descriptions_path=path)
        with self.assertRaisesRegex(ValueError,'distinct'):
            embed_descriptions(self.root,path,output,store.scene['snapshot_id'])
        with self.assertRaisesRegex(ValueError,'outside'):
            embed_descriptions(self.root,path,self.root/'nested','other-snapshot')
        scene_path=output/'scene.json';bad=copy.deepcopy(derived.scene)
        bad['objects'][0]['description']='tampered';write_json(scene_path,bad)
        with self.assertRaisesRegex(ValueError,'content hash'):
            SceneStore(output)
        bad=copy.deepcopy(derived.scene)
        bad['objects'][0]['description_metadata']['sources'][0]['image_sha256']='0'*64
        write_json(scene_path,bad)
        with self.assertRaisesRegex(ValueError,'source binding'):
            SceneStore(output)
        bad=copy.deepcopy(derived.scene);bad.pop('description_generation');write_json(scene_path,bad)
        with self.assertRaisesRegex(ValueError,'generation header'):
            SceneStore(output)

        scene=copy.deepcopy(derived.scene)
        matrix=np.array([[1.,0.],[0.,1.]],np.float32);np.save(output/'mixed.npy',matrix)
        meta=dict(kind='image_text',pooling='LAST',weights_fingerprint='test-weights',
            preprocess_fingerprint='fixture-preprocess',query_template='query:{query}',
            source_protocol_sha256='0'*64,model_manifest={},settings={},
            rows=[dict(object_id='o0',description_id=rows[0]['description_id'],
                       source=rows[0]['sources'][0],crop_sha256='0'*64) for _ in range(2)])
        from sementic_map_mcp.multimodal import fingerprint
        meta['model_manifest']={'model_id':'fixture-vlm'}
        meta['settings']={'query_template':meta['query_template']}
        meta['weights_fingerprint']=fingerprint(meta['model_manifest'])
        meta['preprocess_fingerprint']=fingerprint(meta['settings'])
        scene['assets'].append(dict(asset_id='mixed',path='mixed.npy',sha256=digest(output/'mixed.npy'),
            kind='feature_matrix',metadata=dict(shape=[2,2],dtype='float32',feature_space_id='mixed-space',multimodal_index=meta)))
        scene['feature_spaces'].append(dict(space_id='mixed-space',encoder='fixture-vlm',
            weights_fingerprint=meta['weights_fingerprint'],preprocess_fingerprint=meta['preprocess_fingerprint'],
            dim=2,normalized=True,matrix_asset_id='mixed'))
        scene['objects'][0]['feature_refs'].extend(dict(space_id='mixed-space',row=i,valid=True) for i in range(2))
        write_json(scene_path,scene)
        mixed=SceneStore(output);encoder=FixedEncoder();encoder.space_id='mixed-space';encoder.fingerprint=meta['weights_fingerprint']
        service=MapTools(mixed,self.root/'mixed-media',encoder);service.retriever.top_k=1
        result=service.search_candidates(['a query without lexical matches']).envelope['data']
        self.assertEqual(result['groups'][0]['channels_used'],['label','multimodal'])
        self.assertFalse(result['groups'][0]['truncated'])
        self.assertEqual(result['groups'][0]['multimodal_match_ids'],['o0'])
        self.assertEqual(result['candidates'][0]['scores'][0]['multimodal_cosine'],1.)
        self.assertEqual(result['candidates'][0]['description'],rows[0]['text'])
        from sementic_map_mcp.multimodal import HttpEmbeddingEncoder
        from io import StringIO
        config=dict(space_id='mixed-space',encoder='fixture-vlm',weights_fingerprint=meta['weights_fingerprint'],
            preprocess_fingerprint=meta['preprocess_fingerprint'],query_template=meta['query_template'],
            served_model='fixture-serving',endpoint='http://127.0.0.1:1/v1')
        http_encoder=HttpEmbeddingEncoder(mixed,config)
        response=dict(model='fixture-serving',data=[dict(index=0,embedding=[1.,0.])])
        with patch('sementic_map_mcp.multimodal.urlopen',return_value=StringIO(json.dumps(response))) as request:
            self.assertEqual(http_encoder.encode(['test query']).tolist(),[[1.,0.]])
            self.assertEqual(json.loads(request.call_args.args[0].data)['input'],['query:test query'])
        response['model']='wrong-model'
        with patch('sementic_map_mcp.multimodal.urlopen',return_value=StringIO(json.dumps(response))):
            with self.assertRaisesRegex(ValueError,'response model mismatch'):
                http_encoder.encode(['test query'])
        bad=copy.deepcopy(scene);bad['assets'][-1]['metadata']['multimodal_index']['rows'][0]['description_id']='wrong'
        write_json(scene_path,bad)
        with self.assertRaisesRegex(ValueError,'description binding'):
            SceneStore(output)

    def test_description_cache_binding_text_only_selection_and_missing(self):
        from sementic_map_mcp.descriptions import sources, description_id
        from sementic_map_mcp.preview import call_for_model
        store = SceneStore(self.root)
        row = dict(object_id='o0',status='ok',text='A white cabinet with a flat front.',
            sources=sources(store, store.objects['o0']),provenance=dict(model='fixture-vlm'),error=None)
        row['description_id'] = description_id(row)
        cache = dict(schema_version='1.0.0',scene_id=store.scene['scene_id'],snapshot_id=store.scene['snapshot_id'],
            scene_sha256=digest(self.root/'scene.json'),model='fixture-vlm',endpoint='fixture',
            generation={},complete=False,records=[row])
        path=self.root/'descriptions.json';write_json(path,cache)
        service=MapTools(store,self.root/'caption-media',descriptions_path=path)
        with patch.object(service.renderer,'candidate_sheet',side_effect=AssertionError('must not render')):
            raw,context,media,audit=call_for_model(service,'search_candidates',dict(queries=['fixture']))
        self.assertEqual(media,())
        self.assertNotIn('visual_description',raw.envelope['data']['candidates'][0])
        self.assertEqual(context['data']['candidates'][0]['visual_description']['description_id'],row['description_id'])
        self.assertEqual(set(context['data']['missing_description_ids']),{'o1','o2','o3'})
        self.assertEqual(audit['records'][0]['sources'],row['sources'])
        calls=[]
        def complete(messages):
            calls.append(1)
            if len(calls)==1:
                action=dict(action='tool',tool='search_candidates',arguments=dict(queries=['fixture']))
            else:
                self.assertFalse(any(b.get('type')=='image_url' for m in messages if isinstance(m.get('content'),list) for b in m['content']))
                action=dict(action='finish',decision=dict(status='resolved',object_ids=['o0'],evidence_ids=[row['description_id']],reason='fixture',question=None))
            return dict(choices=[dict(finish_reason='stop',message=dict(content=json.dumps(action)))])
        result=run_agent(service,'fixture',self.root/'caption-agent',complete)
        self.assertEqual(result['selection']['selected_id'],'o0')
        self.assertIsNone(result['navigation']['goal'])
        calls.clear()
        with patch.object(service,'call',side_effect=AssertionError('Local tools must not bypass injected transport')):
            transported=run_agent(service,'fixture',self.root/'transport-agent',complete,
                tool_executor=lambda *args:(raw,context,media,audit))
        self.assertEqual(transported['selection']['selected_id'],'o0')
        def wrong_description(messages):
            if len(messages) == 2:
                action=dict(action='tool',tool='search_candidates',arguments=dict(queries=['fixture']))
            else:
                action=dict(action='finish',decision=dict(status='resolved',object_ids=['o1'],
                    evidence_ids=[row['description_id']],reason='fixture',question=None))
            return dict(choices=[dict(finish_reason='stop',message=dict(content=json.dumps(action)))])
        with self.assertRaisesRegex(ValueError,'lacks supplied'):
            run_agent(service,'fixture',self.root/'wrong-description',wrong_description)
        bad=copy.deepcopy(cache);bad['snapshot_id']='other';write_json(path,bad)
        with self.assertRaisesRegex(ValueError,'snapshot'):
            MapTools(store,self.root/'bad-caption',descriptions_path=path)
        bad=copy.deepcopy(cache);bad['records'][0]['text']='tampered';write_json(path,bad)
        with self.assertRaisesRegex(ValueError,'content hash'):
            MapTools(store,self.root/'bad-caption',descriptions_path=path)
        bad=copy.deepcopy(cache);bad['records'][0]['sources'][0]['image_sha256']='0'*64;write_json(path,bad)
        with self.assertRaisesRegex(ValueError,'source binding'):
            MapTools(store,self.root/'bad-caption',descriptions_path=path)
        bad=copy.deepcopy(cache);bad_row=bad['records'][0]
        bad_row.update(object_id='o1',sources=[])
        bad_row['description_id']=description_id(bad_row);write_json(path,bad)
        with self.assertRaisesRegex(ValueError,'no valid source'):
            MapTools(store,self.root/'bad-caption',descriptions_path=path)

    def test_offline_descriptions_concurrent_requests_and_failed_objects(self):
        import runpy
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Barrier, Thread
        from sementic_map_mcp.descriptions import Descriptions

        self.scene['objects'][1]['observation_links'] = copy.deepcopy(
            self.scene['objects'][0]['observation_links'])
        self.save()
        requests = []
        barrier = Barrier(2)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((body, self.headers.get('Authorization')))
                barrier.wait(timeout=5)
                failed = 'o1' in body['messages'][1]['content'][0]['text']
                self.send_response(500 if failed else 200)
                self.end_headers()
                if not failed:
                    self.wfile.write(json.dumps(dict(choices=[dict(finish_reason='stop',
                        message=dict(content=json.dumps(dict(target_visible=True,
                            description='A white rectangular cabinet.'))))])).encode())

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        output = self.root / 'offline'
        try:
            argv = ['build_descriptions.py', '--scene', str(self.root), '--output', str(output),
                '--endpoint', f'http://127.0.0.1:{server.server_port}/v1', '--model', 'fixture-vlm',
                '--workers', '2', '--disable-thinking']
            with patch('sys.argv', argv), patch.dict('os.environ', {}, clear=True):
                runpy.run_path(str(ROOT / 'scripts/build_descriptions.py'), run_name='__main__')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        cache = Descriptions(SceneStore(self.root), output / 'descriptions.json')
        self.assertTrue(cache.data['complete'])
        self.assertEqual(len(requests), 2)  # Failed calls are not retried.
        self.assertEqual({k: v['status'] for k, v in cache.records.items()},
            dict(o0='ok', o1='error', o2='unavailable', o3='unavailable'))
        for body, authorization in requests:
            self.assertIsNone(authorization)
            self.assertEqual(body['chat_template_kwargs'], dict(enable_thinking=False))
            url = body['messages'][1]['content'][1]['image_url']['url']
            self.assertTrue(base64.b64decode(url.split(',')[1]).startswith(b'\x89PNG'))
        summary = json.loads((output / 'summary.json').read_text())
        self.assertEqual(summary['counts'], dict(ok=1, error=1, unclear=0, unavailable=2))

    def test_model_query_policy_requires_english_without_changing_raw_api(self):
        from sementic_map_mcp.agent import model_tools, PROMPT
        definitions = {t['function']['name']: t['function'] for t in model_tools()}
        for name in ['search_candidates', 'view_map']:
            self.assertIn('queries必须全部使用英文', definitions[name]['description'])
            self.assertIn('不得包含中文', definitions[name]['parameters']['properties']['queries']['items']['description'])
        self.assertNotIn('可用中文类别', PROMPT)
        self.assertIn('无需机械翻完全部页', PROMPT)
        self.assertIn('改写queries或room_ids必须重置page=1', definitions['search_candidates']['description'])
        raw = self.tools().search_candidates(['柜子'])
        self.assertNotEqual(raw.envelope['status'], 'invalid_request')

    def test_half_single_preview_dimensions_and_coordinate_mapping(self):
        from sementic_map_mcp.preview import call_for_model
        high = self.tools()
        low = MapTools(high.store, self.root / 'half-single', single_max_width=300)
        _, a_context, a_media, a = call_for_model(high, 'search_candidates', dict(queries=['fixture']), 'single')
        _, b_context, b_media, b = call_for_model(low, 'search_candidates', dict(queries=['fixture']), 'single')
        self.assertEqual(a_context['data']['candidates'], b_context['data']['candidates'])
        self.assertEqual(len(a_media), len(b_media))
        for old, new, old_cell, new_cell in zip(a_media, b_media, a['cells'], b['cells']):
            self.assertEqual((old.metadata['width'], old.metadata['height']), (600, 400))
            self.assertEqual((new.metadata['width'], new.metadata['height']), (300, 200))
            self.assertEqual(old.metadata['object_ids'], new.metadata['object_ids'])
            self.assertNotEqual(old.metadata['media_id'], new.metadata['media_id'])
            self.assertEqual(new.metadata['media_id'], new_cell['media_id'])
            for p, q in zip(old_cell['panels'], new_cell['panels']):
                self.assertEqual(p['original_crop_xyxy'], q['original_crop_xyxy'])
                self.assertEqual(p['original_size'], q['original_size'])
                np.testing.assert_allclose(q['panel_xyxy'], np.array(p['panel_xyxy']) / 2)
        self.assertEqual(low.inspect_objects(['o0']).media[0].metadata['width'], 1200)
        for invalid in [0, -1, True, 300.5]:
            with self.assertRaises(ValueError):
                MapTools(high.store, self.root / 'invalid-single', single_max_width=invalid)
        with self.assertRaisesRegex(ValueError, 'requires single'):
            call_for_model(low, 'search_candidates', dict(queries=['fixture']))

    def test_reduced_preview_preserves_ids_and_original_coordinates(self):
        from sementic_map_mcp.preview import call_for_model
        high = self.tools()
        low = MapTools(high.store, self.root / 'reduced', montage_max_width=1200)
        _, _, high_media, original = call_for_model(high, 'search_candidates', dict(queries=['fixture']))
        _, context, low_media, reduced = call_for_model(low, 'search_candidates', dict(queries=['fixture']))
        self.assertEqual((high_media[0].metadata['width'], high_media[0].metadata['height']), (1800, 800))
        self.assertEqual((low_media[0].metadata['width'], low_media[0].metadata['height']), (1200, 533))
        self.assertNotEqual(high_media[0].metadata['media_id'], low_media[0].metadata['media_id'])
        self.assertEqual(high_media[0].metadata['object_ids'], low_media[0].metadata['object_ids'])
        for a, b in zip(original['cells'], reduced['cells']):
            self.assertEqual(a['object_id'], b['object_id'])
            for p, q in zip(a['panels'], b['panels']):
                self.assertEqual(p['original_crop_xyxy'], q['original_crop_xyxy'])
                self.assertEqual(p['original_size'], q['original_size'])
                np.testing.assert_allclose(q['panel_xyxy'], np.array(p['panel_xyxy']) * [2/3, 533/800, 2/3, 533/800])
        self.assertEqual(context['data']['preview']['media_id'], low_media[0].metadata['media_id'])
        for invalid in [0, -1, True, 1200.5]:
            with self.assertRaises(ValueError):
                MapTools(high.store, self.root / 'invalid', montage_max_width=invalid)

    def test_agent_single_packing_has_same_pixels_and_valid_evidence(self):
        import io
        from sementic_map_mcp.preview import call_for_model
        for item in self.scene['objects']:
            item['observation_links'] = copy.deepcopy(self.scene['objects'][0]['observation_links'])
        self.save()
        tools = self.tools()
        _, montage_context, montage_media, sheet = call_for_model(tools, 'search_candidates', dict(queries=['fixture']))
        _, context, media, preview = call_for_model(tools, 'search_candidates', dict(queries=['fixture']), 'single')
        self.assertEqual(context['data']['candidates'], montage_context['data']['candidates'])
        self.assertEqual(len(media), 4)
        self.assertNotIn('media_id', context['data']['preview'])
        with Image.open(io.BytesIO(montage_media[0].content)) as full:
            for cell, payload in zip(sheet['cells'], media):
                with Image.open(io.BytesIO(payload.content)) as tile:
                    self.assertTrue(np.array_equal(np.asarray(full.crop(cell['cell_xyxy'])), np.asarray(tile)))
        seen = []
        def complete(messages):
            seen.append(messages)
            if len(seen) == 1:
                action = dict(action='tool', tool='search_candidates', arguments=dict(queries=['fixture']))
            else:
                data = json.loads(messages[-1]['content'][0]['text'])
                cell = next(c for c in data['data']['preview']['cells'] if c['object_id']=='o1')
                self.assertEqual(sum(p['type']=='image_url' for p in messages[-1]['content']), 4)
                action = dict(action='finish', decision=dict(status='resolved', object_ids=['o1'],
                    evidence_ids=[cell['media_id']], reason='fixture', question=None))
            return dict(choices=[dict(finish_reason='stop', message=dict(content=json.dumps(action)))])
        result = run_agent(tools, 'fixture', self.root / 'single-agent', complete, image_packing='single')
        self.assertEqual(result['selection']['selected_id'], 'o1')
        self.assertIsNone(result['navigation']['goal'])
        from jsonschema import validate
        validate(preview, json.loads((ROOT / 'schemas/candidate_preview.schema.json').read_text()))

    def test_packing_experiment_preserves_candidate_pixels_and_order(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('packing', ROOT / 'scripts/compare_image_packing.py')
        packing = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(packing)
        sheet = Image.new('RGB', (1800, 800), 'white')
        colors = ['red', 'green', 'blue', 'yellow', 'purple']
        cells = []
        for i, color in enumerate(colors):
            x, y = i % 3 * 600, i // 3 * 400
            sheet.paste(color, (x, y, x + 600, y + 400))
            cells.append(dict(object_id=f'o{i}', cell_xyxy=[x, y, x + 600, y + 400]))
        preview = dict(cells=cells, missing_ids=[])
        images = packing.fixed_images(packing.png(sheet), preview, [f'o{i}' for i in range(5)])
        import io
        for i, data in enumerate(images):
            with Image.open(io.BytesIO(data)) as tile:
                self.assertEqual(tile.size, (600, 400))
                self.assertEqual(tile.getpixel((20, 20)), Image.new('RGB', (1, 1), colors[i]).getpixel((0, 0)))
        with self.assertRaises(ValueError):
            packing.fixed_images(packing.png(sheet), preview, ['o1', 'o0', 'o2', 'o3', 'o4'])
        with self.assertRaises(ValueError):
            packing.fixed_images(packing.png(sheet), dict(preview, missing_ids=['o1']), [f'o{i}' for i in range(5)])

    def test_search_preview_binding_missing_images_and_direct_finish(self):
        from sementic_map_mcp.preview import call_for_model
        tools = self.tools()
        raw, context, media, preview = call_for_model(tools, 'search_candidates', dict(queries=['fixture']))
        self.assertEqual(raw.media, ())
        self.assertEqual(len(media), 1)
        self.assertEqual(preview['missing_ids'], ['o1', 'o2', 'o3'])
        self.assertEqual(media[0].metadata['object_ids'], ['o0'])
        self.assertEqual([c['object_id'] for c in preview['cells']], ['o0'])
        from jsonschema import validate
        validate(preview, json.loads((ROOT / 'schemas/candidate_preview.schema.json').read_text()))
        cell = preview['cells'][0]
        for panel in cell['panels']:
            x0, y0, x1, y1 = panel['panel_xyxy']
            self.assertTrue(cell['cell_xyxy'][0] <= x0 < x1 <= cell['cell_xyxy'][2])
            self.assertTrue(cell['cell_xyxy'][1] <= y0 < y1 <= cell['cell_xyxy'][3])
        self.assertEqual(cell['observation_id'], 'v0')
        self.assertEqual(cell['panels'][1]['original_crop_xyxy'], [10, 10, 40, 50])
        self.assertNotIn('source_kind', context['data']['preview']['cells'][0])
        self.assertEqual(context['status'], 'partial')
        empty = call_for_model(tools, 'search_candidates', dict(queries=['fixture'], page=2))
        self.assertIsNone(empty[3])
        self.assertEqual(empty[2], ())
        seen = []
        def complete(messages):
            seen.append(messages)
            if len(seen) == 1:
                action = dict(action='tool', tool='search_candidates', arguments=dict(queries=['fixture']))
            else:
                parts = messages[-1]['content']
                image = next(p for p in parts if p['type'] == 'image_url')
                self.assertTrue(base64.b64decode(image['image_url']['url'].split(',')[1]).startswith(b'\x89PNG'))
                data = json.loads(parts[0]['text'])
                action = dict(action='finish', decision=dict(status='resolved', object_ids=['o0'],
                    evidence_ids=[data['data']['preview']['media_id']], reason='fixture', question=None))
            return dict(choices=[dict(finish_reason='stop', message=dict(content=json.dumps(action)))])
        result = run_agent(tools, 'fixture', self.root / 'preview-agent', complete)
        self.assertEqual(result['selection']['selected_id'], 'o0')
        self.assertEqual(len(seen), 2)
        self.assertIsNone(result['navigation']['goal'])
        original_complete = complete
        seen.clear()
        def choose_missing(messages):
            response = original_complete(messages)
            action = json.loads(response['choices'][0]['message']['content'])
            if action['action'] == 'finish':
                action['decision']['object_ids'] = ['o1']
                response['choices'][0]['message']['content'] = json.dumps(action)
            return response
        with self.assertRaisesRegex(ValueError, 'lacks supplied image evidence'):
            run_agent(tools, 'fixture', self.root / 'preview-missing-agent', choose_missing)

    def test_snapshot_cache_reuses_bytes_and_isolates_results(self):
        tools = self.tools()
        first = tools.inspect_objects(['o0'])
        with patch.object(tools.renderer, 'evidence', side_effect=AssertionError('cached image regenerated')):
            second = tools.inspect_objects(['o0'])
        self.assertEqual(first, second)
        self.assertEqual(tools._execute.cache_info().hits, 1)
        first.envelope['data']['objects'][0]['label'] = 'mutated by caller'
        first.media[0].metadata['object_ids'].append('injected')
        third = tools.inspect_objects(['o0'])
        self.assertEqual(second, third)
        independent = self.tools()
        self.assertEqual(independent._execute.cache_info().currsize, 0)
        self.assertEqual(independent.inspect_objects(['o0']), second)
        self.assertEqual(tools._execute.cache_info().maxsize, 32)

    def test_search_does_not_render_and_matches_view_recall(self):
        tools = self.tools(FixedEncoder())
        with patch.object(tools.renderer, 'maps', side_effect=AssertionError('must not render')):
            result = tools.search_candidates(['fixture'], ['R01'])
        self.assertEqual(result.media, ())
        self.assertNotIn('maps', result.envelope['data'])
        mapped = tools.view_map(['R01'], ['fixture'])
        self.assertEqual(result.envelope['data']['candidates'], mapped.envelope['data']['candidates'])
        self.assertEqual(tools.search_candidates([]).envelope['status'], 'invalid_request')
        self.assertEqual(tools.search_candidates(['x'], ['unknown']).envelope['status'], 'invalid_request')

    def test_agent_sends_images_and_blocks_navigation(self):
        tools = self.tools()
        seen = []
        def complete(messages):
            seen.append(messages[-1])
            if len(seen) == 1:
                action = dict(action='tool', tool='inspect_objects', arguments=dict(ids=['o0']))
            else:
                parts = messages[-1]['content']
                image = next(p['image_url']['url'] for p in parts if p['type'] == 'image_url')
                self.assertTrue(base64.b64decode(image.split(',')[1]).startswith(b'\x89PNG'))
                envelope = json.loads(parts[0]['text'])
                self.assertNotIn('source_kind', parts[0]['text'])
                self.assertNotIn('source_refs', parts[0]['text'])
                self.assertNotIn('limitations', envelope)
                self.assertTrue(any('物体图片' in p.get('text', '') for p in parts))
                action = dict(action='finish', decision=dict(status='resolved', object_ids=['o0'],
                    evidence_ids=[envelope['media'][0]['media_id']], reason='fixture only', question=None))
            return dict(choices=[dict(finish_reason='stop', message=dict(content=json.dumps(action)))])
        result = run_agent(tools, 'fixture', self.root / 'agent', complete)
        self.assertIsNone(result['navigation']['goal'])
        self.assertEqual(result['handoff'], 'not_ready')
        self.assertTrue((self.root / 'agent/01-request.json').exists())
        raw = json.loads((self.root / 'agent/00-tool.json').read_text())
        self.assertEqual(raw['data']['objects'][0]['evidence'][0]['source_kind'], 'real_camera')
        self.assertIn('source_refs', raw['data']['objects'][0])

    def test_agent_native_tool_calls_return_results_and_images(self):
        tools = self.tools()
        def complete(messages):
            if len(messages) == 2:
                calls = [dict(id='c1', type='function', function=dict(name='inspect_objects',
                         arguments=json.dumps(dict(ids=['o0'])))),
                         dict(id='c2', type='function', function=dict(name='list_scene', arguments='{}'))]
                return dict(choices=[dict(finish_reason='tool_calls', message=dict(content=None, tool_calls=calls))])
            results = [m for m in messages if m['role'] == 'tool']
            self.assertEqual([r['tool_call_id'] for r in results], ['c1', 'c2'])
            self.assertTrue(any(p['type'] == 'image_url' for p in messages[-1]['content']))
            media = json.loads(results[0]['content'])['media'][0]['media_id']
            action = dict(action='finish', decision=dict(status='resolved', object_ids=['o0'],
                          evidence_ids=[media], reason='fixture', question=None))
            return dict(choices=[dict(finish_reason='tool_calls', message=dict(content=None, tool_calls=[
                dict(id='done', type='function', function=dict(name='finish',
                     arguments=json.dumps(action['decision'])))]))])
        result = run_agent(tools, 'fixture', self.root / 'native', complete)
        self.assertEqual(result['grounding']['object_ids'], ['o0'])

    def test_agent_rejects_unseen_ids_truncation_and_budget(self):
        tools = self.tools()
        action = dict(action='finish', decision=dict(status='ambiguous', object_ids=['invented'],
                      evidence_ids=[], reason='fixture', question='Which?'))
        response = dict(choices=[dict(finish_reason='stop', message=dict(content=json.dumps(action)))])
        with self.assertRaisesRegex(ValueError, 'unseen'):
            run_agent(tools, 'fixture', self.root / 'bad-id', lambda _: response)
        response['choices'][0]['finish_reason'] = 'length'
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            run_agent(tools, 'fixture', self.root / 'truncated', lambda _: response)
        with self.assertRaisesRegex(ValueError, 'budget'):
            run_agent(tools, 'fixture', self.root / 'budget', lambda _: response, max_turns=0)

    def test_model_pagination_and_single_view(self):
        raw = self.tools(FixedEncoder()).search_candidates(['fixture']).envelope
        template = raw['data']['candidates'][0]
        raw['data']['candidates'] = [dict(template, object_id=f'id{i}') for i in range(9)]
        before = copy.deepcopy(raw)
        first = model_context(raw)['data']
        second = model_context(raw, dict(page=2))['data']
        self.assertEqual(len(first['candidates']), 5)
        self.assertEqual(first['next_page'], 2)
        self.assertIsNone(second['next_page'])
        ids = [c['object_id'] for c in first['candidates'] + second['candidates']]
        self.assertEqual(len(set(ids)), 9)
        self.assertEqual(raw, before)
        inspected = self.tools().inspect_objects(['o0']).envelope
        evidence = inspected['data']['objects'][0]['evidence'][0]
        inspected['data']['objects'][0]['evidence'].append(dict(evidence, media_id='second'))
        inspected['media'].append(dict(inspected['media'][0], media_id='second'))
        one = model_context(inspected)
        two = model_context(inspected, dict(view=2))
        self.assertEqual(len(one['media']), 1)
        self.assertEqual(one['data']['objects'][0]['next_view'], 2)
        self.assertEqual(two['media'][0]['media_id'], 'second')
        self.assertEqual(len(inspected['media']), 2)

    def save(self):
        write_json(self.root / 'scene.json', self.scene)

    def tools(self, encoder=None):
        return MapTools(SceneStore(self.root), self.root / 'out', encoder)

    def test_scoped_vector_recall_excludes_invalid_and_retains_labels(self):
        tools = self.tools(FixedEncoder())
        tools.retriever.top_k = 1
        result = tools.view_map(['R01'], ['柜子']).envelope
        self.assertEqual(result['status'], 'ok')
        group = result['data']['groups'][0]
        self.assertEqual(group['visual_match_ids'], ['o1'])
        self.assertEqual(set(group['label_match_ids']), {'o0', 'o1', 'o3'})
        self.assertEqual(set(group['candidate_ids']), {'o0', 'o1', 'o3'})
        self.assertEqual(len(result['media']), 1)

    def test_unavailable_encoder_and_empty_query_differ(self):
        tools = self.tools()
        result = tools.view_map(['R01'], ['not-present']).envelope
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['data']['candidates'], [])
        self.assertEqual(tools.view_map(['R01']).envelope['status'], 'ok')

    def test_no_match_with_working_encoder_and_empty_room(self):
        self.scene['rooms'].append(dict(self.scene['rooms'][0], room_id='empty'))
        self.save()
        tools = self.tools(FixedEncoder())
        result = tools.view_map(['empty'], ['printer']).envelope
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['data']['candidates'], [])

    def test_invalid_arguments_and_unknown_ids(self):
        tools = self.tools()
        for args in [{'ids': []}, {'ids': ['o0', 'o0']}, {'ids': ['o0'], 'extra': 1}]:
            self.assertEqual(tools.call('inspect_objects', args).envelope['status'], 'invalid_request')
        self.assertEqual(tools.view_map(['missing']).envelope['status'], 'invalid_request')
        self.assertEqual(tools.view_map([], ['  ']).envelope['status'], 'invalid_request')
        self.assertEqual(tools.inspect_objects(['missing']).envelope['status'], 'not_found')

    def test_batch_evidence_pixels_and_missing_records(self):
        result = self.tools().inspect_objects(['o0', 'missing', 'o1'])
        self.assertEqual(result.envelope['status'], 'partial')
        self.assertEqual(result.envelope['data']['missing_ids'], ['missing'])
        self.assertEqual([o['object_id'] for o in result.envelope['data']['objects']], ['o0', 'o1'])
        self.assertEqual(len(result.media), 1)
        self.assertTrue(result.media[0].content.startswith(b'\x89PNG'))
        evidence = result.envelope['data']['objects'][0]['evidence'][0]
        self.assertEqual(evidence['panels'][1]['original_crop_xyxy'], [10, 10, 40, 50])
        self.assertEqual(evidence['panels'][1]['panel_xyxy'], [610, 80, 640, 120])

    def test_map_pixel_transform_and_id_binding(self):
        result = self.tools().view_map(['R01'])
        page = result.envelope['data']['maps'][0]
        self.assertEqual(set(page['object_ids']), {'o0', 'o1', 'o3'})
        matrix = np.array(page['world_to_pixel'])
        point = np.array([1.5, -2.5, 1])
        pixel = matrix @ point
        np.testing.assert_allclose(np.linalg.inv(matrix) @ pixel, point)
        self.assertGreater(pixel[0], 0)
        self.assertLess(pixel[0], result.media[0].metadata['width'])

    def test_bad_hash_and_external_path_rejected(self):
        self.scene['assets'][0]['sha256'] = '0' * 64
        self.save()
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            SceneStore(self.root)
        self.scene['assets'][0]['path'] = '../vectors.npy'
        self.save()
        with self.assertRaises(ValidationError) as error:
            SceneStore(self.root)
        self.assertIn('does not match', str(error.exception))

    def test_bad_crop_and_feature_reference_rejected(self):
        self.scene['objects'][0]['observation_links'][0]['crop_xyxy'] = [0, 0, 81, 60]
        self.save()
        with self.assertRaisesRegex(ValueError, 'crop outside'):
            SceneStore(self.root)
        self.scene['objects'][0]['observation_links'] = []
        self.scene['objects'][0]['feature_refs'][0]['row'] = 9
        self.save()
        with self.assertRaisesRegex(ValueError, 'feature row'):
            SceneStore(self.root)

    def test_encoder_identity_rejected(self):
        encoder = FixedEncoder()
        encoder.fingerprint = 'wrong'
        with self.assertRaisesRegex(ValueError, 'encoder identity'):
            self.tools(encoder)


if __name__ == '__main__':
    unittest.main()
