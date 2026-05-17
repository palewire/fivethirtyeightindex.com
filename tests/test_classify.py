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
        "article:the-real-mvp-of-the-finals",
    ),
    # DataLab era article
    (
        "https://fivethirtyeight.com/datalab/why-this-poll-matters/",
        KIND_ARTICLE,
        "article:why-this-poll-matters",
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
    # Liveblog rollup — all three URL path variants merge.
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
    (
        "http://fivethirtyeight.com/liveblog/special-coverage-the-2014-midterms/?lpup=99",
        KIND_LIVEBLOG,
        "liveblog:special-coverage-the-2014-midterms",
    ),
    (
        "http://fivethirtyeight.com/liveblogs/2016-election-first-republican-presidential-debate/",
        KIND_LIVEBLOG,
        "liveblog:2016-election-first-republican-presidential-debate",
    ),
    # Pre-projects.fivethirtyeight.com interactive projects
    (
        "http://fivethirtyeight.com/interactives/senate-forecast/",
        KIND_PROJECT,
        "project:senate-forecast",
    ),
    (
        "http://fivethirtyeight.com/interactives/world-cup/",
        KIND_PROJECT,
        "project:world-cup",
    ),
    (
        "http://fivethirtyeight.com/interactives/",
        KIND_SECTION,
        "section:interactives",
    ),
    # /interactives/page/N still routes to paginated via the page-N guard.
    (
        "http://fivethirtyeight.com/interactives/page/2/",
        KIND_PAGINATED,
        None,
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
    # Methodology — only the first segment after /methodology/ counts;
    # deeper paths are Wayback drilldown junk.
    (
        "https://fivethirtyeight.com/methodology/how-our-pollster-ratings-work/",
        KIND_METHODOLOGY,
        "methodology:how-our-pollster-ratings-work",
    ),
    (
        "https://fivethirtyeight.com/methodology/how-our-pollster-ratings-work/API",
        KIND_METHODOLOGY,
        "methodology:how-our-pollster-ratings-work",
    ),
    (
        "https://fivethirtyeight.com/methodology/how-our-nba-predictions-work/:amp:story/amp",
        KIND_METHODOLOGY,
        "methodology:how-our-nba-predictions-work",
    ),
    # Liveblog with a literal space in the slug — URL-decoded and normalized
    # so it merges with its clean sibling.
    (
        "http://fivethirtyeight.com/live-blog/2016-%20election-results-%20coverage/",
        KIND_LIVEBLOG,
        "liveblog:2016-election-results-coverage",
    ),
    # Bare /live-blog/ is the section landing, not an editorial post — must
    # not surface as an empty-slug `liveblog:` rollup.
    (
        "http://fivethirtyeight.com/live-blog/",
        KIND_SECTION,
        "section:live-blog",
    ),
    # NYT-era post (2010-2014) — slug-only rollup namespace.
    (
        "http://fivethirtyeight.blogs.nytimes.com/2012/05/30/economically-obama-is-no-jimmy-carter/",
        KIND_ARTICLE,
        "article:economically-obama-is-no-jimmy-carter",
    ),
    (
        "http://fivethirtyeight.blogs.nytimes.com/2013/11/01/some-slug",
        KIND_ARTICLE,
        "article:some-slug",
    ),
    # Podcast (Megaphone) — same episode reached via direct host,
    # traffic. variant, or podtrac/pscrb redirect chain all roll up
    # to the same ESP-ID key.
    (
        "https://feeds.megaphone.fm/ESP9835845353",
        KIND_PODCAST,
        "podcast:meg/ESP9835845353",
    ),
    (
        "https://traffic.megaphone.fm/ESP9835845353.mp3",
        KIND_PODCAST,
        "podcast:meg/ESP9835845353",
    ),
    (
        "https://www.podtrac.com/pts/redirect.mp3/pscrb.fm/rss/p/traffic.megaphone.fm/ESP9835845353.mp3?updated=1",
        KIND_PODCAST,
        "podcast:meg/ESP9835845353",
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
    # Projects: the landing is its own rollup, each drilldown is its own
    # rollup (so individual congresspeople, NBA players, states, leagues
    # all surface as separate entries).
    (
        "https://projects.fivethirtyeight.com/polls/",
        KIND_PROJECT,
        "project:polls",
    ),
    (
        "https://projects.fivethirtyeight.com/polls/president-trump/",
        KIND_PROJECT,
        "project:polls/president-trump",
    ),
    (
        "https://projects.fivethirtyeight.com/2020-election-forecast/states/",
        KIND_PROJECT,
        "project:2020-election-forecast/states",
    ),
    # Multi-segment drilldown (district level)
    (
        "https://projects.fivethirtyeight.com/2018-midterm-election-forecast/house/california/25/",
        KIND_PROJECT,
        "project:2018-midterm-election-forecast/house/california/25",
    ),
    # data.fivethirtyeight.com (the data publishing landing page)
    (
        "https://data.fivethirtyeight.com/",
        KIND_PROJECT,
        "project:data",
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


def test_project_drilldowns_each_get_unique_rollup_keys():
    """The project landing and each drilldown surface as separate entries."""
    keys = {
        classify(u).rollup_key
        for u in [
            "https://projects.fivethirtyeight.com/polls/",
            "https://projects.fivethirtyeight.com/polls/generic-ballot/",
            "https://projects.fivethirtyeight.com/polls/president-trump/approval/",
        ]
    }
    assert keys == {
        "project:polls",
        "project:polls/generic-ballot",
        "project:polls/president-trump/approval",
    }


def test_project_drilldown_query_string_variants_merge():
    """Tracking-param URL variants still merge into the same drilldown."""
    keys = {
        classify(u).rollup_key
        for u in [
            "https://projects.fivethirtyeight.com/polls/arizona/",
            "https://projects.fivethirtyeight.com/polls/arizona/?ex_cid=538fb",
            "https://projects.fivethirtyeight.com/polls/arizona/?utm_source=twitter",
        ]
    }
    assert keys == {"project:polls/arizona"}
