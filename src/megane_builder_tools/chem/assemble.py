"""Turn packed copies of molecules into one Structure with complete topology."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from ..sdk.convert import rdkit_topology
from ..sdk.models import Structure
from .molecule import LoadedMolecule, residue_name


@dataclass
class Assembly:
    """Accumulates molecule copies; ``build`` returns the Structure."""

    elements: list[NDArray[np.int64]] = field(default_factory=list)
    positions: list[NDArray[np.float64]] = field(default_factory=list)
    bonds: list[NDArray[np.int64]] = field(default_factory=list)
    orders: list[NDArray[np.int64]] = field(default_factory=list)
    charges: list[NDArray[np.int64]] = field(default_factory=list)
    molecules: list[NDArray[np.int64]] = field(default_factory=list)
    residue_names: list[str] = field(default_factory=list)
    n_atoms: int = 0
    n_molecules: int = 0

    def add_copies(self, molecule: LoadedMolecule, coordinates: NDArray[np.float64]) -> None:
        """Add ``coordinates.shape[0]`` copies of ``molecule`` (coordinates ``(copies, atoms, 3)``)."""
        copies, n, _ = coordinates.shape
        if copies == 0:
            return
        bonds, orders, charges = rdkit_topology(molecule.mol)
        local = np.asarray(bonds, dtype=np.int64).reshape(-1, 2)
        offsets = self.n_atoms + n * np.arange(copies, dtype=np.int64)
        self.elements.append(np.tile(molecule.elements, copies))
        self.positions.append(coordinates.reshape(-1, 3))
        self.bonds.append((local[None, :, :] + offsets[:, None, None]).reshape(-1, 2))
        self.orders.append(np.tile(np.asarray(orders, dtype=np.int64), copies))
        self.charges.append(np.tile(np.asarray(charges, dtype=np.int64), copies))
        self.molecules.append(np.repeat(self.n_molecules + np.arange(copies, dtype=np.int64), n))
        self.residue_names += [residue_name(molecule.name)] * (copies * n)
        self.n_atoms += copies * n
        self.n_molecules += copies

    def build(self, cell: NDArray[np.float64] | None) -> Structure:
        if self.n_atoms == 0:
            return Structure.from_arrays([], np.zeros((0, 3)), cell=cell)
        orders = np.concatenate(self.orders)
        charges = np.concatenate(self.charges)
        molecules = np.concatenate(self.molecules)
        return Structure.from_arrays(
            np.concatenate(self.elements),
            np.concatenate(self.positions),
            cell=cell,
            bonds=np.concatenate(self.bonds),
            bond_orders=orders if np.any(orders != 1) else None,
            formal_charges=charges if np.any(charges) else None,
            molecules=molecules,
            residue_names=self.residue_names,
            residue_ids=molecules + 1,
        )


def orthorhombic_cell(box: NDArray[np.float64]) -> NDArray[np.float64]:
    """The row-major cell matrix of an orthorhombic box."""
    return np.diag(np.asarray(box, dtype=np.float64))
