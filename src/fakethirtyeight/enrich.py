"""Fetch Wayback snapshots for each curated URL and extract minimum metadata.

Reads ``data/curated.csv``, picks each row's most recent snapshot, fetches
the raw HTML through Wayback's ``id_`` endpoint, and runs
:mod:`fakethirtyeight.metadata` over it. Output is ``data/enriched.csv``.

Resumable: rows already in ``enriched.csv`` are skipped on subsequent runs.
Polite by default with configurable concurrency and per-worker delay.
"""

from __future__ import annotations

import csv
import logging
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tqdm.contrib.concurrent import thread_map

from fakethirtyeight.curate import CURATED_FILE
from fakethirtyeight.http import make_client
from fakethirtyeight.metadata import Metadata, extract
from fakethirtyeight.paths import DATA_DIR, ensure_dirs

log = logging.getLogger(__name__)

ENRICHED_FILE = DATA_DIR / "enriched.csv"

WAYBACK_RAW = "https://web.archive.org/web/{timestamp}id_/{url}"

ENRICHED_FIELDS = (
    "rollup_key",
    "kind",
    "url",
    "snapshot_timestamp",
    "wayback_url",
    "title",
    "byline",
    "published_at",
    "extracted_via",
    "http_status",
    "error",
)


@dataclass(slots=True, frozen=True)
class EnrichTarget:
    """One curated row to enrich."""

    rollup_key: str
    kind: str
    url: str
    timestamp: str  # snapshot timestamp to fetch (YYYYMMDDHHMMSS)


@dataclass(slots=True)
class EnrichResult:
    rollup_key: str
    kind: str
    url: str
    snapshot_timestamp: str
    wayback_url: str
    metadata: Metadata
    http_status: int = 0
    error: str = ""


def rescrape_bylines(
    *,
    enriched_path: Path = ENRICHED_FILE,
    workers: int = 4,
    delay: float = 1.0,
    limit: int | None = None,
) -> tuple[int, int]:
    """Re-fetch rows whose byline is empty and re-extract metadata.

    Only updates the byline column when the new extractor produces a
    non-empty value. Returns (refetched_count, recovered_count).

    Run this after improving metadata.extract — most useful for the
    2008-2010 Blogspot-era posts whose byline pattern wasn't covered
    by the original extractor.
    """
    if not enriched_path.exists():
        msg = f"enriched file not found: {enriched_path}. Run `enrich` first."
        raise FileNotFoundError(msg)

    ensure_dirs()

    # Load existing rows, identify which need rescraping.
    rows: list[dict[str, str]] = []
    fieldnames: list[str] = []
    targets: list[tuple[int, EnrichTarget]] = []
    with enriched_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or ENRICHED_FIELDS)
        for row in reader:
            rows.append(row)
            if row.get("error"):
                continue
            if row.get("byline"):
                continue
            url = row.get("url") or ""
            ts = row.get("snapshot_timestamp") or ""
            if not url or not ts:
                continue
            targets.append(
                (
                    len(rows) - 1,
                    EnrichTarget(
                        rollup_key=row.get("rollup_key") or "",
                        kind=row.get("kind") or "",
                        url=url,
                        timestamp=ts,
                    ),
                )
            )
            if limit and len(targets) >= limit:
                break

    if not targets:
        log.info("no candidates to rescrape")
        return (0, 0)

    log.info(
        "rescraping %d rows with empty byline using %d workers, delay=%.1fs",
        len(targets),
        workers,
        delay,
    )

    recovered = 0
    write_lock = threading.Lock()

    with make_client() as client:

        def _process(target_pair: tuple[int, EnrichTarget]) -> int:
            idx, target = target_pair
            result = _fetch_and_extract(client, target, delay=delay)
            if result.error or not result.metadata.byline:
                return 0
            with write_lock:
                rows[idx]["byline"] = result.metadata.byline
                # Don't overwrite title/date — those were correct before;
                # only fill in the byline gap.
            return 1

        outcomes = thread_map(
            _process,
            targets,
            max_workers=workers,
            desc="rescrape-bylines",
            unit="url",
        )
        recovered = sum(outcomes)

    # Write everything back.
    with enriched_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    log.info("recovered %d bylines out of %d rescraped", recovered, len(targets))
    return (len(targets), recovered)


