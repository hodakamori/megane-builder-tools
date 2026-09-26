"""``megane-builder-conformance``: check an MCP server against the Builder Tool Contract.

megane-builder-conformance -- uvx megane-builder-tools
megane-builder-conformance --url http://127.0.0.1:8765/mcp --token TOKEN
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict

from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from .checker import Report, check_server


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="megane-builder-conformance", description=__doc__.splitlines()[0])
    parser.add_argument("--url", help="Streamable HTTP endpoint of the server")
    parser.add_argument("--token", help="bearer token for --url")
    parser.add_argument("--no-call", action="store_true", help="only check the tool definitions")
    parser.add_argument("--tool", action="append", default=None, help="check only this tool (repeatable)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="stdio server command, after --")
    args = parser.parse_args(argv)
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if bool(args.url) == bool(args.command):
        parser.error("give either --url or a server command after --")
    return args


def client_for(args: argparse.Namespace) -> Client:
    if args.url:
        headers = {"Authorization": f"Bearer {args.token}"} if args.token else None
        return Client(streamable_http_client(args.url, http_client=create_mcp_http_client(headers=headers)))
    return Client(StdioServerParameters(command=args.command[0], args=args.command[1:]))


async def run(args: argparse.Namespace) -> Report:
    async with client_for(args) as client:
        return await check_server(client, call=not args.no_call, only=args.tool)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = asyncio.run(run(args))
    if args.json:
        print(json.dumps({"ok": report.ok, **asdict(report)}, indent=2))
    else:
        print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
