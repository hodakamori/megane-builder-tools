"""The ``megane-builder-tools`` MCP server and its command line (§8, §9).

``megane-builder-tools`` serves the reference tools over stdio (the default;
this is what the megane bridge and LLM clients spawn). ``--transport http``
serves Streamable HTTP for the static webapp: it binds to loopback by default,
checks ``Host``/``Origin`` against the allowed origins, and requires a bearer
token on every request, generating one when none is given.
"""

from __future__ import annotations

import argparse
import hmac
import os
import secrets
import sys
from collections.abc import Sequence

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from . import __version__
from .sdk.tool import BuilderTool
from .tools.liquid_box import liquid_box
from .tools.polymer_chain import polymer_chain
from .tools.solvate import solvate

TOOLS: tuple[BuilderTool, ...] = (liquid_box, polymer_chain, solvate)
"""The reference tools, in the order Builder lists them."""

TOKEN_ENV = "MEGANE_BUILDER_TOOLS_TOKEN"
MAX_REQUEST_BYTES = 64 * 1024 * 1024
"""Large enough for a ``document`` argument of the maximum size."""

INSTRUCTIONS = (
    "Structure-building tools for megane Builder (liquid boxes, polymer chains, solvation). "
    "Each tool returns a structure (elements, Cartesian positions in Å, cell, bonds) as structuredContent."
)


def create_server(tools: Sequence[BuilderTool] = TOOLS) -> MCPServer:
    """An MCPServer exposing ``tools`` as Builder tools."""
    server = MCPServer(name="megane-builder-tools", version=__version__, instructions=INSTRUCTIONS)
    for tool in tools:
        tool.register(server)
    return server


class BearerTokenMiddleware:
    """Reject HTTP requests without ``Authorization: Bearer <token>`` (CORS preflights pass)."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("method") != "OPTIONS":
            headers = dict(scope.get("headers") or [])
            if not hmac.compare_digest(headers.get(b"authorization", b""), self._expected):
                await send(
                    {"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"text/plain")]}
                )
                await send({"type": "http.response.body", "body": b"missing or invalid bearer token"})
                return
        await self.app(scope, receive, send)


def http_app(server: MCPServer, *, token: str, host: str, port: int, allowed_origins: Sequence[str]) -> ASGIApp:
    """The Streamable HTTP app with DNS-rebinding protection, CORS and bearer-token auth."""
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[f"{host}:{port}", f"localhost:{port}", f"127.0.0.1:{port}"],
        allowed_origins=list(allowed_origins),
    )
    app: ASGIApp = server.streamable_http_app(
        transport_security=security, max_request_body_size=MAX_REQUEST_BYTES, host=host
    )
    app = BearerTokenMiddleware(app, token)
    return CORSMiddleware(
        app,
        allow_origins=list(allowed_origins),
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["authorization", "content-type", "mcp-session-id", "mcp-protocol-version", "last-event-id"],
        expose_headers=["mcp-session-id"],
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="megane-builder-tools", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind address (default: loopback)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token", default=os.environ.get(TOKEN_ENV), help=f"HTTP bearer token (or ${TOKEN_ENV})")
    parser.add_argument(
        "--allow-origin",
        action="append",
        default=[],
        help="Origin allowed to call the HTTP server (repeatable), e.g. https://megane-labs.github.io",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    server = create_server()
    if args.transport == "stdio":
        server.run("stdio")
        return 0

    import uvicorn

    token = args.token or secrets.token_urlsafe(24)
    if not args.token:
        print(f"megane-builder-tools: bearer token {token}", file=sys.stderr, flush=True)
    app = http_app(server, token=token, host=args.host, port=args.port, allowed_origins=args.allow_origin)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
