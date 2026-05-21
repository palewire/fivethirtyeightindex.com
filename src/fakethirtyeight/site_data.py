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

from fakethirtyeight.classify import KIND_PODCAST, classify
from fakethirtyeight.curate import CURATED_FILE
from fakethirtyeight.datasets import write_site_datasets
from fakethirtyeight.enrich import ENRICHED_FILE, load_enriched
from fakethirtyeight.metadata import _clean_title
from fakethirtyeight.paths import DATA_DIR

log = logging.getLogger(__name__)

SITE_DATA_FILE = Path("web/static/data/articles.json")
SITE_CSV_FILE = Path("web/static/data/articles.csv")
SITE_META_FILE = Path("web/static/data/articles-meta.json")
SITEMAP_FILE = Path("web/static/sitemap.xml")
SITE_BASE_URL = "https://fivethirtyeightindex.com"
PODCAST_METADATA_FILE = DATA_DIR / "podcast_metadata.csv"
PODCAST_UPLOAD_LOG = DATA_DIR / "podcast_upload_log.csv"
ARCHIVE_ITEM_BASE_URL = "https://archive.org/details"

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

#: Blogspot's Atom-feed author wrapper: ``someone@example.com (Real Name)``.
#: Keep only the parenthesized display name. Pre-dates the extractor's
#: cleanup but slipped into a thousand-plus enriched rows during the
#: Blogspot-era enrich, so the build-time cleanup catches them too.
_BLOGGER_EMAIL_AUTHOR = re.compile(r"^\s*\S+@\S+\s*\(([^)]+)\)\s*$")

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
    # The 2008 Blogspot post-author span ran only the first name on a
    # handful of posts; NYT-era atom feeds also occasionally upper-cased
    # the byline. Normalize both to the canonical display form so the
    # byline-page dedup and title+byline+date collapse work.
    "nate": "Nate Silver",
    "nate silver": "Nate Silver",
    # Some Blogspot-era posts carried "Hale Bonddad Stewart" with a quoted
    # nickname; the modern republish dropped the quotes. Normalize both to
    # the canonical surname-only form so the dedup matches across eras.
    'hale "bonddad" stewart': "Hale Stewart",
    "hale bonddad stewart": "Hale Stewart",
}

#: Names that aren't actual people — staff/network/format attributions.
#: Comparison is case-insensitive.
_NON_PERSON_BYLINES: frozenset[str] = frozenset(
    {
        "fivethirtyeight",
        "fivethirtyeight.com",
        "fivethirtyeight staff",
        "fivethirtyeight podcasts",
        "fivethirtyeight video",
        "abc news",
        "abc news live",
        "espn",
        "gma",
        "good morning america",
        "the new york times",
        "staff",
        "a fivethirtyeight chat",
        "a fivethirtyeight podcast",
        "a fivethirtyeightchat",
        "rotha052",  # CMS account handle that surfaced as a byline
    }
)


