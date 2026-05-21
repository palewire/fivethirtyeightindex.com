"""Unit tests for the site-data build."""

from __future__ import annotations

import pytest

from fakethirtyeight.site_data import _split_authors, _title_from_url, slugify


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
