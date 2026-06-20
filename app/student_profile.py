from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DATA_DIR

DB_FILE = DATA_DIR / "eduvision.db"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_storage(profile_file: Path | None = None) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS students (student_id TEXT PRIMARY KEY, profile_json TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS learning_events (id INTEGER PRIMARY KEY AUTOINCREMENT, student_id TEXT NOT NULL, subject TEXT NOT NULL, input TEXT NOT NULL, output TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        count = conn.execute("SELECT COUNT(*) AS c FROM students").fetchone()["c"]
        if count == 0 and profile_file and profile_file.exists():
            profiles = json.loads(profile_file.read_text(encoding="utf-8"))
            for student_id, profile in profiles.items():
                conn.execute(
                    "INSERT OR REPLACE INTO students VALUES (?, ?, ?)",
                    (student_id, json.dumps(profile, ensure_ascii=False), datetime.utcnow().isoformat()),
                )


def log_learning_event(student_id: str, subject: str, input_text: str, output_text: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO learning_events (student_id, subject, input, output, created_at) VALUES (?, ?, ?, ?, ?)",
            (student_id, subject, input_text, output_text, datetime.utcnow().isoformat()),
        )

