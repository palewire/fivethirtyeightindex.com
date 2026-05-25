from __future__ import annotations

from pathlib import Path

from fakethirtyeight.ia_html_upload import HtmlGraphic, _metadata_for, upload_one


class _Response:
    def raise_for_status(self) -> None:
        return None


class _Item:
    def __init__(self) -> None:
        self.files: list[str] = []
        self.metadata: dict[str, str | list[str]] = {}

    def upload(
        self,
        files: list[str],
        metadata: dict[str, str | list[str]],
        retries: int,
        retries_sleep: int,
        verbose: bool,
    ) -> list[_Response]:
        self.files = files
        self.metadata = metadata
        return [_Response()]


class _Session:
    def __init__(self) -> None:
        self.item = _Item()

    def get_item(self, identifier: str) -> _Item:
        return self.item


def _graphic(tmp_path: Path) -> HtmlGraphic:
    png = tmp_path / "render.png"
    html = tmp_path / "source.html"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    html.write_text("<html></html>", encoding="utf-8")
    return HtmlGraphic(
        identifier="fivethirtyeight-ai2html-test",
        canonical_url="https://fivethirtyeight.com/wp-content/uploads/chart.html",
        article_file="data/articles/2023/example.html.gz",
        article_url="https://fivethirtyeight.com/features/example/",
        title="Example chart",
        caption="Original caption.",
        kind="inline",
        bundle_kind="ai2html",
        html_path=html,
        png_path=png,
        published_at="2023-04-10T12:00:00+00:00",
        article_title="Example Article",
        byline="By Jane Doe",
    )


def test_html_graphic_metadata_discloses_rendered_screenshot_and_year(
    tmp_path: Path,
) -> None:
    metadata = _metadata_for(_graphic(tmp_path), collection="test-collection")

    assert metadata["title"] == "Example Chart — Example Article"
    assert metadata["mediatype"] == "image"
    assert metadata["publisher"] == "FiveThirtyEight"
    assert metadata["year"] == "2023"
    assert metadata["subject"] == [
        "graphic",
        "ai2html",
        "html-bundle",
        "FiveThirtyEight",
    ]
    assert "desktop PNG screenshot rendered from that HTML" in str(
        metadata["description"]
    )
    assert "Claude Sonnet 4.6 by Anthropic" in str(metadata["description"])
    assert metadata["external-identifier"] == [
        "urn:fakethirtyeight:ai2html:fivethirtyeight-ai2html-test",
        (
            "urn:fakethirtyeight:html-source-url:"
            "https://fivethirtyeight.com/wp-content/uploads/chart.html"
        ),
        (
            "urn:fakethirtyeight:source-article-url:"
            "https://fivethirtyeight.com/features/example/"
        ),
    ]


def test_upload_one_sends_png_before_html(tmp_path: Path) -> None:
    session = _Session()
    graphic = _graphic(tmp_path)

    result = upload_one(
        session,
        graphic=graphic,
        collection="test-collection",
        contributor="Ben Welsh",
        dry_run=False,
    )

    assert result.status == "uploaded"
    assert session.item.files == [str(graphic.png_path), str(graphic.html_path)]
    assert session.item.metadata["subject"] == [
        "graphic",
        "ai2html",
        "html-bundle",
        "FiveThirtyEight",
    ]
