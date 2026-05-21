"""Build per-episode metadata for the bulk-downloaded podcast MP3s.

The output is ``data/podcast_metadata.csv`` — one row per canonical MP3
URL with the fields ``ia upload`` needs (identifier, title, creator,
date, description, source, …).

Tier 1: deterministic from the URL alone (show + date for the ESPN-era
named files; megaphone ID for the rest; show inferred from the player
iframe topic when the filename doesn't give it). No network.

Tier 2: read ID3 tags from the downloaded MP3 file. The publisher's
embedded tags give us episode title (TIT2), description (COMM/USLT),
show name (TALB), and release year/date (TDRC). The player iframe
HTML itself is empty — all the episode-level metadata lives in the
file's tags.
"""

from __future__ import annotations

import csv
import hashlib
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from mutagen.id3 import ID3, ID3NoHeaderError

from fakethirtyeight.download_podcasts import PODCASTS_DIR, filename_for
from fakethirtyeight.paths import DATA_DIR, INDEX_FILE
from fakethirtyeight.save_now import _canonical_audio_url, collect_podcast_mp3s

log = logging.getLogger(__name__)

METADATA_FILE = DATA_DIR / "podcast_metadata.csv"
THUMBNAILS_DIR = DATA_DIR / "podcast_thumbnails"
FEED_DATE_PATTERN = "feed-*.csv"

#: Map a short URL-derived show slug to a canonical show name + a stable
#: collection-internal identifier prefix. Order: the URL slug we'll see
#: → (display name, short slug for IA identifiers).
SHOW_TABLE: dict[str, tuple[str, str]] = {
    "fivethirtyeightelections": ("FiveThirtyEight Elections", "elections"),
    "fivethirtyeightpolitics": ("FiveThirtyEight Politics", "politics"),
    "hottakedown": ("Hot Takedown", "hot-takedown"),
    "podcast19": (
        "FiveThirtyEight: PODCAST-19 (the Coronavirus podcast)",
        "podcast-19",
    ),
    "whatsthepoint": ("What's The Point", "whats-the-point"),
    "modelconversations": ("Model Conversations", "model-conversations"),
    "ratingsfilm": ("Ratings (Film)", "ratings"),
    "thelab": ("The Lab", "the-lab"),
    "gerrymandering": ("The Gerrymandering Project", "gerrymandering"),
}

#: Player URL paths sometimes carry a "topic" that hints at which show.
#: This maps `/player/<topic>/…` topic → show slug used in SHOW_TABLE.
PLAYER_TOPIC_TO_SHOW: dict[str, str] = {
    "politics": "fivethirtyeightpolitics",
    "elections": "fivethirtyeightelections",
    "hottakedown": "hottakedown",
    "hot-takedown": "hottakedown",
    "sports": "hottakedown",
    "podcast19": "podcast19",
    "podcast-19": "podcast19",
    "coronavirus": "podcast19",
    "whatsthepoint": "whatsthepoint",
    "wtp": "whatsthepoint",
    "ht": "hottakedown",
    "the-lab": "thelab",
    "thelab": "thelab",
    "gerrymandering": "gerrymandering",
}

# Named-file pattern, ESPN era:
#   fivethirtyeightpolitics_2017-12-21-052934.128.mp3
_NAMED_FILE = re.compile(
    r"^(?P<show>[a-z][a-z0-9]+?)_(?P<date>\d{4}-\d{2}-\d{2})-"
    r"(?P<time>\d{6})\.(?P<bitrate>\d+k?)\.mp3$",
    re.IGNORECASE,
)

# Megaphone ID pattern: ESP1059043055.mp3
_MEGAPHONE_FILE = re.compile(r"^(?P<id>ESP\d{6,})\.mp3$", re.IGNORECASE)


@dataclass
class PodcastMetadata:
    """One row per canonical MP3 URL."""

    mp3_url: str
    identifier: str = ""  # archive.org item identifier
    title: str = ""
    creator: str = ""
    date: str = ""
    description: str = ""
    show: str = ""  # canonical show name (e.g. "FiveThirtyEight Politics")
    show_slug: str = ""  # short slug (e.g. "politics")
    bitrate: str = ""
    megaphone_id: str = ""
    player_url: str = ""  # the player iframe URL that embedded this MP3
    source_article_url: str = ""  # the article that hosted the player
    source: str = ""  # original MP3 URL (== mp3_url, for IA upload)
    mediatype: str = "audio"  # IA mediatype field
    subject: str = "podcast;FiveThirtyEight"  # IA subject tags, semi-delim
    thumbnail: str = ""  # path to extracted cover art JPG, if any
    extracted_via: list[str] = field(default_factory=list)

    def to_row(self) -> dict[str, str]:
        d = asdict(self)
        d["extracted_via"] = "+".join(self.extracted_via)
        return d


