"""Bulk-download podcast MP3s into ``data/podcasts/`` for later upload.

Each canonical podcast URL produced by :func:`save_now.collect_podcast_mp3s`
is streamed to disk under a flat filename of the form
``<host>__<basename>.mp3``. Resumable: existing non-empty files are
skipped, and outcomes are appended to ``data/podcast_download_log.csv``
so re-runs only fetch what's missing or failed.

Uses ``follow_redirects=True`` because the podtrac.com URLs redirect to
the actual file on megaphone.fm or castfire.com.
"""

from __future__ import annotations

import csv
import logging
import threading
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

from fakethirtyeight.http import make_client
from fakethirtyeight.paths import DATA_DIR, ensure_dirs
from fakethirtyeight.save_now import collect_podcast_mp3s

log = logging.getLogger(__name__)

PODCASTS_DIR = DATA_DIR / "podcasts"
DOWNLOAD_LOG = DATA_DIR / "podcast_download_log.csv"

LOG_FIELDS = ("mp3_url", "filename", "bytes", "status", "error")

#: Treat anything smaller than this on disk as a failed/aborted partial.
_MIN_VALID_BYTES = 1024


@dataclass(slots=True, frozen=True)
class _DownloadTarget:
    url: str
    out_path: Path


def filename_for(url: str) -> str:
    """``<host>__<basename>`` — flat, collision-resistant across hosts."""
    p = urlparse(url)
    host = (p.netloc or "unknown").replace(".", "_")
    basename = Path(p.path).name or "audio.mp3"
    return f"{host}__{basename}"


@retry(
    retry=retry_if_exception_type(httpx.HTTPError),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    reraise=True,
)
def _stream_to_disk(client: httpx.Client, url: str, out_path: Path) -> int:
    """Stream one URL to ``out_path``. Returns the byte count.

    Writes to a ``.part`` sibling first and renames on success so an
    interrupted download never leaves a misleadingly-named partial file.
    """
    tmp = out_path.with_name(out_path.name + ".part")
    bytes_written = 0
    with client.stream("GET", url, follow_redirects=True) as resp:
        if resp.status_code in {429, 500, 502, 503, 504}:
            raise httpx.HTTPStatusError(
                "retryable", request=resp.request, response=resp
            )
        resp.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                fh.write(chunk)
                bytes_written += len(chunk)
    tmp.replace(out_path)
    return bytes_written


def download_podcasts(
    *,
    workers: int = 4,
    limit: int | None = None,
    log_path: Path = DOWNLOAD_LOG,
    out_dir: Path = PODCASTS_DIR,
) -> tuple[int, int]:
    """Fetch all canonical podcast MP3 URLs into ``out_dir``.

    Returns ``(downloaded, failed)``. Skips URLs whose destination
    already exists on disk (and is bigger than 1 KB).
    """
    ensure_dirs()
    out_dir.mkdir(parents=True, exist_ok=True)

    urls = collect_podcast_mp3s()
    log.info("collected %d canonical MP3 URLs", len(urls))

    targets: list[_DownloadTarget] = []
    skipped_existing = 0
    for u in urls:
        out_path = out_dir / filename_for(u)
        if out_path.exists() and out_path.stat().st_size >= _MIN_VALID_BYTES:
            skipped_existing += 1
            continue
        targets.append(_DownloadTarget(url=u, out_path=out_path))

    log.info("%d already on disk; %d to fetch", skipped_existing, len(targets))
    if limit is not None:
        targets = targets[:limit]
        log.info("limit=%d, fetching %d", limit, len(targets))

    if not targets:
        return (0, 0)

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
                n = _stream_to_disk(client, t.url, t.out_path)
                with write_lock:
                    writer.writerow(
                        {
                            "mp3_url": t.url,
                            "filename": t.out_path.name,
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
                            "mp3_url": t.url,
                            "filename": t.out_path.name,
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
            targets,
            max_workers=workers,
            desc="downloading",
            unit="file",
        )

    n_ok = sum(outcomes)
    return (n_ok, len(targets) - n_ok)
