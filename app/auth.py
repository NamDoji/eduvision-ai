"""Simple auth: bcrypt passwords, random session tokens stored in SQLite."""
from __future__ import annotations

import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

try:
    from passlib.context import CryptContext
    _pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    def hash_password(pw: str) -> str:
        return _pwd_ctx.hash(pw)
    def verify_password(pw: str, hashed: str) -> bool:
        return _pwd_ctx.verify(pw, hashed)
except ImportError:
    import hashlib, hmac
    def hash_password(pw: str) -> str:  # type: ignore[misc]
        salt = secrets.token_hex(16)
        h = hashlib.sha256((salt + pw).encode()).hexdigest()
        return f"sha256${salt}${h}"
    def verify_password(pw: str, hashed: str) -> bool:  # type: ignore[misc]
        parts = hashed.split("$")
        if len(parts) != 3 or parts[0] != "sha256":
            return False
        h = hashlib.sha256((parts[1] + pw).encode()).hexdigest()
        return hmac.compare_digest(h, parts[2])

BASE_DIR = Path(__file__).resolve().parents[1]
WRITE_BASE_DIR = Path(os.environ.get("EDUVISION_WRITE_DIR", "/tmp/eduvision-ai")) if os.environ.get("VERCEL") else BASE_DIR
AUTH_DB = WRITE_BASE_DIR / "data" / "eduvision.db"
SESSION_TTL_HOURS = 24 * 7  # 1 tuần


@contextmanager
def _conn():
    AUTH_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(AUTH_DB)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_auth_db() -> None:
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'student',
                student_id TEXT,
                display_name TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL
            )
        """)
        # Tạo tài khoản giáo viên mặc định nếu chưa có
        existing = conn.execute("SELECT id FROM users WHERE role='teacher'").fetchone()
        if not existing:
            default_user = os.getenv("TEACHER_USERNAME", "giaovien")
            default_pass = os.getenv("TEACHER_PASSWORD", "eduvision2026")
            conn.execute(
                "INSERT OR IGNORE INTO users (username, password_hash, role, display_name, created_at) VALUES (?, ?, 'teacher', ?, ?)",
                (default_user, hash_password(default_pass), "Giáo viên", datetime.utcnow().isoformat()),
            )


def login(username: str, password: str) -> Optional[str]:
    """Trả về session token nếu đăng nhập thành công, None nếu thất bại."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, password_hash FROM users WHERE username = ?", (username,)
        ).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            return None
        token = secrets.token_hex(32)
        expires = (datetime.utcnow() + timedelta(hours=SESSION_TTL_HOURS)).isoformat()
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, row["id"], expires),
        )
    return token


def get_user_by_token(token: str) -> Optional[dict]:
    """Trả về thông tin user nếu token hợp lệ và chưa hết hạn."""
    if not token:
        return None
    with _conn() as conn:
        row = conn.execute("""
            SELECT u.id, u.username, u.role, u.student_id, u.display_name
            FROM sessions s JOIN users u ON s.user_id = u.id
            WHERE s.token = ? AND s.expires_at > ?
        """, (token, datetime.utcnow().isoformat())).fetchone()
    return dict(row) if row else None


def logout(token: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def create_student_account(
    username: str, password: str, student_id: str, display_name: str
) -> dict:
    """Giáo viên tạo tài khoản học sinh."""
    with _conn() as conn:
        try:
            conn.execute(
                "INSERT INTO users (username, password_hash, role, student_id, display_name, created_at) VALUES (?, ?, 'student', ?, ?, ?)",
                (username, hash_password(password), student_id, display_name, datetime.utcnow().isoformat()),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"Tên đăng nhập '{username}' đã tồn tại")
    return {"username": username, "student_id": student_id, "display_name": display_name}


def list_users() -> list:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, username, role, student_id, display_name, created_at FROM users ORDER BY created_at"
        ).fetchall()
    return [dict(r) for r in rows]
