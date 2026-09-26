from __future__ import annotations

import numpy as np
import pytest
from pydantic import ValidationError

from megane_builder_tools.sdk import MAX_ATOMS, BuilderResult, Molecule, Structure


def minimal(**over):
    data = {"elements": [8, 1], "positions": [0, 0, 0, 0.96, 0, 0], "cell": None, "bonds": [(0, 1)]}
    data.update(over)
    return data


def test_structure_round_trip_uses_camel_case():
    s = Structure.model_validate(minimal(bondOrders=[1], residueNames=["WAT", "WAT"]))
    wire = s.model_dump(by_alias=True, exclude_none=True)
    assert wire["bondOrders"] == [1]
    assert wire["residueNames"] == ["WAT", "WAT"]
    assert s.n_atoms == 2
    assert s.positions_array().shape == (2, 3)
    assert s.cell_matrix() is None


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"elements": [0, 1]}, "atomic numbers"),
        ({"positions": [0, 0, 0]}, "positions has 3 values"),
        ({"cell": [1.0, 2.0]}, "cell must have 9"),
        ({"bonds": [(0, 2)]}, "does not join"),
        ({"bonds": [(1, 1)]}, "does not join"),
        ({"bondOrders": [1, 2]}, "parallel"),
        ({"bondOrders": [5]}, "bond orders must be"),
        ({"molecules": [0]}, "molecules has 1"),
        ({"molecules": [0, -1]}, ">= 0"),
        ({"chainIds": ["A", "BB"]}, "single characters"),
        ({"scalars": {"q": [0.1]}}, "scalar channel 'q'"),
    ],
)
def test_structure_rejects_inconsistent_payloads(over, message):
    with pytest.raises(ValidationError, match=message):
        Structure.model_validate(minimal(**over))


def test_structure_rejects_too_many_atoms(monkeypatch):
    monkeypatch.setattr("megane_builder_tools.sdk.models.MAX_ATOMS", 1)
    with pytest.raises(ValidationError, match="exceeds the limit"):
        Structure.model_validate(minimal())
    assert MAX_ATOMS == 500_000


def test_structure_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        Structure.model_validate(minimal(extra=1))


def test_from_arrays_accepts_numpy_and_channels():
    s = Structure.from_arrays(
        np.array([6, 1]),
        np.zeros((2, 3)),
        cell=np.eye(3) * 5,
        bonds=np.array([[0, 1]]),
        molecules=np.array([0, 0]),
        residue_names=["MET", "MET"],
        scalars={"q": np.array([0.5, -0.5])},
        atom_names=None,
    )
    assert s.cell_matrix() is not None and s.cell_matrix()[0, 0] == 5
    assert s.scalars == {"q": [0.5, -0.5]}
    assert s.atom_names is None


def test_molecule_needs_a_source():
    with pytest.raises(ValidationError, match="molblock or a smiles"):
        Molecule(name="x")
    assert Molecule(name="x", smiles="C").smiles == "C"
    schema = Molecule.model_json_schema()
    assert schema["anyOf"] == [{"required": ["molblock"]}, {"required": ["smiles"]}]


def test_builder_result_summary_is_private():
    r = BuilderResult(name="x", structure=Structure.model_validate(minimal()))
    assert r.summary is None
    assert r.with_summary("hello") is r
    assert r.summary == "hello"
    assert "summary" not in r.model_dump()
    assert r.contract == 1
