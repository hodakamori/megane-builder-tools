"""Check an MCP server against the megane Builder Tool Contract (§3–§6, §10).

Static checks read ``tools/list``; runtime checks call every Builder tool with
sample arguments built from its schema (property ``examples`` first, then
``default``, then a generic value for the property's type or widget) and
validate what comes back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from mcp import Client
from mcp_types import CallToolResult, Tool
from pydantic import ValidationError
from rdkit import Chem
from rdkit.Chem import rdDistGeom

from ..sdk.contract import (
    APPLY_MODES,
    CATEGORIES,
    CONTRACT_VERSION,
    DOCUMENT_USES,
    META_KEY,
    OF_KEY,
    WIDGET_KEY,
    WIDGETS,
)
from ..sdk.models import BuilderResult, Structure

Level = Literal["error", "warning", "info"]

_UNSUPPORTED_FORM_KEYWORDS = ("oneOf", "anyOf", "allOf", "if", "not")


@dataclass
class Finding:
    level: Level
    tool: str | None
    message: str

    def __str__(self) -> str:
        where = f"[{self.tool}] " if self.tool else ""
        return f"{self.level.upper():7} {where}{self.message}"


@dataclass
class Report:
    server: str
    builder_tools: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(f.level == "error" for f in self.findings)

    def add(self, level: Level, tool: str | None, message: str) -> None:
        self.findings.append(Finding(level, tool, message))

    def render(self) -> str:
        lines = [f"Server: {self.server}", f"Builder tools: {', '.join(self.builder_tools) or '(none)'}"]
        lines += [str(f) for f in self.findings]
        errors = sum(f.level == "error" for f in self.findings)
        warnings = sum(f.level == "warning" for f in self.findings)
        lines.append(f"Result: {'PASS' if self.ok else 'FAIL'} ({errors} errors, {warnings} warnings)")
        return "\n".join(lines)


# ── samples ──────────────────────────────────────────────────────────────


def _sample_molblock() -> str:
    """Water with explicit hydrogens and 3D coordinates, header dimension code 3D."""
    mol = Chem.AddHs(Chem.MolFromSmiles("O"))
    params = rdDistGeom.ETKDGv3()
    params.randomSeed = 7
    rdDistGeom.EmbedMolecule(mol, params)
    return Chem.MolToMolBlock(mol)


SAMPLE_MOLECULE: dict[str, Any] = {"name": "water", "molblock": _sample_molblock()}


def sample_document() -> dict[str, Any]:
    """A water molecule in the middle of a 20 Å cubic cell, as a wire Structure."""
    mol = Chem.MolFromMolBlock(SAMPLE_MOLECULE["molblock"], removeHs=False)
    positions = mol.GetConformer().GetPositions() + 10.0
    return {
        "elements": [a.GetAtomicNum() for a in mol.GetAtoms()],
        "positions": positions.reshape(-1).tolist(),
        "cell": [20.0, 0, 0, 0, 20.0, 0, 0, 0, 20.0],
        "bonds": [[b.GetBeginAtomIdx(), b.GetEndAtomIdx()] for b in mol.GetBonds()],
    }


_WIDGET_SAMPLES: dict[str, Any] = {
    "molecule": SAMPLE_MOLECULE,
    "atom": 0,
    "element": 6,
    "cell": [20.0, 0, 0, 0, 20.0, 0, 0, 0, 20.0],
    "selection": [],
    "seed": 1,
}


def _resolve(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    """Follow a local ``$ref`` (``#/$defs/...``); siblings of ``$ref`` are kept."""
    ref = schema.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return schema
    target: Any = root
    for part in ref[2:].split("/"):
        target = target.get(part, {}) if isinstance(target, dict) else {}
    merged = {**target, **{k: v for k, v in schema.items() if k != "$ref"}}
    return _resolve(merged, root) if "$ref" in target else merged


def _non_null(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    """For an optional property (``anyOf`` [X, null]), X merged with the property's own keys."""
    options = [o for o in schema.get("anyOf", []) if o.get("type") != "null"]
    if len(options) != 1:
        return schema
    return {**_resolve(options[0], root), **{k: v for k, v in schema.items() if k != "anyOf"}}


def sample_value(schema: dict[str, Any], root: dict[str, Any]) -> Any:
    """A plausible value for a property schema."""
    schema = _resolve(schema, root)
    if isinstance(schema.get("examples"), list) and schema["examples"]:
        return schema["examples"][0]
    if "default" in schema:
        return schema["default"]
    widget = schema.get(WIDGET_KEY)
    if widget == "document":
        return sample_document()
    if widget in _WIDGET_SAMPLES:
        return _WIDGET_SAMPLES[widget]
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind in ("number", "integer"):
        low = schema.get("minimum", schema.get("exclusiveMinimum", 0))
        value = max(low, 1) if kind == "integer" else float(low) + 1.0
        return int(value) if kind == "integer" else value
    if kind == "boolean":
        return False
    if kind == "string":
        return "x"
    if kind == "array":
        count = int(schema.get("minItems", 1))
        return [sample_value(schema.get("items", {}), root) for _ in range(count)]
    if kind == "object":
        return sample_arguments(schema, root)
    return None


def sample_arguments(schema: dict[str, Any], root: dict[str, Any] | None = None) -> dict[str, Any]:
    """Arguments for every required property (and properties with examples)."""
    root = root if root is not None else schema
    props: dict[str, Any] = schema.get("properties", {})
    required = set(schema.get("required", []))
    return {
        name: sample_value(prop, root)
        for name, prop in props.items()
        if name in required or "examples" in _resolve(prop, root)
    }


# ── static checks ────────────────────────────────────────────────────────


def _check_marker(tool: Tool, report: Report) -> dict[str, Any] | None:
    marker = (tool.meta or {}).get(META_KEY)
    if marker is None:
        return None
    name = tool.name
    if not isinstance(marker, dict):
        report.add("error", name, f"_meta[{META_KEY!r}] must be an object")
        return None
    contract = marker.get("contract")
    if contract != CONTRACT_VERSION:
        report.add("error", name, f"contract is {contract!r}; this checker implements contract {CONTRACT_VERSION}")
        return None
    for key, allowed in (("category", CATEGORIES), ("apply", APPLY_MODES), ("document", DOCUMENT_USES)):
        if marker.get(key) not in allowed:
            report.add("error", name, f"marker {key!r} is {marker.get(key)!r}; expected one of {allowed}")
    if not isinstance(marker.get("stochastic"), bool):
        report.add("error", name, "marker 'stochastic' must be a boolean")
    expected = marker.get("expectedSeconds")
    if expected is not None and (not isinstance(expected, int | float) or expected <= 0):
        report.add("error", name, "marker 'expectedSeconds' must be a positive number")
    return marker


def _check_form(name: str, prop: str, schema: dict[str, Any], root: dict[str, Any], depth: int, report: Report) -> None:
    """Properties without a widget must stay inside the form-generation subset (§4.1)."""
    schema = _resolve(schema, root)
    if schema.get(WIDGET_KEY):
        return
    for keyword in _UNSUPPORTED_FORM_KEYWORDS:
        if keyword in schema:
            report.add("error", name, f"property {prop!r} uses {keyword!r}; Builder cannot build a form for it")
            return
    kind = schema.get("type")
    if kind in ("number", "integer", "boolean"):
        return
    if kind == "string":
        return
    if kind == "array":
        items = _resolve(schema.get("items", {}), root)
        if items.get(WIDGET_KEY):
            return
        if items.get("type") in ("number", "integer"):
            if schema.get("minItems") != schema.get("maxItems") or (schema.get("maxItems") or 99) > 9:
                report.add("error", name, f"property {prop!r}: number arrays need minItems == maxItems <= 9")
            return
        if items.get("type") == "object":
            for sub, sub_schema in items.get("properties", {}).items():
                _check_form(name, f"{prop}[].{sub}", sub_schema, root, depth + 1, report)
            return
        report.add("error", name, f"property {prop!r}: unsupported array item type {items.get('type')!r}")
        return
    if kind == "object":
        if depth >= 1:
            report.add("error", name, f"property {prop!r}: objects may nest only one level")
            return
        for sub, sub_schema in schema.get("properties", {}).items():
            _check_form(name, f"{prop}.{sub}", sub_schema, root, depth + 1, report)
        return
    report.add("error", name, f"property {prop!r}: unsupported or missing type {kind!r}")


def _widget_properties(schema: dict[str, Any], root: dict[str, Any], prefix: str = "") -> dict[str, dict[str, Any]]:
    """All properties carrying a widget, keyed by dotted path (array items as ``name[]``)."""
    found: dict[str, dict[str, Any]] = {}
    for name, prop in schema.get("properties", {}).items():
        resolved = _resolve(prop, root)
        path = f"{prefix}{name}"
        if resolved.get(WIDGET_KEY):
            found[path] = resolved
            continue
        if resolved.get("type") == "array":
            items = _resolve(resolved.get("items", {}), root)
            if items.get(WIDGET_KEY):
                found[f"{path}[]"] = items
            elif items.get("type") == "object":
                found |= _widget_properties(items, root, f"{path}[].")
        elif resolved.get("type") == "object":
            found |= _widget_properties(resolved, root, f"{path}.")
    return found


def _check_widget(
    name: str, path: str, schema: dict[str, Any], siblings: dict[str, Any], root: dict[str, Any], report: Report
) -> None:
    schema = _non_null(schema, root)
    widget = schema[WIDGET_KEY]
    props = schema.get("properties", {})
    if widget not in WIDGETS:
        report.add("warning", name, f"property {path!r}: unknown widget {widget!r}; Builder shows a plain control")
    elif widget == "molecule" and not {"name", "molblock"} <= set(props):
        report.add("error", name, f"property {path!r}: a molecule must accept 'name' and 'molblock'")
    elif widget == "document" and not {"elements", "positions", "cell", "bonds"} <= set(props):
        report.add("error", name, f"property {path!r}: a document must be the Structure schema")
    elif widget in ("atom", "element", "seed") and schema.get("type") != "integer":
        report.add("error", name, f"property {path!r}: widget {widget!r} must be an integer")
    elif widget == "cell" and (schema.get("minItems"), schema.get("maxItems")) != (9, 9):
        report.add("error", name, f"property {path!r}: a cell must be an array of exactly 9 numbers")
    elif widget == "selection" and schema.get("type") != "array":
        report.add("error", name, f"property {path!r}: a selection must be an array of atom indices")
    if widget == "atom":
        target = schema.get(OF_KEY)
        if not isinstance(target, str) or siblings.get(target, {}).get(WIDGET_KEY) != "molecule":
            report.add("error", name, f"property {path!r}: {OF_KEY} must name a molecule property, got {target!r}")


def check_tool_static(tool: Tool, report: Report) -> dict[str, Any] | None:
    """Check one tool definition; returns its marker when it is a Builder tool."""
    marker = _check_marker(tool, report)
    if marker is None:
        return None
    name = tool.name
    if not tool.title:
        report.add("warning", name, "no 'title'; Builder labels the button with the tool name")
    if not (tool.description or "").strip():
        report.add("error", name, "no 'description'")
    root = tool.input_schema
    if root.get("type") != "object":
        report.add("error", name, "inputSchema must be an object schema")
        return marker
    top = root.get("properties", {})
    for prop, schema in top.items():
        _check_form(name, prop, schema, root, 0, report)

    widgets = _widget_properties(root, root)
    for path, schema in widgets.items():
        parent = path.rsplit(".", 1)[0] if "." in path else ""
        siblings = top
        if parent:
            parent_schema = _resolve(top.get(parent.split("[")[0], {}), root)
            siblings = _resolve(parent_schema.get("items", parent_schema), root).get("properties", {})
            siblings = {k: _resolve(v, root) for k, v in siblings.items()}
        else:
            siblings = {k: _resolve(v, root) for k, v in top.items()}
        _check_widget(name, path, schema, siblings, root, report)

    seeds = [p for p, s in widgets.items() if s.get(WIDGET_KEY) == "seed"]
    if marker.get("stochastic") and seeds != ["seed"]:
        report.add("error", name, "a stochastic tool must have a top-level 'seed' property with the seed widget")
    if not marker.get("stochastic") and seeds:
        report.add("warning", name, "has a seed property but is not marked stochastic")

    documents = [p for p, s in widgets.items() if s.get(WIDGET_KEY) == "document"]
    use = marker.get("document")
    if len(documents) > 1 or any("." in p or "[" in p for p in documents):
        report.add("error", name, "at most one document property, at the top level")
    elif use == "none" and documents:
        report.add("error", name, "marker says document 'none' but the tool has a document property")
    elif use in ("optional", "required") and not documents:
        report.add("error", name, f"marker says document {use!r} but the tool has no document property")
    elif documents and (documents[0] in root.get("required", [])) != (use == "required"):
        report.add("error", name, f"document property must be {'required' if use == 'required' else 'optional'}")
    if marker.get("apply") == "insert" and use == "none":
        report.add("warning", name, "an insert tool usually needs the open document")

    output = tool.output_schema or {}
    if not {"contract", "name", "structure"} <= set(output.get("properties", {})):
        report.add("error", name, "outputSchema must be the BuilderResult schema")
    return marker


# ── runtime checks ───────────────────────────────────────────────────────


def check_result(
    name: str, marker: dict[str, Any], args: dict[str, Any], result: CallToolResult, report: Report
) -> None:
    if result.is_error:
        text = " ".join(getattr(c, "text", "") for c in result.content)
        report.add("error", name, f"call with sample arguments failed: {text[:300]}")
        return
    if not any(getattr(c, "type", None) == "text" for c in result.content):
        report.add("warning", name, "no text summary in content")
    try:
        parsed = BuilderResult.model_validate(result.structured_content)
    except ValidationError as exc:
        report.add("error", name, f"structuredContent is not a valid BuilderResult: {exc.errors()[0]['msg']}")
        return
    if parsed.structure.n_atoms == 0:
        report.add("warning", name, "the sample call returned no atoms")
    if marker.get("apply") == "insert":
        documents = [k for k, v in args.items() if isinstance(v, dict) and {"elements", "positions"} <= set(v)]
        if documents:
            doc = Structure.model_validate(args[documents[0]])
            if doc.n_atoms and parsed.structure.n_atoms:
                d = np.linalg.norm(
                    parsed.structure.positions_array()[:, None, :] - doc.positions_array()[None, :, :], axis=2
                )
                if float(d.min()) < 0.1:
                    report.add("error", name, "an insert result must not repeat the document's atoms")


async def check_runtime(client: Client, tool: Tool, marker: dict[str, Any], report: Report) -> None:
    name = tool.name
    args = sample_arguments(tool.input_schema)
    updates: list[float] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        updates.append(progress)

    result = await client.call_tool(name, args, progress_callback=on_progress)
    check_result(name, marker, args, result, report)
    if result.is_error:
        return
    if not updates:
        report.add("info", name, "sent no progress notifications")
    if marker.get("stochastic"):
        again = await client.call_tool(name, args)
        first = (result.structured_content or {}).get("structure", {}).get("positions")
        second = (again.structured_content or {}).get("structure", {}).get("positions")
        if first != second:
            report.add("warning", name, "the same arguments and seed gave a different structure")
    try:
        bad = await client.call_tool(name, {})
        if not bad.is_error and tool.input_schema.get("required"):
            report.add("error", name, "a call without the required arguments succeeded")
    except Exception:  # a JSON-RPC error is an acceptable way to refuse invalid arguments
        pass


async def check_server(client: Client, *, call: bool = True, only: list[str] | None = None) -> Report:
    """Run every check against a connected client."""
    listing = await client.list_tools()
    info = client.server_info
    server_name = getattr(info, "name", None) or "server"
    report = Report(server=server_name)
    for tool in listing.tools:
        if only and tool.name not in only:
            continue
        marker = check_tool_static(tool, report)
        if marker is None:
            continue
        report.builder_tools.append(tool.name)
        if call:
            await check_runtime(client, tool, marker, report)
    if not report.builder_tools:
        report.add("error", None, f"no tool carries _meta[{META_KEY!r}]")
    report.add("info", None, "cancellation is not checked")
    return report
