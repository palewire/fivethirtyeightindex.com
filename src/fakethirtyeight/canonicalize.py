"""URL canonicalization — collapses scheme/host/port/tracking variants.

Produces a stable ``canonical_key`` for each URL so that callers can group
soft-duplicates (e.g. http vs https, ``www.`` vs bare host, the same article
linked with and without a ``utm_source`` query param).

The canonical key is intentionally lossy — it's a *grouping* key, not a
round-trippable URL. Keep the original ``url`` column for display and the
``urlkey`` column for the Wayback-native identity.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlsplit, urlunsplit

# Query parameters that don't change the page content and should be stripped
# from the canonical key. Pattern matches the full param name (case-insensitive).
TRACKING_PARAM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^utm_.*$", re.IGNORECASE),
    re.compile(r"^fbclid$", re.IGNORECASE),
    re.compile(r"^gclid$", re.IGNORECASE),
    re.compile(r"^msclkid$", re.IGNORECASE),
    re.compile(r"^mc_(cid|eid)$", re.IGNORECASE),
    re.compile(r"^_ga$", re.IGNORECASE),
    re.compile(r"^igshid$", re.IGNORECASE),
    re.compile(r"^at_.*$", re.IGNORECASE),
    re.compile(r"^__twitter_impression$", re.IGNORECASE),
    re.compile(r"^ref$", re.IGNORECASE),
    re.compile(r"^ref_(src|url)$", re.IGNORECASE),
    re.compile(r"^xid$", re.IGNORECASE),
    re.compile(r"^cmpid$", re.IGNORECASE),
)


def is_tracking_param(name: str) -> bool:
    return any(p.match(name) for p in TRACKING_PARAM_PATTERNS)


def _normalize_host(host: str) -> str:
    """Lowercase, strip default port, strip leading ``www.``."""
    host = host.lower()
    # Strip default port if present.
    if host.endswith((":80", ":443")):
        host = host.rsplit(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def _normalize_path(path: str) -> str:
    """Collapse the trailing slash unless the path is the site root."""
    if not path:
        return "/"
    if path == "/":
        return "/"
    if path.endswith("/"):
        return path.rstrip("/") or "/"
    return path


def _normalize_query(query: str) -> str:
    """Drop tracking params; sort the rest for stability."""
    if not query:
        return ""
    pairs = parse_qsl(query, keep_blank_values=True)
    kept = [(k, v) for k, v in pairs if not is_tracking_param(k)]
    if not kept:
        return ""
    kept.sort()
    # Rebuild manually so we don't double-encode — parse_qsl already decoded.
    return "&".join(f"{k}={v}" if v != "" else k for k, v in kept)


def canonical_key(url: str) -> str:
    """Return a stable, lossy key for grouping soft-duplicates.

    Examples:

        >>> canonical_key(
        ...     "https://www.fivethirtyeight.com/features/foo/?utm_source=twitter#x"
        ... )
        'fivethirtyeight.com/features/foo'
        >>> canonical_key("http://fivethirtyeight.com:80/features/foo")
        'fivethirtyeight.com/features/foo'
        >>> canonical_key("https://projects.fivethirtyeight.com/")
        'projects.fivethirtyeight.com/'
    """
    if not url:
        return ""
    parts = urlsplit(url.strip())
    host = _normalize_host(parts.hostname or "")
    path = _normalize_path(parts.path or "/")
    query = _normalize_query(parts.query or "")

    key = f"{host}{path}"
    if query:
        key = f"{key}?{query}"
    return key


def canonical_url(url: str) -> str:
    """Return a canonicalized URL (scheme-less) for display.

    Same normalization as :func:`canonical_key` but rendered like a URL.
    """
    parts = urlsplit(url.strip())
    host = _normalize_host(parts.hostname or "")
    path = _normalize_path(parts.path or "/")
    query = _normalize_query(parts.query or "")
    return urlunsplit(("", host, path, query, ""))
