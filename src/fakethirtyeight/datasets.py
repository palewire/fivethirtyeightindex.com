"""Build the static dataset index from FiveThirtyEight's public data repo."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import re
import subprocess
import time
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from internetarchive import get_session

from fakethirtyeight.classify import classify
from fakethirtyeight.http import make_client
from fakethirtyeight.ia_metadata import year_from_date
from fakethirtyeight.paths import DATA_DIR

log = logging.getLogger(__name__)

INDEX_URL = "https://raw.githubusercontent.com/fivethirtyeight/data/master/index.csv"
DATASETS_FILE = DATA_DIR / "datasets.csv"
DATASET_BUNDLES_DIR = DATA_DIR / "dataset_bundles"
DATASET_DOWNLOAD_LOG = DATA_DIR / "dataset_download_log.csv"
DATASET_UPLOAD_LOG = DATA_DIR / "dataset_upload_log.csv"
DATASET_METADATA_REPAIR_LOG = DATA_DIR / "dataset_metadata_repair_log.csv"
DATASET_REPO_CLONES_DIR = DATA_DIR / "dataset_repo_clones"
SITE_DATASETS_FILE = Path("web/static/data/datasets.json")
SITE_DATASETS_CSV_FILE = Path("web/static/data/datasets.csv")
SITE_DATASETS_META_FILE = Path("web/static/data/datasets-meta.json")
ARCHIVE_ITEM_BASE_URL = "https://archive.org/details"
DEFAULT_COLLECTION = "fivethirtyeight-collection"
DEFAULT_CONTRIBUTOR = "Ben Welsh"
DEFAULT_PUBLISHER = "FiveThirtyEight"
DOWNLOAD_LOG_FIELDS = ("identifier", "downloaded_at", "status", "files", "error")
UPLOAD_LOG_FIELDS = ("identifier", "uploaded_at", "status", "files", "error")
METADATA_REPAIR_LOG_FIELDS = ("identifier", "repaired_at", "status", "year", "error")
_ZIP_CACHE: dict[tuple[str, str, str], bytes] = {}
TITLE_ACRONYMS = {
    "abc",
    "ahca",
    "elo",
    "gop",
    "mlb",
    "nba",
    "ncaa",
    "nfl",
    "nhl",
    "spi",
    "wnba",
}


class _UploadResponse(Protocol):
    def raise_for_status(self) -> None: ...


class _ArchiveItem(Protocol):
    def upload(
        self,
        files: dict[str, str],
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
class GithubSource:
    owner: str
    repo: str
    ref: str
    path: str


@dataclass(slots=True)
class DatasetRecord:
    """One unique public FiveThirtyEight data resource."""

    id: str
    slug: str
    title: str
    dataset_url: str
    article_urls: list[str]
    identifier: str = ""
    archive_url: str = ""
    date: str = ""

    def __post_init__(self) -> None:
        if not self.identifier:
            self.identifier = identifier_for_slug(self.slug)

    @property
    def article_count(self) -> int:
        return len(self.article_urls)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "slug": self.slug,
            "title": self.title,
            "dataset_url": self.dataset_url,
            "article_urls": self.article_urls,
            "article_count": self.article_count,
            "identifier": self.identifier,
            "archive_url": self.archive_url,
            "date": self.date,
        }


def scrape_index(
    *,
    index_url: str = INDEX_URL,
    out_path: Path = DATASETS_FILE,
    site_json_path: Path = SITE_DATASETS_FILE,
    site_csv_path: Path = SITE_DATASETS_CSV_FILE,
    site_meta_path: Path = SITE_DATASETS_META_FILE,
    enriched_path: Path = DATA_DIR / "enriched.csv",
    include_commit_dates: bool = False,
    fetch_text: Callable[[str], str] | None = None,
) -> int:
    """Fetch FiveThirtyEight's dataset catalog and write site-ready artifacts."""
    if fetch_text is None:
        fetch_text = _fetch_text

    text = fetch_text(index_url)
    records = parse_index_csv(text)
    if include_commit_dates:
        apply_github_commit_dates(records)
    write_datasets(records, out_path)
    write_site_datasets(
        records,
        json_path=site_json_path,
        csv_path=site_csv_path,
        meta_path=site_meta_path,
        enriched_path=enriched_path,
    )
    return len(records)


