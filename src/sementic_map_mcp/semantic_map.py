# Derived from open-nav-harness; modified for standalone map service ownership.
# SPDX-License-Identifier: Apache-2.0
# See licenses/open-nav-harness-LICENSE and licenses/open-nav-harness-NOTICE.
"""Read-only semantic scene tools with a package-owned MCP transport.

Server and deep_search are owned by this package.
"""
from __future__ import annotations

import argparse
import base64
import json
import threading
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from .mcp_registry import ImageContent, TextContent, ToolRegistry, ToolResult

MAP_TOOLS = ("list_scene", "search_candidates", "view_map", "inspect_objects")
Queries = Annotated[list[Annotated[str, Field(min_length=1, max_length=256)]], Field(min_length=1, max_length=4)]
Rooms = Annotated[list[str], Field(max_length=16)]
ObjectIds = Annotated[list[str], Field(min_length=1, max_length=3)]
Page = Annotated[int, Field(ge=1)]
View = Annotated[int, Field(ge=1, le=2)]
Instruction = Annotated[str, Field(min_length=1, max_length=4096, pattern=r'\S')]
Context = Annotated[str, Field(max_length=8192)]


class SemanticMapSession:
    def __init__(self, env):
        from sementic_map_mcp.agent import model_tools
        from sementic_map_mcp.multimodal import load_encoder
        from sementic_map_mcp.service import MapTools
        from sementic_map_mcp.store import SceneStore

        deep_config = env.get('MAP_DEEP_SEARCH_CONFIG')
        self.output = Path(env["MAP_OUTPUT"])
        self.store = SceneStore(env["MAP_SCENE"])
        config = env.get("MAP_ENCODER_CONFIG")
        encoder = load_encoder(self.store, Path(config)) if config else None
        self.output.mkdir(parents=True, exist_ok=False)
        self.service = MapTools(self.store, self.output / "media", encoder)
        self.lock = threading.Lock()
        self.sequence = 0
        self.tools = ToolRegistry()
        descriptions = {t['function']['name']: t['function']['description'] for t in
                        model_tools(use_descriptions=self.service.descriptions is not None)}
        for name in MAP_TOOLS:
            self.tools.register(name, descriptions[name], getattr(self, name))
        self.deep = None
        if deep_config:
            from sementic_map_mcp.deep_search import DeepSearch
            self.deep = DeepSearch(self.service, self.output / 'deep-search', Path(deep_config), self.lock)
            self.tools.register('deep_search', self.deep.schema['description'], self.deep_search)

    def _call(self, name, arguments):
        from sementic_map_mcp.store import write_json

        # Serialize rendering and trace numbering for concurrent SDK tool requests.
        with self.lock:
            from sementic_map_mcp.preview import call_for_model
            result, context, model_media, preview = call_for_model(self.service, name, arguments)
            if name == 'list_scene' and self.deep is not None:
                context = self.deep.cards.enrich(context)
            prefix = self.output / f"{self.sequence:04d}"
            self.sequence += 1
            write_json(str(prefix) + "-arguments.json", arguments)
            write_json(str(prefix) + "-tool.json", result.envelope)
            if preview is not None:
                kind = 'descriptions' if preview.get('kind') == 'descriptions' else 'preview'
                write_json(str(prefix) + f"-{kind}.json", preview)
            write_json(str(prefix) + "-model-context.json", context)
            content = [TextContent(text=json.dumps(context, ensure_ascii=False))]
            visible = {m['media_id'] for m in context['media']}
            for media in model_media:
                if media.metadata['media_id'] in visible:
                    title = "物体图片" if name in ("inspect_objects", "search_candidates") else "空间图"
                    content.append(TextContent(text=title + " media_id=" + media.metadata['media_id']))
                    content.append(ImageContent(data=base64.b64encode(media.content).decode(),
                                                mimeType=media.metadata['mime_type']))
            reply = ToolResult(content=content, isError=context['status'] in (
                'invalid_request', 'not_found', 'unavailable'))
            write_json(str(prefix) + "-result.json", reply.model_dump())
            return reply

    def list_scene(self, include_types: bool = False):
        return self._call("list_scene", dict(include_types=include_types))

    def search_candidates(self, queries: Queries, room_ids: Rooms = [], page: Page = 1):
        return self._call("search_candidates", dict(queries=queries, room_ids=room_ids, page=page))

    def view_map(self, room_ids: Rooms = [], queries: Annotated[list[str], Field(max_length=4)] = []):
        return self._call("view_map", dict(room_ids=room_ids, queries=queries))

    def inspect_objects(self, ids: ObjectIds, view: View = 1):
        return self._call("inspect_objects", dict(ids=ids, view=view))

    async def deep_search(self, instruction: Instruction, context: Context = '',
                          think_mode: Literal['fast', 'middle', 'strong'] = 'fast'):
        if self.deep is None:
            raise ValueError('deep_search is not configured')
        result = await self.deep.run(instruction, context, think_mode)
        return ToolResult(content=[TextContent(text=json.dumps(result, ensure_ascii=False))],
                          isError=result['status'] != 'ok')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", required=True)
    parser.add_argument("--output", required=True, help="New directory for private traces and images")
    parser.add_argument("--encoder-config")
    parser.add_argument('--deep-search-config')
    parser.add_argument("--mcp", action="store_true", help="Serve configured tools over MCP stdio")
    parser.add_argument("--tool", choices=(*MAP_TOOLS, 'deep_search'))
    parser.add_argument("--arguments", default="{}", help="JSON object for a single tool call")
    args = parser.parse_args()
    if args.mcp == bool(args.tool):
        parser.error("choose exactly one of --mcp or --tool")
    env = dict(MAP_SCENE=args.scene, MAP_OUTPUT=args.output)
    if args.encoder_config:
        env['MAP_ENCODER_CONFIG'] = args.encoder_config
    if args.deep_search_config:
        env['MAP_DEEP_SEARCH_CONFIG'] = args.deep_search_config
    session = SemanticMapSession(env)
    if args.mcp:
        import asyncio
        from .mcp_server import build_mcp
        asyncio.run(build_mcp(session).run_stdio_async())
    else:
        import asyncio
        result = asyncio.run(session.tools.call_tool(args.tool, json.loads(args.arguments)))
        print(json.dumps(result.model_dump(), ensure_ascii=False))


if __name__ == "__main__":
    main()
