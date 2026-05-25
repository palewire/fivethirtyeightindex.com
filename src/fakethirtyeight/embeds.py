"""Extract non-ai2html HTML embeds from downloaded article HTML."""

from __future__ import annotations

import csv
import gzip
import hashlib
import html
import logging
import re
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlunparse

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

from fakethirtyeight.ai2html import (
    AI2HTML_REFS_FILE,
    _chrome_executable,
    _collect_refs,
    _load_article_wayback_lookup,
    _load_render_done,
    _render_one,
    _stream_html,
    _wayback_url_for,
)
from fakethirtyeight.articles import ARTICLES_DIR
from fakethirtyeight.http import make_client
from fakethirtyeight.images import _build_article_url_lookup
from fakethirtyeight.paths import DATA_DIR, ensure_dirs

log = logging.getLogger(__name__)

EMBED_REFS_FILE = DATA_DIR / "embed_references.csv"
EMBED_DIR = DATA_DIR / "embeds"
EMBED_LOG = DATA_DIR / "embed_download_log.csv"
EMBED_RENDER_DIR = DATA_DIR / "embed_renders"
EMBED_RENDER_LOG = DATA_DIR / "embed_render_log.csv"

REF_FIELDS = (
    "identifier",
    "article_file",
    "article_url",
    "embed_url",
    "canonical_url",
    "child_id",
    "title",
    "caption",
    "host",
    "kind",  # "pym" | "iframe"
)

LOG_FIELDS = (
    "identifier",
    "canonical_url",
    "file_path",
    "bytes",
    "content_type",
    "fetched_via",  # "live" | "wayback" | ""
    "status",
    "error",
)

RENDER_LOG_FIELDS = (
    "identifier",
    "canonical_url",
    "file_path",
    "render_path",
    "bytes",
    "width",
    "height",
    "status",
    "error",
)

_IA_ID_PREFIX = "fivethirtyeight-embed-"
_AI2HTML_BLOCK_RE = re.compile(r"ai2html_block_", re.IGNORECASE)
_PYM_PARENT_RE = re.compile(
    r"new\s+pym\.Parent\(\s*"
    r"(?P<child_quote>['\"])(?P<child>[^'\"]+)(?P=child_quote)\s*,\s*"
    r"(?P<url_quote>['\"])(?P<url>.*?)(?<!\\)(?P=url_quote)",
    re.DOTALL,
)
_JS_TITLE_RE = re.compile(
    r"title\s*:\s*(?P<quote>['\"])(?P<title>.*?)(?<!\\)(?P=quote)",
    re.DOTALL,
)


def identifier_for(canonical_url: str) -> str:
    """Deterministic archive.org-style identifier for one HTML embed."""
    h = hashlib.sha1(canonical_url.encode("utf-8"), usedforsecurity=False).hexdigest()[
        :12
    ]
    return f"{_IA_ID_PREFIX}{h}"


def _hash(canonical_url: str) -> str:
    return hashlib.sha1(
        canonical_url.encode("utf-8"), usedforsecurity=False
    ).hexdigest()


def path_for(canonical_url: str, base: Path = EMBED_DIR) -> Path:
    h = _hash(canonical_url)
    return base / h[:2] / f"{h}.html"


def render_path_for(canonical_url: str, base: Path = EMBED_RENDER_DIR) -> Path:
    h = _hash(canonical_url)
    return base / h[:2] / f"{h}.png"


def _decode_js_string(text: str) -> str:
    return (
        html.unescape(text)
        .replace("\\'", "'")
        .replace('\\"', '"')
        .replace("\\/", "/")
        .replace("\\\\", "\\")
        .strip()
    )


def _canonicalize_url(url: str) -> str:
    raw = html.unescape(url).strip()
    if raw.startswith("//"):
        raw = f"https:{raw}"
    p = urlparse(raw)
    if not p.scheme and not p.netloc:
        return ""
    if p.scheme in {"data", "file", "mailto", "tel"}:
        return ""
    if not p.scheme:
        p = p._replace(scheme="https")
    return urlunparse((p.scheme, p.netloc.lower(), p.path, "", "", ""))


