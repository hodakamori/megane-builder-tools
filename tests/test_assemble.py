from __future__ import annotations

import numpy as np
import pytest

from megane_builder_tools.chem import molecule as molecule_module
from megane_builder_tools.chem.assemble import Assembly, orthorhombic_cell
from megane_builder_tools.chem.molecule import load_molecule
from megane_builder_tools.sdk import Molecule, ToolError


def test_empty_assembly_builds_an_empty_structure():
    a = Assembly()
    a.add_copies(load_molecule(Molecule(name="w", smiles="O")), np.zeros((0, 3, 3)))
    s = a.build(orthorhombic_cell(np.array([5.0, 6.0, 7.0])))
    assert s.n_atoms == 0 and s.cell == [5.0, 0, 0, 0, 6.0, 0, 0, 0, 7.0]


def test_charged_and_multiple_bond_topology_is_replicated():
    acetate = load_molecule(Molecule(name="acetate", smiles="CC(=O)[O-]"))
    a = Assembly()
    a.add_copies(acetate, np.stack([acetate.positions, acetate.positions + 10]))
    s = a.build(None)
    assert s.bond_orders is not None and s.bond_orders.count(2) == 2
    assert s.formal_charges is not None and s.formal_charges.count(-1) == 2
    assert s.bonds[len(s.bonds) // 2][0] >= acetate.n_atoms


def test_embedding_failure_is_a_tool_error(monkeypatch):
    monkeypatch.setattr(molecule_module.rdDistGeom, "EmbedMolecule", lambda mol, params: -1)
    with pytest.raises(ToolError, match="could not embed"):
        load_molecule(Molecule(name="x", smiles="CC"))
