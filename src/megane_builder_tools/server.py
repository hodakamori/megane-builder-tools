"""The ``megane-builder-tools`` MCP server and its command line (§8, §9).

``megane-builder-tools`` serves the reference tools over stdio (the default;
this is what the megane bridge and LLM clients spawn). ``--transport http``
serves Streamable HTTP: for a local Builder it binds to loopback and checks
``Host``/``Origin``; deployed behind a proxy (App Runner, see
``deploy/``) it binds to all interfaces, answers ``GET /health`` without
authentication, and is configured from the environment. Every HTTP request
to ``/mcp`` needs the bearer token, except in public mode (``--public``), where
it needs an ``Origin`` header naming one of the allowed web origins instead:
for a demo deployment whose page is public anyway, so a token in it would be
public too.

Every option can also be given as an environment variable
(``MEGANE_BUILDER_TOOLS_<OPTION>``, ``PORT`` for the port), which is how
container platforms configure it.
"""

from __future__ import annotations

import argparse
import hmac
import ipaddress
import os
import secrets
import sys
from collections.abc import Mapping, Sequence

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from . import __version__
from .sdk.tool import NO_LIMITS, BuilderTool, CallLimits
from .tools.liquid_box import liquid_box
from .tools.polymer_chain import polymer_chain
from .tools.solvate import solvate

TOOLS: tuple[BuilderTool, ...] = (liquid_box, polymer_chain, solvate)
"""The reference tools, in the order Builder lists them."""

ENV_PREFIX = "MEGANE_BUILDER_TOOLS_"
TOKEN_ENV = f"{ENV_PREFIX}TOKEN"
MAX_REQUEST_BYTES = 64 * 1024 * 1024
"""Large enough for a ``document`` argument of the maximum size."""
HEALTH_PATH = "/health"

INSTRUCTIONS = (
    "Structure-building tools for megane Builder (liquid boxes, polymer chains, solvation). "
    "Each tool returns a structure (elements, Cartesian positions in Å, cell, bonds) as structuredContent."
)


def create_server(tools: Sequence[BuilderTool] = TOOLS, limits: CallLimits = NO_LIMITS) -> MCPServer:
    """An MCPServer exposing ``tools`` as Builder tools, every call bounded by ``limits``."""
    server = MCPServer(name="megane-builder-tools", version=__version__, instructions=INSTRUCTIONS)
    for tool in tools:
        tool.register(server, limits)
    return server


async def _plain(send: Send, status: int, body: bytes) -> None:
    await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": body})


def _needs_check(scope: Scope) -> bool:
    """HTTP requests other than CORS preflights, which carry no credentials by design."""
    return scope["type"] == "http" and scope.get("method") != "OPTIONS"


class HealthMiddleware:
    """Answer ``GET /health`` with ``ok`` before any authentication; load balancers and App Runner poll it."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") == HEALTH_PATH:
            await _plain(send, 200, b"ok")
            return
        await self.app(scope, receive, send)


class BearerTokenMiddleware:
    """Reject HTTP requests without ``Authorization: Bearer <token>`` (CORS preflights pass)."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if _needs_check(scope):
            headers = dict(scope.get("headers") or [])
            if not hmac.compare_digest(headers.get(b"authorization", b""), self._expected):
                await _plain(send, 401, b"missing or invalid bearer token")
                return
        await self.app(scope, receive, send)


class OriginGateMiddleware:
    """Public mode: reject HTTP requests whose ``Origin`` is missing or not allowed (CORS preflights pass).

    CORS only stops a browser page on another site from reading the response;
    this also refuses the request itself, and refuses clients that send no
    ``Origin`` at all (scripts, crawlers). A client can still forge the header,
    so this keeps casual traffic out rather than authenticating anyone; the
    per-call time limit and concurrency cap bound what a forged request costs.
    """

    def __init__(self, app: ASGIApp, allowed_origins: Sequence[str]) -> None:
        self.app = app
        self._allowed = {origin.encode() for origin in allowed_origins}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if _needs_check(scope):
            origin = dict(scope.get("headers") or []).get(b"origin")
            if origin not in self._allowed:
                await _plain(send, 403, b"requests must come from an allowed web origin")
                return
        await self.app(scope, receive, send)


