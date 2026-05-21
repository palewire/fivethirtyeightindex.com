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
from urllib.parse import urlparse

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tqdm.contrib.concurrent import thread_map

from fakethirtyeight.classify import classify
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


# Kinds where a missing byline is most likely an extractor miss rather
# than a legitimately authorless entry (liveblogs were staff-authored,
# project landings rarely credit individuals).
_BYLINE_RESCRAPE_KINDS: frozenset[str] = frozenset({"article", "video", "methodology"})


def rescrape_bylines(
    *,
    enriched_path: Path = ENRICHED_FILE,
    workers: int = 4,
    delay: float = 1.0,
    limit: int | None = None,
    kinds: frozenset[str] = _BYLINE_RESCRAPE_KINDS,
) -> tuple[int, int, int]:
    """Re-fetch rows whose byline is empty and re-extract metadata.

    Only updates the byline column when the new extractor produces a
    non-empty value. Returns
    ``(refetched_count, recovered_count, transient_failures)`` — the
    third number counts rows whose retry hit a network/HTTP error that
    survived tenacity's backoff. A later run usually succeeds.

    Targets the kinds in ``kinds`` (default: article, video, methodology)
    where a missing byline likely means the extractor missed an existing
    one. Liveblog + project entries are skipped because they're usually
    legitimately authorless.
    """
    if not enriched_path.exists():
        msg = f"enriched file not found: {enriched_path}. Run `enrich` first."
        raise FileNotFoundError(msg)

    ensure_dirs()

    rows, fieldnames = _load_all_rows(enriched_path)
    targets: list[tuple[int, EnrichTarget]] = []
    for idx, row in enumerate(rows):
        if row.get("error"):
            continue
        if row.get("byline"):
            continue
        if (row.get("kind") or "") not in kinds:
            continue
        url = row.get("url") or ""
        ts = row.get("snapshot_timestamp") or ""
        if not url or not ts:
            continue
        targets.append(
            (
                idx,
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
        return (0, 0, 0)

    log.info(
        "rescraping %d rows with empty byline using %d workers, delay=%.1fs",
        len(targets),
        workers,
        delay,
    )

    write_lock = threading.Lock()
    failed_urls: list[str] = []

    with make_client() as client:

        def _process(target_pair: tuple[int, EnrichTarget]) -> tuple[int, int]:
            idx, target = target_pair
            result = _fetch_and_extract(client, target, delay=delay)
            if result.error:
                with write_lock:
                    failed_urls.append(target.url)
                return (0, 1)
            if not result.metadata.byline:
                return (0, 0)
            with write_lock:
                rows[idx]["byline"] = result.metadata.byline
                # Don't overwrite title/date — those were correct before;
                # only fill in the byline gap.
            return (1, 0)

        outcomes = thread_map(
            _process,
            targets,
            max_workers=workers,
            desc="rescrape-bylines",
            unit="url",
        )

    recovered, errored = _tally(outcomes)
    _write_back(enriched_path, fieldnames, rows)
    _log_failures("rescrape-bylines", recovered, len(targets), failed_urls)
    return (len(targets), recovered, errored)


def rescrape_dates(
    *,
    enriched_path: Path = ENRICHED_FILE,
    workers: int = 4,
    delay: float = 1.0,
    limit: int | None = None,
) -> tuple[int, int, int]:
    """Re-fetch rows whose ``published_at`` is only YYYY-MM precision.

    The URL-path fallback gives us year+month for Blogspot-era articles when
    no date metadata was found. The updated extractor now parses the
    ``h2.date-header`` Blogspot stamped on every post, recovering full
    YYYY-MM-DD. Only overwrites when the new extraction yields a longer
    (more precise) date. Returns
    ``(refetched_count, recovered_count, transient_failures)`` — the
    third number counts rows whose retry hit a network/HTTP error that
    survived tenacity's backoff. A later run usually succeeds.
    """
    if not enriched_path.exists():
        msg = f"enriched file not found: {enriched_path}. Run `enrich` first."
        raise FileNotFoundError(msg)

    ensure_dirs()

    rows, fieldnames = _load_all_rows(enriched_path)
    targets: list[tuple[int, EnrichTarget]] = []
    for idx, row in enumerate(rows):
        d = row.get("published_at") or ""
        # YYYY-MM is exactly 7 chars; anything richer (YYYY-MM-DD or full
        # ISO) is already at the precision we want. Slice (not index) so
        # short/empty strings short-circuit without an IndexError.
        if len(d) != 7 or d[4:5] != "-":
            continue
        url = row.get("url") or ""
        ts = row.get("snapshot_timestamp") or ""
        if not url or not ts:
            continue
        targets.append(
            (
                idx,
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
        log.info("no YYYY-MM rows to rescrape")
        return (0, 0, 0)

    log.info(
        "rescraping %d partial-date rows using %d workers, delay=%.1fs",
        len(targets),
        workers,
        delay,
    )

    write_lock = threading.Lock()
    failed_urls: list[str] = []

    with make_client() as client:

        def _process(target_pair: tuple[int, EnrichTarget]) -> tuple[int, int]:
            idx, target = target_pair
            result = _fetch_and_extract(client, target, delay=delay)
            if result.error:
                with write_lock:
                    failed_urls.append(target.url)
                return (0, 1)
            new_date = result.metadata.published_at
            if not new_date or len(new_date) <= len(
                rows[idx].get("published_at") or ""
            ):
                return (0, 0)
            with write_lock:
                rows[idx]["published_at"] = new_date
            return (1, 0)

        outcomes = thread_map(
            _process,
            targets,
            max_workers=workers,
            desc="rescrape-dates",
            unit="url",
        )

    recovered, errored = _tally(outcomes)
    _write_back(enriched_path, fieldnames, rows)
    _log_failures("rescrape-dates", recovered, len(targets), failed_urls)
    return (len(targets), recovered, errored)


def retry_failed(
    *,
    enriched_path: Path = ENRICHED_FILE,
    workers: int = 4,
    delay: float = 1.0,
    limit: int | None = None,
) -> tuple[int, int, int]:
    """Re-fetch rows that errored or came back without any metadata.

    Targets rows where ``error`` is non-empty (HTTP/network failures) or
    where every metadata field is blank despite a 200 (extractor saw the
    page but couldn't parse a title/byline/date). Overwrites the row's
    fields with the new result when the retry succeeds; leaves rows
    unchanged when the retry still fails. Returns
    ``(refetched_count, recovered_count, transient_failures)`` — the
    third number counts rows whose retry hit a network/HTTP error that
    survived tenacity's backoff (and where the row's ``error`` column
    was just overwritten with the latest attempt's failure repr).
    """
    if not enriched_path.exists():
        msg = f"enriched file not found: {enriched_path}. Run `enrich` first."
        raise FileNotFoundError(msg)

    ensure_dirs()

    rows, fieldnames = _load_all_rows(enriched_path)
    targets: list[tuple[int, EnrichTarget]] = []
    for idx, row in enumerate(rows):
        has_metadata = any(row.get(k) for k in ("title", "byline", "published_at"))
        if not row.get("error") and has_metadata:
            continue
        url = row.get("url") or ""
        ts = row.get("snapshot_timestamp") or ""
        if not url or not ts:
            continue
        targets.append(
            (
                idx,
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
        log.info("no failed rows to retry")
        return (0, 0, 0)

    log.info(
        "retrying %d failed rows using %d workers, delay=%.1fs",
        len(targets),
        workers,
        delay,
    )

    write_lock = threading.Lock()
    failed_urls: list[str] = []

    with make_client() as client:

        def _process(target_pair: tuple[int, EnrichTarget]) -> tuple[int, int]:
            idx, target = target_pair
            result = _fetch_and_extract(client, target, delay=delay)
            new_row = _result_to_row(result)
            recovered_now = bool(
                not result.error
                and (
                    result.metadata.title
                    or result.metadata.byline
                    or result.metadata.published_at
                )
            )
            with write_lock:
                # Replace the row outright — error/http_status/metadata all
                # reflect the latest attempt. Preserve original
                # snapshot_timestamp + url since those came from curated.
                rows[idx].update(new_row)
                if result.error:
                    failed_urls.append(target.url)
            return (1 if recovered_now else 0, 1 if result.error else 0)

        outcomes = thread_map(
            _process,
            targets,
            max_workers=workers,
            desc="retry-failed",
            unit="url",
        )

    recovered, errored = _tally(outcomes)
    _write_back(enriched_path, fieldnames, rows)
    _log_failures("retry-failed", recovered, len(targets), failed_urls)
    return (len(targets), recovered, errored)


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
    return set(load_enriched(out_path).keys())


def _tally(outcomes: list[tuple[int, int]]) -> tuple[int, int]:
    """Sum the (recovered, errored) per-row tuples emitted by a rescrape."""
    recovered = sum(o[0] for o in outcomes)
    errored = sum(o[1] for o in outcomes)
    return recovered, errored


def _write_back(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    """Overwrite ``enriched.csv`` with the in-memory row list."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _log_failures(
    label: str, recovered: int, target_count: int, failed_urls: list[str]
) -> None:
    """Summarize a rescrape pass at INFO + log a sample of transient fails.

    The transient-failure case (TLS EOF, repeated 5xx through tenacity's
    backoff) is the one that's easy to miss otherwise — they leave the
    on-disk row unchanged. Surfacing the URLs lets a follow-up run know
    where to look.
    """
    log.info("%s: recovered %d/%d", label, recovered, target_count)
    if failed_urls:
        sample = failed_urls[:5]
        log.warning(
            "%s: %d transient fetch failures; re-run to retry. First %d: %s",
            label,
            len(failed_urls),
            len(sample),
            sample,
        )


def _load_all_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    """Read every row from ``enriched.csv`` into memory.

    The in-place rescrape functions (rescrape_bylines, rescrape_dates,
    retry_failed) all need the *full* row list so the write-back at the
    end doesn't drop rows. Loading separately from target selection avoids
    a subtle bug where a ``limit`` short-circuit broke out of the read
    loop early and the subsequent overwrite truncated the file.
    """
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or ENRICHED_FIELDS)
        rows = list(reader)
    return rows, fieldnames


def _current_rollup_key(url: str, fallback: str) -> str:
    """Re-derive the rollup key for a URL using the current classifier.

    Falls back to the stored value when the URL won't parse — should be rare
    but keeps callers from losing rows on malformed input.
    """
    if not url:
        return fallback
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return fallback
    return classify(url, host=host).rollup_key or fallback


def _enrichment_quality(row: dict[str, str]) -> tuple[int, bool]:
    """Sort key for picking between rows that collapse to the same key."""
    fields_filled = sum(1 for k in ("title", "byline", "published_at") if row.get(k))
    prefer_features = "/features/" in (row.get("url") or "")
    return (fields_filled, prefer_features)


def load_enriched(path: Path) -> dict[str, dict[str, str]]:
    """Read ``enriched.csv`` keyed by the *current* rollup_key.

    Each row's ``rollup_key`` is re-derived from its URL using the live
    classifier, so changes to classification rules (e.g. merging features/
    and datalab/ slugs) don't strand existing enrichment data. When multiple
    historical rows collapse to the same current key, the row with the most
    complete metadata wins, tie-breaking toward ``/features/`` URLs.
    """
    if not path.exists():
        return {}
    out: dict[str, dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            key = _current_rollup_key(row.get("url") or "", row.get("rollup_key") or "")
            if not key:
                continue
            row["rollup_key"] = key
            existing = out.get(key)
            if existing is None or _enrichment_quality(row) > _enrichment_quality(
                existing
            ):
                out[key] = row
    return out


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
