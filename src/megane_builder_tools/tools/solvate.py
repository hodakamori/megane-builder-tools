"""``solvate``: fill the free space of the open structure's cell with solvent (§12.3)."""

from __future__ import annotations

from typing import Annotated

import numpy as np
import packmol
import rdkit
from numpy.typing import NDArray
from pydantic import Field
from rdkit import Chem
from scipy.spatial import KDTree

from ..chem.assemble import Assembly
from ..chem.molecule import load_molecule, slug
from ..chem.packing import PackItem, run_packmol
from ..sdk import (
    MAX_ATOMS,
    BuilderResult,
    Density,
    DocumentInput,
    Length,
    MoleculeInput,
    Progress,
    Seed,
    Structure,
    ToolError,
    builder_tool,
)
from .liquid_box import AVOGADRO

_PT = Chem.GetPeriodicTable()
_SAMPLES = 200_000
_NEIGHBOURS = 16


def orthorhombic_box(document: Structure) -> NDArray[np.float64]:
    """Edge lengths of the document's cell; refuses a missing or non-orthorhombic cell."""
    cell = document.cell_matrix()
    if cell is None:
        raise ToolError("Solvate needs a periodic cell; set one in Crystal → Cell.")
    off_diagonal = cell - np.diag(np.diag(cell))
    if np.abs(off_diagonal).max() > 1e-6 or np.any(np.diag(cell) <= 0):
        raise ToolError("Solvate currently supports orthorhombic cells only.")
    return np.diag(cell).copy()


def free_volume(document: Structure, box: NDArray[np.float64], seed: int) -> float:
    """Volume (Å³) of the cell outside the document atoms' van der Waals spheres (Monte Carlo)."""
    volume = float(np.prod(box))
    if document.n_atoms == 0:
        return volume
    wrapped = np.mod(document.positions_array(), box)
    wrapped = np.where(wrapped >= box, wrapped - box, wrapped)
    radii = np.array([_PT.GetRvdw(int(z)) for z in document.elements])
    points = np.random.default_rng(seed).random((_SAMPLES, 3)) * box
    tree = KDTree(wrapped, boxsize=box)
    k = min(_NEIGHBOURS, document.n_atoms)
    dist, idx = tree.query(points, k=k, distance_upper_bound=float(radii.max()))
    dist, idx = dist.reshape(_SAMPLES, k), idx.reshape(_SAMPLES, k)
    padded = np.append(radii, 0.0)  # missing neighbours come back with index n_atoms
    covered = np.any(dist < padded[idx], axis=1)
    return volume * float(1.0 - covered.mean())


@builder_tool(title="Solvate", category="solvation", apply="insert", expected_seconds=30)
async def solvate(
    document: DocumentInput,
    solvent: Annotated[MoleculeInput, Field(title="Solvent")],
    seed: Seed,
    density: Annotated[Density, Field(gt=0, le=5, title="Density")] = 1.0,
    tolerance: Annotated[Length, Field(ge=1.0, le=4.0, title="Minimum distance")] = 2.0,
    progress: Progress = Progress(),  # noqa: B008 - replaced by the decorator on every call
) -> BuilderResult:
    """Fill the empty space of the open structure's cell with solvent molecules.

    The number of solvent molecules is chosen so that the solvent has the
    requested density (g/cm³) in the part of the cell not taken by the existing
    atoms' van der Waals spheres. packmol keeps every solvent atom at least the
    minimum distance from other atoms, across periodic boundaries. Only the
    added solvent is returned; the open structure is not changed. Needs an
    orthorhombic cell.
    """
    box = orthorhombic_box(document)
    molecule = load_molecule(solvent, seed=seed)
    await progress.areport(0.05, "estimating the free volume")
    free = free_volume(document, box, seed)
    count = int(np.floor(density * free * 1e-24 * AVOGADRO / molecule.molar_mass))
    if count < 1:
        raise ToolError("There is no room for solvent in this cell at the requested density.")
    if document.n_atoms + count * molecule.n_atoms > MAX_ATOMS:
        raise ToolError(f"Solvating would exceed {MAX_ATOMS} atoms; use a smaller cell.")

    items = [PackItem(molecule.elements, molecule.positions, number=count)]
    if document.n_atoms:
        fixed = np.mod(document.positions_array(), box)
        items.insert(0, PackItem(np.asarray(document.elements, dtype=np.int64), fixed, fixed=True))
    packed = await run_packmol(items, box, tolerance=tolerance, seed=seed, progress=progress.areport)

    assembly = Assembly()
    assembly.add_copies(molecule, packed.positions[-1])
    structure = assembly.build(document.cell_matrix())

    warnings = list(molecule.warnings)
    if not packed.converged:
        warnings.append(f"packmol stopped before reaching the {tolerance:.1f} Å minimum distance everywhere")
    name = f"{slug(solvent.name)}-solvent"[:128]
    summary = (
        f"Solvate: added {count} {solvent.name} molecules ({structure.n_atoms} atoms) "
        f"to the {box[0]:.1f} x {box[1]:.1f} x {box[2]:.1f} Å cell."
        + (" Warnings: " + "; ".join(warnings) if warnings else "")
    )
    return BuilderResult(
        name=name,
        structure=structure,
        warnings=warnings,
        provenance={"packmol": packmol.__version__, "rdkit": rdkit.__version__},
    ).with_summary(summary)
