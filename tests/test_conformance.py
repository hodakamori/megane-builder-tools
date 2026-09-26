from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from typing import Any

import pytest
from mcp import Client
from mcp_types import CallToolResult, TextContent, Tool

from megane_builder_tools.conformance import checker, cli
from megane_builder_tools.conformance.checker import (
    Report,
    check_result,
    check_runtime,
    check_server,
    check_tool_static,
    sample_arguments,
    sample_document,
    sample_value,
)
from megane_builder_tools.sdk import META_KEY
from megane_builder_tools.server import create_server

OUTPUT = {"properties": {"contract": {}, "name": {}, "structure": {}}}


def marker(**over: Any) -> dict[str, Any]:
    m = {"contract": 1, "category": "bulk", "apply": "new_document", "document": "none", "stochastic": False}
    m.update(over)
    return {META_KEY: m}


def tool(properties: dict[str, Any], *, required=(), meta=None, output=OUTPUT, defs=None, **kw) -> Tool:
    schema: dict[str, Any] = {"type": "object", "properties": properties, "required": list(required)}
    if defs:
        schema["$defs"] = defs
    return Tool(
        name=kw.pop("name", "t"),
        title=kw.pop("title", "T"),
        description=kw.pop("description", "does things"),
        input_schema=kw.pop("input_schema", schema),
        output_schema=output,
        _meta=marker() if meta is None else meta,
    )


def messages(t: Tool) -> list[str]:
    report = Report(server="s")
    check_tool_static(t, report)
    return [f.message for f in report.findings]


async def test_reference_server_passes():
    async with Client(create_server()) as client:
        report = await check_server(client)
    assert report.ok, report.render()
    assert report.builder_tools == ["liquid_box", "polymer_chain", "solvate"]
    assert "PASS" in report.render()


async def test_only_and_static_mode():
    async with Client(create_server()) as client:
        report = await check_server(client, call=False, only=["solvate"])
    assert report.builder_tools == ["solvate"]


def test_non_builder_tools_are_ignored_and_markers_validated():
    assert messages(tool({}, meta={})) == []
    assert messages(tool({}, meta={META_KEY: "x"})) == [f"_meta[{META_KEY!r}] must be an object"]
    assert "this checker implements contract 1" in messages(tool({}, meta=marker(contract=2)))[0]
    bad = messages(tool({}, meta=marker(category="x", apply="y", document="z", stochastic="no", expectedSeconds=-1)))
    assert len(bad) == 6  # "no" is truthy, so the missing seed is reported too


def test_descriptive_fields():
    found = messages(tool({}, title="", description=" "))
    assert any("no 'title'" in m for m in found) and "no 'description'" in found
    assert "inputSchema must be an object schema" in messages(tool({}, input_schema={"type": "array"}))
    assert "outputSchema must be the BuilderResult schema" in messages(tool({}, output=None))


