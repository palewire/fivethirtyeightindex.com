"""Extract and download every content graphic referenced in the articles.

Two-stage pipeline:

1. :func:`extract_references` walks ``data/articles/**/*.html.gz``,
   parses each page with BeautifulSoup, and emits one row per
   ``<img>`` / ``<source srcset>`` reference to
   ``data/image_references.csv``. Drops ad pixels, tracking beacons,
   and WordPress theme/UI chrome.

2. :func:`download_images` reads the references CSV, dedupes by
   canonical URL (query string stripped), and streams each image to
   disk under ``data/images/<sha1>.<ext>``. Tries the live URL first,
   falls back to a Wayback ``id_`` snapshot when live 404s. Resumable
   via ``data/image_download_log.csv``.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import logging
import re
import threading
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup
from bs4.element import Tag
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tqdm import tqdm
from tqdm.contrib.concurrent import thread_map

from fakethirtyeight.articles import ARTICLES_DIR
from fakethirtyeight.http import make_client
from fakethirtyeight.paths import DATA_DIR, ensure_dirs

log = logging.getLogger(__name__)

IMAGE_REFS_FILE = DATA_DIR / "image_references.csv"
IMAGES_DIR = DATA_DIR / "images"
IMAGE_LOG = DATA_DIR / "image_download_log.csv"

REF_FIELDS = (
    "identifier",  # archive.org item identifier (deterministic from canonical_url)
    "article_file",
    "image_url",
    "canonical_url",
    "alt",
    "caption",
    "kind",  # "img" | "source"
    "category",  # "chart" | "featured-image" | "banner" | "screenshot" | "headshot"
)

LOG_FIELDS = (
    "identifier",  # archive.org item identifier (deterministic from canonical_url)
    "canonical_url",
    "file_path",
    "bytes",
    "content_type",
    "fetched_via",  # "live" | "wayback" | ""
    "status",
    "error",
)

#: Prefix for the deterministic archive.org item identifier we mint per
#: canonical image URL. Same image → same identifier, every run, so
#: the four image CSVs join cleanly on this single key.
_IA_ID_PREFIX = "fivethirtyeight-image-"


def identifier_for(canonical_url: str) -> str:
    """Deterministic archive.org item identifier for one canonical URL.

    Defined here (rather than in ``ia_image_upload``) so the
    extraction and download stages can stamp it into their logs
    without dragging in the upload module's heavier dependencies.
    """
    h = hashlib.sha1(canonical_url.encode("utf-8"), usedforsecurity=False).hexdigest()[
        :12
    ]
    return f"{_IA_ID_PREFIX}{h}"


#: Hostnames whose images are never content graphics — ads, analytics
#: pixels, sharing widgets, etc. Any ``<img src>`` matching one of
#: these gets dropped on extraction.
_NOISE_HOSTS = frozenset(
    [
        # Ad networks + analytics pixels.
        "ad.doubleclick.net",
        "doubleclick.net",
        "googleads.g.doubleclick.net",
        "secure-us.imrworldwide.com",
        "imrworldwide.com",
        "stats.wordpress.com",
        "pixel.wp.com",
        "pixel.quantserve.com",
        "s.w.org",
        "static.chartbeat.com",
        "static.chartbeat.io",
        "b.scorecardresearch.com",
        "sb.scorecardresearch.com",
        "web.blogads.com",
        # Social / share widget sprites and donate buttons that show
        # up in every post's chrome.
        "s7.addthis.com",
        "www.addthis.com",
        "www.paypal.com",
        "s.abcnews.com",
        # Blogger / Blogspot UI sprites — leak in from 538's 2008-2010
        # pre-NYT-era posts that still link to comment-bubble icons,
        # voting arrows, blog-template chrome.
        "img1.blogblog.com",
        "img2.blogblog.com",
        "www.blogblog.com",
        "resources.blogblog.com",
        "bp0.blogger.com",
        "bp1.blogger.com",
        "bp2.blogger.com",
        "bp3.blogger.com",
        # Blogspot-era third-party template designers and partner
        # sites whose images on 538 posts are always sidebar chrome.
        "www.gauldindesign.com",
        "gauldindesign.com",
        "static1.firedoglake.com",
        # BaseballProspectus comment-voting arrow sprites that landed
        # in early 538 posts via embedded BP comment widgets.
        "www.baseballprospectus.com",
        "baseballprospectus.com",
    ]
)

#: Path prefixes (host-relative) that indicate theme/UI assets — logos,
#: emoji sprites, icon fonts — not editorial graphics.
_NOISE_PATH_PREFIXES = (
    "/wp-content/themes/",
    "/wp-includes/",
    "/wp-admin/",
)

#: Filename patterns that identify wire-service photographs we DON'T
#: want to re-host on archive.org. These are AP/Getty/Reuters/Flickr-
#: hosted images, identifiable from their canonical IDs in the
#: filename. Skipped at extraction time so the corpus we upload is
#: just 538-original graphics (charts, screenshots, banners, etc.).
_WIRE_PHOTO_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Associated Press wire IDs — long digit run after an "ap" prefix.
    re.compile(r"(?:^|[/_-])ap\d{6,}", re.IGNORECASE),
    # Getty Images — always "gettyimages-<digits>"
    re.compile(r"gettyimages?[-_]?\d{5,}", re.IGNORECASE),
    # Reuters wire IDs — "rtr" + 4+ alphanumerics, typical Reuters filenames.
    re.compile(r"(?:^|[/_-])rtr[a-z0-9]{4,}", re.IGNORECASE),
    # Flickr / stock-numeric: 8+ leading digits then underscore (Flickr's
    # photo-ID/secret pattern). Strict so 538-charts named like
    # ``2014_polls.png`` don't get caught.
    re.compile(r"^\d{8,}[_-]", re.IGNORECASE),
)

#: 538's recurring chart-series filenames that name themselves "profile"
#: (e.g. ``ECON-PROFILE-0419-4x3-1.png`` — the weekly Econ Profile chart
#: series). These predate the headshot regex below and would otherwise
#: be mis-bucketed. First-match-wins: any of these patterns lock the
#: category to ``chart`` before the headshot rule can fire.
_FORCE_CHART_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^econ[-_]profile[-_]", re.IGNORECASE),
)

#: Filename → category. Order matters; first hit wins. Default is
#: ``chart`` since the bulk of unclassified WP uploads are original
#: 538 graphics.
_CATEGORY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "featured-image",
        re.compile(r"(?:^|[-_])(?:lede|hero|featured?)(?:[-_.]|$)", re.IGNORECASE),
    ),
    (
        "banner",
        re.compile(r"(?:^|[-_])(?:banner|divider)(?:[-_.]|$)", re.IGNORECASE),
    ),
    (
        "screenshot",
        re.compile(r"screen[-_]?shot", re.IGNORECASE),
    ),
    (
        "headshot",
        re.compile(r"(?:^|[-_])(?:headshot|avatar|profile)(?:[-_.]|$)", re.IGNORECASE),
    ),
)

#: Categories we extract but DO NOT keep. Mission scope is charts,
#: graphs, maps, and data visualizations — illustrations, photos,
#: decorative banners and player headshots are out.
_DROP_CATEGORIES = frozenset(["banner", "headshot", "featured-image"])

#: Screenshots are only kept when the host article URL contains one of
#: these path fragments. The recurring data-roundup columns
#: (Significant Digits, Week In Data, Ctrl+←, Datalab) screenshot real
#: charts as part of their format; screenshots in other contexts are
#: typically chats, product photos, or game UIs — out of mission.
_SCREENSHOT_KEEP_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"/significant-digits[/_-]", re.IGNORECASE),
    re.compile(r"/week-in-data[/_-]", re.IGNORECASE),
    re.compile(r"/datalab[/_-]", re.IGNORECASE),
    re.compile(r"/ctrl-", re.IGNORECASE),
)


def _screenshot_in_scope(article_url: str) -> bool:
    """``True`` if a screenshot from this article URL is within mission."""
    return any(p.search(article_url) for p in _SCREENSHOT_KEEP_PATTERNS)


#: Specific noise filenames within the ``chart`` bucket that aren't
#: actually charts — layout spacer GIFs/PNGs from the Blogspot era.
_NOISE_FILENAME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^whitespace\d*\.(?:gif|png|jpg)$", re.IGNORECASE),
    re.compile(r"^spacer\d*\.(?:gif|png|jpg)$", re.IGNORECASE),
    re.compile(r"^pixel\d*\.(?:gif|png|jpg)$", re.IGNORECASE),
)


def _is_wire_photo(filename: str) -> bool:
    """``True`` if the filename matches a wire-service photo ID pattern."""
    return any(p.search(filename) for p in _WIRE_PHOTO_PATTERNS)


def _categorize(filename: str) -> str:
    """Bucket an image filename into one of our editorial-asset categories.

    Rescue rules fire first (e.g. ``ECON-PROFILE-*`` is always a chart
    even though it contains the word "profile"), then the general
    category patterns, then a ``chart`` default.
    """
    for pat in _FORCE_CHART_PATTERNS:
        if pat.search(filename):
            return "chart"
    for label, pat in _CATEGORY_PATTERNS:
        if pat.search(filename):
            return label
    return "chart"


def _is_noise_filename(filename: str) -> bool:
    """Layout-spacer / pixel-gif files that snuck in as `chart` defaults."""
    return any(p.search(filename) for p in _NOISE_FILENAME_PATTERNS)


# ---------------------------------------------------------------------------
# Stage 1: extract
# ---------------------------------------------------------------------------


def _canonicalize(url: str) -> str:
    """Strip query + fragment, rewrite legacy hosts to their live equivalents.

    Two normalizations on top of the obvious query-string strip:

    1. ``espnfivethirtyeight.files.wordpress.com/<YYYY>/<MM>/<file>``
       is rewritten to ``fivethirtyeight.com/wp-content/uploads/<YYYY>/<MM>/<file>``.
       The WordPress.com legacy host returns 403 universally now, but
       the ESPN migration carried every file over to the new path
       (verified hit-rate 10/10 on a smoke test).

    2. ``i[012].wp.com/<rest>`` is the Jetpack CDN. The path after the
       host is the actual source URL — extract it and recurse so e.g.
       ``i0.wp.com/espnfivethirtyeight.files.wordpress.com/foo.jpg``
       collapses to the same canonical key as the legacy / migrated URL.

    WordPress serves the same source image at many sizes via query
    params (``?w=575``, ``?resize=100,75``). The canonical form
    ignores those and references the master file.
    """
    p = urlparse(url)
    if not p.scheme and not p.netloc:
        return ""
    # Drop ``data:`` (inline base64 placeholders for lazy-loading) and
    # ``file://`` (Windows-desktop paths from authors pasting local
    # screenshots into the WordPress editor).
    if p.scheme in {"data", "file"}:
        return ""
    if not p.scheme:
        p = p._replace(scheme="https")

    host = p.netloc.lower()
    path = p.path

    # Jetpack i0/i1/i2.wp.com proxies — peel them off and recurse.
    if host in {"i0.wp.com", "i1.wp.com", "i2.wp.com"} and path.startswith("/"):
        inner = "https://" + path.lstrip("/")
        canon = _canonicalize(inner)
        if canon:
            return canon

    # Legacy WordPress.com bucket → migrated WP media library.
    if host == "espnfivethirtyeight.files.wordpress.com":
        host = "fivethirtyeight.com"
        path = "/wp-content/uploads" + path

    return urlunparse((p.scheme, host, path, "", "", ""))


def _is_noise(url: str) -> bool:
    """True if ``url`` is an ad/tracking/theme asset, not a content image."""
    p = urlparse(url)
    host = p.netloc.lower()
    # Strip a leading ``www.`` (proper prefix removal, not ``lstrip`` —
    # that one would chew through any combination of w/. characters).
    bare = host[4:] if host.startswith("www.") else host
    for candidate in (host, bare):
        if candidate in _NOISE_HOSTS:
            return True
        if any(candidate.endswith("." + n) for n in _NOISE_HOSTS):
            return True
    for prefix in _NOISE_PATH_PREFIXES:
        if p.path.startswith(prefix):
            return True
    # 1x1 tracking gifs — sometimes named explicitly
    if p.path.endswith("/blank.gif") or p.path.endswith("/transparent.gif"):
        return True
    return False


def _expand_srcset(srcset: str) -> Iterable[str]:
    """Yield each URL from a srcset value (largest variant first)."""
    parts: list[tuple[int, str]] = []
    for entry in srcset.split(","):
        entry = entry.strip()
        if not entry:
            continue
        bits = entry.split()
        url = bits[0]
        # Width descriptor like "1200w" → sort by width desc so we
        # emit the largest variant first; helps when a downstream
        # consumer only wants the master file.
        width = 0
        if len(bits) > 1 and bits[1].endswith("w"):
            try:
                width = int(bits[1][:-1])
            except ValueError:
                width = 0
        parts.append((width, url))
    parts.sort(reverse=True)
    for _, url in parts:
        yield url


def _caption_for(img_or_source: Tag) -> str:
    """Pull the nearest ``<figcaption>`` text, if any."""
    fig = img_or_source.find_parent("figure")
    if fig is None:
        return ""
    cap = fig.find("figcaption")
    if cap is None:
        return ""
    return cap.get_text(" ", strip=True)


def _extract_one(
    html: str, article_file: str, article_url: str
) -> list[dict[str, str]]:
    """Pull image references out of one article's HTML."""
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict[str, str]] = []
    seen_in_article: set[str] = set()

    def _emit(url: str, alt: str, caption: str, kind: str) -> None:
        if not url:
            return
        canonical = _canonicalize(url)
        if not canonical or _is_noise(canonical):
            return
        filename = canonical.rsplit("/", 1)[-1]
        if _is_wire_photo(filename):
            return  # AP/Getty/Reuters/etc — skip per archival policy
        if _is_noise_filename(filename):
            return  # layout spacer / pixel gif
        category = _categorize(filename)
        if category in _DROP_CATEGORIES:
            return  # out-of-mission: banners, headshots, ledes
        # NB: we deliberately don't drop screenshots here. They get a
        # vision-based classification pass in :mod:`caption` and are
        # filtered at upload time based on what Claude actually sees.
        if canonical in seen_in_article:
            return
        seen_in_article.add(canonical)
        rows.append(
            {
                "identifier": identifier_for(canonical),
                "article_file": article_file,
                "image_url": url,
                "canonical_url": canonical,
                "alt": alt or "",
                "caption": caption or "",
                "kind": kind,
                "category": category,
            }
        )

    for img in soup.find_all("img"):
        alt = _attr_text(img, "alt").strip()
        caption = _caption_for(img)
        src = _attr_text(img, "src").strip()
        _emit(src, alt, caption, "img")
        srcset = _attr_text(img, "srcset").strip()
        if srcset:
            for url in _expand_srcset(srcset):
                _emit(url, alt, caption, "img")

    # <picture><source srcset=...> for art-direction variants
    for source in soup.find_all("source"):
        srcset = _attr_text(source, "srcset").strip()
        if not srcset:
            continue
        caption = _caption_for(source)
        for url in _expand_srcset(srcset):
            _emit(url, "", caption, "source")

    return rows


