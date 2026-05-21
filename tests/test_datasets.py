from __future__ import annotations

import json
import subprocess
import zipfile
from io import BytesIO
from pathlib import Path

from fakethirtyeight.datasets import (
    ARCHIVE_ITEM_BASE_URL,
    DATASET_UPLOAD_LOG,
    _download_bundle,
    _log_row,
    apply_github_commit_dates,
    apply_related_story_dates,
    apply_upload_log,
    first_git_commit_date,
    first_github_commit_date,
    metadata_for_record,
    parse_index_csv,
    scrape_index,
    upload_one_bundle,
    write_site_datasets,
)

INDEX_CSV = """dataset_url,article_url
https://github.com/fivethirtyeight/data/tree/master/nba-raptor,https://projects.fivethirtyeight.com/nba-player-ratings/
https://github.com/fivethirtyeight/data/tree/master/nba-raptor,https://fivethirtyeight.com/features/how-our-raptor-metric-works/
https://github.com/fivethirtyeight/superbowl-ads,https://projects.fivethirtyeight.com/super-bowl-ads/
"""


def test_parse_index_csv_groups_unique_dataset_urls() -> None:
    records = parse_index_csv(INDEX_CSV)

    assert [r.slug for r in records] == ["nba-raptor", "superbowl-ads"]
    assert records[0].id == "dataset:nba-raptor"
    assert records[0].title == "NBA Raptor"
    assert records[0].identifier == "fivethirtyeight-dataset-nba-raptor"
    assert records[0].article_count == 2
    assert records[0].archive_url == ""
    assert records[0].date == ""
    assert records[1].dataset_url == "https://github.com/fivethirtyeight/superbowl-ads"


def test_scrape_index_writes_source_and_site_artifacts(tmp_path: Path) -> None:
    source_csv = tmp_path / "datasets.csv"
    site_json = tmp_path / "datasets.json"
    site_csv = tmp_path / "site-datasets.csv"
    meta_json = tmp_path / "datasets-meta.json"

    count = scrape_index(
        out_path=source_csv,
        site_json_path=site_json,
        site_csv_path=site_csv,
        site_meta_path=meta_json,
        enriched_path=tmp_path / "missing-enriched.csv",
        fetch_text=lambda _: INDEX_CSV,
    )

    assert count == 2
    assert "archive_url" in source_csv.read_text(encoding="utf-8").splitlines()[0]
    rows = json.loads(site_json.read_text(encoding="utf-8"))
    assert rows[0]["id"] == "dataset:nba-raptor"
    assert rows[0]["article_urls"] == [
        "https://projects.fivethirtyeight.com/nba-player-ratings/",
        "https://fivethirtyeight.com/features/how-our-raptor-metric-works/",
    ]
    assert rows[0]["date"] == ""
    assert json.loads(meta_json.read_text(encoding="utf-8")) == {"total": 2}


def test_write_site_datasets_accepts_missing_source(tmp_path: Path) -> None:
    count = write_site_datasets(
        source_path=tmp_path / "missing.csv",
        json_path=tmp_path / "datasets.json",
        csv_path=tmp_path / "datasets.csv",
        meta_path=tmp_path / "datasets-meta.json",
        enriched_path=tmp_path / "missing-enriched.csv",
    )

    assert count == 0
    assert json.loads((tmp_path / "datasets.json").read_text(encoding="utf-8")) == []


def test_write_site_datasets_applies_uploaded_archive_urls(tmp_path: Path) -> None:
    upload_log = tmp_path / DATASET_UPLOAD_LOG.name
    upload_log.write_text(
        "identifier,uploaded_at,status,files,error\n"
        "fivethirtyeight-dataset-nba-raptor,2026-05-21T00:00:00+00:00,uploaded,bundle.zip,\n"
        "fivethirtyeight-dataset-superbowl-ads,2026-05-21T00:00:00+00:00,dry_run,bundle.zip,\n",
        encoding="utf-8",
    )
    records = parse_index_csv(INDEX_CSV)

    apply_upload_log(records, log_path=upload_log)

    assert records[0].archive_url == (
        f"{ARCHIVE_ITEM_BASE_URL}/fivethirtyeight-dataset-nba-raptor"
    )
    assert records[1].archive_url == ""


