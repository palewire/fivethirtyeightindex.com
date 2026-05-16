"""Unit tests for curate (editorial subset + rollups)."""

from __future__ import annotations

import csv
from pathlib import Path

from fakethirtyeight import curate, merge


def _write_index(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=merge.INDEX_FIELDS)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in merge.INDEX_FIELDS})


def _row(**overrides: str) -> dict[str, str]:
    base = {
        "urlkey": "",
        "url": "",
        "canonical_key": "",
        "kind": "",
        "rollup_key": "",
        "host": "fivethirtyeight.com",
        "path": "/",
        "query": "",
        "first_seen_ts": "20200101000000",
        "last_seen_ts": "20200101000000",
        "latest_status": "200",
        "latest_mimetype": "text/html",
        "latest_digest": "X",
        "latest_length": "1",
        "snapshot_observations": "1",
        "source": "cdx",
    }
    base.update(overrides)
    return base


def test_curate_emits_one_row_per_rollup_key(tmp_path):
    index = tmp_path / "index.csv"
    out = tmp_path / "curated.csv"

    _write_index(
        index,
        [
            # Three sub-URLs of one liveblog → 1 row
            _row(
                url="https://fivethirtyeight.com/live-blog/election-2020/",
                kind="liveblog",
                rollup_key="liveblog:election-2020",
                path="/live-blog/election-2020/",
                first_seen_ts="20201101",
                last_seen_ts="20201103",
            ),
            _row(
                url="https://fivethirtyeight.com/live-blog/election-2020/update-1/",
                kind="liveblog",
                rollup_key="liveblog:election-2020",
                path="/live-blog/election-2020/update-1/",
                first_seen_ts="20201104",
                last_seen_ts="20201104",
            ),
            _row(
                url="https://fivethirtyeight.com/live-blog/election-2020/update-2/",
                kind="liveblog",
                rollup_key="liveblog:election-2020",
                path="/live-blog/election-2020/update-2/",
                first_seen_ts="20201105",
                last_seen_ts="20210101",
            ),
            # One feature article → 1 row
            _row(
                url="https://fivethirtyeight.com/features/some-slug/",
                kind="article",
                rollup_key="article:features/some-slug",
                path="/features/some-slug/",
            ),
            # Tag page should be dropped (not in EDITORIAL_KINDS)
            _row(
                url="https://fivethirtyeight.com/tag/elections/",
                kind="tag",
                rollup_key="tag:/tag/elections/",
                path="/tag/elections/",
            ),
            # Project URLs → all collapse to one row
            _row(
                url="https://projects.fivethirtyeight.com/polls/",
                kind="project",
                rollup_key="project:polls",
                host="projects.fivethirtyeight.com",
                path="/polls/",
            ),
            _row(
                url="https://projects.fivethirtyeight.com/polls/president-trump/",
                kind="project",
                rollup_key="project:polls",
                host="projects.fivethirtyeight.com",
                path="/polls/president-trump/",
            ),
            # Non-200 article should be skipped (not qualifying)
            _row(
                url="https://fivethirtyeight.com/features/old-story/",
                kind="article",
                rollup_key="article:features/old-story",
                latest_status="404",
            ),
        ],
    )

    summary = curate.curate(index_path=index, out_path=out)

    # 3 expected rollup groups: liveblog, features-some-slug, project-polls
    assert summary.out_rows == 3

    with out.open() as fh:
        rows = list(csv.DictReader(fh))
    by_rollup = {r["rollup_key"]: r for r in rows}

    # Liveblog row
    lb = by_rollup["liveblog:election-2020"]
    assert lb["kind"] == "liveblog"
    assert int(lb["member_url_count"]) == 3
    assert lb["first_seen_ts"] == "20201101"
    assert lb["last_seen_ts"] == "20210101"
    # Best representative is the shortest path
    assert lb["path"] == "/live-blog/election-2020/"

    # Project row
    proj = by_rollup["project:polls"]
    assert proj["kind"] == "project"
    assert int(proj["member_url_count"]) == 2
    assert proj["path"] == "/polls/"

    # Article row
    art = by_rollup["article:features/some-slug"]
    assert int(art["member_url_count"]) == 1


def test_curate_drops_non_html_and_non_200(tmp_path):
    index = tmp_path / "index.csv"
    out = tmp_path / "curated.csv"
    _write_index(
        index,
        [
            _row(
                url="https://fivethirtyeight.com/features/a/",
                kind="article",
                rollup_key="article:features/a",
                latest_status="200",
                latest_mimetype="text/html",
            ),
            _row(
                url="https://fivethirtyeight.com/features/b/",
                kind="article",
                rollup_key="article:features/b",
                latest_status="301",
                latest_mimetype="text/html",
            ),
            _row(
                url="https://fivethirtyeight.com/features/c.png",
                kind="article",  # rare, but exercise mimetype filter
                rollup_key="article:features/c.png",
                latest_status="200",
                latest_mimetype="image/png",
            ),
        ],
    )

    summary = curate.curate(index_path=index, out_path=out)
    assert summary.out_rows == 1


def test_curate_raises_when_index_missing(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        curate.curate(index_path=tmp_path / "missing.csv")
