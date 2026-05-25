from __future__ import annotations

import csv
from pathlib import Path

import pytest

import fakethirtyeight.ia_image_upload as image_upload_mod
from fakethirtyeight.ia_image_upload import (
    _iter_image_rows,
    _load_captions,
    _merge_caption,
    _metadata_for,
    _pending_upload_rows,
    upload_one,
)


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_upload_images_supports_concurrent_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_log = tmp_path / "image_download_log.csv"
    refs = tmp_path / "image_references.csv"
    enriched = tmp_path / "enriched.csv"
    captions = tmp_path / "image_captions.csv"
    upload_log = tmp_path / "image_upload_log.csv"
    one = tmp_path / "one.png"
    two = tmp_path / "two.png"
    one.write_bytes(b"\x89PNG\r\n\x1a\n")
    two.write_bytes(b"\x89PNG\r\n\x1a\n")

    _write_csv(
        image_log,
        [
            {
                "identifier": "fivethirtyeight-image-one",
                "canonical_url": "https://example.com/one.png",
                "file_path": str(one),
                "bytes": "3",
                "content_type": "image/png",
                "fetched_via": "live",
                "status": "ok",
                "error": "",
            },
            {
                "identifier": "fivethirtyeight-image-two",
                "canonical_url": "https://example.com/two.png",
                "file_path": str(two),
                "bytes": "3",
                "content_type": "image/png",
                "fetched_via": "live",
                "status": "ok",
                "error": "",
            },
        ],
    )
    _write_csv(
        refs,
        [
            {
                "identifier": "fivethirtyeight-image-one",
                "article_file": "",
                "article_url": "",
                "image_url": "https://example.com/one.png",
                "canonical_url": "https://example.com/one.png",
                "alt": "",
                "caption": "",
                "kind": "img",
                "category": "chart",
            },
            {
                "identifier": "fivethirtyeight-image-two",
                "article_file": "",
                "article_url": "",
                "image_url": "https://example.com/two.png",
                "canonical_url": "https://example.com/two.png",
                "alt": "",
                "caption": "",
                "kind": "img",
                "category": "map",
            },
        ],
    )
    _write_csv(
        enriched,
        [
            {
                "rollup_key": "",
                "kind": "",
                "url": "https://fivethirtyeight.com/features/example/",
                "snapshot_timestamp": "20170101000000",
                "wayback_url": "",
                "title": "",
                "byline": "",
                "published_at": "",
                "extracted_via": "",
                "http_status": "",
                "error": "",
            }
        ],
    )
    _write_csv(
        captions,
        [
            {
                "identifier": "fivethirtyeight-image-one",
                "ai_category": "chart",
                "ai_description": "A chart.",
                "ai_title": "Chart",
                "ai_text": "",
                "model": "vision-model",
                "status": "ok",
                "error": "",
            },
            {
                "identifier": "fivethirtyeight-image-two",
                "ai_category": "map",
                "ai_description": "A map.",
                "ai_title": "Map",
                "ai_text": "",
                "model": "vision-model",
                "status": "ok",
                "error": "",
            },
        ],
    )

    monkeypatch.setattr(
        image_upload_mod, "_load_credentials", lambda: ("key", "secret")
    )
    monkeypatch.setattr(image_upload_mod, "DATA_DIR", tmp_path / "data")

    uploaded, skipped, failed = image_upload_mod.upload_images(
        collection="test-collection",
        delay=0,
        workers=2,
        image_log_path=image_log,
        refs_path=refs,
        enriched_path=enriched,
        captions_path=captions,
        log_path=upload_log,
        dry_run=True,
    )

    assert (uploaded, skipped, failed) == (2, 0, 0)
    with upload_log.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert {row["status"] for row in rows} == {"uploaded"}
    assert {row["file"] for row in rows} == {"one.png", "two.png"}


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
    assert metadata["publisher"] == "FiveThirtyEight"
    assert metadata["subject"] == ["chart", "graphic", "FiveThirtyEight"]
    assert metadata["external-identifier"] == [
        "urn:fakethirtyeight:image:fivethirtyeight-image-8df60aa51985",
        "urn:fakethirtyeight:image-source-url:https://example.com/screen-shot.png",
        (
            "urn:fakethirtyeight:source-article-url:"
            "https://fivethirtyeight.com/features/example/"
        ),
    ]
    assert metadata["date"] == "2017-08-09T12:30:00+00:00"
    assert metadata["year"] == "2017"
    assert str(metadata["description"]).startswith(
        "AI-generated image description: A screenshot of a line chart."
    )
    assert "Original caption: Original caption" in str(metadata["description"])
    assert "AI-extracted visible text:\nTrend line" in str(metadata["description"])
    assert str(metadata["description"]).endswith(
        "AI disclosure: Image descriptions and visible-text extraction were "
        "generated using Sonnet 4.6 by Anthropic."
    )


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