def _attr_text(node: Tag, name: str) -> str:
    value = node.get(name) if hasattr(node, "get") else ""
    return value if isinstance(value, str) else ""


def _caption_for(node: Tag) -> str:
    fig = node.find_parent("figure")
    if fig is None:
        return ""
    cap = fig.find("figcaption")
    if cap is None:
        return ""
    return cap.get_text(" ", strip=True)


def _is_sidebar_embed(node: Tag | None) -> bool:
    if node is None:
        return False
    for parent in [node, *node.parents]:
        if not isinstance(parent, Tag):
            continue
        if _attr_text(parent, "id") == "secondary":
            return True
        classes = parent.get("class") or []
        if isinstance(classes, str):
            classes = classes.split()
        if (
            "sidebar-feature" in classes
            or "widget" in classes
            or "interactive-section" in classes
        ):
            return True
    return False


def _is_ai2html_url(url: str) -> bool:
    if "ai2html" in url.lower():
        return True
    p = urlparse(html.unescape(url))
    query = parse_qs(p.query)
    return "ai2html" in query


def _is_candidate_embed(url: str) -> bool:
    canonical = _canonicalize_url(url)
    if not canonical or _is_ai2html_url(url):
        return False
    p = urlparse(canonical)
    host = p.netloc.lower()
    path = p.path.lower()
    if host == "projects.fivethirtyeight.com":
        return True
    if host.endswith(".projects.fivethirtyeight.com"):
        return True
    if host == "projects.abcnews.go.com":
        return True
    if host in {"fivethirtyeight.com", "www.fivethirtyeight.com"}:
        return path.startswith("/wp-content/uploads/") and path.endswith(".html")
    return False


def _has_candidate_signal(html_text: str) -> bool:
    haystack = html_text.lower()
    return (
        "pym.parent" in haystack
        or "projects.fivethirtyeight.com" in haystack
        or "projects.abcnews.go.com" in haystack
        or ("wp-content/uploads/" in haystack and ".html" in haystack)
    )


def _load_ai2html_canonicals(path: Path = AI2HTML_REFS_FILE) -> set[str]:
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as fh:
        return {
            row["canonical_url"]
            for row in csv.DictReader(fh)
            if row.get("canonical_url")
        }


def _extract_one(
    html_text: str,
    article_file: str,
    article_url: str,
    *,
    ai2html_canonicals: set[str] | None = None,
) -> list[dict[str, str]]:
    """Pull non-ai2html HTML embed references out of one article's HTML."""
    soup = BeautifulSoup(html_text, "lxml")
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    ai2html_canonicals = ai2html_canonicals or set()

    def emit(
        *,
        embed_url: str,
        child_id: str,
        title: str,
        caption: str,
        kind: str,
    ) -> None:
        if _AI2HTML_BLOCK_RE.search(child_id) or _is_ai2html_url(embed_url):
            return
        canonical_url = _canonicalize_url(embed_url)
        if (
            not canonical_url
            or canonical_url in seen
            or canonical_url in ai2html_canonicals
            or not _is_candidate_embed(embed_url)
        ):
            return
        seen.add(canonical_url)
        rows.append(
            {
                "identifier": identifier_for(canonical_url),
                "article_file": article_file,
                "article_url": article_url,
                "embed_url": embed_url,
                "canonical_url": canonical_url,
                "child_id": child_id,
                "title": title,
                "caption": caption,
                "host": urlparse(canonical_url).netloc,
                "kind": kind,
            }
        )

    placeholders: dict[str, Tag] = {}
    for tag in soup.find_all(id=True):
        if isinstance(tag, Tag):
            placeholders[_attr_text(tag, "id")] = tag

    for script in soup.find_all("script"):
        script_text = script.get_text("\n")
        if "pym.parent" not in script_text.lower():
            continue
        for match in _PYM_PARENT_RE.finditer(script_text):
            child_id = _decode_js_string(match.group("child"))
            raw_url = _decode_js_string(match.group("url"))
            title_match = _JS_TITLE_RE.search(
                script_text[match.end() : match.end() + 500]
            )
            title = _decode_js_string(title_match.group("title")) if title_match else ""
            placeholder = placeholders.get(child_id)
            if _is_sidebar_embed(placeholder):
                continue
            emit(
                embed_url=raw_url,
                child_id=child_id,
                title=title,
                caption=_caption_for(placeholder) if placeholder else "",
                kind="pym",
            )

    for tag in soup.find_all("iframe"):
        if not isinstance(tag, Tag) or _is_sidebar_embed(tag):
            continue
        raw_url = _attr_text(tag, "src")
        emit(
            embed_url=raw_url,
            child_id=_attr_text(tag, "id"),
            title=_attr_text(tag, "title") or _attr_text(tag, "aria-label"),
            caption=_caption_for(tag),
            kind="iframe",
        )

    return rows


