"""Unit tests for the Wayback feed walker's RSS/Atom/podcast parser."""

from __future__ import annotations

from fakethirtyeight.feeds import _canonical_enclosure, _parse_feed


def test_canonical_enclosure_strips_podtrac_redirect_chain():
    podtrac = (
        "https://www.podtrac.com/pts/redirect.mp3/pscrb.fm/rss/p/"
        "traffic.megaphone.fm/ESP9835845353.mp3?updated=1742000000"
    )
    assert (
        _canonical_enclosure(podtrac)
        == "https://traffic.megaphone.fm/ESP9835845353.mp3"
    )


def test_canonical_enclosure_strips_query_only_when_no_megaphone_id():
    plain = "https://example.com/audio.mp3?download=1"
    assert _canonical_enclosure(plain) == "https://example.com/audio.mp3"


def test_canonical_enclosure_preserves_megaphone_direct_url():
    assert (
        _canonical_enclosure("https://traffic.megaphone.fm/ESP9835845353.mp3")
        == "https://traffic.megaphone.fm/ESP9835845353.mp3"
    )


def test_canonical_enclosure_normalizes_case_of_episode_id():
    """Episode IDs in tracking URLs occasionally appear in lower case;
    canonicalize to upper so they roll up with the classifier's key."""
    assert (
        _canonical_enclosure("https://traffic.megaphone.fm/esp9835845353.mp3")
        == "https://traffic.megaphone.fm/ESP9835845353.mp3"
    )


# --- blog feed parsing ---------------------------------------------------


_RSS_BLOG_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>FiveThirtyEight</title>
    <item>
      <title>Economically, Obama Is No Jimmy Carter</title>
      <link>http://fivethirtyeight.blogs.nytimes.com/2012/05/30/economically-obama-is-no-jimmy-carter/</link>
      <pubDate>Wed, 30 May 2012 10:00:04 +0000</pubDate>
      <dc:creator>By NATE SILVER</dc:creator>
    </item>
    <item>
      <title>In Wisconsin, Walker Is Likely to Survive Recall</title>
      <link>http://fivethirtyeight.blogs.nytimes.com/2012/05/24/in-wisconsin-walker-is-likely-to-survive-recall/?utm_source=feedburner</link>
      <pubDate>Thu, 24 May 2012 18:12:53 +0000</pubDate>
      <dc:creator>By NATE SILVER</dc:creator>
    </item>
  </channel>
</rss>"""


def test_parse_feed_rss_blog_items():
    entries = _parse_feed(_RSS_BLOG_FEED)
    assert len(entries) == 2
    e = entries[0]
    assert e.url == (
        "http://fivethirtyeight.blogs.nytimes.com/2012/05/30/economically-obama-is-no-jimmy-carter/"
    )
    assert e.title == "Economically, Obama Is No Jimmy Carter"
    assert e.byline == "NATE SILVER"
    assert e.published_at.startswith("2012-05-30T10:00:04")


def test_parse_feed_strips_utm_query_params_from_link():
    entries = _parse_feed(_RSS_BLOG_FEED)
    assert "utm_source" not in entries[1].url


# --- podcast feed parsing ------------------------------------------------


_PODCAST_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>FiveThirtyEight Politics</title>
    <item>
      <title>Is The Era Of 'Macho Man' Politics Here?</title>
      <guid>54d0fba4-960d-11ef-af70-1fba7f742bd7</guid>
      <pubDate>Mon, 03 Mar 2025 21:41:21 +0000</pubDate>
      <itunes:author>ABC News, 538, FiveThirtyEight, Galen Druke</itunes:author>
      <enclosure url="https://www.podtrac.com/pts/redirect.mp3/pscrb.fm/rss/p/traffic.megaphone.fm/ESP9835845353.mp3?updated=1741000000" length="0" type="audio/mpeg"/>
    </item>
  </channel>
</rss>"""


def test_parse_feed_podcast_uses_enclosure_when_link_missing():
    entries = _parse_feed(_PODCAST_FEED)
    assert len(entries) == 1
    e = entries[0]
    # Enclosure replaces the missing <link>, redirect chain stripped.
    assert e.url == "https://traffic.megaphone.fm/ESP9835845353.mp3"
    assert e.title == "Is The Era Of 'Macho Man' Politics Here?"
    # Show-level credit from itunes:author falls in as byline.
    assert e.byline == "ABC News, 538, FiveThirtyEight, Galen Druke"
    assert e.published_at.startswith("2025-03-03T21:41:21")


_PODCAST_FEED_WITH_CREATOR = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <item>
      <title>An Episode With Both Tags</title>
      <pubDate>Mon, 03 Mar 2025 21:41:21 +0000</pubDate>
      <dc:creator>Galen Druke</dc:creator>
      <itunes:author>ABC News</itunes:author>
      <enclosure url="https://traffic.megaphone.fm/ESP1234567890.mp3" type="audio/mpeg"/>
    </item>
  </channel>
</rss>"""


def test_parse_feed_prefers_dc_creator_over_itunes_author():
    entries = _parse_feed(_PODCAST_FEED_WITH_CREATOR)
    assert entries[0].byline == "Galen Druke"


def test_parse_feed_tolerates_malformed_xml():
    assert _parse_feed(b"not xml") == []
    assert _parse_feed(b"") == []
    assert _parse_feed(b"<rss><channel><item>") == []  # truncated
