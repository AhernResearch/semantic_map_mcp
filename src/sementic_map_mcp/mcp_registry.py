# Derived from open-nav-harness; modified for standalone map service ownership.
# SPDX-License-Identifier: Apache-2.0
# See licenses/open-nav-harness-LICENSE and licenses/open-nav-harness-NOTICE.
"""Transport-neutral map tools and results. No MCP or model SDK dependency."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Literal, get_type_hints

from pydantic import BaseModel, ConfigDict, create_model


class TextContent(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImageContent(BaseModel):
    type: Literal["image"] = "image"
    data: str
    mimeType: str


class ToolResult(BaseModel):
    content: list[TextContent | ImageContent]
    isError: bool = False


class ToolDefinition(BaseModel):
    name: str
    description: str
    inputSchema: dict


class ToolRegistry:
    def __init__(self):
        self.functions: dict[str, Any] = {}
        self.arguments: dict[str, type[BaseModel]] = {}
        self.definitions: dict[str, ToolDefinition] = {}

    def register(self, name, description, fn):
        if name in self.functions:
            raise ValueError("Duplicate tool: " + name)
        hints = get_type_hints(fn, include_extras=True)
        fields = {
            key: (hints.get(key, Any), ... if p.default is inspect.Parameter.empty else p.default)
            for key, p in inspect.signature(fn).parameters.items()
        }
        args = create_model(name + "Arguments", __config__=ConfigDict(extra="forbid"), **fields)
        self.arguments[name] = args
        self.functions[name] = fn
        self.definitions[name] = ToolDefinition(
            name=name, description=description or "", inputSchema=args.model_json_schema()
        )

    def validate(self, name, arguments):
        """Strictly validate one call without executing it."""
        if name not in self.functions:
            raise ValueError("Unknown map tool: " + str(name))
        return self.arguments[name].model_validate(arguments, strict=True)

    def invoke(self, name, arguments) -> ToolResult:
        """Execute one validated synchronous call."""
        args = self.validate(name, arguments).model_dump()
        if inspect.iscoroutinefunction(self.functions[name]):
            raise ValueError('Async tool requires await call_tool: ' + name)
        return self.functions[name](**args)

    async def call_tool(self, name, arguments):
        if name in self.functions and inspect.iscoroutinefunction(self.functions[name]):
            args = self.validate(name, arguments).model_dump()
            return await self.functions[name](**args)
        return await asyncio.to_thread(self.invoke, name, arguments)

    async def list_tools(self):
        return list(self.definitions.values())