def enrich(
    *,
    curated_path: Path = CURATED_FILE,
    out_path: Path = ENRICHED_FILE,
    workers: int = 4,
    delay: float = 1.0,
    limit: int | None = None,
) -> int:
    """Enrich every curated row that isn't already in ``out_path``.

    Returns the number of newly-enriched rows.
    """
    if not curated_path.exists():
        msg = f"curated file not found: {curated_path}. Run `curate` first."
        raise FileNotFoundError(msg)

    ensure_dirs()

    done = _load_done(out_path)
    targets = list(_iter_targets(curated_path, skip=done, limit=limit))
    if not targets:
        log.info("nothing to do — all %d rows already enriched", len(done))
        return 0

    log.info(
        "enriching %d targets (%d already done) with %d workers, delay=%.1fs",
        len(targets),
        len(done),
        workers,
        delay,
    )

    write_header = not out_path.exists()
    write_lock = threading.Lock()

    with (
        make_client() as client,
        out_path.open("a", newline="", encoding="utf-8") as fh,
    ):
        writer = csv.DictWriter(fh, fieldnames=ENRICHED_FIELDS)
        if write_header:
            writer.writeheader()
            fh.flush()

        def _process(target: EnrichTarget) -> int:
            result = _fetch_and_extract(client, target, delay=delay)
            with write_lock:
                writer.writerow(_result_to_row(result))
                fh.flush()
            return 1 if not result.error else 0

        outcomes = thread_map(
            _process,
            targets,
            max_workers=workers,
            desc="enrich",
            unit="url",
        )

    n_ok = sum(outcomes)
    log.info("enriched %d/%d successfully", n_ok, len(targets))
    return n_ok


def _iter_targets(
    curated_path: Path, *, skip: set[str], limit: int | None
) -> Iterable[EnrichTarget]:
    n = 0
    with curated_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rk = row.get("rollup_key") or ""
            if rk in skip:
                continue
            url = row.get("url") or ""
            ts = row.get("last_seen_ts") or row.get("first_seen_ts") or ""
            if not url or not ts:
                continue
            yield EnrichTarget(
                rollup_key=rk,
                kind=row.get("kind") or "",
                url=url,
                timestamp=ts,
            )
            n += 1
            if limit and n >= limit:
                return


def _load_done(out_path: Path) -> set[str]:
    if not out_path.exists():
        return set()
    done: set[str] = set()
    with out_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rk = row.get("rollup_key") or ""
            if rk:
                done.add(rk)
    return done


def _fetch_and_extract(
    client: httpx.Client, target: EnrichTarget, *, delay: float
) -> EnrichResult:
    wayback_url = WAYBACK_RAW.format(timestamp=target.timestamp, url=target.url)
    result = EnrichResult(
        rollup_key=target.rollup_key,
        kind=target.kind,
        url=target.url,
        snapshot_timestamp=target.timestamp,
        wayback_url=wayback_url,
        metadata=Metadata(),
    )
    try:
        resp = _fetch(client, wayback_url)
        result.http_status = resp.status_code
        if resp.status_code != 200:
            result.error = f"http {resp.status_code}"
            return result
        result.metadata = extract(resp.content, fallback_url=target.url)
    except Exception as exc:  # noqa: BLE001
        result.error = repr(exc)
        log.warning("enrich failed for %s: %s", target.url, exc)
    finally:
        if delay:
            time.sleep(delay)
    return result


@retry(
    retry=retry_if_exception_type(httpx.HTTPError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    reraise=True,
)
def _fetch(client: httpx.Client, url: str) -> httpx.Response:
    resp = client.get(url)
    if resp.status_code in {429, 500, 502, 503, 504}:
        raise httpx.HTTPStatusError("retryable", request=resp.request, response=resp)
    return resp


def _result_to_row(r: EnrichResult) -> dict[str, str]:
    return {
        "rollup_key": r.rollup_key,
        "kind": r.kind,
        "url": r.url,
        "snapshot_timestamp": r.snapshot_timestamp,
        "wayback_url": r.wayback_url,
        "title": r.metadata.title,
        "byline": r.metadata.byline,
        "published_at": r.metadata.published_at,
        "extracted_via": r.metadata.extracted_via,
        "http_status": str(r.http_status) if r.http_status else "",
        "error": r.error,
    }
