"""Bulk-download Wayback snapshot HTML for every curated article.

Reads ``data/enriched.csv``, fetches each row's ``wayback_url`` (the raw
``id_`` snapshot — no Wayback chrome), and saves the bytes to disk
under ``data/articles/<year>/<url-hash>.html.gz``.

The point of this pass is to put a local copy of every story on disk
so we can mine it for assets (images, datawrapper embeds, project
links, embedded JSON, etc.) without re-fetching from Wayback every
time.

Layout: gzipped HTML, sharded by snapshot year. A manifest CSV maps
url → file path + byte count + outcome. Resumable: existing
non-empty files (and rows already logged as ``ok``) are skipped.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import logging
import threading
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

from fakethirtyeight.enrich import ENRICHED_FILE
from fakethirtyeight.http import make_client
from fakethirtyeight.paths import DATA_DIR, ensure_dirs

log = logging.getLogger(__name__)

ARTICLES_DIR = DATA_DIR / "articles"
DOWNLOAD_LOG = DATA_DIR / "article_download_log.csv"

LOG_FIELDS = (
    "url",
    "wayback_url",
    "file_path",
    "bytes",
    "status",
    "error",
)

#: A successful download must be at least this large. Anything smaller
#: is almost certainly a Wayback error page that snuck past the status
#: check; we reject it so the retry loop has another shot.
_MIN_VALID_BYTES = 512


@dataclass(slots=True, frozen=True)
class _DownloadTarget:
    url: str
    wayback_url: str
    snapshot_year: str
    out_path: Path


def _url_hash(url: str) -> str:
    """16-char hex hash — short enough to be readable, wide enough to
    have effectively no collision risk across our ~25k articles."""
    return hashlib.sha1(url.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


def path_for(url: str, snapshot_timestamp: str, base: Path = ARTICLES_DIR) -> Path:
    """``data/articles/<yyyy>/<hash>.html.gz`` — stable across re-runs."""
    year = (snapshot_timestamp or "0000")[:4]
    return base / year / f"{_url_hash(url)}.html.gz"


@retry(
    retry=retry_if_exception_type(httpx.HTTPError),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    reraise=True,
)
def _fetch_to_disk(client: httpx.Client, target: _DownloadTarget) -> int:
    """Fetch one snapshot, gzip-write to ``target.out_path``, return byte count.

    Writes to a ``.part`` sibling first and renames on success so an
    interrupted run never leaves a misleadingly-named partial file.
    """
    tmp = target.out_path.with_name(target.out_path.name + ".part")
    target.out_path.parent.mkdir(parents=True, exist_ok=True)
    with client.stream("GET", target.wayback_url, follow_redirects=True) as resp:
        if resp.status_code in {429, 500, 502, 503, 504}:
            raise httpx.HTTPStatusError(
                "retryable", request=resp.request, response=resp
            )
        resp.raise_for_status()
        bytes_written = 0
        with gzip.open(tmp, "wb") as fh:
            for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                fh.write(chunk)
                bytes_written += len(chunk)
    if bytes_written < _MIN_VALID_BYTES:
        tmp.unlink(missing_ok=True)
        msg = f"snapshot too small ({bytes_written} bytes)"
        raise httpx.HTTPError(msg)
    tmp.replace(target.out_path)
    return bytes_written


def _load_done(log_path: Path) -> set[str]:
    """URLs that previously downloaded successfully."""
    if not log_path.exists():
        return set()
    done: set[str] = set()
    with log_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (row.get("status") or "") == "ok" and row.get("url"):
                done.add(row["url"])
    return done


def _iter_targets(enriched_path: Path, out_dir: Path) -> list[_DownloadTarget]:
    """Build the work list from the enriched CSV."""
    targets: list[_DownloadTarget] = []
    with enriched_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (row.get("http_status") or "") != "200":
                continue
            url = (row.get("url") or "").strip()
            wb = (row.get("wayback_url") or "").strip()
            ts = (row.get("snapshot_timestamp") or "").strip()
            if not url or not wb:
                continue
            targets.append(
                _DownloadTarget(
                    url=url,
                    wayback_url=wb,
                    snapshot_year=ts[:4] or "0000",
                    out_path=path_for(url, ts, base=out_dir),
                )
            )
    return targets


def download_articles(
    *,
    workers: int = 4,
    limit: int | None = None,
    enriched_path: Path = ENRICHED_FILE,
    out_dir: Path = ARTICLES_DIR,
    log_path: Path = DOWNLOAD_LOG,
) -> tuple[int, int, int]:
    """Fetch every curated article's Wayback snapshot to ``out_dir``.

    Returns ``(downloaded, skipped_existing, failed)``. Skips URLs whose
    destination file already exists (and is non-trivially sized) and
    URLs already logged as ``ok``.
    """
    ensure_dirs()
    out_dir.mkdir(parents=True, exist_ok=True)

    all_targets = _iter_targets(enriched_path, out_dir)
    log.info("enriched.csv contributes %d candidate targets", len(all_targets))

    done = _load_done(log_path)

    pending: list[_DownloadTarget] = []
    skipped_existing = 0
    for t in all_targets:
        if t.url in done:
            skipped_existing += 1
            continue
        if t.out_path.exists() and t.out_path.stat().st_size >= _MIN_VALID_BYTES:
            skipped_existing += 1
            continue
        pending.append(t)

    log.info(
        "%d already on disk / logged; %d to fetch",
        skipped_existing,
        len(pending),
    )

    if limit is not None:
        pending = pending[:limit]
        log.info("limit=%d, fetching %d", limit, len(pending))

    if not pending:
        return (0, skipped_existing, 0)

    write_header = not log_path.exists()
    write_lock = threading.Lock()

    with (
        make_client() as client,
        log_path.open("a", newline="", encoding="utf-8") as fh,
    ):
        writer = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
        if write_header:
            writer.writeheader()
            fh.flush()

        def _process(t: _DownloadTarget) -> int:
            try:
                n = _fetch_to_disk(client, t)
                with write_lock:
                    writer.writerow(
                        {
                            "url": t.url,
                            "wayback_url": t.wayback_url,
                            "file_path": str(t.out_path.relative_to(DATA_DIR.parent)),
                            "bytes": str(n),
                            "status": "ok",
                            "error": "",
                        }
                    )
                    fh.flush()
                return 1
            except Exception as exc:  # noqa: BLE001
                with write_lock:
                    writer.writerow(
                        {
                            "url": t.url,
                            "wayback_url": t.wayback_url,
                            "file_path": str(t.out_path.relative_to(DATA_DIR.parent)),
                            "bytes": "0",
                            "status": "error",
                            "error": repr(exc)[:200],
                        }
                    )
                    fh.flush()
                log.warning("download failed: %s — %s", t.url[-80:], exc)
                return 0

        outcomes = thread_map(
            _process,
            pending,
            max_workers=workers,
            desc="fetching",
            unit="article",
        )

    n_ok = sum(outcomes)
    return (n_ok, skipped_existing, len(pending) - n_ok)
