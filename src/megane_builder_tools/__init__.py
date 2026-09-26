"""megane-builder-tools: reference MCP server and SDK for megane Builder tool buttons."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("megane-builder-tools")
except PackageNotFoundError:  # pragma: no cover - running from a source tree without metadata
    __version__ = "0.0.0"

__all__ = ["__version__"]
