from __future__ import annotations

import numpy as np
import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

from megane_builder_tools.sdk import Molecule, Structure


def embedded(smiles: str, seed: int = 7) -> Chem.Mol:
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    params = AllChem.ETKDGv3()
    params.randomSeed = seed
    AllChem.EmbedMolecule(mol, params)
    return mol


@pytest.fixture
def water_molblock() -> str:
    return Chem.MolToMolBlock(embedded("O"))


@pytest.fixture
def water(water_molblock: str) -> Molecule:
    return Molecule(name="water", molblock=water_molblock)


@pytest.fixture
def box_document() -> Structure:
    """A methane in the middle of an empty 20 Å cubic cell."""
    mol = embedded("C")
    positions = mol.GetConformer().GetPositions() + 10.0
    return Structure.from_arrays(
        [a.GetAtomicNum() for a in mol.GetAtoms()],
        positions,
        cell=np.diag([20.0, 20.0, 20.0]),
        bonds=[(b.GetBeginAtomIdx(), b.GetEndAtomIdx()) for b in mol.GetBonds()],
    )