# ---------------------------------------------------------------------------
# Tier 1: URL-derived
# ---------------------------------------------------------------------------


def parse_url(mp3_url: str) -> PodcastMetadata:
    """Pull whatever's deterministically encoded in the URL."""
    md = PodcastMetadata(mp3_url=mp3_url, source=mp3_url)
    parsed = urlparse(mp3_url)
    basename = parsed.path.rsplit("/", 1)[-1]

    m = _NAMED_FILE.match(basename)
    if m:
        show_key = m.group("show").lower()
        md.show_slug = SHOW_TABLE.get(show_key, ("", show_key))[1]
        md.show = SHOW_TABLE.get(show_key, (show_key.replace("-", " ").title(), ""))[0]
        md.date = m.group("date")
        md.bitrate = m.group("bitrate")
        md.extracted_via.append("filename")
        return md

    m = _MEGAPHONE_FILE.match(basename)
    if m:
        md.megaphone_id = m.group("id")
        md.extracted_via.append("megaphone-id")
        # Try the podtrac prefix path for a show hint:
        # /espn-fivethirtyeightpolitics/c.espnradio.com/...
        segs = [s for s in parsed.path.split("/") if s]
        for s in segs:
            if s.startswith("espn-"):
                key = s.removeprefix("espn-").lower()
                # try direct + a tolerance for the long coronavirus slug
                if key in SHOW_TABLE:
                    name, slug = SHOW_TABLE[key]
                    md.show, md.show_slug = name, slug
                    md.extracted_via.append("podtrac-prefix")
                    break
                for k in SHOW_TABLE:
                    if key.startswith(k):
                        name, slug = SHOW_TABLE[k]
                        md.show, md.show_slug = name, slug
                        md.extracted_via.append("podtrac-prefix")
                        break
                break
    return md


def _slugify(*parts: str) -> str:
    s = "-".join(p for p in parts if p)
    s = re.sub(r"[^a-z0-9-]+", "-", s.lower())
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:100] or "podcast"


def build_identifier(md: PodcastMetadata) -> str:
    """Generate an archive.org item identifier.

    Format: ``fivethirtyeight-<show>-<date or id>``. All lowercase,
    hyphenated, [a-z0-9-] only, ≤100 chars.
    """
    parts = ["fivethirtyeight"]
    if md.show_slug:
        parts.append(md.show_slug)
    if md.date:
        parts.append(md.date)
    elif md.megaphone_id:
        parts.append(md.megaphone_id.lower())
    return _slugify(*parts)


def _full_date(raw: str) -> str:
    """Return YYYY-MM-DD from an ISO-ish date string, or ``""``."""
    raw = (raw or "").strip()
    if len(raw) >= 10 and raw[4:5] == "-" and raw[7:8] == "-":
        return raw[:10]
    return ""


def _load_feed_dates(feed_paths: list[Path] | None = None) -> dict[str, str]:
    """Megaphone episode ID → full publish date from feed walker outputs."""
    paths = (
        feed_paths
        if feed_paths is not None
        else sorted(DATA_DIR.glob(FEED_DATE_PATTERN))
    )
    out: dict[str, str] = {}
    for path in paths:
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                date = _full_date(row.get("published_at") or "")
                if not date:
                    continue
                m = _MEGAPHONE_FILE.match(Path(row.get("url") or "").name)
                if not m:
                    continue
                out[m.group("id").upper()] = date
    return out


