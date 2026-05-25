"""Upload rendered ai2html/embed graphics to archive.org.

Each item preserves the extracted HTML source and uploads the rendered
desktop PNG first so archive.org treats the screenshot as the lead image.
"""

from __future__ import annotations

import csv
import hashlib
import logging
import os
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from internetarchive import get_session

from fakethirtyeight.ai2html import (
    AI2HTML_LOG,
    AI2HTML_REFS_FILE,
    AI2HTML_RENDER_LOG,
)
from fakethirtyeight.embeds import EMBED_LOG, EMBED_REFS_FILE, EMBED_RENDER_LOG
from fakethirtyeight.enrich import ENRICHED_FILE
from fakethirtyeight.ia_image_upload import (
    DEFAULT_COLLECTION,
    DEFAULT_CONTRIBUTOR,
    DEFAULT_PUBLISHER,
    _append_unique,
    _configure_session_tls,
)
from fakethirtyeight.ia_metadata import year_from_date
from fakethirtyeight.paths import DATA_DIR, ensure_dirs
from fakethirtyeight.site_data import clean_byline

log = logging.getLogger(__name__)

UPLOAD_LOG = DATA_DIR / "html_graphic_upload_log.csv"
LOG_FIELDS = (
    "identifier",
    "canonical_url",
    "uploaded_at",
    "status",
    "files",
    "error",
)

HTML_BUNDLE_SUBJECT = "html-bundle"
AI_DISCLOSURE = (
    "AI-generated text disclosure: descriptive title/category/summary metadata "
    "for this archived item may include text generated from automated "
    "classification and captioning of the source asset.\n\n"
    "Generated with assistance from Claude Sonnet 4.6 by Anthropic."
)


class _UploadResponse(Protocol):
    def raise_for_status(self) -> None: ...


class _ArchiveItem(Protocol):
    def upload(
        self,
        files: list[str],
        metadata: dict[str, str | list[str]],
        retries: int,
        retries_sleep: int,
        verbose: bool,
    ) -> Iterable[_UploadResponse]: ...


class _ArchiveSession(Protocol):
    def get_item(self, identifier: str) -> _ArchiveItem: ...


@dataclass(slots=True, frozen=True)
class HtmlGraphic:
    identifier: str
    canonical_url: str
    article_file: str
    article_url: str
    title: str
    caption: str
    kind: str
    bundle_kind: str
    html_path: Path
    png_path: Path
    published_at: str = ""
    article_title: str = ""
    byline: str = ""


@dataclass(slots=True, frozen=True)
class UploadResult:
    identifier: str
    canonical_url: str
    status: str  # 'uploaded' | 'skipped' | 'error' | 'dry_run'
    files: str = ""
    error: str = ""


def _archive_session(access: str, secret: str) -> _ArchiveSession:
    session = get_session(config={"s3": {"access": access, "secret": secret}})
    _configure_session_tls(session)
    return session


def _load_credentials() -> tuple[str, str]:
    access = os.environ.get("IA_ACCESS_KEY")
    secret = os.environ.get("IA_SECRET_KEY")
    if not access or not secret:
        msg = "Set IA_ACCESS_KEY and IA_SECRET_KEY env vars first."
        raise RuntimeError(msg)
    return access, secret


def _load_done(log_path: Path) -> set[str]:
    if not log_path.exists():
        return set()
    out: set[str] = set()
    with log_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (row.get("status") or "") == "uploaded" and row.get("identifier"):
                out.add(row["identifier"])
    return out


def _read_latest_rows(path: Path) -> dict[str, dict[str, str]]:
    latest: dict[str, dict[str, str]] = {}
    if not path.exists():
        return latest
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            canonical = (row.get("canonical_url") or "").strip()
            if canonical:
                latest[canonical] = row
    return latest


def _collect_refs(path: Path, bundle_kind: str) -> list[dict[str, str]]:
    if not path.exists():
        return []
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            canonical = (row.get("canonical_url") or "").strip()
            if not canonical or canonical in seen:
                continue
            seen.add(canonical)
            row["bundle_kind"] = bundle_kind
            out.append(row)
    return out


def _load_article_meta(enriched_path: Path) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    if not enriched_path.exists():
        return out
    with enriched_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            url = (row.get("url") or "").strip()
            ts = (row.get("snapshot_timestamp") or "").strip()
            if not url or not ts:
                continue
            year = ts[:4]
            uhash = hashlib.sha1(
                url.encode("utf-8"), usedforsecurity=False
            ).hexdigest()[:16]
            out[f"data/articles/{year}/{uhash}.html.gz"] = row
    return out


