import csv
from pathlib import Path

from fakethirtyeight.caption import CAPTION_FIELDS
from fakethirtyeight.caption_review import build_review
from fakethirtyeight.images import LOG_FIELDS, REF_FIELDS


def _write_csv(path: Path, fieldnames: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_build_review_joins_caption_download_and_reference_rows(
    tmp_path: Path,
) -> None:
    captions = tmp_path / "image_captions.csv"
    image_log = tmp_path / "image_download_log.csv"
    refs = tmp_path / "image_references.csv"
    out = tmp_path / "review.html"
    image = tmp_path / "images" / "chart.png"
    image.parent.mkdir()
    image.write_bytes(b"\x89PNG\r\n\x1a\nchart")

    _write_csv(
        captions,
        CAPTION_FIELDS,
        [
            {
                "identifier": "fivethirtyeight-image-one",
                "ai_category": "chart",
                "ai_description": "A chart of vote share.",
                "ai_title": "Vote Share",
                "ai_text": "Vote share 52%",
                "model": "vision-model",
                "status": "ok",
                "error": "",
            }
        ],
    )
    _write_csv(
        image_log,
        LOG_FIELDS,
        [
            {
                "identifier": "fivethirtyeight-image-one",
                "canonical_url": "https://example.com/chart.png",
                "file_path": str(image),
                "bytes": "12",
                "content_type": "image/png",
                "fetched_via": "live",
                "status": "ok",
                "error": "",
            }
        ],
    )
    _write_csv(
        refs,
        REF_FIELDS,
        [
            {
                "identifier": "fivethirtyeight-image-one",
                "article_file": "data/articles/2012/example.html.gz",
                "image_url": "https://example.com/chart.png",
                "canonical_url": "https://example.com/chart.png",
                "alt": "Original alt",
                "caption": "Original caption",
                "kind": "img",
                "category": "screenshot",
            }
        ],
    )

    assert (
        build_review(
            captions_path=captions,
            image_log_path=image_log,
            refs_path=refs,
            out_path=out,
        )
        == 1
    )

    html = out.read_text(encoding="utf-8")
    assert "Vote Share" in html
    assert "A chart of vote share." in html
    assert "Vote share 52%" in html
    assert "screenshot" in html
    assert "https://example.com/chart.png" in html
    assert 'id="review-data"' in html
    assert "data-search=" not in html
    assert "const BATCH_SIZE" in html
    assert 'id="load-more"' in html
    assert "renderNextBatch()" in html


def test_build_review_uses_latest_caption_row_for_identifier(tmp_path: Path) -> None:
    captions = tmp_path / "image_captions.csv"
    image_log = tmp_path / "image_download_log.csv"
    refs = tmp_path / "image_references.csv"
    out = tmp_path / "review.html"
    image = tmp_path / "images" / "chart.png"
    image.parent.mkdir()
    image.write_bytes(b"\x89PNG\r\n\x1a\nchart")

    _write_csv(
        captions,
        CAPTION_FIELDS,
        [
            {
                "identifier": "fivethirtyeight-image-one",
                "ai_category": "",
                "ai_description": "",
                "ai_title": "",
                "ai_text": "",
                "model": "vision-model",
                "status": "error",
                "error": "JSONDecodeError",
            },
            {
                "identifier": "fivethirtyeight-image-one",
                "ai_category": "chart",
                "ai_description": "A repaired chart.",
                "ai_title": "Repaired Chart",
                "ai_text": "Chart text",
                "model": "vision-model",
                "status": "ok",
                "error": "",
            },
        ],
    )
    _write_csv(
        image_log,
        LOG_FIELDS,
        [
            {
                "identifier": "fivethirtyeight-image-one",
                "canonical_url": "https://example.com/chart.png",
                "file_path": str(image),
                "bytes": "12",
                "content_type": "image/png",
                "fetched_via": "live",
                "status": "ok",
                "error": "",
            }
        ],
    )
    _write_csv(
        refs,
        REF_FIELDS,
        [
            {
                "identifier": "fivethirtyeight-image-one",
                "article_file": "data/articles/2012/example.html.gz",
                "image_url": "https://example.com/chart.png",
                "canonical_url": "https://example.com/chart.png",
                "alt": "",
                "caption": "",
                "kind": "img",
                "category": "screenshot",
            }
        ],
    )

    assert (
        build_review(
            captions_path=captions,
            image_log_path=image_log,
            refs_path=refs,
            out_path=out,
        )
        == 1
    )

    html = out.read_text(encoding="utf-8")
    assert "Repaired Chart" in html
    assert "JSONDecodeError" not in html


def test_build_review_keeps_latest_success_when_later_retry_errors(
    tmp_path: Path,
) -> None:
    captions = tmp_path / "image_captions.csv"
    image_log = tmp_path / "image_download_log.csv"
    refs = tmp_path / "image_references.csv"
    out = tmp_path / "review.html"
    image = tmp_path / "images" / "chart.png"
    image.parent.mkdir()
    image.write_bytes(b"\x89PNG\r\n\x1a\nchart")

    _write_csv(
        captions,
        CAPTION_FIELDS,
        [
            {
                "identifier": "fivethirtyeight-image-one",
                "ai_category": "chart",
                "ai_description": "A good chart.",
                "ai_title": "Good Chart",
                "ai_text": "Chart text",
                "model": "vision-model",
                "status": "ok",
                "error": "",
            },
            {
                "identifier": "fivethirtyeight-image-one",
                "ai_category": "",
                "ai_description": "",
                "ai_title": "",
                "ai_text": "",
                "model": "vision-model",
                "status": "error",
                "error": "temporary DNS failure",
            },
        ],
    )
    _write_csv(
        image_log,
        LOG_FIELDS,
        [
            {
                "identifier": "fivethirtyeight-image-one",
                "canonical_url": "https://example.com/chart.png",
                "file_path": str(image),
                "bytes": "12",
                "content_type": "image/png",
                "fetched_via": "live",
                "status": "ok",
                "error": "",
            }
        ],
    )
    _write_csv(
        refs,
        REF_FIELDS,
        [
            {
                "identifier": "fivethirtyeight-image-one",
                "article_file": "data/articles/2012/example.html.gz",
                "image_url": "https://example.com/chart.png",
                "canonical_url": "https://example.com/chart.png",
                "alt": "",
                "caption": "",
                "kind": "img",
                "category": "screenshot",
            }
        ],
    )

    build_review(
        captions_path=captions,
        image_log_path=image_log,
        refs_path=refs,
        out_path=out,
    )

    html = out.read_text(encoding="utf-8")
    assert "Good Chart" in html
    assert "temporary DNS failure" not in html


def test_build_review_prefers_meaningful_error_over_later_dns_error(
    tmp_path: Path,
) -> None:
    captions = tmp_path / "image_captions.csv"
    image_log = tmp_path / "image_download_log.csv"
    refs = tmp_path / "image_references.csv"
    out = tmp_path / "review.html"
    image = tmp_path / "images" / "chart.png"
    image.parent.mkdir()
    image.write_bytes(b"\x89PNG\r\n\x1a\nchart")

    _write_csv(
        captions,
        CAPTION_FIELDS,
        [
            {
                "identifier": "fivethirtyeight-image-one",
                "ai_category": "",
                "ai_description": "",
                "ai_title": "",
                "ai_text": "",
                "model": "vision-model",
                "status": "error",
                "error": "image exceeds 5 MB maximum",
            },
            {
                "identifier": "fivethirtyeight-image-one",
                "ai_category": "",
                "ai_description": "",
                "ai_title": "",
                "ai_text": "",
                "model": "vision-model",
                "status": "error",
                "error": "[Errno 8] nodename nor servname provided",
            },
        ],
    )
    _write_csv(
        image_log,
        LOG_FIELDS,
        [
            {
                "identifier": "fivethirtyeight-image-one",
                "canonical_url": "https://example.com/chart.png",
                "file_path": str(image),
                "bytes": "12",
                "content_type": "image/png",
                "fetched_via": "live",
                "status": "ok",
                "error": "",
            }
        ],
    )
    _write_csv(
        refs,
        REF_FIELDS,
        [
            {
                "identifier": "fivethirtyeight-image-one",
                "article_file": "data/articles/2012/example.html.gz",
                "image_url": "https://example.com/chart.png",
                "canonical_url": "https://example.com/chart.png",
                "alt": "",
                "caption": "",
                "kind": "img",
                "category": "screenshot",
            }
        ],
    )

    build_review(
        captions_path=captions,
        image_log_path=image_log,
        refs_path=refs,
        out_path=out,
    )

    html = out.read_text(encoding="utf-8")
    assert "image exceeds 5 MB maximum" in html
    assert "nodename nor servname provided" not in html
