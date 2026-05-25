import csv
import gzip
from pathlib import Path

from fakethirtyeight import embeds
from fakethirtyeight.embeds import _extract_one


def test_extract_one_finds_non_ai2html_pym_project_embed() -> None:
    html = """
    <html><body>
      <figure>
        <div id="senate-forecast"></div>
        <figcaption>Forecast widget caption.</figcaption>
      </figure>
      <script>
      var pymChild = new pym.Parent(
        'senate-forecast',
        '//projects.fivethirtyeight.com/senate-2014/promo.html?v=200',
        { title: 'Senate forecast' }
      )
      </script>
    </body></html>
    """

    rows = _extract_one(
        html, "data/articles/2014/example.html.gz", "https://example.com/article/"
    )

    assert len(rows) == 1
    assert rows[0]["kind"] == "pym"
    assert rows[0]["child_id"] == "senate-forecast"
    assert rows[0]["canonical_url"] == (
        "https://projects.fivethirtyeight.com/senate-2014/promo.html"
    )
    assert rows[0]["title"] == "Senate forecast"
    assert rows[0]["caption"] == "Forecast widget caption."
    assert rows[0]["identifier"].startswith("fivethirtyeight-embed-")


def test_extract_one_skips_ai2html_pym_and_iframe() -> None:
    html = """
    <html><body>
      <div id="ai2html_block_659273a59c45e"></div>
      <script>
      new pym.Parent(
        'ai2html_block_659273a59c45e',
        'https://fivethirtyeight.com/?ai2html=https%3A%2F%2Ffivethirtyeight.com%2Fwp-content%2Fuploads%2F2023%2F04%2Fchart.html',
        {}
      )
      </script>
      <iframe src="https://fivethirtyeight.com/?ai2html=https%3A%2F%2Fexample.com%2Fchart.html"></iframe>
    </body></html>
    """

    assert (
        _extract_one(
            html,
            "data/articles/2024/example.html.gz",
            "https://example.com/article/",
        )
        == []
    )


def test_extract_one_finds_project_iframe_and_skips_ad_iframe() -> None:
    html = """
    <html><body>
      <figure>
        <iframe
          id="forecast"
          src="https://projects.fivethirtyeight.com/2020-election-forecast/?cid=rrpromo"
          title="2020 forecast"></iframe>
        <figcaption>Election model.</figcaption>
      </figure>
      <iframe src="https://securepubads.g.doubleclick.net/pagead/ads"></iframe>
    </body></html>
    """

    rows = _extract_one(
        html, "data/articles/2020/example.html.gz", "https://example.com/article/"
    )

    assert len(rows) == 1
    assert rows[0]["kind"] == "iframe"
    assert rows[0]["canonical_url"] == (
        "https://projects.fivethirtyeight.com/2020-election-forecast/"
    )
    assert rows[0]["title"] == "2020 forecast"
    assert rows[0]["caption"] == "Election model."


def test_extract_one_skips_sidebar_project_iframe() -> None:
    html = """
    <html><body>
      <div id="secondary" class="single-col">
        <div class="sidebar-feature">
          <aside class="widget flexible interactives embed">
            <h2 class="widget-title">Interactives</h2>
            <iframe
              id="pym-fivethirtyeight_embed_490"
              src="https://projects.fivethirtyeight.com/polls/president-primary-r/2024/national/"
              title="Interactives"></iframe>
          </aside>
        </div>
      </div>
    </body></html>
    """

    assert (
        _extract_one(
            html,
            "data/articles/2023/example.html.gz",
            "https://example.com/article/",
        )
        == []
    )


def test_extract_one_skips_latest_interactives_iframe() -> None:
    html = """
    <html><body>
      <div class="interactive-section">
        <h2 class="interactive-section__title">Latest Interactives</h2>
        <iframe
          src="https://projects.fivethirtyeight.com/biden-approval-rating/promo.html"
          title="biden approval rating"></iframe>
      </div>
    </body></html>
    """

    assert (
        _extract_one(
            html,
            "data/articles/2024/example.html.gz",
            "https://example.com/article/",
        )
        == []
    )


def test_extract_references_excludes_existing_ai2html_canonical(
    tmp_path: Path, monkeypatch
) -> None:
    data_dir = tmp_path / "data"
    article = data_dir / "articles" / "2014" / "example.html.gz"
    article.parent.mkdir(parents=True)
    html = """
    <script>
    new pym.Parent(
      'project',
      'https://projects.fivethirtyeight.com/senate-2014/promo.html?v=200',
      {}
    )
    </script>
    """
    with gzip.open(article, "wt", encoding="utf-8") as fh:
        fh.write(html)

    ai2html_refs = tmp_path / "ai2html_refs.csv"
    with ai2html_refs.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=("canonical_url",))
        writer.writeheader()
        writer.writerow(
            {
                "canonical_url": (
                    "https://projects.fivethirtyeight.com/senate-2014/promo.html"
                )
            }
        )

    monkeypatch.setattr(embeds, "DATA_DIR", data_dir)
    out_path = tmp_path / "embed_refs.csv"

    n = embeds.extract_references(
        articles_dir=data_dir / "articles",
        out_path=out_path,
        ai2html_refs_path=ai2html_refs,
        enriched_path=tmp_path / "missing_enriched.csv",
    )

    assert n == 0
    assert list(csv.DictReader(out_path.open())) == []
