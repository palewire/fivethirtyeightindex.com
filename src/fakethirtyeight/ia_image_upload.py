"""Upload each downloaded article image to archive.org as a collection item.

One row of ``data/image_download_log.csv`` becomes one IA item. We
upload the image file and attach metadata derived by joining three
tables:

* :data:`IMAGE_LOG` — what's on disk + its content type
* :data:`IMAGE_REFS_FILE` — which article each image was embedded in,
  with its alt text and caption
* :data:`CAPTIONS_FILE` — optional vision labels and descriptions
* :data:`ENRICHED_FILE` — that article's title, byline, and publish date

Auth: same ``IA_ACCESS_KEY`` + ``IA_SECRET_KEY`` env vars used by the
podcast uploader.

Resumable: each outcome appended to ``data/image_upload_log.csv``.
Identifiers already marked ``uploaded`` are skipped on re-run;
``error`` rows get retried.

Collection: defaults to the curated FiveThirtyEight collection. Pass
``--collection <slug>`` to override it for a test collection.

This module never runs implicitly. Invoke via ``fakethirtyeight
upload-images``.
"""

from __future__ import annotations

import csv
import hashlib
import logging
import os
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, TextIO

from internetarchive import get_session

from fakethirtyeight.caption import (
    CAPTIONS_FILE,
    IN_SCOPE_AI_CATEGORIES,
    infer_caption_category,
)
from fakethirtyeight.enrich import ENRICHED_FILE
from fakethirtyeight.ia_metadata import year_from_date
from fakethirtyeight.images import (
    IMAGE_LOG,
    IMAGE_REFS_FILE,
    _logged_file_is_image,
    identifier_for,
)
from fakethirtyeight.paths import DATA_DIR, ensure_dirs
from fakethirtyeight.site_data import clean_byline

__all__ = ["identifier_for", "upload_images"]

log = logging.getLogger(__name__)

UPLOAD_LOG = DATA_DIR / "image_upload_log.csv"
LOG_FIELDS = (
    "identifier",
    "canonical_url",
    "uploaded_at",
    "status",
    "file",
    "error",
)

#: Default IA collection slug for the FiveThirtyEight archive.
DEFAULT_COLLECTION = "fivethirtyeight-collection"
DEFAULT_PUBLISHER = "FiveThirtyEight"

#: Per-category subject tags. Most uploaded items also carry the
#: catch-all ``graphic`` and the brand ``FiveThirtyEight`` subjects so
#: search-by-tag works even if a future caller drops the category.
#: ``category`` values come from :func:`fakethirtyeight.images._categorize`.
_CATEGORY_SUBJECTS: dict[str, tuple[str, ...]] = {
    "chart": ("chart",),
    "map": ("map",),
    "table": ("table",),
    "chart-screenshot": ("chart",),
    "infographic": (),
    "diagram": (),
    "chess-diagram": ("chess",),
    "artistic-illustration": ("illustration",),
    "featured-image": ("featured-image",),
    "banner": ("banner",),
    "screenshot": ("screenshot",),
    "headshot": ("headshot",),
}

#: Fallback subjects when no category is set on the row (shouldn't
#: happen post-extraction, but kept as a safety net).
DEFAULT_SUBJECTS = ("graphic", "FiveThirtyEight")

