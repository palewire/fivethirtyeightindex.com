import csv
from pathlib import Path

import httpx
from PIL import Image

from fakethirtyeight.caption import (
    ALLOWED_CATEGORIES,
    CAPTION_FIELDS,
    IN_SCOPE_AI_CATEGORIES,
    MAX_TEXT_CHARS,
    CaptionParseError,
    _classification_payload,
    _classify_one,
    _content_from_response,
    _ensure_caption_header,
    _litellm_headers,
    _litellm_url,
    _parse_response,
    _prepare_model_input,
    _select_targets,
    caption_images,
    infer_caption_category,
)
from fakethirtyeight.images import LOG_FIELDS


def test_litellm_url_accepts_base_or_chat_completions(monkeypatch) -> None:
    monkeypatch.setenv("LITELLM_BASE_URL", "https://llm.example.test/v1")
    assert _litellm_url() == "https://llm.example.test/v1/chat/completions"

    monkeypatch.setenv(
        "LITELLM_BASE_URL", "https://llm.example.test/v1/chat/completions"
    )
    assert _litellm_url() == "https://llm.example.test/v1/chat/completions"


def test_litellm_headers_include_spoofed_user_agent(monkeypatch) -> None:
    monkeypatch.setenv("LITELLM_API_KEY", "secret")
    monkeypatch.setenv("LITELLM_USER_AGENT", "Mozilla/5.0 test")

    headers = _litellm_headers()

    assert headers["Authorization"] == "Bearer secret"
    assert headers["User-Agent"] == "Mozilla/5.0 test"


def test_classify_one_posts_openai_compatible_vision_request(
    tmp_path: Path, monkeypatch
) -> None:
    image = tmp_path / "chart.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nchart")
    monkeypatch.setenv("LITELLM_BASE_URL", "https://llm.example.test/v1")
    seen: dict[str, str | bytes] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["payload"] = request.read()
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"category":"map","description":"A map.",'
                                '"title":"Map","text":"County results"}'
                            )
                        }
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = _classify_one(client, image, model="vision-model")

    assert result == {
        "category": "map",
        "description": "A map.",
        "title": "Map",
        "text": "County results",
    }
    assert seen["url"] == "https://llm.example.test/v1/chat/completions"
    payload = seen["payload"]
    assert isinstance(payload, bytes)
    assert b'"model":"vision-model"' in payload
    assert b'"max_tokens":900' in payload
    assert b'"temperature":0' in payload
    assert b'"response_format":{"type":"json_object"}' in payload
    assert b'"type":"image_url"' in payload
    assert b"data:image/png;base64," in payload


def test_classification_payload_strict_json_caps_text_more_aggressively(
    tmp_path: Path,
) -> None:
    image = tmp_path / "chart.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nchart")

    payload = _classification_payload(image, model="vision-model", strict_json=True)

    assert payload["max_tokens"] == 700
    payload_text = str(payload)
    assert "Your previous response was invalid JSON" in payload_text
    assert 'cap "text" at 250' in payload_text


def test_prepare_model_input_downscales_large_image(tmp_path: Path) -> None:
    image = tmp_path / "large.png"
    Image.new("RGB", (8100, 1000), "white").save(image)

    prepared = _prepare_model_input(image, identifier="large-test")

    assert prepared != image
    assert prepared.suffix == ".jpg"
    with Image.open(prepared) as img:
        assert max(img.size) <= 4096


def test_parse_response_wraps_truncated_json() -> None:
    try:
        _parse_response('{"category":"chart","description":"cut off')
    except CaptionParseError as exc:
        assert "Could not parse model JSON response" in str(exc)
    else:
        raise AssertionError("expected CaptionParseError")


def test_edge_case_visual_categories_are_allowed_and_in_scope() -> None:
    assert "chess-diagram" in ALLOWED_CATEGORIES
    assert "chess-diagram" in IN_SCOPE_AI_CATEGORIES
    assert "infographic" in ALLOWED_CATEGORIES
    assert "infographic" in IN_SCOPE_AI_CATEGORIES
    assert "diagram" in ALLOWED_CATEGORIES
    assert "diagram" in IN_SCOPE_AI_CATEGORIES
    assert "artistic-illustration" in ALLOWED_CATEGORIES
    assert "artistic-illustration" in IN_SCOPE_AI_CATEGORIES
    assert MAX_TEXT_CHARS == 500


