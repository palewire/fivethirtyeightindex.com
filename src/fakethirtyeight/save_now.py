"""Submit URLs to archive.org's Save Page Now V2 API.

Targets the podcast MP3 URLs we pulled out of fivethirtyeight.com's
``/player/?src=…`` iframe URLs. The MP3s themselves live on
``traffic.megaphone.fm`` (typically behind a podtrac redirect), so they
were never crawled by archive.org as part of the CDX sweep we did for
``*.fivethirtyeight.com``. SPN2 fetches them on demand and adds them to
the Wayback Machine.

Auth: requires an archive.org S3-like access/secret pair in the
``IA_ACCESS_KEY`` and ``IA_SECRET_KEY`` env vars
(https://archive.org/account/s3.php). Anonymous SPN hits a per-IP
bandwidth cap quickly; with keys you get higher per-account quotas.

Resumable: every submission outcome is appended to a log CSV. Re-running
skips URLs that were already submitted or marked as already-archived.
"""

from __future__ import annotations

import csv
import logging
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from fakethirtyeight.http import make_client
from fakethirtyeight.paths import DATA_DIR, INDEX_FILE, ensure_dirs

log = logging.getLogger(__name__)

SPN2_SUBMIT_URL = "https://web.archive.org/save/"
WAYBACK_AVAILABILITY_URL = "https://archive.org/wayback/available"

PODCAST_LOG = DATA_DIR / "podcast_archive_log.csv"

#: Supplementary MP3 URLs discovered outside the Wayback CDX crawl.
#: Populated by hand or by helper scripts (e.g. scraping ESPN's live
#: ``podcast/archive`` pages). One URL per row, first column.
EXTRA_URLS_FILE = DATA_DIR / "extra_podcast_urls.csv"
LOG_FIELDS = (
    "mp3_url",
    "submitted_at",
    "status",
    "job_id",
    "wayback_timestamp",
    "error",
)


@dataclass(slots=True, frozen=True)
class SubmissionResult:
    url: str
    status: str  # 'submitted' | 'skipped_archived' | 'error'
    job_id: str = ""
    wayback_timestamp: str = ""
    error: str = ""


def load_credentials() -> tuple[str, str] | None:
    """Return ``(access, secret)`` from env, or ``None`` if either is missing."""
    access = os.environ.get("IA_ACCESS_KEY")
    secret = os.environ.get("IA_SECRET_KEY")
    if not access or not secret:
        return None
    return access, secret


def _canonical_audio_url(url: str) -> str:
    """Strip the query string from a podcast audio URL.

    The MP3s carry ad-server tracking params (``ad_params``, ``station_id``,
    ``updated``) that vary across captures but don't change the underlying
    audio file. Submitting both forms wastes SPN quota; canonicalize to
    the scheme + host + path.

    Some player iframe ``?src=`` values were misencoded on the site
    without a scheme (``src=www.podtrac.com/…`` rather than
    ``src=https://www.podtrac.com/…``). Promote those to https so they
    canonicalize to the same form as the well-encoded variants.
    """
    p = urlparse(url)
    if not p.scheme:
        url = "https://" + url.lstrip("/")
        p = urlparse(url)
    path = p.path
    # A handful of player iframe srcs were truncated in CDX to ".mp"
    # instead of ".mp3". Restore the trailing 3 so we canonicalize to
    # the actual audio file (verified via HEAD against megaphone).
    if path.endswith(".mp"):
        path += "3"
    return f"{p.scheme}://{p.netloc}{path}"


def collect_podcast_mp3s(index_path: Path = INDEX_FILE) -> list[str]:
    """Extract unique podcast audio URLs from ``/player/?src=…`` rows.

    Dedups by canonical form (scheme + host + path), so URLs that differ
    only in their ad-tracking query strings collapse to one entry.
    """
    if not index_path.exists():
        msg = f"index file not found: {index_path}. Run `crawl` + `merge` first."
        raise FileNotFoundError(msg)

    unique: set[str] = set()
    with index_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            path = row.get("path") or ""
            if "/player/" not in path:
                continue
            url = row.get("url") or ""
            src = parse_qs(urlparse(url).query).get("src", [None])[0]
            if src and "mp3" in src.lower():
                unique.add(_canonical_audio_url(src))

    # Fold in any hand-curated supplementary URLs (e.g. episodes
    # discovered on ESPN's still-live PodCenter archive pages that
    # were never embedded as a /player/ iframe).
    unique.update(_load_extra_urls())

    return sorted(unique)


def _load_extra_urls(path: Path = EXTRA_URLS_FILE) -> set[str]:
    """Read canonical MP3 URLs from the supplementary CSV, if present."""
    if not path.exists():
        return set()
    out: set[str] = set()
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            url = (row.get("mp3_url") or "").strip()
            if url:
                out.add(_canonical_audio_url(url))
    return out


