import copy
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image
from jsonschema import ValidationError

from map_query_tools.service import MapTools
from map_query_tools.store import SceneStore, digest, write_json

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

    def test_search_does_not_render_and_matches_view_recall(self):
        from unittest.mock import patch
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
        import base64
        from map_query_tools.agent import run_agent
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
        from map_query_tools.agent import run_agent
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
        from map_query_tools.agent import run_agent
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
        from map_query_tools.agent import model_context
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
