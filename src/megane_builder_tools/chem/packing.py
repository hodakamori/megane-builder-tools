"""packmol runner: pack copies of molecules into a periodic orthorhombic box."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import anyio
import numpy as np
from numpy.typing import NDArray
from rdkit import Chem
from scipy.spatial import KDTree

from ..sdk.tool import ToolError

PACKMOL_UNCONVERGED = 173
"""packmol's exit status when it stops at the iteration limit (it still writes ``<output>_FORCED``)."""

_PT = Chem.GetPeriodicTable()


@dataclass
class PackItem:
    """One structure to place ``number`` times; ``fixed`` places a single copy exactly as given."""

    elements: NDArray[np.int64]
    positions: NDArray[np.float64]
    number: int = 1
    fixed: bool = False


@dataclass
class PackResult:
    """Positions per item, shaped ``(number, n_atoms, 3)``, and whether packmol converged."""

    positions: list[NDArray[np.float64]]
    converged: bool


def packmol_executable() -> str:
    """The packmol binary: the one shipped by the ``packmol`` wheel, else the one on PATH."""
    try:
        from packmol.cli import get_binary_path

        return str(get_binary_path())
    except (ImportError, FileNotFoundError):  # pragma: no cover - depends on the install
        found = shutil.which("packmol")
        if found is None:
            raise ToolError("packmol is not installed (pip install packmol).") from None
        return found


def _write_xyz(path: Path, elements: NDArray[np.int64], positions: NDArray[np.float64]) -> None:
    lines = [str(len(elements)), "megane-builder-tools"]
    for z, (x, y, w) in zip(elements, positions, strict=True):
        lines.append(f"{_PT.GetElementSymbol(int(z))} {x:.6f} {y:.6f} {w:.6f}")
    path.write_text("\n".join(lines) + "\n")


def _read_xyz_positions(path: Path, n_atoms: int) -> NDArray[np.float64]:
    lines = path.read_text().splitlines()
    rows = [line.split()[1:4] for line in lines[2 : 2 + n_atoms]]
    if len(rows) != n_atoms:
        raise ToolError(f"packmol wrote {len(rows)} atoms, expected {n_atoms}.")
    return np.asarray(rows, dtype=np.float64)


def packmol_input(items: list[PackItem], box: NDArray[np.float64], tolerance: float, seed: int) -> str:
    """The packmol input deck; structure files are ``item<k>.xyz`` next to it."""
    a, b, c = (float(v) for v in box)
    lines = [
        f"tolerance {tolerance:.4f}",
        "filetype xyz",
        "output packed.xyz",
        f"seed {seed}",
        f"pbc 0. 0. 0. {a:.6f} {b:.6f} {c:.6f}",
    ]
    for k, item in enumerate(items):
        lines += [f"structure item{k}.xyz", f"  number {1 if item.fixed else item.number}"]
        if item.fixed:
            lines.append("  fixed 0. 0. 0. 0. 0. 0.")
        lines.append("end structure")
    return "\n".join(lines) + "\n"


async def run_packmol(
    items: list[PackItem],
    box: NDArray[np.float64],
    *,
    tolerance: float,
    seed: int,
    progress: Callable[[float, str], Awaitable[None]] | None = None,
) -> PackResult:
    """Run packmol in a temporary directory; cancelling the caller kills the process."""
    counts = [1 if it.fixed else it.number for it in items]
    total = sum(n * len(it.elements) for n, it in zip(counts, items, strict=True))
    with tempfile.TemporaryDirectory(prefix="megane-packmol-") as tmp:
        work = Path(tmp)
        for k, item in enumerate(items):
            _write_xyz(work / f"item{k}.xyz", item.elements, item.positions)
        (work / "input.inp").write_text(packmol_input(items, box, tolerance, seed))
        if progress is not None:
            await progress(0.1, "packing with packmol")
        with open(work / "input.inp", "rb") as stdin, open(work / "packmol.log", "wb") as log:
            process = await anyio.open_process([packmol_executable()], stdin=stdin, stdout=log, stderr=log, cwd=work)
            try:
                status = await process.wait()
            except BaseException:
                process.kill()
                with anyio.CancelScope(shield=True):
                    await process.wait()
                raise
        if status == 0:
            packed, converged = work / "packed.xyz", True
        elif status == PACKMOL_UNCONVERGED and (work / "packed.xyz_FORCED").exists():
            packed, converged = work / "packed.xyz_FORCED", False
        else:
            tail = (work / "packmol.log").read_text(errors="replace").strip().splitlines()[-5:]
            raise ToolError(f"packmol failed (exit status {status}): {' '.join(tail)}")
        flat = _read_xyz_positions(packed, total)
    if progress is not None:
        await progress(0.9, "assembling the structure")
    out: list[NDArray[np.float64]] = []
    start = 0
    for n, item in zip(counts, items, strict=True):
        size = n * len(item.elements)
        out.append(flat[start : start + size].reshape(n, len(item.elements), 3))
        start += size
    return PackResult(positions=out, converged=converged)


def box_for_volume(volume: float, aspect: tuple[float, float, float]) -> NDArray[np.float64]:
    """Edge lengths (Å) of an orthorhombic box of ``volume`` Å³ with the given a:b:c ratio."""
    ratio = np.asarray(aspect, dtype=np.float64)
    scale = (volume / float(np.prod(ratio))) ** (1.0 / 3.0)
    return ratio * scale


def min_intermolecular_distance(
    positions: NDArray[np.float64], molecules: NDArray[np.int64], box: NDArray[np.float64], cutoff: float
) -> float:
    """Smallest distance below ``cutoff`` between atoms of different molecules, under
    periodic boundaries (Å); ``inf`` when no such pair is closer than ``cutoff``."""
    if len(positions) < 2:
        return float("inf")
    wrapped = np.mod(positions, box)
    wrapped = np.where(wrapped >= box, wrapped - box, wrapped)
    tree = KDTree(wrapped, boxsize=box)
    pairs = tree.query_pairs(cutoff, output_type="ndarray")
    if len(pairs) == 0:
        return float("inf")
    pairs = pairs[molecules[pairs[:, 0]] != molecules[pairs[:, 1]]]
    if len(pairs) == 0:
        return float("inf")
    delta = wrapped[pairs[:, 0]] - wrapped[pairs[:, 1]]
    delta -= box * np.round(delta / box)
    return float(np.sqrt((delta**2).sum(axis=1)).min())
