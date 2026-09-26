"""Record ``tools/list`` and one ``tools/call`` per tool as JSON, for megane's tests.

megane's Builder never needs Python to run its tests: it replays these
responses (contract §10). Re-record after changing a tool's schema or output.

    uv run python scripts/record_fixtures.py fixtures
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import Client

from megane_builder_tools.conformance.checker import sample_arguments
from megane_builder_tools.server import create_server


async def record(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    async with Client(create_server()) as client:
        listing = await client.list_tools()
        dump = [t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in listing.tools]
        (out / "tools-list.json").write_text(json.dumps({"tools": dump}, indent=2) + "\n")
        for tool in listing.tools:
            args = sample_arguments(tool.input_schema)
            result = await client.call_tool(tool.name, args)
            call = {
                "name": tool.name,
                "arguments": args,
                "result": result.model_dump(mode="json", by_alias=True, exclude_none=True),
            }
            (out / f"call-{tool.name}.json").write_text(json.dumps(call, indent=2) + "\n")
            print(f"recorded {tool.name}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(record(Path(sys.argv[1] if len(sys.argv) > 1 else "fixtures")))