def check_recent_capture(client: httpx.Client, url: str) -> str:
    """Return the Wayback timestamp of the closest existing capture, or ``""``.

    Uses the lightweight availability API which returns just one result.
    """
    try:
        resp = client.get(WAYBACK_AVAILABILITY_URL, params={"url": url})
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return ""
    snap = (data.get("archived_snapshots") or {}).get("closest") or {}
    if snap.get("available"):
        return snap.get("timestamp", "")
    return ""


@retry(
    retry=retry_if_exception_type(httpx.HTTPError),
    stop=stop_after_attempt(8),
    wait=wait_exponential(multiplier=4, min=4, max=300),
    reraise=True,
)
def submit_to_spn2(
    client: httpx.Client, url: str, auth: tuple[str, str]
) -> dict[str, str]:
    """POST one URL to Save Page Now V2 and return the JSON response."""
    headers = {
        "Authorization": f"LOW {auth[0]}:{auth[1]}",
        "Accept": "application/json",
    }
    resp = client.post(SPN2_SUBMIT_URL, data={"url": url}, headers=headers)
    if resp.status_code in {429, 500, 502, 503, 504, 523}:
        raise httpx.HTTPStatusError("retryable", request=resp.request, response=resp)
    resp.raise_for_status()
    return resp.json()


def _load_log(path: Path) -> dict[str, dict[str, str]]:
    """Map canonicalized mp3_url → previous log row.

    Older runs logged the full URL with query string; new runs log the
    canonical form. Canonicalize on read so resume works across both.
    """
    if not path.exists():
        return {}
    out: dict[str, dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            url = row.get("mp3_url") or ""
            if url:
                out[_canonical_audio_url(url)] = row
    return out


def archive_podcast_mp3s(
    *,
    delay: float = 3.0,
    limit: int | None = None,
    skip_recent: bool = True,
    log_path: Path = PODCAST_LOG,
) -> tuple[int, int, int]:
    """Submit each podcast MP3 to SPN2. Returns ``(submitted, skipped, failed)``.

    Resumes from the log on disk. URLs already present in the log (either
    successfully submitted or marked as already-archived) are not retried.
    """
    auth = load_credentials()
    if not auth:
        msg = (
            "Set IA_ACCESS_KEY and IA_SECRET_KEY env vars first. "
            "Get an archive.org S3-like keypair at "
            "https://archive.org/account/s3.php."
        )
        raise RuntimeError(msg)

    ensure_dirs()
    existing = _load_log(log_path)
    all_mp3s = collect_podcast_mp3s()
    log.info("collected %d unique MP3 URLs from the index", len(all_mp3s))

    # Only consider a URL "done" if it actually succeeded; errored rows
    # get retried on resume so transient SSL/429 failures don't stick.
    finished = {
        u
        for u, row in existing.items()
        if (row.get("status") or "") in {"submitted", "skipped_archived"}
    }
    pending = [m for m in all_mp3s if m not in finished]
    log.info(
        "%d already succeeded; %d remaining (incl. retries of past errors)",
        len(finished),
        len(pending),
    )
    if limit is not None:
        pending = pending[:limit]
        log.info("limit=%d, processing %d", limit, len(pending))

    write_header = not log_path.exists()
    submitted = skipped = failed = 0

    with (
        make_client() as client,
        log_path.open("a", newline="", encoding="utf-8") as fh,
    ):
        writer = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
        if write_header:
            writer.writeheader()

        for i, url in enumerate(pending, 1):
            result = _process_one(client, url, auth, skip_recent=skip_recent)
            writer.writerow(
                {
                    "mp3_url": result.url,
                    "submitted_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "status": result.status,
                    "job_id": result.job_id,
                    "wayback_timestamp": result.wayback_timestamp,
                    "error": result.error,
                }
            )
            fh.flush()
            if result.status == "submitted":
                submitted += 1
            elif result.status == "skipped_archived":
                skipped += 1
            else:
                failed += 1
            log.info(
                "[%d/%d] %s — %s",
                i,
                len(pending),
                result.status,
                url[-80:],
            )
            time.sleep(delay)

    return submitted, skipped, failed


def _process_one(
    client: httpx.Client,
    url: str,
    auth: tuple[str, str],
    *,
    skip_recent: bool,
) -> SubmissionResult:
    if skip_recent:
        ts = check_recent_capture(client, url)
        if ts:
            return SubmissionResult(
                url=url, status="skipped_archived", wayback_timestamp=ts
            )

    try:
        resp = submit_to_spn2(client, url, auth)
    except Exception as exc:  # noqa: BLE001
        return SubmissionResult(url=url, status="error", error=repr(exc)[:200])

    job_id = str(resp.get("job_id") or "")
    if not job_id:
        return SubmissionResult(
            url=url,
            status="error",
            error=f"no job_id in response: {resp!r}"[:200],
        )
    return SubmissionResult(url=url, status="submitted", job_id=job_id)
