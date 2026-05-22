"""Upload each downloaded podcast MP3 to archive.org as a collection item.

One row of ``data/podcast_metadata.csv`` becomes one IA item. We upload
the MP3 and (if present) the extracted cover-art JPG, and attach the
Tier 1 + Tier 2 metadata as item fields.

Auth: same ``IA_ACCESS_KEY`` + ``IA_SECRET_KEY`` env vars that
``save_now`` uses. ``internetarchive`` reads them via its own config
mechanism; we pass them explicitly so the script doesn't depend on an
``~/.config/internetarchive/ia.ini`` having been initialized.

Resumable: each upload outcome is appended to
``data/podcast_upload_log.csv``. Identifiers already marked as
``uploaded`` are skipped on re-run; ``error`` rows get retried.

This module never runs implicitly. Invoke via ``fakethirtyeight
upload-podcasts``, and only after the IA collection has been granted
to the account.
"""

from __future__ import annotations

import csv
import logging
import os
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from internetarchive import get_session

from fakethirtyeight.download_podcasts import PODCASTS_DIR, filename_for
from fakethirtyeight.ia_metadata import year_from_date
from fakethirtyeight.paths import DATA_DIR, ensure_dirs
from fakethirtyeight.podcast_metadata import METADATA_FILE

log = logging.getLogger(__name__)

UPLOAD_LOG = DATA_DIR / "podcast_upload_log.csv"
METADATA_REPAIR_LOG = DATA_DIR / "podcast_metadata_repair_log.csv"
LOG_FIELDS = ("identifier", "uploaded_at", "status", "files", "error")
METADATA_REPAIR_LOG_FIELDS = ("identifier", "repaired_at", "status", "year", "error")

#: Default IA collection slug for the FiveThirtyEight archive.
DEFAULT_COLLECTION = "fivethirtyeight-collection"
DEFAULT_CONTRIBUTOR = "Ben Welsh"
DEFAULT_PUBLISHER = "FiveThirtyEight"
DEFAULT_SUBJECTS = ("podcast", "FiveThirtyEight")
SHOW_SUBJECTS: dict[str, tuple[str, ...]] = {
    "elections": ("FiveThirtyEight Elections", "elections", "politics"),
    "politics": ("FiveThirtyEight Politics", "politics"),
    "hot-takedown": ("Hot Takedown", "sports"),
    "podcast-19": (
        "FiveThirtyEight: PODCAST-19",
        "PODCAST-19",
        "coronavirus",
        "COVID-19",
    ),
    "whats-the-point": ("What's The Point", "data journalism"),
    "model-conversations": ("Model Conversations",),
    "ratings": ("Ratings", "film"),
    "the-lab": ("The Lab",),
    "gerrymandering": ("The Gerrymandering Project", "gerrymandering"),
}


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
        request_kwargs: dict[str, str] | None = None,
    ) -> Iterable[_UploadResponse]: ...

    def modify_metadata(
        self,
        metadata: dict[str, str],
        request_kwargs: dict[str, str] | None = None,
    ) -> _UploadResponse: ...


class _ArchiveSession(Protocol):
    def get_item(self, identifier: str) -> _ArchiveItem: ...


@dataclass(slots=True, frozen=True)
class UploadResult:
    identifier: str
    status: str  # 'uploaded' | 'dry_run' | 'skipped_missing' | 'error'
    files: tuple[str, ...] = ()
    error: str = ""


def _load_credentials() -> tuple[str, str]:
    access = os.environ.get("IA_ACCESS_KEY")
    secret = os.environ.get("IA_SECRET_KEY")
    if not access or not secret:
        msg = (
            "Set IA_ACCESS_KEY and IA_SECRET_KEY env vars first. "
            "Same keys as save-podcasts uses."
        )
        raise RuntimeError(msg)
    return access, secret


def _configure_ca_bundle() -> None:
    ca_bundle = _ca_bundle_path()
    if not ca_bundle:
        return
    os.environ.setdefault("REQUESTS_CA_BUNDLE", ca_bundle)
    os.environ.setdefault("SSL_CERT_FILE", ca_bundle)


def _ia_request_kwargs() -> dict[str, str] | None:
    ca_bundle = _ca_bundle_path()
    return {"verify": ca_bundle} if ca_bundle else None


def _ca_bundle_path() -> str:
    env_bundle = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    if env_bundle:
        return env_bundle
    corporate_bundle = Path("/Users/U6122976/final-certs.pem")
    if corporate_bundle.exists():
        return str(corporate_bundle)
    try:
        import certifi
    except ImportError:
        return ""
    return certifi.where()


