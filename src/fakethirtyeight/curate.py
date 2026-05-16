"""Curate the merged index into an editorial subset.

Reads the merged ``data/index.csv``, filters to the editorial ``kind``
labels assigned by :mod:`fakethirtyeight.classify`, rolls up sub-URLs that
share a ``rollup_key`` into one row each, and writes
``data/curated.csv``.

The curated CSV is the foundation for the eventual index-page website. It
is intentionally URL-only (no titles, no metadata) — title enrichment
happens later, after snapshot HTML is fetched.
"""

from __future__ import annotations

import csv
import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from fakethirtyeight.classify import EDITORIAL_KINDS, KIND_PROJECT
from fakethirtyeight.paths import DATA_DIR, INDEX_FILE

log = logging.getLogger(__name__)

CURATED_FILE = DATA_DIR / "curated.csv"

CURATED_FIELDS = (
    "kind",
    "rollup_key",
    "url",
    "canonical_key",
    "host",
    "path",
    "first_seen_ts",
    "last_seen_ts",
    "member_url_count",
    "latest_status",
    "latest_mimetype",
)


@dataclass
class CurateSummary:
    total_in: int
    total_kept: int
    out_rows: int
    by_kind: Counter[str]


def curate(
    *,
    index_path: Path = INDEX_FILE,
    out_path: Path = CURATED_FILE,
    kinds: frozenset[str] = EDITORIAL_KINDS,
) -> CurateSummary:
    """Filter to editorial kinds and roll up by ``rollup_key``."""
    if not index_path.exists():
        msg = f"index file not found: {index_path}. Run `merge` first."
        raise FileNotFoundError(msg)

    total_in = 0
    total_kept = 0
    by_kind: Counter[str] = Counter()

    # rollup_key → best representative row + member count.
    groups: dict[str, dict[str, str | int]] = {}

    with index_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            total_in += 1
            kind = row.get("kind") or ""
            if kind not in kinds:
                continue
            # Only successful HTML captures qualify (skip 404, 30x, JSON, etc.)
            if not _is_qualifying(row):
                continue

            total_kept += 1
            rollup_key = row.get("rollup_key") or row.get("urlkey") or ""
            existing = groups.get(rollup_key)
            if existing is None:
                groups[rollup_key] = _seed(row)
            else:
                _merge_into(existing, row)
            by_kind[kind] += 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CURATED_FIELDS)
        writer.writeheader()
        for _, group in sorted(
            groups.items(),
            key=lambda kv: (kv[1]["kind"], kv[1]["url"]),
        ):
            writer.writerow({k: group.get(k, "") for k in CURATED_FIELDS})

    summary = CurateSummary(
        total_in=total_in,
        total_kept=total_kept,
        out_rows=len(groups),
        by_kind=by_kind,
    )
    log.info(
        "curate: %d input rows → %d qualifying → %d rollup groups",
        total_in,
        total_kept,
        len(groups),
    )
    return summary


def _is_qualifying(row: dict[str, str]) -> bool:
    """True for rows that should contribute to the curated subset.

    HTML 200s only — we don't want a soft-404 page or a 30x stub showing up
    as the canonical for an article.
    """
    status = row.get("latest_status") or ""
    mimetype = row.get("latest_mimetype") or ""
    if status == "200" and mimetype == "text/html":
        return True
    return False


def _seed(row: dict[str, str]) -> dict[str, str | int]:
    return {
        "kind": row.get("kind") or "",
        "rollup_key": row.get("rollup_key") or "",
        "url": row.get("url") or "",
        "canonical_key": row.get("canonical_key") or "",
        "host": row.get("host") or "",
        "path": row.get("path") or "",
        "first_seen_ts": row.get("first_seen_ts") or "",
        "last_seen_ts": row.get("last_seen_ts") or "",
        "member_url_count": 1,
        "latest_status": row.get("latest_status") or "",
        "latest_mimetype": row.get("latest_mimetype") or "",
    }


def _merge_into(group: dict[str, str | int], row: dict[str, str]) -> None:
    """Fold one extra index row into an existing rollup group."""
    group["member_url_count"] = int(group["member_url_count"]) + 1  # type: ignore[arg-type]

    ts_first = row.get("first_seen_ts") or ""
    ts_last = row.get("last_seen_ts") or ""
    if ts_first and (
        not group["first_seen_ts"] or ts_first < str(group["first_seen_ts"])
    ):
        group["first_seen_ts"] = ts_first
    if ts_last and ts_last > str(group["last_seen_ts"]):
        group["last_seen_ts"] = ts_last
        # Promote the latest 200 HTML representative for display.
        group["latest_status"] = row.get("latest_status") or ""
        group["latest_mimetype"] = row.get("latest_mimetype") or ""

    # Prefer the *shortest, simplest* URL as the canonical representative.
    # For liveblogs this picks /live-blog/<slug>/ over /live-blog/<slug>/x/y;
    # for projects it picks /<project>/ over /<project>/subpage.
    new_url = row.get("url") or ""
    cur_url = str(group.get("url") or "")
    if new_url and _is_better_representative(new_url, cur_url, group["kind"]):
        group["url"] = new_url
        group["canonical_key"] = row.get("canonical_key") or ""
        group["host"] = row.get("host") or ""
        group["path"] = row.get("path") or ""


def _is_better_representative(candidate: str, current: str, kind: str | int) -> bool:
    """Heuristic: shorter path = better for rollup display.

    For projects specifically we strongly prefer the bare project root
    (e.g. ``/polls/``) over any drill-down.
    """
    if not current:
        return True
    cur_segs = current.count("/")
    cand_segs = candidate.count("/")
    if cand_segs < cur_segs:
        return True
    if cand_segs == cur_segs and len(candidate) < len(current):
        return True
    if kind == KIND_PROJECT and candidate.endswith("/") and not current.endswith("/"):
        return True
    return False
