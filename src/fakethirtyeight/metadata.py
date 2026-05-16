"""Extract minimum metadata (title / byline / published_at) from snapshot HTML.

Walks a priority chain of extraction strategies for each field. Designed to
emit ``""`` rather than raise for fields it can't find — the corpus has 15+
years of template changes and the orchestrator's job is to record what's
present, not to enforce completeness.

Priority for each field (first hit wins):

  title       og:title → twitter:title → JSON-LD headline → <title> → <h1>
  byline      JSON-LD author → meta[name=author] → meta[property=article:author]
              → meta[name=parsely-author] → <a rel=author> → custom DOM patterns
  published_at  meta[property=article:published_time]
              → JSON-LD datePublished → meta[name=parsely-pub-date]
              → <time datetime=...> → URL path year/month fallback
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from bs4 import Tag as Bs4Tag

JsonObj = dict[str, Any]

log = logging.getLogger(__name__)

#: FiveThirtyEight templates almost always suffix ``<title>`` with this. Strip it
#: so the headline column is clean.
_TITLE_SITE_SUFFIXES = (
    " | FiveThirtyEight",
    " - FiveThirtyEight",
    " — FiveThirtyEight",
    " :: FiveThirtyEight",
)

#: The Nate Silver Blogspot era (2008–2010) prepended the site name + tagline
#: to every ``<title>`` tag, e.g.:
#:     "FiveThirtyEight.com: Politics Done Right: Live from Invesco: ..."
#: Strip those prefixes so the headline alone shows up.
_TITLE_SITE_PREFIXES = re.compile(
    r"^FiveThirtyEight(?:\.com)?\s*:\s*"
    r"(?:Politics\s+Done\s+Right\s*:\s*)?",
    re.IGNORECASE,
)

_WP_DATE_FROM_PATH = re.compile(r"^/(?P<year>20\d{2})/(?P<month>\d{2})/")


@dataclass(slots=True)
class Metadata:
    title: str = ""
    byline: str = ""
    published_at: str = ""  # ISO-8601 if available, else empty or YYYY-MM
    extracted_via: str = ""  # debug: which path produced the title


def extract(html: bytes | str, fallback_url: str = "") -> Metadata:
    """Extract title / byline / published_at from snapshot HTML.

    Permissive: returns a :class:`Metadata` with empty strings for any field
    we couldn't find. ``fallback_url`` is used only to recover a year/month
    publish date from Blogspot-era WordPress permalinks when no metadata tags
    are present.
    """
    if not html:
        return Metadata()

    soup = BeautifulSoup(html, "lxml")

    jsonld = _collect_jsonld(soup)
    via_parts: list[str] = []

    title = _extract_title(soup, jsonld)
    if title:
        via_parts.append("title")
    byline = _extract_byline(soup, jsonld)
    if byline:
        via_parts.append("byline")
    published_at = _extract_published_at(soup, jsonld, fallback_url)
    if published_at:
        via_parts.append("date")

    return Metadata(
        title=title,
        byline=byline,
        published_at=published_at,
        extracted_via="+".join(via_parts),
    )


# ---------------------------------------------------------------------------
# Title
# ---------------------------------------------------------------------------


def _extract_title(soup: BeautifulSoup, jsonld: list[JsonObj]) -> str:
    # og:title
    val = _meta(soup, "property", "og:title") or _meta(soup, "name", "og:title")
    if val:
        return _clean_title(val)
    # twitter:title
    val = _meta(soup, "name", "twitter:title") or _meta(
        soup, "property", "twitter:title"
    )
    if val:
        return _clean_title(val)
    # JSON-LD headline
    for obj in jsonld:
        head = _str(obj.get("headline")) or _str(obj.get("name"))
        if head:
            return _clean_title(head)
    # <title>
    if soup.title and soup.title.string:
        return _clean_title(soup.title.string)
    # <h1>
    h1 = soup.find("h1")
    if isinstance(h1, Bs4Tag):
        text = h1.get_text(" ", strip=True)
        if text:
            return _clean_title(text)
    return ""


_TITLE_EDGE_PUNCT = re.compile(r"^[|:\-–—\s]+|[|:\-–—\s]+$")

#: Strings that should be treated as "no title" if they're all that's left
#: after suffix/prefix/edge cleanup. These are the bare site identifications.
_BARE_SITE_NAMES: frozenset[str] = frozenset(
    {"fivethirtyeight", "fivethirtyeight.com"}
)


def _clean_title(raw: str) -> str:
    s = " ".join(raw.split()).strip()
    # Edge-trim first so a leading "| FiveThirtyEight" form normalizes to
    # the bare "FiveThirtyEight" that the bare-site check below can drop.
    s = _TITLE_EDGE_PUNCT.sub("", s).strip()
    for suffix in _TITLE_SITE_SUFFIXES:
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
            break
    s = _TITLE_SITE_PREFIXES.sub("", s, count=1)
    s = _TITLE_EDGE_PUNCT.sub("", s).strip()
    if s.casefold() in _BARE_SITE_NAMES:
        return ""
    return s


# ---------------------------------------------------------------------------
# Byline
# ---------------------------------------------------------------------------


def _extract_byline(soup: BeautifulSoup, jsonld: list[JsonObj]) -> str:
    # JSON-LD author (string, dict, or list of dicts)
    for obj in jsonld:
        author = obj.get("author")
        names = _names_from_author(author)
        if names:
            return _join_names(names)

    # meta[name=author]
    val = _meta(soup, "name", "author")
    if val:
        return _clean_text(val)

    # meta[property=article:author] is sometimes a URL, sometimes a name
    val = _meta(soup, "property", "article:author")
    if val and not val.startswith("http"):
        return _clean_text(val)

    # Parse.ly tags
    val = _meta(soup, "name", "parsely-author")
    if val:
        return _clean_text(val)

    # <a rel="author">
    a = soup.find("a", attrs={"rel": "author"})
    if isinstance(a, Bs4Tag):
        text = a.get_text(" ", strip=True)
        if text:
            return _clean_text(text)

    # FiveThirtyEight-specific DOM patterns. The ESPN-era template used
    # <p class="single-metadata"> with <a class="author">…</a> children.
    for cls in (
        "author",
        "byline",
        "single-metadata",
        "post-info-meta-author",
    ):
        node = soup.find(class_=cls)
        if isinstance(node, Bs4Tag):
            text = node.get_text(" ", strip=True)
            if text:
                return _clean_text(text)

    return ""


def _names_from_author(author: object) -> list[str]:
    if isinstance(author, str):
        return [author]
    if isinstance(author, dict):
        name = _str(cast(JsonObj, author).get("name"))
        return [name] if name else []
    if isinstance(author, list):
        names: list[str] = []
        for entry in author:
            if isinstance(entry, dict):
                name = _str(cast(JsonObj, entry).get("name"))
                if name:
                    names.append(name)
            elif isinstance(entry, str):
                names.append(entry)
        return names
    return []


def _join_names(names: list[str]) -> str:
    cleaned = [_clean_text(n) for n in names if n.strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} and {cleaned[1]}"
    return ", ".join(cleaned[:-1]) + f", and {cleaned[-1]}"


# ---------------------------------------------------------------------------
# Published at
# ---------------------------------------------------------------------------


def _extract_published_at(
    soup: BeautifulSoup, jsonld: list[JsonObj], fallback_url: str
) -> str:
    # Open Graph article:published_time
    val = _meta(soup, "property", "article:published_time")
    if val:
        return _norm_date(val)

    # JSON-LD datePublished
    for obj in jsonld:
        d = _str(obj.get("datePublished"))
        if d:
            return _norm_date(d)

    # Parse.ly publication date
    val = _meta(soup, "name", "parsely-pub-date")
    if val:
        return _norm_date(val)

    # <time datetime=...>
    t = soup.find("time")
    if isinstance(t, Bs4Tag):
        dt = t.get("datetime")
        if isinstance(dt, str) and dt:
            return _norm_date(dt)

    # URL-path fallback (Blogspot era)
    if fallback_url:
        path = urlsplit(fallback_url).path or ""
        m = _WP_DATE_FROM_PATH.match(path)
        if m:
            return f"{m.group('year')}-{m.group('month')}"

    return ""


def _norm_date(raw: str) -> str:
    """Normalize a date string to an ISO-8601-ish representation.

    We deliberately preserve whatever precision the source provided
    (full timestamp, date, or YYYY-MM) rather than guessing.
    """
    s = raw.strip()
    if not s:
        return ""
    # Strip surrounding quotes occasionally seen in JSON-LD.
    s = s.strip('"').strip("'")
    # Already ISO?
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s
    # Common European-style YYYY/MM/DD or YYYY.MM.DD.
    m = re.match(r"^(\d{4})[/.](\d{1,2})[/.](\d{1,2})", s)
    if m:
        y, mo, d = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    return s


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _meta(soup: BeautifulSoup, attr: str, value: str) -> str:
    tag = soup.find("meta", attrs={attr: value})
    if not isinstance(tag, Bs4Tag):
        return ""
    content = tag.get("content")
    if not isinstance(content, str):
        return ""
    return content.strip()


def _collect_jsonld(soup: BeautifulSoup) -> list[JsonObj]:
    """Return a flat list of every JSON-LD object in the page.

    Handles single objects, arrays, and ``@graph`` containers.
    """
    out: list[dict] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        if not isinstance(script, Bs4Tag):
            continue
        text = script.string or script.get_text() or ""
        if not text.strip():
            continue
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            continue
        for obj in _flatten_jsonld(parsed):
            if isinstance(obj, dict):
                out.append(obj)
    return out


def _flatten_jsonld(node: object) -> list[object]:
    if isinstance(node, list):
        result: list[object] = []
        for entry in node:
            result.extend(_flatten_jsonld(entry))
        return result
    if isinstance(node, dict):
        graph = cast(JsonObj, node).get("@graph")
        if isinstance(graph, list):
            return _flatten_jsonld(graph)
        return [node]
    return []


def _str(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _clean_text(s: str) -> str:
    return " ".join(s.split()).strip()