@pytest.mark.parametrize(
    ("prop", "expected"),
    [
        ({"anyOf": [{"type": "string"}, {"type": "null"}]}, "uses 'anyOf'"),
        ({"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 3}, "minItems == maxItems"),
        ({"type": "array", "items": {"type": "number"}, "minItems": 12, "maxItems": 12}, "minItems == maxItems"),
        ({"type": "array", "items": {"type": "string"}}, "unsupported array item type"),
        ({"type": "object", "properties": {"inner": {"type": "object", "properties": {}}}}, "nest only one level"),
        ({"type": "null"}, "unsupported or missing type"),
        ({"type": "array", "items": {"type": "object", "properties": {"q": {"oneOf": []}}}}, "uses 'oneOf'"),
    ],
)
def test_form_subset(prop, expected):
    assert any(expected in m for m in messages(tool({"p": prop})))


def test_supported_forms_pass():
    props = {
        "n": {"type": "number"},
        "s": {"type": "string"},
        "b": {"type": "boolean"},
        "v": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
        "rows": {"type": "array", "items": {"$ref": "#/$defs/Row"}},
        "group": {"type": "object", "properties": {"x": {"type": "integer"}}},
        "picks": {"type": "array", "items": {"type": "integer", "x-megane-widget": "element"}},
    }
    defs = {"Row": {"type": "object", "properties": {"k": {"type": "integer"}}}}
    assert messages(tool(props, defs=defs)) == []


MOL = {"type": "object", "properties": {"name": {}, "molblock": {}, "smiles": {}}, "x-megane-widget": "molecule"}
DOC = {"type": "object", "properties": {"elements": {}, "positions": {}, "cell": {}, "bonds": {}}}


@pytest.mark.parametrize(
    ("prop", "expected"),
    [
        ({"type": "object", "x-megane-widget": "molecule", "properties": {"name": {}}}, "must accept 'name'"),
        ({"type": "object", "x-megane-widget": "document", "properties": {}}, "must be the Structure"),
        ({"type": "number", "x-megane-widget": "element"}, "must be an integer"),
        ({"type": "array", "x-megane-widget": "cell", "minItems": 3, "maxItems": 3}, "exactly 9 numbers"),
        ({"type": "integer", "x-megane-widget": "selection"}, "array of atom indices"),
        ({"type": "integer", "x-megane-widget": "atom", "x-megane-of": "nothing"}, "must name a molecule"),
        ({"type": "string", "x-megane-widget": "sparkles"}, "unknown widget"),
    ],
)
def test_widget_shapes(prop, expected):
    assert any(expected in m for m in messages(tool({"p": prop})))


def test_atom_widget_inside_array_rows():
    row = {
        "type": "object",
        "properties": {"mol": MOL, "at": {"type": "integer", "x-megane-widget": "atom", "x-megane-of": "mol"}},
    }
    assert messages(tool({"rows": {"type": "array", "items": row}})) == []
    nested = {"type": "object", "properties": {"mol": MOL}}
    assert messages(tool({"group": nested})) == []


def test_seed_and_document_rules():
    seed = {"type": "integer", "x-megane-widget": "seed"}
    assert any("top-level 'seed'" in m for m in messages(tool({}, meta=marker(stochastic=True))))
    assert any("not marked stochastic" in m for m in messages(tool({"seed": seed})))
    assert messages(tool({"seed": seed}, meta=marker(stochastic=True))) == []

    doc = {**DOC, "x-megane-widget": "document"}
    assert any("document 'none'" in m for m in messages(tool({"d": doc})))
    assert any("has no document property" in m for m in messages(tool({}, meta=marker(document="required"))))
    assert any("must be required" in m for m in messages(tool({"d": doc}, meta=marker(document="required"))))
    assert any(
        "must be optional" in m for m in messages(tool({"d": doc}, required=["d"], meta=marker(document="optional")))
    )
    assert any(
        "at most one document" in m for m in messages(tool({"d": doc, "e": doc}, meta=marker(document="required")))
    )
    optional = {"anyOf": [{"$ref": "#/$defs/S"}, {"type": "null"}], "x-megane-widget": "document", "default": None}
    assert messages(tool({"d": optional}, meta=marker(document="optional"), defs={"S": DOC})) == []
    assert any("insert tool usually" in m for m in messages(tool({}, meta=marker(apply="insert"))))


def test_sample_values():
    root = {"$defs": {"M": {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}}}
    assert sample_value({"$ref": "#/$defs/M"}, root) == {"a": 1}
    assert sample_value({"type": "integer", "examples": [5]}, root) == 5
    assert sample_value({"type": "number", "exclusiveMinimum": 0}, root) == 1.0
    assert sample_value({"type": "integer", "minimum": 3}, root) == 3
    assert sample_value({"type": "string", "enum": ["b", "c"]}, root) == "b"
    assert sample_value({"type": "string"}, root) == "x"
    assert sample_value({"type": "boolean"}, root) is False
    assert sample_value({"type": "array", "items": {"type": "integer"}, "minItems": 2}, root) == [1, 1]
    assert sample_value({"x-megane-widget": "cell"}, root)[0] == 20.0
    assert sample_value({}, root) is None
    assert sample_value({"x-megane-widget": "document"}, root)["cell"][0] == 20.0
    assert sample_arguments({"properties": {"a": {"type": "integer"}, "b": {"type": "integer", "examples": [2]}}}) == {
        "b": 2
    }
    assert len(sample_document()["elements"]) == 3


def result(payload: dict[str, Any] | None, *, error: bool = False, text: bool = True) -> CallToolResult:
    content = [TextContent(type="text", text="summary")] if text else []
    return CallToolResult(content=content, structured_content=payload, is_error=error)


def good_payload(positions=(0, 0, 0)) -> dict[str, Any]:
    return {
        "contract": 1,
        "name": "x",
        "structure": {"elements": [1], "positions": list(positions), "cell": None, "bonds": []},
    }


def test_result_checks():
    report = Report(server="s")
    check_result("t", {}, {}, result(None, error=True), report)
    check_result("t", {}, {}, result({"contract": 1}), report)
    check_result("t", {}, {}, result(good_payload(), text=False), report)
    empty = good_payload()
    empty["structure"].update(elements=[], positions=[])
    check_result("t", {}, {}, result(empty), report)
    doc = sample_document()
    doc_atom = doc["positions"][:3]
    check_result("t", {"apply": "insert"}, {"document": doc}, result(good_payload(doc_atom)), report)
    msgs = [f.message for f in report.findings]
    assert msgs[0].startswith("call with sample arguments failed")
    assert "not a valid BuilderResult" in msgs[1]
    assert "no text summary in content" in msgs
    assert "the sample call returned no atoms" in msgs
    assert "must not repeat the document's atoms" in msgs[-1]


class FakeClient:
    def __init__(self, results: list[Any]):
        self.results = results
        self.server_info = SimpleNamespace(name="fake")

    async def call_tool(self, name, args, progress_callback=None):
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def list_tools(self):
        return SimpleNamespace(tools=[])


async def test_runtime_findings():
    t = tool({"seed": {"type": "integer", "x-megane-widget": "seed"}}, required=["seed"], meta=marker(stochastic=True))
    m = t.meta[META_KEY]
    report = Report(server="s")
    await check_runtime(
        FakeClient([result(good_payload()), result(good_payload((1, 1, 1))), result(good_payload())]), t, m, report
    )
    msgs = [f.message for f in report.findings]
    assert "sent no progress notifications" in msgs
    assert "the same arguments and seed gave a different structure" in msgs
    assert "a call without the required arguments succeeded" in msgs

    quiet = Report(server="s")
    await check_runtime(
        FakeClient([result(good_payload()), result(good_payload()), RuntimeError("rejected")]), t, m, quiet
    )
    assert [f.message for f in quiet.findings] == ["sent no progress notifications"]

    failing = Report(server="s")
    await check_runtime(FakeClient([result(None, error=True)]), t, m, failing)
    assert len(failing.findings) == 1 and not failing.ok


async def test_server_without_builder_tools():
    report = await check_server(FakeClient([]))
    assert not report.ok
    assert "no tool carries" in report.findings[0].message
    assert report.server == "fake"


def test_cli_arguments():
    args = cli.parse_args(["--", "uvx", "megane-builder-tools"])
    assert args.command == ["uvx", "megane-builder-tools"]
    assert cli.client_for(args).server.command == "uvx"
    http = cli.parse_args(["--url", "http://127.0.0.1:1/mcp", "--token", "t"])
    assert http.command == []
    assert cli.client_for(http) is not None
    assert cli.client_for(cli.parse_args(["--url", "http://x/mcp"])) is not None
    with pytest.raises(SystemExit):
        cli.parse_args([])
    with pytest.raises(SystemExit):
        cli.parse_args(["--url", "http://x", "--", "cmd"])


@pytest.mark.parametrize("as_json", [False, True])
def test_cli_main(monkeypatch, capsys, as_json):
    ok = Report(server="s", builder_tools=["a"])
    monkeypatch.setattr(cli, "run", lambda args: _done(ok))
    argv = ["--json", "--", "x"] if as_json else ["--", "x"]
    assert cli.main(argv) == 0
    out = capsys.readouterr().out
    if as_json:
        assert json.loads(out)["ok"] is True
    else:
        assert "Result: PASS" in out
    failed = copy.deepcopy(ok)
    failed.add("error", "a", "broken")
    monkeypatch.setattr(cli, "run", lambda args: _done(failed))
    assert cli.main(["--", "x"]) == 1


async def _done(report):
    return report


async def test_cli_run_in_process(monkeypatch):
    monkeypatch.setattr(cli, "client_for", lambda args: Client(create_server()))
    report = await cli.run(cli.parse_args(["--no-call", "--", "unused"]))
    assert report.ok


def test_checker_module_constants():
    assert checker.SAMPLE_MOLECULE["name"] == "water"
