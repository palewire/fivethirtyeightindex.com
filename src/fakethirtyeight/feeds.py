"""Walk archived Atom/RSS feed snapshots to recover post URLs + metadata.

Workaround for CDX endpoints that refuse unauthenticated prefix queries
(notably ``*.nytimes.com``, which blocks the FiveThirtyEight NYT-era
content at ``fivethirtyeight.blogs.nytimes.com``). The Wayback Machine
captured the blog's RSS feed thousands of times during its 2010-2014
active life, and each snapshot lists the most recent ~25 posts with
title, byline, link, and publication date. By walking the timemap and
parsing each capture we get the whole post inventory plus enrichment
in one pass.

Output: a sitemap-style shard CSV (so ``merge`` ingests it the same way
as captured sitemap.xml URLs) plus a parallel ``feed-<host>.csv`` file
that pre-stages the title/byline/date enrichment.
"""

from __future__ import annotations

import csv
import logging
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tqdm import tqdm

from fakethirtyeight.http import make_client
from fakethirtyeight.paths import DATA_DIR, SHARDS_DIR, ensure_dirs

log = logging.getLogger(__name__)

WAYBACK_RAW = "https://web.archive.org/web/{timestamp}id_/{url}"
TIMEMAP_URL = "https://web.archive.org/web/timemap/link/{url}"

SITEMAP_SHARD_FIELDS = ("url", "source_sitemap_url", "source_sitemap_timestamp")
FEED_ENRICH_FIELDS = (
    "url",
    "title",
    "byline",
    "published_at",
    "source_feed_url",
    "source_feed_timestamp",
)

#: Wayback memento "datetime" strings look like:
#: "Wed, 30 May 2012 10:00:04 GMT".
_MEMENTO_TS_RE = re.compile(
    r'<(?P<wayback_url>[^>]+)>;\s*rel="(?P<rel>[^"]+)"'
    r'(?:;\s*datetime="(?P<datetime>[^"]+)")?'
)
#: Pull the 14-digit timestamp out of the wayback URL itself, which is the
#: format the rest of the pipeline uses.
_WAYBACK_TS_RE = re.compile(r"/web/(\d{14})")


@dataclass(slots=True, frozen=True)
class FeedMemento:
    feed_url: str
    timestamp: str  # YYYYMMDDHHMMSS

    @property
    def wayback_url(self) -> str:
        return WAYBACK_RAW.format(timestamp=self.timestamp, url=self.feed_url)


@dataclass(slots=True, frozen=True)
class FeedEntry:
    url: str
    title: str
    byline: str
    published_at: str  # ISO-8601


def walk(
    feed_url: str,
    *,
    host: str | None = None,
    workers: int = 4,
    delay: float = 0.5,
    sample_every_days: int | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
) -> tuple[int, int]:
    """Walk a feed's Wayback timemap, extract entries, write shard + enrich CSVs.

    ``sample_every_days`` thins out the memento list by keeping only one
    capture per N-day bucket (helps when there are thousands of mementos
    most of which are redundant). ``start_year`` / ``end_year`` filter by
    capture year. ``host`` defaults to the feed URL's hostname.

    Returns (mementos_fetched, unique_post_urls).
    """
    ensure_dirs()

    host = host or urlsplit(feed_url).hostname or ""
    if not host:
        msg = f"could not derive host from feed_url: {feed_url}"
        raise ValueError(msg)

    sitemap_out = SHARDS_DIR / f"sitemap-{host}.csv"
    enrich_out = DATA_DIR / f"feed-{host}.csv"

    with make_client() as client:
        mementos = list(
            _filter_mementos(
                _list_mementos(client, feed_url),
                sample_every_days=sample_every_days,
                start_year=start_year,
                end_year=end_year,
            )
        )
        log.info("found %d mementos to fetch for %s", len(mementos), feed_url)

        entries_by_url: dict[str, FeedEntry] = {}
        source_per_url: dict[str, tuple[str, str]] = {}

        for memento in tqdm(mementos, desc="feed mementos", unit="memento"):
            try:
                body = _fetch(client, memento.wayback_url)
            except Exception:
                log.exception("memento fetch failed: %s", memento.wayback_url)
                continue
            if delay:
                time.sleep(delay)

            for entry in _parse_feed(body):
                if entry.url in entries_by_url:
                    # Keep the richer record (more non-empty fields).
                    existing = entries_by_url[entry.url]
                    if _entry_score(entry) <= _entry_score(existing):
                        continue
                entries_by_url[entry.url] = entry
                source_per_url[entry.url] = (memento.feed_url, memento.timestamp)

    if not entries_by_url:
        log.warning("no entries extracted from any memento")
        return (len(mementos), 0)

    # Sitemap shard: just the URL list. Append-friendly so merge can keep
    # picking it up alongside other shards.
    write_header = not sitemap_out.exists()
    with sitemap_out.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if write_header:
            writer.writerow(SITEMAP_SHARD_FIELDS)
        for url, (src_url, src_ts) in source_per_url.items():
            writer.writerow([url, src_url, src_ts])

    # Pre-enriched CSV: title/byline/date keyed by URL.
    with enrich_out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(FEED_ENRICH_FIELDS)
        for url, entry in entries_by_url.items():
            src_url, src_ts = source_per_url[url]
            writer.writerow(
                [
                    entry.url,
                    entry.title,
                    entry.byline,
                    entry.published_at,
                    src_url,
                    src_ts,
                ]
            )

    log.info("wrote %d urls to %s and %s", len(entries_by_url), sitemap_out, enrich_out)
    return (len(mementos), len(entries_by_url))