def _load_done(log_path: Path) -> set[str]:
    """Identifiers that previously uploaded successfully.

    Dry-run log rows are deliberately ignored so a rehearsal never poisons
    the resume state for the real upload.
    """
    if not log_path.exists():
        return set()
    done: set[str] = set()
    with log_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (row.get("status") or "") == "uploaded" and row.get("identifier"):
                done.add(row["identifier"])
    return done


def _metadata_for_row(
    row: dict[str, str], *, collection: str, contributor: str = DEFAULT_CONTRIBUTOR
) -> dict[str, str | list[str]]:
    """Build the IA item metadata dict from one CSV row."""
    external_identifiers = _external_identifiers_for(row)
    date = row.get("date") or ""
    md: dict[str, str | list[str]] = {
        "collection": collection,
        "mediatype": row.get("mediatype") or "audio",
        "title": row.get("title") or row.get("identifier", ""),
        "creator": row.get("creator") or "FiveThirtyEight",
        "contributor": contributor,
        "publisher": DEFAULT_PUBLISHER,
        "date": date,
        "year": year_from_date(date),
        "description": row.get("description") or "",
        "subject": _subjects_for(row),
        "source": row.get("source") or row.get("mp3_url", ""),
        "language": "eng",
    }
    if row.get("show"):
        md["album"] = row["show"]
    if external_identifiers:
        md["external-identifier"] = external_identifiers
    if row.get("source_article_url"):
        md["originalurl"] = row["source_article_url"]
    elif row.get("player_url"):
        md["originalurl"] = row["player_url"]
    # Drop empty values — IA rejects empty strings in some fields.
    return {k: v for k, v in md.items() if v not in ("", [], None)}


def _subjects_for(row: dict[str, str]) -> list[str]:
    """Build repeatable IA subject tags from trusted podcast metadata."""
    subjects: list[str] = []
    for subject in DEFAULT_SUBJECTS:
        _append_unique(subjects, subject)
    for subject in (row.get("subject") or "").split(";"):
        _append_unique(subjects, subject)

    show = (row.get("show") or "").strip()
    if show:
        _append_unique(subjects, show)

    show_slug = (row.get("show_slug") or "").strip()
    for subject in SHOW_SUBJECTS.get(show_slug, ()):
        _append_unique(subjects, subject)
    return subjects


def _external_identifiers_for(row: dict[str, str]) -> list[str]:
    """Build repeatable external identifiers for source-system lookups."""
    out: list[str] = []
    megaphone_id = (row.get("megaphone_id") or "").strip().upper()
    if megaphone_id:
        _append_unique(out, f"urn:megaphone:{megaphone_id}")

    mp3_url = (row.get("mp3_url") or "").strip()
    if mp3_url:
        _append_unique(out, f"urn:fakethirtyeight:podcast-audio-url:{mp3_url}")

    player_url = (row.get("player_url") or "").strip()
    if player_url:
        _append_unique(out, f"urn:fakethirtyeight:podcast-player-url:{player_url}")

    source_article_url = (row.get("source_article_url") or "").strip()
    if source_article_url:
        _append_unique(
            out, f"urn:fakethirtyeight:source-article-url:{source_article_url}"
        )
    return out


def _append_unique(items: list[str], value: str) -> None:
    value = value.strip()
    if value and value not in items:
        items.append(value)


def _files_for_row(row: dict[str, str], *, podcasts_dir: Path) -> list[Path]:
    """Local files to attach to the IA item — MP3 first, then thumbnail."""
    files: list[Path] = []
    mp3 = podcasts_dir / filename_for(row["mp3_url"])
    if mp3.exists():
        files.append(mp3)
    thumb = row.get("thumbnail") or ""
    if thumb:
        thumb_path = Path(thumb)
        if not thumb_path.is_absolute():
            # CSV stores it relative to the repo root.
            thumb_path = DATA_DIR.parent / thumb_path
        if thumb_path.exists() and _is_supported_image(thumb_path):
            files.append(thumb_path)
    return files