def test_apply_related_story_dates_uses_earliest_matched_article_date(
    tmp_path: Path,
) -> None:
    enriched = tmp_path / "enriched.csv"
    enriched.write_text(
        "rollup_key,published_at\n"
        "article:features/how-our-raptor-metric-works,2019-10-10T12:00:00+00:00\n"
        "project:nba-player-ratings,2020-01-01T12:00:00+00:00\n",
        encoding="utf-8",
    )
    records = parse_index_csv(INDEX_CSV)

    apply_related_story_dates(records, enriched_path=enriched)

    assert records[0].date == "2019-10-10T12:00:00+00:00"


def test_metadata_for_record_prefers_preservation_context() -> None:
    record = parse_index_csv(INDEX_CSV)[0]

    md = metadata_for_record(record, collection="test-collection")

    assert md["collection"] == "test-collection"
    assert md["mediatype"] == "data"
    assert md["creator"] == "FiveThirtyEight"
    assert md["contributor"] == "Ben Welsh"
    assert md["publisher"] == "FiveThirtyEight"
    assert md["title"] == "FiveThirtyEight Dataset: NBA Raptor"
    assert record.dataset_url in md["description"]
    assert "urn:fakethirtyeight:dataset:nba-raptor" in md["external-identifier"]


def test_upload_one_bundle_dry_run_never_requires_session(tmp_path: Path) -> None:
    record = parse_index_csv(INDEX_CSV)[0]
    bundle = tmp_path / record.slug
    bundle.mkdir()
    (bundle / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (bundle / "2024").mkdir()
    (bundle / "2024" / "data.csv").write_text("a,b\n3,4\n", encoding="utf-8")

    result = upload_one_bundle(
        None,
        record,
        collection="test-collection",
        contributor="Ben Welsh",
        bundles_dir=tmp_path,
        dry_run=True,
    )

    assert result == {
        "status": "dry_run",
        "files": ["2024/data.csv", "data.csv"],
        "error": "",
    }


class _FakeArchiveItem:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}

    def upload(
        self,
        files: dict[str, str],
        metadata: dict[str, str | list[str]],
        retries: int,
        retries_sleep: int,
        verbose: bool,
        request_kwargs: dict[str, str] | None = None,
    ) -> list[_FakeResponse]:
        self.files = files
        return [_FakeResponse()]


class _FakeArchiveSession:
    def __init__(self) -> None:
        self.item = _FakeArchiveItem()

    def get_item(self, identifier: str) -> _FakeArchiveItem:
        return self.item


def test_upload_one_bundle_preserves_relative_remote_paths(tmp_path: Path) -> None:
    record = parse_index_csv(INDEX_CSV)[0]
    bundle = tmp_path / record.slug
    bundle.mkdir()
    (bundle / "README.md").write_text("# root\n", encoding="utf-8")
    (bundle / "2024").mkdir()
    (bundle / "2024" / "README.md").write_text("# nested\n", encoding="utf-8")
    session = _FakeArchiveSession()

    result = upload_one_bundle(
        session,  # type: ignore[arg-type]
        record,
        collection="test-collection",
        contributor="Ben Welsh",
        bundles_dir=tmp_path,
        dry_run=False,
    )

    assert result["status"] == "uploaded"
    assert sorted(session.item.files) == ["2024/README.md", "README.md"]


