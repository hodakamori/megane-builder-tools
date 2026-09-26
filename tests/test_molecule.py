from __future__ import annotations

import pytest
from rdkit import Chem

from megane_builder_tools.chem.molecule import load_molecule, molblock_dimension, residue_name, slug
from megane_builder_tools.sdk import Molecule, ToolError

from .conftest import embedded


def with_dimension(molblock: str, code: str) -> str:
    lines = molblock.splitlines()
    lines[1] = lines[1][:20].ljust(20) + code + lines[1][22:]
    return "\n".join(lines)


def test_dimension_code(water_molblock):
    assert molblock_dimension(water_molblock) == "3D"
    assert molblock_dimension(with_dimension(water_molblock, "2D")) == "2D"
    assert molblock_dimension(with_dimension(water_molblock, "  ")) is None
    assert molblock_dimension("x") is None


def test_molblock_is_used_as_given(water_molblock):
    loaded = load_molecule(Molecule(name="water", molblock=water_molblock))
    mol = Chem.MolFromMolBlock(water_molblock, removeHs=False)
    assert (loaded.positions == mol.GetConformer().GetPositions()).all()
    assert loaded.n_atoms == 3 and list(loaded.elements) == [8, 1, 1]
    assert loaded.molar_mass == pytest.approx(18.015, abs=0.01)
    assert loaded.warnings == []


def test_flat_but_3d_molecules_are_accepted():
    # Benzene with z = 0 everywhere is a valid 3D geometry (megane's preset is exactly this).
    mol = embedded("c1ccccc1")
    conf = mol.GetConformer()
    for i in range(mol.GetNumAtoms()):
        p = conf.GetAtomPosition(i)
        conf.SetAtomPosition(i, (p.x, p.y, 0.0))
    block = with_dimension(Chem.MolToMolBlock(mol), "  ")
    assert load_molecule(Molecule(name="benzene", molblock=block)).n_atoms == 12


def test_2d_molblocks_are_refused(water_molblock):
    with pytest.raises(ToolError, match="2D drawing"):
        load_molecule(Molecule(name="w", molblock=with_dimension(water_molblock, "2D")))


def test_unreadable_and_invalid_molblocks(water_molblock):
    with pytest.raises(ToolError, match="Could not read"):
        load_molecule(Molecule(name="junk", molblock="not a mol block"))
    pentavalent = with_dimension(Chem.MolToMolBlock(Chem.MolFromSmiles("C(C)(C)(C)(C)C", sanitize=False)), "3D")
    with pytest.raises(ToolError, match="not a valid molecule"):
        load_molecule(Molecule(name="bad", molblock=pentavalent))


def test_implicit_hydrogens_are_reported_not_added():
    mol = embedded("CO")
    heavy = Chem.RemoveHs(mol)
    loaded = load_molecule(Molecule(name="methanol", molblock=Chem.MolToMolBlock(heavy)))
    assert loaded.n_atoms == 2
    assert "implicit hydrogens" in loaded.warnings[0]


def test_smiles_is_embedded_deterministically():
    a = load_molecule(Molecule(name="e", smiles="CCO"), seed=3)
    b = load_molecule(Molecule(name="e", smiles="CCO"), seed=3)
    assert a.n_atoms == 9
    assert (a.positions == b.positions).all()


def test_smiles_errors_and_force_field_fallbacks():
    with pytest.raises(ToolError, match="Could not parse"):
        load_molecule(Molecule(name="x", smiles="C1CC"))
    uff = load_molecule(Molecule(name="trimethylborane", smiles="B(C)(C)C"))
    assert uff.warnings == ["'trimethylborane': MMFF has no parameters, relaxed with UFF"]
    bare = load_molecule(Molecule(name="xenon", smiles="[Xe]"))
    assert bare.warnings == ["'xenon': no force field parameters, geometry not relaxed"]


def test_residue_name():
    assert residue_name("water") == "WATE"
    assert residue_name("N-methyl") == "NMET"
    assert residue_name("--") == "MOL"


def test_slug():
    assert slug("Water") == "water"
    assert slug("N,N-dimethyl formamide") == "n-n-dimethyl-formamide"
    assert slug("***") == "molecule"
