"""Account authentication and request-local user context."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import unicodedata
from contextvars import ContextVar
from datetime import datetime
from typing import Any
from uuid import uuid4

import requests

from . import common as _common


PASSWORD_ITERATIONS = 600_000
GUEST_USER_PREFIX = "guest-session:"
_current_user_id: ContextVar[str] = ContextVar(
    "careerlens_current_user_id", default="legacy-local-user"
)
_current_guest_store: ContextVar[dict[str, Any] | None] = ContextVar(
    "careerlens_current_guest_store", default=None
)
_current_scope: ContextVar[tuple[int, str]] = ContextVar(
    "careerlens_current_scope", default=(0, "")
)


class AuthStorageError(RuntimeError):
    """Raised when the persistent account store cannot be reached."""


def _supabase_config() -> tuple[str, str] | None:
    url = str(os.getenv("SUPABASE_URL", "")).strip().rstrip("/")
    key = str(
        os.getenv("SUPABASE_SECRET_KEY", "")
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    ).strip()
    if not url and not key:
        return None
    if not url or not key:
        raise AuthStorageError(
            "SupabaseのURLと秘密鍵の両方を設定してください。"
        )
    if not url.startswith("https://"):
        raise AuthStorageError("SUPABASE_URLが不正です。")
    return url, key


def using_persistent_auth() -> bool:
    """Return whether accounts are stored in Supabase."""
    return _supabase_config() is not None


def _username_lookup(normalized_username: str) -> str:
    return hashlib.sha256(normalized_username.encode("utf-8")).hexdigest()


def _supabase_request(
    method: str,
    *,
    params: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
) -> requests.Response:
    config = _supabase_config()
    if config is None:
        raise AuthStorageError("Supabaseが設定されていません。")
    url, key = config
    headers = {
        "apikey": key,
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    # New sb_secret keys are not JWTs and must not be used as bearer tokens.
    if not key.startswith("sb_secret_"):
        headers["Authorization"] = f"Bearer {key}"
    try:
        response = requests.request(
            method,
            f"{url}/rest/v1/careerlens_users",
            headers=headers,
            params=params,
            json=payload,
            timeout=12,
        )
    except requests.RequestException as exc:
        raise AuthStorageError(
            "アカウント保存先に接続できません。"
        ) from exc
    return response


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(_common.DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def init_auth_db() -> None:
    if using_persistent_auth():
        return
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
    created_at = datetime.now().isoformat(timespec="seconds")

    if using_persistent_auth():
        response = _supabase_request(
            "POST",
            payload={
                "user_id": user_id,
                "username": normalized,
                "username_lookup": _username_lookup(normalized),
                "display_name": clean_display_name,
                "password_salt": base64.b64encode(salt).decode("ascii"),
                "password_hash": base64.b64encode(password_hash).decode("ascii"),
                "password_iterations": PASSWORD_ITERATIONS,
                "created_at": created_at,
                "is_active": True,
            },
        )
        if response.status_code == 409:
            raise ValueError("このメールアドレスは既に使用されています。")
        if not response.ok:
            raise AuthStorageError(
                f"アカウントを保存できません。({response.status_code})"
            )
        return {
            "user_id": user_id,
            "username": normalized,
            "display_name": clean_display_name,
        }

    init_auth_db()
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
                    created_at,
                ),
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError("このメールアドレスは既に使用されています。") from exc
    return {"user_id": user_id, "username": normalized, "display_name": clean_display_name}


def authenticate_user(username: str, password: str) -> dict[str, Any] | None:
    normalized = normalize_username(username)
    if not normalized or not password:
        return None
    if using_persistent_auth():
        response = _supabase_request(
            "GET",
            params={
                "username_lookup": f"eq.{_username_lookup(normalized)}",
                "select": (
                    "user_id,username,display_name,password_salt,"
                    "password_hash,password_iterations,is_active"
                ),
                "limit": "1",
            },
        )
        if not response.ok:
            raise AuthStorageError(
                f"アカウントを読み込めません。({response.status_code})"
            )
        rows = response.json()
        row = rows[0] if rows else None
    else:
        init_auth_db()
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
    if using_persistent_auth():
        response = _supabase_request(
            "GET",
            params={
                "user_id": f"eq.{str(user_id)}",
                "is_active": "eq.true",
                "select": "user_id,username,display_name",
                "limit": "1",
            },
        )
        if not response.ok:
            raise AuthStorageError(
                f"アカウントを読み込めません。({response.status_code})"
            )
        rows = response.json()
        return rows[0] if rows else None

    init_auth_db()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT user_id, username, display_name
            FROM users WHERE user_id = ? AND is_active = 1
            """,
            (str(user_id),),
        ).fetchone()
    return dict(row) if row else None


def create_guest_user() -> dict[str, Any]:
    """Create an isolated identity that exists only in one browser session."""
    return {
        "user_id": f"{GUEST_USER_PREFIX}{uuid4().hex}",
        "username": "",
        "display_name": "ゲスト",
        "is_guest": True,
    }


def is_guest_user_id(user_id: str | None = None) -> bool:
    candidate = str(user_id if user_id is not None else _current_user_id.get())
    return candidate.startswith(GUEST_USER_PREFIX)


def set_current_user(
    user_id: str,
    guest_store: dict[str, Any] | None = None,
) -> None:
    normalized = str(user_id or "legacy-local-user")
    previous = _current_user_id.get()
    _current_user_id.set(normalized)
    if is_guest_user_id(normalized):
        if guest_store is not None:
            _current_guest_store.set(guest_store)
        elif previous != normalized:
            _current_guest_store.set({})
    else:
        _current_guest_store.set(None)


def get_current_user_id() -> str:
    return _current_user_id.get()


def get_guest_session_store() -> dict[str, Any]:
    """Return non-persistent state isolated to the current guest session."""
    if not is_guest_user_id():
        return {}
    store = _current_guest_store.get()
    if store is None:
        store = {}
        _current_guest_store.set(store)
    return store


def set_research_scope(target_year: int, recruitment_type: str) -> None:
    _current_scope.set((int(target_year or 0), str(recruitment_type or "")))


def get_research_scope() -> tuple[int, str]:
    return _current_scope.get()
