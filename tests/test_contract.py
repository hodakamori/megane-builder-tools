from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

from megane_builder_tools.sdk import Cell, Element, Length, Seed, Selection, atom_of, unit, widget
from megane_builder_tools.sdk.contract import OF_KEY, UNIT_KEY, WIDGET_KEY


class Args(BaseModel):
    seed: Seed
    element: Element
    cell: Cell
    selection: Selection
    length: Length
    head: Annotated[int, atom_of("monomer")]
    density: Annotated[float, unit("g/cm3")]
    doc: Annotated[dict, widget("document", extra="y")]


def test_annotations_reach_the_json_schema():
    props = Args.model_json_schema()["properties"]
    assert props["seed"][WIDGET_KEY] == "seed" and props["seed"]["minimum"] == 0
    assert props["element"][WIDGET_KEY] == "element" and props["element"]["maximum"] == 118
    assert props["cell"]["minItems"] == props["cell"]["maxItems"] == 9
    assert props["selection"][WIDGET_KEY] == "selection"
    assert props["length"][UNIT_KEY] == "angstrom"
    assert props["head"][WIDGET_KEY] == "atom" and props["head"][OF_KEY] == "monomer"
    assert props["density"][UNIT_KEY] == "g/cm3"
    assert props["doc"]["extra"] == "y"


def test_widget_and_unit_merge_with_other_fields():
    class M(BaseModel):
        n: Annotated[int, Field(ge=1, title="N"), unit("count")]

    prop = M.model_json_schema()["properties"]["n"]
    assert prop == {"minimum": 1, "title": "N", "type": "integer", UNIT_KEY: "count"}
