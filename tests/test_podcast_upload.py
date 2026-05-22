from __future__ import annotations

import csv
import gzip
from pathlib import Path

import pytest
import requests

from fakethirtyeight.download_podcasts import filename_for
from fakethirtyeight.ia_upload import (
    _files_for_row,
    _load_done,
    _metadata_for_row,
    repair_one_podcast_year,
    upload_podcasts,
)
from fakethirtyeight.podcast_metadata import (
    PodcastMetadata,
    _load_article_context,
    _load_feed_dates,
    _load_title_dates,
    _megaphone_id_from_url,
    _normalize_cover_art,
    build_identifier,
    disambiguate_identifiers,
)


def _write_metadata(path: Path, rows: list[dict[str, str]]) -> None:
    fields = list(PodcastMetadata.__dataclass_fields__)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _metadata_row(url: str, identifier: str = "fivethirtyeight-test") -> dict[str, str]:
    return {
        "mp3_url": url,
        "identifier": identifier,
        "title": "Test Episode",
        "creator": "",
        "date": "2020",
        "description": "",
        "show": "FiveThirtyEight Politics",
        "show_slug": "politics",
        "bitrate": "",
        "megaphone_id": "",
        "player_url": "",
        "source_article_url": "",
        "source": url,
        "mediatype": "audio",
        "subject": "podcast;FiveThirtyEight",
        "thumbnail": "",
        "extracted_via": "test",
    }


def _touch_mp3(podcasts_dir: Path, url: str) -> None:
    podcasts_dir.mkdir(parents=True, exist_ok=True)
    (podcasts_dir / filename_for(url)).write_bytes(b"mp3")


def test_podcast_upload_metadata_lists_ben_welsh_as_contributor() -> None:
    row = _metadata_row("https://traffic.megaphone.fm/ESP1234567890.mp3")
    row["date"] = "2020-04-15T10:30:00+00:00"

    metadata = _metadata_for_row(row, collection="fivethirtyeight-podcasts")

    assert metadata["contributor"] == "Ben Welsh"
    assert metadata["publisher"] == "FiveThirtyEight"
    assert metadata["date"] == "2020-04-15T10:30:00+00:00"
    assert metadata["year"] == "2020"


def test_podcast_upload_metadata_uses_richer_subjects_and_external_ids() -> None:
    row = _metadata_row("https://traffic.megaphone.fm/ESP1234567890.mp3")
    row["show"] = "FiveThirtyEight Politics"
    row["show_slug"] = "politics"
    row["megaphone_id"] = "ESP1234567890"
    row["player_url"] = "https://fivethirtyeight.com/player/politics/0/a/"
    row["source_article_url"] = "https://fivethirtyeight.com/features/example/"

    metadata = _metadata_for_row(row, collection="fivethirtyeight-podcasts")

    assert metadata["subject"] == [
        "podcast",
        "FiveThirtyEight",
        "FiveThirtyEight Politics",
        "politics",
    ]
    assert metadata["external-identifier"] == [
        "urn:megaphone:ESP1234567890",
        "urn:fakethirtyeight:podcast-audio-url:https://traffic.megaphone.fm/ESP1234567890.mp3",
        "urn:fakethirtyeight:podcast-player-url:https://fivethirtyeight.com/player/politics/0/a/",
        "urn:fakethirtyeight:source-article-url:https://fivethirtyeight.com/features/example/",
    ]
    assert metadata["originalurl"] == "https://fivethirtyeight.com/features/example/"


def test_dry_run_logs_without_marking_done_or_requiring_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("IA_ACCESS_KEY", raising=False)
    monkeypatch.delenv("IA_SECRET_KEY", raising=False)
    url = "https://traffic.megaphone.fm/ESP1234567890.mp3"
    csv_path = tmp_path / "podcast_metadata.csv"
    podcasts_dir = tmp_path / "podcasts"
    log_path = tmp_path / "podcast_upload_log.csv"
    _write_metadata(csv_path, [_metadata_row(url)])
    _touch_mp3(podcasts_dir, url)

    uploaded, skipped, failed = upload_podcasts(
        dry_run=True,
        csv_path=csv_path,
        podcasts_dir=podcasts_dir,
        log_path=log_path,
    )

    assert (uploaded, skipped, failed) == (0, 1, 0)
    rows = list(csv.DictReader(log_path.open(newline="", encoding="utf-8")))
    assert rows[0]["status"] == "dry_run"
    assert _load_done(log_path) == set()


