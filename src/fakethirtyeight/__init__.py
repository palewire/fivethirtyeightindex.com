"""Index every fivethirtyeight.com URL captured by the Wayback Machine."""

from __future__ import annotations

__all__ = ["__version__"]

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("fakethirtyeight")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"
