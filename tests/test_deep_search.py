"""Contract, evidence and cancellation checks; no real model calls."""

import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

import httpx
from jsonschema import ValidationError
from PIL import Image

from sementic_map_mcp.deep_search import DeepSearch
from sementic_map_mcp.descriptions import description_id, embed_descriptions, sources
from sementic_map_mcp.service import MapTools
from sementic_map_mcp.store import SCHEMAS, SceneStore, digest, read_json, write_json


def tool(name, args):
    return dict(choices=[dict(finish_reason='tool_calls', message=dict(role='assistant', content=None,
        tool_calls=[dict(id=uuid.uuid4().hex, type='function', function=dict(
            name=name, arguments=json.dumps(args, ensure_ascii=False)))]))])


def decision(rooms=(), objects=(), evidence=(), status='resolved', question=None):
    return dict(status=status, room_ids=list(rooms), object_ids=list(objects), evidence_ids=list(evidence),
                reason='已根据地图证据找到目标。', question=question)


class DeepSearchTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root / 'source'
        source.mkdir()
        scene = read_json(SCHEMAS.parent / 'examples/scene.json')
        scene['rooms'] += [dict(scene['rooms'][0], room_id=rid, name=rid) for rid in ('R02', 'R03')]
        prototype = deepcopy(scene['objects'][0])
        scene['objects'] = [dict(deepcopy(prototype), object_id=f'o{i}', source_id=f'o{i}',
            label='fixture cabinet', room_ids=[rid], observation_links=[dict(
                observation_id='v0', crop_xyxy=None, target_support='valid')])
            for i, rid in enumerate(('R01', 'R02'))]
        Image.new('RGB', (80, 60), 'white').save(source / 'image.png')
        scene['assets'] = [dict(asset_id='image', path='image.png', sha256=digest(source / 'image.png'),
            kind='image', metadata=dict(width=80, height=60))]
        scene['observations'] = [dict(observation_id='v0', image_asset_id='image', width=80, height=60,
            source_kind='real_camera', reconstruction_id=None, captured_at=None, rendered_at=None,
            K=None, camera_to_world=None, camera_convention='unknown', provenance=['synthetic fixture'])]
        write_json(source / 'scene.json', scene)
        store = SceneStore(source)
        rows = []
        for oid, item in store.objects.items():
            row = dict(object_id=oid, status='ok', text='A white cabinet fixture.', sources=sources(store, item),
                       provenance=dict(model='fixture-vlm'), error=None)
            row['description_id'] = description_id(row)
            rows.append(row)
        write_json(self.root / 'descriptions.json', dict(schema_version='1.0.0', scene_id=scene['scene_id'],
            snapshot_id=scene['snapshot_id'], scene_sha256=digest(source / 'scene.json'), model='fixture-vlm',
            endpoint='fixture', generation={}, complete=True, records=rows))
        embed_descriptions(source, self.root / 'descriptions.json', self.root / 'snapshot', 'fixture-described')
        self.store = SceneStore(self.root / 'snapshot')
        self.service = MapTools(self.store, self.root / 'media')
        cards, evidence = [], {}
        for rid, room in self.store.rooms.items():
            objects = [o for o in self.store.objects.values() if rid in o['room_ids']]
            cards.append(dict(room_id=rid, name=room['name'], summary='有白色柜子' if objects else '暂无物体记录',
                main_objects=[dict(name='白色柜子', object_ids=[o['object_id'] for o in objects])] if objects else [],
                distinctive_features=[]))
            evidence[rid] = {o['object_id']: self.store.descriptions.get(o['object_id']) for o in objects}
        self.cards = self.root / 'cards.json'
        write_json(self.cards, dict(schema_version='1.0.0', scene_id=scene['scene_id'],
            snapshot_id=self.store.scene['snapshot_id'], scene_sha256=digest(self.store.root / 'scene.json'),
            cards=cards, evidence=evidence))
        self.env = patch.dict(os.environ, DEEP_TEST_KEY='synthetic-test-key')
        self.env.start()
        self.addCleanup(self.env.stop)

    def make(self, **options):
        path = self.root / (uuid.uuid4().hex + '.json')
        profiles = {mode: dict(endpoint='https://fixture.invalid/v1', model=f'fixture-{mode}',
                               api_key_env='DEEP_TEST_KEY') for mode in ('fast', 'middle', 'strong')}
        write_json(path, dict(dict(profiles=profiles, cards_path=str(self.cards)), **options))
        return DeepSearch(self.service, self.root / uuid.uuid4().hex, path, threading.Lock())

    def client(self, handler):
        cls = httpx.AsyncClient
        async def reply(request):
            return httpx.Response(200, json=await handler(json.loads(request.content)))
        return patch('sementic_map_mcp.deep_search.httpx.AsyncClient',
                     side_effect=lambda **kwargs: cls(transport=httpx.MockTransport(reply), **kwargs))

    async def settled(self, deep):
        for _ in range(100):
            if not deep.slots.locked():
                return
            await asyncio.sleep(.005)
        self.fail('Worker retained concurrency slot')

    async def test_region_seed_context_and_native_finish(self):
        deep = self.make()
        cid = deep.cards.cards['R01']['card_id']
        seen = []
        async def reply(payload):
            seen.append(payload)
            self.assertEqual({t['function']['name'] for t in payload['tools']},
                             {'list_scene', 'search_candidates', 'inspect_objects', 'view_map', 'finish'})
            self.assertEqual(payload['tool_choice'], 'required')
            return tool('finish', decision(rooms=['R01'], evidence=[cid]))
        with self.client(reply):
            result = await deep.run('就那个区域', '上轮用户选择了有白色柜子的区域')
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['data']['selection']['selected_room_id'], 'R01')
        self.assertIsNone(result['data']['selection']['selected_id'])
        self.assertIsNone(result['data']['navigation']['goal'])
        self.assertEqual(result['metrics']['model_calls'], 1)
        self.assertEqual(result['metrics']['tool_calls'], 0)
        self.assertEqual(result['data']['evidence'][0]['kind'], 'card')
        self.assertEqual(json.loads(seen[0]['messages'][-1]['content'])['context'], '上轮用户选择了有白色柜子的区域')
        self.assertIn('scene_context', json.loads(seen[0]['messages'][1]['content']))

    async def test_modes_route_explicitly_and_default_to_fast(self):
        deep = self.make()
        seen = []
        async def reply(payload):
            seen.append(payload['model'])
            return tool('finish', decision(rooms=['R01'], evidence=[deep.cards.cards['R01']['card_id']]))
        with self.client(reply):
            default = await deep.run('找区域')
            for mode in ('fast', 'middle', 'strong'):
                result = await deep.run('找区域', think_mode=mode)
                self.assertEqual(result['execution']['think_mode'], mode)
                self.assertEqual(result['execution']['model'], f'fixture-{mode}')
            bad = await deep.run('找区域', think_mode='auto')
        self.assertEqual(default['execution']['think_mode'], 'fast')
        self.assertEqual(seen, ['fixture-fast', 'fixture-fast', 'fixture-middle', 'fixture-strong'])
        self.assertEqual(bad['status'], 'invalid_request')
        self.assertIsNone(bad['execution'])
        self.assertEqual(bad['metrics']['model_calls'], 0)

    async def test_local_no_auth_and_upstream_error_does_not_change_mode(self):
        profiles = {mode: dict(endpoint='http://127.0.0.1:8892/v1', model=f'fixture-{mode}')
                    for mode in ('fast', 'middle', 'strong')}
        deep = self.make(profiles=profiles)
        seen = []
        async def reply(request):
            self.assertNotIn('authorization', request.headers)
            seen.append(json.loads(request.content)['model'])
            return httpx.Response(503, json=dict(error='fixture unavailable'))
        cls = httpx.AsyncClient
        with patch('sementic_map_mcp.deep_search.httpx.AsyncClient',
                   side_effect=lambda **kwargs: cls(transport=httpx.MockTransport(reply), **kwargs)):
            result = await deep.run('找区域')
        self.assertEqual(seen, ['fixture-fast'])
        self.assertEqual(result['errors'][0]['code'], 'model_error')
        self.assertEqual(result['execution']['think_mode'], 'fast')

    async def test_responses_replays_reasoning_calls_and_images(self):
        profiles = {mode: dict(endpoint='https://fixture.invalid/v1', model=f'fixture-{mode}',
                    api_key_env='DEEP_TEST_KEY') for mode in ('fast', 'middle', 'strong')}
        profiles['strong'].update(api_format='responses', reasoning_effort='medium', output_tokens=4096)
        deep = self.make(profiles=profiles, max_turns=2)
        seen = []
        async def reply(payload):
            seen.append(payload)
            self.assertEqual(payload['reasoning'], dict(effort='medium'))
            self.assertNotIn('temperature', payload)
            self.assertFalse(payload['store'])
            if len(seen) == 1:
                name, args = 'inspect_objects', dict(ids=['o0'])
                self.assertEqual(payload['tool_choice'], 'required')
            else:
                self.assertIn(dict(type='reasoning', id='rs_fixture', encrypted_content='fixture'), payload['input'])
                prior = next(item for item in payload['input'] if item.get('type') == 'function_call_output')
                self.assertEqual(prior['call_id'], 'call_fixture')
                images = [p for item in payload['input'] for p in item.get('content', [])
                          if p['type'] == 'input_image']
                self.assertTrue(images[0]['image_url'].startswith('data:image/png;base64,'))
                evidence = json.loads(prior['output'])['media'][0]['media_id']
                name, args = 'finish', decision(objects=['o0'], rooms=['R01'], evidence=[evidence])
                self.assertEqual(payload['tool_choice'], dict(type='function', name='finish'))
            return dict(status='completed', output=[dict(type='reasoning', id='rs_fixture', encrypted_content='fixture'),
                dict(type='function_call', call_id='call_fixture', name=name,
                     arguments=json.dumps(args, ensure_ascii=False))])
        with self.client(reply):
            result = await deep.run('找柜子并看图', think_mode='strong')
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['data']['selection']['selected_id'], 'o0')
        self.assertEqual(result['metrics']['model_calls'], 2)
        self.assertEqual(result['execution']['api_format'], 'responses')

    async def test_configuration_rejects_missing_mode_and_missing_key(self):
        for profiles in ({'fast': dict(endpoint='http://localhost/v1', model='fixture')},
                         {m: dict(endpoint='https://fixture.invalid/v1', model='fixture', api_key_env='MISSING_FIXTURE_KEY')
                          for m in ('fast', 'middle', 'strong')}):
            with self.assertRaises((ValueError, ValidationError)):
                self.make(profiles=profiles)

    async def test_vllm_generation_schema_keeps_full_host_uniqueness_validation(self):
        profiles = {m: dict(endpoint='http://localhost/v1', model='fixture') for m in ('fast', 'middle', 'strong')}
        profiles['fast']['omit_unique_items'] = True
        deep = self.make(profiles=profiles)
        async def reply(payload):
            self.assertNotIn('uniqueItems', json.dumps(payload['tools']))
            self.assertIn('uniqueItems', json.dumps(deep.tools))
            return tool('finish', decision(rooms=['R01'], evidence=[deep.cards.cards['R01']['card_id']] * 2))
        with self.client(reply):
            result = await deep.run('找区域')
        self.assertEqual(result['errors'][0]['code'], 'agent_protocol_error')
        self.assertEqual(result['metrics']['model_calls'], 1)

    async def test_object_requires_retrieved_description_and_final_turn_finishes(self):
        deep = self.make(max_turns=2)
        choices = []
        async def reply(payload):
            choices.append(payload['tool_choice'])
            if len(choices) == 1:
                return tool('search_candidates', dict(queries=['fixture']))
            return tool('finish', decision(objects=['o0'], rooms=['R01'],
                evidence=[self.store.descriptions.get('o0')['description_id']]))
        with self.client(reply):
            result = await deep.run('找白色柜子')
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['data']['selection']['selected_id'], 'o0')
        self.assertEqual(result['data']['selection']['selected_room_id'], 'R01')
        self.assertEqual(result['data']['evidence'][0]['kind'], 'description')
        self.assertEqual(choices[-1], dict(type='function', function=dict(name='finish')))

    async def test_unknown_mismatched_empty_and_context_only_evidence_rejected(self):
        for mode in ('unknown_room', 'wrong_room', 'empty_room', 'unseen_object', 'recursive'):
            with self.subTest(mode=mode):
                deep = self.make()
                cid = deep.cards.cards['R01']['card_id']
                args = decision(rooms=['R99' if mode == 'unknown_room' else 'R02'], evidence=[cid])
                if mode == 'empty_room':
                    args = decision(rooms=['R03'], evidence=[deep.cards.cards['R03']['card_id']])
                if mode == 'unseen_object':
                    args = decision(objects=['o0'], evidence=[cid])
                async def reply(payload):
                    return tool('deep_search', dict(instruction='recursive')) if mode == 'recursive' else tool('finish', args)
                with self.client(reply):
                    result = await deep.run('就那个', '此前有人提过o0和R99')
                self.assertEqual(result['status'], 'unavailable')
                self.assertEqual(result['errors'][0]['code'], 'agent_protocol_error')

    async def test_prose_is_protocol_error_not_repaired_or_retried(self):
        deep = self.make()
        async def reply(payload):
            return dict(choices=[dict(finish_reason='stop', message=dict(content='推荐R01'))])
        with self.client(reply):
            result = await deep.run('找一个区域')
        self.assertEqual(result['errors'][0]['code'], 'agent_protocol_error')
        self.assertEqual(result['metrics']['model_calls'], 1)
        self.assertIsNone(result['data'])

    async def test_object_area_mismatch_and_missing_evidence_are_not_repaired(self):
        for mismatch in (True, False):
            deep = self.make()
            calls = []
            async def reply(payload):
                calls.append(payload)
                if mismatch and len(calls) == 1:
                    return tool('search_candidates', dict(queries=['fixture']))
                args = decision(objects=['o0'], rooms=['R02'],
                                evidence=[self.store.descriptions.get('o0')['description_id']])
                if not mismatch:
                    args = decision(status='needs_context', question='请补充前文')
                    args.pop('evidence_ids')
                return tool('finish', args)
            with self.client(reply):
                result = await deep.run('查找')
            self.assertEqual(result['errors'][0]['code'], 'agent_protocol_error')
            self.assertIsNone(result['data'])
            self.assertNotIn('Failed validating', result['errors'][0]['message'])

    async def test_bad_input_and_stale_cards(self):
        deep = self.make()
        self.assertEqual((await deep.run('   '))['status'], 'invalid_request')
        self.assertEqual((await deep.run('查找', {}))['status'], 'invalid_request')
        for mode in ('hash', 'description', 'coverage'):
            original = read_json(self.cards)
            damaged = deepcopy(original)
            if mode == 'hash':
                damaged['scene_sha256'] = '0' * 64
            elif mode == 'description':
                damaged['evidence']['R01']['o0']['text'] = 'stale text'
            else:
                damaged['cards'].pop()
            write_json(self.cards, damaged)
            with self.assertRaises(ValueError):
                self.make()
            write_json(self.cards, original)

    async def test_deadline_cancels_model_request(self):
        deep = self.make(deadline_seconds=.1, max_concurrency=1)
        cancelled = asyncio.Event()
        async def reply(payload):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        with self.client(reply):
            tick = time.monotonic()
            result = await deep.run('查找')
            self.assertLess(time.monotonic() - tick, .8)
            self.assertEqual(result['errors'][0]['code'], 'deadline_exceeded')
            await asyncio.wait_for(cancelled.wait(), .5)
            await self.settled(deep)

    async def test_caller_cancellation_and_busy_admission(self):
        deep = self.make(max_concurrency=1)
        entered, cancelled = asyncio.Event(), asyncio.Event()
        async def reply(payload):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        with self.client(reply):
            task = asyncio.create_task(deep.run('查找'))
            await asyncio.wait_for(entered.wait(), 1)
            busy = await deep.run('另一次查找')
            self.assertEqual(busy['errors'][0]['code'], 'busy')
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(cancelled.wait(), .5)
            await self.settled(deep)

    async def test_deadline_retains_slot_until_current_read_only_tool_stops(self):
        deep = self.make(deadline_seconds=.1, max_concurrency=1)
        original = self.service.call
        def slow(name, arguments):
            if name == 'search_candidates':
                time.sleep(.25)
            return original(name, arguments)
        async def reply(payload):
            return tool('search_candidates', dict(queries=['fixture']))
        with self.client(reply), patch.object(self.service, 'call', side_effect=slow):
            result = await deep.run('查找')
            self.assertEqual(result['errors'][0]['code'], 'deadline_exceeded')
            self.assertTrue(deep.slots.locked())
            self.assertEqual(result['metrics']['model_calls'], 1)
            await self.settled(deep)

    async def test_parallel_requests_keep_context_and_trace_separate(self):
        deep = self.make(max_concurrency=2)
        async def reply(payload):
            rid = json.loads(payload['messages'][-1]['content'])['utterance']
            await asyncio.sleep(.01)
            return tool('finish', decision(rooms=[rid], evidence=[deep.cards.cards[rid]['card_id']]))
        with self.client(reply):
            results = await asyncio.gather(deep.run('R01'), deep.run('R02'))
        self.assertEqual([r['data']['selection']['selected_room_id'] for r in results], ['R01', 'R02'])
        self.assertNotEqual(results[0]['trace_id'], results[1]['trace_id'])
