"""Thin client for the Wayback Machine CDX Server API.

https://github.com/internetarchive/wayback/tree/master/wayback-cdx-server-webapp
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from fakethirtyeight.http import DEFAULT_HEADERS, DEFAULT_TIMEOUT, make_client

CDX_URL = "https://web.archive.org/cdx/search/cdx"
DEFAULT_FIELDS = (
    "urlkey",
    "timestamp",
    "original",
    "mimetype",
    "statuscode",
    "digest",
    "length",
)

__all__ = ["DEFAULT_HEADERS", "DEFAULT_TIMEOUT", "CdxClient", "CdxPage", "CdxRow"]

log = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class CdxRow:
    urlkey: str
    timestamp: str
    original: str
    mimetype: str
    statuscode: str
    digest: str
    length: str


class CdxClient:
    """Paginated CDX client. Polite by default."""

    def __init__(
        self,
        *,
        delay: float = 1.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.delay = delay
        self._owns_client = client is None
        self._client = client or make_client()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> CdxClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, json.JSONDecodeError)),
        stop=stop_after_attempt(6),
        wait=wait_exponential(multiplier=2, min=2, max=120),
        reraise=True,
    )
    def _fetch_page(self, params: dict[str, str]) -> tuple[list[list[str]], str | None]:
        """Fetch one CDX page; retry transparently on HTTP and JSON errors.

        Large year responses (15+ MB) occasionally truncate mid-stream behind
        flaky transit/proxies and surface as a JSONDecodeError. Treating that
        as retryable lets a fresh request heal the shard.
        """
        resp = self._client.get(CDX_URL, params=params)
        if resp.status_code in {429, 500, 502, 503, 504}:
            log.warning("CDX %s on %s — retrying", resp.status_code, params)
            raise httpx.HTTPStatusError(
                "retryable status", request=resp.request, response=resp
            )
        resp.raise_for_status()
        try:
            return _parse_page(resp.text)
        except json.JSONDecodeError:
            log.warning("CDX JSON parse failed on %d bytes — retrying", len(resp.text))
            raise

    def iter_pages(
        self,
        url: str,
        *,
        match_type: str = "domain",
        year: int | None = None,
        collapse: str | None = "urlkey",
        resume_key: str | None = None,
        limit: int | None = None,
    ) -> Iterator[CdxPage]:
        """Yield one CdxPage per CDX request.

        Each page carries the rows it received and the resume key needed to
        fetch the next page (or None when the stream is exhausted). Persist
        the resume key after writing a page's rows to make the crawl resumable.
        """

        base_params: dict[str, str] = {
            "url": url,
            "matchType": match_type,
            "output": "json",
            "fl": ",".join(DEFAULT_FIELDS),
            "showResumeKey": "true",
        }
        if collapse:
            base_params["collapse"] = collapse
        if year is not None:
            base_params["from"] = f"{year}0101000000"
            base_params["to"] = f"{year}1231235959"
        if limit is not None:
            base_params["limit"] = str(limit)

        next_key = resume_key
        while True:
            params = dict(base_params)
            if next_key:
                params["resumeKey"] = next_key

            raw_rows, next_key = self._fetch_page(params)
            rows = [_row_from_list(r) for r in raw_rows]
            log.info("CDX page: %d rows, next_key=%r", len(rows), next_key)

            yield CdxPage(rows=rows, next_resume_key=next_key)

            if not next_key or not rows:
                return
            if self.delay:
                time.sleep(self.delay)


@dataclass(slots=True, frozen=True)
class CdxPage:
    rows: list[CdxRow]
    next_resume_key: str | None


def _parse_page(text: str) -> tuple[list[list[str]], str | None]:
    """Parse the JSON response. First row is the header.

    The Wayback CDX API in JSON mode emits a resume key as a final element,
    separated from the data by a blank line. The exact framing has varied
    over time, so we try a few shapes.
    """
    text = text.strip()
    if not text:
        return [], None

    # Format 1: a normal JSON array with a header row, then optionally a
    # final ["resumeKey"] entry.
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Format 2: data array + blank line + resume key as plain text.
        chunks = text.split("\n\n", 1)
        data = json.loads(chunks[0])
        resume = chunks[1].strip() if len(chunks) > 1 else None
        return _strip_header(data), resume or None

    if not parsed:
        return [], None

    # Detect a trailing resume key row: a 1-element list whose value is a
    # non-empty, non-numeric string and that doesn't look like a header.
    resume: str | None = None
    if (
        len(parsed) >= 2
        and isinstance(parsed[-1], list)
        and len(parsed[-1]) == 1
        and isinstance(parsed[-1][0], str)
    ):
        resume = parsed[-1][0]
        parsed = parsed[:-1]

    return _strip_header(parsed), resume


def _strip_header(rows: list[list[str]]) -> list[list[str]]:
    if not rows:
        return []
    first = rows[0]
    if first and first[0] == "urlkey":
        return rows[1:]
    return rows


def _row_from_list(values: list[str]) -> CdxRow:
    # Pad short rows defensively.
    padded = list(values) + [""] * (len(DEFAULT_FIELDS) - len(values))
    return CdxRow(
        urlkey=padded[0],
        timestamp=padded[1],
        original=padded[2],
        mimetype=padded[3],
        statuscode=padded[4],
        digest=padded[5],
        length=padded[6],
    )
