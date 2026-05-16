"""Find URLs that share content (same SHA-1 digest) or canonical key."""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from fakethirtyeight.paths import DATA_DIR, INDEX_FILE

log = logging.getLogger(__name__)

DIGEST_REPORT = DATA_DIR / "duplicates_by_digest.csv"
CANONICAL_REPORT = DATA_DIR / "duplicates_by_canonical.csv"

DIGEST_FIELDS = ("digest", "url_count", "host_count", "sample_urls")
CANONICAL_FIELDS = ("canonical_key", "url_count", "host_count", "sample_urls")

# Treat these as "no digest" — Wayback uses "-" for non-2xx captures.
_MISSING_DIGEST = {"", "-", "0"}


@dataclass(slots=True)
class DupSummary:
    digest_groups: int  # number of distinct digests with >1 URL
    digest_dupe_urls: int  # total URLs across those groups
    canonical_groups: int  # number of distinct canonical keys with >1 URL
    canonical_dupe_urls: int


def report(
    index_path: Path = INDEX_FILE,
    *,
    digest_out: Path = DIGEST_REPORT,
    canonical_out: Path = CANONICAL_REPORT,
    sample_size: int = 5,
) -> DupSummary:
    """Build both duplicate reports from the merged index.

    - ``duplicates_by_digest.csv``: groups of URLs whose latest Wayback capture
      had byte-identical content (same SHA-1).
    - ``duplicates_by_canonical.csv``: groups of URLs that collapse to the same
      ``canonical_key`` (scheme/host/tracking normalization).

    Both reports list only groups with 2+ members.
    """
    if not index_path.exists():
        msg = f"index file not found: {index_path}. Run `merge` first."
        raise FileNotFoundError(msg)

    by_digest: dict[str, list[dict[str, str]]] = defaultdict(list)
    by_canonical: dict[str, list[dict[str, str]]] = defaultdict(list)

    with index_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            digest = (row.get("latest_digest") or "").strip()
            if digest and digest not in _MISSING_DIGEST:
                by_digest[digest].append(row)
            canon = (row.get("canonical_key") or "").strip()
            if canon:
                by_canonical[canon].append(row)

    digest_groups = _write_groups(
        digest_out,
        DIGEST_FIELDS,
        ((k, v) for k, v in by_digest.items() if len(v) > 1),
        key_name="digest",
        sample_size=sample_size,
    )
    canonical_groups = _write_groups(
        canonical_out,
        CANONICAL_FIELDS,
        ((k, v) for k, v in by_canonical.items() if len(v) > 1),
        key_name="canonical_key",
        sample_size=sample_size,
    )

    summary = DupSummary(
        digest_groups=digest_groups.count,
        digest_dupe_urls=digest_groups.total_urls,
        canonical_groups=canonical_groups.count,
        canonical_dupe_urls=canonical_groups.total_urls,
    )
    log.info(
        "digest: %d groups / %d URLs; canonical: %d groups / %d URLs",
        summary.digest_groups,
        summary.digest_dupe_urls,
        summary.canonical_groups,
        summary.canonical_dupe_urls,
    )
    return summary


@dataclass(slots=True)
class _GroupStats:
    count: int
    total_urls: int


def _write_groups(
    out_path: Path,
    fields: tuple[str, ...],
    groups,  # noqa: ANN001 — iterator of (key, list[row])
    *,
    key_name: str,
    sample_size: int,
) -> _GroupStats:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    total_urls = 0
    for key, members in groups:
        urls = [m.get("url") or "" for m in members]
        hosts = {m.get("host") or "" for m in members}
        sample = "; ".join(urls[:sample_size])
        rows.append(
            {
                key_name: key,
                "url_count": len(urls),
                "host_count": len(hosts),
                "sample_urls": sample,
            }
        )
        total_urls += len(urls)

    # Largest groups first.
    rows.sort(key=lambda r: r["url_count"], reverse=True)

    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            r["url_count"] = str(r["url_count"])
            r["host_count"] = str(r["host_count"])
            writer.writerow(r)

    return _GroupStats(count=len(rows), total_urls=total_urls)