def test_missing_mp3_is_skipped_not_failed_in_dry_run(tmp_path: Path) -> None:
    url = "https://traffic.megaphone.fm/ESP1234567890.mp3"
    csv_path = tmp_path / "podcast_metadata.csv"
    log_path = tmp_path / "podcast_upload_log.csv"
    _write_metadata(csv_path, [_metadata_row(url)])

    uploaded, skipped, failed = upload_podcasts(
        dry_run=True,
        csv_path=csv_path,
        podcasts_dir=tmp_path / "podcasts",
        log_path=log_path,
    )

    assert (uploaded, skipped, failed) == (0, 1, 0)
    rows = list(csv.DictReader(log_path.open(newline="", encoding="utf-8")))
    assert rows[0]["status"] == "skipped_missing"


def test_invalid_thumbnail_is_not_uploaded(tmp_path: Path) -> None:
    url = "https://traffic.megaphone.fm/ESP1234567890.mp3"
    podcasts_dir = tmp_path / "podcasts"
    thumb = tmp_path / "bad.jpg"
    thumb.write_bytes(b"\xc2\x89PNG\r\n\x1a\n")
    _touch_mp3(podcasts_dir, url)
    row = _metadata_row(url)
    row["thumbnail"] = str(thumb)

    files = _files_for_row(row, podcasts_dir=podcasts_dir)

    assert files == [podcasts_dir / filename_for(url)]


def test_mojibake_png_cover_art_is_repaired() -> None:
    png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    corrupted = png.decode("latin-1").encode("utf-8")

    repaired, mime = _normalize_cover_art(corrupted, "image/")

    assert repaired == png
    assert mime == "image/png"


def test_upload_preflight_rejects_duplicate_uploadable_identifiers(
    tmp_path: Path,
) -> None:
    url1 = "https://traffic.megaphone.fm/ESP1234567890.mp3"
    url2 = "https://traffic.megaphone.fm/ESP1234567891.mp3"
    csv_path = tmp_path / "podcast_metadata.csv"
    podcasts_dir = tmp_path / "podcasts"
    _write_metadata(
        csv_path,
        [
            _metadata_row(url1, identifier="fivethirtyeight-duplicate"),
            _metadata_row(url2, identifier="fivethirtyeight-duplicate"),
        ],
    )
    _touch_mp3(podcasts_dir, url1)
    _touch_mp3(podcasts_dir, url2)

    with pytest.raises(RuntimeError, match=r"duplicate archive\.org identifiers"):
        upload_podcasts(
            dry_run=True,
            csv_path=csv_path,
            podcasts_dir=podcasts_dir,
            log_path=tmp_path / "podcast_upload_log.csv",
        )


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None


class _FakeArchiveItem:
    def __init__(self) -> None:
        self.metadata: dict[str, str] = {}

    def modify_metadata(
        self,
        metadata: dict[str, str],
        request_kwargs: dict[str, str] | None = None,
    ) -> _FakeResponse:
        self.metadata.update(metadata)
        return _FakeResponse()


class _NoopMetadataArchiveItem:
    def modify_metadata(
        self,
        metadata: dict[str, str],
        request_kwargs: dict[str, str] | None = None,
    ) -> _FakeResponse:
        response = requests.Response()
        response.status_code = 400
        response._content = b'{"success":false,"error":"no changes to _meta.xml"}'
        error = requests.HTTPError("400 Client Error")
        error.response = response
        raise error


class _FakeArchiveSession:
    def __init__(
        self, item: _FakeArchiveItem | _NoopMetadataArchiveItem | None = None
    ) -> None:
        self.item = item or _FakeArchiveItem()

    def get_item(self, identifier: str) -> _FakeArchiveItem | _NoopMetadataArchiveItem:
        return self.item


