"""Linear homopolymers with RadonPy's random-walk polymerisation.

The contract describes a monomer by its head and tail atoms, each of which
gives up one hydrogen at every junction (§12.2). RadonPy describes the same
thing with linker atoms: the monomer carries two ``[3H]`` atoms, the first is
the head linker and the second the tail linker, and ``polymerize_rw`` joins
units by replacing linker pairs with bonds. So the leaving hydrogens become
the linkers, and the two linkers left at the chain ends become ordinary
hydrogens again: the chain ends keep their hydrogens, as the contract says.

Tacticity is not controlled or checked: every unit is the monomer as given
(RadonPy's ``atactic`` mode with ``atac_ratio=1.0`` mirrors no unit, and
without a ``tac_array`` it accepts any result), so stereocentres keep the
handedness the monomer has.

RadonPy draws random numbers from the global ``random`` and ``numpy.random``
states, so every call seeds both under a lock.
"""

from __future__ import annotations

import random
import threading
from dataclasses import dataclass

import numpy as np
import radonpy
from numpy.typing import NDArray
from radonpy.core import const as radonpy_const
from radonpy.core import poly as radonpy_poly
from radonpy.core.utils import RadonPyError
from rdkit import Chem
from scipy.spatial import KDTree

from ..sdk.convert import from_rdkit
from ..sdk.models import Structure
from ..sdk.tool import ToolError
from .molecule import LoadedMolecule, residue_name

LINKER_ISOTOPE = 3
"""RadonPy marks linker atoms as tritium (``[3H]``)."""

MAX_UNITS = 200
"""Longest chain offered. RadonPy re-optimises the whole growing chain with MMFF94
after every step, so time grows steeply: ~40 s for 100 ethylene units, ~7 min for 200."""

RADONPY_VERSION: str = radonpy.__version__

# RadonPy prints progress to stdout, which would corrupt an MCP stdio stream.
# Level 3 (errors) raises instead of printing, so nothing is ever printed.
setattr(radonpy_const, "print_level", 3)  # noqa: B010 - the attribute is typed Literal[1]
radonpy_const.tqdm_disable = True

_LOCK = threading.Lock()


@dataclass
class Junction:
    """The atoms of the monomer that take part in a junction."""

    head: int
    tail: int
    head_h: int
    tail_h: int


def junction_atoms(monomer: Chem.Mol, head: int, tail: int) -> Junction:
    """Validate head/tail and pick the hydrogen each gives up (lowest index)."""
    n = monomer.GetNumAtoms()
    for label, idx in (("head", head), ("tail", tail)):
        if not 0 <= idx < n:
            raise ToolError(f"The {label} atom index {idx} is out of range (the monomer has {n} atoms).")
    if head == tail:
        raise ToolError("The head and tail atoms must be different atoms.")

    def leaving_h(idx: int, label: str) -> int:
        atom = monomer.GetAtomWithIdx(idx)
        hs = sorted(nb.GetIdx() for nb in atom.GetNeighbors() if nb.GetAtomicNum() == 1)
        if not hs:
            raise ToolError(
                f"The {label} atom {idx} ({atom.GetSymbol()}) has no hydrogen to give up for the junction bond."
            )
        return hs[0]

    return Junction(head, tail, leaving_h(head, "head"), leaving_h(tail, "tail"))


def linker_monomer(monomer: Chem.Mol, junction: Junction) -> Chem.Mol:
    """The monomer with its leaving hydrogens turned into RadonPy linkers, head linker first."""
    order = list(range(monomer.GetNumAtoms()))
    if junction.head_h > junction.tail_h:
        # RadonPy takes the first linker as the head: swap the two hydrogens' indices.
        order[junction.head_h], order[junction.tail_h] = order[junction.tail_h], order[junction.head_h]
    mol = Chem.RenumberAtoms(Chem.Mol(monomer), order)
    for old in (junction.head_h, junction.tail_h):
        mol.GetAtomWithIdx(order.index(old)).SetIsotope(LINKER_ISOTOPE)
    return mol


def polymerize(
    monomer: LoadedMolecule,
    head: int,
    tail: int,
    length: int,
    seed: int,
) -> Chem.Mol:
    """Run ``radonpy.core.poly.polymerize_rw`` without tacticity control.

    Each step is optimised with MMFF94 (``opt="rdkit"``): without it the random
    walk collides and RadonPy gives up on chains of about 100 units.
    """
    junction = junction_atoms(monomer.mol, head, tail)
    mol = linker_monomer(monomer.mol, junction)
    with _LOCK:
        random.seed(seed)
        np.random.seed(seed)
        try:
            chain = radonpy_poly.polymerize_rw(mol, length, tacticity="atactic", atac_ratio=1.0, opt="rdkit")
        except RadonPyError as exc:
            raise ToolError(f"RadonPy could not build the chain: {exc}") from exc
    for atom in chain.GetAtoms():
        if atom.GetAtomicNum() == 1 and atom.GetIsotope() == LINKER_ISOTOPE:
            atom.SetIsotope(0)
    return chain


def chain_structure(chain: Chem.Mol, monomer_name: str) -> tuple[Structure, NDArray[np.int64]]:
    """The chain as a Structure (no cell) and the 0-based repeat-unit index of every atom."""
    units = np.array(
        [
            (info.GetResidueNumber() if (info := atom.GetPDBResidueInfo()) is not None else 1) - 1
            for atom in chain.GetAtoms()
        ],
        dtype=np.int64,
    )
    structure = from_rdkit(
        chain,
        conf_id=0,
        molecules=np.zeros(chain.GetNumAtoms(), dtype=np.int64),
        residue_names=[residue_name(monomer_name)] * chain.GetNumAtoms(),
        residue_ids=units + 1,
    )
    return structure, units


def closest_nonadjacent(structure: Structure, units: NDArray[np.int64], cutoff: float = 1.0) -> float:
    """Smallest distance below ``cutoff`` between atoms of units that are not neighbours."""
    positions = structure.positions_array()
    pairs = KDTree(positions).query_pairs(cutoff, output_type="ndarray")
    if len(pairs) == 0:
        return float("inf")
    pairs = pairs[np.abs(units[pairs[:, 0]] - units[pairs[:, 1]]) >= 2]
    if len(pairs) == 0:
        return float("inf")
    return float(np.linalg.norm(positions[pairs[:, 0]] - positions[pairs[:, 1]], axis=1).min())
