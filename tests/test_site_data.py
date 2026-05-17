"""Unit tests for the site-data build."""

from __future__ import annotations

import pytest

from fakethirtyeight.site_data import (
    _split_authors,
    _title_from_url,
    _year_from_url,
    slugify,
)


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
        # Pipe-separated multi-credit
        (
            "Trevor Martin | Art by yesyesno",
            ["Trevor Martin", "Art by yesyesno"],
        ),
        # Empty / whitespace
        ("", []),
        ("   ", []),
        # Dedup case-insensitively
        ("Nate Silver and nate silver", ["Nate Silver"]),
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