def http_app(
    server: MCPServer,
    *,
    token: str | None,
    host: str,
    port: int,
    allowed_origins: Sequence[str],
    allowed_hosts: Sequence[str] = (),
    stateless: bool = False,
) -> ASGIApp:
    """The Streamable HTTP app with Host/Origin checks, CORS, access control and ``/health``.

    Access control is the bearer ``token``, or with ``token=None`` (public mode)
    the ``Origin`` gate over ``allowed_origins``.

    ``allowed_hosts`` adds ``Host`` header values to the loopback defaults; ``"*"``
    turns the Host/Origin check off, for a server behind a proxy whose public
    name is not known in advance (access control and CORS still apply).
    """
    check_hosts = "*" not in allowed_hosts
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=check_hosts,
        allowed_hosts=[f"{host}:{port}", f"localhost:{port}", f"127.0.0.1:{port}", *allowed_hosts],
        allowed_origins=list(allowed_origins),
    )
    app: ASGIApp = server.streamable_http_app(
        transport_security=security,
        max_request_body_size=MAX_REQUEST_BYTES,
        host=host,
        stateless_http=stateless,
    )
    app = BearerTokenMiddleware(app, token) if token is not None else OriginGateMiddleware(app, allowed_origins)
    app = HealthMiddleware(app)
    return CORSMiddleware(
        app,
        allow_origins=list(allowed_origins),
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["authorization", "content-type", "mcp-session-id", "mcp-protocol-version", "last-event-id"],
        expose_headers=["mcp-session-id"],
    )


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _env_list(env: Mapping[str, str], name: str) -> list[str]:
    return [item.strip() for item in env.get(name, "").split(",") if item.strip()]


def _env_flag(env: Mapping[str, str], name: str) -> bool:
    return env.get(name, "").strip().lower() in ("1", "true", "yes")


def _env_float(env: Mapping[str, str], name: str) -> float | None:
    raw = env.get(name, "").strip()
    return float(raw) if raw else None


def _env_int(env: Mapping[str, str], name: str, default: int | None = None) -> int | None:
    raw = env.get(name, "").strip()
    return int(raw) if raw else default


def parse_args(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> argparse.Namespace:
    env = os.environ if env is None else env
    parser = argparse.ArgumentParser(prog="megane-builder-tools", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--transport", choices=["stdio", "http"], default=env.get(f"{ENV_PREFIX}TRANSPORT", "stdio"))
    parser.add_argument(
        "--host", default=env.get(f"{ENV_PREFIX}HOST", "127.0.0.1"), help="HTTP bind address (default: loopback)"
    )
    parser.add_argument("--port", type=int, default=_env_int(env, "PORT", 8765))
    parser.add_argument("--token", default=env.get(TOKEN_ENV), help=f"HTTP bearer token (or ${TOKEN_ENV})")
    parser.add_argument(
        "--public",
        action="store_true",
        default=_env_flag(env, f"{ENV_PREFIX}PUBLIC"),
        help="Serve HTTP without a token; requests must carry an allowed Origin (for a public demo page)",
    )
    parser.add_argument(
        "--allow-origin",
        action="append",
        default=_env_list(env, f"{ENV_PREFIX}ALLOWED_ORIGINS"),
        help="Origin allowed to call the HTTP server (repeatable), e.g. https://megane-labs.github.io",
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=_env_list(env, f"{ENV_PREFIX}ALLOWED_HOSTS"),
        help="Extra Host header value to accept (repeatable); '*' disables the Host/Origin check",
    )
    parser.add_argument(
        "--stateless",
        action="store_true",
        default=_env_flag(env, f"{ENV_PREFIX}STATELESS"),
        help="Serve Streamable HTTP without sessions (for several instances behind a load balancer)",
    )
    parser.add_argument(
        "--call-timeout",
        type=float,
        default=_env_float(env, f"{ENV_PREFIX}CALL_TIMEOUT"),
        help="Seconds a tool call may take, waiting included; longer calls fail with a tool error",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=_env_int(env, f"{ENV_PREFIX}MAX_CONCURRENCY"),
        help="Tool calls computed at the same time; later calls wait for a free slot",
    )
    return parser.parse_args(argv)


def _refuse(message: str) -> int:
    print(f"megane-builder-tools: {message}", file=sys.stderr)
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    limits = CallLimits(timeout=args.call_timeout, max_concurrency=args.max_concurrency)
    server = create_server(limits=limits)
    if args.transport == "stdio":
        server.run("stdio")
        return 0

    import uvicorn

    if args.public:
        if args.token:
            return _refuse(f"--public serves without a token; unset {TOKEN_ENV} or drop --public")
        if not args.allow_origin:
            return _refuse("--public needs at least one --allow-origin (the page allowed to call the server)")
        token: str | None = None
    elif not args.token and not is_loopback(args.host):
        return _refuse(f"refusing to serve on {args.host} without a token; set {TOKEN_ENV} or use --public")
    else:
        token = args.token or secrets.token_urlsafe(24)
        if not args.token:
            print(f"megane-builder-tools: bearer token {token}", file=sys.stderr, flush=True)
    app = http_app(
        server,
        token=token,
        host=args.host,
        port=args.port,
        allowed_origins=args.allow_origin,
        allowed_hosts=args.allowed_host,
        stateless=args.stateless,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", proxy_headers=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
