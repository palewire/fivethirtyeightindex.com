"""Unit tests for the resume state store."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from fakethirtyeight import state


def test_load_returns_empty_when_no_file(tmp_path):
    s = state.load(tmp_path / "state.json")
    assert s.shards == {}


def test_round_trip_through_update_shard(tmp_path):
    path = tmp_path / "state.json"

    shard = state.ShardState(shard_id="cdx-2014-x", host="x", year=2014)
    state.mark_started(shard, path)

    shard.rows_written = 42
    shard.last_resume_key = "resume-1"
    state.update_shard(shard, path)

    reloaded = state.load(path)
    assert "cdx-2014-x" in reloaded.shards
    s = reloaded.shards["cdx-2014-x"]
    assert s.rows_written == 42
    assert s.last_resume_key == "resume-1"
    assert s.status == "running"
    assert s.started_at is not None


def test_concurrent_updates_do_not_lose_writes(tmp_path):
    path = tmp_path / "state.json"

    def write_one(i: int) -> None:
        shard = state.ShardState(
            shard_id=f"cdx-{i}-x",
            host="x",
            year=i,
            rows_written=i,
        )
        state.update_shard(shard, path)

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(write_one, range(32)))

    final = state.load(path)
    assert len(final.shards) == 32
    for i in range(32):
        assert final.shards[f"cdx-{i}-x"].rows_written == i


def test_mark_complete_sets_status(tmp_path):
    path = tmp_path / "state.json"
    shard = state.ShardState(shard_id="s", host="x", year=2020)
    state.mark_started(shard, path)
    state.mark_complete(shard, path)
    reloaded = state.load(path)
    assert reloaded.shards["s"].status == "complete"


def test_mark_failed_records_error(tmp_path):
    path = tmp_path / "state.json"
    shard = state.ShardState(shard_id="s", host="x", year=2020)
    state.mark_failed(shard, "boom", path)
    reloaded = state.load(path)
    assert reloaded.shards["s"].status == "failed"
    assert reloaded.shards["s"].error == "boom"
