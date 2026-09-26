from __future__ import annotations

import time

import anyio
import numpy as np
import pytest

from megane_builder_tools.chem import packing
from megane_builder_tools.chem.packing import (
    PackItem,
    box_for_volume,
    min_intermolecular_distance,
    packmol_input,
    run_packmol,
)
from megane_builder_tools.sdk import ToolError

WATER_Z = np.array([8, 1, 1])
WATER_XYZ = np.array([[0, 0, 0.117], [0, 0.757, -0.469], [0, -0.757, -0.469]])


def test_input_deck_lists_fixed_and_free_structures():
    deck = packmol_input(
        [PackItem(WATER_Z, WATER_XYZ, fixed=True), PackItem(WATER_Z, WATER_XYZ, number=5)],
        np.array([10.0, 11.0, 12.0]),
        tolerance=2.0,
        seed=4,
    )
    assert "pbc 0. 0. 0. 10.000000 11.000000 12.000000" in deck
    assert "structure item0.xyz\n  number 1\n  fixed 0. 0. 0. 0. 0. 0.\nend structure" in deck
    assert "structure item1.xyz\n  number 5\nend structure" in deck
    assert "seed 4" in deck


def test_box_for_volume():
    box = box_for_volume(1000.0, (1.0, 2.0, 4.0))
    assert np.prod(box) == pytest.approx(1000.0)
    assert box[1] / box[0] == pytest.approx(2.0)


def test_min_intermolecular_distance_uses_periodic_images():
    positions = np.array([[0.2, 0, 0], [9.9, 0, 0], [5, 5, 5]])
    box = np.array([10.0, 10.0, 10.0])
    assert min_intermolecular_distance(positions, np.array([0, 1, 2]), box, cutoff=2.0) == pytest.approx(0.3)
    assert min_intermolecular_distance(positions, np.array([0, 0, 1]), box, cutoff=2.0) == float("inf")
    assert min_intermolecular_distance(positions, np.array([0, 1, 2]), box, cutoff=0.1) == float("inf")
    assert min_intermolecular_distance(positions[:1], np.array([0]), box, cutoff=2.0) == float("inf")


async def test_packs_and_keeps_fixed_atoms():
    fixed = WATER_XYZ + 5.0
    result = await run_packmol(
        [PackItem(WATER_Z, fixed, fixed=True), PackItem(WATER_Z, WATER_XYZ, number=20)],
        np.array([12.0, 12.0, 12.0]),
        tolerance=2.0,
        seed=1,
    )
    assert result.converged
    assert result.positions[0].shape == (1, 3, 3)
    assert np.allclose(result.positions[0][0], fixed, atol=1e-3)
    assert result.positions[1].shape == (20, 3, 3)


async def test_unconverged_packing_returns_the_forced_solution(monkeypatch, tmp_path):
    # packmol exits with 173 at its iteration limit and writes the best packing to <output>_FORCED.
    script = tmp_path / "fake-packmol"
    rows = "\\n".join(f"O {i} 0 0\\nH {i} 1 0\\nH {i} 0 1" for i in range(2))
    script.write_text(f"#!/bin/sh\nprintf '6\\nforced\\n{rows}\\n' > packed.xyz_FORCED\nexit 173\n")
    script.chmod(0o755)
    monkeypatch.setattr(packing, "packmol_executable", lambda: str(script))
    reports = []

    async def progress(value, message):
        reports.append(value)

    result = await run_packmol(
        [PackItem(WATER_Z, WATER_XYZ, number=2)], np.array([8.0, 8.0, 8.0]), tolerance=2.0, seed=7, progress=progress
    )
    assert not result.converged
    assert result.positions[0].shape == (2, 3, 3)
    assert result.positions[0][1, 0].tolist() == [1.0, 0.0, 0.0]
    assert reports == [0.1, 0.9]


async def test_packmol_failure_is_a_tool_error(monkeypatch, tmp_path):
    script = tmp_path / "fake-packmol"
    script.write_text("#!/bin/sh\necho 'ERROR: something broke'\nexit 3\n")
    script.chmod(0o755)
    monkeypatch.setattr(packing, "packmol_executable", lambda: str(script))
    with pytest.raises(ToolError, match="exit status 3"):
        await run_packmol([PackItem(WATER_Z, WATER_XYZ, number=2)], np.array([10.0] * 3), tolerance=2.0, seed=1)


async def test_short_output_is_a_tool_error(monkeypatch, tmp_path):
    script = tmp_path / "fake-packmol"
    script.write_text("#!/bin/sh\nprintf '1\\nx\\nO 0 0 0\\n' > packed.xyz\n")
    script.chmod(0o755)
    monkeypatch.setattr(packing, "packmol_executable", lambda: str(script))
    with pytest.raises(ToolError, match="wrote 1 atoms"):
        await run_packmol([PackItem(WATER_Z, WATER_XYZ, number=2)], np.array([10.0] * 3), tolerance=2.0, seed=1)


async def test_cancelling_kills_packmol(monkeypatch, tmp_path):
    marker = tmp_path / "alive"
    script = tmp_path / "slow-packmol"
    script.write_text(f"#!/bin/sh\ntouch {marker}\nsleep 30\n")
    script.chmod(0o755)
    monkeypatch.setattr(packing, "packmol_executable", lambda: str(script))
    start = time.monotonic()
    with anyio.move_on_after(1.0):
        await run_packmol([PackItem(WATER_Z, WATER_XYZ, number=2)], np.array([10.0] * 3), tolerance=2.0, seed=1)
    assert time.monotonic() - start < 10
    assert marker.exists()