def parse_index_csv(text: str) -> list[DatasetRecord]:
    """Return one record per unique ``dataset_url`` in ``index.csv``."""
    grouped: dict[str, list[str]] = {}
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        dataset_url = (row.get("dataset_url") or "").strip()
        article_url = (row.get("article_url") or "").strip()
        if not dataset_url:
            continue
        articles = grouped.setdefault(dataset_url, [])
        if article_url and article_url not in articles:
            articles.append(article_url)

    records: list[DatasetRecord] = []
    seen_slugs: set[str] = set()
    for dataset_url, article_urls in grouped.items():
        slug = _slug_from_dataset_url(dataset_url)
        if not slug:
            continue
        slug = _unique_slug(slug, seen_slugs)
        seen_slugs.add(slug)
        records.append(
            DatasetRecord(
                id=f"dataset:{slug}",
                slug=slug,
                title=_title_from_slug(slug),
                dataset_url=dataset_url,
                article_urls=article_urls,
                identifier=identifier_for_slug(slug),
                archive_url="",
                date="",
            )
        )

    records.sort(key=lambda r: r.title.casefold())
    return records


def load_datasets(path: Path = DATASETS_FILE) -> list[DatasetRecord]:
    """Read ``data/datasets.csv``."""
    if not path.exists():
        return []

    records: list[DatasetRecord] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            slug = row.get("slug") or ""
            dataset_url = row.get("dataset_url") or ""
            if not slug or not dataset_url:
                continue
            article_urls = [
                url for url in (row.get("article_urls") or "").split(";") if url.strip()
            ]
            records.append(
                DatasetRecord(
                    id=row.get("id") or f"dataset:{slug}",
                    slug=slug,
                    title=row.get("title") or _title_from_slug(slug),
                    dataset_url=dataset_url,
                    article_urls=article_urls,
                    identifier=row.get("identifier") or identifier_for_slug(slug),
                    archive_url=row.get("archive_url") or "",
                    date=row.get("date") or "",
                )
            )
    return records


