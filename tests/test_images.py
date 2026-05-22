import csv
from pathlib import Path

import httpx

from fakethirtyeight import images
from fakethirtyeight.images import _extract_one, _image_kind, _load_done, _try_fetch


def test_extract_one_keeps_featured_images_for_classification() -> None:
    html = """
    <html><body>
      <figure>
        <img
          src="https://fivethirtyeight.com/wp-content/uploads/2015/01/model-lede.png"
          alt="A model comparison chart"
        />
        <figcaption>Model comparison chart.</figcaption>
      </figure>
    </body></html>
    """

    rows = _extract_one(
        html, "2015/example.html.gz", "https://fivethirtyeight.com/features/example/"
    )

    assert len(rows) == 1
    assert rows[0]["category"] == "featured-image"


def test_extract_one_still_drops_banners_and_headshots() -> None:
    html = """
    <html><body>
      <img src="https://fivethirtyeight.com/wp-content/uploads/2015/01/site-banner.png" />
      <img src="https://fivethirtyeight.com/wp-content/uploads/2015/01/player-headshot.jpg" />
    </body></html>
    """

    rows = _extract_one(
        html, "2015/example.html.gz", "https://fivethirtyeight.com/features/example/"
    )

    assert rows == []


def test_image_kind_sniffs_real_image_bytes() -> None:
    assert _image_kind(b"\x89PNG\r\n\x1a\n...") == "png"
    assert _image_kind(b"\xff\xd8\xff...") == "jpeg"
    assert _image_kind(b"GIF89a...") == "gif"
    assert _image_kind(b"RIFF....WEBP...") == "webp"
    assert _image_kind(b"\xef\xbb\xbf<svg viewBox='0 0 1 1'></svg>") == "svg"
    assert _image_kind(b"<!doctype html><html></html>") == ""


def test_try_fetch_retries_wayback_when_live_returns_html(monkeypatch) -> None:
    calls: list[str] = []

    def fake_stream(client: httpx.Client, url: str) -> tuple[bytes, str]:
        calls.append(url)
        if url == "https://example.com/chart.png":
            return b"<!doctype html><html>parked</html>", "text/html"
        return b"\x89PNG\r\n\x1a\nimage", "image/png"

    monkeypatch.setattr(images, "_stream", fake_stream)
    monkeypatch.setattr(
        images,
        "_wayback_url_for",
        lambda client, url: (
            "https://web.archive.org/web/20200101id_/https://example.com/chart.png"
        ),
    )

    body, content_type, source = _try_fetch(
        httpx.Client(), "https://example.com/chart.png"
    )

    assert body == b"\x89PNG\r\n\x1a\nimage"
    assert content_type == "image/png"
    assert source == "wayback"
    assert calls == [
        "https://example.com/chart.png",
        "https://web.archive.org/web/20200101id_/https://example.com/chart.png",
    ]


def test_try_fetch_rejects_wayback_html(monkeypatch) -> None:
    def fake_stream(client: httpx.Client, url: str) -> tuple[bytes, str]:
        return b"<!doctype html><html>not an image</html>", "text/html"

    monkeypatch.setattr(images, "_stream", fake_stream)
    monkeypatch.setattr(
        images,
        "_wayback_url_for",
        lambda client, url: (
            "https://web.archive.org/web/20200101id_/https://example.com/chart.png"
        ),
    )

    body, content_type, error = _try_fetch(
        httpx.Client(), "https://example.com/chart.png"
    )

    assert body is None
    assert content_type is None
    assert "non-image response" in error


def test_load_done_ignores_logged_html_files(tmp_path: Path) -> None:
    image_path = tmp_path / "ok.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    html_path = tmp_path / "bad.png"
    html_path.write_text("<!doctype html><html>not an image</html>", encoding="utf-8")
    log_path = tmp_path / "image_download_log.csv"

    with log_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=images.LOG_FIELDS)
        writer.writeheader()
        writer.writerow(
            {
                "identifier": "ok",
                "canonical_url": "https://example.com/ok.png",
                "file_path": image_path.name,
                "bytes": str(image_path.stat().st_size),
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
                "file_path": html_path.name,
                "bytes": str(html_path.stat().st_size),
                "content_type": "text/html",
                "fetched_via": "wayback",
                "status": "ok",
                "error": "",
            }
        )

    assert _load_done(log_path, root=tmp_path) == {"https://example.com/ok.png"}