#: Strings that the extractor occasionally picks up where a byline would
#: normally be — date stamps, "Updated:" markers, etc. Drop on prefix match
#: so date variants beyond the literal seen ones don't surface.
_NON_PERSON_BYLINE_PREFIXES: tuple[str, ...] = (
    "published ",
    "updated ",
    # Production credits that aren't reporter bylines: "Art by yesyesno",
    # "Photos by Gabriella Demczuk", etc.
    "art by ",
    "design by ",
    "illustration by ",
    "illustrations by ",
    "photos by ",
    "photography by ",
    "video by ",
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
    podcast_item_urls = _load_podcast_item_urls()
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
            if _is_junk_record(record):
                continue
            if record.kind == KIND_PODCAST and record.id in podcast_item_urls:
                record.url = podcast_item_urls[record.id]
            records.append(record)

    records = _dedupe_articles(records)
    _disambiguate_project_drilldown_titles(records)

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
        writer = csv.writer(fh, lineterminator="\n")
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

    # Keep dataset artifacts separate from articles, but refresh their site
    # JSON/CSV when the source inventory exists so sitemap prerendering sees
    # the current dataset route list.
    write_site_datasets()

    # Sitemap covers every prerendered route — homepage, byline index,
    # dataset index, one entry per year, and one entry per byline slug.
    _write_sitemap(records)

    log.info("wrote %d records to %s and %s", len(records), out_path, csv_out_path)
    return len(records)


#: Title prefixes that mean "this row is sandbox/junk content the CMS
#: surfaced by accident." Currently just liveblog drafts saved with the
#: theme placeholder title.
_JUNK_LIVEBLOG_TITLES: frozenset[str] = frozenset({"headline"})

#: Slug suffixes (after the last `/`) that mark a project URL as an
#: embed/promo shim rather than an editorial dashboard. These pages are
#: thin HTML fragments rendered as network embeds elsewhere; they have
#: no standalone reader value and surface with junk titles like
#: "Abc Embed" / "Promo".
_JUNK_PROJECT_SLUG_SUFFIXES: tuple[str, ...] = (
    "abc-embed.html",
    "abc-promo.html",
    "promo.html",
    "abc-embed",
    "abc-promo",
)


def _is_junk_record(record: SiteRecord) -> bool:
    """Drop sandbox/draft content the CMS exposed by accident.

    The "Headline" placeholder is WordPress's default liveblog title — any
    liveblog that still has it never received a real title and is almost
    certainly a test post the CMS admin saved as live. Project-embed
    shim URLs (abc-embed.html, promo.html) are similar — fragments
    rendered as network embeds elsewhere, not standalone editorial pages.
    """
    if record.kind == "liveblog":
        title = (record.title or "").strip().lower()
        if title in _JUNK_LIVEBLOG_TITLES:
            return True
    if record.kind == "project":
        slug = record.id.split(":", 1)[1] if ":" in record.id else record.id
        last_segment = slug.rsplit("/", 1)[-1].lower()
        if last_segment in _JUNK_PROJECT_SLUG_SUFFIXES:
            return True
    return False


#: Slug suffix WordPress added to draft/revision URLs, e.g.
#: `dow-rebounds_19`. The clean sibling is always preferred when present.
_REVISION_SLUG_SUFFIX = re.compile(r"_\d+$")


#: Kinds eligible for cross-publish dedup at the site_data step. Article
#: and video are the same FT segment under two URLs (`/features/<slug>`
#: and `/videos/<slug>`) — the article version wins because it carries
#: the full text plus an embed. Project / methodology / podcast / liveblog
#: stay out: project drilldowns sharing a generic title aren't dupes, and
#: the methodology + article pair (4 cases) covers genuinely different
#: content even when slugs match.
_DEDUPE_KINDS: frozenset[str] = frozenset({"article", "video"})


def _dedupe_articles(records: list[SiteRecord]) -> list[SiteRecord]:
    """Collapse rows that share title+date.

    Same-article slug variants: WordPress draft revisions, truncated slugs
    from the early CMS, and editor typo-fixes that left both URLs live.
    Cross-publish: FiveThirtyEight republished hundreds of segments as
    both /features/<slug> (article) and /videos/<slug> (video); the
    article carries the writeup + embedded player, so it wins. Kinds not
    in :data:`_DEDUPE_KINDS` are passed through unchanged.

    The key intentionally drops byline so a bylineless row collapses
    with its bylined sibling (the enricher occasionally missed the
    author span on one snapshot but not on another for the same post).
    When two rows in a title+date bucket have *different* non-empty
    bylines, they stay separate — different reporters covering the
    same headline on the same day is plausible enough to preserve.
    """
    groups: dict[tuple[str, str], list[SiteRecord]] = {}
    out: list[SiteRecord] = []
    for r in records:
        if r.kind not in _DEDUPE_KINDS or not r.title or not r.date:
            out.append(r)
            continue
        key = (_dedup_title_key(r.title), r.date[:10])
        groups.setdefault(key, []).append(r)
    for group in groups.values():
        if len(group) == 1:
            out.append(group[0])
            continue
        # If every non-empty byline in the bucket agrees (or only one row
        # carries a byline at all), it's the same article — collapse.
        non_empty = {r.byline.strip().lower() for r in group if r.byline.strip()}
        if len(non_empty) <= 1:
            out.append(max(group, key=_canonical_score))
            continue
        # WP-era + modern-features pair with conflicting bylines: the
        # modern republish frequently inherits a backfilled byline that
        # doesn't match the original publish date (e.g. Neil Paine
        # attributed to a 2009 post, even though he joined in 2014).
        # Trust the WP-era byline — it came from the contemporaneous
        # HTML — and keep the higher-canonical-score row otherwise.
        wp_rows = [r for r in group if r.id.startswith("article:wp/")]
        non_wp_rows = [r for r in group if not r.id.startswith("article:wp/")]
        if wp_rows and non_wp_rows:
            wp_survivor = max(wp_rows, key=_canonical_score)
            survivor = max(group, key=_canonical_score)
            out.append(
                SiteRecord(
                    id=survivor.id,
                    title=survivor.title,
                    byline=wp_survivor.byline,
                    authors=list(wp_survivor.authors),
                    year=survivor.year,
                    date=survivor.date,
                    kind=survivor.kind,
                    url=survivor.url,
                )
            )
            continue
        # Multiple distinct authors share title+date and no WP-era hint —
        # keep one best row per byline so we don't conflate unrelated posts.
        by_b: dict[str, list[SiteRecord]] = {}
        for r in group:
            by_b.setdefault(r.byline.strip().lower(), []).append(r)
        for sub in by_b.values():
            out.append(max(sub, key=_canonical_score))
    return out


def _load_podcast_item_urls(
    *,
    metadata_path: Path = PODCAST_METADATA_FILE,
    upload_log_path: Path = PODCAST_UPLOAD_LOG,
) -> dict[str, str]:
    """Map podcast rollup keys to archive.org item URLs."""
    if not metadata_path.exists() or not upload_log_path.exists():
        return {}

    uploaded = _load_uploaded_podcast_identifiers(upload_log_path)
    if not uploaded:
        return {}

    out: dict[str, str] = {}
    with metadata_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            identifier = row.get("identifier") or ""
            if identifier not in uploaded:
                continue
            rollup_key = _podcast_rollup_key(row)
            if not rollup_key:
                continue
            out[rollup_key] = f"{ARCHIVE_ITEM_BASE_URL}/{identifier}"
    return out


def _load_uploaded_podcast_identifiers(upload_log_path: Path) -> set[str]:
    """Return IA identifiers whose upload log has a successful upload row."""
    out: set[str] = set()
    with upload_log_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (row.get("status") or "") != "uploaded":
                continue
            identifier = row.get("identifier") or ""
            if identifier:
                out.add(identifier)
    return out


def _podcast_rollup_key(row: dict[str, str]) -> str:
    """Resolve a podcast metadata row to the site-data rollup key."""
    megaphone_id = (row.get("megaphone_id") or "").upper()
    if megaphone_id:
        return f"podcast:meg/{megaphone_id}"

    c = classify(row.get("mp3_url") or "")
    if c.kind == KIND_PODCAST:
        return c.rollup_key
    return ""


#: Smart-quote / curly-punctuation characters that snuck into titles via
#: different CMS templates. Normalize when computing the dedup key so a
#: curly-apostrophe title matches its straight-quote sibling.
_TITLE_QUOTE_NORMALIZE = str.maketrans(
    {
        "‘": "'",  # left single quote
        "’": "'",  # right single quote
        "“": '"',  # left double quote
        "”": '"',  # right double quote
        "–": "-",  # en dash
        "—": "-",  # em dash
    }
)


def _dedup_title_key(title: str) -> str:
    """Normalize a title for dedup-key purposes.

    Lowercase, strip, and fold smart quotes / dashes down to ASCII so
    typographic variants of the same string collide. The display title
    on the record is left untouched.
    """
    return title.strip().translate(_TITLE_QUOTE_NORMALIZE).lower()


def _disambiguate_project_drilldown_titles(records: list[SiteRecord]) -> None:
    """Append a slug-derived suffix to project drilldown titles that collide.

    Some project dashboards (e.g. congress-trump-score, carmelo) shipped
    hundreds of per-entity drilldown URLs that all carry the same
    page-level ``<title>``: "Tracking Congress In The Age Of Trump" for
    every congressmember, "FiveThirtyEight's CARMELO NBA Projections"
    for every NBA player. The polls drilldowns already disambiguate
    themselves via the snapshot HTML title, so we only append a suffix
    when sibling rows actually share a title.

    Mutates ``records`` in place.
    """
    from collections import Counter

    title_counts: Counter[str] = Counter(
        r.title for r in records if r.kind == "project" and r.title
    )
    for r in records:
        if r.kind != "project" or not r.title:
            continue
        if title_counts[r.title] < 2:
            continue
        suffix = _drilldown_suffix(r.id)
        if suffix and suffix.lower() not in r.title.lower():
            r.title = f"{r.title} — {suffix}"


def _drilldown_suffix(rollup_key: str) -> str:
    """Prettify the sub-path of a project rollup key.

    ``project:congress-trump-score/a-donald-mceachin`` → ``A Donald Mceachin``
    ``project:carmelo/lebron-james``                  → ``Lebron James``
    ``project:2018-midterm-election-forecast/house/al/1`` → ``House Al 1``
    """
    if ":" not in rollup_key:
        return ""
    slug = rollup_key.split(":", 1)[1]
    if "/" not in slug:
        return ""
    # Drop the project root; everything after is the drilldown identity.
    sub = slug.split("/", 1)[1]
    parts = [p.replace("-", " ").strip() for p in sub.split("/") if p]
    return " ".join(p.title() for p in parts if p)


def _canonical_score(record: SiteRecord) -> tuple[int, int, int, int, str]:
    """Sort key for picking the canonical row out of a dedup group.

    Higher tuples win. Priority order:
    1. Article kind beats video (richer text content, /features/ URL).
    2. A non-empty byline beats an empty one (more complete metadata).
    3. Avoid the `_N` WordPress revision suffix.
    4. Prefer the longer slug (a truncated variant of the same article
       loses to its full sibling).
    5. Alphabetical id as a stable tie-break.
    """
    slug = record.id.split(":", 1)[1] if ":" in record.id else record.id
    is_article = 1 if record.kind == "article" else 0
    has_byline = 1 if record.byline.strip() else 0
    not_revision = 0 if _REVISION_SLUG_SUFFIX.search(slug) else 1
    return (is_article, has_byline, not_revision, len(slug), slug)


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
    # Sitemap/feed-source rows don't carry a CDX timestamp through merge,
    # so the curated row's first_seen_ts is empty. Fall back to the
    # snapshot the enricher (or feed walker) recorded — for HTML pages
    # (NYT etc.) Wayback reliably has a snapshot, so wrapping is the
    # right move. Podcast audio is a special case: Wayback rarely caches
    # mp3 bodies, so the wrapped URL would just resolve to a "no snapshot"
    # page even though we minted a fake timestamp from the feed memento.
    # The Megaphone CDN is still serving the audio live, so we keep the
    # bare host URL for podcasts and let users click straight to play.
    if not wayback_url and enrich_row and kind != "podcast":
        wayback_url = _build_wayback_url(
            enrich_row.get("snapshot_timestamp") or "",
            enrich_row.get("url") or url,
        )

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
    year = _year_from_date(date) or _year_from_url(url)

    # Sitemap-only rows we never enriched fall through with no Wayback
    # wrapper at all. Use the no-timestamp Wayback form — the server 302s
    # to the closest snapshot — so every URL on the site routes through
    # archive.org rather than a (likely dead) live origin. Skip for
    # podcasts: Wayback rarely caches mp3 bodies, and the Megaphone CDN
    # is still serving the audio live.
    if not wayback_url and url and kind != "podcast":
        wayback_url = f"https://web.archive.org/web/{url}"
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


def clean_byline(byline: str) -> str:
    """Return a normalized display byline."""
    return _join_authors(_split_authors(byline))


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
    s = byline.strip()
    m = _BLOGGER_EMAIL_AUTHOR.match(s)
    if m:
        s = m.group(1).strip()
    cleaned = _BYLINE_ROLE_PREFIX.sub("", s, count=1)
    parts = _BYLINE_SPLIT.split(cleaned)
    out: list[str] = []
    seen: set[str] = set()
    for raw in parts:
        name = raw.strip().strip(".,;")
        if not name:
            continue
        if name.isdigit():  # e.g. extractor picked up a year "2017" as the author
            continue
        lower = name.lower()
        if any(lower.startswith(p) for p in _NON_PERSON_BYLINE_PREFIXES):
            continue
        # NYT-era atom feeds rendered bylines in all caps (KEVIN QUEALY,
        # MICAH COHEN, etc.). Title-case any all-uppercase multi-word name
        # so the byline-page slug and the dedup key match the normal form.
        if " " in name and name == name.upper():
            name = name.title()
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


#: 538-era plausible publication years. Used to filter spurious 4-digit
#: matches in URLs (e.g. zip codes, sample sizes, ticket IDs).
_URL_YEAR = re.compile(r"(?<!\d)(20[0-2]\d)(?!\d)")


def _year_from_url(url: str) -> int | None:
    """Fallback year derivation for SPA/no-metadata pages.

    Many project URLs encode the cycle year (``/election-2016/``,
    ``/2024-election-forecast/``); use that when we have nothing better.
    Only emit years between 2008 (site launch) and the current decade so
    we don't pick up incidental 4-digit substrings.
    """
    if not url:
        return None
    from urllib.parse import urlsplit

    path = urlsplit(url).path or ""
    matches = _URL_YEAR.findall(path)
    if not matches:
        return None
    # Prefer the *latest* plausible year in the path — projects with
    # multi-cycle slugs like ``/2024-election-forecast/`` should bucket
    # by the active cycle, not by a historical reference.
    candidates = [int(m) for m in matches if 2008 <= int(m) <= 2029]
    if not candidates:
        return None
    return max(candidates)


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
    year_months: set[str] = set()  # "YYYY-MM" buckets with at least one entry
    bylines: set[str] = set()
    for r in records:
        if r.year is not None:
            years.add(r.year)
        if r.date and len(r.date) >= 7 and r.date[4] == "-":
            ym = r.date[:7]
            if ym[:4].isdigit() and ym[5:].isdigit():
                year_months.add(ym)
        for name in r.authors:
            slug = slugify(name)
            if slug:
                bylines.add(slug)

    urls: list[str] = [
        f"{SITE_BASE_URL}/",
        f"{SITE_BASE_URL}/byline/",
        f"{SITE_BASE_URL}/dataset/",
    ]
    urls.extend(f"{SITE_BASE_URL}/year/{y}/" for y in sorted(years))
    urls.extend(
        f"{SITE_BASE_URL}/year/{ym[:4]}/{ym[5:]}/" for ym in sorted(year_months)
    )
    urls.extend(f"{SITE_BASE_URL}/byline/{slug}/" for slug in sorted(bylines))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        fh.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        fh.write('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        for url in urls:
            fh.write(f"  <url><loc>{url}</loc></url>\n")
        fh.write("</urlset>\n")
    log.info("wrote sitemap with %d urls to %s", len(urls), out_path)