def write_datasets(
    records: list[DatasetRecord], out_path: Path = DATASETS_FILE
) -> None:
    """Write the source dataset inventory under ``data/``."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(
            [
                "id",
                "slug",
                "title",
                "dataset_url",
                "article_urls",
                "article_count",
                "identifier",
                "archive_url",
                "date",
            ]
        )
        for record in records:
            writer.writerow(
                [
                    record.id,
                    record.slug,
                    record.title,
                    record.dataset_url,
                    ";".join(record.article_urls),
                    record.article_count,
                    record.identifier,
                    record.archive_url,
                    record.date,
                ]
            )


def write_site_datasets(
    records: list[DatasetRecord] | None = None,
    *,
    source_path: Path = DATASETS_FILE,
    json_path: Path = SITE_DATASETS_FILE,
    csv_path: Path = SITE_DATASETS_CSV_FILE,
    meta_path: Path = SITE_DATASETS_META_FILE,
    enriched_path: Path = DATA_DIR / "enriched.csv",
) -> int:
    """Write the static-site dataset JSON, CSV, and tiny metadata file."""
    if records is None:
        records = load_datasets(source_path)

    apply_upload_log(records)
    apply_related_story_dates(records, enriched_path=enriched_path)

    json_path.parent.mkdir(parents=True, exist_ok=True)
    with json_path.open("w", encoding="utf-8") as fh:
        json.dump([r.to_dict() for r in records], fh, ensure_ascii=False)

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(
            [
                "title",
                "dataset_url",
                "archive_url",
                "article_urls",
                "article_count",
                "identifier",
                "date",
                "id",
            ]
        )
        for record in records:
            writer.writerow(
                [
                    record.title,
                    record.dataset_url,
                    record.archive_url,
                    ";".join(record.article_urls),
                    record.article_count,
                    record.identifier,
                    record.date,
                    record.id,
                ]
            )

    meta_path.parent.mkdir(parents=True, exist_ok=True)
    with meta_path.open("w", encoding="utf-8") as fh:
        json.dump({"total": len(records)}, fh)

    log.info("wrote %d datasets to %s and %s", len(records), json_path, csv_path)
    return len(records)


def download_bundles(
    *,
    datasets_path: Path = DATASETS_FILE,
    bundles_dir: Path = DATASET_BUNDLES_DIR,
    log_path: Path = DATASET_DOWNLOAD_LOG,
    limit: int | None = None,
    force: bool = False,
) -> tuple[int, int, int]:
    """Mirror every dataset's source files into a local bundle directory.

    For URLs under ``github.com/<owner>/<repo>/tree/<ref>/<path>``, only that
    tree path is mirrored. For plain repo URLs, the whole repository is
    mirrored. Each bundle also gets a ``manifest.json`` with source URLs,
    article links, and checksums.
    """
    records = load_datasets(datasets_path)
    if limit is not None:
        records = records[:limit]

    done = _load_done(log_path)
    bundles_dir.mkdir(parents=True, exist_ok=True)
    write_header = not log_path.exists()
    downloaded = skipped = failed = 0

    with make_client() as client:
        with log_path.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=DOWNLOAD_LOG_FIELDS)
            if write_header:
                writer.writeheader()

            for record in records:
                if record.identifier in done and not force:
                    skipped += 1
                    continue
                try:
                    files = _download_bundle(client, record, bundles_dir=bundles_dir)
                except Exception as exc:
                    failed += 1
                    writer.writerow(
                        _log_row(
                            record.identifier,
                            status="error",
                            files=[],
                            error=repr(exc)[:300],
                        )
                    )
                    fh.flush()
                    log.exception("failed to download %s", record.identifier)
                    continue

                downloaded += 1
                writer.writerow(
                    _log_row(
                        record.identifier,
                        status="downloaded",
                        files=[
                            str(p.relative_to(bundles_dir / record.slug)) for p in files
                        ],
                        error="",
                    )
                )
                fh.flush()
                log.info("downloaded %s (%d files)", record.identifier, len(files))

    return downloaded, skipped, failed


def upload_bundles(
    *,
    collection: str = DEFAULT_COLLECTION,
    contributor: str = DEFAULT_CONTRIBUTOR,
    datasets_path: Path = DATASETS_FILE,
    bundles_dir: Path = DATASET_BUNDLES_DIR,
    log_path: Path = DATASET_UPLOAD_LOG,
    delay: float = 1.0,
    limit: int | None = None,
    dry_run: bool = False,
    force: bool = False,
    path_sensitive_only: bool = False,
) -> tuple[int, int, int]:
    """Upload each local dataset bundle to one archive.org item."""
    records = load_datasets(datasets_path)
    if path_sensitive_only:
        records = [
            r
            for r in records
            if _needs_path_sensitive_upload(r, bundles_dir=bundles_dir)
        ]
    done = _load_done(log_path)
    pending = [r for r in records if force or r.identifier not in done]
    skipped = len(records) - len(pending)
    if limit is not None:
        pending = pending[:limit]

    session: _ArchiveSession | None
    if dry_run:
        session = None
    else:
        session = _ia_session()

    write_header = not log_path.exists()
    uploaded = failed = 0
    with log_path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=UPLOAD_LOG_FIELDS)
        if write_header:
            writer.writeheader()

        for record in pending:
            result = upload_one_bundle(
                session,
                record,
                collection=collection,
                contributor=contributor,
                bundles_dir=bundles_dir,
                dry_run=dry_run,
            )
            writer.writerow(
                _log_row(
                    record.identifier,
                    status=result["status"],
                    files=result["files"],
                    error=result["error"],
                    timestamp_field="uploaded_at",
                )
            )
            fh.flush()
            if result["status"] == "uploaded":
                uploaded += 1
            elif result["status"] in {"dry_run", "skipped_missing"}:
                skipped += 1
            else:
                failed += 1
            log.info("%s — %s", result["status"], record.identifier)
            if delay > 0 and not dry_run:
                time.sleep(delay)

    return uploaded, skipped, failed


def repair_dataset_years(
    *,
    datasets_path: Path = DATASETS_FILE,
    upload_log_path: Path = DATASET_UPLOAD_LOG,
    repair_log_path: Path = DATASET_METADATA_REPAIR_LOG,
    delay: float = 1.0,
    limit: int | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> tuple[int, int, int]:
    """Backfill archive.org ``year`` metadata for uploaded dataset items."""
    records = load_datasets(datasets_path)
    uploaded = _load_done(upload_log_path)
    done = set() if force else _load_done(repair_log_path)
    pending = [
        r
        for r in records
        if r.identifier in uploaded
        and r.identifier not in done
        and year_from_date(r.date)
    ]
    skipped = len(records) - len(pending)
    if limit is not None:
        pending = pending[:limit]

    session: _ArchiveSession | None = None if dry_run else _ia_session()
    write_header = not repair_log_path.exists()
    repaired = failed = 0
    with repair_log_path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=METADATA_REPAIR_LOG_FIELDS)
        if write_header:
            writer.writeheader()

        for record in pending:
            year = year_from_date(record.date)
            result = repair_one_dataset_year(
                session,
                record,
                year=year,
                dry_run=dry_run,
            )
            writer.writerow(
                _metadata_repair_log_row(
                    record.identifier,
                    status=result["status"],
                    year=year,
                    error=result["error"],
                )
            )
            fh.flush()
            if result["status"] == "repaired":
                repaired += 1
            elif result["status"] == "dry_run":
                skipped += 1
            else:
                failed += 1
            log.info("%s year=%s — %s", result["status"], year, record.identifier)
            if delay > 0 and not dry_run:
                time.sleep(delay)

    return repaired, skipped, failed


def repair_one_dataset_year(
    session: _ArchiveSession | None,
    record: DatasetRecord,
    *,
    year: str,
    dry_run: bool,
) -> dict[str, str]:
    """Patch one uploaded dataset item's IA ``year`` metadata field."""
    if not year:
        return {"status": "skipped_missing", "error": "record has no year"}
    if dry_run:
        return {"status": "dry_run", "error": ""}
    if session is None:
        return {"status": "error", "error": "missing IA session"}

    try:
        item = session.get_item(record.identifier)
        resp = item.modify_metadata(
            {"year": year},
            request_kwargs=_ia_request_kwargs(),
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": repr(exc)[:300]}
    return {"status": "repaired", "error": ""}


def upload_one_bundle(
    session: _ArchiveSession | None,
    record: DatasetRecord,
    *,
    collection: str,
    contributor: str,
    bundles_dir: Path = DATASET_BUNDLES_DIR,
    dry_run: bool,
) -> dict[str, object]:
    bundle_dir = bundles_dir / record.slug
    upload_files = _upload_file_map(bundle_dir)
    if not upload_files:
        return {
            "status": "skipped_missing",
            "files": [],
            "error": "bundle not downloaded",
        }

    metadata = metadata_for_record(
        record,
        collection=collection,
        contributor=contributor,
    )
    if dry_run:
        return {"status": "dry_run", "files": list(upload_files), "error": ""}
    if session is None:
        return {"status": "error", "files": [], "error": "missing IA session"}

    try:
        item = session.get_item(record.identifier)
        responses = item.upload(
            files=upload_files,
            metadata=metadata,
            retries=10,
            retries_sleep=30,
            verbose=False,
            request_kwargs=_ia_request_kwargs(),
        )
        for resp in responses:
            resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "files": [], "error": repr(exc)[:300]}

    return {"status": "uploaded", "files": list(upload_files), "error": ""}


