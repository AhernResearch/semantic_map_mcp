# Derived from open-nav-harness; modified for standalone map service ownership.
# SPDX-License-Identifier: Apache-2.0
# See licenses/open-nav-harness-LICENSE and licenses/open-nav-harness-NOTICE.
"""Authenticated LAN Streamable HTTP transport for the read-only map registry."""
from __future__ import annotations

import argparse
import hmac
import ipaddress
from pathlib import Path

from .mcp_server import build_mcp
from .semantic_map import SemanticMapSession


class BearerAuth:
    def __init__(self, app, token):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            headers = dict(scope['headers'])
            if not hmac.compare_digest(headers.get(b'authorization', b''), b'Bearer ' + self.token):
                from starlette.responses import PlainTextResponse
                response = PlainTextResponse('Unauthorized', status_code=401,
                                             headers={'WWW-Authenticate': 'Bearer'})
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_http_app(session, host, port, token_file):
    from mcp.server.transport_security import TransportSecuritySettings

    address = ipaddress.ip_address(host)
    if address.is_unspecified or address.is_multicast:
        raise ValueError('Bind to a specific LAN or loopback address')
    token = Path(token_file).read_bytes().strip()
    if len(token) < 32 or any(c < 33 or c > 126 for c in token):
        raise ValueError('Bearer token must contain at least 32 printable ASCII characters')
    authority = f'[{host}]:{port}' if address.version == 6 else f'{host}:{port}'
    server = build_mcp(session, host=host, port=port, stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=[authority], allowed_origins=['http://' + authority]))
    return BearerAuth(server.streamable_http_app(), token)


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--encoder-config')
    parser.add_argument('--deep-search-config')
    parser.add_argument('--host', required=True, help='Specific LAN address to bind')
    parser.add_argument('--port', type=int, default=8879)
    parser.add_argument('--token-file', required=True)
    args = parser.parse_args()
    env = dict(MAP_SCENE=args.scene, MAP_OUTPUT=args.output)
    if args.encoder_config:
        env['MAP_ENCODER_CONFIG'] = args.encoder_config
    if args.deep_search_config:
        env['MAP_DEEP_SEARCH_CONFIG'] = args.deep_search_config
    session = SemanticMapSession(env)
    app = build_http_app(session, args.host, args.port, args.token_file)
    uvicorn.run(app, host=args.host, port=args.port, access_log=False)


if __name__ == '__main__':
    main()
