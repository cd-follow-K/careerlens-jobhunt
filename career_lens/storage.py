"""SQLite persistence for histories, confirmations, caches, and deadline registry."""

import copy

from .common import *
from . import common as _common
from .auth import (
    get_current_user_id,
    get_guest_session_store,
    get_research_scope,
    is_guest_user_id,
)


def _db_path() -> Path:
    return _common.DB_PATH


def _personal_scope(
    target_year: int | None = None,
    recruitment_type: str | None = None,
) -> tuple[str, int, str]:
    scope_year, scope_type = get_research_scope()
    return (
        get_current_user_id(),
        int(target_year if target_year is not None else scope_year),
        str(recruitment_type if recruitment_type is not None else scope_type),
    )


def _guest_section(name: str) -> dict[Any, Any]:
    """Return an in-memory guest namespace that is never written to SQLite."""
    store = get_guest_session_store()
    section = store.setdefault(name, {})
    if not isinstance(section, dict):
        section = {}
        store[name] = section
    return section


def init_db() -> None:
    with sqlite3.connect(_db_path()) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS research_history_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                mode TEXT NOT NULL,
                company_input TEXT NOT NULL,
                target_year INTEGER NOT NULL,
                recruitment_type TEXT NOT NULL,
                source_records TEXT NOT NULL,
                ai_result TEXT NOT NULL,
                verification_result TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schedule_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                company_name TEXT NOT NULL,
                course_name TEXT NOT NULL,
                deadline TEXT NOT NULL,
                constraints_json TEXT NOT NULL,
                tasks_json TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                solver_status TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS deadline_confirmations (
                company_name TEXT NOT NULL,
                course_name TEXT NOT NULL,
                deadline TEXT NOT NULL,
                source_url TEXT NOT NULL,
                status TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (company_name, course_name, deadline, source_url)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS official_domain_confirmations (
                company_key TEXT NOT NULL,
                domain TEXT NOT NULL,
                status TEXT NOT NULL,
                evidence_url TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (company_key, domain)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS deadline_registry (
                company_key TEXT NOT NULL,
                company_name TEXT NOT NULL,
                target_year INTEGER NOT NULL,
                recruitment_type TEXT NOT NULL,
                course_key TEXT NOT NULL,
                course_name TEXT NOT NULL,
                deadline TEXT NOT NULL,
                deadline_type TEXT NOT NULL,
                deadline_original TEXT NOT NULL,
                source_url TEXT NOT NULL,
                source_reliability TEXT NOT NULL,
                evidence TEXT NOT NULL,
                deadline_status TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                seen_count INTEGER NOT NULL DEFAULT 1,
                seen_in_latest INTEGER NOT NULL DEFAULT 1,
                last_verified INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (
                    company_key, target_year, recruitment_type,
                    course_key, deadline, deadline_type, source_url
                )
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_search_cache (
                cache_key TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                results_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS page_text_cache (
                url TEXT PRIMARY KEY,
                fetched_at TEXT NOT NULL,
                content TEXT NOT NULL,
                fetch_success INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS source_extraction_cache (
                cache_key TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                result_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS research_history_v3 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                mode TEXT NOT NULL,
                company_input TEXT NOT NULL,
                target_year INTEGER NOT NULL,
                recruitment_type TEXT NOT NULL,
                source_records TEXT NOT NULL,
                ai_result TEXT NOT NULL,
                verification_result TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schedule_history_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                company_name TEXT NOT NULL,
                course_name TEXT NOT NULL,
                deadline TEXT NOT NULL,
                constraints_json TEXT NOT NULL,
                tasks_json TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                solver_status TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS deadline_confirmations_v2 (
                user_id TEXT NOT NULL,
                company_key TEXT NOT NULL,
                company_name TEXT NOT NULL,
                target_year INTEGER NOT NULL,
                recruitment_type TEXT NOT NULL,
                course_key TEXT NOT NULL,
                course_name TEXT NOT NULL,
                deadline TEXT NOT NULL,
                source_url TEXT NOT NULL,
                status TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (
                    user_id, company_key, target_year, recruitment_type,
                    course_key, deadline, source_url
                )
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS official_domain_confirmations_v2 (
                user_id TEXT NOT NULL,
                company_key TEXT NOT NULL,
                domain TEXT NOT NULL,
                status TEXT NOT NULL,
                evidence_url TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, company_key, domain)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS deadline_registry_v2 (
                user_id TEXT NOT NULL,
                company_key TEXT NOT NULL,
                company_name TEXT NOT NULL,
                target_year INTEGER NOT NULL,
                recruitment_type TEXT NOT NULL,
                course_key TEXT NOT NULL,
                course_name TEXT NOT NULL,
                deadline TEXT NOT NULL,
                deadline_type TEXT NOT NULL,
                deadline_original TEXT NOT NULL,
                source_url TEXT NOT NULL,
                source_reliability TEXT NOT NULL,
                evidence TEXT NOT NULL,
                deadline_status TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                seen_count INTEGER NOT NULL DEFAULT 1,
                seen_in_latest INTEGER NOT NULL DEFAULT 1,
                last_verified INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (
                    user_id, company_key, target_year, recruitment_type,
                    course_key, deadline, deadline_type, source_url
                )
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_deadline_confirmations_v2_community
            ON deadline_confirmations_v2 (
                company_key, target_year, recruitment_type,
                course_key, deadline, source_url, status
            )
            """
        )


def get_deadline_confirmation(
    company_name: str,
    course_name: str,
    deadline: str,
    source_url: str,
    target_year: int | None = None,
    recruitment_type: str | None = None,
) -> str:
    user_id, scope_year, scope_type = _personal_scope(target_year, recruitment_type)
    if is_guest_user_id(user_id):
        key = (
            registry_company_key(company_name), scope_year, scope_type,
            registry_course_key(course_name), deadline, source_url,
        )
        return str(_guest_section("deadline_confirmations").get(key, "未確認"))
    try:
        with sqlite3.connect(_db_path()) as conn:
            row = conn.execute(
                """
                SELECT status FROM deadline_confirmations_v2
                WHERE user_id = ? AND company_key = ? AND target_year = ?
                  AND recruitment_type = ? AND course_key = ?
                  AND deadline = ? AND source_url = ?
                """,
                (
                    user_id, registry_company_key(company_name), scope_year, scope_type,
                    registry_course_key(course_name), deadline, source_url,
                ),
            ).fetchone()
    except sqlite3.OperationalError:
        return "未確認"
    return str(row[0]) if row else "未確認"


def set_deadline_confirmation(
    company_name: str,
    course_name: str,
    deadline: str,
    source_url: str,
    status: str,
    target_year: int | None = None,
    recruitment_type: str | None = None,
) -> None:
    if status not in {"未確認", "確認済み", "誤情報として除外"}:
        raise ValueError("不正な確認状態です。")
    user_id, scope_year, scope_type = _personal_scope(target_year, recruitment_type)
    if is_guest_user_id(user_id):
        key = (
            registry_company_key(company_name), scope_year, scope_type,
            registry_course_key(course_name), deadline, source_url,
        )
        _guest_section("deadline_confirmations")[key] = status
        return
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            INSERT INTO deadline_confirmations_v2 (
                user_id, company_key, company_name, target_year, recruitment_type,
                course_key, course_name, deadline, source_url, status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(
                user_id, company_key, target_year, recruitment_type,
                course_key, deadline, source_url
            )
            DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at
            """,
            (
                user_id, registry_company_key(company_name), company_name,
                scope_year, scope_type, registry_course_key(course_name), course_name,
                deadline, source_url, status,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )


def get_community_deadline_consensus(
    company_name: str,
    target_year: int,
    recruitment_type: str,
    course_name: str,
    deadline: str,
    source_url: str = "",
) -> dict[str, int]:
    """Return anonymous confirmation counts from other accounts."""
    user_id = get_current_user_id()
    parameters: list[Any] = [
        registry_company_key(company_name), int(target_year), recruitment_type,
        registry_course_key(course_name), deadline, user_id,
    ]
    url_clause = ""
    if source_url:
        url_clause = " AND source_url = ?"
        parameters.append(source_url)
    try:
        with sqlite3.connect(_db_path()) as conn:
            rows = conn.execute(
                f"""
                SELECT status, COUNT(DISTINCT user_id)
                FROM deadline_confirmations_v2
                WHERE company_key = ? AND target_year = ? AND recruitment_type = ?
                  AND course_key = ? AND deadline = ? AND user_id <> ?
                  {url_clause}
                GROUP BY status
                """,
                tuple(parameters),
            ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    counts = {"confirmed_count": 0, "rejected_count": 0, "unconfirmed_count": 0}
    for status, count in rows:
        key = {
            "確認済み": "confirmed_count",
            "誤情報として除外": "rejected_count",
            "未確認": "unconfirmed_count",
        }.get(str(status))
        if key:
            counts[key] = int(count)
    return counts


def list_community_deadline_consensus(
    company_name: str, target_year: int, recruitment_type: str, limit: int = 30
) -> list[dict[str, Any]]:
    """Return anonymous, aggregate-only signals; no account identifiers leave storage."""
    user_id = get_current_user_id()
    try:
        with sqlite3.connect(_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT course_name, deadline, source_url,
                       COUNT(DISTINCT CASE WHEN status = '確認済み' THEN user_id END)
                           AS confirmed_count,
                       COUNT(DISTINCT CASE WHEN status = '誤情報として除外' THEN user_id END)
                           AS rejected_count
                FROM deadline_confirmations_v2
                WHERE company_key = ? AND target_year = ? AND recruitment_type = ?
                  AND user_id <> ?
                GROUP BY course_key, course_name, deadline, source_url
                HAVING confirmed_count > 0 OR rejected_count > 0
                ORDER BY confirmed_count DESC, rejected_count DESC, deadline
                LIMIT ?
                """,
                (
                    registry_company_key(company_name), int(target_year),
                    recruitment_type, user_id, int(limit),
                ),
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(row) for row in rows]


def get_official_domain_confirmation(company_name: str, url: str) -> str:
    """企業ごとのドメイン公式性に対する利用者確認を返す。"""
    domain = host_of(url)
    if not domain:
        return "未確認"
    if is_guest_user_id():
        key = (registry_company_key(company_name), domain)
        return str(_guest_section("official_domain_confirmations").get(key, "未確認"))
    try:
        with sqlite3.connect(_db_path()) as conn:
            row = conn.execute(
                """
                SELECT status FROM official_domain_confirmations_v2
                WHERE user_id = ? AND company_key = ? AND domain = ?
                """,
                (get_current_user_id(), registry_company_key(company_name), domain),
            ).fetchone()
    except sqlite3.OperationalError:
        return "未確認"
    return str(row[0]) if row else "未確認"


def set_official_domain_confirmation(
    company_name: str, url: str, status: str
) -> None:
    """利用者が確認した企業公式ドメインの判定を保存する。"""
    if status not in {"未確認", "公式と確認", "非公式と確認"}:
        raise ValueError("不正な公式ドメイン確認状態です。")
    domain = host_of(url)
    if not domain:
        raise ValueError("URLからドメインを確認できません。")
    if is_guest_user_id():
        key = (registry_company_key(company_name), domain)
        _guest_section("official_domain_confirmations")[key] = status
        return
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            INSERT INTO official_domain_confirmations_v2 (
                user_id, company_key, domain, status, evidence_url, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, company_key, domain)
            DO UPDATE SET
                status = excluded.status,
                evidence_url = excluded.evidence_url,
                updated_at = excluded.updated_at
            """,
            (
                get_current_user_id(), registry_company_key(company_name),
                domain, status, url,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )


def is_social_reliability(value: Any) -> bool:
    return str(value or "") in {"official_social", "social_unverified"}


def social_confirmation_key(
    company_name: str, course_name: str, deadline: str, source_url: str
) -> str:
    raw = "|".join([company_name, course_name, deadline, source_url])
    return str(abs(hash(raw)))


def registry_company_key(company_name: str) -> str:
    """会社表記の揺れを抑え、企業単位の蓄積キーを作る。"""
    key = normalize_text(company_name)
    for token in ("株式会社", "有限会社", "合同会社", "ホールディングス", "グループ"):
        key = key.replace(normalize_text(token), "")
    return key or normalize_text(company_name)


def registry_course_key(course_name: str) -> str:
    return normalize_text(course_name) or "選考"


def _registry_current_rows(
    company_name: str,
    ai_result: dict[str, Any],
    verification: VerificationResult,
) -> list[dict[str, Any]]:
    check_map = {
        (str(item.get("course_name")), str(item.get("deadline")), str(item.get("source_url"))): item
        for item in verification.course_deadline_checks
    }
    rows: list[dict[str, Any]] = []
    for course_name, item in iter_course_deadlines(ai_result):
        deadline_iso = str(item.get("deadline") or "")
        if parse_deadline(deadline_iso) is None:
            continue
        source_url = str(item.get("source_url") or "")
        check = check_map.get((course_name, deadline_iso, source_url), {})
        source_reliability = str(item.get("source_reliability") or "other")
        if (
            check.get("source_type") == "企業公式候補"
            and not check.get("official_source_verified")
        ):
            source_reliability = "other"
        verified = bool(check.get("verified"))
        rows.append({
            "company_name": company_name,
            "course_name": course_name,
            "deadline": deadline_iso,
            "deadline_type": str(item.get("deadline_type") or "応募締切"),
            "deadline_original": str(item.get("deadline_original") or deadline_iso),
            "source_url": source_url,
            "source_reliability": source_reliability,
            "evidence": str(item.get("evidence") or ""),
            "deadline_status": (
                str(item.get("deadline_status") or "confirmed")
                if verified else "needs_confirmation"
            ),
            "verified": verified,
        })

    # コース配列がない本選考などでは要約締切を蓄積する。
    if not rows:
        deadline_iso = str(ai_result.get("deadline") or "")
        if parse_deadline(deadline_iso) is not None:
            rows.append({
                "company_name": company_name,
                "course_name": str(ai_result.get("recruitment_type") or "選考"),
                "deadline": deadline_iso,
                "deadline_type": str(ai_result.get("deadline_type") or "応募締切"),
                "deadline_original": str(ai_result.get("deadline_original") or deadline_iso),
                "source_url": str(ai_result.get("source_url") or ""),
                "source_reliability": str(ai_result.get("source_reliability") or "other"),
                "evidence": str(ai_result.get("evidence") or ""),
                "deadline_status": str(ai_result.get("deadline_status") or "needs_confirmation"),
                "verified": bool(verification.passed),
            })
    return rows


def save_deadlines_to_registry(
    company_name: str,
    target_year: int,
    recruitment_type: str,
    ai_result: dict[str, Any],
    verification: VerificationResult,
) -> None:
    """企業を最上位単位とし、年度・募集区分を分離して締切を蓄積する。"""
    user_id = get_current_user_id()
    if is_guest_user_id(user_id):
        return
    company_key = registry_company_key(company_name)
    now = datetime.now().isoformat(timespec="seconds")
    rows = _registry_current_rows(company_name, ai_result, verification)
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            UPDATE deadline_registry_v2 SET seen_in_latest = 0
            WHERE user_id = ? AND company_key = ?
              AND target_year = ? AND recruitment_type = ?
            """,
            (user_id, company_key, target_year, recruitment_type),
        )
        for row in rows:
            conn.execute(
                """
                INSERT INTO deadline_registry_v2 (
                    user_id, company_key, company_name, target_year, recruitment_type,
                    course_key, course_name, deadline, deadline_type,
                    deadline_original, source_url, source_reliability, evidence,
                    deadline_status, first_seen_at, last_seen_at, seen_count,
                    seen_in_latest, last_verified
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?)
                ON CONFLICT (
                    user_id, company_key, target_year, recruitment_type,
                    course_key, deadline, deadline_type, source_url
                ) DO UPDATE SET
                    company_name = excluded.company_name,
                    course_name = excluded.course_name,
                    deadline_original = excluded.deadline_original,
                    source_reliability = excluded.source_reliability,
                    evidence = excluded.evidence,
                    deadline_status = excluded.deadline_status,
                    last_seen_at = excluded.last_seen_at,
                    seen_count = deadline_registry_v2.seen_count + 1,
                    seen_in_latest = 1,
                    last_verified = excluded.last_verified
                """,
                (
                    user_id, company_key, company_name, target_year, recruitment_type,
                    registry_course_key(row["course_name"]), row["course_name"],
                    row["deadline"], row["deadline_type"], row["deadline_original"],
                    row["source_url"], row["source_reliability"], row["evidence"],
                    row["deadline_status"], now, now, int(row["verified"]),
                ),
            )


def load_deadline_registry(
    company_name: str, target_year: int, recruitment_type: str
) -> list[dict[str, Any]]:
    company_key = registry_company_key(company_name)
    user_id = get_current_user_id()
    if is_guest_user_id(user_id):
        return []
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM deadline_registry_v2
            WHERE user_id = ? AND company_key = ?
              AND target_year = ? AND recruitment_type = ?
            ORDER BY deadline, course_name, source_reliability
            """,
            (user_id, company_key, target_year, recruitment_type),
        ).fetchall()
    return [dict(row) for row in rows]


def merge_registry_into_ai_result(
    company_name: str,
    target_year: int,
    recruitment_type: str,
    ai_result: dict[str, Any],
) -> dict[str, Any]:
    """今回取得できなかった過去の締切も消さずに統合する。"""
    registry_rows = load_deadline_registry(company_name, target_year, recruitment_type)
    courses = ai_result.get("courses") or []
    if not isinstance(courses, list):
        courses = []
    course_map: dict[str, dict[str, Any]] = {}
    for course in courses:
        if not isinstance(course, dict):
            continue
        name = str(course.get("course_name") or "選考")
        course_map[registry_course_key(name)] = course

    for row in registry_rows:
        confirmation = get_deadline_confirmation(
            company_name, str(row["course_name"]), str(row["deadline"]), str(row["source_url"])
        )
        if confirmation == "誤情報として除外":
            continue
        course_key = str(row["course_key"])
        course = course_map.setdefault(
            course_key,
            {
                "course_name": row["course_name"],
                "course_summary": None,
                "eligibility": None,
                "deadlines": [],
            },
        )
        deadlines = course.setdefault("deadlines", [])
        exists = any(
            str(item.get("deadline") or "") == str(row["deadline"])
            and normalize_text(str(item.get("deadline_type") or "")) == normalize_text(str(row["deadline_type"]))
            and normalize_url(str(item.get("source_url") or "")) == normalize_url(str(row["source_url"]))
            for item in deadlines if isinstance(item, dict)
        )
        metadata = {
            "_registry_first_seen": row["first_seen_at"],
            "_registry_last_seen": row["last_seen_at"],
            "_registry_seen_count": int(row["seen_count"]),
            "_registry_seen_latest": bool(row["seen_in_latest"]),
            "_registry_last_verified": bool(row["last_verified"]),
        }
        if exists:
            for item in deadlines:
                if not isinstance(item, dict):
                    continue
                if (
                    str(item.get("deadline") or "") == str(row["deadline"])
                    and normalize_text(str(item.get("deadline_type") or "")) == normalize_text(str(row["deadline_type"]))
                    and normalize_url(str(item.get("source_url") or "")) == normalize_url(str(row["source_url"]))
                ):
                    item.update(metadata)
                    break
            continue
        deadlines.append({
            "deadline": row["deadline"],
            "deadline_original": row["deadline_original"],
            "deadline_type": row["deadline_type"],
            "source_id": "HISTORY",
            "source_url": row["source_url"],
            "evidence": row["evidence"],
            "confidence": 0.0,
            "source_reliability": row["source_reliability"],
            "deadline_status": (
                row["deadline_status"]
                if bool(row["seen_in_latest"])
                else "needs_confirmation"
            ),
            "notes": ["過去の検索で取得。今回の検索では未取得。"],
            **metadata,
        })

    ai_result["courses"] = list(course_map.values())
    ai_result["_registry_count"] = len(registry_rows)
    return postprocess_ai_result(ai_result)


def save_history(
    mode: str,
    company_input: str,
    target_year: int,
    recruitment_type: str,
    source_records: list[dict[str, Any]],
    ai_result: dict[str, Any],
    verification: VerificationResult,
) -> None:
    if is_guest_user_id():
        return
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            INSERT INTO research_history_v3 (
                user_id, created_at, mode, company_input, target_year, recruitment_type,
                source_records, ai_result, verification_result
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                get_current_user_id(),
                datetime.now().isoformat(timespec="seconds"),
                mode,
                company_input,
                target_year,
                recruitment_type,
                json.dumps(source_records, ensure_ascii=False),
                json.dumps(ai_result, ensure_ascii=False),
                json.dumps(asdict(verification), ensure_ascii=False),
            ),
        )


def _cache_is_fresh(timestamp: str, ttl_hours: int) -> bool:
    try:
        created = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return False
    return datetime.now() - created <= timedelta(hours=ttl_hours)


def _search_cache_key(
    company: str, target_year: int, recruitment_type: str, strategy: str
) -> str:
    raw = "|".join(
        [
            get_current_user_id(), normalize_text(company), str(target_year),
            recruitment_type, strategy,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_search_cache(
    company: str, target_year: int, recruitment_type: str, strategy: str
) -> list[dict[str, Any]] | None:
    key = _search_cache_key(company, target_year, recruitment_type, strategy)
    if is_guest_user_id():
        cached = _guest_section("search_cache").get(key)
        if not isinstance(cached, dict) or not _cache_is_fresh(
            str(cached.get("created_at") or ""), SEARCH_CACHE_TTL_HOURS
        ):
            return None
        results = cached.get("results")
        return copy.deepcopy(results) if isinstance(results, list) else None
    with sqlite3.connect(_db_path()) as conn:
        row = conn.execute(
            "SELECT created_at, results_json FROM web_search_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
    if not row or not _cache_is_fresh(str(row[0]), SEARCH_CACHE_TTL_HOURS):
        return None
    try:
        results = json.loads(str(row[1]))
    except json.JSONDecodeError:
        return None
    return results if isinstance(results, list) else None


def save_search_cache(
    company: str,
    target_year: int,
    recruitment_type: str,
    strategy: str,
    results: list[dict[str, Any]],
) -> None:
    key = _search_cache_key(company, target_year, recruitment_type, strategy)
    if is_guest_user_id():
        _guest_section("search_cache")[key] = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "results": copy.deepcopy(results),
        }
        return
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            INSERT INTO web_search_cache (cache_key, created_at, results_json)
            VALUES (?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                created_at = excluded.created_at,
                results_json = excluded.results_json
            """,
            (
                key,
                datetime.now().isoformat(timespec="seconds"),
                json.dumps(results, ensure_ascii=False),
            ),
        )


def load_page_cache(url: str) -> tuple[str, bool] | None:
    with sqlite3.connect(_db_path()) as conn:
        row = conn.execute(
            "SELECT fetched_at, content, fetch_success FROM page_text_cache WHERE url = ?",
            (normalize_url(url),),
        ).fetchone()
    if not row:
        return None
    success = bool(row[2])
    ttl = PAGE_CACHE_TTL_HOURS if success else FAILED_PAGE_CACHE_TTL_HOURS
    if not _cache_is_fresh(str(row[0]), ttl):
        return None
    return str(row[1]), success


def save_page_cache(url: str, content: str, success: bool) -> None:
    # 並列取得中のSQLite同時書込みを直列化し、database is lockedを避ける。
    with CACHE_DB_LOCK:
        with sqlite3.connect(_db_path(), timeout=30) as conn:
            conn.execute(
                """
                INSERT INTO page_text_cache (url, fetched_at, content, fetch_success)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    fetched_at = excluded.fetched_at,
                    content = excluded.content,
                    fetch_success = excluded.fetch_success
                """,
                (
                    normalize_url(url),
                    datetime.now().isoformat(timespec="seconds"),
                    content,
                    int(success),
                ),
            )


def _source_extraction_cache_key(
    company: str,
    target_year: int,
    recruitment_type: str,
    record: dict[str, Any],
    user_id: str | None = None,
) -> str:
    payload = "|".join([
        "accuracy-v71",
        str(user_id or get_current_user_id()),
        normalize_text(company),
        str(target_year),
        recruitment_type,
        normalize_url(str(record.get("url") or "")),
        hashlib.sha256(str(record.get("source_text") or "").encode("utf-8")).hexdigest(),
        REVIEW_MODEL,
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_source_extraction_cache(
    company: str,
    target_year: int,
    recruitment_type: str,
    record: dict[str, Any],
    user_id: str | None = None,
) -> dict[str, Any] | None:
    if is_guest_user_id(user_id):
        return None
    key = _source_extraction_cache_key(
        company, target_year, recruitment_type, record, user_id
    )
    with sqlite3.connect(_db_path()) as conn:
        row = conn.execute(
            "SELECT created_at, result_json FROM source_extraction_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
    if not row or not _cache_is_fresh(str(row[0]), SOURCE_EXTRACTION_CACHE_TTL_HOURS):
        return None
    try:
        parsed = json.loads(str(row[1]))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def save_source_extraction_cache(
    company: str, target_year: int, recruitment_type: str,
    record: dict[str, Any], result: dict[str, Any], user_id: str | None = None
) -> None:
    if is_guest_user_id(user_id):
        return
    key = _source_extraction_cache_key(
        company, target_year, recruitment_type, record, user_id
    )
    with CACHE_DB_LOCK:
        with sqlite3.connect(_db_path(), timeout=30) as conn:
            conn.execute(
                """
                INSERT INTO source_extraction_cache (cache_key, created_at, result_json)
                VALUES (?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    created_at = excluded.created_at,
                    result_json = excluded.result_json
                """,
                (key, datetime.now().isoformat(timespec="seconds"),
                 json.dumps(result, ensure_ascii=False)),
            )


def save_schedule_history(
    company_name: str,
    course_name: str,
    deadline: str,
    constraints: dict[str, Any],
    tasks: list[dict[str, Any]],
    plan: list[dict[str, Any]],
    solver_status: str,
) -> None:
    if is_guest_user_id():
        return
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            INSERT INTO schedule_history_v2 (
                user_id, created_at, company_name, course_name, deadline,
                constraints_json, tasks_json, plan_json, solver_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                get_current_user_id(),
                datetime.now().isoformat(timespec="seconds"),
                company_name,
                course_name,
                deadline,
                json.dumps(constraints, ensure_ascii=False),
                json.dumps(tasks, ensure_ascii=False),
                json.dumps(plan, ensure_ascii=False),
                solver_status,
            ),
        )
