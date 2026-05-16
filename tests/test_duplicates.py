"""Unit tests for the duplicates report."""

from __future__ import annotations

import csv
from pathlib import Path

from fakethirtyeight import duplicates, merge


def _write_index(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=merge.INDEX_FIELDS)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in merge.INDEX_FIELDS})


def test_digest_groups_collapse_same_content_urls(tmp_path):
    index = tmp_path / "index.csv"
    digest_out = tmp_path / "dup_digest.csv"
    canon_out = tmp_path / "dup_canon.csv"

    _write_index(
        index,
        [
            # Same SHA-1 across 3 distinct URLs
            {
                "urlkey": "k1",
                "url": "https://fivethirtyeight.com/a",
                "canonical_key": "fivethirtyeight.com/a",
                "host": "fivethirtyeight.com",
                "latest_digest": "SAMEDIGEST",
            },
            {
                "urlkey": "k2",
                "url": "https://fivethirtyeight.com/b",
                "canonical_key": "fivethirtyeight.com/b",
                "host": "fivethirtyeight.com",
                "latest_digest": "SAMEDIGEST",
            },
            {
                "urlkey": "k3",
                "url": "https://www.fivethirtyeight.com/b",
                "canonical_key": "fivethirtyeight.com/b",
                "host": "www.fivethirtyeight.com",
                "latest_digest": "SAMEDIGEST",
            },
            # Unique digest — should not appear in the report
            {
                "urlkey": "k4",
                "url": "https://fivethirtyeight.com/c",
                "canonical_key": "fivethirtyeight.com/c",
                "host": "fivethirtyeight.com",
                "latest_digest": "OTHER",
            },
            # Missing digest — should not appear
            {
                "urlkey": "k5",
                "url": "https://fivethirtyeight.com/d",
                "canonical_key": "fivethirtyeight.com/d",
                "host": "fivethirtyeight.com",
                "latest_digest": "-",
            },
            {
                "urlkey": "k6",
                "url": "https://fivethirtyeight.com/d2",
                "canonical_key": "fivethirtyeight.com/d2",
                "host": "fivethirtyeight.com",
                "latest_digest": "-",
            },
        ],
    )

    summary = duplicates.report(
        index_path=index, digest_out=digest_out, canonical_out=canon_out
    )

    assert summary.digest_groups == 1
    assert summary.digest_dupe_urls == 3

    with digest_out.open() as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert rows[0]["digest"] == "SAMEDIGEST"
    assert rows[0]["url_count"] == "3"
    assert rows[0]["host_count"] == "2"


def test_canonical_groups_collapse_soft_dupes(tmp_path):
    index = tmp_path / "index.csv"
    digest_out = tmp_path / "dup_digest.csv"
    canon_out = tmp_path / "dup_canon.csv"

    _write_index(
        index,
        [
            {
                "urlkey": "k1",
                "url": "http://fivethirtyeight.com/x",
                "canonical_key": "fivethirtyeight.com/x",
                "host": "fivethirtyeight.com",
                "latest_digest": "A",
            },
            {
                "urlkey": "k2",
                "url": "https://fivethirtyeight.com/x",
                "canonical_key": "fivethirtyeight.com/x",
                "host": "fivethirtyeight.com",
                "latest_digest": "B",
            },
            {
                "urlkey": "k3",
                "url": "https://www.fivethirtyeight.com/x/",
                "canonical_key": "fivethirtyeight.com/x",
                "host": "www.fivethirtyeight.com",
                "latest_digest": "C",
            },
            # Distinct canonical
            {
                "urlkey": "k4",
                "url": "https://fivethirtyeight.com/y",
                "canonical_key": "fivethirtyeight.com/y",
                "host": "fivethirtyeight.com",
                "latest_digest": "D",
            },
        ],
    )

    summary = duplicates.report(
        index_path=index, digest_out=digest_out, canonical_out=canon_out
    )

    assert summary.canonical_groups == 1
    assert summary.canonical_dupe_urls == 3

    with canon_out.open() as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["canonical_key"] == "fivethirtyeight.com/x"
    assert rows[0]["url_count"] == "3"
    assert rows[0]["host_count"] == "2"


def test_report_raises_when_index_missing(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        duplicates.report(index_path=tmp_path / "missing.csv")
