"""Pull captured sitemap.xml files from the Wayback Machine, parse URLs."""

from __future__ import annotations

import csv
import logging
import re
import xml.etree.ElementTree as ET
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

from fakethirtyeight.http import make_client
from fakethirtyeight.paths import INDEX_FILE, SHARDS_DIR, ensure_dirs

log = logging.getLogger(__name__)

WAYBACK_RAW = "https://web.archive.org/web/{timestamp}id_/{url}"
SITEMAP_PATH_RE = re.compile(r"sitemap.*\.xml(\.gz)?$", re.IGNORECASE)
NAMESPACES = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

SITEMAP_SHARD_FIELDS = ("url", "source_sitemap_url", "source_sitemap_timestamp")


@dataclass(slots=True, frozen=True)
class SitemapTarget:
    """A sitemap URL we want to fetch from the Wayback Machine."""

    original_url: str
    timestamp: str


def find_targets(index_path: Path = INDEX_FILE) -> list[SitemapTarget]:
    """Find sitemap URLs in the merged index."""
    if not index_path.exists():
        msg = f"index file not found: {index_path}. Run `merge` first."
        raise FileNotFoundError(msg)

    targets: dict[str, SitemapTarget] = {}
    with index_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            url = row.get("url") or ""
            if not _looks_like_sitemap(url, row.get("latest_mimetype", "")):
                continue
            status = row.get("latest_status") or ""
            if status and not status.startswith("2") and status != "-":
                continue
            ts = row.get("last_seen_ts") or row.get("first_seen_ts") or ""
            if not ts:
                continue
            # Prefer the newest seen timestamp.
            existing = targets.get(url)
            if existing is None or ts > existing.timestamp:
                targets[url] = SitemapTarget(original_url=url, timestamp=ts)
    return list(targets.values())


def _looks_like_sitemap(url: str, mimetype: str) -> bool:
    if SITEMAP_PATH_RE.search(url):
        return True
    if "xml" in mimetype.lower() and "sitemap" in url.lower():
        return True
    return False


def enrich(
    *,
    workers: int = 4,
    delay: float = 1.0,
    host: str = "fivethirtyeight.com",
) -> int:
    """Fetch sitemap snapshots, extract URLs, write to a sitemap shard CSV.

    Returns the number of URLs discovered.
    """
    ensure_dirs()
    targets = find_targets()
    if not targets:
        log.warning("no sitemap targets found in index")
        return 0

    log.info("found %d sitemap targets", len(targets))

    out_path = SHARDS_DIR / f"sitemap-{host}.csv"
    write_header = not out_path.exists()

    with (
        make_client() as client,
        out_path.open("a", newline="", encoding="utf-8") as fh,
    ):
        writer = csv.writer(fh)
        if write_header:
            writer.writerow(SITEMAP_SHARD_FIELDS)

        def _process(target: SitemapTarget) -> list[tuple[str, str, str]]:
            try:
                return _fetch_and_parse(client, target, delay=delay)
            except Exception:
                log.exception("sitemap fetch failed: %s", target.original_url)
                return []

        all_results = thread_map(
            _process,
            targets,
            max_workers=workers,
            desc="sitemaps",
            unit="sitemap",
        )

        count = 0
        seen: set[str] = set()
        for batch in all_results:
            for url, src_url, src_ts in batch:
                if url in seen:
                    continue
                seen.add(url)
                writer.writerow([url, src_url, src_ts])
                count += 1
        fh.flush()

    log.info("wrote %d URLs to %s", count, out_path)
    return count


@retry(
    retry=retry_if_exception_type(httpx.HTTPError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    reraise=True,
)
def _fetch(client: httpx.Client, url: str) -> bytes:
    resp = client.get(url)
    if resp.status_code in {429, 500, 502, 503, 504}:
        raise httpx.HTTPStatusError("retryable", request=resp.request, response=resp)
    resp.raise_for_status()
    return resp.content


def _fetch_and_parse(
    client: httpx.Client,
    target: SitemapTarget,
    *,
    delay: float,
    _depth: int = 0,
) -> list[tuple[str, str, str]]:
    """Fetch one sitemap snapshot from Wayback, parse URLs.

    Follows sitemap-index files one level deep.
    """
    if _depth > 3:
        log.warning("sitemap recursion too deep at %s", target.original_url)
        return []

    wayback_url = WAYBACK_RAW.format(
        timestamp=target.timestamp, url=target.original_url
    )
    body = _fetch(client, wayback_url)
    if delay:
        import time

        time.sleep(delay)

    try:
        root = ET.fromstring(body)  # noqa: S314 — Wayback content, trusted enough
    except ET.ParseError:
        log.warning("could not parse sitemap XML at %s", target.original_url)
        return []

    tag = _localname(root.tag)
    results: list[tuple[str, str, str]] = []

    if tag == "urlset":
        for loc in root.findall(".//sm:url/sm:loc", NAMESPACES) or root.findall(
            ".//loc"
        ):
            url = (loc.text or "").strip()
            if url:
                results.append((url, target.original_url, target.timestamp))

    elif tag == "sitemapindex":
        children: list[SitemapTarget] = []
        for loc in root.findall(".//sm:sitemap/sm:loc", NAMESPACES) or root.findall(
            ".//loc"
        ):
            url = (loc.text or "").strip()
            if url:
                children.append(
                    SitemapTarget(original_url=url, timestamp=target.timestamp)
                )
        for child in children:
            results.extend(
                _fetch_and_parse(client, child, delay=delay, _depth=_depth + 1)
            )

    return results


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def iter_sitemap_rows(host: str = "fivethirtyeight.com") -> Iterable[dict[str, str]]:
    path = SHARDS_DIR / f"sitemap-{host}.csv"
    if not path.exists():
        return
    with path.open(newline="", encoding="utf-8") as fh:
        yield from csv.DictReader(fh)
