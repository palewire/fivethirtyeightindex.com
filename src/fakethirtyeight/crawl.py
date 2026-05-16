"""Sharded, parallel CDX crawl."""

from __future__ import annotations

import csv
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tqdm.contrib.concurrent import thread_map

from fakethirtyeight import state as state_mod
from fakethirtyeight.cdx import CdxClient, CdxRow
from fakethirtyeight.paths import SHARDS_DIR, ensure_dirs

log = logging.getLogger(__name__)

DEFAULT_HOST = "fivethirtyeight.com"
DEFAULT_START_YEAR = 2008
SHARD_FIELDS = (
    "urlkey",
    "timestamp",
    "original",
    "mimetype",
    "statuscode",
    "digest",
    "length",
    "shard_id",
)


@dataclass(slots=True, frozen=True)
class Shard:
    host: str
    year: int | None  # None means "no year filter"
    match_type: str = "domain"

    @property
    def shard_id(self) -> str:
        year_part = str(self.year) if self.year is not None else "all"
        return f"cdx-{year_part}-{self.host}"

    @property
    def csv_path(self) -> Path:
        return SHARDS_DIR / f"{self.shard_id}.csv"


def build_default_shards(
    *,
    host: str = DEFAULT_HOST,
    start_year: int = DEFAULT_START_YEAR,
    end_year: int | None = None,
) -> list[Shard]:
    end = end_year or datetime.now(UTC).year
    return [Shard(host=host, year=y) for y in range(start_year, end + 1)]


def run(
    shards: Iterable[Shard],
    *,
    workers: int = 4,
    delay: float = 1.0,
    page_limit: int | None = None,
    row_limit: int | None = None,
) -> None:
    """Run shards in parallel, writing one CSV per shard, persisting state."""

    ensure_dirs()
    shards = list(shards)
    if not shards:
        log.warning("no shards to run")
        return

    def _run(shard: Shard) -> None:
        _run_shard(
            shard,
            delay=delay,
            page_limit=page_limit,
            row_limit=row_limit,
        )

    thread_map(
        _run,
        shards,
        max_workers=workers,
        desc="shards",
        unit="shard",
    )


def _run_shard(
    shard: Shard,
    *,
    delay: float,
    page_limit: int | None,
    row_limit: int | None,
) -> None:
    existing_state = state_mod.load()
    shard_state = existing_state.shards.get(shard.shard_id) or state_mod.ShardState(
        shard_id=shard.shard_id, host=shard.host, year=shard.year
    )
    if shard_state.status == "complete":
        log.info("shard %s already complete; skipping", shard.shard_id)
        return

    state_mod.mark_started(shard_state)

    is_resume = shard_state.last_resume_key is not None and shard.csv_path.exists()
    csv_path = shard.csv_path
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    write_header = not is_resume
    mode = "a" if is_resume else "w"

    try:
        with (
            CdxClient(delay=delay) as client,
            csv_path.open(mode, newline="", encoding="utf-8") as fh,
        ):
            writer = csv.writer(fh)
            if write_header:
                writer.writerow(SHARD_FIELDS)
                fh.flush()

            pages = client.iter_pages(
                shard.host,
                match_type=shard.match_type,
                year=shard.year,
                resume_key=shard_state.last_resume_key,
            )

            for page_num, page in enumerate(pages, start=1):
                for row in page.rows:
                    writer.writerow(_row_to_csv(row, shard.shard_id))
                    shard_state.rows_written += 1
                    if row_limit and shard_state.rows_written >= row_limit:
                        break
                fh.flush()
                shard_state.pages_fetched += 1
                shard_state.last_resume_key = page.next_resume_key
                state_mod.update_shard(shard_state)

                if page_limit and page_num >= page_limit:
                    break
                if row_limit and shard_state.rows_written >= row_limit:
                    break
                if page.next_resume_key is None:
                    break

        if shard_state.last_resume_key is None:
            state_mod.mark_complete(shard_state)
    except Exception as exc:
        log.exception("shard %s failed", shard.shard_id)
        state_mod.mark_failed(shard_state, repr(exc))
        raise


def _row_to_csv(row: CdxRow, shard_id: str) -> list[str]:
    return [
        row.urlkey,
        row.timestamp,
        row.original,
        row.mimetype,
        row.statuscode,
        row.digest,
        row.length,
        shard_id,
    ]
