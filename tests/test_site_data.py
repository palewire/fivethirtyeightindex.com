"""Unit tests for the site-data build."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from fakethirtyeight.site_data import (
    _build_record,
    _load_podcast_item_urls,
    _load_site_podcasts,
    _lookup_enrichment,
    _normalize_site_date,
    _split_authors,
    _title_from_url,
    _year_from_url,
    slugify,
)


@pytest.mark.parametrize(
    ("rollup_key", "expected"),
    [
        # Single-segment slug — no drilldown.
        ("project:congress-trump-score", ""),
        # Per-member drilldown — title-case the sub-slug.
        ("project:congress-trump-score/a-donald-mceachin", "A Donald Mceachin"),
        ("project:carmelo/lebron-james", "Lebron James"),
        # Multi-segment drilldown — joined with spaces.
        ("project:2018-midterm-election-forecast/house/al/1", "House Al 1"),
        # No namespace prefix → no suffix.
        ("just-a-slug", ""),
    ],
)
def test_drilldown_suffix(rollup_key: str, expected: str):
    from fakethirtyeight.site_data import _drilldown_suffix

    assert _drilldown_suffix(rollup_key) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # Cycle-year project slugs are the main motivation for the fallback.
        ("https://projects.fivethirtyeight.com/2024-election-forecast/", 2024),
        ("https://projects.fivethirtyeight.com/2018-midterm-election-forecast/", 2018),
        ("https://projects.fivethirtyeight.com/election-2016/primary-forecast/", 2016),
        # Multi-year paths take the latest plausible year.
        (
            "https://projects.fivethirtyeight.com/checking-our-work/2020-elections/",
            2020,
        ),
        # No year-like substring → None.
        ("https://fivethirtyeight.com/features/some-slug/", None),
        ("", None),
        # Out-of-range 4-digit substrings (zip code, count) are rejected.
        ("https://example.com/zip/90210/", None),
        ("https://example.com/n/1979/", None),
    ],
)
def test_year_from_url(url: str, expected: int | None):
    assert _year_from_url(url) == expected


@pytest.mark.parametrize(
    ("byline", "expected"),
    [
        ("Nate Silver", ["Nate Silver"]),
        ("Nate Silver and Harry Enten", ["Nate Silver", "Harry Enten"]),
        (
            "Ryan Best, Jay Boice, and Ella Koeze",
            ["Ryan Best", "Jay Boice", "Ella Koeze"],
        ),
        ("Ryan Best, Jay Boice", ["Ryan Best", "Jay Boice"]),
        # Staff / network bylines drop entirely
        ("FiveThirtyEight", []),
        ("FiveThirtyEight.com", []),
        ("ABC News / FiveThirtyEight", []),
        # ESPN co-credited network attribution; no individual reporter.
        ("ESPN and FiveThirtyEight", []),
        # Department / format attributions, not real people.
        ("FiveThirtyEight Staff", []),
        ("FiveThirtyEight Podcasts", []),
        ("FiveThirtyEight Video", []),
        # Truncated / shouted variants of Nate Silver get aliased to canonical.
        ("Nate", ["Nate Silver"]),
        ("NATE SILVER", ["Nate Silver"]),
        # NYT-era all-caps bylines get title-cased.
        ("KEVIN QUEALY", ["Kevin Quealy"]),
        ("MICAH COHEN", ["Micah Cohen"]),
        ("JOHN SIDES", ["John Sides"]),
        # GMA / NYT / etc. network attributions drop.
        ("GMA", []),
        ("Good Morning America", []),
        ("THE NEW YORK TIMES", []),
        # Date-stamp strings the extractor occasionally grabbed.
        ("Published Feb. 16", []),
        ("Updated 3:14 PM", []),
        ("Staff", []),
        ("A FiveThirtyEight Chat", []),
        ("A FiveThirtyEight Podcast", []),
        ("A FiveThirtyEightChat", []),
        ("ABC News Live", []),
        # Pure-numeric strings (years, IDs) aren't names
        ("2017", []),
        ("Nate Silver and 2017", ["Nate Silver"]),
        # Typo aliases normalize to canonical form
        ("Juila Wolfe", ["Julia Wolfe"]),
        ("Laura Bronnner", ["Laura Bronner"]),
        ("meena.ganesan", ["Meena Ganesan"]),
        # Mixed: real author + typo of same author merges to one
        ("Julia Wolfe and Juila Wolfe", ["Julia Wolfe"]),
        # Mix: staff + a real author drops only the staff one
        ("FiveThirtyEight and Nate Silver", ["Nate Silver"]),
        # Role prefixes get stripped
        ("Edited by Oliver Roeder", ["Oliver Roeder"]),
        ("By Nate Silver", ["Nate Silver"]),
        ("Written by Walt Hickey", ["Walt Hickey"]),
        # Leading-dash attribution from Blogspot-era comment pages
        ("-- Nate Silver", ["Nate Silver"]),
        ("-- Sean Quinn", ["Sean Quinn"]),
        ("— Nate Silver", ["Nate Silver"]),
        # Pipe-separated multi-credit. The artist credit ("Art by ...") is a
        # production attribution, not a reporter byline — drop it.
        (
            "Trevor Martin | Art by yesyesno",
            ["Trevor Martin"],
        ),
        ("Sam Smith and Photos by Gabriella Demczuk", ["Sam Smith"]),
        # Empty / whitespace
        ("", []),
        ("   ", []),
        # Dedup case-insensitively
        ("Nate Silver and nate silver", ["Nate Silver"]),
        # Blogspot Atom feed format: `email@host (Real Name)` → name only.
        ("noreply@blogger.com (Nate Silver)", ["Nate Silver"]),
        ("someone@example.com (Harry Enten)", ["Harry Enten"]),
    ],
)
def test_split_authors(byline: str, expected: list[str]) -> None:
    assert _split_authors(byline) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Nate Silver", "nate-silver"),
        ("Harry Enten", "harry-enten"),
        ("Ryan Best", "ryan-best"),
        ("José Ramírez", "jose-ramirez"),  # accents stripped
        ("Anna O'Brien", "anna-o-brien"),
        ("   spaces around   ", "spaces-around"),
        ("", ""),
    ],
)
def test_slugify(text: str, expected: str) -> None:
    assert slugify(text) == expected


def test_title_from_url_falls_back_cleanly():
    assert (
        _title_from_url(
            "https://fivethirtyeight.com/features/the-real-mvp-of-the-finals/"
        )
        == "The Real Mvp Of The Finals"
    )
    assert (
        _title_from_url(
            "http://www.fivethirtyeight.com/2008/05/whats-wrong-with-battleground.html"
        )
        == "Whats Wrong With Battleground"
    )
    assert _title_from_url("") == ""
    assert _title_from_url("https://fivethirtyeight.com/") == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("20150502234807", "2015-05-02"),
        ("20150502", "2015-05-02"),
        ("201505", "2015-05"),
        ("2015-05-02T23:48:07+00:00", "2015-05-02T23:48:07+00:00"),
        ("", ""),
    ],
)
def test_normalize_site_date(raw: str, expected: str) -> None:
    assert _normalize_site_date(raw) == expected


def test_build_record_normalizes_wayback_timestamp_fallback_date() -> None:
    record = _build_record(
        {
            "rollup_key": "article:features/but-first-a-word-from-100-podcast-sponsors",
            "kind": "article",
            "url": "http://fivethirtyeight.com/features/but-first-a-word-from-100-podcast-sponsors/",
            "first_seen_ts": "20150502234807",
            "last_seen_ts": "20150502234807",
        },
        None,
    )

    assert record is not None
    assert record.date == "2015-05-02"
    assert record.year == 2015


def test_lookup_enrichment_uses_current_rollup_for_stale_curated_id() -> None:
    current_key = "article:politics-podcast-what-the-debt-ceiling-and-george-santoss-career-have-in-common"
    enrich = {
        current_key: {
            "title": "Politics Podcast: What The Debt Ceiling And George Santos’s Career Have In Common",
            "published_at": "2023-01-23T23:13:49+00:00",
        }
    }

    row = {
        "rollup_key": (
            "article:features/politics-podcast-what-the-debt-ceiling-and-george-santoss-career-have-in-common%EF%BF%BC"
        ),
        "url": (
            "https://fivethirtyeight.com/features/"
            "politics-podcast-what-the-debt-ceiling-and-george-santoss-career-have-in-common%EF%BF%BC/"
        ),
    }

    assert _lookup_enrichment(row, enrich) is enrich[current_key]


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_load_podcast_item_urls_maps_only_uploaded_rows(tmp_path: Path) -> None:
    metadata = tmp_path / "podcast_metadata.csv"
    upload_log = tmp_path / "podcast_upload_log.csv"
    _write_csv(
        metadata,
        [
            {
                "mp3_url": "https://traffic.megaphone.fm/ESP1234567890.mp3",
                "identifier": "fivethirtyeight-politics-esp1234567890",
                "megaphone_id": "ESP1234567890",
                "source_article_url": "https://fivethirtyeight.com/features/politics-episode/",
            },
            {
                "mp3_url": "https://traffic.megaphone.fm/ESP1234567891.mp3",
                "identifier": "fivethirtyeight-politics-esp1234567891",
                "megaphone_id": "ESP1234567891",
                "source_article_url": "https://fivethirtyeight.com/features/skipped-episode/",
            },
        ],
    )
    _write_csv(
        upload_log,
        [
            {
                "identifier": "fivethirtyeight-politics-esp1234567890",
                "uploaded_at": "2026-05-21T00:00:00+00:00",
                "status": "uploaded",
                "files": "episode.mp3",
                "error": "",
            },
            {
                "identifier": "fivethirtyeight-politics-esp1234567891",
                "uploaded_at": "2026-05-21T00:00:01+00:00",
                "status": "dry_run",
                "files": "episode.mp3",
                "error": "",
            },
        ],
    )

    assert _load_podcast_item_urls(
        metadata_path=metadata,
        upload_log_path=upload_log,
    ) == {
        "podcast:meg/ESP1234567890": (
            "https://archive.org/details/fivethirtyeight-politics-esp1234567890"
        ),
        "article:politics-episode": (
            "https://archive.org/details/fivethirtyeight-politics-esp1234567890"
        ),
        "article:features/politics-episode": (
            "https://archive.org/details/fivethirtyeight-politics-esp1234567890"
        ),
    }


def test_load_site_podcasts_groups_uploaded_items_by_series(tmp_path: Path) -> None:
    metadata = tmp_path / "podcast_metadata.csv"
    upload_log = tmp_path / "podcast_upload_log.csv"
    _write_csv(
        metadata,
        [
            {
                "mp3_url": "https://traffic.megaphone.fm/ESP1234567890.mp3",
                "identifier": "fivethirtyeight-politics-esp1234567890",
                "title": "Politics episode",
                "date": "2020-04-15T10:30:00+00:00",
                "show": "FiveThirtyEight Politics",
                "show_slug": "politics",
                "megaphone_id": "ESP1234567890",
            },
            {
                "mp3_url": "https://traffic.megaphone.fm/ESP1234567891.mp3",
                "identifier": "fivethirtyeight-podcast-19-esp1234567891",
                "title": "",
                "date": "2020-05-01",
                "show": "PODCAST-19: FiveThirtyEight on the Novel Coronavirus",
                "show_slug": "",
                "megaphone_id": "ESP1234567891",
            },
        ],
    )
    _write_csv(
        upload_log,
        [
            {
                "identifier": "fivethirtyeight-politics-esp1234567890",
                "uploaded_at": "2026-05-21T00:00:00+00:00",
                "status": "uploaded",
                "files": "episode.mp3",
                "error": "",
            },
            {
                "identifier": "fivethirtyeight-podcast-19-esp1234567891",
                "uploaded_at": "2026-05-21T00:00:01+00:00",
                "status": "uploaded",
                "files": "episode.mp3",
                "error": "",
            },
        ],
    )

    podcasts = _load_site_podcasts(metadata_path=metadata, upload_log_path=upload_log)

    assert [p.series_slug for p in podcasts] == ["politics", "podcast-19"]
    assert podcasts[0].url == (
        "https://archive.org/details/fivethirtyeight-politics-esp1234567890"
    )
    assert podcasts[1].series == "Podcast 19"
    assert podcasts[1].title == "Podcast 19 (2020-05-01)"
