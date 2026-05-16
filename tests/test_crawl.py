"""Integration test for the crawl orchestrator using a mocked CDX transport."""

from __future__ import annotations

import csv
import json
from unittest.mock import patch

import httpx

from fakethirtyeight import crawl, state


def test_shard_id_includes_host_and_year():
    s = crawl.Shard(host="fivethirtyeight.com", year=2014)
    assert s.shard_id == "cdx-2014-fivethirtyeight.com"


def test_shard_id_with_no_year():
    s = crawl.Shard(host="fivethirtyeight.com", year=None)
    assert s.shard_id == "cdx-all-fivethirtyeight.com"


def test_build_default_shards_covers_range():
    shards = crawl.build_default_shards(
        host="fivethirtyeight.com", start_year=2014, end_year=2016
    )
    years = [s.year for s in shards]
    assert years == [2014, 2015, 2016]


def test_run_shard_writes_csv_and_marks_complete(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    pages_text = [
        json.dumps(
            [
                [
                    "urlkey",
                    "timestamp",
                    "original",
                    "mimetype",
                    "statuscode",
                    "digest",
                    "length",
                ],
                [
                    "com,fivethirtyeight)/",
                    "20140317",
                    "https://fivethirtyeight.com/",
                    "text/html",
                    "200",
                    "AAA",
                    "1",
                ],
                [
                    "com,fivethirtyeight)/about",
                    "20140401",
                    "https://fivethirtyeight.com/about",
                    "text/html",
                    "200",
                    "BBB",
                    "2",
                ],
                ["resume-1"],
            ]
        ),
        json.dumps(
            [
                [
                    "urlkey",
                    "timestamp",
                    "original",
                    "mimetype",
                    "statuscode",
                    "digest",
                    "length",
                ],
                [
                    "com,fivethirtyeight)/jobs",
                    "20141001",
                    "https://fivethirtyeight.com/jobs",
                    "text/html",
                    "200",
                    "CCC",
                    "3",
                ],
            ]
        ),
    ]
    page_iter = iter(pages_text)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=next(page_iter))

    transport = httpx.MockTransport(handler)

    real_cdx_client = crawl.CdxClient

    def make_client(**kwargs):
        return real_cdx_client(client=httpx.Client(transport=transport), delay=0)

    with patch.object(crawl, "CdxClient", side_effect=make_client):
        crawl.run(
            [crawl.Shard(host="fivethirtyeight.com", year=2014)],
            workers=1,
            delay=0,
        )

    shard_csv = tmp_path / "data" / "shards" / "cdx-2014-fivethirtyeight.com.csv"
    assert shard_csv.exists()
    with shard_csv.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 3
    assert rows[0]["urlkey"] == "com,fivethirtyeight)/"
    assert rows[-1]["urlkey"] == "com,fivethirtyeight)/jobs"

    final_state = state.load(tmp_path / "data" / "state.json")
    s = final_state.shards["cdx-2014-fivethirtyeight.com"]
    assert s.status == "complete"
    assert s.rows_written == 3
    assert s.pages_fetched == 2
