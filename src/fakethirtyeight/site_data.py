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
from fakethirtyeight.enrich import ENRICHED_FILE, load_enriched
from fakethirtyeight.metadata import _clean_title

log = logging.getLogger(__name__)

SITE_DATA_FILE = Path("web/static/data/articles.json")
SITE_CSV_FILE = Path("web/static/data/articles.csv")
SITE_META_FILE = Path("web/static/data/articles-meta.json")
SITEMAP_FILE = Path("web/static/sitemap.xml")
SITE_BASE_URL = "https://fivethirtyeightindex.com"

# Capture "Nate Silver and Harry Enten", "A, B, and C", "A / B", "A | B".
# Slash and pipe forms appear in network-attribution bylines
# ("ABC News / FiveThirtyEight") and rare multi-credit forms
# ("Trevor Martin | Art by yesyesno").
_BYLINE_SPLIT = re.compile(
    r"\s*(?:,\s*and\s+|,\s*|\s+and\s+|\s*/\s*|\s*\|\s*)\s*",
    re.IGNORECASE,
)

#: Role prefixes that prepend an author's real name. Strip so the author
#: gets credit on their byline page instead of having "Edited by" tacked on.
#: The leading-dash alternative handles the 2008-era Blogspot comment
#: attribution pattern ("-- Nate Silver").
_BYLINE_ROLE_PREFIX = re.compile(
    r"^(?:[-–—]+\s*|(?:edited\s+by|written\s+by|posted\s+by|by)\s+)",
    re.IGNORECASE,
)

#: Canonical-form aliases for misspelled or CMS-handle bylines found in
#: the source data. Comparison is case-insensitive on the key. The mapped
#: value is used verbatim as the display name, so entries with the typo
#: and the canonical spelling merge to one byline page.
_BYLINE_ALIASES: dict[str, str] = {
    "juila wolfe": "Julia Wolfe",
    "laura bronnner": "Laura Bronner",
    "elena mejía": "Elena Mejia",
    "amelia thomson-deveaux": "Amelia Thomson-DeVeaux",
    "meena.ganesan": "Meena Ganesan",
}

#: Names that aren't actual people — staff/network/format attributions.
#: Comparison is case-insensitive.
_NON_PERSON_BYLINES: frozenset[str] = frozenset(
    {
        "fivethirtyeight",
        "fivethirtyeight.com",
        "abc news",
        "abc news live",
        "staff",
        "a fivethirtyeight chat",
        "a fivethirtyeight podcast",
        "a fivethirtyeightchat",
        "rotha052",  # CMS account handle that surfaced as a byline
    }
)


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
    csv_out_path: Path = SITE_CSV_FILE,
    meta_out_path: Path = SITE_META_FILE,
) -> int:
    """Build the site JSON, CSV, and tiny metadata file.

    The metadata file (just ``{"total": N}``) is what the layout loads on
    every page so the full 8 MB articles.json is only fetched when the
    user actually searches.
    """
    if not curated_path.exists():
        msg = f"curated file not found: {curated_path}. Run `curate` first."
        raise FileNotFoundError(msg)
    if not enriched_path.exists():
        msg = f"enriched file not found: {enriched_path}. Run `enrich` first."
        raise FileNotFoundError(msg)

    enriched_by_id = load_enriched(enriched_path)
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

    # Sort: oldest first. This is a retrospective archive — chronological
    # reading order makes more sense than newest-first.
    # Records with no date sort to the end (treat "" as the highest value).
    records.sort(key=lambda r: (r.date or "￿", r.title))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump([r.to_dict() for r in records], fh, ensure_ascii=False)

    # Also write a flat CSV — useful for spreadsheets and one-off analyses.
    csv_out_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["date", "year", "kind", "title", "byline", "authors", "url", "id"]
        )
        for r in records:
            writer.writerow(
                [
                    r.date,
                    r.year if r.year is not None else "",
                    r.kind,
                    r.title,
                    r.byline,
                    "; ".join(r.authors),
                    r.url,
                    r.id,
                ]
            )

    # Tiny metadata file: only the total entry count, loaded by the
    # layout on every page (so the 8 MB articles.json stays opt-in).
    meta_out_path.parent.mkdir(parents=True, exist_ok=True)
    with meta_out_path.open("w", encoding="utf-8") as fh:
        json.dump({"total": len(records)}, fh)

    # Sitemap covers every prerendered route — homepage, byline index,
    # one entry per year, one entry per byline slug.
    _write_sitemap(records)

    log.info("wrote %d records to %s and %s", len(records), out_path, csv_out_path)
    return len(records)


