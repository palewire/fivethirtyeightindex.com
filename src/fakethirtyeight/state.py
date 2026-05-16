"""Thread-safe JSON resume state."""

from __future__ import annotations

import fcntl
import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fakethirtyeight.paths import STATE_FILE


@dataclass
class ShardState:
    shard_id: str
    host: str
    year: int | None
    status: str = "pending"  # pending | running | complete | failed
    last_resume_key: str | None = None
    rows_written: int = 0
    pages_fetched: int = 0
    started_at: str | None = None
    updated_at: str | None = None
    error: str | None = None


@dataclass
class State:
    shards: dict[str, ShardState] = field(default_factory=dict)


_PROCESS_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@contextmanager
def _file_lock(path: Path) -> Iterator[int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _serialize(state: State) -> str:
    return json.dumps(
        {"shards": {sid: asdict(s) for sid, s in state.shards.items()}},
        indent=2,
        sort_keys=True,
    )


def _deserialize(text: str) -> State:
    if not text.strip():
        return State()
    raw: dict[str, Any] = json.loads(text)
    shards_raw = raw.get("shards") or {}
    shards = {sid: ShardState(**vals) for sid, vals in shards_raw.items()}
    return State(shards=shards)


def load(path: Path = STATE_FILE) -> State:
    with _PROCESS_LOCK, _file_lock(path) as fd:
        os.lseek(fd, 0, os.SEEK_SET)
        data = os.read(fd, _filesize(fd)).decode("utf-8")
        return _deserialize(data)


def save(state: State, path: Path = STATE_FILE) -> None:
    payload = _serialize(state).encode("utf-8")
    with _PROCESS_LOCK, _file_lock(path) as fd:
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, payload)


def update_shard(shard: ShardState, path: Path = STATE_FILE) -> None:
    """Atomically merge a shard's progress into the state file."""
    shard.updated_at = _now()
    with _PROCESS_LOCK, _file_lock(path) as fd:
        os.lseek(fd, 0, os.SEEK_SET)
        data = os.read(fd, _filesize(fd)).decode("utf-8")
        state = _deserialize(data)
        state.shards[shard.shard_id] = shard
        payload = _serialize(state).encode("utf-8")
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, payload)


def mark_started(shard: ShardState, path: Path = STATE_FILE) -> None:
    shard.status = "running"
    shard.started_at = shard.started_at or _now()
    update_shard(shard, path)


def mark_complete(shard: ShardState, path: Path = STATE_FILE) -> None:
    shard.status = "complete"
    update_shard(shard, path)


def mark_failed(shard: ShardState, error: str, path: Path = STATE_FILE) -> None:
    shard.status = "failed"
    shard.error = error
    update_shard(shard, path)


def _filesize(fd: int) -> int:
    return os.fstat(fd).st_size
