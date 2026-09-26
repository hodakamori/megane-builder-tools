from __future__ import annotations

import numpy as np
import pytest
from rdkit import Chem
from rdkit.Chem import rdMolDescriptors

from megane_builder_tools.chem import polymer
from megane_builder_tools.chem.molecule import load_molecule
from megane_builder_tools.chem.polymer import chain_structure, closest_nonadjacent, junction_atoms, linker_monomer
from megane_builder_tools.sdk import Molecule, Progress, Structure, ToolError
from megane_builder_tools.sdk.convert import to_rdkit
from megane_builder_tools.tools.polymer_chain import polymer_chain

ETHANE = Molecule(name="ethylene", smiles="CC")


def test_junction_validation():
    mol = load_molecule(ETHANE).mol
    assert junction_atoms(mol, 0, 1).head_h == 2
    with pytest.raises(ToolError, match="out of range"):
        junction_atoms(mol, 0, 99)
    with pytest.raises(ToolError, match="must be different"):
        junction_atoms(mol, 1, 1)
    oxygen = load_molecule(Molecule(name="co2", smiles="O=C=O")).mol
    with pytest.raises(ToolError, match="no hydrogen to give up"):
        junction_atoms(oxygen, 0, 1)


@pytest.mark.parametrize(("head", "tail"), [(0, 1), (1, 0)])
def test_linkers_are_ordered_head_first(head, tail):
    mol = load_molecule(ETHANE).mol
    j = junction_atoms(mol, head, tail)
    linked = linker_monomer(mol, j)
    linkers = [a.GetIdx() for a in linked.GetAtoms() if a.GetIsotope() == polymer.LINKER_ISOTOPE]
    assert len(linkers) == 2
    first = linked.GetAtomWithIdx(linkers[0]).GetNeighbors()[0].GetIdx()
    assert first == head


def test_polyethylene_chain():
    result = polymer_chain(ETHANE, head=0, tail=1, seed=1, length=5, progress=Progress())
    s = result.structure
    assert rdMolDescriptors.CalcMolFormula(to_rdkit(s)) == "C10H22"
    assert sorted(set(s.residue_ids)) == [1, 2, 3, 4, 5]
    assert set(s.molecules) == {0}
    assert s.cell is None
    assert result.provenance["radonpy"] == polymer.RADONPY_VERSION
    again = polymer_chain(ETHANE, head=0, tail=1, seed=1, length=5, progress=Progress())
    assert again.structure.positions == s.positions


def test_single_unit_is_the_monomer():
    result = polymer_chain(ETHANE, head=0, tail=1, seed=1, length=1, progress=Progress())
    assert result.structure.n_atoms == 8
    assert not any(Chem.Atom(z).GetIsotope() for z in result.structure.elements)


def test_radonpy_failure_is_a_tool_error(monkeypatch):
    def fail(*args, **kwargs):
        raise polymer.RadonPyError("Reached maximum number of retrying polymerize_rw.")

    monkeypatch.setattr(polymer.radonpy_poly, "polymerize_rw", fail)
    with pytest.raises(ToolError, match="RadonPy could not build the chain"):
        polymer_chain(ETHANE, head=0, tail=1, seed=1, length=3, progress=Progress())


def test_close_contacts_between_distant_units_are_reported(monkeypatch):
    chain = polymer.polymerize(load_molecule(ETHANE), 0, 1, 3, 1)
    structure, units = chain_structure(chain, "ethylene")
    assert closest_nonadjacent(structure, units) == float("inf")
    moved = structure.positions_array()
    first, last = np.flatnonzero(units == 0)[0], np.flatnonzero(units == 2)[0]
    moved[last] = moved[first] + [0.5, 0, 0]
    crowded = Structure.model_validate({**structure.model_dump(), "positions": moved.reshape(-1).tolist()})
    assert closest_nonadjacent(crowded, units) == pytest.approx(0.5)

    monkeypatch.setattr("megane_builder_tools.tools.polymer_chain.closest_nonadjacent", lambda s, u: 0.5)
    result = polymer_chain(ETHANE, head=0, tail=1, seed=1, length=3, progress=Progress())
    assert result.warnings == ["atoms of non-neighbouring repeat units are only 0.50 Å apart"]
    assert "Warnings:" in result.summary


def test_units_default_to_one_without_residue_info():
    mol = Chem.AddHs(Chem.MolFromSmiles("C"))
    Chem.rdDistGeom.EmbedMolecule(mol, randomSeed=1)
    structure, units = chain_structure(mol, "x")
    assert set(units) == {0}
    assert closest_nonadjacent(structure, units, cutoff=5.0) == float("inf")