def _attr_text(node: Tag, name: str) -> str:
    value = node.get(name) if hasattr(node, "get") else ""
    return value if isinstance(value, str) else ""


def _build_article_url_lookup(enriched_path: Path) -> dict[str, str]:
    """``file_path → article URL`` from the enriched articles CSV.

    Used by :func:`extract_references` so the per-image filters can
    inspect the host article URL (e.g. to scope ``screenshot`` items
    to the data-roundup columns).
    """
    if not enriched_path.exists():
        return {}
    out: dict[str, str] = {}
    with enriched_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            url = (row.get("url") or "").strip()
            ts = (row.get("snapshot_timestamp") or "").strip()
            if not url or not ts:
                continue
            uhash = hashlib.sha1(
                url.encode("utf-8"), usedforsecurity=False
            ).hexdigest()[:16]
            key = f"data/articles/{ts[:4]}/{uhash}.html.gz"
            out[key] = url
    return out


def extract_references(
    *,
    articles_dir: Path = ARTICLES_DIR,
    out_path: Path = IMAGE_REFS_FILE,
    enriched_path: Path | None = None,
) -> int:
    """Walk all downloaded article HTML, emit one row per image reference."""
    if enriched_path is None:
        from fakethirtyeight.enrich import ENRICHED_FILE

        enriched_path = ENRICHED_FILE

    files = sorted(articles_dir.rglob("*.html.gz"))
    log.info("scanning %d article files", len(files))
    url_by_file = _build_article_url_lookup(enriched_path)
    log.info("loaded article URL lookup for %d files", len(url_by_file))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    n_rows = 0
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=REF_FIELDS)
        writer.writeheader()
        for f in tqdm(files, desc="extracting", unit="article"):
            try:
                with gzip.open(f, "rt", errors="replace") as gh:
                    html = gh.read()
            except OSError:
                log.warning("could not read %s, skipping", f)
                continue
            rel = str(f.relative_to(DATA_DIR.parent))
            article_url = url_by_file.get(rel, "")
            for row in _extract_one(html, rel, article_url):
                writer.writerow(row)
                n_rows += 1

    log.info("wrote %d image references to %s", n_rows, out_path)
    return n_rows