def test_infer_caption_category_repairs_chess_board_illustrations() -> None:
    assert (
        infer_caption_category(
            "illustration",
            title="Chess Board Position Diagram",
            description=(
                "Chess board diagram showing a mid-game position with pieces "
                "arranged across the board."
            ),
        )
        == "chess-diagram"
    )


def test_infer_caption_category_does_not_relabel_chess_photos() -> None:
    assert (
        infer_caption_category(
            "photo",
            title="Back to the Future chess machine scene",
            description=(
                "Movie still showing two men and a dog around a "
                "chess-playing machine in a cluttered workshop."
            ),
        )
        == "photo"
    )


def test_infer_caption_category_splits_artistic_illustrations() -> None:
    assert (
        infer_caption_category(
            "illustration",
            title="Two Figures Holding Face Masks Illustration",
            description=(
                "Editorial illustration of two figures holding white face "
                "masks toward each other against a pink sunset sky over water."
            ),
        )
        == "artistic-illustration"
    )


def test_infer_caption_category_splits_explanatory_diagrams() -> None:
    assert (
        infer_caption_category(
            "illustration",
            title="Triangle geometry diagram with labeled sides",
            description=(
                "Geometric diagram of a triangle with sides labeled 1, 2, "
                "and 3, containing a blue diamond shape."
            ),
        )
        == "diagram"
    )


def test_infer_caption_category_repairs_illustrated_data_graphics() -> None:
    assert (
        infer_caption_category(
            "illustration",
            title="Pizza Chain Price Comparison - Los Angeles",
            description=(
                "Comparison of three pizza chains showing Domino's at "
                "$15.99, Pizza Hut at $14.99, and Papa John's at $16.99."
            ),
        )
        == "infographic"
    )


def test_infer_caption_category_refines_infographic_charts() -> None:
    assert (
        infer_caption_category(
            "infographic",
            title="Percentage Grid Heat Map",
            description=(
                "A heat-map style grid of percentages colored from dark red "
                "to light pink indicating magnitude."
            ),
        )
        == "chart"
    )


def test_infer_caption_category_refines_infographic_tables() -> None:
    assert (
        infer_caption_category(
            "infographic",
            title="Best Director Oscar Nominees Ranked by Awards Points",
            description=(
                "Ranking of Best Director Oscar nominees by points from "
                "awards and nominations."
            ),
        )
        == "table"
    )


def test_infer_caption_category_refines_infographic_diagrams() -> None:
    assert (
        infer_caption_category(
            "infographic",
            title="Queuing System Configurations Diagram",
            description=(
                "Diagram illustrating five queuing system configurations: "
                "single-server single-phase and multiserver single-line."
            ),
        )
        == "diagram"
    )


def test_infer_caption_category_excludes_blank_placeholders() -> None:
    assert (
        infer_caption_category(
            "artistic-illustration",
            title="Blank or Dark Image",
            description=(
                "A mostly blank or very dark image with minimal visible content, "
                "likely a placeholder or failed image load."
            ),
        )
        == "other"
    )
    assert (
        infer_caption_category(
            "diagram",
            title="Blank 10x10 Grid",
            description=(
                "A blank 10x10 grid of equal squares with no labels, "
                "data, or content filled in."
            ),
        )
        == "other"
    )


def test_content_from_response_reports_litellm_error_body() -> None:
    data = {
        "error": {
            "message": "model: example was not found",
            "code": "404",
        }
    }

    try:
        _content_from_response(data)
    except RuntimeError as exc:
        assert "model: example was not found" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_ensure_caption_header_adds_ai_text_to_existing_csv(tmp_path: Path) -> None:
    captions = tmp_path / "image_captions.csv"
    old_fields = [field for field in CAPTION_FIELDS if field != "ai_text"]
    with captions.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=old_fields)
        writer.writeheader()
        writer.writerow(
            {
                "identifier": "fivethirtyeight-image-ok",
                "ai_category": "chart",
                "ai_description": "A chart.",
                "ai_title": "Chart",
                "model": "vision-model",
                "status": "ok",
                "error": "",
            }
        )

    _ensure_caption_header(captions)

    rows = list(csv.DictReader(captions.open(newline="", encoding="utf-8")))
    assert rows[0]["identifier"] == "fivethirtyeight-image-ok"
    assert rows[0]["ai_text"] == ""


