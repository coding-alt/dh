"""Tiny SQLite persistence layer (avatars + generation tasks)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from .config import settings

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS avatars (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    status        TEXT NOT NULL,
    voice_id      TEXT,
    ref_video_url TEXT,
    ref_video_key TEXT,
    preview_url   TEXT,
    duration      REAL DEFAULT 0,
    reuse_count   INTEGER DEFAULT 0,
    error         TEXT,
    meta          TEXT DEFAULT '{}',
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id                     TEXT PRIMARY KEY,
    kind                   TEXT NOT NULL,
    status                 TEXT NOT NULL,
    stage                  TEXT NOT NULL,
    progress               INTEGER DEFAULT 0,
    avatar_id              TEXT,
    title                  TEXT,
    text                   TEXT,
    error                  TEXT,
    audio_url              TEXT,
    audio_path             TEXT,
    audio_duration         REAL DEFAULT 0,
    remote_task_id         TEXT,
    remote_media_url       TEXT,
    remote_subtitles_url   TEXT,
    video_url              TEXT,
    srt_path               TEXT,
    meta                   TEXT DEFAULT '{}',
    logs                   TEXT DEFAULT '[]',
    created_at             REAL NOT NULL,
    updated_at             REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_avatar ON tasks(avatar_id);
"""


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(
            settings.database_path, check_same_thread=False, timeout=30
        )
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA)
        _conn.commit()
    return _conn