def _entry_score(entry: FeedEntry) -> int:
    return (
        (1 if entry.title else 0)
        + (1 if entry.byline else 0)
        + (1 if entry.published_at else 0)
    )


@retry(
    retry=retry_if_exception_type(httpx.HTTPError),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    reraise=True,
)
def _fetch_timemap(client: httpx.Client, timemap_url: str) -> str:
    """Fetch a Wayback timemap with tenacity-backed retries.

    Wayback's edge intermittently drops TLS connections mid-handshake
    when it's under load; tenacity around the get() call rides through
    transient `SSL: UNEXPECTED_EOF_WHILE_READING` and 5xx blips.
    """
    resp = client.get(timemap_url, timeout=60.0)
    if resp.status_code in {429, 500, 502, 503, 504}:
        raise httpx.HTTPStatusError("retryable", request=resp.request, response=resp)
    resp.raise_for_status()
    return resp.text


def _list_mementos(client: httpx.Client, feed_url: str) -> Iterator[FeedMemento]:
    """Yield every memento Wayback knows about for ``feed_url``."""
    timemap = TIMEMAP_URL.format(url=feed_url)
    body = _fetch_timemap(client, timemap)
    for line in body.splitlines():
        m = _MEMENTO_TS_RE.search(line)
        if not m:
            continue
        rel = m.group("rel") or ""
        if "memento" not in rel:
            continue
        ts_match = _WAYBACK_TS_RE.search(m.group("wayback_url"))
        if not ts_match:
            continue
        yield FeedMemento(feed_url=feed_url, timestamp=ts_match.group(1))


def _filter_mementos(
    mementos: Iterable[FeedMemento],
    *,
    sample_every_days: int | None,
    start_year: int | None,
    end_year: int | None,
) -> Iterator[FeedMemento]:
    """Apply year-range filter + optional per-day sampling."""
    last_day: str | None = None
    sample_window = (
        sample_every_days if sample_every_days and sample_every_days > 0 else None
    )
    for memento in mementos:
        year = int(memento.timestamp[:4])
        if start_year is not None and year < start_year:
            continue
        if end_year is not None and year > end_year:
            continue
        if sample_window is not None:
            day_bucket = memento.timestamp[:8]  # YYYYMMDD
            # Skip when same N-day bucket as last yielded.
            if (
                last_day is not None
                and _days_between(last_day, day_bucket) < sample_window
            ):
                continue
            last_day = day_bucket
        yield memento


def _days_between(a: str, b: str) -> int:
    """Approximate day diff between two YYYYMMDD strings (b > a)."""
    from datetime import datetime

    fmt = "%Y%m%d"
    return (datetime.strptime(b, fmt) - datetime.strptime(a, fmt)).days


