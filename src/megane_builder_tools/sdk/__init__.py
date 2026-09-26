"""SDK for writing megane Builder tools on top of the MCP Python SDK.

See megane's "Builder Tool Contract (MCP)" page for the specification.
"""

from .contract import (
    CONTRACT_VERSION,
    MAX_ATOMS,
    META_KEY,
    Angle,
    Cell,
    Count,
    Density,
    Element,
    Length,
    Seed,
    Selection,
    Temperature,
    atom_of,
    unit,
    widget,
)
from .models import BuilderResult, DocumentInput, Molecule, MoleculeInput, OptionalDocumentInput, Structure
from .tool import BuilderTool, Progress, ToolError, builder_tool

__all__ = [
    "CONTRACT_VERSION",
    "MAX_ATOMS",
    "META_KEY",
    "Angle",
    "BuilderResult",
    "BuilderTool",
    "Cell",
    "Count",
    "Density",
    "DocumentInput",
    "Element",
    "Length",
    "Molecule",
    "MoleculeInput",
    "OptionalDocumentInput",
    "Progress",
    "Seed",
    "Selection",
    "Structure",
    "Temperature",
    "ToolError",
    "atom_of",
    "builder_tool",
    "unit",
    "widget",
]