def extract_references(
    *,
    articles_dir: Path = ARTICLES_DIR,
    out_path: Path = EMBED_REFS_FILE,
    ai2html_refs_path: Path = AI2HTML_REFS_FILE,
    enriched_path: Path | None = None,
) -> int:
    """Walk downloaded article HTML, emit one row per non-ai2html HTML embed."""
    if enriched_path is None:
        from fakethirtyeight.enrich import ENRICHED_FILE

        enriched_path = ENRICHED_FILE

    files = sorted(articles_dir.rglob("*.html.gz"))
    log.info("scanning %d article files", len(files))
    url_by_file = _build_article_url_lookup(enriched_path)
    ai2html_canonicals = _load_ai2html_canonicals(ai2html_refs_path)
    log.info("loaded %d ai2html canonical URLs to exclude", len(ai2html_canonicals))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    n_rows = 0
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=REF_FIELDS)
        writer.writeheader()
        for f in tqdm(files, desc="extracting-embeds", unit="article"):
            try:
                with gzip.open(f, "rt", errors="replace") as gh:
                    html_text = gh.read()
            except OSError:
                log.warning("could not read %s, skipping", f)
                continue
            if not _has_candidate_signal(html_text):
                continue
            rel = str(f.relative_to(DATA_DIR.parent))
            article_url = url_by_file.get(rel, "")
            for row in _extract_one(
                html_text,
                rel,
                article_url,
                ai2html_canonicals=ai2html_canonicals,
            ):
                writer.writerow(row)
                n_rows += 1

    log.info("wrote %d embed references to %s", n_rows, out_path)
    return n_rows


def _is_html_body(text: str, content_type: str = "") -> bool:
    ctype = content_type.lower()
    haystack = text[:100_000].lower()
    return (
        "text/html" in ctype
        or "<!doctype html" in haystack
        or "<html" in haystack
        or "<body" in haystack
    )


def _same_live_target(requested: str, final_url: str) -> bool:
    requested_parts = urlparse(requested)
    final_parts = urlparse(final_url)
    return (
        requested_parts.netloc.lower() == final_parts.netloc.lower()
        and requested_parts.path.rstrip("/") == final_parts.path.rstrip("/")
    )


