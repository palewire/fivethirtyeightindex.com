"""Export the merged index to alternative formats."""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

from fakethirtyeight.paths import INDEX_FILE

log = logging.getLogger(__name__)


def to_jsonl(out_path: Path, *, index_path: Path = INDEX_FILE) -> int:
    if not index_path.exists():
        msg = f"index file not found: {index_path}. Run `merge` first."
        raise FileNotFoundError(msg)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with (
        index_path.open(newline="", encoding="utf-8") as src,
        out_path.open("w", encoding="utf-8") as dst,
    ):
        reader = csv.DictReader(src)
        for row in reader:
            dst.write(json.dumps(row, ensure_ascii=False))
            dst.write("\n")
            count += 1
    log.info("wrote %d rows to %s", count, out_path)
    return count


def to_parquet(out_path: Path, *, index_path: Path = INDEX_FILE) -> int:
    """Convert to Parquet. Requires `pyarrow` (in the notebooks extras)."""
    try:
        import pyarrow as pa
        import pyarrow.csv as pa_csv
        import pyarrow.parquet as pq
    except ImportError as exc:
        msg = "Parquet export needs pyarrow. Install with: pip install '.[notebooks]'"
        raise RuntimeError(msg) from exc

    if not index_path.exists():
        msg = f"index file not found: {index_path}. Run `merge` first."
        raise FileNotFoundError(msg)

    table: pa.Table = pa_csv.read_csv(index_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out_path)
    log.info("wrote %d rows to %s", table.num_rows, out_path)
    return table.num_rows
