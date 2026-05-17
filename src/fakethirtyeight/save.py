"""Submit URLs to the Wayback Machine via Save Page Now (SPN2).

Authenticated SPN2 calls (using the IA S3 keys in ``IA_ACCESS_KEY`` /
``IA_SECRET_KEY``) get higher rate limits than the public endpoint and
return a job_id we can poll for capture status. Polling is optional —
this module only logs the submission; the capture itself runs
asynchronously on archive.org.

Designed for asset URLs that the regular Wayback crawl wouldn't have
hit on its own, e.g. the Megaphone-hosted podcast mp3s referenced by
our feed walker. Most archive.org infrastructure already captures HTML
pages; binaries like mp3s usually need explicit submission.
"""

from __future__ import annotations

import csv
import logging
import os
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Iterator

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tqdm import tqdm

from fakethirtyeight.http import make_client

log = logging.getLogger(__name__)

SPN_URL = "https://web.archive.org/save/"


def _auth_header() -> dict[str, str]:
    access = os.environ.get("IA_ACCESS_KEY")
    secret = os.environ.get("IA_SECRET_KEY")
    if not (access and secret):
        msg = (
            "Save Page Now needs IA_ACCESS_KEY and IA_SECRET_KEY in the "
            "environment. Generate them at https://archive.org/account/s3.php"
        )
        raise RuntimeError(msg)
    return {"Authorization": f"LOW {access}:{secret}"}


@retry(
    retry=retry_if_exception_type(httpx.HTTPError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    reraise=True,
)
def _submit(client: httpx.Client, url: str) -> dict[str, str]:
    """POST one URL to Save Page Now. Returns the JSON response."""
    resp = client.post(
        SPN_URL,
        data={
            "url": url,
            # Capture the whole redirect chain so dcs-spotify.megaphone.fm
            # signed-URL redirects from traffic.megaphone.fm get caught.
            "capture_all": "1",
            # Skip if a snapshot already exists from the last ~24h. Without
            # this, every submission would re-capture even fresh ones.
            "skip_first_archive": "1",
            "if_not_archived_within": "30d",
        },
        timeout=60.0,
    )
    if resp.status_code in {429, 500, 502, 503, 504}:
        raise httpx.HTTPStatusError(
            "retryable", request=resp.request, response=resp
        )
    resp.raise_for_status()
    return resp.json()


def submit_urls(urls: Iterable[str], *, delay: float = 5.0) -> tuple[int, int]:
    """Submit each URL to SPN2. Returns (submitted, errored).

    ``delay`` is the polite wait between submissions. SPN2's
    authenticated rate limit is roughly 12-15 captures/minute, so a
    5-second delay leaves margin.
    """
    headers = {**_auth_header(), "Accept": "application/json"}
    submitted = 0
    errored = 0
    with make_client() as client:
        # Layer in the auth + accept headers per-request (make_client may
        # already include the IA auth header, but Accept needs to be set).
        client.headers.update(headers)
        for url in tqdm(list(urls), desc="save-to-wayback", unit="url"):
            try:
                body = _submit(client, url)
            except Exception as exc:  # noqa: BLE001
                log.warning("SPN submit failed for %s: %s", url, exc)
                errored += 1
                time.sleep(delay)
                continue
            job_id = body.get("job_id", "")
            if not job_id:
                log.warning("SPN response missing job_id for %s: %s", url, body)
                errored += 1
            else:
                log.info("queued %s → %s", url, job_id)
                submitted += 1
            time.sleep(delay)
    return submitted, errored


def urls_from_feed_csv(path: Path) -> Iterator[str]:
    """Yield the ``url`` column from a ``data/feed-*.csv``."""
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            url = row.get("url") or ""
            if url:
                yield url
