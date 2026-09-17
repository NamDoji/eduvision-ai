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
            CREATE TABLE IF NOT EXISTS schools (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                city TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'student',
                school_code TEXT REFERENCES schools(code),
                student_id TEXT,
                display_name TEXT,
                created_at TEXT NOT NULL
            )
        """)
        # Self-heal: add school_code column if DB was created before this version
        cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "school_code" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN school_code TEXT")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL
            )
        """)
        # Tài khoản superadmin mặc định (Cuong)
        conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, role, display_name, created_at) VALUES (?, ?, 'superadmin', ?, ?)",
            ("admin", hash_password(os.getenv("ADMIN_PASSWORD", "admin2026")), "Quản trị hệ thống", datetime.utcnow().isoformat()),
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
    username: str, password: str, student_id: str, display_name: str,
    school_code: Optional[str] = None,
) -> dict:
    """Giáo viên tạo tài khoản học sinh."""
    with _conn() as conn:
        try:
            conn.execute(
                "INSERT INTO users (username, password_hash, role, school_code, student_id, display_name, created_at) VALUES (?, ?, 'student', ?, ?, ?, ?)",
                (username, hash_password(password), school_code, student_id, display_name, datetime.utcnow().isoformat()),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"Tên đăng nhập '{username}' đã tồn tại")
    return {"username": username, "student_id": student_id, "display_name": display_name}


def list_users() -> list:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, username, role, school_code, student_id, display_name, created_at FROM users ORDER BY created_at"
        ).fetchall()
    return [dict(r) for r in rows]


# ── SCHOOL MANAGEMENT ────────────────────────────────────────────────────────

def create_school(code: str, name: str, city: str = "") -> dict:
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO schools (code, name, city, created_at) VALUES (?, ?, ?, ?)",
            (code, name, city, datetime.utcnow().isoformat()),
        )
    return {"code": code, "name": name, "city": city}


def list_schools() -> list:
    with _conn() as conn:
        schools = conn.execute("SELECT code, name, city FROM schools ORDER BY name").fetchall()
        result = []
        for s in schools:
            sc = dict(s)
            sc["teacher_count"] = conn.execute(
                "SELECT COUNT(*) FROM users WHERE school_code=? AND role='teacher'", (s["code"],)
            ).fetchone()[0]
            sc["student_count"] = conn.execute(
                "SELECT COUNT(*) FROM users WHERE school_code=? AND role='student'", (s["code"],)
            ).fetchone()[0]
            result.append(sc)
    return result


def get_school(code: str) -> Optional[dict]:
    with _conn() as conn:
        row = conn.execute("SELECT code, name, city FROM schools WHERE code=?", (code,)).fetchone()
    return dict(row) if row else None


def list_school_users(school_code: str, role: Optional[str] = None) -> list:
    with _conn() as conn:
        if role:
            rows = conn.execute(
                "SELECT id, username, role, student_id, display_name, created_at FROM users WHERE school_code=? AND role=? ORDER BY username",
                (school_code, role),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, username, role, student_id, display_name, created_at FROM users WHERE school_code=? ORDER BY role, username",
                (school_code,),
            ).fetchall()
    return [dict(r) for r in rows]


def reset_user_password(username: str, new_password: str = "1") -> bool:
    with _conn() as conn:
        row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if not row:
            return False
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(new_password), row["id"]))
    return True


def create_teacher_account(username: str, password: str, school_code: str, display_name: str) -> dict:
    with _conn() as conn:
        try:
            conn.execute(
                "INSERT INTO users (username, password_hash, role, school_code, display_name, created_at) VALUES (?, ?, 'teacher', ?, ?, ?)",
                (username, hash_password(password), school_code, display_name, datetime.utcnow().isoformat()),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"Tên đăng nhập '{username}' đã tồn tại")
    return {"username": username, "school_code": school_code, "display_name": display_name}


def change_password(username: str, old_password: str, new_password: str) -> bool:
    """Đổi mật khẩu. Trả về True nếu thành công, False nếu username không tồn tại hoặc mật khẩu cũ sai."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, password_hash FROM users WHERE username = ?", (username,)
        ).fetchone()
        if not row or not verify_password(old_password, row["password_hash"]):
            return False
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(new_password), row["id"]),
        )
    return True
