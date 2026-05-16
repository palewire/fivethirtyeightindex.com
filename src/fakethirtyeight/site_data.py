"""Build the static-site data file the SvelteKit frontend consumes.

Joins ``data/curated.csv`` with ``data/enriched.csv`` and emits
``web/static/data/articles.json`` containing one record per editorial entry
with the bare-minimum fields the frontend needs:

- ``id``        — rollup_key
- ``title``     — extracted headline
- ``byline``    — display byline as captured
- ``authors``   — byline split into individual names for browse-by-author
- ``year``      — integer year derived from published_at
- ``date``      — published_at (ISO-8601 or YYYY-MM)
- ``kind``      — article/liveblog/project/podcast/video/methodology
- ``url``       — wayback_url to link off to

This is a build artifact, not source data. Regenerate whenever enrichment
finishes or new entries land.
"""

from __future__ import annotations

import csv
import json
import logging
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from fakethirtyeight.curate import CURATED_FILE
from fakethirtyeight.enrich import ENRICHED_FILE

log = logging.getLogger(__name__)

SITE_DATA_FILE = Path("web/static/data/articles.json")

# Capture "Nate Silver and Harry Enten" or "A, B, and C" or "A, B" forms.
_BYLINE_SPLIT = re.compile(r"\s*(?:,\s*and\s+|,\s*|\s+and\s+)\s*", re.IGNORECASE)


@dataclass(slots=True)
class SiteRecord:
    id: str
    title: str
    byline: str
    authors: list[str]
    year: int | None
    date: str
    kind: str
    url: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "byline": self.byline,
            "authors": self.authors,
            "year": self.year,
            "date": self.date,
            "kind": self.kind,
            "url": self.url,
        }


def build(
    *,
    curated_path: Path = CURATED_FILE,
    enriched_path: Path = ENRICHED_FILE,
    out_path: Path = SITE_DATA_FILE,
) -> int:
    """Build the site JSON. Returns the number of records written."""
    if not curated_path.exists():
        msg = f"curated file not found: {curated_path}. Run `curate` first."
        raise FileNotFoundError(msg)
    if not enriched_path.exists():
        msg = f"enriched file not found: {enriched_path}. Run `enrich` first."
        raise FileNotFoundError(msg)

    enriched_by_id = _load_enriched(enriched_path)
    records: list[SiteRecord] = []

    with curated_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rid = row.get("rollup_key") or ""
            if not rid:
                continue
            enrich = enriched_by_id.get(rid)
            record = _build_record(row, enrich)
            if record is None:
                continue
            records.append(record)

    # Sort: newest first, then alphabetical title for stability.
    records.sort(key=lambda r: (r.date or "", r.title), reverse=True)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump([r.to_dict() for r in records], fh, ensure_ascii=False)

    log.info("wrote %d records to %s", len(records), out_path)
    return len(records)


def _load_enriched(path: Path) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rid = row.get("rollup_key") or ""
            if rid:
                out[rid] = row
    return out


def _build_record(
    curated_row: dict[str, str], enrich_row: dict[str, str] | None
) -> SiteRecord | None:
    rid = curated_row.get("rollup_key") or ""
    kind = curated_row.get("kind") or ""
    url = curated_row.get("url") or ""

    if enrich_row:
        title = enrich_row.get("title") or ""
        byline = enrich_row.get("byline") or ""
        date = enrich_row.get("published_at") or ""
        wayback_url = enrich_row.get("wayback_url") or ""
    else:
        title = ""
        byline = ""
        date = curated_row.get("last_seen_ts") or curated_row.get("first_seen_ts") or ""
        wayback_url = _build_wayback_url(
            curated_row.get("last_seen_ts") or curated_row.get("first_seen_ts") or "",
            url,
        )

    # Fall back to a slug-derived title when we couldn't extract one.
    if not title:
        title = _title_from_url(url) or "(untitled)"

    authors = _split_authors(byline)
    year = _year_from_date(date)

    final_url = wayback_url or url
    if not final_url:
        return None

    return SiteRecord(
        id=rid,
        title=title,
        byline=byline,
        authors=authors,
        year=year,
        date=date,
        kind=kind,
        url=final_url,
    )


def _split_authors(byline: str) -> list[str]:
    """Split a display byline into individual author names.

    Drops the staff byline ``FiveThirtyEight`` (used for liveblogs) so each
    individual liveblog doesn't pretend to be by an "author" of that name.
    Callers can still display ``byline`` verbatim if desired.
    """
    if not byline.strip():
        return []
    parts = _BYLINE_SPLIT.split(byline.strip())
    out: list[str] = []
    seen: set[str] = set()
    for raw in parts:
        name = raw.strip()
        if not name or name.lower() == "fivethirtyeight":
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def _year_from_date(date: str) -> int | None:
    if not date or len(date) < 4:
        return None
    head = date[:4]
    if head.isdigit():
        return int(head)
    return None


def _title_from_url(url: str) -> str:
    """Last-resort title: the URL's last meaningful path segment, prettified."""
    if not url:
        return ""
    from urllib.parse import urlsplit

    path = urlsplit(url).path or ""
    segs = [s for s in path.split("/") if s]
    if not segs:
        return ""
    slug = segs[-1].removesuffix(".html").removesuffix(".htm")
    slug = slug.replace("-", " ").replace("_", " ")
    return " ".join(w.capitalize() for w in slug.split())


def _build_wayback_url(timestamp: str, url: str) -> str:
    if not timestamp or not url:
        return ""
    return f"https://web.archive.org/web/{timestamp}/{url}"


def slugify(text: str) -> str:
    """Stable, URL-safe slug used for byline page paths."""
    if not text:
        return ""
    norm = unicodedata.normalize("NFKD", text)
    norm = norm.encode("ascii", "ignore").decode("ascii")
    norm = re.sub(r"[^a-z0-9]+", "-", norm.lower()).strip("-")
    return norm


def iter_byline_slugs(records: Iterable[SiteRecord]) -> dict[str, list[str]]:
    """Map slug → list of record ids for byline routing."""
    out: dict[str, list[str]] = {}
    for r in records:
        for name in r.authors:
            out.setdefault(slugify(name), []).append(r.id)
    return out