def test_repair_one_podcast_year_patches_only_year_metadata() -> None:
    row = _metadata_row(
        "https://traffic.megaphone.fm/ESP1234567890.mp3",
        identifier="fivethirtyeight-politics-esp1234567890",
    )
    item = _FakeArchiveItem()
    session = _FakeArchiveSession(item)

    result = repair_one_podcast_year(
        session,  # type: ignore[arg-type]
        row,
        year="2020",
        dry_run=False,
    )

    assert result.status == "repaired"
    assert result.identifier == "fivethirtyeight-politics-esp1234567890"
    assert item.metadata == {"year": "2020"}


def test_repair_one_podcast_year_treats_archive_noop_as_repaired() -> None:
    row = _metadata_row(
        "https://traffic.megaphone.fm/ESP1234567890.mp3",
        identifier="fivethirtyeight-politics-esp1234567890",
    )
    session = _FakeArchiveSession(_NoopMetadataArchiveItem())

    result = repair_one_podcast_year(
        session,  # type: ignore[arg-type]
        row,
        year="2020",
        dry_run=False,
    )

    assert result.status == "repaired"
    assert result.error == ""


def test_disambiguate_identifiers_suffixes_only_collisions() -> None:
    one = PodcastMetadata(
        mp3_url="http://c.espnradio.com/audio/2692891/show_2016-02-29-173431.64k.mp3"
    )
    two = PodcastMetadata(
        mp3_url="https://serve.castfire.com/audio/2692891/show_2016-02-29-173431.64k.mp3"
    )
    three = PodcastMetadata(
        mp3_url="https://traffic.megaphone.fm/ESP1234567890.mp3",
        megaphone_id="ESP1234567890",
    )
    one.identifier = "fivethirtyeight-elections-2016-02-29"
    two.identifier = "fivethirtyeight-elections-2016-02-29"
    three.identifier = build_identifier(three)

    disambiguate_identifiers([one, two, three])

    assert one.identifier.startswith("fivethirtyeight-elections-2016-02-29-")
    assert two.identifier.startswith("fivethirtyeight-elections-2016-02-29-")
    assert one.identifier != two.identifier
    assert three.identifier == "fivethirtyeight-esp1234567890"


def test_megaphone_identifiers_prefer_episode_id_over_date() -> None:
    metadata = PodcastMetadata(
        mp3_url="https://traffic.megaphone.fm/ESP1234567890.mp3",
        megaphone_id="ESP1234567890",
        date="2023-08-02",
    )

    assert build_identifier(metadata) == "fivethirtyeight-esp1234567890"


def test_load_feed_dates_extracts_timestamps_by_megaphone_id(tmp_path: Path) -> None:
    feed = tmp_path / "feed-feeds.megaphone.fm.csv"
    _write_csv(
        feed,
        [
            {
                "url": "https://traffic.megaphone.fm/ESP1234567890.mp3",
                "title": "Episode",
                "byline": "FiveThirtyEight",
                "published_at": "2025-03-03T21:41:21+00:00",
                "source_feed_url": "https://feeds.megaphone.fm/ESP8794877317",
                "source_feed_timestamp": "20250305140341",
            }
        ],
    )

    assert _load_feed_dates([feed]) == {"ESP1234567890": "2025-03-03T21:41:21+00:00"}


def test_load_title_dates_keeps_only_unique_matches(tmp_path: Path) -> None:
    enriched = tmp_path / "enriched.csv"
    _write_csv(
        enriched,
        [
            {
                "rollup_key": "article:one",
                "kind": "article",
                "url": "https://example.com/one",
                "snapshot_timestamp": "20200101000000",
                "wayback_url": "",
                "title": "Politics Podcast: A Clear Match",
                "byline": "FiveThirtyEight",
                "published_at": "2020-01-02T03:04:05+00:00",
                "extracted_via": "test",
                "http_status": "200",
                "error": "",
            },
            {
                "rollup_key": "article:two",
                "kind": "article",
                "url": "https://example.com/two",
                "snapshot_timestamp": "20200101000000",
                "wayback_url": "",
                "title": "Duplicate",
                "byline": "FiveThirtyEight",
                "published_at": "2020-01-02T00:00:00+00:00",
                "extracted_via": "test",
                "http_status": "200",
                "error": "",
            },
            {
                "rollup_key": "article:three",
                "kind": "article",
                "url": "https://example.com/three",
                "snapshot_timestamp": "20200101000000",
                "wayback_url": "",
                "title": "Duplicate",
                "byline": "FiveThirtyEight",
                "published_at": "2020-01-03T00:00:00+00:00",
                "extracted_via": "test",
                "http_status": "200",
                "error": "",
            },
        ],
    )

    dates = _load_title_dates(enriched)

    assert dates["a clear match"] == "2020-01-02T03:04:05+00:00"
    assert "duplicate" not in dates


