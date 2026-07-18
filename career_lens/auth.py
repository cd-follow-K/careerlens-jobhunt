"""Account authentication and request-local user context."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
import unicodedata
from contextvars import ContextVar
from datetime import datetime
from typing import Any
from uuid import uuid4

from . import common as _common


PASSWORD_ITERATIONS = 600_000
_current_user_id: ContextVar[str] = ContextVar(
    "careerlens_current_user_id", default="legacy-local-user"
)
_current_scope: ContextVar[tuple[int, str]] = ContextVar(
    "careerlens_current_scope", default=(0, "")
)


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(_common.DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def init_auth_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                password_iterations INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            )
            """
        )


def normalize_username(username: str) -> str:
    return unicodedata.normalize("NFKC", str(username or "")).strip().casefold()


def validate_username(username: str) -> str:
    normalized = normalize_username(username)
    if not normalized:
        raise ValueError("メールアドレスを入力してください。")
    return normalized


def validate_password(password: str) -> None:
    if not password:
        raise ValueError("パスワードを入力してください。")


def _derive_password(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations, dklen=32
    )


def create_account(username: str, display_name: str, password: str) -> dict[str, Any]:
    init_auth_db()
    normalized = validate_username(username)
    validate_password(password)
    clean_display_name = unicodedata.normalize("NFKC", str(display_name or "")).strip()
    if not clean_display_name:
        clean_display_name = normalized
    if len(clean_display_name) > 40:
        raise ValueError("表示名は40文字以内にしてください。")

    salt = secrets.token_bytes(24)
    password_hash = _derive_password(password, salt, PASSWORD_ITERATIONS)
    user_id = uuid4().hex
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO users (
                    user_id, username, display_name, password_salt,
                    password_hash, password_iterations, created_at, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    user_id,
                    normalized,
                    clean_display_name,
                    base64.b64encode(salt).decode("ascii"),
                    base64.b64encode(password_hash).decode("ascii"),
                    PASSWORD_ITERATIONS,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError("このメールアドレスは既に使用されています。") from exc
    return {"user_id": user_id, "username": normalized, "display_name": clean_display_name}


def authenticate_user(username: str, password: str) -> dict[str, Any] | None:
    init_auth_db()
    normalized = normalize_username(username)
    if not normalized or not password:
        return None
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT user_id, username, display_name, password_salt,
                   password_hash, password_iterations, is_active
            FROM users WHERE username = ?
            """,
            (normalized,),
        ).fetchone()
    if not row or not bool(row["is_active"]):
        return None
    try:
        salt = base64.b64decode(str(row["password_salt"]))
        expected = base64.b64decode(str(row["password_hash"]))
        actual = _derive_password(password, salt, int(row["password_iterations"]))
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(actual, expected):
        return None
    return {
        "user_id": str(row["user_id"]),
        "username": str(row["username"]),
        "display_name": str(row["display_name"]),
    }


def get_user_by_id(user_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT user_id, username, display_name
            FROM users WHERE user_id = ? AND is_active = 1
            """,
            (str(user_id),),
        ).fetchone()
    return dict(row) if row else None


def set_current_user(user_id: str) -> None:
    _current_user_id.set(str(user_id or "legacy-local-user"))


def get_current_user_id() -> str:
    return _current_user_id.get()


def set_research_scope(target_year: int, recruitment_type: str) -> None:
    _current_scope.set((int(target_year or 0), str(recruitment_type or "")))


def get_research_scope() -> tuple[int, str]:
    return _current_scope.get()