def metadata_for_record(
    record: DatasetRecord,
    *,
    collection: str,
    contributor: str = DEFAULT_CONTRIBUTOR,
) -> dict[str, str | list[str]]:
    """Build archive.org metadata for a dataset bundle."""
    description_bits = [
        f"Preserved bundle of FiveThirtyEight dataset files for {record.title}.",
        f"Original dataset URL: {record.dataset_url}",
    ]
    if record.article_urls:
        description_bits.append("Related stories: " + "; ".join(record.article_urls))

    md: dict[str, str | list[str]] = {
        "collection": collection,
        "mediatype": "data",
        "title": f"FiveThirtyEight Dataset: {record.title}",
        "creator": "FiveThirtyEight",
        "contributor": contributor,
        "publisher": DEFAULT_PUBLISHER,
        "date": record.date,
        "year": year_from_date(record.date),
        "description": "\n\n".join(description_bits),
        "subject": ["dataset", "FiveThirtyEight", "data journalism"],
        "source": record.dataset_url,
        "language": "eng",
        "external-identifier": [
            f"urn:fakethirtyeight:dataset:{record.slug}",
            f"urn:fakethirtyeight:dataset-source:{record.dataset_url}",
        ],
        "originalurl": record.dataset_url,
    }
    return {k: v for k, v in md.items() if v not in ("", [], None)}


