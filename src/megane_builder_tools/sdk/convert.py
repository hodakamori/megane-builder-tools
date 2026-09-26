"""Converters between :class:`Structure` and RDKit / ASE objects.

RDKit is a dependency of this package; ASE is imported lazily so that tool
authors who do not use it do not need it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import ArrayLike
from rdkit import Chem
from rdkit.Geometry import Point3D

from .models import Structure

if TYPE_CHECKING:  # pragma: no cover
    from ase import Atoms

_ORDER_OF = {
    Chem.BondType.SINGLE: 1,
    Chem.BondType.DOUBLE: 2,
    Chem.BondType.TRIPLE: 3,
    Chem.BondType.AROMATIC: 4,
}
_TYPE_OF = {1: Chem.BondType.SINGLE, 2: Chem.BondType.DOUBLE, 3: Chem.BondType.TRIPLE, 4: Chem.BondType.AROMATIC}


def rdkit_topology(mol: Chem.Mol) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Bonds, bond orders (1/2/3/4) and formal charges of an RDKit molecule."""
    bonds = [(b.GetBeginAtomIdx(), b.GetEndAtomIdx()) for b in mol.GetBonds()]
    orders = [_ORDER_OF.get(b.GetBondType(), 1) for b in mol.GetBonds()]
    charges = [a.GetFormalCharge() for a in mol.GetAtoms()]
    return bonds, orders, charges


def from_rdkit(mol: Chem.Mol, *, conf_id: int = -1, cell: ArrayLike | None = None, **per_atom: Any) -> Structure:
    """A Structure from an RDKit molecule's conformer, with its bonds and orders."""
    bonds, orders, charges = rdkit_topology(mol)
    return Structure.from_arrays(
        [a.GetAtomicNum() for a in mol.GetAtoms()],
        mol.GetConformer(conf_id).GetPositions(),
        cell=cell,
        bonds=bonds,
        bond_orders=orders if any(o != 1 for o in orders) else None,
        formal_charges=charges if any(charges) else None,
        **per_atom,
    )


def to_rdkit(structure: Structure, *, sanitize: bool = True) -> Chem.Mol:
    """An RDKit molecule (one conformer) with the structure's atoms, bonds and charges."""
    rw = Chem.RWMol()
    charges = structure.formal_charges or [0] * structure.n_atoms
    for z, q in zip(structure.elements, charges, strict=True):
        atom = Chem.Atom(int(z))
        atom.SetFormalCharge(int(q))
        atom.SetNoImplicit(True)
        rw.AddAtom(atom)
    orders = structure.bond_orders or [1] * len(structure.bonds)
    for (i, j), order in zip(structure.bonds, orders, strict=True):
        rw.AddBond(int(i), int(j), _TYPE_OF[order])
        if order == 4:
            rw.GetAtomWithIdx(int(i)).SetIsAromatic(True)
            rw.GetAtomWithIdx(int(j)).SetIsAromatic(True)
    conf = Chem.Conformer(structure.n_atoms)
    for k, (x, y, z) in enumerate(structure.positions_array()):
        conf.SetAtomPosition(k, Point3D(float(x), float(y), float(z)))
    conf.Set3D(True)
    mol = rw.GetMol()
    mol.AddConformer(conf, assignId=True)
    if sanitize:
        Chem.SanitizeMol(mol)
    return mol


def from_ase(atoms: Atoms, *, bonds: ArrayLike | None = None, **per_atom: Any) -> Structure:
    """A Structure from ASE Atoms. ASE carries no bonds; pass them if the tool knows them.

    The cell is kept when any direction is periodic, and dropped otherwise.
    """
    periodic = bool(np.any(atoms.get_pbc()))
    return Structure.from_arrays(
        atoms.get_atomic_numbers(),
        atoms.get_positions(),
        cell=np.asarray(atoms.get_cell()) if periodic else None,
        bonds=bonds,
        **per_atom,
    )


def to_ase(structure: Structure) -> Atoms:
    """ASE Atoms with the structure's elements, positions and cell (bonds are dropped)."""
    from ase import Atoms

    cell = structure.cell_matrix()
    return Atoms(
        numbers=structure.elements,
        positions=structure.positions_array(),
        cell=cell if cell is not None else np.zeros((3, 3)),
        pbc=cell is not None,
    )
