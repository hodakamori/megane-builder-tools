from __future__ import annotations

import pytest
from mcp import Client

from megane_builder_tools import server
from megane_builder_tools.server import (
    TOOLS,
    BearerTokenMiddleware,
    HealthMiddleware,
    OriginGateMiddleware,
    create_server,
    http_app,
    parse_args,
)


async def test_server_lists_the_reference_tools():
    async with Client(create_server()) as client:
        names = [t.name for t in (await client.list_tools()).tools]
    assert names == [t.name for t in TOOLS] == ["liquid_box", "polymer_chain", "solvate"]


async def asgi(app, method: str, headers: list[tuple[bytes, bytes]]):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": method, "path": "/mcp", "headers": headers, "query_string": b""}
    await app(scope, receive, send)
    return sent


async def test_health_needs_no_token():
    async def inner(scope, receive, send):  # pragma: no cover - never reached
        raise AssertionError("health must not reach the app")

    sent = await asgi_path(HealthMiddleware(inner), "GET", "/health")
    assert sent[0]["status"] == 200 and sent[1]["body"] == b"ok"


async def asgi_path(app, method: str, path: str):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app({"type": "http", "method": method, "path": path, "headers": [], "query_string": b""}, receive, send)
    return sent


async def test_bearer_token_middleware():
    calls = []

    async def inner(scope, receive, send):
        calls.append(scope["method"])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = BearerTokenMiddleware(inner, "s3cret")
    denied = await asgi(app, "POST", [])
    assert denied[0]["status"] == 401
    wrong = await asgi(app, "POST", [(b"authorization", b"Bearer nope")])
    assert wrong[0]["status"] == 401
    ok = await asgi(app, "POST", [(b"authorization", b"Bearer s3cret")])
    assert ok[0]["status"] == 204
    preflight = await asgi(app, "OPTIONS", [])
    assert preflight[0]["status"] == 204
    assert calls == ["POST", "OPTIONS"]


async def test_origin_gate_middleware():
    calls = []

    async def inner(scope, receive, send):
        calls.append(scope["method"])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = OriginGateMiddleware(inner, ["https://page.example"])
    missing = await asgi(app, "POST", [])
    assert missing[0]["status"] == 403 and b"allowed web origin" in missing[1]["body"]
    other = await asgi(app, "POST", [(b"origin", b"https://evil.example")])
    assert other[0]["status"] == 403
    ok = await asgi(app, "POST", [(b"origin", b"https://page.example")])
    assert ok[0]["status"] == 204
    preflight = await asgi(app, "OPTIONS", [])
    assert preflight[0]["status"] == 204
    assert calls == ["POST", "OPTIONS"]


async def test_public_http_app_gates_on_origin_and_serves_health():
    app = http_app(
        create_server(),
        token=None,
        host="0.0.0.0",
        port=9,
        allowed_origins=["https://page.example"],
        allowed_hosts=["*"],
    )
    health = await asgi_path(app, "GET", "/health")
    assert health[0]["status"] == 200
    denied = await asgi(app, "POST", [(b"origin", b"https://evil.example")])
    assert denied[0]["status"] == 403
    token_mode = http_app(create_server(), token="t", host="127.0.0.1", port=9, allowed_origins=[])
    assert (await asgi(token_mode, "POST", [(b"origin", b"https://page.example")]))[0]["status"] == 401


def test_http_app_is_wrapped():
    app = http_app(create_server(), token="t", host="127.0.0.1", port=9, allowed_origins=["http://localhost:5173"])
    assert type(app).__name__ == "CORSMiddleware"
    public = http_app(
        create_server(), token="t", host="0.0.0.0", port=9, allowed_origins=[], allowed_hosts=["*"], stateless=True
    )
    assert type(public).__name__ == "CORSMiddleware"


def test_parse_args(monkeypatch):
    monkeypatch.setenv(server.TOKEN_ENV, "from-env")
    args = parse_args(["--transport", "http", "--allow-origin", "https://a.example"])
    assert args.transport == "http" and args.token == "from-env" and args.allow_origin == ["https://a.example"]
    assert args.stateless is False and args.call_timeout is None and args.max_concurrency is None
    with pytest.raises(SystemExit):
        parse_args(["--transport", "carrier-pigeon"])