def test_select_targets_skips_non_image_logged_ok_files(tmp_path: Path) -> None:
    refs = tmp_path / "image_references.csv"
    image_log = tmp_path / "image_download_log.csv"
    good = tmp_path / "good.png"
    bad = tmp_path / "bad.png"
    good.write_bytes(b"\x89PNG\r\n\x1a\nchart")
    bad.write_text("<!doctype html><html>not an image</html>", encoding="utf-8")

    with refs.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["identifier", "canonical_url", "category"]
        )
        writer.writeheader()
        writer.writerow(
            {
                "identifier": "good",
                "canonical_url": "https://example.com/good.png",
                "category": "screenshot",
            }
        )
        writer.writerow(
            {
                "identifier": "bad",
                "canonical_url": "https://example.com/bad.png",
                "category": "screenshot",
            }
        )

    with image_log.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
        writer.writeheader()
        writer.writerow(
            {
                "identifier": "good",
                "canonical_url": "https://example.com/good.png",
                "file_path": str(good),
                "bytes": str(good.stat().st_size),
                "content_type": "image/png",
                "fetched_via": "live",
                "status": "ok",
                "error": "",
            }
        )
        writer.writerow(
            {
                "identifier": "bad",
                "canonical_url": "https://example.com/bad.png",
                "file_path": str(bad),
                "bytes": str(bad.stat().st_size),
                "content_type": "text/html",
                "fetched_via": "wayback",
                "status": "ok",
                "error": "",
            }
        )

    assert _select_targets(refs, image_log, only_screenshots=True) == [("good", good)]


def test_caption_images_force_recaptions_successful_rows(
    tmp_path: Path, monkeypatch
) -> None:
    refs = tmp_path / "image_references.csv"
    image_log = tmp_path / "image_download_log.csv"
    captions = tmp_path / "image_captions.csv"
    image = tmp_path / "good.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nchart")

    with refs.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["identifier", "canonical_url", "category"]
        )
        writer.writeheader()
        writer.writerow(
            {
                "identifier": "good",
                "canonical_url": "https://example.com/good.png",
                "category": "screenshot",
            }
        )
    with image_log.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
        writer.writeheader()
        writer.writerow(
            {
                "identifier": "good",
                "canonical_url": "https://example.com/good.png",
                "file_path": str(image),
                "bytes": str(image.stat().st_size),
                "content_type": "image/png",
                "fetched_via": "live",
                "status": "ok",
                "error": "",
            }
        )
    with captions.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CAPTION_FIELDS)
        writer.writeheader()
        writer.writerow(
            {
                "identifier": "good",
                "ai_category": "chart",
                "ai_description": "Old chart.",
                "ai_title": "Old",
                "ai_text": "",
                "model": "vision-model",
                "status": "ok",
                "error": "",
            }
        )

    monkeypatch.setenv("LITELLM_API_KEY", "secret")
    monkeypatch.setenv("LITELLM_BASE_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("LITELLM_USER_AGENT", "Mozilla/5.0 test")
    monkeypatch.setattr(
        "fakethirtyeight.caption._classify_one",
        lambda client, image_path, *, model: {
            "category": "infographic",
            "description": "New infographic.",
            "title": "New",
            "text": "New text",
        },
    )

    assert caption_images(
        workers=1,
        limit=1,
        force=True,
        refs_path=refs,
        image_log_path=image_log,
        out_path=captions,
    ) == (1, 0)

    rows = list(csv.DictReader(captions.open(newline="", encoding="utf-8")))
    assert [row["ai_title"] for row in rows] == ["Old", "New"]
