"""Unit tests for the merge step."""

from __future__ import annotations

import csv
from pathlib import Path

from fakethirtyeight import merge


def _write_cdx_shard(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "urlkey",
                "timestamp",
                "original",
                "mimetype",
                "statuscode",
                "digest",
                "length",
                "shard_id",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def _write_sitemap_shard(path: Path, urls: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["url", "source_sitemap_url", "source_sitemap_timestamp"])
        for u in urls:
            writer.writerow([u, "https://fivethirtyeight.com/sitemap.xml", "20200101"])


def test_surt_key_is_stable_and_lowercase():
    a = merge.surt_key("https://fivethirtyeight.com/Features/Politics")
    b = merge.surt_key("https://fivethirtyeight.com/features/politics")
    # Case is normalized
    assert a == b
    assert "com,fivethirtyeight" in a


def test_surt_key_includes_query():
    k = merge.surt_key("https://fivethirtyeight.com/?page=2")
    assert "?page=2" in k


def test_merge_dedupes_across_shards_and_picks_latest_status(tmp_path):
    shards = tmp_path / "shards"
    out = tmp_path / "index.csv"

    _write_cdx_shard(
        shards / "cdx-2014-fivethirtyeight.com.csv",
        [
            {
                "urlkey": "com,fivethirtyeight)/",
                "timestamp": "20140317120000",
                "original": "https://fivethirtyeight.com/",
                "mimetype": "text/html",
                "statuscode": "200",
                "digest": "AAA",
                "length": "1000",
                "shard_id": "cdx-2014-fivethirtyeight.com",
            },
            {
                "urlkey": "com,fivethirtyeight)/about",
                "timestamp": "20140401000000",
                "original": "https://fivethirtyeight.com/about",
                "mimetype": "text/html",
                "statuscode": "200",
                "digest": "BBB",
                "length": "500",
                "shard_id": "cdx-2014-fivethirtyeight.com",
            },
        ],
    )
    _write_cdx_shard(
        shards / "cdx-2020-fivethirtyeight.com.csv",
        [
            {
                "urlkey": "com,fivethirtyeight)/",
                "timestamp": "20200505000000",
                "original": "https://fivethirtyeight.com/",
                "mimetype": "text/html",
                "statuscode": "301",
                "digest": "CCC",
                "length": "0",
                "shard_id": "cdx-2020-fivethirtyeight.com",
            }
        ],
    )

    count = merge.merge(shards_dir=shards, out_path=out)
    assert count == 2

    with out.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    by_key = {r["urlkey"]: r for r in rows}
    root = by_key["com,fivethirtyeight)/"]
    assert root["first_seen_ts"] == "20140317120000"
    assert root["last_seen_ts"] == "20200505000000"
    assert root["latest_status"] == "301"
    assert root["latest_digest"] == "CCC"
    assert root["snapshot_observations"] == "2"
    assert root["source"] == "cdx"
    assert root["host"] == "fivethirtyeight.com"
    assert root["path"] == "/"


def test_merge_folds_in_sitemap_urls(tmp_path):
    shards = tmp_path / "shards"
    out = tmp_path / "index.csv"

    _write_cdx_shard(
        shards / "cdx-2014-fivethirtyeight.com.csv",
        [
            {
                "urlkey": "com,fivethirtyeight)/seen",
                "timestamp": "20140101000000",
                "original": "https://fivethirtyeight.com/seen",
                "mimetype": "text/html",
                "statuscode": "200",
                "digest": "A",
                "length": "1",
                "shard_id": "cdx-2014-fivethirtyeight.com",
            }
        ],
    )
    _write_sitemap_shard(
        shards / "sitemap-fivethirtyeight.com.csv",
        [
            "https://fivethirtyeight.com/seen",  # overlaps with CDX
            "https://fivethirtyeight.com/only-in-sitemap",
        ],
    )

    merge.merge(shards_dir=shards, out_path=out)
    with out.open(encoding="utf-8") as fh:
        rows = {r["url"]: r for r in csv.DictReader(fh)}

    assert "https://fivethirtyeight.com/seen" in rows
    assert "https://fivethirtyeight.com/only-in-sitemap" in rows
    assert rows["https://fivethirtyeight.com/seen"]["source"] == "cdx+sitemap"
    assert rows["https://fivethirtyeight.com/only-in-sitemap"]["source"] == "sitemap"


def test_url_record_first_seen_picks_minimum():
    rec = merge.UrlRecord(urlkey="com,x)/")
    rec.update_cdx(
        url="https://x/",
        timestamp="20200101000000",
        status="200",
        mimetype="text/html",
        digest="A",
        length="10",
    )
    rec.update_cdx(
        url="https://x/",
        timestamp="20180101000000",
        status="200",
        mimetype="text/html",
        digest="B",
        length="11",
    )
    assert rec.first_seen_ts == "20180101000000"
    assert rec.last_seen_ts == "20200101000000"
    assert rec.latest_digest == "A"  # latest = newer timestamp
    assert rec.snapshot_observations == 2
