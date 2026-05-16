"""Unit tests for URL canonicalization."""

from __future__ import annotations

import pytest

from fakethirtyeight.canonicalize import (
    canonical_key,
    canonical_url,
    is_tracking_param,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # Identity
        (
            "https://fivethirtyeight.com/features/foo",
            "fivethirtyeight.com/features/foo",
        ),
        # Scheme drops
        ("http://fivethirtyeight.com/features/foo", "fivethirtyeight.com/features/foo"),
        # www. strips
        (
            "https://www.fivethirtyeight.com/features/foo",
            "fivethirtyeight.com/features/foo",
        ),
        # Default ports
        (
            "http://fivethirtyeight.com:80/features/foo",
            "fivethirtyeight.com/features/foo",
        ),
        (
            "https://fivethirtyeight.com:443/features/foo",
            "fivethirtyeight.com/features/foo",
        ),
        # Trailing slash
        (
            "https://fivethirtyeight.com/features/foo/",
            "fivethirtyeight.com/features/foo",
        ),
        ("https://fivethirtyeight.com/", "fivethirtyeight.com/"),
        # Fragments drop
        (
            "https://fivethirtyeight.com/features/foo#section",
            "fivethirtyeight.com/features/foo",
        ),
        # Tracking params drop, real ones survive
        (
            "https://fivethirtyeight.com/features/foo?utm_source=twitter&utm_medium=social",
            "fivethirtyeight.com/features/foo",
        ),
        (
            "https://fivethirtyeight.com/features/foo?fbclid=ABC&page=2",
            "fivethirtyeight.com/features/foo?page=2",
        ),
        (
            "https://fivethirtyeight.com/features/foo?p=12345",
            "fivethirtyeight.com/features/foo?p=12345",
        ),
        # Query param ordering stabilizes
        (
            "https://fivethirtyeight.com/x?b=2&a=1",
            "fivethirtyeight.com/x?a=1&b=2",
        ),
        # Case on host normalizes; case in path preserved
        (
            "https://FiveThirtyEight.COM/Features/Foo",
            "fivethirtyeight.com/Features/Foo",
        ),
        # Subdomains preserved
        (
            "https://projects.fivethirtyeight.com/world-cup/",
            "projects.fivethirtyeight.com/world-cup",
        ),
        # Empty / falsy
        ("", ""),
    ],
)
def test_canonical_key(url: str, expected: str) -> None:
    assert canonical_key(url) == expected


def test_canonical_url_produces_displayable_form():
    out = canonical_url("https://www.fivethirtyeight.com:443/x/?utm_source=foo#y")
    assert out == "//fivethirtyeight.com/x"


@pytest.mark.parametrize(
    "param",
    [
        "utm_source",
        "utm_medium",
        "utm_term",
        "UTM_CAMPAIGN",  # case-insensitive
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "_ga",
        "ref",
        "igshid",
        "at_medium",
    ],
)
def test_is_tracking_param_known(param: str) -> None:
    assert is_tracking_param(param)


@pytest.mark.parametrize(
    "param",
    ["page", "p", "id", "q", "search", "category", "tag"],
)
def test_is_tracking_param_keeps_meaningful(param: str) -> None:
    assert not is_tracking_param(param)


def test_canonical_key_collapses_obvious_dupes_to_same_key():
    keys = {
        canonical_key("http://fivethirtyeight.com/x"),
        canonical_key("https://fivethirtyeight.com/x"),
        canonical_key("https://www.fivethirtyeight.com/x"),
        canonical_key("https://fivethirtyeight.com/x/"),
        canonical_key("https://fivethirtyeight.com/x?utm_source=t"),
        canonical_key("https://fivethirtyeight.com/x#anchor"),
        canonical_key("http://fivethirtyeight.com:80/x"),
    }
    assert len(keys) == 1
    assert keys.pop() == "fivethirtyeight.com/x"
