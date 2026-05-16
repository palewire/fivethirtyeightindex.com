"""Classify each indexed URL by editorial kind.

Assigns one of a fixed set of ``kind`` labels per URL, plus a ``rollup_key``
that groups URLs belonging to the same logical entity (e.g. all sub-permalinks
of a liveblog, or all drill-down pages of a project) so the curated subset
can emit one row per logical thing.

Rules are deliberately FiveThirtyEight-specific and informed by inspecting
the live corpus. They live here, not in :mod:`canonicalize`, because the
classifier is lossy and opinionated whereas canonicalization is generic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

KIND_ARTICLE = "article"
KIND_LIVEBLOG = "liveblog"
KIND_PROJECT = "project"
KIND_SECTION = "section"
KIND_PODCAST = "podcast"
KIND_VIDEO = "video"
KIND_METHODOLOGY = "methodology"
KIND_CONTRIBUTOR = "contributor"
KIND_HOMEPAGE = "homepage"
KIND_TAG = "tag"
KIND_PAGINATED = "paginated"
KIND_AUTH = "auth"
KIND_ARCHIVE = "archive"
KIND_OTHER = "other"

#: kinds that belong in the curated "editorial corpus" output.
#: Section landings and the homepage are intentionally excluded — they're
#: navigational, not editorial, and shift over time.
EDITORIAL_KINDS: frozenset[str] = frozenset(
    {
        KIND_ARTICLE,
        KIND_LIVEBLOG,
        KIND_PROJECT,
        KIND_PODCAST,
        KIND_VIDEO,
        KIND_METHODOLOGY,
    }
)

# Section landings on the main host (the trailing /<section>/ root or
# /<section>/<subsection>/ root, with no page-N or other noise).
_SECTIONS: frozenset[str] = frozenset(
    {
        "politics",
        "sports",
        "science",
        "life",
        "economics",
        "culture",
    }
)

# WordPress-style early permalinks under www.fivethirtyeight.com:
# /2008/05/some-slug.html  OR  /2008/05/some-slug/
_WP_PERMALINK = re.compile(
    r"^/(?P<year>(?:200[89]|201[0-3]))/(?P<month>\d{2})/(?P<slug>[^/]+?)(?:\.html|/)?$"
)
# WordPress month/year archive pages (drop): /2008/, /2008/05/, /2008_05_15_archive.html...
_WP_DATE_ARCHIVE = re.compile(
    r"^/(?:\d{4}/?$|\d{4}/\d{2}/?$|\d{4}_\d{2}_\d{2}_archive\.html.*)$"
)
# Paginated marker anywhere in the path
_HAS_PAGE_N = re.compile(r"/page/\d+/?$|/page/\d+/")
# /features/<slug>/comment-page-N/
_COMMENT_PAGE = re.compile(r"/comment-page-\d+/?$")

_NUMERIC = re.compile(r"^\d+$")


@dataclass(slots=True, frozen=True)
class Classification:
    kind: str
    #: A grouping key. URLs sharing a rollup_key represent the same logical
    #: entity and should collapse to one row in the curated subset.
    rollup_key: str


def classify(url: str, host: str | None = None) -> Classification:
    """Classify a URL by editorial kind and emit a rollup key.

    ``host`` is optional; if omitted it's parsed from the URL. Empty/unknown
    inputs return ``(KIND_OTHER, '')``.
    """
    if not url:
        return Classification(KIND_OTHER, "")

    parts = urlsplit(url)
    h = (host or parts.hostname or "").lower()
    path = parts.path or "/"
    segs = [s for s in path.split("/") if s]

    # The 2008-2013 Nate Silver era lived at www.fivethirtyeight.com with
    # Blogger/WordPress permalinks. Match those before stripping ``www.``
    # so the modern bare-host rules don't accidentally claim them.
    if h == "www.fivethirtyeight.com":
        if not segs:
            return Classification(KIND_HOMEPAGE, "site-www:/")
        if _WP_DATE_ARCHIVE.match(path):
            return Classification(KIND_ARCHIVE, f"archive:{path}")
        m = _WP_PERMALINK.match(path)
        if m:
            year = m.group("year")
            month = m.group("month")
            slug = m.group("slug")
            return Classification(KIND_ARTICLE, f"article:wp/{year}/{month}/{slug}")
        # Non-WP path on www. -> fall through to the bare-host branch by
        # canonicalizing the host now.
        h = "fivethirtyeight.com"

    # Strip ``www.`` so the main-host rules apply uniformly.
    bare_host = h[4:] if h.startswith("www.") else h

    # ---- projects.fivethirtyeight.com -----------------------------------
    if bare_host == "projects.fivethirtyeight.com":
        if not segs:
            return Classification(KIND_HOMEPAGE, "projects:/")
        return Classification(KIND_PROJECT, f"project:{segs[0]}")

    # ---- main site ------------------------------------------------------
    if bare_host == "fivethirtyeight.com":
        # Homepage
        if not segs:
            return Classification(KIND_HOMEPAGE, "site:/")

        first = segs[0]

        # /oneid-responder is Disney auth callback noise
        if first == "oneid-responder":
            return Classification(KIND_AUTH, f"auth:{path}")

        # Paginated index pages
        if first == "page" or _HAS_PAGE_N.search(path):
            return Classification(KIND_PAGINATED, f"paginated:{path}")

        # Tag archives
        if first == "tag":
            return Classification(KIND_TAG, f"tag:{path}")

        # Author/contributor archives
        if first == "contributors":
            return Classification(KIND_CONTRIBUTOR, f"contrib:{path}")

        # Video player iframes (different from /videos/ posts)
        if first == "player":
            return Classification(KIND_OTHER, f"player:{path}")

        # Liveblogs — rollup by second segment (the liveblog slug). The
        # canonical path is `/live-blog/` (hyphenated) but the early site
        # used `/liveblog/` and `/liveblogs/` as well. All variants roll up
        # to the same `liveblog:<slug>` key so dupes across URL forms merge.
        if first in {"live-blog", "liveblog", "liveblogs"}:
            if len(segs) >= 2:
                return Classification(KIND_LIVEBLOG, f"liveblog:{segs[1]}")
            return Classification(KIND_LIVEBLOG, "liveblog:")

        # Features articles
        if first == "features":
            if len(segs) == 2:
                return Classification(KIND_ARTICLE, f"article:features/{segs[1]}")
            # /features/<slug>/comment-page-N/ → paginated
            if _COMMENT_PAGE.search(path):
                return Classification(KIND_PAGINATED, f"paginated:{path}")
            # /features (landing index)
            if len(segs) == 1:
                return Classification(KIND_SECTION, "section:features")
            # Anything deeper that isn't comments is "other" noise
            return Classification(KIND_OTHER, f"other:{path}")

        # DataLab era articles
        if first == "datalab" and len(segs) == 2:
            return Classification(KIND_ARTICLE, f"article:datalab/{segs[1]}")

        # Pre-`projects.fivethirtyeight.com` interactive projects lived at
        # `/interactives/<slug>/` (and were paginated for archive views,
        # already routed to `paginated` above). Roll up by slug into the
        # same `project:<slug>` namespace as the subdomain projects.
        if first == "interactives":
            if len(segs) == 1:
                return Classification(KIND_SECTION, "section:interactives")
            return Classification(KIND_PROJECT, f"project:{segs[1]}")

        # Videos & podcasts
        if first == "videos":
            if len(segs) == 2:
                return Classification(KIND_VIDEO, f"video:{segs[1]}")
            if len(segs) == 1:
                return Classification(KIND_SECTION, "section:videos")
            return Classification(KIND_OTHER, f"other:{path}")
        if first in {"podcasts", "podcast"}:
            if len(segs) == 2:
                return Classification(KIND_PODCAST, f"podcast:{segs[1]}")
            if len(segs) == 1:
                return Classification(KIND_SECTION, "section:podcasts")
            return Classification(KIND_OTHER, f"other:{path}")

        # Methodology
        if first == "methodology":
            return Classification(
                KIND_METHODOLOGY,
                f"methodology:{path.rstrip('/')}",
            )

        # Section landings/sub-landings.
        # Only depth=1 (e.g. /politics/) or shallow non-page sub-landings
        # (e.g. /science/coronavirus/) count as sections; everything with
        # /page/N or other noise was already routed above.
        if first in _SECTIONS:
            if len(segs) == 1:
                return Classification(KIND_SECTION, f"section:{first}")
            if len(segs) == 2 and not _NUMERIC.fullmatch(segs[1]):
                return Classification(KIND_SECTION, f"section:{first}/{segs[1]}")
            return Classification(KIND_OTHER, f"other:{path}")

        return Classification(KIND_OTHER, f"other:{path}")

    # WordPress / Blogspot-era permalinks on www.fivethirtyeight.com were
    # already handled at the top of this function before bare-host stripping.

    return Classification(KIND_OTHER, f"other:{h}{path}")

    # ---- data.fivethirtyeight.com (tiny, rare) -------------------------
    if bare_host == "data.fivethirtyeight.com":
        return Classification(KIND_PROJECT, f"data:{path}")

    # ---- malformed / unrecognized hosts --------------------------------
    return Classification(KIND_OTHER, f"unknown-host:{h}{path}")
