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
from fakethirtyeight.paths import DATA_DIR, ensure_dirs
from fakethirtyeight.podcast_metadata import METADATA_FILE

log = logging.getLogger(__name__)

UPLOAD_LOG = DATA_DIR / "podcast_upload_log.csv"
LOG_FIELDS = ("identifier", "uploaded_at", "status", "files", "error")

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
    ) -> Iterable[_UploadResponse]: ...


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
    md: dict[str, str | list[str]] = {
        "collection": collection,
        "mediatype": row.get("mediatype") or "audio",
        "title": row.get("title") or row.get("identifier", ""),
        "creator": row.get("creator") or "FiveThirtyEight",
        "contributor": contributor,
        "publisher": DEFAULT_PUBLISHER,
        "date": row.get("date") or "",
        "description": row.get("description") or "",
        "subject": _subjects_for(row),
        "source": row.get("source") or row.get("mp3_url", ""),
        "language": "eng",
    }
    if row.get("show"):
        md["album"] = row["show"]
    if external_identifiers:
        md["external-identifier"] = external_identifiers
    if row.get("player_url"):
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
        if thumb_path.exists():
            files.append(thumb_path)
    return files


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