def _url_suffix(url: str) -> str:
    """Stable short suffix for disambiguating rare identifier collisions."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]


def disambiguate_identifiers(rows: list[PodcastMetadata]) -> None:
    """Ensure archive.org identifiers are unique in-place.

    Most identifiers are naturally unique because they include an episode date
    or Megaphone ID. A few legacy ESPN/Castfire URLs can represent distinct
    rows with the same show/date identifier, so suffix only the colliding rows
    with a stable digest of the source URL.
    """
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.identifier] = counts.get(row.identifier, 0) + 1

    for row in rows:
        if counts.get(row.identifier, 0) <= 1:
            continue
        base = row.identifier[:91].rstrip("-")
        row.identifier = f"{base}-{_url_suffix(row.mp3_url)}"


# ---------------------------------------------------------------------------
# Tier 2: player iframe context — map mp3 → player_url + article URL
# ---------------------------------------------------------------------------


def build_player_index(index_path: Path = INDEX_FILE) -> dict[str, list[str]]:
    """``mp3_url → list of player iframe URLs that embedded it``.

    A single MP3 may have been embedded in multiple posts/players over
    the years; we keep them all so Tier 2 can pick the richest snapshot.
    """
    out: dict[str, list[str]] = {}
    with index_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if "/player/" not in (row.get("path") or ""):
                continue
            url = row.get("url") or ""
            src = parse_qs(urlparse(url).query).get("src", [None])[0]
            if not src or "mp3" not in src.lower():
                continue
            canonical = _canonical_audio_url(src)
            out.setdefault(canonical, []).append(url)
    return out


def show_from_player_url(player_url: str) -> str:
    """Pluck a show slug out of a ``/player/<topic>/…`` URL."""
    parsed = urlparse(player_url)
    segs = [s for s in parsed.path.split("/") if s]
    if len(segs) >= 2 and segs[0] == "player":
        topic = segs[1].lower()
        return PLAYER_TOPIC_TO_SHOW.get(topic, "")
    return ""


# ---------------------------------------------------------------------------
# Tier 1 build entrypoint
# ---------------------------------------------------------------------------


def build_tier1(out_path: Path = METADATA_FILE) -> int:
    """Run Tier 1 — URL-derived metadata for every canonical MP3."""
    mp3s = collect_podcast_mp3s()
    player_index = build_player_index()
    feed_dates = _load_feed_dates()
    rows: list[PodcastMetadata] = []

    for mp3 in mp3s:
        md = parse_url(mp3)

        # Backfill show from the embedding player URL if the filename
        # didn't give us one.
        if not md.show_slug and mp3 in player_index:
            for player_url in player_index[mp3]:
                slug = show_from_player_url(player_url)
                if slug and slug in SHOW_TABLE:
                    name, short = SHOW_TABLE[slug]
                    md.show, md.show_slug = name, short
                    md.extracted_via.append("player-topic")
                    break

        # Remember one player URL as the lead — Tier 2 will fetch its snapshot.
        if player_index.get(mp3):
            md.player_url = player_index[mp3][0]

        if md.megaphone_id in feed_dates and not _full_date(md.date):
            md.date = feed_dates[md.megaphone_id]
            md.extracted_via.append("feed-date")

        md.identifier = build_identifier(md)
        rows.append(md)

    disambiguate_identifiers(rows)

    fields = list(PodcastMetadata.__dataclass_fields__.keys())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow(r.to_row())

    log.info("wrote Tier 1 metadata for %d MP3s to %s", len(rows), out_path)
    return len(rows)


# ---------------------------------------------------------------------------
# Tier 2: ID3 tags from the downloaded MP3 file
# ---------------------------------------------------------------------------


def _strip_html(text: str) -> str:
    """Convert show-notes HTML to plain text.

    The ID3 USLT frame is often used by podcast publishers as a
    rich-text show-notes field — paragraph tags, anchor links, the
    works. archive.org's description renders Markdown-flavored text,
    not HTML, so we collapse to plain text and normalize whitespace.
    """
    if not text or "<" not in text:
        return text.strip()
    soup = BeautifulSoup(text, "html.parser")
    # Preserve link targets as bare URLs in parentheses.
    for a in soup.find_all("a"):
        href = a.get("href")
        label = a.get_text(strip=True)
        if href and href != label:
            a.replace_with(f"{label} ({href})")
    plain = soup.get_text(separator="\n")
    # Collapse 3+ blank lines and trim per-line whitespace.
    lines = [ln.strip() for ln in plain.splitlines()]
    out: list[str] = []
    blank = 0
    for ln in lines:
        if ln:
            out.append(ln)
            blank = 0
        else:
            blank += 1
            if blank <= 1:
                out.append("")
    return "\n".join(out).strip()


def _extract_thumbnail(tags: ID3, identifier: str, out_dir: Path) -> tuple[str, str]:
    """Write the largest APIC cover-art frame to ``<out_dir>/<identifier>.jpg``.

    Returns ``(relative_path, "")`` on success or ``("", "")`` if no
    suitable APIC frame is present. The file extension follows the
    embedded MIME type when known.
    """
    apics = [tags[k] for k in tags if k.startswith("APIC")]
    if not apics:
        return "", ""
    # Pick the largest payload — usually the highest-res cover.
    apic = max(apics, key=lambda a: len(a.data))
    mime = (apic.mime or "image/jpeg").lower()
    ext = {"image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png"}.get(mime, "jpg")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{identifier}.{ext}"
    out_path.write_bytes(apic.data)
    return str(out_path.relative_to(DATA_DIR.parent)), mime


def _load_tags(path: Path) -> ID3 | None:
    """Open ID3 tags or return ``None`` if the file is missing/untagged."""
    if not path.exists():
        return None
    try:
        return ID3(path)
    except (ID3NoHeaderError, Exception):  # noqa: BLE001
        return None


def _read_id3_fields(tags: ID3) -> dict[str, str]:
    """Pull the fields we care about out of one MP3's ID3 frames.

    ``COMM`` is preferred over ``USLT`` for the description because
    USLT sometimes carries HTML show notes; both get HTML-stripped
    before being returned.
    """
    out: dict[str, str] = {}
    if "TIT2" in tags:
        out["title"] = str(tags["TIT2"]).strip()
    if "TALB" in tags:
        out["album"] = str(tags["TALB"]).strip()
    if "TDRC" in tags:
        out["date"] = str(tags["TDRC"]).strip()
    if "TPE1" in tags:
        out["artist"] = str(tags["TPE1"]).strip()
    # Prefer the plain comment frame, fall back to lyrics (sometimes
    # the only place the show notes are stored).
    for k in tags:
        if k.startswith("COMM"):
            out["description"] = _strip_html(str(tags[k]))
            break
    if "description" not in out:
        for k in tags:
            if k.startswith("USLT"):
                out["description"] = _strip_html(str(tags[k]))
                break
    return out


def enrich_with_id3(
    csv_path: Path = METADATA_FILE,
    podcasts_dir: Path = PODCASTS_DIR,
    thumbnails_dir: Path = THUMBNAILS_DIR,
) -> int:
    """Tier 2: fill title/description/date and extract cover art.

    Reads the existing Tier 1 CSV, opens each downloaded MP3 by its
    flat ``<host>__<basename>`` filename, patches missing fields in
    place, and writes the embedded APIC cover art (if any) to
    ``thumbnails_dir/<identifier>.jpg`` so IA items can carry their
    own thumbnail.
    """
    rows: list[dict[str, str]] = []
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fields = reader.fieldnames or list(PodcastMetadata.__dataclass_fields__)
        rows = list(reader)

    enriched = 0
    thumbs = 0
    for row in rows:
        mp3_path = podcasts_dir / filename_for(row["mp3_url"])
        tags = _load_tags(mp3_path)
        if tags is None:
            continue

        fields_from_tags = _read_id3_fields(tags)
        via = (
            (row.get("extracted_via") or "").split("+")
            if row.get("extracted_via")
            else []
        )

        if fields_from_tags.get("title") and not row.get("title"):
            row["title"] = fields_from_tags["title"]
        if fields_from_tags.get("description") and not row.get("description"):
            row["description"] = fields_from_tags["description"]
        if fields_from_tags.get("date") and not row.get("date"):
            # TDRC can be a year ("2020"), full date, or timestamp.
            # IA accepts any of these.
            row["date"] = fields_from_tags["date"]
        # ID3 album field tends to be the canonical show name. If it
        # disagrees with our URL-derived guess, trust the publisher.
        if fields_from_tags.get("album"):
            row["show"] = fields_from_tags["album"]
        if fields_from_tags.get("artist") and not row.get("creator"):
            row["creator"] = fields_from_tags["artist"]

        thumb_path, _ = _extract_thumbnail(tags, row["identifier"], thumbnails_dir)
        if thumb_path:
            row["thumbnail"] = thumb_path
            thumbs += 1

        if "id3" not in via:
            via.append("id3")
        row["extracted_via"] = "+".join(v for v in via if v)
        enriched += 1

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    log.info(
        "enriched %d/%d rows with ID3 tags; extracted %d thumbnails",
        enriched,
        len(rows),
        thumbs,
    )
    return enriched
