from __future__ import annotations

import numpy as np
import pytest
from mcp import Client

from megane_builder_tools.chem.packing import PackResult
from megane_builder_tools.server import create_server
from megane_builder_tools.tools import liquid_box as lb
from megane_builder_tools.tools.liquid_box import AVOGADRO, box_volume


def test_box_volume_matches_density():
    # 1 mol of 18 g at 1 g/cm3 is 18 cm3.
    assert box_volume(18.0 * AVOGADRO, 1.0) == pytest.approx(18.0 * 1e24)


async def test_liquid_box_over_mcp(water):
    async with Client(create_server()) as client:
        result = await client.call_tool(
            "liquid_box",
            {
                "components": [
                    {"molecule": water.model_dump(exclude_none=True), "count": 60},
                    {"molecule": {"name": "methanol", "smiles": "CO"}, "count": 5},
                ],
                "seed": 3,
                "density": 0.9,
            },
        )
    assert not result.is_error, result.content
    s = result.structured_content["structure"]
    n = 60 * 3 + 5 * 6
    assert len(s["elements"]) == n
    assert len(s["bonds"]) == 60 * 2 + 5 * 5
    assert max(s["molecules"]) == 64
    assert s["residueNames"][0] == "WATE" and s["residueNames"][-1] == "METH"
    assert s["residueIds"][-1] == 65
    cell = np.array(s["cell"]).reshape(3, 3)
    mass = 60 * 18.015 + 5 * 32.042
    density = mass / AVOGADRO / (np.linalg.det(cell) * 1e-24)
    assert density == pytest.approx(0.9, rel=1e-3)
    assert result.structured_content["name"] == "water-methanol"
    assert "Liquid box: 60 water, 5 methanol" in result.content[0].text
    assert result.structured_content["provenance"]["seed"] == 3


async def test_orthorhombic_aspect(water):
    result = await lb.liquid_box.run(
        components=[lb.Component(molecule=water, count=40)], seed=1, shape="orthorhombic", aspect=[1.0, 1.0, 2.0]
    )
    cell = result.structure.cell_matrix()
    assert cell[2, 2] / cell[0, 0] == pytest.approx(2.0)


async def test_limits_are_tool_errors(water, monkeypatch):
    from megane_builder_tools.sdk import ToolError

    with pytest.raises(ToolError, match=r"only 3\.1 Å wide"):
        await lb.liquid_box.run(components=[lb.Component(molecule=water, count=1)], seed=1)
    monkeypatch.setattr(lb, "MAX_ATOMS", 10)
    with pytest.raises(ToolError, match="limit is 10"):
        await lb.liquid_box.run(components=[lb.Component(molecule=water, count=4)], seed=1)


async def test_unconverged_packing_is_a_warning(water, monkeypatch):
    async def fake_pack(items, box, **kwargs):
        coords = np.zeros((items[0].number, 3, 3))
        coords[:, :, 0] = np.arange(items[0].number)[:, None] * 0.5
        coords += items[0].positions
        return PackResult(positions=[coords], converged=False)

    monkeypatch.setattr(lb, "run_packmol", fake_pack)
    result = await lb.liquid_box.run(components=[lb.Component(molecule=water, count=30)], seed=1)
    assert "packmol stopped before reaching the 2.0 Å minimum distance" in result.warnings[0]
    assert "Å apart" in result.warnings[0]
    assert "Warnings:" in result.summary