def _as_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else DATA_DIR.parent / path


def _fallback_year(graphic: HtmlGraphic) -> str:
    if graphic.published_at:
        return year_from_date(graphic.published_at)
    parts = Path(graphic.article_file).parts
    if "articles" in parts:
        idx = parts.index("articles")
        if len(parts) > idx + 1 and parts[idx + 1].isdigit():
            return parts[idx + 1]
    return ""


def _title_for(graphic: HtmlGraphic) -> str:
    if graphic.kind == "inline" and graphic.title and graphic.article_title:
        return f"{graphic.title.title()} — {graphic.article_title}"[:200]
    for candidate in (graphic.title, graphic.caption, graphic.article_title):
        candidate = candidate.strip()
        if len(candidate) > 3:
            return candidate[:200]
    return graphic.identifier


def _description_for(graphic: HtmlGraphic) -> str:
    bits: list[str] = []
    title = graphic.article_title.strip()
    if title:
        bits.append(f'Archived FiveThirtyEight HTML graphic bundle from "{title}".')
    else:
        bits.append("Archived FiveThirtyEight HTML graphic bundle.")

    bits.append(
        "This item includes the extracted HTML source for the original "
        "FiveThirtyEight graphic and a desktop PNG screenshot rendered from "
        "that HTML for preview and discovery."
    )

    if graphic.caption.strip():
        bits.append(f"Original caption: {graphic.caption.strip()}")

    byline = clean_byline(graphic.byline)
    if graphic.article_url:
        if title and byline:
            bits.append(
                f'Originally embedded in "{title}", an article by {byline}: '
                f"{graphic.article_url}"
            )
        elif title:
            bits.append(f'Originally embedded in "{title}": {graphic.article_url}')
        elif byline:
            bits.append(
                f"Originally embedded in an article by {byline}: {graphic.article_url}"
            )
        else:
            bits.append(f"Originally embedded in: {graphic.article_url}")

    bits.append(f"Original HTML source URL: {graphic.canonical_url}")
    bits.append(AI_DISCLOSURE)
    return "\n\n".join(bits)


def _external_identifiers_for(graphic: HtmlGraphic) -> list[str]:
    out: list[str] = []
    _append_unique(
        out, f"urn:fakethirtyeight:{graphic.bundle_kind}:{graphic.identifier}"
    )
    _append_unique(
        out,
        f"urn:fakethirtyeight:html-source-url:{graphic.canonical_url}",
    )
    if graphic.article_url:
        _append_unique(
            out,
            f"urn:fakethirtyeight:source-article-url:{graphic.article_url}",
        )
    return out


def _subjects_for(graphic: HtmlGraphic) -> list[str]:
    subjects = ["graphic", HTML_BUNDLE_SUBJECT, "FiveThirtyEight"]
    if graphic.bundle_kind not in subjects:
        subjects.insert(1, graphic.bundle_kind)
    return subjects


def _metadata_for(
    graphic: HtmlGraphic,
    *,
    collection: str,
    contributor: str = DEFAULT_CONTRIBUTOR,
) -> dict[str, str | list[str]]:
    md: dict[str, str | list[str]] = {
        "collection": collection,
        "mediatype": "image",
        "title": _title_for(graphic),
        "creator": "FiveThirtyEight",
        "contributor": contributor,
        "publisher": DEFAULT_PUBLISHER,
        "description": _description_for(graphic),
        "subject": _subjects_for(graphic),
        "source": graphic.canonical_url,
        "language": "eng",
        "external-identifier": _external_identifiers_for(graphic),
    }
    if graphic.published_at:
        md["date"] = graphic.published_at
    year = _fallback_year(graphic)
    if year:
        md["year"] = year
    if graphic.article_url:
        md["originalurl"] = graphic.article_url
    return {k: v for k, v in md.items() if v not in ("", [], None)}