def _build_record(
    curated_row: dict[str, str], enrich_row: dict[str, str] | None
) -> SiteRecord | None:
    rid = curated_row.get("rollup_key") or ""
    kind = curated_row.get("kind") or ""
    url = curated_row.get("url") or ""

    # Always link to the EARLIEST snapshot we know of (curated.first_seen_ts).
    # The article's metadata is identical across captures, but the earliest
    # snapshot is closer to "how the site actually looked then" and avoids
    # linking 2015 articles to 2024 mementos captured during the site's
    # final months. Falls back to last_seen_ts when first is missing.
    earliest_ts = (
        curated_row.get("first_seen_ts") or curated_row.get("last_seen_ts") or ""
    )
    wayback_url = _build_wayback_url(earliest_ts, url) if earliest_ts else ""

    if enrich_row:
        # Re-clean titles to apply any extractor improvements that landed
        # after enrich.csv was written (e.g., Blogspot-era prefix stripping).
        title = _clean_title(enrich_row.get("title") or "")
        byline = enrich_row.get("byline") or ""
        date = enrich_row.get("published_at") or ""
    else:
        title = ""
        byline = ""
        date = earliest_ts

    # Fall back to a slug-derived title when we couldn't extract one.
    if not title:
        title = _title_from_url(url) or "(untitled)"

    authors = _split_authors(byline)
    # Rebuild the display byline from the cleaned author list so role
    # prefixes ("Edited by …") and staff attributions vanish from the JSON
    # and the CSV alike. If no real authors survive the scrub, blank the
    # display byline too.
    byline = _join_authors(authors)
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


def _join_authors(authors: list[str]) -> str:
    """Render a clean display byline from a cleaned author list."""
    if not authors:
        return ""
    if len(authors) == 1:
        return authors[0]
    if len(authors) == 2:
        return f"{authors[0]} and {authors[1]}"
    return ", ".join(authors[:-1]) + f", and {authors[-1]}"


def _split_authors(byline: str) -> list[str]:
    """Split a display byline into individual author names.

    - Strips leading role prefixes ("Edited by ", "By ", etc.) so the
      person gets credit on their byline page.
    - Splits on ``,`` / ``and`` / ``/`` / ``|``.
    - Filters out non-person attributions (FiveThirtyEight, ABC News, …).
    - Normalizes known typo/handle aliases ("Juila Wolfe" → "Julia Wolfe").
    - Deduplicates case-insensitively, preserves order.
    """
    if not byline.strip():
        return []
    cleaned = _BYLINE_ROLE_PREFIX.sub("", byline.strip(), count=1)
    parts = _BYLINE_SPLIT.split(cleaned)
    out: list[str] = []
    seen: set[str] = set()
    for raw in parts:
        name = raw.strip().strip(".,;")
        if not name:
            continue
        if name.isdigit():  # e.g. extractor picked up a year "2017" as the author
            continue
        # Normalize typos / CMS handles to the canonical display form.
        name = _BYLINE_ALIASES.get(name.casefold(), name)
        key = name.casefold()
        if key in _NON_PERSON_BYLINES:
            continue
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
    return re.sub(r"[^a-z0-9]+", "-", norm.lower()).strip("-")


def iter_byline_slugs(records: Iterable[SiteRecord]) -> dict[str, list[str]]:
    """Map slug → list of record ids for byline routing."""
    out: dict[str, list[str]] = {}
    for r in records:
        for name in r.authors:
            out.setdefault(slugify(name), []).append(r.id)
    return out


def _write_sitemap(records: list[SiteRecord], out_path: Path = SITEMAP_FILE) -> None:
    """Emit a flat sitemap.xml listing every prerendered route."""
    years: set[int] = set()
    bylines: set[str] = set()
    for r in records:
        if r.year is not None:
            years.add(r.year)
        for name in r.authors:
            slug = slugify(name)
            if slug:
                bylines.add(slug)

    urls: list[str] = [f"{SITE_BASE_URL}/", f"{SITE_BASE_URL}/byline/"]
    urls.extend(f"{SITE_BASE_URL}/year/{y}/" for y in sorted(years))
    urls.extend(f"{SITE_BASE_URL}/byline/{slug}/" for slug in sorted(bylines))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        fh.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        fh.write('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        for url in urls:
            fh.write(f"  <url><loc>{url}</loc></url>\n")
        fh.write("</urlset>\n")
    log.info("wrote sitemap with %d urls to %s", len(urls), out_path)
