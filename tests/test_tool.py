from __future__ import annotations

import inspect
from typing import Annotated

import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer
from pydantic import Field

from megane_builder_tools.sdk import (
    META_KEY,
    BuilderResult,
    DocumentInput,
    MoleculeInput,
    OptionalDocumentInput,
    Progress,
    Seed,
    Structure,
    ToolError,
    builder_tool,
)
from megane_builder_tools.sdk.tool import default_summary, widget_of


def atoms(n: int = 1, cell=None) -> Structure:
    return Structure.from_arrays([1] * n, [[float(i), 0.0, 0.0] for i in range(n)], cell=cell)


@builder_tool(title="Make", category="bulk", apply="new_document", expected_seconds=2)
def make(count: Annotated[int, Field(ge=1)], seed: Seed, progress: Progress = Progress()) -> BuilderResult:  # noqa: B008
    """Make hydrogen atoms."""
    progress(0.5, "half")
    return BuilderResult(name="h", structure=atoms(count))


@builder_tool(title="Add", category="other", apply="insert")
async def add(document: DocumentInput, fail: bool = False) -> BuilderResult:
    """Add one atom."""
    if fail:
        raise ToolError("asked to fail")
    return BuilderResult(name="one", structure=atoms(1), warnings=["careful"]).with_summary("custom")


@builder_tool(title="Maybe", category="molecule", apply="new_document", name="maybe_doc")
def maybe(molecule: MoleculeInput, document: OptionalDocumentInput = None) -> BuilderResult:
    """Uses the document when given."""
    return "not a result"  # type: ignore[return-value]


def test_marker_is_derived_from_the_signature():
    assert make.meta == {
        META_KEY: {
            "contract": 1,
            "category": "bulk",
            "apply": "new_document",
            "document": "none",
            "stochastic": True,
            "expectedSeconds": 2,
        }
    }
    assert add.meta[META_KEY]["document"] == "required"
    assert add.meta[META_KEY]["stochastic"] is False
    assert "expectedSeconds" not in add.meta[META_KEY]
    assert maybe.name == "maybe_doc"
    assert maybe.meta[META_KEY]["document"] == "optional"
    assert make.progress_param == "progress"
    assert make.description == "Make hydrogen atoms."


def test_direct_call_passes_through():
    result = make(2, seed=1, progress=Progress())
    assert result.structure.n_atoms == 2


def test_handler_signature_hides_progress_and_adds_context():
    sig = inspect.signature(make.handler())
    assert "progress" not in sig.parameters
    assert "ctx" in sig.parameters
    assert widget_of(sig.parameters["seed"].annotation) == "seed"
    assert widget_of(int) is None


@pytest.mark.parametrize(
    ("kwargs", "error", "match"),
    [
        ({"category": "nope", "apply": "new_document"}, ValueError, "unknown category"),
        ({"category": "bulk", "apply": "replace"}, ValueError, "unknown apply mode"),
    ],
)
def test_decorator_arguments_are_validated(kwargs, error, match):
    with pytest.raises(error, match=match):
        builder_tool(title="x", **kwargs)


def test_signature_rules():
    deco = builder_tool(title="x", category="bulk", apply="new_document")

    def wrong_seed(s: Seed) -> BuilderResult:
        """d"""

    def two_docs(a: DocumentInput, b: DocumentInput) -> BuilderResult:
        """d"""

    def no_doc() -> BuilderResult: ...

    def with_ctx(ctx: int) -> BuilderResult:
        """d"""

    with pytest.raises(TypeError, match="must be named 'seed'"):
        deco(wrong_seed)
    with pytest.raises(TypeError, match="at most one document"):
        deco(two_docs)
    with pytest.raises(TypeError, match="needs a description"):
        deco(no_doc)
    with pytest.raises(TypeError, match="no 'ctx' parameter"):
        deco(with_ctx)
    with pytest.raises(TypeError, match="insert tool must take the open document"):
        builder_tool(title="x", category="bulk", apply="insert")(lambda: None)


async def test_run_finishes_results_and_rejects_other_types():
    result = await make.run(count=3, seed=9)
    assert result.provenance["seed"] == 9
    assert "megane-builder-tools" in result.provenance
    other = await add.run(document=atoms(1))
    assert "seed" not in other.provenance
    with pytest.raises(TypeError, match="expected BuilderResult"):
        await maybe.run(molecule=None)


def test_default_summary_mentions_cell_molecules_and_warnings():
    r = BuilderResult(
        name="w",
        structure=Structure.from_arrays(
            [1, 1], [[0, 0, 0], [1, 0, 0]], cell=[[3, 0, 0], [0, 4, 0], [0, 0, 5]], molecules=[0, 1]
        ),
        warnings=["w1"],
    )
    text = default_summary("Tool", r)
    assert "2 atoms" in text and "2 molecules" in text and "3.00 x 4.00 x 5.00" in text and "w1" in text
    assert "no cell" in default_summary("Tool", BuilderResult(name="x", structure=atoms(1)))


async def test_tools_over_mcp():
    server = MCPServer("test")
    for tool in (make, add):
        tool.register(server)
    async with Client(server) as client:
        listed = {t.name: t for t in (await client.list_tools()).tools}
        assert listed["make"].meta[META_KEY]["stochastic"] is True
        assert listed["make"].title == "Make"
        assert listed["make"].output_schema["title"] == "BuilderResult"
        assert "progress" not in listed["make"].input_schema["properties"]

        updates = []

        async def on_progress(value, total, message):
            updates.append((value, message))

        result = await client.call_tool("make", {"count": 2, "seed": 4}, progress_callback=on_progress)
        assert not result.is_error
        assert result.structured_content["structure"]["cell"] is None
        assert "bondOrders" not in result.structured_content["structure"]
        assert result.content[0].text.startswith("Make: built 'h' (2 atoms")
        assert updates == [(0.5, "half")]

        doc = atoms(2).model_dump(by_alias=True)
        added = await client.call_tool("add", {"document": doc})
        assert added.content[0].text == "custom"
        assert added.structured_content["warnings"] == ["careful"]
        failed = await client.call_tool("add", {"document": doc, "fail": True})
        assert failed.is_error and "asked to fail" in failed.content[0].text


def test_progress_without_context_records():
    p = Progress()
    p(1.5, "clamped")
    assert p.reports == [(1.0, "clamped")]