def _manifest(
    *,
    enriched_path: Path = ENRICHED_FILE,
) -> list[HtmlGraphic]:
    article_meta = _load_article_meta(enriched_path)
    refs = [
        *_collect_refs(AI2HTML_REFS_FILE, "ai2html"),
        *_collect_refs(EMBED_REFS_FILE, "embed"),
    ]
    downloads = {
        "ai2html": _read_latest_rows(AI2HTML_LOG),
        "embed": _read_latest_rows(EMBED_LOG),
    }
    renders = {
        "ai2html": _read_latest_rows(AI2HTML_RENDER_LOG),
        "embed": _read_latest_rows(EMBED_RENDER_LOG),
    }
    out: list[HtmlGraphic] = []
    for ref in refs:
        canonical = ref["canonical_url"]
        bundle_kind = ref["bundle_kind"]
        download = downloads[bundle_kind].get(canonical, {})
        render = renders[bundle_kind].get(canonical, {})
        if (download.get("status") or "") != "ok" or (
            render.get("status") or ""
        ) != "ok":
            continue
        html_path = _as_path(download.get("file_path") or "")
        png_path = _as_path(render.get("render_path") or "")
        if not html_path.exists() or not png_path.exists():
            continue
        article = article_meta.get(ref.get("article_file") or "", {})
        out.append(
            HtmlGraphic(
                identifier=ref["identifier"],
                canonical_url=canonical,
                article_file=ref.get("article_file") or "",
                article_url=ref.get("article_url") or article.get("url") or "",
                title=ref.get("title") or "",
                caption=ref.get("caption") or "",
                kind=ref.get("kind") or "",
                bundle_kind=bundle_kind,
                html_path=html_path,
                png_path=png_path,
                published_at=article.get("published_at") or "",
                article_title=article.get("title") or "",
                byline=article.get("byline") or "",
            )
        )
    return out


def upload_one(
    session: _ArchiveSession,
    *,
    graphic: HtmlGraphic,
    collection: str,
    contributor: str,
    dry_run: bool,
) -> UploadResult:
    if not graphic.png_path.exists() or not graphic.html_path.exists():
        return UploadResult(
            identifier=graphic.identifier,
            canonical_url=graphic.canonical_url,
            status="error",
            error="missing PNG render or HTML source",
        )
    files = [str(graphic.png_path), str(graphic.html_path)]
    if dry_run:
        log.info(
            "DRY RUN %s: %s",
            graphic.identifier,
            ", ".join(Path(f).name for f in files),
        )
        return UploadResult(
            identifier=graphic.identifier,
            canonical_url=graphic.canonical_url,
            status="dry_run",
            files=";".join(Path(f).name for f in files),
        )

    try:
        item = session.get_item(graphic.identifier)
        responses = item.upload(
            files=files,
            metadata=_metadata_for(
                graphic, collection=collection, contributor=contributor
            ),
            retries=10,
            retries_sleep=30,
            verbose=False,
        )
        for resp in responses:
            resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return UploadResult(
            identifier=graphic.identifier,
            canonical_url=graphic.canonical_url,
            status="error",
            error=repr(exc)[:300],
        )

    return UploadResult(
        identifier=graphic.identifier,
        canonical_url=graphic.canonical_url,
        status="uploaded",
        files=";".join(Path(f).name for f in files),
    )


def upload_html_graphics(
    *,
    collection: str = DEFAULT_COLLECTION,
    contributor: str = DEFAULT_CONTRIBUTOR,
    delay: float = 0.5,
    limit: int | None = None,
    dry_run: bool = False,
    log_path: Path = UPLOAD_LOG,
    enriched_path: Path = ENRICHED_FILE,
) -> tuple[int, int, int]:
    """Upload rendered ai2html/embed graphic bundles to archive.org."""
    ensure_dirs()
    auth = ("", "") if dry_run else _load_credentials()
    done = _load_done(log_path)
    pending = [
        graphic
        for graphic in _manifest(enriched_path=enriched_path)
        if graphic.identifier not in done
    ]
    if limit is not None:
        pending = pending[:limit]
        log.info("limit=%d, processing %d", limit, len(pending))

    write_header = not log_path.exists()
    uploaded = skipped = failed = 0
    session = _archive_session(auth[0], auth[1]) if not dry_run else None
    with log_path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
        if write_header:
            writer.writeheader()
        for index, graphic in enumerate(pending, 1):
            result = upload_one(
                session,  # type: ignore[arg-type]
                graphic=graphic,
                collection=collection,
                contributor=contributor,
                dry_run=dry_run,
            )
            writer.writerow(
                {
                    "identifier": result.identifier,
                    "canonical_url": result.canonical_url,
                    "uploaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "status": result.status,
                    "files": result.files,
                    "error": result.error,
                }
            )
            fh.flush()
            if result.status == "uploaded":
                uploaded += 1
            elif result.status in {"skipped", "dry_run"}:
                skipped += 1
            else:
                failed += 1
            log.info(
                "[%d/%d] %s — %s", index, len(pending), result.status, result.identifier
            )
            if delay > 0 and not dry_run:
                time.sleep(delay)
    return uploaded, skipped, failed
