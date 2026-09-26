"""``liquid_box``: fill a periodic box with molecules at a target density (§12.1)."""

from __future__ import annotations

from typing import Annotated, Literal

import numpy as np
import packmol
import rdkit
from pydantic import BaseModel, ConfigDict, Field

from ..chem.assemble import Assembly, orthorhombic_cell
from ..chem.molecule import load_molecule, slug
from ..chem.packing import PackItem, box_for_volume, min_intermolecular_distance, run_packmol
from ..sdk import (
    MAX_ATOMS,
    BuilderResult,
    Count,
    Density,
    Length,
    MoleculeInput,
    Progress,
    Seed,
    ToolError,
    builder_tool,
)

AVOGADRO = 6.02214076e23
"""mol⁻¹"""


class Component(BaseModel):
    """One species of the mixture and how many copies of it to pack."""

    model_config = ConfigDict(extra="forbid")

    molecule: Annotated[MoleculeInput, Field(title="Molecule")]
    count: Annotated[Count, Field(ge=1, le=MAX_ATOMS, title="Count", examples=[100])]


def box_volume(total_mass_g_per_mol: float, density: float) -> float:
    """Volume in Å³ holding ``total_mass`` (g/mol summed over molecules) at ``density`` g/cm³."""
    return total_mass_g_per_mol / AVOGADRO / density * 1e24


@builder_tool(title="Liquid box", category="bulk", apply="new_document", expected_seconds=20)
async def liquid_box(
    components: Annotated[list[Component], Field(min_length=1, max_length=8, title="Components")],
    seed: Seed,
    density: Annotated[Density, Field(gt=0, le=5, title="Density")] = 1.0,
    shape: Annotated[Literal["cubic", "orthorhombic"], Field(title="Box shape")] = "cubic",
    aspect: Annotated[
        list[Annotated[float, Field(gt=0)]],
        Field(min_length=3, max_length=3, title="Aspect ratio a:b:c", description="Used for orthorhombic boxes."),
    ] = [1.0, 1.0, 1.0],  # noqa: B006 - pydantic copies defaults
    tolerance: Annotated[Length, Field(ge=1.0, le=4.0, title="Minimum distance")] = 2.0,
    progress: Progress = Progress(),  # noqa: B008 - replaced by the decorator on every call
) -> BuilderResult:
    """Fill a periodic box with molecules at a target density using packmol.

    Give each component and its number of copies; the box is sized so that the
    mixture has the requested density (g/cm³). Molecules are packed whole with
    at least the minimum distance between atoms of different molecules, and are
    returned with their bonds, one residue per molecule.
    """
    loaded = [load_molecule(c.molecule, seed=seed) for c in components]
    total_atoms = sum(c.count * m.n_atoms for c, m in zip(components, loaded, strict=True))
    if total_atoms > MAX_ATOMS:
        raise ToolError(f"The box would hold {total_atoms} atoms; the limit is {MAX_ATOMS}. Use fewer molecules.")

    mass = sum(c.count * m.molar_mass for c, m in zip(components, loaded, strict=True))
    ratio = (1.0, 1.0, 1.0) if shape == "cubic" else (aspect[0], aspect[1], aspect[2])
    box = box_for_volume(box_volume(mass, density), ratio)
    if float(box.min()) < 2 * tolerance:
        raise ToolError(f"The box would be only {box.min():.1f} Å wide; add more molecules or lower the density.")

    items = [PackItem(m.elements, m.positions, number=c.count) for c, m in zip(components, loaded, strict=True)]
    packed = await run_packmol(items, box, tolerance=tolerance, seed=seed, progress=progress.areport)

    assembly = Assembly()
    for molecule, coordinates in zip(loaded, packed.positions, strict=True):
        assembly.add_copies(molecule, coordinates)
    structure = assembly.build(orthorhombic_cell(box))

    warnings = [w for m in loaded for w in m.warnings]
    if not packed.converged:
        closest = min_intermolecular_distance(
            structure.positions_array(), np.asarray(structure.molecules), box, cutoff=tolerance
        )
        detail = f"closest atoms of different molecules are {closest:.2f} Å apart" if np.isfinite(closest) else ""
        warnings.append(
            f"packmol stopped before reaching the {tolerance:.1f} Å minimum distance"
            + (f"; the {detail}" if detail else "")
        )

    name = "-".join(slug(c.molecule.name) for c in components)[:128]
    mix = ", ".join(f"{c.count} {c.molecule.name}" for c in components)
    summary = (
        f"Liquid box: {mix} in a {box[0]:.1f} x {box[1]:.1f} x {box[2]:.1f} Å box "
        f"({density:.3g} g/cm³, {structure.n_atoms} atoms)."
    )
    if warnings:
        summary += " Warnings: " + "; ".join(warnings)
    return BuilderResult(
        name=name,
        structure=structure,
        warnings=warnings,
        provenance={"packmol": packmol.__version__, "rdkit": rdkit.__version__},
    ).with_summary(summary)
