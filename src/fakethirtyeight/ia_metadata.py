"""Shared helpers for archive.org item metadata."""

from __future__ import annotations

import re

_YEAR_RE = re.compile(r"^(?P<year>20\d{2}|19\d{2})(?:\D|$)")


def year_from_date(value: str) -> str:
    """Return the leading publication year from an IA date value."""
    match = _YEAR_RE.match((value or "").strip())
    return match.group("year") if match else ""
