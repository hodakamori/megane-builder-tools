"""``polymer_chain``: a linear homopolymer from a monomer and its head/tail atoms (§12.2)."""

from __future__ import annotations

from typing import Annotated

import numpy as np
import rdkit
from pydantic import Field

from ..chem.molecule import load_molecule, slug
from ..chem.polymer import MAX_UNITS, RADONPY_VERSION, chain_structure, closest_nonadjacent, polymerize
from ..sdk import BuilderResult, Count, MoleculeInput, Progress, Seed, atom_of, builder_tool


@builder_tool(title="Polymer chain", category="polymer", apply="new_document", expected_seconds=60)
def polymer_chain(
    monomer: Annotated[MoleculeInput, Field(title="Monomer", examples=[{"name": "ethylene", "smiles": "CC"}])],
    head: Annotated[int, atom_of("monomer"), Field(title="Head atom", examples=[0])],
    tail: Annotated[int, atom_of("monomer"), Field(title="Tail atom", examples=[1])],
    seed: Seed,
    length: Annotated[Count, Field(ge=1, le=MAX_UNITS, title="Repeat units")] = 10,
    progress: Progress = Progress(),  # noqa: B008 - replaced by the decorator on every call
) -> BuilderResult:
    """Build a linear homopolymer with RadonPy's random-walk polymerisation.

    The head and tail atoms (0-based indices into the monomer) are the atoms that
    bond to the neighbouring units; each gives up one hydrogen at every junction,
    so the chain ends keep theirs. Every unit keeps the monomer's stereochemistry
    (tacticity is not controlled). RadonPy relaxes the growing chain with MMFF94
    after every step, so long chains are slow: about 40 s for 100 ethylene units
    and 7 minutes for 200. Every repeat unit is one residue.
    """
    loaded = load_molecule(monomer, seed=seed)
    progress(0.1, "polymerising with RadonPy")
    chain = polymerize(loaded, head, tail, length, seed)
    progress(0.9, "assembling the structure")
    structure, units = chain_structure(chain, monomer.name)
    warnings = list(loaded.warnings)
    closest = closest_nonadjacent(structure, units)
    if np.isfinite(closest):
        warnings.append(f"atoms of non-neighbouring repeat units are only {closest:.2f} Å apart")
    name = f"poly-{slug(monomer.name)}"[:128]
    summary = f"Polymer chain: {length} units of {monomer.name} ({structure.n_atoms} atoms)." + (
        " Warnings: " + "; ".join(warnings) if warnings else ""
    )
    return BuilderResult(
        name=name,
        structure=structure,
        warnings=warnings,
        provenance={"radonpy": RADONPY_VERSION, "rdkit": rdkit.__version__},
    ).with_summary(summary)
