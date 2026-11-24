import json
import os
import sqlite3
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("VIDEO_AGENT_DATA", str(ROOT / "data")))
LOCK = threading.RLock()


def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATA / "app.sqlite", timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def initialize():
    with LOCK, connect() as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS videos (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL,
                duration REAL NOT NULL, source TEXT NOT NULL,
                segments TEXT NOT NULL DEFAULT '[]',
                summary TEXT, created REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, video_id TEXT NOT NULL,
                mode TEXT NOT NULL, status TEXT NOT NULL,
                events TEXT NOT NULL DEFAULT '[]', result TEXT,
                error TEXT, created REAL NOT NULL
            );
        """)
        connection.execute(
            "UPDATE tasks SET status='failed', error=? WHERE status IN ('queued', 'running')",
            ("服务已重启，请重新提交；已有转写会被复用。",),
        )


def decode(row):
    if row is None:
        return None
    record = dict(row)
    for key in ("segments", "summary", "events", "result"):
        if key in record:
            record[key] = json.loads(record[key]) if record[key] else None
    return record


def video(video_id):
    with connect() as connection:
        return decode(connection.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone())


def videos():
    with connect() as connection:
        return [decode(row) for row in connection.execute("SELECT * FROM videos ORDER BY created DESC")]


def add_video(video_id, name, path, duration, source, segments):
    with LOCK, connect() as connection:
        connection.execute(
            "INSERT INTO videos (id,name,path,duration,source,segments,created) VALUES (?,?,?,?,?,?,?)",
            (video_id, name, str(path), duration, source, json.dumps(segments, ensure_ascii=False), time.time()),
        )


def set_segments(video_id, segments, source):
    with LOCK, connect() as connection:
        connection.execute(
            "UPDATE videos SET segments=?, source=?, summary=NULL WHERE id=?",
            (json.dumps(segments, ensure_ascii=False), source, video_id),
        )


def save_summary(video_id, result):
    with LOCK, connect() as connection:
        connection.execute("UPDATE videos SET summary=? WHERE id=?", (json.dumps(result, ensure_ascii=False), video_id))


def create_task(task_id, video_id, mode):
    with LOCK, connect() as connection:
        active = connection.execute(
            "SELECT id FROM tasks WHERE video_id=? AND status IN ('queued','running')", (video_id,)
        ).fetchone()
        if active:
            return active["id"]
        connection.execute(
            "INSERT INTO tasks (id,video_id,mode,status,created) VALUES (?,?,?,'queued',?)",
            (task_id, video_id, mode, time.time()),
        )
    return task_id


def task(task_id):
    with connect() as connection:
        return decode(connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())


def active_task(video_id):
    with connect() as connection:
        return decode(connection.execute(
            "SELECT * FROM tasks WHERE video_id=? AND status IN ('queued','running') LIMIT 1", (video_id,)
        ).fetchone())


def latest_task(video_id):
    with connect() as connection:
        return decode(connection.execute("SELECT * FROM tasks WHERE video_id=? AND mode IN ('llm','transcribe') ORDER BY created DESC LIMIT 1", (video_id,)).fetchone())


def latest_search(video_id):
    with connect() as connection:
        return decode(connection.execute("SELECT * FROM tasks WHERE video_id=? AND mode='search' ORDER BY created DESC LIMIT 1", (video_id,)).fetchone())


def update_task(task_id, status, result=None, error=None):
    with LOCK, connect() as connection:
        connection.execute(
            "UPDATE tasks SET status=?,result=?,error=? WHERE id=?",
            (status, json.dumps(result, ensure_ascii=False) if result is not None else None, error, task_id),
        )


def event(task_id, kind, title, detail="", data=None):
    with LOCK, connect() as connection:
        row = connection.execute("SELECT events FROM tasks WHERE id=?", (task_id,)).fetchone()
        events = json.loads(row["events"])
        events.append({"index": len(events) + 1, "kind": kind, "title": title, "detail": detail,
                       "data": data, "time": time.time()})
        connection.execute("UPDATE tasks SET events=? WHERE id=?", (json.dumps(events, ensure_ascii=False), task_id))