def apply_upload_log(
    records: list[DatasetRecord], log_path: Path = DATASET_UPLOAD_LOG
) -> None:
    """Patch records in-place with archive.org URLs from successful uploads."""
    uploaded = _load_done(log_path)
    if not uploaded:
        return
    for record in records:
        if record.identifier in uploaded:
            record.archive_url = f"{ARCHIVE_ITEM_BASE_URL}/{record.identifier}"


def apply_related_story_dates(
    records: list[DatasetRecord], enriched_path: Path = DATA_DIR / "enriched.csv"
) -> None:
    """Patch records with the earliest publish date of matched related stories."""
    if not enriched_path.exists():
        return

    story_dates: dict[str, str] = {}
    with enriched_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            key = row.get("rollup_key") or ""
            date = row.get("published_at") or ""
            if key and date:
                story_dates[key] = date

    for record in records:
        dates = [
            story_dates[classify(url).rollup_key]
            for url in record.article_urls
            if classify(url).rollup_key in story_dates
        ]
        if dates:
            record.date = min(dates)


def apply_github_commit_dates(
    records: list[DatasetRecord],
    *,
    repo_cache_dir: Path = DATASET_REPO_CLONES_DIR,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
) -> None:
    """Patch records with the first commit date for their GitHub source path."""
    if runner is None:
        runner = _run_git

    for record in records:
        record.date = (
            first_git_commit_date(
                record,
                repo_cache_dir=repo_cache_dir,
                runner=runner,
            )
            or record.date
        )


def first_git_commit_date(
    record: DatasetRecord,
    *,
    repo_cache_dir: Path = DATASET_REPO_CLONES_DIR,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
) -> str:
    """Return the oldest local git commit date for a dataset source path."""
    if runner is None:
        runner = _run_git

    source = _github_source_from_url(record.dataset_url)
    repo_path = _ensure_git_repo(source, repo_cache_dir=repo_cache_dir, runner=runner)
    pathspec = source.path or "."
    result = runner(
        [
            "git",
            "-C",
            str(repo_path),
            "log",
            "--reverse",
            "--format=%cs",
            "--",
            pathspec,
        ]
    )
    if result.returncode != 0:
        msg = result.stderr.strip() or f"git log failed for {record.dataset_url}"
        raise RuntimeError(msg)
    return result.stdout.splitlines()[0] if result.stdout.splitlines() else ""


def first_github_commit_date(
    record: DatasetRecord,
    *,
    client: httpx.Client,
) -> str:
    """Return the oldest commit date for a dataset source path."""
    source = _github_source_from_url(record.dataset_url)
    params = {"sha": source.ref, "per_page": "100"}
    if source.path:
        params["path"] = source.path

    url = f"https://api.github.com/repos/{source.owner}/{source.repo}/commits"
    response = client.get(url, params=params)
    if response.status_code == 404 and source.ref == "master":
        params["sha"] = "main"
        response = client.get(url, params=params)
    response.raise_for_status()

    last_url = _last_link_url(response.headers.get("link") or "")
    if last_url:
        response = client.get(last_url)
        response.raise_for_status()

    commits = response.json()
    if not commits:
        return ""
    oldest = commits[-1]
    date = (
        oldest.get("commit", {}).get("committer", {}).get("date")
        or oldest.get("commit", {}).get("author", {}).get("date")
        or ""
    )
    return date[:10] if len(date) >= 10 else date


