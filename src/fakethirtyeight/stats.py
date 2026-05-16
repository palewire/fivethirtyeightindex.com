"""Read-only summaries over the merged index CSV."""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from fakethirtyeight.paths import INDEX_FILE


@dataclass
class Stats:
    total: int
    unique_canonical: int
    html_pages: int  # HTML pages with latest_status=200
    html_pages_unique_canonical: int
    html_pages_unique_digest: int
    by_host: Counter[str]
    by_year: Counter[str]
    by_mimetype: Counter[str]
    by_status: Counter[str]
    by_source: Counter[str]
    by_kind: Counter[str]


def summarize(index_path: Path = INDEX_FILE) -> Stats:
    if not index_path.exists():
        msg = f"index file not found: {index_path}. Run `merge` first."
        raise FileNotFoundError(msg)

    total = 0
    canonical_keys: set[str] = set()
    html_pages = 0
    html_canonical: set[str] = set()
    html_digests: set[str] = set()
    by_host: Counter[str] = Counter()
    by_year: Counter[str] = Counter()
    by_mimetype: Counter[str] = Counter()
    by_status: Counter[str] = Counter()
    by_source: Counter[str] = Counter()
    by_kind: Counter[str] = Counter()

    with index_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            total += 1
            canon = row.get("canonical_key") or ""
            if canon:
                canonical_keys.add(canon)
            mimetype = row.get("latest_mimetype") or ""
            status = row.get("latest_status") or ""
            if status == "200" and mimetype == "text/html":
                html_pages += 1
                if canon:
                    html_canonical.add(canon)
                digest = row.get("latest_digest") or ""
                if digest and digest not in {"-", "0"}:
                    html_digests.add(digest)
            by_host[row.get("host") or "(unknown)"] += 1
            by_mimetype[mimetype or "(unknown)"] += 1
            by_status[status or "(unknown)"] += 1
            by_source[row.get("source") or "(unknown)"] += 1
            by_kind[row.get("kind") or "(unknown)"] += 1
            ts = row.get("last_seen_ts") or row.get("first_seen_ts") or ""
            year = ts[:4] if len(ts) >= 4 else "(unknown)"
            by_year[year] += 1

    return Stats(
        total=total,
        unique_canonical=len(canonical_keys),
        html_pages=html_pages,
        html_pages_unique_canonical=len(html_canonical),
        html_pages_unique_digest=len(html_digests),
        by_host=by_host,
        by_year=by_year,
        by_mimetype=by_mimetype,
        by_status=by_status,
        by_source=by_source,
        by_kind=by_kind,
    )


def format_text(stats: Stats, *, top: int = 15) -> str:
    """Plaintext summary suitable for stdout."""
    collapsed = stats.total - stats.unique_canonical
    lines = [
        f"Total URLs:                       {stats.total:,}",
        f"Unique canonical key:             {stats.unique_canonical:,}"
        f"  (collapses {collapsed:,} soft-duplicates)",
        f"HTML pages (status=200):          {stats.html_pages:,}",
        f"  └ unique canonical keys:        {stats.html_pages_unique_canonical:,}",
        f"  └ unique content digests:       {stats.html_pages_unique_digest:,}",
        "",
    ]
    for label, counter in (
        ("By kind", stats.by_kind),
        ("By host", stats.by_host),
        ("By year (last_seen)", stats.by_year),
        ("By source", stats.by_source),
        ("By status", stats.by_status),
        (f"By mimetype (top {top})", stats.by_mimetype),
    ):
        lines.append(label)
        lines.append("-" * len(label))
        for key, count in counter.most_common(top):
            lines.append(f"  {key or '(empty)':<40s} {count:>10,}")
        lines.append("")
    return "\n".join(lines)
