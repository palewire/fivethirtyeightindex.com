"""Unit tests for the URL classifier."""

from __future__ import annotations

import pytest

from fakethirtyeight.classify import (
    KIND_ARCHIVE,
    KIND_ARTICLE,
    KIND_AUTH,
    KIND_CONTRIBUTOR,
    KIND_HOMEPAGE,
    KIND_LIVEBLOG,
    KIND_METHODOLOGY,
    KIND_OTHER,
    KIND_PAGINATED,
    KIND_PODCAST,
    KIND_PROJECT,
    KIND_SECTION,
    KIND_TAG,
    KIND_VIDEO,
    classify,
)

CASES: list[tuple[str, str, str]] = [
    # Homepages
    ("https://fivethirtyeight.com/", KIND_HOMEPAGE, "site:/"),
    ("https://projects.fivethirtyeight.com/", KIND_HOMEPAGE, "projects:/"),
    ("https://www.fivethirtyeight.com/", KIND_HOMEPAGE, "site-www:/"),
    # Modern article: /features/<slug>/
    (
        "https://fivethirtyeight.com/features/the-real-mvp-of-the-finals/",
        KIND_ARTICLE,
        "article:features/the-real-mvp-of-the-finals",
    ),
    # DataLab era article
    (
        "https://fivethirtyeight.com/datalab/why-this-poll-matters/",
        KIND_ARTICLE,
        "article:datalab/why-this-poll-matters",
    ),
    # Features comment pagination should be paginated, not article
    (
        "https://fivethirtyeight.com/features/the-slug/comment-page-2/",
        KIND_PAGINATED,
        None,
    ),
    # Features bare landing
    (
        "https://fivethirtyeight.com/features",
        KIND_SECTION,
        "section:features",
    ),
    # Liveblog rollup
    (
        "https://fivethirtyeight.com/live-blog/2020-election-results/foo/bar/",
        KIND_LIVEBLOG,
        "liveblog:2020-election-results",
    ),
    (
        "https://fivethirtyeight.com/live-blog/2020-election-results/",
        KIND_LIVEBLOG,
        "liveblog:2020-election-results",
    ),
    # Videos
    (
        "https://fivethirtyeight.com/videos/some-clip/",
        KIND_VIDEO,
        "video:some-clip",
    ),
    # Podcast
    (
        "https://fivethirtyeight.com/podcasts/episode-100/",
        KIND_PODCAST,
        "podcast:episode-100",
    ),
    # Methodology
    (
        "https://fivethirtyeight.com/methodology/how-our-pollster-ratings-work/",
        KIND_METHODOLOGY,
        "methodology:/methodology/how-our-pollster-ratings-work",
    ),
    # Section landings
    ("https://fivethirtyeight.com/politics/", KIND_SECTION, "section:politics"),
    (
        "https://fivethirtyeight.com/science/coronavirus/",
        KIND_SECTION,
        "section:science/coronavirus",
    ),
    # Section + page → paginated
    (
        "https://fivethirtyeight.com/politics/elections/page/12",
        KIND_PAGINATED,
        None,
    ),
    # /page/2 → paginated
    ("https://fivethirtyeight.com/page/2/", KIND_PAGINATED, None),
    # Tag archives
    ("https://fivethirtyeight.com/tag/donald-trump/", KIND_TAG, None),
    # Contributors
    (
        "https://fivethirtyeight.com/contributors/nate-silver/",
        KIND_CONTRIBUTOR,
        None,
    ),
    # Auth callback
    (
        "https://fivethirtyeight.com/oneid-responder?clientId=abc",
        KIND_AUTH,
        None,
    ),
    # Old WordPress permalink (Blogspot-style .html)
    (
        "http://www.fivethirtyeight.com/2008/05/live-from-invesco.html",
        KIND_ARTICLE,
        "article:wp/2008/05/live-from-invesco",
    ),
    # Old WP permalink trailing slash
    (
        "http://www.fivethirtyeight.com/2009/11/some-post/",
        KIND_ARTICLE,
        "article:wp/2009/11/some-post",
    ),
    # Old WP date archive
    (
        "http://www.fivethirtyeight.com/2008/",
        KIND_ARCHIVE,
        "archive:/2008/",
    ),
    (
        "http://www.fivethirtyeight.com/2008/04/",
        KIND_ARCHIVE,
        "archive:/2008/04/",
    ),
    (
        "http://www.fivethirtyeight.com/2008_05_04_archive.html",
        KIND_ARCHIVE,
        "archive:/2008_05_04_archive.html",
    ),
    # Projects rollup — all sub-paths share rollup_key
    (
        "https://projects.fivethirtyeight.com/polls/",
        KIND_PROJECT,
        "project:polls",
    ),
    (
        "https://projects.fivethirtyeight.com/polls/president-trump/",
        KIND_PROJECT,
        "project:polls",
    ),
    (
        "https://projects.fivethirtyeight.com/2020-election-forecast/states/",
        KIND_PROJECT,
        "project:2020-election-forecast",
    ),
    # Empty / weird
    ("", KIND_OTHER, ""),
]


@pytest.mark.parametrize(("url", "expected_kind", "expected_rollup"), CASES)
def test_classify_kind_and_rollup(
    url: str, expected_kind: str, expected_rollup: str | None
) -> None:
    c = classify(url)
    assert c.kind == expected_kind, (url, c)
    if expected_rollup is not None:
        assert c.rollup_key == expected_rollup, (url, c)


def test_liveblog_sub_urls_all_collapse_to_one_rollup_key():
    keys = {
        classify(u).rollup_key
        for u in [
            "https://fivethirtyeight.com/live-blog/2020-election-results/",
            "https://fivethirtyeight.com/live-blog/2020-election-results/update-1/",
            "https://fivethirtyeight.com/live-blog/2020-election-results/update-2/",
            "https://fivethirtyeight.com/live-blog/2020-election-results/post-id-12345/",
        ]
    }
    assert keys == {"liveblog:2020-election-results"}


def test_project_sub_urls_all_collapse_to_one_rollup_key():
    keys = {
        classify(u).rollup_key
        for u in [
            "https://projects.fivethirtyeight.com/polls/",
            "https://projects.fivethirtyeight.com/polls/generic-ballot/",
            "https://projects.fivethirtyeight.com/polls/president-trump/approval/",
        ]
    }
    assert keys == {"project:polls"}