def init_db() -> None:
    with _lock:
        _db()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _loads(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _avatar_row(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["meta"] = _loads(data.get("meta"), {})
    return data


def _task_row(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["meta"] = _loads(data.get("meta"), {})
    data["logs"] = _loads(data.get("logs"), [])
    return data


# --------------------------------------------------------------------------
# avatars
# --------------------------------------------------------------------------
def create_avatar(name: str, meta: dict | None = None) -> dict:
    now = time.time()
    avatar_id = new_id("av")
    with _lock:
        conn = _db()
        conn.execute(
            """INSERT INTO avatars (id, name, status, meta, created_at, updated_at)
               VALUES (?, ?, 'processing', ?, ?, ?)""",
            (avatar_id, name, json.dumps(meta or {}), now, now),
        )
        conn.commit()
        return get_avatar(avatar_id)  # type: ignore[return-value]


def update_avatar(avatar_id: str, **fields: Any) -> dict | None:
    allowed = {
        "name",
        "status",
        "voice_id",
        "ref_video_url",
        "ref_video_key",
        "preview_url",
        "duration",
        "reuse_count",
        "error",
        "meta",
    }
    patch = {k: v for k, v in fields.items() if k in allowed}
    if not patch:
        return get_avatar(avatar_id)
    if "meta" in patch:
        patch["meta"] = json.dumps(patch["meta"], ensure_ascii=False)
    sets = ", ".join(f"{key} = ?" for key in patch)
    with _lock:
        conn = _db()
        conn.execute(
            f"UPDATE avatars SET {sets}, updated_at = ? WHERE id = ?",
            (*patch.values(), time.time(), avatar_id),
        )
        conn.commit()
    return get_avatar(avatar_id)


def bump_reuse(avatar_id: str) -> None:
    with _lock:
        conn = _db()
        conn.execute(
            "UPDATE avatars SET reuse_count = reuse_count + 1, updated_at = ? WHERE id = ?",
            (time.time(), avatar_id),
        )
        conn.commit()


def get_avatar(avatar_id: str) -> dict | None:
    with _lock:
        row = _db().execute("SELECT * FROM avatars WHERE id = ?", (avatar_id,)).fetchone()
    return _avatar_row(row) if row else None


def list_avatars() -> list[dict]:
    with _lock:
        rows = _db().execute("SELECT * FROM avatars ORDER BY created_at DESC").fetchall()
    return [_avatar_row(r) for r in rows]


def delete_avatar(avatar_id: str) -> None:
    with _lock:
        conn = _db()
        conn.execute("DELETE FROM avatars WHERE id = ?", (avatar_id,))
        conn.commit()


# --------------------------------------------------------------------------
# tasks
# --------------------------------------------------------------------------
def create_task(
    kind: str,
    stage: str,
    status: str = "processing",
    **fields: Any,
) -> dict:
    now = time.time()
    task_id = new_id("job" if kind == "setup" else "task")
    payload = {
        "id": task_id,
        "kind": kind,
        "status": status,
        "stage": stage,
        "progress": 0,
        "avatar_id": fields.get("avatar_id"),
        "title": fields.get("title"),
        "text": fields.get("text"),
        "meta": json.dumps(fields.get("meta") or {}, ensure_ascii=False),
        "logs": "[]",
        "created_at": now,
        "updated_at": now,
    }
    with _lock:
        conn = _db()
        conn.execute(
            f"""INSERT INTO tasks ({", ".join(payload)})
                VALUES ({", ".join("?" for _ in payload)})""",
            tuple(payload.values()),
        )
        conn.commit()
    return get_task(task_id)  # type: ignore[return-value]


def update_task(task_id: str, *, log: str | None = None, **fields: Any) -> dict | None:
    allowed = {
        "status",
        "stage",
        "progress",
        "avatar_id",
        "title",
        "text",
        "error",
        "audio_url",
        "audio_path",
        "audio_duration",
        "remote_task_id",
        "remote_media_url",
        "remote_subtitles_url",
        "video_url",
        "srt_path",
        "meta",
    }
    patch = {k: v for k, v in fields.items() if k in allowed}
    if "meta" in patch:
        patch["meta"] = json.dumps(patch["meta"], ensure_ascii=False)
    with _lock:
        conn = _db()
        if patch:
            sets = ", ".join(f"{key} = ?" for key in patch)
            conn.execute(
                f"UPDATE tasks SET {sets}, updated_at = ? WHERE id = ?",
                (*patch.values(), time.time(), task_id),
            )
        if log:
            row = conn.execute("SELECT logs FROM tasks WHERE id = ?", (task_id,)).fetchone()
            logs = _loads(row["logs"] if row else None, [])
            logs.append({"at": time.time(), "message": log})
            conn.execute(
                "UPDATE tasks SET logs = ?, updated_at = ? WHERE id = ?",
                (json.dumps(logs[-60:], ensure_ascii=False), time.time(), task_id),
            )
        conn.commit()
    return get_task(task_id)


def get_task(task_id: str) -> dict | None:
    with _lock:
        row = _db().execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _task_row(row) if row else None


def list_tasks(
    kind: str | None = None, limit: int = 30, offset: int = 0
) -> list[dict]:
    query = "SELECT * FROM tasks"
    params: Iterable[Any] = ()
    if kind:
        query += " WHERE kind = ?"
        params = (kind,)
    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    with _lock:
        rows = _db().execute(query, (*params, limit, max(0, offset))).fetchall()
    return [_task_row(r) for r in rows]


def count_tasks(kind: str | None = None) -> int:
    query = "SELECT COUNT(*) AS n FROM tasks"
    params: Iterable[Any] = ()
    if kind:
        query += " WHERE kind = ?"
        params = (kind,)
    with _lock:
        row = _db().execute(query, params).fetchone()
    return int(row["n"]) if row else 0


def count_tasks_by_status(kind: str | None = None) -> dict[str, int]:
    query = "SELECT status, COUNT(*) AS n FROM tasks"
    params: Iterable[Any] = ()
    if kind:
        query += " WHERE kind = ?"
        params = (kind,)
    query += " GROUP BY status"
    counts: dict[str, int] = {}
    with _lock:
        rows = _db().execute(query, params).fetchall()
    for row in rows:
        counts[str(row["status"])] = int(row["n"])
    return counts


def tasks_for_avatar(avatar_id: str, limit: int = 50) -> list[dict]:
    with _lock:
        rows = _db().execute(
            "SELECT * FROM tasks WHERE avatar_id = ? ORDER BY created_at DESC LIMIT ?",
            (avatar_id, limit),
        ).fetchall()
    return [_task_row(r) for r in rows]


def delete_task(task_id: str) -> None:
    with _lock:
        conn = _db()
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()


def recover_unfinished_tasks() -> list[dict]:
    """Tasks interrupted by a restart are marked failed so the UI never hangs."""
    with _lock:
        conn = _db()
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status IN ('processing', 'pending')"
        ).fetchall()
        conn.execute(
            """UPDATE tasks SET status = 'failed', stage = 'failed',
                      error = COALESCE(error, '服务重启，任务中断'),
                      updated_at = ?
               WHERE status IN ('processing', 'pending')""",
            (time.time(),),
        )
        conn.commit()
    return [_task_row(r) for r in rows]


def path_for_task(task: dict) -> Path | None:
    if task.get("video_url"):
        name = task["video_url"].rsplit("/", 1)[-1]
        return settings.output_dir / name
    return None