def _ensure_git_repo(
    source: GithubSource,
    *,
    repo_cache_dir: Path,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]],
) -> Path:
    repo_path = repo_cache_dir / f"{source.owner}-{source.repo}"
    if repo_path.exists():
        return repo_path

    repo_cache_dir.mkdir(parents=True, exist_ok=True)
    result = runner(
        [
            "git",
            "clone",
            "--filter=blob:none",
            "--no-checkout",
            f"https://github.com/{source.owner}/{source.repo}",
            str(repo_path),
        ]
    )
    if result.returncode != 0:
        msg = (
            result.stderr.strip()
            or f"git clone failed for {source.owner}/{source.repo}"
        )
        raise RuntimeError(msg)
    return repo_path


def _run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)  # noqa: S603


def _upload_file_map(bundle_dir: Path) -> dict[str, str]:
    files = sorted(p for p in bundle_dir.rglob("*") if p.is_file())
    return {p.relative_to(bundle_dir).as_posix(): str(p) for p in files}


def _needs_path_sensitive_upload(
    record: DatasetRecord,
    *,
    bundles_dir: Path = DATASET_BUNDLES_DIR,
) -> bool:
    bundle_dir = bundles_dir / record.slug
    files = sorted(p for p in bundle_dir.rglob("*") if p.is_file())
    names = [p.name for p in files]
    has_nested_files = any(p.parent != bundle_dir for p in files)
    has_duplicate_basenames = len(names) != len(set(names))
    return has_nested_files or has_duplicate_basenames


def _ia_session(env_path: Path = Path(".env")) -> _ArchiveSession:
    _configure_ca_bundle()
    access = os.environ.get("IA_ACCESS_KEY")
    secret = os.environ.get("IA_SECRET_KEY")
    if not access or not secret:
        file_env = _read_env_keys(env_path, {"IA_ACCESS_KEY", "IA_SECRET_KEY"})
        access = access or file_env.get("IA_ACCESS_KEY")
        secret = secret or file_env.get("IA_SECRET_KEY")

    config = {"s3": {"access": access, "secret": secret}} if access and secret else None
    return get_session(config=config)


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


def _read_env_keys(env_path: Path, keys: set[str]) -> dict[str, str]:
    if not env_path.exists():
        return {}

    values: dict[str, str] = {}
    with env_path.open(encoding="utf-8") as fh:
        for line in fh:
            clean = line.strip()
            if not clean or clean.startswith("#") or "=" not in clean:
                continue
            key, value = clean.split("=", 1)
            key = key.strip()
            if key not in keys:
                continue
            values[key] = value.strip().strip("\"'")
    return values


def _last_link_url(link_header: str) -> str:
    for part in link_header.split(","):
        url_part, _, rel_part = part.strip().partition(";")
        if 'rel="last"' not in rel_part:
            continue
        return url_part.strip().removeprefix("<").removesuffix(">")
    return ""


def identifier_for_slug(slug: str) -> str:
    return f"fivethirtyeight-dataset-{slug}"


