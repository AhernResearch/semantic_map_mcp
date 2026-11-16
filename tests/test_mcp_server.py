"""Contract tests with synthetic scenes; no model or simulator calls."""
import base64
import copy
import json
import tempfile
import unittest
from pathlib import Path
from contextlib import asynccontextmanager

from pydantic import ValidationError

from sementic_map_mcp.semantic_map import MAP_TOOLS, SemanticMapSession


@asynccontextmanager
async def open_tools(env):
    yield SemanticMapSession(env).tools


class SemanticMapTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from sementic_map_mcp.store import SCHEMAS, digest, write_json
        from PIL import Image

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        scene = json.loads((SCHEMAS.parent / 'examples/scene.json').read_text())
        Image.new('RGB', (80, 60), 'white').save(self.root / 'image.png')
        scene['assets'] = [dict(asset_id='image', path='image.png', sha256=digest(self.root / 'image.png'),
            kind='image', metadata=dict(width=80, height=60))]
        scene['feature_spaces'] = []
        scene['observations'] = [dict(observation_id=f'v{i}', image_asset_id='image', width=80,
            height=60, source_kind='real_camera', reconstruction_id=None, captured_at=None,
            rendered_at=None, K=None, camera_to_world=None, camera_convention='unknown',
            provenance=['synthetic']) for i in range(2)]
        prototype = scene['objects'][0]
        scene['objects'] = []
        for i in range(7):
            item = copy.deepcopy(prototype)
            item.update(object_id=f'o{i}', source_id=f'o{i}', label='fixture', feature_refs=[],
                observation_links=[dict(observation_id=f'v{j}', crop_xyxy=None, target_support='valid')
                                   for j in range(2)])
            scene['objects'].append(item)
        write_json(self.root / 'scene.json', scene)
        self.env = dict(MAP_SCENE=str(self.root), MAP_OUTPUT=str(self.root / 'trace'))

    async def test_transport_pagination_and_errors(self):
        async with open_tools(self.env) as tools:
            self.assertEqual(set(tools.definitions), set(MAP_TOOLS))
            result = tools.invoke('search_candidates', dict(queries=['fixture']))
            first = json.loads(result.content[0].text)
            self.assertEqual(len(first['data']['candidates']), 5)
            self.assertEqual(len([c for c in result.content if c.type == 'image']), 1)
            self.assertEqual(len(first['data']['preview']['cells']), 5)
            self.assertEqual({c['object_id'] for c in first['data']['preview']['cells']},
                             {c['object_id'] for c in first['data']['candidates']})
            self.assertEqual(first['data']['next_page'], 2)
            self.assertEqual(first['status'], 'partial')  # no encoder, explicitly preserved
            second = json.loads(tools.invoke('search_candidates', dict(queries=['fixture'], page=2)).content[0].text)
            self.assertEqual(len(second['data']['candidates']), 2)
            self.assertIsNone(second['data']['next_page'])
            error = tools.invoke('inspect_objects', dict(ids=['unknown']))
            self.assertTrue(error.isError)
            for name, args in [('search_candidates', dict(queries=['fixture'], page=0)),
                               ('inspect_objects', dict(ids=['o0'] * 4)),
                               ('inspect_objects', dict(ids=['o0'], view=3)),
                               ('list_scene', dict(unexpected=True))]:
                with self.assertRaises(ValidationError):
                    tools.invoke(name, args)
        raw = json.loads((self.root / 'trace/0000-tool.json').read_text())
        self.assertEqual(len(raw['data']['candidates']), 7)
        self.assertIn('limitations', raw)
        self.assertNotIn('limitations', first)
        self.assertEqual(first['snapshot_id'], raw['snapshot_id'])

    async def test_images_and_mcp_schema(self):
        from sementic_map_mcp.mcp_server import build_mcp
        session = SemanticMapSession(self.env)
        server = build_mcp(session)
        listed = {t.name: t for t in await server.list_tools()}
        self.assertEqual(set(listed), set(MAP_TOOLS))
        for name in MAP_TOOLS:
            self.assertEqual(listed[name].inputSchema['properties'],
                             session.tools.definitions[name].inputSchema['properties'])
        result = await server.call_tool('inspect_objects', dict(ids=['o0']))
        images = [p for p in result.content if p.type == 'image']
        self.assertEqual(len(images), 1)
        self.assertTrue(base64.b64decode(images[0].data).startswith(b'\x89PNG'))
        first = json.loads(result.content[0].text)
        self.assertEqual(first['data']['objects'][0]['next_view'], 2)
        second = session.tools.invoke('inspect_objects', dict(ids=['o0'], view=2))
        data = json.loads(second.content[0].text)['data']['objects'][0]
        self.assertIsNone(data['next_view'])
        self.assertNotEqual(data['evidence'][0]['media_id'], first['data']['objects'][0]['evidence'][0]['media_id'])
        mapped = session.tools.invoke('view_map', dict(queries=['fixture']))
        self.assertTrue(any(c.type == 'image' for c in mapped.content))

    async def test_embedded_descriptions_are_text_only_and_inspection_keeps_images(self):
        import shutil
        from sementic_map_mcp.store import SceneStore, digest, write_json
        from sementic_map_mcp.descriptions import sources, description_id, embed_descriptions
        from sementic_map_mcp.mcp_server import build_mcp
        store=SceneStore(self.root)
        rows=[]
        for oid,item in store.objects.items():
            row=dict(object_id=oid,status='ok',text='A white fixture object.',sources=sources(store,item),
                     provenance=dict(model='fixture-vlm'),error=None)
            row['description_id']=description_id(row);rows.append(row)
        cache=self.root/'descriptions.json'
        write_json(cache,dict(schema_version='1.0.0',scene_id=store.scene['scene_id'],
            snapshot_id=store.scene['snapshot_id'],scene_sha256=digest(self.root/'scene.json'),
            model='fixture-vlm',endpoint='fixture',generation={},complete=True,records=rows))
        output=self.root.parent/(self.root.name+'-described')
        self.addCleanup(shutil.rmtree,output,True)
        embed_descriptions(self.root,cache,output,'fixture-described')
        session=SemanticMapSession(dict(self.env,MAP_SCENE=str(output)))
        server=build_mcp(session)
        listed={t.name:t for t in await server.list_tools()}
        self.assertIn('外观描述',listed['search_candidates'].description)
        result=await server.call_tool('search_candidates',dict(queries=['fixture']))
        self.assertFalse(any(c.type=='image' for c in result.content))
        data=json.loads(result.content[0].text)
        self.assertEqual(data['snapshot_id'],'fixture-described')
        self.assertEqual(len(data['data']['candidates']),5)
        self.assertTrue(all(c['visual_description']['text']=='A white fixture object.'
                            for c in data['data']['candidates']))
        self.assertTrue((self.root/'trace/0000-descriptions.json').exists())
        self.assertFalse((self.root/'trace/0000-preview.json').exists())
        image=await server.call_tool('inspect_objects',dict(ids=['o0']))
        self.assertEqual(len([c for c in image.content if c.type=='image']),1)

    def test_reject_existing_output(self):
        SemanticMapSession(self.env)
        with self.assertRaises(FileExistsError):
            SemanticMapSession(self.env)

    async def test_http_auth_and_initialize(self):
        import httpx
        from sementic_map_mcp.semantic_map_http import build_http_app
        token_file = self.root / 'token'
        token_file.write_text('t' * 48)
        app = build_http_app(SemanticMapSession(self.env), '127.0.0.1', 8879, token_file)
        async with app.app.router.lifespan_context(app.app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url='http://127.0.0.1:8879') as client:
                self.assertEqual((await client.post('/mcp')).status_code, 401)
                self.assertEqual((await client.post('/mcp', headers={'Authorization': 'Bearer wrong'})).status_code, 401)
                headers = {'Authorization': 'Bearer ' + 't' * 48,
                           'Accept': 'application/json, text/event-stream'}
                request = dict(jsonrpc='2.0', id=1, method='initialize', params=dict(
                    protocolVersion='2025-03-26', capabilities={},
                    clientInfo=dict(name='contract-test', version='1')))
                response = await client.post('/mcp', headers=headers, json=request)
                self.assertEqual(response.status_code, 200)
                self.assertIn('serverInfo', response.json()['result'])
                denied = await client.post('/mcp', headers=dict(headers, Host='attacker.invalid'), json=request)
                self.assertEqual(denied.status_code, 421)

    async def test_optional_deep_search_async_schema_context_and_cancellation(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import patch
        from sementic_map_mcp.store import SCHEMAS, read_json
        from sementic_map_mcp.mcp_server import build_mcp

        entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def run(instruction, context='', think_mode='fast'):
            entered.set()
            try:
                await release.wait()
                return dict(status='ok', data=dict(instruction=instruction, context=context, think_mode=think_mode))
            finally:
                cancelled.set()

        stub = SimpleNamespace(schema=read_json(SCHEMAS / 'deep_search.schema.json'), run=run,
                               cards=SimpleNamespace(enrich=lambda context: context))
        env = dict(self.env, MAP_DEEP_SEARCH_CONFIG='fixture-config.json')
        with patch('sementic_map_mcp.deep_search.DeepSearch', return_value=stub):
            session = SemanticMapSession(env)
        server = build_mcp(session)
        listed = {tool.name: tool for tool in await server.list_tools()}
        self.assertEqual(set(listed), set(MAP_TOOLS) | {'deep_search'})
        self.assertEqual(listed['deep_search'].inputSchema['required'], ['instruction'])
        self.assertEqual(listed['deep_search'].inputSchema['properties']['context']['default'], '')
        self.assertEqual(listed['deep_search'].inputSchema['properties']['think_mode']['default'], 'fast')
        for arguments in [dict(instruction=' '), dict(instruction=123),
                          dict(instruction='查找', context={}), dict(instruction='查找', model='override'),
                          dict(instruction='查找', think_mode='auto')]:
            with self.assertRaises(ValidationError):
                await session.tools.call_tool('deep_search', arguments)
        task = asyncio.create_task(session.tools.call_tool('deep_search', dict(instruction='查找', context='前文')))
        await asyncio.wait_for(entered.wait(), 1)
        # A model request must not hold the rendering lock or block other data tools.
        overview = await asyncio.wait_for(session.tools.call_tool('list_scene', {}), 1)
        self.assertEqual(json.loads(overview.content[0].text)['tool'], 'list_scene')
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cancelled.is_set())
        release.set()
        reply = await server.call_tool('deep_search', dict(instruction='原始中文', context='用户此前补充', think_mode='strong'))
        data = json.loads(reply.content[0].text)['data']
        self.assertEqual(data, dict(instruction='原始中文', context='用户此前补充', think_mode='strong'))
        with self.assertRaisesRegex(ValueError, 'Async tool'):
            session.tools.invoke('deep_search', dict(instruction='查找'))
