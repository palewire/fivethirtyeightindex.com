"""Merge shard CSVs into the deduplicated index CSV."""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from tqdm import tqdm

from fakethirtyeight.paths import INDEX_FILE, SHARDS_DIR, ensure_dirs

log = logging.getLogger(__name__)

INDEX_FIELDS = (
    "urlkey",
    "url",
    "canonical_key",
    "host",
    "path",
    "query",
    "first_seen_ts",
    "last_seen_ts",
    "latest_status",
    "latest_mimetype",
    "latest_digest",
    "latest_length",
    "snapshot_observations",
    "source",
)


@dataclass
class UrlRecord:
    urlkey: str
    url: str = ""
    first_seen_ts: str = ""
    last_seen_ts: str = ""
    latest_status: str = ""
    latest_mimetype: str = ""
    latest_digest: str = ""
    latest_length: str = ""
    snapshot_observations: int = 0
    sources: set[str] = field(default_factory=set)

    def update_cdx(
        self,
        *,
        url: str,
        timestamp: str,
        status: str,
        mimetype: str,
        digest: str,
        length: str,
    ) -> None:
        if not self.url:
            self.url = url
        if not self.first_seen_ts or (timestamp and timestamp < self.first_seen_ts):
            self.first_seen_ts = timestamp
        if timestamp and timestamp > self.last_seen_ts:
            self.last_seen_ts = timestamp
            self.latest_status = status
            self.latest_mimetype = mimetype
            self.latest_digest = digest
            self.latest_length = length
        self.snapshot_observations += 1
        self.sources.add("cdx")

    def add_sitemap(self, url: str) -> None:
        if not self.url:
            self.url = url
        self.sources.add("sitemap")

    def to_row(self) -> dict[str, str]:
        from fakethirtyeight.canonicalize import canonical_key

        host, path, query = _split_url(self.url)
        return {
            "urlkey": self.urlkey,
            "url": self.url,
            "canonical_key": canonical_key(self.url),
            "host": host,
            "path": path,
            "query": query,
            "first_seen_ts": self.first_seen_ts,
            "last_seen_ts": self.last_seen_ts,
            "latest_status": self.latest_status,
            "latest_mimetype": self.latest_mimetype,
            "latest_digest": self.latest_digest,
            "latest_length": self.latest_length,
            "snapshot_observations": str(self.snapshot_observations),
            "source": "+".join(sorted(self.sources)),
        }


def merge(
    *,
    shards_dir: Path = SHARDS_DIR,
    out_path: Path = INDEX_FILE,
) -> int:
    """Build out_path from every shard CSV under shards_dir."""
    ensure_dirs()
    records: dict[str, UrlRecord] = {}

    cdx_shards = sorted(shards_dir.glob("cdx-*.csv"))
    sitemap_shards = sorted(shards_dir.glob("sitemap-*.csv"))

    for path in tqdm(cdx_shards, desc="cdx shards", unit="shard"):
        _ingest_cdx_shard(path, records)
    for path in tqdm(sitemap_shards, desc="sitemap shards", unit="shard"):
        _ingest_sitemap_shard(path, records)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=INDEX_FIELDS)
        writer.writeheader()
        for key in sorted(records):
            writer.writerow(records[key].to_row())

    log.info("wrote %d unique URLs to %s", len(records), out_path)
    return len(records)


def _ingest_cdx_shard(path: Path, records: dict[str, UrlRecord]) -> None:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            urlkey = row.get("urlkey") or ""
            if not urlkey:
                continue
            rec = records.get(urlkey) or UrlRecord(urlkey=urlkey)
            rec.update_cdx(
                url=row.get("original") or "",
                timestamp=row.get("timestamp") or "",
                status=row.get("statuscode") or "",
                mimetype=row.get("mimetype") or "",
                digest=row.get("digest") or "",
                length=row.get("length") or "",
            )
            records[urlkey] = rec


def _ingest_sitemap_shard(path: Path, records: dict[str, UrlRecord]) -> None:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            url = row.get("url") or ""
            if not url:
                continue
            urlkey = surt_key(url)
            rec = records.get(urlkey) or UrlRecord(urlkey=urlkey, url=url)
            rec.add_sitemap(url)
            records[urlkey] = rec


def surt_key(url: str) -> str:
    """Compute a SURT-like key for sitemap URLs that aren't in CDX yet.

    Not bit-identical to archive.org's SURT, but stable for our dedup purposes
    within a single host. Format: ``host,reversed)/path?query``.
    """
    parts = urlsplit(url)
    host = parts.hostname or ""
    reversed_host = ",".join(reversed(host.split("."))) if host else ""
    path = parts.path or "/"
    key = f"{reversed_host})/{path.lstrip('/')}"
    if parts.query:
        key += f"?{parts.query}"
    return key.lower()


def _split_url(url: str) -> tuple[str, str, str]:
    if not url:
        return "", "", ""
    parts = urlsplit(url)
    return parts.hostname or "", parts.path or "", parts.query or ""