def test_pending_upload_rows_prefilters_out_of_scope_before_limit() -> None:
    chart_url = "https://example.com/chart.png"
    other_url = "https://example.com/photo.png"
    done_url = "https://example.com/done.png"
    rows = [
        {"canonical_url": other_url, "file_path": "data/images/photo.png"},
        {"canonical_url": chart_url, "file_path": "data/images/chart.png"},
        {"canonical_url": done_url, "file_path": "data/images/done.png"},
    ]
    pending = _pending_upload_rows(
        rows,
        done={"fivethirtyeight-image-8d36a9951cf1"},
        article_meta={
            other_url: {"category": "screenshot"},
            chart_url: {"category": "screenshot"},
            done_url: {"category": "chart"},
        },
        captions={
            "fivethirtyeight-image-7070c09a24bb": {"ai_category": "other"},
            "fivethirtyeight-image-c198f820a1a0": {"ai_category": "chart"},
        },
    )

    assert [row["canonical_url"] for row, _ in pending] == [chart_url]
    assert [rec["category"] for _, rec in pending] == ["chart"]


def test_iter_image_rows_skips_html_content_type(tmp_path: Path) -> None:
    image_log = tmp_path / "image_download_log.csv"
    image_path = tmp_path / "chart.png"
    unknown_path = tmp_path / "unknown.gif"
    html_path = tmp_path / "not-really.png"
    spoofed_path = tmp_path / "spoofed.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    unknown_path.write_bytes(b"GIF89a")
    html_path.write_bytes(b"<!doctype html>")
    spoofed_path.write_bytes(b"<!doctype html>")
    _write_csv(
        image_log,
        [
            {
                "identifier": "fivethirtyeight-image-ok",
                "canonical_url": "https://example.com/chart.png",
                "file_path": str(image_path),
                "bytes": "3",
                "content_type": "image/png",
                "fetched_via": "live",
                "status": "ok",
                "error": "",
            },
            {
                "identifier": "fivethirtyeight-image-html",
                "canonical_url": "https://example.com/not-really.png",
                "file_path": str(html_path),
                "bytes": "1200",
                "content_type": "text/html; charset=utf-8",
                "fetched_via": "live",
                "status": "ok",
                "error": "",
            },
            {
                "identifier": "fivethirtyeight-image-unknown",
                "canonical_url": "https://example.com/unknown.gif",
                "file_path": str(unknown_path),
                "bytes": "3",
                "content_type": "application/octet-stream",
                "fetched_via": "live",
                "status": "ok",
                "error": "",
            },
            {
                "identifier": "fivethirtyeight-image-spoofed",
                "canonical_url": "https://example.com/spoofed.png",
                "file_path": str(spoofed_path),
                "bytes": "1200",
                "content_type": "image/png",
                "fetched_via": "live",
                "status": "ok",
                "error": "",
            },
        ],
    )

    rows = list(_iter_image_rows(image_log))

    assert [row["canonical_url"] for row in rows] == [
        "https://example.com/chart.png",
        "https://example.com/unknown.gif",
    ]


def test_chess_diagram_metadata_is_in_scope() -> None:
    rec = _merge_caption(
        {
            "category": "screenshot",
            "caption": "",
            "alt": "",
            "article_url": "https://fivethirtyeight.com/features/example/",
            "published_at": "2017-08-09T12:30:00+00:00",
        },
        {
            "ai_category": "chess-diagram",
            "ai_description": "A chess board diagram showing a position.",
            "ai_title": "Chess board position",
            "ai_text": "a b c d e f g h",
        },
    )

    metadata = _metadata_for(
        "https://example.com/chess.png",
        rec,
        collection="test-collection",
    )

    assert metadata["subject"] == ["chess", "FiveThirtyEight"]


def test_infographic_metadata_is_in_scope() -> None:
    rec = _merge_caption(
        {
            "category": "screenshot",
            "caption": "",
            "alt": "",
            "article_url": "https://fivethirtyeight.com/features/example/",
            "published_at": "2017-08-09T12:30:00+00:00",
        },
        {
            "ai_category": "infographic",
            "ai_description": "A designed comparison of pizza prices and distances.",
            "ai_title": "Pizza price comparison",
            "ai_text": "Domino's $15.99 Pizza Hut $14.99 Papa John's $16.99",
        },
    )

    metadata = _metadata_for(
        "https://example.com/pizza.png",
        rec,
        collection="test-collection",
    )

    assert metadata["subject"] == ["graphic", "FiveThirtyEight"]


def test_diagram_metadata_is_in_scope_without_diagram_subject() -> None:
    rec = _merge_caption(
        {
            "category": "screenshot",
            "caption": "",
            "alt": "",
            "article_url": "https://fivethirtyeight.com/features/example/",
            "published_at": "2017-08-09T12:30:00+00:00",
        },
        {
            "ai_category": "diagram",
            "ai_description": "A geometry diagram with labeled angles.",
            "ai_title": "Geometry diagram",
            "ai_text": "A B C",
        },
    )

    metadata = _metadata_for(
        "https://example.com/diagram.png",
        rec,
        collection="test-collection",
    )

    assert metadata["subject"] == ["graphic", "FiveThirtyEight"]


def test_artistic_illustration_metadata_is_in_scope() -> None:
    rec = _merge_caption(
        {
            "category": "screenshot",
            "caption": "",
            "alt": "",
            "article_url": "https://fivethirtyeight.com/features/example/",
            "published_at": "2017-08-09T12:30:00+00:00",
        },
        {
            "ai_category": "artistic-illustration",
            "ai_description": "Editorial art of two figures holding masks.",
            "ai_title": "Mask Illustration",
            "ai_text": "",
        },
    )

    metadata = _metadata_for(
        "https://example.com/mask.png",
        rec,
        collection="test-collection",
    )

    assert metadata["subject"] == ["illustration", "FiveThirtyEight"]