class _FakeResponse:
    def __init__(
        self,
        *,
        payload: object | None = None,
        content: bytes = b"",
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._payload = payload
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class _FakeClient:
    def get(self, url: str) -> _FakeResponse:
        if (
            url
            == "https://codeload.github.com/fivethirtyeight/data/zip/refs/heads/master"
        ):
            return _FakeResponse(content=_fake_zip())
        raise AssertionError(url)


def _fake_zip() -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("data-master/nba-raptor/README.md", "# NBA Raptor\n")
        zf.writestr("data-master/nba-raptor/data.csv", "a,b\n1,2\n")
        zf.writestr("data-master/other/data.csv", "x,y\n3,4\n")
    return buf.getvalue()


def test_download_bundle_mirrors_tree_path_and_writes_manifest(tmp_path: Path) -> None:
    record = parse_index_csv(INDEX_CSV)[0]

    files = _download_bundle(_FakeClient(), record, bundles_dir=tmp_path)  # type: ignore[arg-type]

    rel_files = sorted(str(p.relative_to(tmp_path / record.slug)) for p in files)
    assert rel_files == ["README.md", "data.csv", "manifest.json"]
    manifest = json.loads(
        (tmp_path / record.slug / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["dataset"]["identifier"] == "fivethirtyeight-dataset-nba-raptor"
    assert [f["path"] for f in manifest["files"]] == ["README.md", "data.csv"]


def test_log_row_uses_requested_timestamp_field() -> None:
    row = _log_row(
        "fivethirtyeight-dataset-nba-raptor",
        status="uploaded",
        files=["data.csv"],
        error="",
        timestamp_field="uploaded_at",
    )

    assert row["identifier"] == "fivethirtyeight-dataset-nba-raptor"
    assert row["status"] == "uploaded"
    assert row["files"] == "data.csv"
    assert "uploaded_at" in row


class _CommitClient:
    def get(
        self,
        url: str,
        params: dict[str, str] | None = None,
    ) -> _FakeResponse:
        if params:
            assert params["path"] == "nba-raptor"
            return _FakeResponse(
                payload=[{"commit": {"committer": {"date": "2020-01-02T00:00:00Z"}}}],
                headers={
                    "link": '<https://api.github.com/repos/fivethirtyeight/data/commits?page=2>; rel="last"'
                },
            )
        assert url.endswith("page=2")
        return _FakeResponse(
            payload=[
                {"commit": {"committer": {"date": "2018-03-04T00:00:00Z"}}},
                {"commit": {"committer": {"date": "2017-02-03T00:00:00Z"}}},
            ],
        )


def test_first_github_commit_date_reads_oldest_commit_page() -> None:
    record = parse_index_csv(INDEX_CSV)[0]

    assert first_github_commit_date(record, client=_CommitClient()) == "2017-02-03"  # type: ignore[arg-type]


def test_first_git_commit_date_uses_cached_repo_path(tmp_path: Path) -> None:
    record = parse_index_csv(INDEX_CSV)[0]
    repo = tmp_path / "fivethirtyeight-data"
    repo.mkdir()
    calls: list[list[str]] = []

    def runner(args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="2015-05-22\n", stderr="")

    assert (
        first_git_commit_date(record, repo_cache_dir=tmp_path, runner=runner)
        == "2015-05-22"
    )
    assert calls == [
        [
            "git",
            "-C",
            str(repo),
            "log",
            "--reverse",
            "--format=%cs",
            "--",
            "nba-raptor",
        ]
    ]


def test_apply_github_commit_dates_fills_missing_dates(tmp_path: Path) -> None:
    records = parse_index_csv(INDEX_CSV)
    (tmp_path / "fivethirtyeight-data").mkdir()
    (tmp_path / "fivethirtyeight-superbowl-ads").mkdir()

    def runner(args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[-1] == "nba-raptor":
            return subprocess.CompletedProcess(
                args, 0, stdout="2015-05-22\n", stderr=""
            )
        return subprocess.CompletedProcess(args, 0, stdout="2021-02-03\n", stderr="")

    apply_github_commit_dates(records, repo_cache_dir=tmp_path, runner=runner)

    assert [r.date for r in records] == ["2015-05-22", "2021-02-03"]
