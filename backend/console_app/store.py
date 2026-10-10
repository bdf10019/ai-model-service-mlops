from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, state: Path):
        state.mkdir(parents=True, exist_ok=True)
        self.path = state / "console.db"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password TEXT NOT NULL,
                    role TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    digest TEXT PRIMARY KEY, user_id TEXT NOT NULL, csrf TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL,
                    status TEXT NOT NULL, document TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL,
                    action TEXT NOT NULL, target TEXT NOT NULL, time REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS records (
                    namespace TEXT NOT NULL, id TEXT NOT NULL, document TEXT NOT NULL,
                    created REAL NOT NULL, PRIMARY KEY(namespace,id));
            """)
            # Interrupted work is never reported as successful after restart.
            records = db.execute(
                "SELECT id,document FROM tasks WHERE status IN ('queued','running','validating','cancelling')"
            ).fetchall()
            for record in records:
                doc = json.loads(record["document"])
                doc.update(status="failed", error="SERVICE_RESTARTED", finished_at=time.time())
                doc["events"].append(
                    {"stage": "failed", "time": time.time(), "reason": "SERVICE_RESTARTED"}
                )
                db.execute(
                    "UPDATE tasks SET status='failed', document=? WHERE id=?",
                    (json.dumps(doc), record["id"]),
                )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def audit(self, actor: str, action: str, target: str):
        with self.connect() as db:
            db.execute(
                "INSERT INTO audit(actor,action,target,time) VALUES(?,?,?,?)",
                (actor, action, target, time.time()),
            )

    def save_task(self, doc: dict):
        with self.connect() as db:
            db.execute(
                "INSERT INTO tasks VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,document=excluded.document",
                (
                    doc["id"],
                    doc["owner"],
                    doc["kind"],
                    doc["status"],
                    json.dumps(doc, ensure_ascii=False),
                    doc["created_at"],
                ),
            )

    def task(self, task_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT document FROM tasks WHERE id=?", (task_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def tasks(self, owner: str | None = None) -> list[dict]:
        with self.connect() as db:
            if owner:
                rows = db.execute(
                    "SELECT document FROM tasks WHERE owner=? ORDER BY created DESC LIMIT 100",
                    (owner,),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT document FROM tasks ORDER BY created DESC LIMIT 100"
                ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def metadata(self, key: str, value: dict | None = None) -> dict | None:
        with self.connect() as db:
            if value is not None:
                db.execute(
                    "INSERT INTO metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, json.dumps(value)),
                )
                return value
            row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def record(self, namespace: str, key: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT document FROM records WHERE namespace=? AND id=?", (namespace, key)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def records(self, namespace: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT document FROM records WHERE namespace=? ORDER BY created DESC LIMIT 100",
                (namespace,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def put_record(self, namespace: str, doc: dict, *, create=False):
        with self.connect() as db:
            if create:
                db.execute(
                    "INSERT INTO records VALUES(?,?,?,?)",
                    (namespace, doc["id"], json.dumps(doc, ensure_ascii=False), doc["created_at"]),
                )
            else:
                db.execute(
                    "UPDATE records SET document=? WHERE namespace=? AND id=?",
                    (json.dumps(doc, ensure_ascii=False), namespace, doc["id"]),
                )