def _download_bundle(
    client: httpx.Client,
    record: DatasetRecord,
    *,
    bundles_dir: Path,
) -> list[Path]:
    source = _github_source_from_url(record.dataset_url)
    bundle_dir = bundles_dir / record.slug
    bundle_dir.mkdir(parents=True, exist_ok=True)
    zip_bytes, resolved_ref = _repo_zip(client, source)
    source = GithubSource(
        owner=source.owner,
        repo=source.repo,
        ref=resolved_ref,
        path=source.path,
    )
    prefix = source.path.strip("/")
    prefix_with_slash = f"{prefix}/" if prefix else ""
    downloaded: list[Path] = []
    manifest_files: list[dict[str, object]] = []

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            source_path = "/".join(Path(info.filename).parts[1:])
            if (
                prefix
                and source_path != prefix
                and not source_path.startswith(prefix_with_slash)
            ):
                continue
            rel = source_path.removeprefix(prefix_with_slash) if prefix else source_path
            if not rel or (rel == source_path and prefix and source_path == prefix):
                rel = Path(source_path).name
            target = _safe_target(bundle_dir, rel)
            content = zf.read(info)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            downloaded.append(target)
            raw_url = (
                f"https://raw.githubusercontent.com/{source.owner}/{source.repo}/"
                f"{source.ref}/{source_path}"
            )
            manifest_files.append(
                {
                    "path": rel,
                    "source_path": source_path,
                    "source_url": raw_url,
                    "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )

    if not downloaded:
        msg = f"no files found for {record.dataset_url}"
        raise RuntimeError(msg)

    manifest = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "dataset": record.to_dict(),
        "github": {
            "owner": source.owner,
            "repo": source.repo,
            "ref": source.ref,
            "path": source.path,
            "archive_url": _zip_url(source),
        },
        "files": manifest_files,
    }
    manifest_path = bundle_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    downloaded.append(manifest_path)
    return downloaded


def _repo_zip(client: httpx.Client, source: GithubSource) -> tuple[bytes, str]:
    refs = [source.ref]
    if source.ref == "master":
        refs.append("main")
    elif source.ref == "main":
        refs.append("master")

    for ref in refs:
        key = (source.owner, source.repo, ref)
        if key in _ZIP_CACHE:
            return _ZIP_CACHE[key], ref

        url = _zip_url(GithubSource(source.owner, source.repo, ref, source.path))
        response = client.get(url)
        if response.status_code == 404 and ref != refs[-1]:
            continue
        response.raise_for_status()
        _ZIP_CACHE[key] = response.content
        return response.content, ref

    msg = f"could not download repository archive for {source.owner}/{source.repo}"
    raise RuntimeError(msg)


def _zip_url(source: GithubSource) -> str:
    return (
        f"https://codeload.github.com/{source.owner}/{source.repo}"
        f"/zip/refs/heads/{source.ref}"
    )


def _github_source_from_url(url: str) -> GithubSource:
    parsed = urlsplit(url)
    if parsed.netloc.lower() != "github.com":
        msg = f"unsupported dataset source host: {url}"
        raise ValueError(msg)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        msg = f"unsupported GitHub dataset URL: {url}"
        raise ValueError(msg)

    owner, repo = parts[0], parts[1]
    if len(parts) >= 4 and parts[2] == "tree":
        return GithubSource(
            owner=owner, repo=repo, ref=parts[3], path="/".join(parts[4:])
        )

    return GithubSource(owner=owner, repo=repo, ref="master", path="")


def _safe_target(root: Path, rel_path: str) -> Path:
    target = root / rel_path
    resolved_root = root.resolve()
    resolved_target = target.resolve()
    if (
        resolved_root not in resolved_target.parents
        and resolved_target != resolved_root
    ):
        msg = f"unsafe bundle path: {rel_path}"
        raise ValueError(msg)
    return target


def _load_done(log_path: Path) -> set[str]:
    if not log_path.exists():
        return set()
    done: set[str] = set()
    with log_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (row.get("status") or "") not in {"downloaded", "uploaded", "repaired"}:
                continue
            identifier = row.get("identifier") or ""
            if identifier:
                done.add(identifier)
    return done


def _log_row(
    identifier: str,
    *,
    status: object,
    files: object,
    error: object,
    timestamp_field: str = "downloaded_at",
) -> dict[str, str]:
    file_values = [str(f) for f in files] if isinstance(files, list) else []
    return {
        "identifier": identifier,
        timestamp_field: datetime.now(UTC).isoformat(timespec="seconds"),
        "status": str(status),
        "files": "|".join(file_values),
        "error": str(error),
    }


def _metadata_repair_log_row(
    identifier: str,
    *,
    status: object,
    year: object,
    error: object,
) -> dict[str, str]:
    return {
        "identifier": identifier,
        "repaired_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": str(status),
        "year": str(year),
        "error": str(error),
    }


def _fetch_text(url: str) -> str:
    with make_client() as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text


def _slug_from_dataset_url(url: str) -> str:
    path = urlsplit(url).path.strip("/")
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 5 and parts[:4] == ["fivethirtyeight", "data", "tree", "master"]:
        return parts[4]
    if len(parts) >= 2 and parts[0] == "fivethirtyeight":
        return parts[1]
    return parts[-1] if parts else ""


def _unique_slug(slug: str, seen: set[str]) -> str:
    if slug not in seen:
        return slug
    i = 2
    while f"{slug}-{i}" in seen:
        i += 1
    return f"{slug}-{i}"


def _title_from_slug(slug: str) -> str:
    words = re.split(r"[-_]+", slug)
    return " ".join(
        w.upper() if w.casefold() in TITLE_ACRONYMS else w.capitalize()
        for w in words
        if w
    )
