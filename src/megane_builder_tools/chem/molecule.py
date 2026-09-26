"""Molecule arguments → RDKit molecules with 3D coordinates (§4.2 ``molecule``)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray
from rdkit import Chem, rdBase
from rdkit.Chem import rdDistGeom, rdForceFieldHelpers

from ..sdk.models import Molecule
from ..sdk.tool import ToolError

rdBase.DisableLog("rdApp.*")

_PT = Chem.GetPeriodicTable()


@dataclass
class LoadedMolecule:
    """A molecule argument resolved to an RDKit molecule with one 3D conformer."""

    name: str
    mol: Chem.Mol
    warnings: list[str] = field(default_factory=list)

    @property
    def n_atoms(self) -> int:
        return self.mol.GetNumAtoms()

    @property
    def elements(self) -> NDArray[np.int64]:
        return np.array([a.GetAtomicNum() for a in self.mol.GetAtoms()], dtype=np.int64)

    @property
    def positions(self) -> NDArray[np.float64]:
        return np.array(self.mol.GetConformer().GetPositions(), dtype=np.float64)

    @property
    def molar_mass(self) -> float:
        """g/mol, counting only the atoms present (explicit hydrogens)."""
        return float(sum(_PT.GetAtomicWeight(int(z)) for z in self.elements))


def molblock_dimension(molblock: str) -> str | None:
    """The dimension code (``"2D"``/``"3D"``) of a mol block header, if present.

    It sits in columns 21-22 of the second header line (the program line).
    """
    lines = molblock.splitlines()
    if len(lines) < 2:
        return None
    code = lines[1][20:22].strip().upper()
    return code if code in ("2D", "3D") else None


def load_molecule(molecule: Molecule, *, seed: int = 0) -> LoadedMolecule:
    """Resolve a molecule argument.

    A mol block is used exactly as given: it is never re-embedded or
    re-optimised, because the user chose that conformer. Only a mol block whose
    header declares ``2D`` is refused. A SMILES is embedded with ETKDG and
    relaxed with MMFF94s (UFF when MMFF has no parameters).
    """
    if molecule.molblock:
        return _from_molblock(molecule.name, molecule.molblock)
    assert molecule.smiles is not None
    return _from_smiles(molecule.name, molecule.smiles, seed)


def _from_molblock(name: str, molblock: str) -> LoadedMolecule:
    if molblock_dimension(molblock) == "2D":
        raise ToolError(
            f"'{name}' is a 2D drawing; this tool needs 3D coordinates. "
            "Embed it in 3D first (sketch it in the Builder library, which embeds with RDKit)."
        )
    mol = Chem.MolFromMolBlock(molblock, sanitize=False, removeHs=False)
    if mol is None or mol.GetNumAtoms() == 0:
        raise ToolError(f"Could not read the mol block of '{name}'.")
    warnings: list[str] = []
    try:
        Chem.SanitizeMol(mol)
    except Exception as exc:  # RDKit raises several sanitisation exception types
        raise ToolError(f"'{name}' is not a valid molecule: {exc}") from exc
    implicit = sum(a.GetNumImplicitHs() for a in mol.GetAtoms())
    if implicit:
        warnings.append(f"'{name}' has {implicit} implicit hydrogens; they were not added")
    return LoadedMolecule(name=name, mol=mol, warnings=warnings)


def _from_smiles(name: str, smiles: str, seed: int) -> LoadedMolecule:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ToolError(f"Could not parse the SMILES of '{name}': {smiles!r}")
    mol = Chem.AddHs(mol)
    params = rdDistGeom.ETKDGv3()
    params.randomSeed = seed
    if rdDistGeom.EmbedMolecule(mol, params) != 0:
        params.useRandomCoords = True
        if rdDistGeom.EmbedMolecule(mol, params) != 0:
            raise ToolError(f"RDKit could not embed '{name}' ({smiles}) in 3D.")
    warnings: list[str] = []
    if rdForceFieldHelpers.MMFFHasAllMoleculeParams(mol):
        rdForceFieldHelpers.MMFFOptimizeMolecule(mol, mmffVariant="MMFF94s", maxIters=2000)
    elif rdForceFieldHelpers.UFFHasAllMoleculeParams(mol):
        rdForceFieldHelpers.UFFOptimizeMolecule(mol, maxIters=2000)
        warnings.append(f"'{name}': MMFF has no parameters, relaxed with UFF")
    else:
        warnings.append(f"'{name}': no force field parameters, geometry not relaxed")
    return LoadedMolecule(name=name, mol=mol, warnings=warnings)


def slug(name: str) -> str:
    """A file-name friendly form of a molecule name: lower-case alphanumerics and hyphens."""
    out = "".join(ch if ch.isalnum() else "-" for ch in name.lower())
    return "-".join(part for part in out.split("-") if part) or "molecule"


def residue_name(name: str) -> str:
    """A PDB-style residue name (≤ 4 upper-case alphanumerics) from a molecule name."""
    letters = "".join(ch for ch in name.upper() if ch.isalnum())
    return letters[:4] or "MOL"
