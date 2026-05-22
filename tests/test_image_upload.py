from __future__ import annotations

import csv
from pathlib import Path

from fakethirtyeight.ia_image_upload import (
    _load_captions,
    _merge_caption,
    _metadata_for,
    upload_one,
)


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_load_captions_keeps_only_successful_rows(tmp_path: Path) -> None:
    captions = tmp_path / "image_captions.csv"
    _write_csv(
        captions,
        [
            {
                "identifier": "fivethirtyeight-image-ok",
                "ai_category": "map",
                "ai_description": "A map of county-level results.",
                "ai_title": "County results map",
                "ai_text": "County-level results",
                "model": "claude-test",
                "status": "ok",
                "error": "",
            },
            {
                "identifier": "fivethirtyeight-image-error",
                "ai_category": "",
                "ai_description": "",
                "ai_title": "",
                "ai_text": "",
                "model": "claude-test",
                "status": "error",
                "error": "timeout",
            },
        ],
    )

    loaded = _load_captions(captions)

    assert loaded == {
        "fivethirtyeight-image-ok": {
            "ai_category": "map",
            "ai_description": "A map of county-level results.",
            "ai_title": "County results map",
            "ai_text": "County-level results",
        }
    }


def test_caption_metadata_overrides_screenshot_category_and_text() -> None:
    rec = _merge_caption(
        {
            "category": "screenshot",
            "caption": "Original caption",
            "alt": "screen shot",
            "article_url": "https://fivethirtyeight.com/features/example/",
            "published_at": "2017-08-09T12:30:00+00:00",
        },
        {
            "ai_category": "chart-screenshot",
            "ai_description": "A screenshot of a line chart.",
            "ai_title": "Line chart screenshot",
            "ai_text": "Trend line",
        },
    )

    metadata = _metadata_for(
        "https://example.com/screen-shot.png",
        rec,
        collection="test-collection",
    )

    assert metadata["title"] == "Line chart screenshot"
    assert metadata["subject"] == ["chart", "screenshot", "graphic", "FiveThirtyEight"]
    assert metadata["date"] == "2017-08-09T12:30:00+00:00"
    assert metadata["year"] == "2017"
    assert str(metadata["description"]).startswith("A screenshot of a line chart.")
    assert "Text visible in image:\nTrend line" in str(metadata["description"])


def test_upload_one_skips_uncaptioned_screenshot(tmp_path: Path) -> None:
    image = tmp_path / "screen-shot.png"
    image.write_bytes(b"png")

    result = upload_one(
        session=None,  # type: ignore[arg-type]
        canonical_url="https://example.com/screen-shot.png",
        file_path=image,
        rec={"category": "screenshot"},
        collection="test-collection",
        contributor="Ben Welsh",
        dry_run=True,
    )

    assert result.status == "skipped"
    assert "out-of-scope category" in result.error


def test_upload_one_skips_ai_classified_chat_screenshot(tmp_path: Path) -> None:
    image = tmp_path / "screen-shot.png"
    image.write_bytes(b"png")
    rec = _merge_caption(
        {"category": "screenshot"},
        {
            "ai_category": "chat",
            "ai_description": "A screenshot of text messages.",
            "ai_title": "Text message screenshot",
        },
    )

    result = upload_one(
        session=None,  # type: ignore[arg-type]
        canonical_url="https://example.com/screen-shot.png",
        file_path=image,
        rec=rec,
        collection="test-collection",
        contributor="Ben Welsh",
        dry_run=True,
    )

    assert result.status == "skipped"
    assert result.error == "out-of-scope category: chat"


def test_upload_one_accepts_ai_classified_map(tmp_path: Path) -> None:
    image = tmp_path / "map.png"
    image.write_bytes(b"png")
    rec = _merge_caption(
        {"category": "screenshot"},
        {
            "ai_category": "map",
            "ai_description": "A map of election results.",
            "ai_title": "Election results map",
        },
    )

    result = upload_one(
        session=None,  # type: ignore[arg-type]
        canonical_url="https://example.com/map.png",
        file_path=image,
        rec=rec,
        collection="test-collection",
        contributor="Ben Welsh",
        dry_run=True,
    )

    assert result.status == "uploaded"
