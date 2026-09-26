from __future__ import annotations

import numpy as np
import pytest

from megane_builder_tools.chem.packing import PackResult
from megane_builder_tools.sdk import Structure, ToolError
from megane_builder_tools.tools import solvate as sv


async def test_solvate_returns_only_the_solvent(box_document, water):
    result = await sv.solvate.run(document=box_document, solvent=water, seed=2)
    s = result.structure
    assert s.n_atoms % 3 == 0 and s.n_atoms > 600
    assert s.cell == box_document.cell
    solvent = s.positions_array()
    solute = box_document.positions_array()
    delta = solvent[:, None, :] - solute[None, :, :]
    delta -= 20.0 * np.round(delta / 20.0)
    assert np.linalg.norm(delta, axis=2).min() > 1.9
    assert result.name == "water-solvent"
    assert "Solvate: added" in result.summary


async def test_solvate_empty_cell(water):
    empty = Structure.from_arrays([], np.zeros((0, 3)), cell=np.eye(3) * 12)
    result = await sv.solvate.run(document=empty, solvent=water, seed=1)
    assert result.structure.n_atoms > 0


def test_free_volume_excludes_atoms(box_document):
    box = sv.orthorhombic_box(box_document)
    free = sv.free_volume(box_document, box, seed=1)
    assert 7900 < free < 8000
    empty = Structure.from_arrays([], np.zeros((0, 3)), cell=np.eye(3) * 10)
    assert sv.free_volume(empty, np.array([10.0] * 3), seed=1) == pytest.approx(1000.0)


def test_cell_requirements(box_document):
    no_cell = Structure.from_arrays([1], [[0, 0, 0]])
    with pytest.raises(ToolError, match="needs a periodic cell"):
        sv.orthorhombic_box(no_cell)
    tilted = Structure.from_arrays([1], [[0, 0, 0]], cell=[[10, 0, 0], [2, 10, 0], [0, 0, 10]])
    with pytest.raises(ToolError, match="orthorhombic cells only"):
        sv.orthorhombic_box(tilted)


async def test_no_room_and_too_many_atoms(box_document, water, monkeypatch):
    tiny = Structure.from_arrays([6], [[0, 0, 0]], cell=np.eye(3) * 2.5)
    with pytest.raises(ToolError, match="no room"):
        await sv.solvate.run(document=tiny, solvent=water, seed=1)
    monkeypatch.setattr(sv, "MAX_ATOMS", 50)
    with pytest.raises(ToolError, match="exceed 50 atoms"):
        await sv.solvate.run(document=box_document, solvent=water, seed=1)


async def test_unconverged_solvation_is_a_warning(box_document, water, monkeypatch):
    async def fake_pack(items, box, **kwargs):
        free = items[-1]
        return PackResult(
            positions=[np.zeros((1, box_document.n_atoms, 3)), np.tile(free.positions, (free.number, 1, 1))],
            converged=False,
        )

    monkeypatch.setattr(sv, "run_packmol", fake_pack)
    result = await sv.solvate.run(document=box_document, solvent=water, seed=1)
    assert result.warnings == ["packmol stopped before reaching the 2.0 Å minimum distance everywhere"]