def _is_supported_image(path: Path) -> bool:
    """Return whether ``path`` starts with an IA-acceptable image signature."""
    with path.open("rb") as fh:
        head = fh.read(12)
    return head.startswith(
        (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a")
    )


def _duplicate_pending_identifiers(
    rows: list[dict[str, str]], *, podcasts_dir: Path
) -> dict[str, int]:
    """Return duplicate identifiers among rows with local uploadable files."""
    counts: dict[str, int] = {}
    for row in rows:
        identifier = row.get("identifier") or ""
        if not identifier:
            continue
        if not _files_for_row(row, podcasts_dir=podcasts_dir):
            continue
        counts[identifier] = counts.get(identifier, 0) + 1
    return {identifier: n for identifier, n in counts.items() if n > 1}


def upload_one(
    session: _ArchiveSession | None,
    row: dict[str, str],
    *,
    collection: str,
    contributor: str,
    podcasts_dir: Path,
    dry_run: bool,
) -> UploadResult:
    """Upload one CSV row's files + metadata as a single IA item."""
    identifier = row.get("identifier") or ""
    if not identifier:
        return UploadResult(identifier="", status="error", error="missing identifier")

    files = _files_for_row(row, podcasts_dir=podcasts_dir)
    if not files:
        return UploadResult(
            identifier=identifier,
            status="skipped_missing",
            error="no local files (mp3 not downloaded?)",
        )

    metadata = _metadata_for_row(row, collection=collection, contributor=contributor)

    if dry_run:
        log.info(
            "DRY RUN %s: %d file(s), %d metadata field(s)",
            identifier,
            len(files),
            len(metadata),
        )
        return UploadResult(
            identifier=identifier,
            status="dry_run",
            files=tuple(f.name for f in files),
        )

    if session is None:
        return UploadResult(
            identifier=identifier,
            status="error",
            error="archive.org session not initialized",
        )

    try:
        item = session.get_item(identifier)
        responses = item.upload(
            files=[str(f) for f in files],
            metadata=metadata,
            retries=10,
            retries_sleep=30,
            verbose=False,
            request_kwargs=_ia_request_kwargs(),
        )
        for resp in responses:
            resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return UploadResult(
            identifier=identifier, status="error", error=repr(exc)[:300]
        )

    return UploadResult(
        identifier=identifier,
        status="uploaded",
        files=tuple(f.name for f in files),
    )


def upload_podcasts(
    *,
    collection: str = DEFAULT_COLLECTION,
    contributor: str = DEFAULT_CONTRIBUTOR,
    delay: float = 1.0,
    limit: int | None = None,
    dry_run: bool = False,
    csv_path: Path = METADATA_FILE,
    podcasts_dir: Path = PODCASTS_DIR,
    log_path: Path = UPLOAD_LOG,
) -> tuple[int, int, int]:
    """Upload every metadata row's MP3 + thumbnail to ``collection``.

    Returns ``(uploaded, skipped, failed)``. Skips identifiers that
    previously logged as ``uploaded``. ``dry_run=True`` writes ``dry_run``
    rows to the log but never marks them as uploaded, so a later real run
    still processes them.
    """
    ensure_dirs()

    if not csv_path.exists():
        msg = (
            f"metadata file not found: {csv_path}. "
            "Run `fakethirtyeight podcast-metadata` first."
        )
        raise FileNotFoundError(msg)

    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    done = _load_done(log_path)
    pending = [r for r in rows if r.get("identifier") not in done]
    log.info(
        "%d rows total; %d already uploaded; %d to upload",
        len(rows),
        len(done),
        len(pending),
    )
    if limit is not None:
        pending = pending[:limit]
        log.info("limit=%d, processing %d", limit, len(pending))

    duplicates = _duplicate_pending_identifiers(pending, podcasts_dir=podcasts_dir)
    if duplicates:
        sample = ", ".join(
            f"{identifier} ({count} rows)"
            for identifier, count in sorted(duplicates.items())[:10]
        )
        msg = (
            "duplicate archive.org identifiers among uploadable podcast rows: "
            f"{sample}. Regenerate podcast metadata before uploading."
        )
        raise RuntimeError(msg)

    if dry_run:
        session = None
    else:
        _configure_ca_bundle()
        auth = _load_credentials()
        session = get_session(config={"s3": {"access": auth[0], "secret": auth[1]}})

    write_header = not log_path.exists()
    uploaded = skipped = failed = 0

    with log_path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
        if write_header:
            writer.writeheader()

        for i, row in enumerate(pending, 1):
            result = upload_one(
                session,
                row,
                collection=collection,
                contributor=contributor,
                podcasts_dir=podcasts_dir,
                dry_run=dry_run,
            )
            writer.writerow(
                {
                    "identifier": result.identifier,
                    "uploaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "status": result.status,
                    "files": "|".join(result.files),
                    "error": result.error,
                }
            )
            fh.flush()

            if result.status == "uploaded":
                uploaded += 1
            elif result.status in {"dry_run", "skipped_missing"}:
                skipped += 1
            else:
                failed += 1

            log.info(
                "[%d/%d] %s — %s",
                i,
                len(pending),
                result.status,
                result.identifier,
            )
            if delay > 0 and not dry_run:
                time.sleep(delay)

    return uploaded, skipped, failed


def repair_podcast_years(
    *,
    csv_path: Path = METADATA_FILE,
    upload_log_path: Path = UPLOAD_LOG,
    repair_log_path: Path = METADATA_REPAIR_LOG,
    delay: float = 1.0,
    limit: int | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> tuple[int, int, int]:
    """Backfill archive.org ``year`` metadata for uploaded podcast items."""
    ensure_dirs()
    if not csv_path.exists():
        msg = (
            f"metadata file not found: {csv_path}. "
            "Run `fakethirtyeight podcast-metadata` first."
        )
        raise FileNotFoundError(msg)

    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    uploaded = _load_done(upload_log_path)
    repaired_done = set() if force else _load_repaired(repair_log_path)
    pending = [
        r
        for r in rows
        if r.get("identifier") in uploaded
        and r.get("identifier") not in repaired_done
        and year_from_date(r.get("date") or "")
    ]
    skipped = len(rows) - len(pending)
    if limit is not None:
        pending = pending[:limit]

    if dry_run:
        session = None
    else:
        _configure_ca_bundle()
        auth = _load_credentials()
        session = get_session(config={"s3": {"access": auth[0], "secret": auth[1]}})

    write_header = not repair_log_path.exists()
    repaired = failed = 0
    with repair_log_path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=METADATA_REPAIR_LOG_FIELDS)
        if write_header:
            writer.writeheader()

        for row in pending:
            result = repair_one_podcast_year(
                session,
                row,
                year=year_from_date(row.get("date") or ""),
                dry_run=dry_run,
            )
            writer.writerow(_metadata_repair_log_row(result))
            fh.flush()

            if result.status == "repaired":
                repaired += 1
            elif result.status == "dry_run":
                skipped += 1
            else:
                failed += 1

            log.info("%s year=%s — %s", result.status, result.year, result.identifier)
            if delay > 0 and not dry_run:
                time.sleep(delay)

    return repaired, skipped, failed


@dataclass(slots=True, frozen=True)
class MetadataRepairResult:
    identifier: str
    status: str  # 'repaired' | 'dry_run' | 'skipped_missing' | 'error'
    year: str = ""
    error: str = ""


def repair_one_podcast_year(
    session: _ArchiveSession | None,
    row: dict[str, str],
    *,
    year: str,
    dry_run: bool,
) -> MetadataRepairResult:
    """Patch one uploaded podcast item's IA ``year`` metadata field."""
    identifier = row.get("identifier") or ""
    if not identifier:
        return MetadataRepairResult(
            identifier="", status="error", year=year, error="missing identifier"
        )
    if not year:
        return MetadataRepairResult(
            identifier=identifier,
            status="skipped_missing",
            error="row has no year",
        )
    if dry_run:
        return MetadataRepairResult(identifier=identifier, status="dry_run", year=year)
    if session is None:
        return MetadataRepairResult(
            identifier=identifier,
            status="error",
            year=year,
            error="archive.org session not initialized",
        )

    try:
        item = session.get_item(identifier)
        resp = item.modify_metadata(
            {"year": year},
            request_kwargs=_ia_request_kwargs(),
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        if _is_noop_metadata_error(exc):
            return MetadataRepairResult(
                identifier=identifier, status="repaired", year=year
            )
        return MetadataRepairResult(
            identifier=identifier,
            status="error",
            year=year,
            error=repr(exc)[:300],
        )
    return MetadataRepairResult(identifier=identifier, status="repaired", year=year)


def _is_noop_metadata_error(exc: Exception) -> bool:
    """True when archive.org says the requested metadata is already present."""
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    text = getattr(response, "text", "")
    return status_code == 400 and "no changes to _meta.xml" in text


def _load_repaired(log_path: Path) -> set[str]:
    if not log_path.exists():
        return set()
    done: set[str] = set()
    with log_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (row.get("status") or "") == "repaired" and row.get("identifier"):
                done.add(row["identifier"])
    return done


def _metadata_repair_log_row(result: MetadataRepairResult) -> dict[str, str]:
    return {
        "identifier": result.identifier,
        "repaired_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": result.status,
        "year": result.year,
        "error": result.error,
    }