@retry(
    retry=retry_if_exception_type(httpx.HTTPError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def _stream_live_embed(client: httpx.Client, canonical_url: str) -> tuple[str, str]:
    with client.stream("GET", canonical_url, follow_redirects=True) as resp:
        if resp.status_code in {429, 500, 502, 503, 504}:
            raise httpx.HTTPStatusError(
                "retryable", request=resp.request, response=resp
            )
        resp.raise_for_status()
        if not _same_live_target(canonical_url, str(resp.url)):
            msg = f"redirected to {resp.url}"
            raise RuntimeError(msg)
        body = ""
        for chunk in resp.iter_text(chunk_size=64 * 1024):
            body += chunk
        return body, resp.headers.get("content-type", "")


def _try_fetch(
    client: httpx.Client, canonical_url: str
) -> tuple[str, str, str] | tuple[None, None, str]:
    try:
        text, ct = _stream_live_embed(client, canonical_url)
        if text and _is_html_body(text, ct):
            return text, ct, "live"
        live_err = f"non-html response: {ct or 'unknown content type'}"
    except Exception as live_exc:  # noqa: BLE001
        live_err = repr(live_exc)[:160]

    wb = _wayback_url_for(client, canonical_url)
    if not wb:
        parts = urlparse(canonical_url)
        if parts.scheme == "https":
            http_url = urlunparse(("http", parts.netloc, parts.path, "", "", ""))
            wb = _wayback_url_for(client, http_url)
    if not wb:
        return None, None, f"live: {live_err}; no wayback capture"
    try:
        text, ct = _stream_html(client, wb)
        if text and _is_html_body(text, ct):
            return text, ct, "wayback"
        return (
            None,
            None,
            (f"live: {live_err}; wayback: non-html response: {ct or 'unknown'}"),
        )
    except Exception as wb_exc:  # noqa: BLE001
        return None, None, f"live: {live_err}; wayback: {repr(wb_exc)[:80]}"


def _timestamp_from_wayback_url(url: str) -> str:
    match = re.search(r"/web/(\d{8,14})", url)
    return match.group(1) if match else ""


def _try_fetch_at_timestamp(
    client: httpx.Client, canonical_url: str, timestamp: str
) -> tuple[str, str, str] | tuple[None, None, str]:
    if not timestamp:
        return None, None, "missing article timestamp"
    url = f"https://web.archive.org/web/{timestamp}id_/{canonical_url}"
    try:
        text, ct = _stream_html(client, url)
        if text and _is_html_body(text, ct):
            return text, ct, f"wayback:{timestamp}"
        return None, None, f"timestamp wayback: non-html response: {ct or 'unknown'}"
    except Exception as exc:  # noqa: BLE001
        return None, None, f"timestamp wayback: {repr(exc)[:120]}"


def _logged_file_is_html(row: dict[str, str], *, root: Path = DATA_DIR.parent) -> bool:
    path = row.get("file_path") or ""
    if not path:
        return False
    p = root / path
    if not p.exists():
        return False
    return _is_html_body(p.read_text(encoding="utf-8", errors="replace"))


def _load_done(log_path: Path, *, root: Path = DATA_DIR.parent) -> set[str]:
    if not log_path.exists():
        return set()
    out: set[str] = set()
    with log_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (
                (row.get("status") or "") == "ok"
                and row.get("canonical_url")
                and _logged_file_is_html(row, root=root)
            ):
                out.add(row["canonical_url"])
    return out


def _load_downloaded_embeds(
    log_path: Path = EMBED_LOG, *, root: Path = DATA_DIR.parent
) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    if not log_path.exists():
        return out
    with log_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            canonical = (row.get("canonical_url") or "").strip()
            file_path = (row.get("file_path") or "").strip()
            if (
                canonical
                and file_path
                and (row.get("status") or "") == "ok"
                and (root / file_path).exists()
            ):
                out[canonical] = row
    return out


def download_embeds(
    *,
    workers: int = 4,
    limit: int | None = None,
    refs_path: Path = EMBED_REFS_FILE,
    enriched_path: Path | None = None,
    out_dir: Path = EMBED_DIR,
    log_path: Path = EMBED_LOG,
) -> tuple[int, int, int]:
    """Save each unique non-ai2html HTML embed as local HTML."""
    if enriched_path is None:
        from fakethirtyeight.enrich import ENRICHED_FILE

        enriched_path = ENRICHED_FILE
    ensure_dirs()
    out_dir.mkdir(parents=True, exist_ok=True)

    refs = _collect_refs(refs_path)
    wayback_by_file = _load_article_wayback_lookup(enriched_path)
    log.info("collected %d unique embed references", len(refs))
    done = _load_done(log_path)
    pending = [row for row in refs if row["canonical_url"] not in done]
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

        def _process(row: dict[str, str]) -> int:
            canonical = row["canonical_url"]
            body, content_type, source = _try_fetch(client, canonical)
            if body is None:
                body, content_type, timestamp_source = _try_fetch_at_timestamp(
                    client,
                    canonical,
                    _timestamp_from_wayback_url(
                        wayback_by_file.get(row.get("article_file") or "", "")
                    ),
                )
                if body is not None:
                    source = timestamp_source
            error = source if body is None else ""
            if body is None:
                with write_lock:
                    writer.writerow(
                        {
                            "identifier": identifier_for(canonical),
                            "canonical_url": canonical,
                            "file_path": "",
                            "bytes": "0",
                            "content_type": "",
                            "fetched_via": "",
                            "status": "error",
                            "error": error[:200],
                        }
                    )
                    fh.flush()
                return 0

            out_path = path_for(canonical, base=out_dir)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(body, encoding="utf-8")
            with write_lock:
                writer.writerow(
                    {
                        "identifier": identifier_for(canonical),
                        "canonical_url": canonical,
                        "file_path": str(out_path.relative_to(DATA_DIR.parent)),
                        "bytes": str(out_path.stat().st_size),
                        "content_type": content_type or "text/html",
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
            desc="downloading-embeds",
            unit="html",
        )

    n_ok = sum(outcomes)
    return (n_ok, len(done), len(pending) - n_ok)


def render_embeds(
    *,
    limit: int | None = None,
    width: int = 1000,
    height: int = 4096,
    timeout: int = 60,
    force: bool = False,
    refs_path: Path = EMBED_REFS_FILE,
    download_log_path: Path = EMBED_LOG,
    enriched_path: Path | None = None,
    out_dir: Path = EMBED_RENDER_DIR,
    log_path: Path = EMBED_RENDER_LOG,
) -> tuple[int, int, int]:
    """Render downloaded non-ai2html HTML embeds to desktop PNG screenshots."""
    if enriched_path is None:
        from fakethirtyeight.enrich import ENRICHED_FILE

        enriched_path = ENRICHED_FILE
    ensure_dirs()
    out_dir.mkdir(parents=True, exist_ok=True)

    refs = _collect_refs(refs_path)
    downloaded = _load_downloaded_embeds(download_log_path)
    done = set() if force else _load_render_done(log_path)
    pending = [
        row
        for row in refs
        if row["canonical_url"] in downloaded and row["canonical_url"] not in done
    ]
    missing = len([row for row in refs if row["canonical_url"] not in downloaded])
    log.info(
        "%d embed refs; %d downloaded; %d already rendered; %d missing html; %d to render",
        len(refs),
        len(downloaded),
        len(done),
        missing,
        len(pending),
    )
    if limit is not None:
        pending = pending[:limit]
        log.info("limit=%d, rendering %d", limit, len(pending))
    if not pending:
        return (0, len(done), missing)

    chrome = _chrome_executable()
    wayback_by_file = _load_article_wayback_lookup(enriched_path)
    write_header = not log_path.exists()
    rendered = failed = 0
    with log_path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=RENDER_LOG_FIELDS)
        if write_header:
            writer.writeheader()
        for row in tqdm(pending, desc="rendering-embeds", unit="png"):
            canonical = row["canonical_url"]
            source_row = downloaded[canonical]
            src_path = DATA_DIR.parent / source_row["file_path"]
            out_path = render_path_for(canonical, base=out_dir)
            try:
                size, png_width, png_height, error = _render_one(
                    canonical_url=canonical,
                    file_path=src_path,
                    out_path=out_path,
                    chrome=chrome,
                    base_url=wayback_by_file.get(row.get("article_file") or "")
                    or row.get("article_url")
                    or canonical,
                    width=width,
                    height=height,
                    timeout=timeout,
                )
            except Exception as exc:  # noqa: BLE001
                size, png_width, png_height, error = 0, 0, 0, repr(exc)[:240]

            if error:
                failed += 1
                status = "error"
                render_path = ""
            else:
                rendered += 1
                status = "ok"
                render_path = str(out_path.relative_to(DATA_DIR.parent))
            writer.writerow(
                {
                    "identifier": identifier_for(canonical),
                    "canonical_url": canonical,
                    "file_path": source_row["file_path"],
                    "render_path": render_path,
                    "bytes": str(size),
                    "width": str(png_width),
                    "height": str(png_height),
                    "status": status,
                    "error": error,
                }
            )
            fh.flush()

    return (rendered, len(done), failed + missing)
