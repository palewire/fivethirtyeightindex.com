from __future__ import annotations

import csv
from pathlib import Path

import pytest

from fakethirtyeight.download_podcasts import filename_for
from fakethirtyeight.ia_upload import _load_done, _metadata_for_row, upload_podcasts
from fakethirtyeight.podcast_metadata import (
    PodcastMetadata,
    _load_feed_dates,
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

    metadata = _metadata_for_row(row, collection="fivethirtyeight-podcasts")

    assert metadata["contributor"] == "Ben Welsh"
    assert metadata["publisher"] == "FiveThirtyEight"


def test_podcast_upload_metadata_uses_richer_subjects_and_external_ids() -> None:
    row = _metadata_row("https://traffic.megaphone.fm/ESP1234567890.mp3")
    row["show"] = "FiveThirtyEight Politics"
    row["show_slug"] = "politics"
    row["megaphone_id"] = "ESP1234567890"
    row["player_url"] = "https://fivethirtyeight.com/player/politics/0/a/"

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
    ]


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


def test_load_feed_dates_extracts_full_dates_by_megaphone_id(tmp_path: Path) -> None:
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

    assert _load_feed_dates([feed]) == {"ESP1234567890": "2025-03-03"}
