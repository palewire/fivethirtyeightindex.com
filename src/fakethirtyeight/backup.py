"""Create checksum manifests and compressed backups of preservation artifacts."""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import tarfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_BACKUP_SOURCES: tuple[str, ...] = (
    "data/*.csv",
    "data/*.json",
    "data/*.html",
    "data/articles",
    "data/images",
    "data/ai2html",
    "data/ai2html_renders",
    "data/embeds",
    "data/embed_renders",
    "data/dataset_bundles",
    "data/podcasts",
    "data/podcast_thumbnails",
    "web/static/data",
)

EXCLUDED_DEFAULT_PATHS: tuple[str, ...] = (
    "data/index.csv",
    "data/shards",
)


@dataclass(frozen=True)
class BackupFile:
    """One file included in a backup manifest."""

    path: str
    size: int
    mtime: str
    sha256: str


@dataclass(frozen=True)
class BackupResult:
    """Paths and counts produced by a backup bundle run."""

    archive_path: Path | None
    manifest_json_path: Path
    manifest_csv_path: Path
    file_count: int
    total_bytes: int


def _has_glob(source: str) -> bool:
    return any(c in source for c in "*?[]")


def _is_excluded(path: Path, excluded: set[Path]) -> bool:
    return any(path == item or item in path.parents for item in excluded)


def _source_files(
    sources: tuple[str, ...] = DEFAULT_BACKUP_SOURCES,
    *,
    root: Path = Path(),
    excluded: tuple[str, ...] = EXCLUDED_DEFAULT_PATHS,
) -> list[Path]:
    excluded_paths = {(root / item).resolve() for item in excluded}
    files: set[Path] = set()
    for source in sources:
        matches = root.glob(source) if _has_glob(source) else [(root / source)]
        for match in matches:
            if not match.exists():
                continue
            resolved = match.resolve()
            if _is_excluded(resolved, excluded_paths):
                continue
            if match.is_file():
                files.add(resolved)
                continue
            for path in match.rglob("*"):
                if path.is_file() and not _is_excluded(path.resolve(), excluded_paths):
                    files.add(path.resolve())
    return sorted(files, key=lambda p: p.relative_to(root.resolve()).as_posix())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_rows(files: list[Path], *, root: Path = Path()) -> list[BackupFile]:
    rows: list[BackupFile] = []
    root_resolved = root.resolve()
    for path in files:
        stat = path.stat()
        rows.append(
            BackupFile(
                path=path.relative_to(root_resolved).as_posix(),
                size=stat.st_size,
                mtime=datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                sha256=_sha256(path),
            )
        )
    return rows


def _write_manifest_json(
    path: Path, rows: list[BackupFile], *, created_at: str
) -> None:
    payload = {
        "created_at": created_at,
        "file_count": len(rows),
        "total_bytes": sum(row.size for row in rows),
        "files": [asdict(row) for row in rows],
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _write_manifest_csv(path: Path, rows: list[BackupFile]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["path", "size", "mtime", "sha256"])
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def create_backup_bundle(
    out_dir: Path,
    *,
    root: Path = Path(),
    name: str | None = None,
    sources: tuple[str, ...] = DEFAULT_BACKUP_SOURCES,
    dry_run: bool = False,
) -> BackupResult:
    """Create a manifest and optional ``.tar.gz`` archive in ``out_dir``."""

    created_at = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    bundle_name = name or f"fivethirtyeight-backup-{created_at}"
    out_dir.mkdir(parents=True, exist_ok=True)

    files = _source_files(sources, root=root)
    rows = _manifest_rows(files, root=root)
    manifest_json_path = out_dir / f"{bundle_name}.manifest.json"
    manifest_csv_path = out_dir / f"{bundle_name}.manifest.csv"
    _write_manifest_json(manifest_json_path, rows, created_at=created_at)
    _write_manifest_csv(manifest_csv_path, rows)

    archive_path = None
    if not dry_run:
        archive_path = out_dir / f"{bundle_name}.tar.gz"
        log.info("writing %s with %d files", archive_path, len(files))
        with tarfile.open(archive_path, "w:gz") as tf:
            tf.add(
                manifest_json_path, arcname=f"{bundle_name}/{manifest_json_path.name}"
            )
            tf.add(manifest_csv_path, arcname=f"{bundle_name}/{manifest_csv_path.name}")
            for path in files:
                arcname = f"{bundle_name}/{path.relative_to(root.resolve()).as_posix()}"
                tf.add(path, arcname=arcname)

    return BackupResult(
        archive_path=archive_path,
        manifest_json_path=manifest_json_path,
        manifest_csv_path=manifest_csv_path,
        file_count=len(rows),
        total_bytes=sum(row.size for row in rows),
    )
