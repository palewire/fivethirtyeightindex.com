"""Unit tests for the CDX client and response parsing."""

from __future__ import annotations

import json

import httpx

from fakethirtyeight import cdx


def test_parse_page_with_inline_resume_key():
    payload = json.dumps(
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
                "1234",
            ],
            [
                "com,fivethirtyeight)/about",
                "20140401",
                "https://fivethirtyeight.com/about",
                "text/html",
                "200",
                "BBB",
                "5678",
            ],
            ["resumeKey-abc"],
        ]
    )
    rows, resume = cdx._parse_page(payload)
    assert len(rows) == 2
    assert rows[0][0] == "com,fivethirtyeight)/"
    assert resume == "resumeKey-abc"


def test_parse_page_with_separate_resume_key():
    payload = (
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
                    "com,fivethirtyeight)/x",
                    "20150101",
                    "https://fivethirtyeight.com/x",
                    "text/html",
                    "200",
                    "C",
                    "10",
                ],
            ]
        )
        + "\n\nresumeKey-xyz\n"
    )
    rows, resume = cdx._parse_page(payload)
    assert len(rows) == 1
    assert resume == "resumeKey-xyz"


def test_parse_page_empty():
    rows, resume = cdx._parse_page("")
    assert rows == []
    assert resume is None


def test_parse_page_terminal_response_has_no_resume_key():
    payload = json.dumps(
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
                "com,fivethirtyeight)/final",
                "20230101",
                "https://fivethirtyeight.com/final",
                "text/html",
                "200",
                "Z",
                "1",
            ],
        ]
    )
    rows, resume = cdx._parse_page(payload)
    assert len(rows) == 1
    assert resume is None


def test_row_from_list_pads_short_rows():
    row = cdx._row_from_list(["com,x)/", "20200101", "https://x/"])
    assert row.urlkey == "com,x)/"
    assert row.timestamp == "20200101"
    assert row.mimetype == ""
    assert row.length == ""


def test_iter_pages_uses_mocked_transport():
    page1 = json.dumps(
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
                "com,fivethirtyeight)/a",
                "20140101",
                "https://fivethirtyeight.com/a",
                "text/html",
                "200",
                "A",
                "1",
            ],
            ["resume-1"],
        ]
    )
    page2 = json.dumps(
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
                "com,fivethirtyeight)/b",
                "20140102",
                "https://fivethirtyeight.com/b",
                "text/html",
                "200",
                "B",
                "2",
            ],
        ]
    )

    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params))
        if request.url.params.get("resumeKey") == "resume-1":
            return httpx.Response(200, text=page2)
        return httpx.Response(200, text=page1)

    transport = httpx.MockTransport(handler)
    with (
        httpx.Client(transport=transport) as client,
        cdx.CdxClient(client=client, delay=0) as cdx_client,
    ):
        pages = list(
            cdx_client.iter_pages("fivethirtyeight.com", match_type="domain", year=2014)
        )

    assert len(pages) == 2
    assert [r.urlkey for r in pages[0].rows] == ["com,fivethirtyeight)/a"]
    assert pages[0].next_resume_key == "resume-1"
    assert [r.urlkey for r in pages[1].rows] == ["com,fivethirtyeight)/b"]
    assert pages[1].next_resume_key is None
    # Year was translated into from/to params
    assert calls[0]["from"] == "20140101000000"
    assert calls[0]["to"] == "20141231235959"
    assert calls[1]["resumeKey"] == "resume-1"


def test_get_retries_on_5xx(monkeypatch):
    from tenacity import wait_none

    monkeypatch.setattr(cdx.CdxClient._fetch_page.retry, "wait", wait_none())

    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(503, text="Service Unavailable")
        return httpx.Response(
            200,
            text=json.dumps(
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
                    ["com,x)/", "20200101", "https://x/", "text/html", "200", "D", "1"],
                ]
            ),
        )

    transport = httpx.MockTransport(handler)
    with (
        httpx.Client(transport=transport) as client,
        cdx.CdxClient(client=client, delay=0) as cdx_client,
    ):
        pages = list(cdx_client.iter_pages("x.example", match_type="exact"))

    assert attempts["n"] == 3
    assert len(pages) == 1
    assert pages[0].rows[0].urlkey == "com,x)/"


def test_fetch_page_retries_on_truncated_json(monkeypatch):
    """Mid-stream truncation surfaces as a JSONDecodeError and should retry."""
    from tenacity import wait_none

    monkeypatch.setattr(cdx.CdxClient._fetch_page.retry, "wait", wait_none())

    good = json.dumps(
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
            ["com,x)/", "20190101", "https://x/", "text/html", "200", "Z", "1"],
        ]
    )
    # Simulate a partial response on the first attempt that JSON-fails to parse.
    truncated = good[: len(good) // 2]
    responses = iter([truncated, good])

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=next(responses))

    transport = httpx.MockTransport(handler)
    with (
        httpx.Client(transport=transport) as client,
        cdx.CdxClient(client=client, delay=0) as cdx_client,
    ):
        pages = list(cdx_client.iter_pages("x.example", match_type="exact"))

    assert len(pages) == 1
    assert pages[0].rows[0].urlkey == "com,x)/"


def test_iter_pages_handles_empty_first_page():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="")

    transport = httpx.MockTransport(handler)
    with (
        httpx.Client(transport=transport) as client,
        cdx.CdxClient(client=client, delay=0) as cdx_client,
    ):
        pages = list(cdx_client.iter_pages("x.example", match_type="exact"))

    assert pages == [] or all(not p.rows for p in pages)
