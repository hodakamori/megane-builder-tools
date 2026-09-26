"""Wire models of the Builder Tool Contract: Molecule, Structure, BuilderResult (§4.2, §5)."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

import numpy as np
from numpy.typing import ArrayLike, NDArray
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator
from pydantic.alias_generators import to_camel

from .contract import CONTRACT_VERSION, MAX_ATOMS, widget

_CAMEL = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class Molecule(BaseModel):
    """A molecule: a V2000 mol block with explicit hydrogens and 3D coordinates in Å
    (header dimension code 3D), or a SMILES the server embeds in 3D."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"anyOf": [{"required": ["molblock"]}, {"required": ["smiles"]}]},
    )

    name: Annotated[str, Field(min_length=1, max_length=128, description="Display name, e.g. 'water'.")]
    molblock: Annotated[str | None, Field(description="V2000 mol block, explicit H, 3D, Å.")] = None
    smiles: Annotated[str | None, Field(description="SMILES; embedded in 3D by the server.")] = None

    @model_validator(mode="after")
    def _one_source(self) -> Self:
        if not self.molblock and not self.smiles:
            raise ValueError("a molecule needs a molblock or a smiles")
        return self


class Structure(BaseModel):
    """Atoms as parallel arrays: atomic numbers, Cartesian positions in Å (flat x,y,z),
    row-major 3x3 cell in Å or null, and complete bonds as atom index pairs."""

    model_config = _CAMEL

    elements: list[int]
    positions: list[float]
    cell: list[float] | None
    bonds: list[tuple[int, int]]
    bond_orders: list[int] | None = None
    molecules: list[int] | None = None
    residue_names: list[str] | None = None
    residue_ids: list[int] | None = None
    atom_names: list[str] | None = None
    chain_ids: list[str] | None = None
    formal_charges: list[int] | None = None
    scalars: dict[str, list[float]] | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        n = len(self.elements)
        if n > MAX_ATOMS:
            raise ValueError(f"{n} atoms exceeds the limit of {MAX_ATOMS}")
        if any(z < 1 or z > 118 for z in self.elements):
            raise ValueError("elements must be atomic numbers 1..118")
        if len(self.positions) != 3 * n:
            raise ValueError(f"positions has {len(self.positions)} values, expected {3 * n}")
        if self.cell is not None and len(self.cell) != 9:
            raise ValueError("cell must have 9 values (row-major 3x3) or be null")
        for i, j in self.bonds:
            if not (0 <= i < n and 0 <= j < n) or i == j:
                raise ValueError(f"bond ({i}, {j}) does not join two distinct atoms of {n}")
        if self.bond_orders is not None:
            if len(self.bond_orders) != len(self.bonds):
                raise ValueError("bondOrders must be parallel to bonds")
            if any(o not in (1, 2, 3, 4) for o in self.bond_orders):
                raise ValueError("bond orders must be 1, 2, 3 or 4 (aromatic)")
        per_atom: dict[str, list[Any] | None] = {
            "molecules": self.molecules,
            "residueNames": self.residue_names,
            "residueIds": self.residue_ids,
            "atomNames": self.atom_names,
            "chainIds": self.chain_ids,
            "formalCharges": self.formal_charges,
        }
        for key, values in per_atom.items():
            if values is not None and len(values) != n:
                raise ValueError(f"{key} has {len(values)} entries, expected {n}")
        if self.molecules is not None and any(m < 0 for m in self.molecules):
            raise ValueError("molecule indices must be >= 0")
        if self.chain_ids is not None and any(len(c) != 1 for c in self.chain_ids):
            raise ValueError("chain ids must be single characters")
        for name, values in (self.scalars or {}).items():
            if len(values) != n:
                raise ValueError(f"scalar channel {name!r} has {len(values)} entries, expected {n}")
        return self

    @property
    def n_atoms(self) -> int:
        return len(self.elements)

    def positions_array(self) -> NDArray[np.float64]:
        """Positions as an ``(N, 3)`` array in Å."""
        return np.asarray(self.positions, dtype=np.float64).reshape(-1, 3)

    def cell_matrix(self) -> NDArray[np.float64] | None:
        """Cell as a ``(3, 3)`` array whose rows are the lattice vectors, or None."""
        return None if self.cell is None else np.asarray(self.cell, dtype=np.float64).reshape(3, 3)

    @classmethod
    def from_arrays(
        cls,
        elements: ArrayLike,
        positions: ArrayLike,
        *,
        cell: ArrayLike | None = None,
        bonds: ArrayLike | None = None,
        **per_atom: Any,
    ) -> Structure:
        """Build a validated Structure from NumPy-like arrays."""
        pos = np.asarray(positions, dtype=np.float64).reshape(-1)
        bond_list = [] if bonds is None else np.asarray(bonds, dtype=np.int64).reshape(-1, 2).tolist()
        data: dict[str, Any] = {
            "elements": np.asarray(elements, dtype=np.int64).reshape(-1).tolist(),
            "positions": pos.tolist(),
            "cell": None if cell is None else np.asarray(cell, dtype=np.float64).reshape(-1).tolist(),
            "bonds": bond_list,
        }
        for key, value in per_atom.items():
            if value is None:
                continue
            if key == "scalars":
                data[key] = {k: np.asarray(v, dtype=np.float64).tolist() for k, v in value.items()}
            else:
                data[key] = np.asarray(value).tolist()
        return cls.model_validate(data)


class BuilderResult(BaseModel):
    """A structure built by a megane Builder tool, with warnings and provenance."""

    model_config = ConfigDict(extra="forbid")

    contract: Literal[1] = CONTRACT_VERSION
    name: Annotated[str, Field(min_length=1, max_length=128)]
    structure: Structure
    warnings: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)

    _summary: str | None = PrivateAttr(default=None)

    def with_summary(self, summary: str) -> BuilderResult:
        """Set the one-line text summary returned to LLM clients (§5)."""
        self._summary = summary
        return self

    @property
    def summary(self) -> str | None:
        return self._summary


MoleculeInput = Annotated[Molecule, widget("molecule")]
"""A ``molecule`` argument: the library picker in Builder."""

DocumentInput = Annotated[Structure, widget("document")]
"""The open document, filled by Builder (§4.3). At most one per tool."""

OptionalDocumentInput = Annotated[Structure | None, widget("document")]
"""The open document for tools that also work without one; give it the default ``None``."""