# ---------------------------------------------------------------------------
# Stage 2: download
# ---------------------------------------------------------------------------


def _hash(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8"), usedforsecurity=False).hexdigest()


def _extension_for(content_type: str, url: str) -> str:
    """Pick a file extension from the response content type, falling back
    to the URL path's extension or ``.bin``."""
    ct = (content_type or "").split(";")[0].strip().lower()
    by_ct = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "image/avif": ".avif",
    }
    if ct in by_ct:
        return by_ct[ct]
    path_ext = Path(urlparse(url).path).suffix.lower()
    if path_ext in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".avif"}:
        return ".jpg" if path_ext == ".jpeg" else path_ext
    return ".bin"


def path_for(
    canonical_url: str, content_type: str = "", base: Path = IMAGES_DIR
) -> Path:
    """``data/images/<aa>/<sha1>.<ext>`` — 256-bucket sharding."""
    h = _hash(canonical_url)
    return base / h[:2] / f"{h}{_extension_for(content_type, canonical_url)}"


@retry(
    retry=retry_if_exception_type(httpx.HTTPError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def _stream(client: httpx.Client, url: str) -> tuple[bytes, str]:
    """Fetch ``url`` and return ``(content_bytes, content_type)``.

    Raises ``HTTPStatusError`` on retryable status codes so tenacity
    will back off and retry.
    """
    with client.stream("GET", url, follow_redirects=True) as resp:
        if resp.status_code in {429, 500, 502, 503, 504}:
            raise httpx.HTTPStatusError(
                "retryable", request=resp.request, response=resp
            )
        resp.raise_for_status()
        body = b""
        for chunk in resp.iter_bytes(chunk_size=64 * 1024):
            body += chunk
        return body, resp.headers.get("content-type", "")


def _wayback_url_for(client: httpx.Client, canonical_url: str) -> str | None:
    """Use the availability API to find a real Wayback snapshot for ``url``."""
    try:
        resp = client.get(
            "https://archive.org/wayback/available",
            params={"url": canonical_url},
            timeout=20,
        )
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return None
    snap = (data.get("archived_snapshots") or {}).get("closest") or {}
    if not snap.get("available"):
        return None
    ts = snap.get("timestamp") or ""
    if not ts:
        return None
    # ``id_`` gives us the raw bytes — no Wayback chrome/rewrite.
    return f"https://web.archive.org/web/{ts}id_/{canonical_url}"


def _try_fetch(
    client: httpx.Client, canonical_url: str
) -> tuple[bytes, str, str] | tuple[None, None, str]:
    """Attempt live → Wayback. Return ``(body, content_type, source)``
    where ``source`` is ``"live"`` or ``"wayback"``, or ``(None, None, error)``."""
    try:
        body, ct = _stream(client, canonical_url)
        if body:
            return body, ct, "live"
    except Exception as live_exc:  # noqa: BLE001
        live_err = repr(live_exc)[:160]
    else:
        live_err = "empty-body"

    wb = _wayback_url_for(client, canonical_url)
    if not wb:
        return None, None, f"live: {live_err}; no wayback capture"
    try:
        body, ct = _stream(client, wb)
        if body:
            return body, ct, "wayback"
        return None, None, f"live: {live_err}; wayback: empty"
    except Exception as wb_exc:  # noqa: BLE001
        return None, None, f"live: {live_err}; wayback: {repr(wb_exc)[:80]}"


def _load_done(log_path: Path) -> set[str]:
    if not log_path.exists():
        return set()
    out: set[str] = set()
    with log_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (row.get("status") or "") == "ok" and row.get("canonical_url"):
                out.add(row["canonical_url"])
    return out


def _collect_canonical_urls(refs_path: Path) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    with refs_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            u = (row.get("canonical_url") or "").strip()
            if u and u not in seen:
                seen.add(u)
                out.append(u)
    return out


def download_images(
    *,
    workers: int = 8,
    limit: int | None = None,
    refs_path: Path = IMAGE_REFS_FILE,
    out_dir: Path = IMAGES_DIR,
    log_path: Path = IMAGE_LOG,
) -> tuple[int, int, int]:
    """Stream every canonical image URL to disk.

    Returns ``(downloaded, skipped, failed)``. Live URL first, Wayback
    fallback. Saves as ``data/images/<aa>/<sha1>.<ext>``.
    """
    ensure_dirs()
    out_dir.mkdir(parents=True, exist_ok=True)

    urls = _collect_canonical_urls(refs_path)
    log.info("collected %d unique canonical image URLs", len(urls))

    done = _load_done(log_path)
    pending = [u for u in urls if u not in done]
    log.info("%d already logged; %d to fetch", len(done), len(pending))
    if limit is not None:
        pending = pending[:limit]
        log.info("limit=%d, fetching %d", limit, len(pending))

    if not pending:
        return (0, len(done), 0)

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

        def _process(url: str) -> int:
            ident = identifier_for(url)
            body, ct, source = _try_fetch(client, url)
            if body is None:
                with write_lock:
                    writer.writerow(
                        {
                            "identifier": ident,
                            "canonical_url": url,
                            "file_path": "",
                            "bytes": "0",
                            "content_type": "",
                            "fetched_via": "",
                            "status": "error",
                            "error": source[:200],
                        }
                    )
                    fh.flush()
                return 0
            content_type = ct or ""
            out_path = path_for(url, content_type, base=out_dir)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(body)
            with write_lock:
                writer.writerow(
                    {
                        "identifier": ident,
                        "canonical_url": url,
                        "file_path": str(out_path.relative_to(DATA_DIR.parent)),
                        "bytes": str(len(body)),
                        "content_type": content_type,
                        "fetched_via": source,
                        "status": "ok",
                        "error": "",
                    }
                )
                fh.flush()
            return 1

        outcomes = thread_map(
            _process,
            pending,
            max_workers=workers,
            desc="downloading",
            unit="img",
        )

    n_ok = sum(outcomes)
    return (n_ok, len(done), len(pending) - n_ok)
