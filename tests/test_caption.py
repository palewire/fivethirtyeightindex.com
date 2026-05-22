import csv
from pathlib import Path

import httpx

from fakethirtyeight.caption import (
    CAPTION_FIELDS,
    _classify_one,
    _content_from_response,
    _ensure_caption_header,
    _litellm_headers,
    _litellm_url,
    _select_targets,
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
    assert b'"type":"image_url"' in payload
    assert b"data:image/png;base64," in payload


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