#: Person archiving these items — set as ``contributor`` on each IA
#: item so the upload is attributable on archive.org even though the
#: ``creator`` field holds the original author of the host article.
DEFAULT_CONTRIBUTOR = "Ben Welsh"
AI_DISCLOSURE = (
    "AI disclosure: Image descriptions and visible-text extraction were generated "
    "using Sonnet 4.6 by Anthropic."
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
    verify: bool | str

    def get_item(self, identifier: str) -> _ArchiveItem: ...


def _configure_session_tls(session: _ArchiveSession) -> None:
    """Teach internetarchive's requests session about local corporate CAs."""
    ca_bundle = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    if ca_bundle:
        session.verify = ca_bundle


def _archive_session(access: str, secret: str) -> _ArchiveSession:
    session = get_session(config={"s3": {"access": access, "secret": secret}})
    _configure_session_tls(session)
    return session


@dataclass(slots=True, frozen=True)
class UploadResult:
    identifier: str
    canonical_url: str
    status: str  # 'uploaded' | 'skipped' | 'error'
    file: str = ""
    error: str = ""


# ---------------------------------------------------------------------------
# Cross-table joining
# ---------------------------------------------------------------------------


def _load_article_meta(
    refs_path: Path, enriched_path: Path
) -> dict[str, dict[str, str]]:
    """``canonical_url → {alt, caption, article_url, title, byline, date}``.

    Joins the image references CSV (which knows which article each
    image was in + the local alt/caption text) with the enriched
    articles CSV (which knows the article's title, byline, and date).

    When multiple articles embedded the same image, we keep the one
    with the richest metadata (longest title, present byline).
    """
    # Load enriched.csv → file path → article record. Article HTML lives
    # at data/articles/<year>/<hash>.html.gz; image_references stores the
    # same path. So we key by that file path.
    article_by_file: dict[str, dict[str, str]] = {}
    with enriched_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            # Reconstruct the article's on-disk file path from
            # url + snapshot_timestamp the same way articles.path_for does.
            url = (row.get("url") or "").strip()
            ts = (row.get("snapshot_timestamp") or "").strip()
            if not url or not ts:
                continue
            year = ts[:4]
            uhash = hashlib.sha1(
                url.encode("utf-8"), usedforsecurity=False
            ).hexdigest()[:16]
            key = f"data/articles/{year}/{uhash}.html.gz"
            article_by_file[key] = row

    out: dict[str, dict[str, str]] = {}
    with refs_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            cu = (row.get("canonical_url") or "").strip()
            if not cu:
                continue
            article = article_by_file.get(row.get("article_file") or "", {})
            rec = {
                "alt": row.get("alt") or "",
                "caption": row.get("caption") or "",
                "category": row.get("category") or "chart",
                "article_url": row.get("article_url") or article.get("url") or "",
                "article_title": article.get("title") or "",
                "byline": article.get("byline") or "",
                "published_at": article.get("published_at") or "",
            }
            # Keep the richest record per canonical URL.
            prev = out.get(cu)
            if prev is None:
                out[cu] = rec
                continue
            if len(rec["caption"]) > len(prev["caption"]) or (
                rec["byline"] and not prev["byline"]
            ):
                out[cu] = rec
    return out


def _load_captions(captions_path: Path) -> dict[str, dict[str, str]]:
    """``identifier → vision metadata`` from image_captions.csv."""
    if not captions_path.exists():
        return {}
    out: dict[str, dict[str, str]] = {}
    with captions_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ident = (row.get("identifier") or "").strip()
            if not ident or (row.get("status") or "") != "ok":
                continue
            out[ident] = {
                "ai_category": infer_caption_category(
                    row.get("ai_category") or "",
                    title=row.get("ai_title") or "",
                    description=row.get("ai_description") or "",
                    text=row.get("ai_text") or "",
                ),
                "ai_description": row.get("ai_description") or "",
                "ai_title": row.get("ai_title") or "",
                "ai_text": row.get("ai_text") or "",
            }
    return out


def _merge_caption(
    rec: dict[str, str], caption: dict[str, str] | None
) -> dict[str, str]:
    """Overlay vision classification on the metadata row when present."""
    if not caption:
        return rec
    out = dict(rec)
    for key in ("ai_category", "ai_description", "ai_title", "ai_text"):
        if caption.get(key):
            out[key] = caption[key]
    if out.get("ai_category"):
        out["category"] = out["ai_category"]
    return out


def _in_scope_for_upload(rec: dict[str, str]) -> bool:
    """Return True when an image belongs in the chart/map/table archive."""
    ai_category = (rec.get("ai_category") or "").strip()
    if ai_category:
        return ai_category in IN_SCOPE_AI_CATEGORIES
    category = (rec.get("category") or "").strip()
    # Screenshots are ambiguous until the vision pass says they are a
    # data visualization. Filename-classified charts can still upload
    # without AI captions.
    return category in {
        "artistic-illustration",
        "chart",
        "map",
        "table",
        "chart-screenshot",
        "infographic",
        "diagram",
        "chess-diagram",
    }


def _pending_upload_rows(
    rows: list[dict[str, str]],
    *,
    done: set[str],
    article_meta: dict[str, dict[str, str]],
    captions: dict[str, dict[str, str]],
) -> list[tuple[dict[str, str], dict[str, str]]]:
    """Return not-yet-uploaded rows that are in scope for IA image upload."""
    pending: list[tuple[dict[str, str], dict[str, str]]] = []
    for row in rows:
        canonical_url = row["canonical_url"]
        identifier = identifier_for(canonical_url)
        if identifier in done:
            continue
        rec = _merge_caption(
            article_meta.get(canonical_url, {}),
            captions.get(identifier),
        )
        if _in_scope_for_upload(rec):
            pending.append((row, rec))
    return pending


# ---------------------------------------------------------------------------
# Metadata construction
# ---------------------------------------------------------------------------


def _subjects_for(rec: dict[str, str]) -> list[str]:
    """Subject tag list for one item — category-specific + brand tags."""
    cat = (rec.get("category") or "").strip()
    out: list[str] = []
    out.extend(_CATEGORY_SUBJECTS.get(cat, ()))
    # Always include the catch-all asset-kind tag + the brand so a
    # collection-wide query (``subject:graphic``, ``subject:FiveThirtyEight``)
    # still surfaces every item.
    if cat not in {"artistic-illustration", "chess-diagram"} and "graphic" not in out:
        out.append("graphic")
    out.append("FiveThirtyEight")
    return out


def _append_unique(values: list[str], value: str) -> None:
    value = value.strip()
    if value and value not in values:
        values.append(value)


def _external_identifiers_for(canonical_url: str, rec: dict[str, str]) -> list[str]:
    """Build repeatable external identifiers for source-system lookups."""
    out: list[str] = []
    _append_unique(out, f"urn:fakethirtyeight:image:{identifier_for(canonical_url)}")
    _append_unique(out, f"urn:fakethirtyeight:image-source-url:{canonical_url}")
    article_url = (rec.get("article_url") or "").strip()
    if article_url:
        _append_unique(out, f"urn:fakethirtyeight:source-article-url:{article_url}")
    return out


def _title_for(rec: dict[str, str], canonical_url: str) -> str:
    """Pick the best human-readable title for this image."""
    for candidate in (rec.get("ai_title"), rec.get("caption"), rec.get("alt")):
        candidate = (candidate or "").strip()
        if candidate and len(candidate) > 3:
            return candidate[:200]
    # Filename fallback.
    name = Path(canonical_url).name
    if name and "." in name:
        return name
    return identifier_for(canonical_url)


def _description_for(rec: dict[str, str], canonical_url: str) -> str:
    """Build a plain-text description from caption + provenance.

    The article byline lives here (rather than as ``creator``) because
    the writer of the host article usually isn't the person who made
    the image. Treating the byline as provenance — "from an article by
    X" — is more honest than crediting them with the graphic.
    """
    bits: list[str] = []
    ai_description = (rec.get("ai_description") or "").strip()
    if ai_description:
        bits.append(f"AI-generated image description: {ai_description}")
    caption = (rec.get("caption") or "").strip()
    if caption and caption != ai_description:
        bits.append(f"Original caption: {caption}")
    ai_text = (rec.get("ai_text") or "").strip()
    if ai_text:
        bits.append(f"AI-extracted visible text:\n{ai_text}")
    article_url = (rec.get("article_url") or "").strip()
    article_title = (rec.get("article_title") or "").strip()
    byline = clean_byline(rec.get("byline") or "")
    if article_url:
        if article_title and byline:
            bits.append(
                f'Originally embedded in "{article_title}", an article by '
                f"{byline}: {article_url}"
            )
        elif article_title:
            bits.append(f'Originally embedded in "{article_title}": {article_url}')
        elif byline:
            bits.append(f"From an article by {byline}: {article_url}")
        else:
            bits.append(f"Originally embedded in: {article_url}")
    elif byline:
        bits.append(f"From an article by {byline}.")
    bits.append(f"Original source URL: {canonical_url}")
    if ai_description or ai_text:
        bits.append(AI_DISCLOSURE)
    return "\n\n".join(bits)


def _metadata_for(
    canonical_url: str,
    rec: dict[str, str],
    *,
    collection: str,
    contributor: str = DEFAULT_CONTRIBUTOR,
) -> dict[str, str | list[str]]:
    md: dict[str, str | list[str]] = {
        "collection": collection,
        "mediatype": "image",
        "title": _title_for(rec, canonical_url),
        # Always credit the publication — the host-article writer
        # usually isn't the graphic's author, so we surface the byline
        # in `description` instead.
        "creator": "FiveThirtyEight",
        "contributor": contributor,
        "publisher": DEFAULT_PUBLISHER,
        "description": _description_for(rec, canonical_url),
        "subject": _subjects_for(rec),
        "source": canonical_url,
        "language": "eng",
        "external-identifier": _external_identifiers_for(canonical_url, rec),
    }
    date = (rec.get("published_at") or "").strip()
    if date:
        # IA accepts ISO 8601 or YYYY-MM-DD; published_at is ISO already.
        md["date"] = date
        md["year"] = year_from_date(date)
    article_url = (rec.get("article_url") or "").strip()
    if article_url:
        md["originalurl"] = article_url
    return {k: v for k, v in md.items() if v not in ("", [], None)}


# ---------------------------------------------------------------------------
# Upload driver
# ---------------------------------------------------------------------------


def _load_credentials() -> tuple[str, str]:
    access = os.environ.get("IA_ACCESS_KEY")
    secret = os.environ.get("IA_SECRET_KEY")
    if not access or not secret:
        msg = (
            "Set IA_ACCESS_KEY and IA_SECRET_KEY env vars first. "
            "Same keys as upload-podcasts uses."
        )
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


def _has_uploadable_content_type(row: dict[str, str]) -> bool:
    """Return False for clear HTML/text downloads saved as image files."""
    content_type = (row.get("content_type") or "").split(";", 1)[0].strip().lower()
    if content_type.startswith("image/"):
        return True
    if not content_type or content_type == "application/octet-stream":
        return True
    return not (
        content_type.startswith("text/")
        or content_type in {"application/html", "application/xhtml+xml"}
    )


def _iter_image_rows(image_log_path: Path) -> Iterable[dict[str, str]]:
    """Yield rows from the image download log that successfully saved a file."""
    with image_log_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (row.get("status") or "") != "ok":
                continue
            if not (row.get("file_path") or "").strip():
                continue
            if not _has_uploadable_content_type(row):
                continue
            if not _logged_file_is_image(row):
                continue
            yield row


def upload_one(
    session: _ArchiveSession,
    *,
    canonical_url: str,
    file_path: Path,
    rec: dict[str, str],
    collection: str,
    contributor: str,
    dry_run: bool,
) -> UploadResult:
    """Upload one image as a single-file IA item."""
    identifier = identifier_for(canonical_url)
    if not file_path.exists():
        return UploadResult(
            identifier=identifier,
            canonical_url=canonical_url,
            status="error",
            error=f"local file missing: {file_path}",
        )
    if not _in_scope_for_upload(rec):
        return UploadResult(
            identifier=identifier,
            canonical_url=canonical_url,
            status="skipped",
            error=f"out-of-scope category: {rec.get('category') or 'unknown'}",
        )

    metadata = _metadata_for(
        canonical_url, rec, collection=collection, contributor=contributor
    )

    if dry_run:
        log.info(
            "DRY RUN %s: 1 file, %d metadata field(s)",
            identifier,
            len(metadata),
        )
        return UploadResult(
            identifier=identifier,
            canonical_url=canonical_url,
            status="uploaded",
            file=file_path.name,
        )

    try:
        item = session.get_item(identifier)
        responses = item.upload(
            files=[str(file_path)],
            metadata=metadata,
            retries=10,
            retries_sleep=30,
            verbose=False,
        )
        for resp in responses:
            resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return UploadResult(
            identifier=identifier,
            canonical_url=canonical_url,
            status="error",
            error=repr(exc)[:300],
        )

    return UploadResult(
        identifier=identifier,
        canonical_url=canonical_url,
        status="uploaded",
        file=file_path.name,
    )


def upload_images(
    *,
    collection: str = DEFAULT_COLLECTION,
    contributor: str = DEFAULT_CONTRIBUTOR,
    delay: float = 0.5,
    workers: int = 1,
    limit: int | None = None,
    dry_run: bool = False,
    image_log_path: Path = IMAGE_LOG,
    refs_path: Path = IMAGE_REFS_FILE,
    enriched_path: Path = ENRICHED_FILE,
    captions_path: Path = CAPTIONS_FILE,
    log_path: Path = UPLOAD_LOG,
) -> tuple[int, int, int]:
    """Upload every downloaded image's file + metadata to ``collection``.

    Returns ``(uploaded, skipped, failed)``. Skips identifiers that
    previously logged as ``uploaded``. ``dry_run=True`` walks the
    pipeline and writes the log as if uploads succeeded but never hits
    archive.org.
    """
    auth = _load_credentials()
    ensure_dirs()

    for path, label in (
        (image_log_path, "image download log"),
        (refs_path, "image references"),
        (enriched_path, "enriched articles"),
    ):
        if not path.exists():
            msg = (
                f"{label} not found: {path}. Run the upstream commands first "
                "(extract-images / download-images / enrich)."
            )
            raise FileNotFoundError(msg)

    log.info("loading article metadata join …")
    article_meta = _load_article_meta(refs_path, enriched_path)
    log.info("joined metadata for %d unique images", len(article_meta))
    captions = _load_captions(captions_path)
    if captions:
        log.info("loaded vision captions for %d images", len(captions))

    done = _load_done(log_path)
    rows = list(_iter_image_rows(image_log_path))
    not_done = [r for r in rows if identifier_for(r["canonical_url"]) not in done]
    pending = _pending_upload_rows(
        rows,
        done=done,
        article_meta=article_meta,
        captions=captions,
    )
    excluded = len(not_done) - len(pending)
    log.info(
        "%d images on disk; %d already uploaded; %d out of scope; %d to upload",
        len(rows),
        len(done),
        excluded,
        len(pending),
    )
    if limit is not None:
        pending = pending[:limit]
        log.info("limit=%d, processing %d", limit, len(pending))

    write_header = not log_path.exists()
    uploaded = skipped = failed = 0

    def run_upload(row: dict[str, str], rec: dict[str, str]) -> UploadResult:
        canonical_url = row["canonical_url"]
        file_path = DATA_DIR.parent / row["file_path"]
        session = _archive_session(auth[0], auth[1])
        return upload_one(
            session,
            canonical_url=canonical_url,
            file_path=file_path,
            rec=rec,
            collection=collection,
            contributor=contributor,
            dry_run=dry_run,
        )

    def record_result(
        writer: csv.DictWriter,
        fh: TextIO,
        result: UploadResult,
        *,
        index: int,
        total: int,
    ) -> None:
        nonlocal uploaded, skipped, failed
        writer.writerow(
            {
                "identifier": result.identifier,
                "canonical_url": result.canonical_url,
                "uploaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "status": result.status,
                "file": result.file,
                "error": result.error,
            }
        )
        fh.flush()

        if result.status == "uploaded":
            uploaded += 1
        elif result.status == "skipped":
            skipped += 1
        else:
            failed += 1

        log.info(
            "[%d/%d] %s — %s",
            index,
            total,
            result.status,
            result.identifier,
        )

    with log_path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
        if write_header:
            writer.writeheader()

        if workers <= 1:
            session = _archive_session(auth[0], auth[1])
            for i, (row, rec) in enumerate(pending, 1):
                canonical_url = row["canonical_url"]
                file_path = DATA_DIR.parent / row["file_path"]
                result = upload_one(
                    session,
                    canonical_url=canonical_url,
                    file_path=file_path,
                    rec=rec,
                    collection=collection,
                    contributor=contributor,
                    dry_run=dry_run,
                )
                record_result(writer, fh, result, index=i, total=len(pending))
                if delay > 0 and not dry_run:
                    time.sleep(delay)
        else:
            log.info("uploading with %d worker(s)", workers)
            with ThreadPoolExecutor(max_workers=workers) as executor:
                future_to_index = {
                    executor.submit(run_upload, row, rec): i
                    for i, (row, rec) in enumerate(pending, 1)
                }
                for completed, future in enumerate(as_completed(future_to_index), 1):
                    index = future_to_index[future]
                    try:
                        result = future.result()
                    except Exception as exc:  # noqa: BLE001
                        row, _ = pending[index - 1]
                        result = UploadResult(
                            identifier=identifier_for(row["canonical_url"]),
                            canonical_url=row["canonical_url"],
                            status="error",
                            error=repr(exc)[:300],
                        )
                    record_result(
                        writer,
                        fh,
                        result,
                        index=completed,
                        total=len(pending),
                    )

    return uploaded, skipped, failed
