from __future__ import annotations

import csv
import json
import tarfile
from pathlib import Path

from fakethirtyeight.backup import create_backup_bundle


def test_create_backup_bundle_writes_manifests_and_archive(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    out_dir = tmp_path / "nas"
    (root / "data/articles/2020").mkdir(parents=True)
    (root / "data/images/ab").mkdir(parents=True)
    (root / "data/shards").mkdir(parents=True)
    (root / "web/static/data").mkdir(parents=True)
    (root / "data/articles/2020/example.html.gz").write_bytes(b"article")
    (root / "data/images/ab/image.png").write_bytes(b"image")
    (root / "data/image_upload_log.csv").write_text(
        "id,status\n1,ok\n", encoding="utf-8"
    )
    (root / "web/static/data/graphics.json").write_text("[]\n", encoding="utf-8")
    (root / "data/index.csv").write_text("large regeneratable file\n", encoding="utf-8")
    (root / "data/shards/2008.csv").write_text(
        "regeneratable shard\n", encoding="utf-8"
    )

    result = create_backup_bundle(out_dir, root=root, name="test-backup")

    assert result.file_count == 4
    assert result.archive_path == out_dir / "test-backup.tar.gz"
    manifest = json.loads(result.manifest_json_path.read_text(encoding="utf-8"))
    manifest_paths = {row["path"] for row in manifest["files"]}
    assert manifest_paths == {
        "data/articles/2020/example.html.gz",
        "data/images/ab/image.png",
        "data/image_upload_log.csv",
        "web/static/data/graphics.json",
    }
    with result.manifest_csv_path.open(newline="", encoding="utf-8") as fh:
        assert {row["path"] for row in csv.DictReader(fh)} == manifest_paths
    assert all(row["sha256"] for row in manifest["files"])

    assert result.archive_path is not None
    with tarfile.open(result.archive_path, "r:gz") as tf:
        names = set(tf.getnames())
    assert "test-backup/test-backup.manifest.json" in names
    assert "test-backup/data/images/ab/image.png" in names
    assert "test-backup/data/index.csv" not in names
    assert "test-backup/data/shards/2008.csv" not in names


def test_create_backup_bundle_dry_run_skips_archive(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    (root / "data/enriched.csv").write_text("id,title\n", encoding="utf-8")

    result = create_backup_bundle(tmp_path / "nas", root=root, name="dry", dry_run=True)

    assert result.archive_path is None
    assert result.manifest_json_path.exists()
    assert result.manifest_csv_path.exists()
    assert not (tmp_path / "nas/dry.tar.gz").exists()
