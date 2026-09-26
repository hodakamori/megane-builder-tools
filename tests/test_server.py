from __future__ import annotations

import pytest
from mcp import Client

from megane_builder_tools import server
from megane_builder_tools.server import TOOLS, BearerTokenMiddleware, create_server, http_app, parse_args


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


def test_http_app_is_wrapped():
    app = http_app(create_server(), token="t", host="127.0.0.1", port=9, allowed_origins=["http://localhost:5173"])
    assert type(app).__name__ == "CORSMiddleware"


def test_parse_args(monkeypatch):
    monkeypatch.setenv(server.TOKEN_ENV, "from-env")
    args = parse_args(["--transport", "http", "--allow-origin", "https://a.example"])
    assert args.transport == "http" and args.token == "from-env" and args.allow_origin == ["https://a.example"]
    with pytest.raises(SystemExit):
        parse_args(["--transport", "carrier-pigeon"])


def test_main_stdio(monkeypatch):
    ran = []
    monkeypatch.setattr(server.MCPServer, "run", lambda self, transport: ran.append(transport))
    assert server.main([]) == 0
    assert ran == ["stdio"]


def test_main_http_generates_a_token(monkeypatch, capsys):
    import uvicorn

    ran = {}
    monkeypatch.delenv(server.TOKEN_ENV, raising=False)
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port, log_level: ran.update(host=host, port=port))
    assert server.main(["--transport", "http", "--port", "9999"]) == 0
    assert ran == {"host": "127.0.0.1", "port": 9999}
    assert "bearer token" in capsys.readouterr().err

    ran.clear()
    assert server.main(["--transport", "http", "--token", "given"]) == 0
    assert "bearer token" not in capsys.readouterr().err
