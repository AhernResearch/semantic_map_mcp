# Derived from open-nav-harness; modified for standalone map service ownership.
# SPDX-License-Identifier: Apache-2.0
# See licenses/open-nav-harness-LICENSE and licenses/open-nav-harness-NOTICE.
"""Package-owned MCP registry transport."""

def build_mcp(session, **settings):
    """Expose the map registry to MCP clients."""
    import functools
    from mcp.server.fastmcp import FastMCP
    from mcp.types import CallToolResult

    registry = getattr(session, "tools", session)
    server = FastMCP("sementic_map_mcp", **settings)
    for name, definition in registry.definitions.items():

        def bind(tool_name):
            @functools.wraps(registry.functions[tool_name])
            async def call(*args, **kwargs):
                result = await registry.call_tool(tool_name, kwargs)
                return CallToolResult.model_validate(result.model_dump())

            return call

        server.tool(name=name, description=definition.description)(bind(name))
    return server
