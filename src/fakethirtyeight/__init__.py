"""Index every fivethirtyeight.com URL captured by the Wayback Machine."""

from __future__ import annotations

__all__ = ["__version__"]

from importlib.metadata import PackageNotFoundError, version

from dotenv import load_dotenv

# Load .env from cwd so IA_ACCESS_KEY/IA_SECRET_KEY etc. are available
# before any submodule reads os.environ. Happens once per process.
load_dotenv()

try:
    __version__ = version("fakethirtyeight")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"