def test_load_article_context_maps_embedded_player_to_article_date(
    tmp_path: Path,
) -> None:
    article = tmp_path / "article.html.gz"
    player_url = (
        "https://fivethirtyeight.com/player/politics/0/a/"
        "?src=https%3A%2F%2Ftraffic.megaphone.fm%2FESP1234567890.mp3"
    )
    with gzip.open(article, "wt", encoding="utf-8") as fh:
        fh.write(f'<iframe src="{player_url}"></iframe>')
    download_log = tmp_path / "article_download_log.csv"
    _write_csv(
        download_log,
        [
            {
                "url": "https://fivethirtyeight.com/features/example/",
                "wayback_url": "https://web.archive.org/web/20200101000000id_/https://fivethirtyeight.com/features/example/",
                "file_path": str(article),
                "bytes": "123",
                "status": "ok",
                "error": "",
            }
        ],
    )
    enriched = tmp_path / "enriched.csv"
    _write_csv(
        enriched,
        [
            {
                "rollup_key": "article:example",
                "kind": "article",
                "url": "https://fivethirtyeight.com/features/example/",
                "snapshot_timestamp": "20200101000000",
                "wayback_url": "",
                "title": "Example",
                "byline": "FiveThirtyEight",
                "published_at": "2020-01-02T03:04:05+00:00",
                "extracted_via": "test",
                "http_status": "200",
                "error": "",
            }
        ],
    )

    context = _load_article_context(
        download_log_path=download_log,
        enriched_path=enriched,
    )

    assert context["https://traffic.megaphone.fm/ESP1234567890.mp3"] == (
        "https://fivethirtyeight.com/features/example/",
        "2020-01-02T03:04:05+00:00",
    )


def test_megaphone_id_from_url_handles_podtrac_redirects() -> None:
    assert (
        _megaphone_id_from_url(
            "https://www.podtrac.com/pts/redirect.mp3/traffic.megaphone.fm/ESP1234567890.mp3"
        )
        == "ESP1234567890"
    )


def test_load_article_context_preserves_full_podtrac_redirect_url(
    tmp_path: Path,
) -> None:
    article = tmp_path / "article.html.gz"
    mp3_url = (
        "https://www.podtrac.com/pts/redirect.mp3/"
        "traffic.megaphone.fm/ESP1234567890.mp3"
    )
    with gzip.open(article, "wt", encoding="utf-8") as fh:
        fh.write(f'<source src="{mp3_url}">')
    download_log = tmp_path / "article_download_log.csv"
    _write_csv(
        download_log,
        [
            {
                "url": "https://fivethirtyeight.com/features/example/",
                "wayback_url": "",
                "file_path": str(article),
                "bytes": "123",
                "status": "ok",
                "error": "",
            }
        ],
    )
    enriched = tmp_path / "enriched.csv"
    _write_csv(
        enriched,
        [
            {
                "rollup_key": "article:example",
                "kind": "article",
                "url": "https://fivethirtyeight.com/features/example/",
                "snapshot_timestamp": "",
                "wayback_url": "",
                "title": "Example",
                "byline": "FiveThirtyEight",
                "published_at": "2020-01-02T03:04:05+00:00",
                "extracted_via": "test",
                "http_status": "200",
                "error": "",
            }
        ],
    )

    context = _load_article_context(
        download_log_path=download_log,
        enriched_path=enriched,
    )

    assert context[mp3_url] == (
        "https://fivethirtyeight.com/features/example/",
        "2020-01-02T03:04:05+00:00",
    )
