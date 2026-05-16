"""Unit tests for the metadata extractor."""

from __future__ import annotations

import pytest

from fakethirtyeight.metadata import extract


def test_extract_from_full_html_with_og_and_jsonld():
    html = b"""
    <html><head>
      <title>Some Headline | FiveThirtyEight</title>
      <meta property="og:title" content="Some Headline">
      <meta property="article:published_time" content="2020-11-03T14:30:00Z">
      <meta property="article:author" content="Nate Silver">
      <script type="application/ld+json">
      {"@type":"NewsArticle","headline":"Some Headline","author":{"@type":"Person","name":"Nate Silver"},"datePublished":"2020-11-03T14:30:00Z"}
      </script>
    </head>
    <body><h1>Some Headline</h1><p>Lorem ipsum.</p></body></html>
    """
    md = extract(html, fallback_url="https://fivethirtyeight.com/features/foo/")
    assert md.title == "Some Headline"
    assert md.byline == "Nate Silver"
    assert md.published_at == "2020-11-03T14:30:00Z"
    assert "title" in md.extracted_via
    assert "byline" in md.extracted_via
    assert "date" in md.extracted_via


def test_extract_title_strips_site_suffix():
    html = b"<html><head><title>The Real MVP - FiveThirtyEight</title></head></html>"
    md = extract(html)
    assert md.title == "The Real MVP"


def test_extract_title_drops_when_only_separator_left():
    """A <title> that's just '| FiveThirtyEight' should clean to empty."""
    html = b"<html><head><title>| FiveThirtyEight</title></head></html>"
    assert extract(html).title == ""


def test_extract_title_strips_leftover_edge_separators():
    html = b"<html><head><title>: A Real Headline -</title></head></html>"
    assert extract(html).title == "A Real Headline"


def test_extract_title_strips_blogspot_era_prefix():
    html = (
        b"<html><head><title>FiveThirtyEight.com: Politics Done Right: Live from Invesco</title></head></html>"
    )
    md = extract(html)
    assert md.title == "Live from Invesco"

    html2 = b"<html><head><title>FiveThirtyEight: Politics Done Right: Open Discussion</title></head></html>"
    assert extract(html2).title == "Open Discussion"

    html3 = b"<html><head><title>FiveThirtyEight: Some Standalone Title</title></head></html>"
    assert extract(html3).title == "Some Standalone Title"


def test_extract_byline_joins_multiple_authors():
    html = b"""
    <html><head>
      <script type="application/ld+json">
      [{"@type":"NewsArticle","headline":"x","author":[{"name":"A"},{"name":"B"},{"name":"C"}]}]
      </script>
    </head></html>
    """
    md = extract(html)
    assert md.byline == "A, B, and C"


def test_extract_byline_two_authors():
    html = b"""
    <html><head>
      <script type="application/ld+json">
      {"author":[{"name":"Nate Silver"},{"name":"Harry Enten"}]}
      </script>
    </head></html>
    """
    md = extract(html)
    assert md.byline == "Nate Silver and Harry Enten"


def test_extract_published_from_time_element():
    html = b"""
    <html><body>
      <article>
        <time datetime="2016-11-08T18:00:00-05:00">Election day</time>
      </article>
    </body></html>
    """
    md = extract(html)
    assert md.published_at.startswith("2016-11-08")


def test_extract_published_falls_back_to_url_path_for_wp_era():
    html = b"<html><head><title>Some Blogspot Post</title></head><body><p>Hi</p></body></html>"
    md = extract(html, fallback_url="http://www.fivethirtyeight.com/2008/05/foo.html")
    assert md.title == "Some Blogspot Post"
    assert md.published_at == "2008-05"


def test_extract_handles_jsonld_graph_container():
    html = b"""
    <html><head>
      <script type="application/ld+json">
      {"@context":"https://schema.org","@graph":[
        {"@type":"NewsArticle","headline":"From Graph","author":{"name":"Author X"},
         "datePublished":"2019-07-15"}
      ]}
      </script>
    </head></html>
    """
    md = extract(html)
    assert md.title == "From Graph"
    assert md.byline == "Author X"
    assert md.published_at == "2019-07-15"


def test_extract_with_only_h1_fallback_for_title():
    html = b"<html><body><h1>Just an H1 Headline</h1></body></html>"
    md = extract(html)
    assert md.title == "Just an H1 Headline"


def test_extract_empty_input_returns_empty_metadata():
    md = extract(b"")
    assert md.title == ""
    assert md.byline == ""
    assert md.published_at == ""


def test_extract_handles_broken_jsonld_gracefully():
    html = b"""
    <html><head>
      <title>Resilient</title>
      <script type="application/ld+json">{not even close to json</script>
    </head></html>
    """
    md = extract(html)
    assert md.title == "Resilient"


def test_extract_ignores_author_url_in_article_author_meta():
    html = b"""
    <html><head>
      <meta property="article:author" content="https://fivethirtyeight.com/contributors/nate-silver/">
      <meta name="parsely-author" content="Nate Silver">
    </head></html>
    """
    md = extract(html)
    assert md.byline == "Nate Silver"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2020-01-15T08:00:00Z", "2020-01-15T08:00:00Z"),
        ("2020-01-15", "2020-01-15"),
        ("2020/01/15", "2020-01-15"),
        ("2020.1.5", "2020-01-05"),
        ("not a date", "not a date"),  # passthrough — extractor doesn't reject
        ("", ""),
    ],
)
def test_norm_date_passes_through_or_normalizes(raw: str, expected: str):
    from fakethirtyeight.metadata import _norm_date

    assert _norm_date(raw) == expected
