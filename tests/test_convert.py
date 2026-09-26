from __future__ import annotations

import numpy as np
from ase import Atoms
from ase.build import bulk
from rdkit import Chem

from megane_builder_tools.sdk import Structure
from megane_builder_tools.sdk.convert import from_ase, from_rdkit, rdkit_topology, to_ase, to_rdkit

from .conftest import embedded


def test_rdkit_round_trip_keeps_orders_and_charges():
    mol = embedded("C[N+](C)(C)CC(=O)[O-]")
    s = from_rdkit(mol, cell=np.eye(3) * 30)
    assert s.bond_orders is not None and 2 in s.bond_orders
    assert s.formal_charges is not None and sorted(set(s.formal_charges)) == [-1, 0, 1]
    back = to_rdkit(s)
    assert Chem.MolToSmiles(Chem.RemoveHs(back)) == Chem.MolToSmiles(Chem.RemoveHs(mol))
    assert np.allclose(back.GetConformer().GetPositions(), mol.GetConformer().GetPositions())


def test_aromatic_bonds_and_plain_molecules():
    mol = Chem.MolFromSmiles("c1ccccc1")
    mol = Chem.AddHs(mol)
    Chem.rdDepictor.Compute2DCoords(mol)
    _, orders, charges = rdkit_topology(mol)
    assert 4 in orders and not any(charges)
    s = from_rdkit(mol)
    assert s.bond_orders is not None
    assert to_rdkit(s, sanitize=False).GetNumAtoms() == 12
    plain = from_rdkit(embedded("C"))
    assert plain.bond_orders is None and plain.formal_charges is None


def test_ase_round_trip():
    cu = bulk("Cu", "fcc", a=3.6, cubic=True)
    s = from_ase(cu)
    assert s.cell is not None and s.bonds == []
    atoms = to_ase(s)
    assert atoms.get_pbc().all()
    assert np.allclose(atoms.get_positions(), cu.get_positions())

    molecule = Atoms("H2", positions=[[0, 0, 0], [0.74, 0, 0]])
    s2 = from_ase(molecule, bonds=[(0, 1)])
    assert s2.cell is None and s2.bonds == [(0, 1)]
    assert not to_ase(s2).get_pbc().any()
    assert isinstance(s2, Structure)