def test_parse_args_from_the_environment():
    env = {
        "PORT": "8080",
        "MEGANE_BUILDER_TOOLS_TRANSPORT": "http",
        "MEGANE_BUILDER_TOOLS_HOST": "0.0.0.0",
        "MEGANE_BUILDER_TOOLS_TOKEN": "t",
        "MEGANE_BUILDER_TOOLS_ALLOWED_ORIGINS": "https://a.example, https://b.example",
        "MEGANE_BUILDER_TOOLS_ALLOWED_HOSTS": "*",
        "MEGANE_BUILDER_TOOLS_STATELESS": "true",
        "MEGANE_BUILDER_TOOLS_CALL_TIMEOUT": "100",
        "MEGANE_BUILDER_TOOLS_MAX_CONCURRENCY": "2",
    }
    args = parse_args([], env)
    assert (args.transport, args.host, args.port, args.token) == ("http", "0.0.0.0", 8080, "t")
    assert args.allow_origin == ["https://a.example", "https://b.example"]
    assert args.allowed_host == ["*"]
    assert args.stateless is True and args.call_timeout == 100.0 and args.max_concurrency == 2
    assert parse_args(["--port", "9"], env).port == 9


def test_loopback_detection():
    assert server.is_loopback("127.0.0.1") and server.is_loopback("::1") and server.is_loopback("localhost")
    assert not server.is_loopback("0.0.0.0") and not server.is_loopback("example.com")


def test_public_bind_needs_a_token(monkeypatch, capsys):
    monkeypatch.delenv(server.TOKEN_ENV, raising=False)
    assert server.main(["--transport", "http", "--host", "0.0.0.0"]) == 2
    assert "without a token" in capsys.readouterr().err


def test_public_mode(monkeypatch, capsys):
    import uvicorn

    ran = {}
    monkeypatch.delenv(server.TOKEN_ENV, raising=False)
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port, **kw: ran.update(app=app, host=host))
    public = ["--transport", "http", "--host", "0.0.0.0", "--public"]
    assert server.main(public) == 2
    assert "--allow-origin" in capsys.readouterr().err
    assert server.main([*public, "--allow-origin", "https://page.example", "--token", "t"]) == 2
    assert "without a token" in capsys.readouterr().err
    assert ran == {}
    assert server.main([*public, "--allow-origin", "https://page.example"]) == 0
    assert ran["host"] == "0.0.0.0"
    assert "bearer token" not in capsys.readouterr().err
    assert parse_args([], {"MEGANE_BUILDER_TOOLS_PUBLIC": "1"}).public is True
    assert parse_args([], {}).public is False


def test_main_passes_limits_and_proxy_options(monkeypatch):
    import uvicorn

    seen = {}
    real_create = server.create_server
    monkeypatch.setattr(server, "create_server", lambda limits: seen.setdefault("limits", limits) and real_create())
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port, **kw: seen.update(host=host, **kw))
    argv = ["--transport", "http", "--host", "0.0.0.0", "--token", "t", "--allowed-host", "*", "--stateless"]
    assert server.main([*argv, "--call-timeout", "90", "--max-concurrency", "1"]) == 0
    assert seen["limits"].timeout == 90 and seen["limits"].max_concurrency == 1
    assert seen["host"] == "0.0.0.0" and seen["proxy_headers"] is True


def test_main_stdio(monkeypatch):
    ran = []
    monkeypatch.setattr(server.MCPServer, "run", lambda self, transport: ran.append(transport))
    assert server.main([]) == 0
    assert ran == ["stdio"]


def test_main_http_generates_a_token(monkeypatch, capsys):
    import uvicorn

    ran = {}
    monkeypatch.delenv(server.TOKEN_ENV, raising=False)
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port, **kw: ran.update(host=host, port=port))
    assert server.main(["--transport", "http", "--port", "9999"]) == 0
    assert ran == {"host": "127.0.0.1", "port": 9999}
    assert "bearer token" in capsys.readouterr().err

    ran.clear()
    assert server.main(["--transport", "http", "--token", "given"]) == 0
    assert "bearer token" not in capsys.readouterr().err
