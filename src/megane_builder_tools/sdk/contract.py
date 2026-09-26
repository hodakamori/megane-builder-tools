"""Constants and schema annotations of the megane Builder Tool Contract.

The contract is specified in megane's documentation
(``docs/docs/dev/builder-tools.md``, "Builder Tool Contract (MCP)"). Section
numbers in comments (``§4.2``) refer to that page.
"""

from __future__ import annotations

from typing import Annotated, Any, Final, Literal

from pydantic import Field
from pydantic.fields import FieldInfo

CONTRACT_VERSION: Final = 1
"""The contract version this SDK implements (§11)."""

META_KEY: Final = "io.github.megane-labs/builder"
"""``Tool._meta`` key that turns an MCP tool into a Builder button (§3)."""

WIDGET_KEY: Final = "x-megane-widget"
UNIT_KEY: Final = "x-megane-unit"
OF_KEY: Final = "x-megane-of"

MAX_ATOMS: Final = 500_000
"""Largest structure Builder accepts in one result (§5.1)."""

Category = Literal["bulk", "molecule", "polymer", "surface", "solvation", "other"]
ApplyMode = Literal["new_document", "insert"]
DocumentUse = Literal["none", "optional", "required"]
Widget = Literal["molecule", "atom", "element", "cell", "selection", "seed", "document"]
UnitName = Literal["angstrom", "degree", "g/cm3", "kelvin", "count"]

CATEGORIES: Final[tuple[str, ...]] = ("bulk", "molecule", "polymer", "surface", "solvation", "other")
APPLY_MODES: Final[tuple[str, ...]] = ("new_document", "insert")
DOCUMENT_USES: Final[tuple[str, ...]] = ("none", "optional", "required")
WIDGETS: Final[tuple[str, ...]] = ("molecule", "atom", "element", "cell", "selection", "seed", "document")
UNITS: Final[tuple[str, ...]] = ("angstrom", "degree", "g/cm3", "kelvin", "count")


def widget(name: Widget, **extra: Any) -> FieldInfo:
    """A field annotation that selects a Builder widget (§4.2)."""
    return Field(json_schema_extra={WIDGET_KEY: name, **extra})


def unit(name: UnitName) -> FieldInfo:
    """A field annotation naming the canonical unit of a number (§4.4)."""
    return Field(json_schema_extra={UNIT_KEY: name})


def atom_of(argument: str) -> FieldInfo:
    """Annotation for an atom index into the molecule passed as ``argument`` (§4.2)."""
    return Field(ge=0, json_schema_extra={WIDGET_KEY: "atom", OF_KEY: argument})


Seed = Annotated[
    int, Field(ge=0, title="Seed", description="Random seed; the same seed gives the same result."), widget("seed")
]
"""The ``seed`` argument every stochastic tool declares (§4.4)."""

Element = Annotated[int, Field(ge=1, le=118), widget("element")]
"""An atomic number, shown as the periodic-table picker."""

Cell = Annotated[list[float], Field(min_length=9, max_length=9), widget("cell")]
"""A row-major 3x3 cell in Å."""

Selection = Annotated[list[Annotated[int, Field(ge=0)]], widget("selection")]
"""Atom indices into the ``document`` argument, filled from the 3D selection."""

Length = Annotated[float, unit("angstrom")]
Angle = Annotated[float, unit("degree")]
Density = Annotated[float, unit("g/cm3")]
Temperature = Annotated[float, unit("kelvin")]
Count = Annotated[int, unit("count")]