@retry(
    retry=retry_if_exception_type(httpx.HTTPError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    reraise=True,
)
def _fetch(client: httpx.Client, url: str) -> bytes:
    resp = client.get(url, timeout=60.0)
    if resp.status_code in {429, 500, 502, 503, 504}:
        raise httpx.HTTPStatusError("retryable", request=resp.request, response=resp)
    resp.raise_for_status()
    return resp.content


_BY_PREFIX = re.compile(r"^by\s+", re.IGNORECASE)


#: Megaphone publishes mp3s through a podtrac → pscrb.fm → traffic.megaphone
#: redirect chain. Pull the canonical episode ID out of any layer so two
#: captures of the same episode (even via different redirect intermediaries)
#: collapse to one row.
_MEGAPHONE_EP = re.compile(r"(ESP\d+)", re.IGNORECASE)


def _canonical_enclosure(url: str) -> str:
    """Strip tracking-redirect wrappers from a podcast enclosure URL."""
    m = _MEGAPHONE_EP.search(url)
    if m:
        return f"https://traffic.megaphone.fm/{m.group(1).upper()}.mp3"
    # Fall back to the bare URL minus query params.
    return url.split("?", 1)[0]


def _parse_feed(body: bytes) -> list[FeedEntry]:
    """Parse RSS 2.0, Atom, or podcast XML; return entries with metadata.

    Podcast feeds (Megaphone, Libsyn, Art19) typically omit per-item ``<link>``
    and put the audio URL on ``<enclosure url=...>``. When the link is missing
    but an enclosure is present, the enclosure URL stands in as the entry's
    canonical URL after redirect-stripping.

    Tolerates parse errors and unknown formats by returning an empty list.
    """
    try:
        root = ET.fromstring(body)  # noqa: S314 — Wayback content
    except ET.ParseError:
        return []

    entries: list[FeedEntry] = []
    # RSS 2.0: <rss><channel><item>...; Atom: <feed><entry>...
    for item in root.iter():
        local = _localname(item.tag)
        if local not in {"item", "entry"}:
            continue
        url = ""
        enclosure_url = ""
        title = ""
        byline = ""
        itunes_author = ""
        pub = ""
        for child in item:
            tag = _localname(child.tag)
            text = (child.text or "").strip()
            if tag == "link":
                # RSS link is text; Atom link is href attribute.
                href = child.get("href")
                url = url or (href if isinstance(href, str) and href else text)
            elif tag == "enclosure":
                enclosure_url = enclosure_url or (child.get("url") or "")
            elif tag == "title" and not title:
                title = text
            elif tag == "author" and "itunes" in (child.tag or "").lower():
                itunes_author = itunes_author or text
            elif tag in {"creator", "author"} and not byline:
                byline = _BY_PREFIX.sub("", text).strip()
            elif tag in {"pubDate", "published", "updated"} and not pub:
                pub = _normalize_pub(text)
        # Podcast fallback: no <link>, but an audio enclosure is present.
        if not url and enclosure_url:
            url = _canonical_enclosure(enclosure_url)
        # Prefer the explicit dc:creator byline; otherwise fall back to
        # itunes:author (which on Megaphone feeds is the show-level credit
        # string, but still useful).
        if not byline and itunes_author:
            byline = _BY_PREFIX.sub("", itunes_author).strip()
        if url and (title or pub):
            entries.append(
                FeedEntry(
                    url=_strip_feedburner(url),
                    title=title,
                    byline=byline,
                    published_at=pub,
                )
            )
    return entries


def _localname(tag: str) -> str:
    """Drop the XML namespace prefix from a tag name."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


_FEEDBURNER_QS = re.compile(
    r"\?utm_[^#]*|\?(?:source|medium|campaign)=[^#]*", re.IGNORECASE
)


def _strip_feedburner(url: str) -> str:
    """Remove feedburner / UTM tracking gunk that the NYT feeds appended."""
    return re.sub(r"\?utm_[^#]+", "", url)


def _normalize_pub(raw: str) -> str:
    """Best-effort RFC822 → ISO-8601. Falls back to the raw string."""
    if not raw:
        return ""
    from datetime import datetime

    # Common RFC822 forms used by RSS pubDate, e.g. "Wed, 30 May 2012 10:00:04 +0000".
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
    ):
        try:
            return datetime.strptime(raw, fmt).isoformat()
        except ValueError:
            continue
    return raw
