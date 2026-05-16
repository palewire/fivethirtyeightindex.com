from __future__ import annotations

from pathlib import Path

DATA_DIR = Path("data")
SHARDS_DIR = DATA_DIR / "shards"
STATE_FILE = DATA_DIR / "state.json"
INDEX_FILE = DATA_DIR / "index.csv"


def ensure_dirs() -> None:
    SHARDS_DIR.mkdir(parents=True, exist_ok=True)
