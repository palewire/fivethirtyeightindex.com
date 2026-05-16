"""Unit tests for sitemap discovery and parsing."""

from __future__ import annotations

import csv

import httpx

from fakethirtyeight import sitemaps

SAMPLE_URLSET = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://fivethirtyeight.com/features/post-1</loc></url>
  <url><loc>https://fivethirtyeight.com/features/post-2</loc></url>
</urlset>
"""

SAMPLE_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://fivethirtyeight.com/sitemap-1.xml</loc></sitemap>
</sitemapindex>
"""


def test_looks_like_sitemap_matches_path():
    assert sitemaps._looks_like_sitemap("https://fivethirtyeight.com/sitemap.xml", "")
    assert sitemaps._looks_like_sitemap(
        "https://fivethirtyeight.com/news-sitemap.xml", "application/xml"
    )
    assert not sitemaps._looks_like_sitemap(
        "https://fivethirtyeight.com/features/", "text/html"
    )


def test_find_targets_picks_latest_timestamp(tmp_path):
    index = tmp_path / "index.csv"
    with index.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "urlkey",
                "url",
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
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "urlkey": "com,fivethirtyeight)/sitemap.xml",
                "url": "https://fivethirtyeight.com/sitemap.xml",
                "host": "fivethirtyeight.com",
                "path": "/sitemap.xml",
                "query": "",
                "first_seen_ts": "20140101000000",
                "last_seen_ts": "20200101000000",
                "latest_status": "200",
                "latest_mimetype": "application/xml",
                "latest_digest": "X",
                "latest_length": "1",
                "snapshot_observations": "5",
                "source": "cdx",
            }
        )

    targets = sitemaps.find_targets(index)
    assert len(targets) == 1
    assert targets[0].original_url == "https://fivethirtyeight.com/sitemap.xml"
    assert targets[0].timestamp == "20200101000000"


def test_fetch_and_parse_urlset_uses_id_endpoint(monkeypatch):
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, text=SAMPLE_URLSET)

    transport = httpx.MockTransport(handler)
    target = sitemaps.SitemapTarget(
        original_url="https://fivethirtyeight.com/sitemap.xml",
        timestamp="20200101000000",
    )
    with httpx.Client(transport=transport) as client:
        results = sitemaps._fetch_and_parse(client, target, delay=0)

    assert len(results) == 2
    assert results[0][0] == "https://fivethirtyeight.com/features/post-1"
    # Verify we used the `id_` raw-content endpoint
    assert "20200101000000id_/" in seen_urls[0]


def test_fetch_and_parse_follows_sitemap_index(monkeypatch):
    bodies = iter([SAMPLE_INDEX, SAMPLE_URLSET])

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=next(bodies))

    transport = httpx.MockTransport(handler)
    target = sitemaps.SitemapTarget(
        original_url="https://fivethirtyeight.com/sitemap.xml",
        timestamp="20200101000000",
    )
    with httpx.Client(transport=transport) as client:
        results = sitemaps._fetch_and_parse(client, target, delay=0)

    # The index pointed at a urlset that has two URLs.
    urls = [r[0] for r in results]
    assert urls == [
        "https://fivethirtyeight.com/features/post-1",
        "https://fivethirtyeight.com/features/post-2",
    ]


def test_fetch_and_parse_handles_bad_xml():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<not-xml>oops")

    transport = httpx.MockTransport(handler)
    target = sitemaps.SitemapTarget(
        original_url="https://fivethirtyeight.com/sitemap.xml",
        timestamp="20200101000000",
    )
    with httpx.Client(transport=transport) as client:
        results = sitemaps._fetch_and_parse(client, target, delay=0)
    assert results == []
