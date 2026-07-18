"""Deadline options, AI task decomposition, Z3 planning, and calendar export."""

from .common import *
from .storage import *
from .ai_client import *


def manually_confirmed_progressive_options(
    company_name: str,
    progressive_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return page-level candidates explicitly confirmed by the user."""
    options: list[dict[str, Any]] = []
    for item in progressive_candidates:
        deadline_iso = str(item.get("deadline") or "")
        if parse_deadline(deadline_iso) is None:
            continue
        course_name = str(item.get("course_name") or "コース未特定")
        source_url = str(item.get("source_url") or "")
        confirmation_status = get_deadline_confirmation(
            company_name, course_name, deadline_iso, source_url
        )
        if confirmation_status != "確認済み":
            continue
        reliability = str(item.get("source_reliability") or "other")
        source_type_label = str(item.get("source_type") or "")
        options.append({
            "course_name": course_name,
            "deadline": deadline_iso,
            "deadline_original": item.get("deadline_original") or deadline_iso,
            "deadline_type": item.get("deadline_type") or "応募締切",
            "evidence": item.get("evidence") or "",
            "source_url": source_url,
            "verified": True,
            "machine_verified": False,
            "source_reliability": reliability,
            "social_source": (
                source_type_label.startswith("SNS")
                or is_social_reliability(reliability)
            ),
            "confirmation_status": confirmation_status,
            "seen_latest": True,
            "first_seen": None,
            "last_seen": None,
            "seen_count": 1,
        })
    return options


def collect_deadline_options(
    ai_result: dict[str, Any],
    verification: VerificationResult,
    progressive_candidates: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    check_map = {
        (str(item.get("course_name")), str(item.get("deadline")), str(item.get("source_url"))): item
        for item in verification.course_deadline_checks
    }
    candidates: dict[tuple[str, str, str], list[dict[str, Any]]] = {}

    reliability_rank = {
        "official": 70, "manual": 70, "mynavi": 60, "onecareer": 55,
        "official_social": 45, "other": 30, "social_unverified": 20,
    }

    for course_name, item in iter_course_deadlines(ai_result):
        deadline_iso = str(item.get("deadline") or "")
        if parse_deadline(deadline_iso) is None:
            continue
        source_url = str(item.get("source_url") or "")
        check = check_map.get((course_name, deadline_iso, source_url), {})
        confirmation_status = str(check.get("confirmation_status") or "未確認")
        if confirmation_status == "誤情報として除外":
            continue
        reliability = str(item.get("source_reliability") or "other")
        option = {
            "course_name": course_name,
            "deadline": deadline_iso,
            "deadline_original": item.get("deadline_original") or deadline_iso,
            "deadline_type": item.get("deadline_type") or "応募締切",
            "evidence": item.get("evidence") or "",
            "source_url": source_url,
            "verified": (
                bool(check.get("verified")) or confirmation_status == "確認済み"
            ),
            "machine_verified": bool(check.get("verified")),
            "source_reliability": reliability,
            "social_source": bool(check.get("social_source")) or is_social_reliability(reliability),
            "confirmation_status": confirmation_status,
            "seen_latest": bool(item.get("_registry_seen_latest", True)),
            "first_seen": item.get("_registry_first_seen"),
            "last_seen": item.get("_registry_last_seen"),
            "seen_count": int(item.get("_registry_seen_count") or 1),
        }
        key = (course_name, deadline_iso, str(option["deadline_type"]))
        candidates.setdefault(key, []).append(option)

    # 同じコース・同じ締切は、検証済み・今回取得・信頼度の順で代表情報源を選ぶ。
    options: list[dict[str, Any]] = []
    for values in candidates.values():
        values.sort(
            key=lambda item: (
                int(bool(item["verified"])),
                int(item.get("confirmation_status") == "確認済み"),
                int(bool(item["seen_latest"])),
                reliability_rank.get(str(item["source_reliability"]), 0),
                int(item.get("seen_count") or 0),
            ),
            reverse=True,
        )
        options.append(values[0])

    # コース情報がない本選考等では要約締切を利用する。
    if not options:
        deadline_iso = str(ai_result.get("deadline") or "")
        if parse_deadline(deadline_iso) is not None:
            course_name = str(ai_result.get("recruitment_type") or "選考")
            source_url = str(ai_result.get("source_url") or "")
            reliability = ai_result.get("source_reliability") or "other"
            social_source = is_social_reliability(reliability)
            confirmation_status = get_deadline_confirmation(
                str(ai_result.get("_company_input") or ai_result.get("company_name") or ""),
                course_name, deadline_iso, source_url
            )
            if confirmation_status != "誤情報として除外":
                options.append({
                    "course_name": course_name,
                    "deadline": deadline_iso,
                    "deadline_original": ai_result.get("deadline_original") or deadline_iso,
                    "deadline_type": ai_result.get("deadline_type") or "応募締切",
                    "evidence": ai_result.get("evidence") or "",
                    "source_url": source_url,
                    "verified": (
                        bool(verification.passed)
                        or confirmation_status == "確認済み"
                    ),
                    "machine_verified": bool(verification.passed),
                    "source_reliability": reliability,
                    "social_source": social_source,
                    "confirmation_status": confirmation_status,
                    "seen_latest": True,
                    "first_seen": None,
                    "last_seen": None,
                    "seen_count": 1,
                })
    for option in manually_confirmed_progressive_options(
        str(ai_result.get("_company_input") or ai_result.get("company_name") or ""),
        progressive_candidates or [],
    ):
        key = (
            str(option["course_name"]),
            str(option["deadline"]),
            str(option["deadline_type"]),
        )
        existing_index = next((
            index for index, current in enumerate(options)
            if (
                str(current["course_name"]),
                str(current["deadline"]),
                str(current["deadline_type"]),
            ) == key
        ), None)
        if existing_index is None:
            options.append(option)
        elif not options[existing_index].get("verified"):
            options[existing_index] = option

    return sorted(options, key=lambda item: (item["deadline"], item["course_name"]))


def fallback_tasks(recruitment_type: str) -> list[dict[str, Any]]:
    if recruitment_type == "説明会":
        raw = [
            ("企業情報の確認", "事業内容と説明会の目的を整理する", 60),
            ("質問事項の作成", "説明会で確認したい質問を準備する", 60),
            ("参加環境の確認", "URL、服装、通信環境、開始時刻を確認する", 30),
        ]
    elif recruitment_type == "インターン":
        raw = [
            ("企業・コース研究", "企業と応募コースの特徴を整理する", 90),
            ("応募設問の整理", "設問、文字数、提出物を一覧化する", 60),
            ("応募書類の初稿", "ES等の初稿を作成する", 120),
            ("内容の推敲", "根拠、具体性、誤字を確認して修正する", 90),
            ("最終確認・提出", "提出形式と締切を再確認して提出する", 30),
        ]
    else:
        raw = [
            ("企業研究", "事業、強み、募集職種を整理する", 90),
            ("応募設問の整理", "設問、文字数、提出物を確認する", 60),
            ("ES初稿の作成", "設問に対応した初稿を作成する", 120),
            ("推敲・第三者確認", "論理性、具体性、誤字を確認する", 90),
            ("最終確認・提出", "入力内容と添付ファイルを確認して提出する", 30),
        ]
    return [
        {"task_name": name, "description": desc, "duration_minutes": minutes, "order": i}
        for i, (name, desc, minutes) in enumerate(raw, start=1)
    ]


def sanitize_tasks(tasks: Any, recruitment_type: str) -> list[dict[str, Any]]:
    if not isinstance(tasks, list):
        return fallback_tasks(recruitment_type)
    cleaned: list[dict[str, Any]] = []
    for item in tasks:
        if not isinstance(item, dict):
            continue
        name = str(item.get("task_name") or "").strip()
        description = str(item.get("description") or "").strip()
        try:
            duration = int(item.get("duration_minutes"))
            order = int(item.get("order"))
        except (TypeError, ValueError):
            continue
        if not name:
            continue
        duration = max(30, min(240, int(round(duration / 30) * 30)))
        cleaned.append({
            "task_name": name[:80],
            "description": description[:300],
            "duration_minutes": duration,
            "order": order,
        })
    cleaned.sort(key=lambda item: (item["order"], item["task_name"]))
    # 重複名を削除し、順序番号を振り直す。
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in cleaned:
        key = normalize_text(item["task_name"])
        if key in seen:
            continue
        seen.add(key)
        item["order"] = len(unique) + 1
        unique.append(item)
    return unique if 3 <= len(unique) <= 7 else fallback_tasks(recruitment_type)


def ask_ai_for_tasks(
    company_name: str,
    course_name: str,
    recruitment_type: str,
    deadline_iso: str,
) -> tuple[list[dict[str, Any]], str]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return fallback_tasks(recruitment_type), "APIキー未設定のため標準テンプレートを利用した。"

    client = make_gemini_client(api_key)
    prompt = f"""
企業名: {company_name}
応募区分: {recruitment_type}
コース・選考名: {course_name}
締切: {deadline_iso}

締切までに行う準備を3〜7個の具体的な作業へ分解してください。
作業時間は30分単位、各30〜240分としてください。
企業固有の未確認情報を推測してはいけません。
作業は依存関係の順に並べ、応募書類の作成、推敲、最終確認などを必要に応じて含めてください。
日付は割り当てず、作業名、説明、所要時間、順序だけを返してください。
""".strip()
    try:
        response, _, _, _ = generate_content_resilient(
            client,
            primary_model=MODEL,
            fallback_models=[EXTRACT_FALLBACK_MODEL],
            contents=prompt,
            schema=TASK_RESULT_SCHEMA,
            system_instruction=(
                "あなたは就職活動の作業分解担当です。"
                "確認できない企業固有情報は作らず、実行可能な一般作業へ分解してください。"
                "必ずJSONだけを返してください。"
            ),
            max_output_tokens=4_000,
            thinking_level="minimal",
        )
        if not response.text:
            raise RuntimeError("作業分解結果が空でした。")
        parsed = extract_json(response.text)
        tasks = sanitize_tasks(parsed.get("tasks"), recruitment_type)
        summary = str(parsed.get("reasoning_summary") or "AIが作業を分解した。")
        return tasks, summary
    except Exception as exc:
        return fallback_tasks(recruitment_type), f"AI作業分解に失敗したため標準テンプレートを利用した: {exc}"


def parse_blocked_dates(text: str) -> set[date]:
    blocked: set[date] = set()
    for token in re.split(r"[,、\s]+", text.strip()):
        if not token:
            continue
        parsed = parse_deadline(token)
        if parsed is None:
            raise ValueError(f"除外日「{token}」はYYYY-MM-DD形式で入力してください。")
        blocked.add(parsed)
    return blocked


def schedule_tasks_with_z3(
    tasks: list[dict[str, Any]],
    start_date: date,
    deadline_date: date,
    allowed_weekdays: set[int],
    blocked_dates: set[date],
    max_daily_minutes: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if deadline_date <= start_date:
        raise ValueError("締切が開始日以前のため予定を作成できません。")
    if not allowed_weekdays:
        raise ValueError("作業可能な曜日を1つ以上選択してください。")

    candidate_dates: list[date] = []
    current = start_date
    while current < deadline_date:
        if current.weekday() in allowed_weekdays and current not in blocked_dates:
            candidate_dates.append(current)
        current += timedelta(days=1)
    if not candidate_dates:
        raise ValueError("指定条件内に作業可能日がありません。")

    durations = [int(task["duration_minutes"]) for task in tasks]
    if any(duration > max_daily_minutes for duration in durations):
        too_long = [tasks[i]["task_name"] for i, duration in enumerate(durations) if duration > max_daily_minutes]
        raise ValueError(
            "1日の上限時間より長い作業があります: " + "、".join(too_long)
        )
    if sum(durations) > len(candidate_dates) * max_daily_minutes:
        raise ValueError("必要作業時間が、締切までに確保できる総作業時間を超えています。")

    optimizer = Optimize()
    day_vars = [Int(f"task_day_{i}") for i in range(len(tasks))]
    constraint_count = 0
    for var in day_vars:
        optimizer.add(var >= 0, var < len(candidate_dates))
        constraint_count += 2
    # 作業順序を維持する。同日配置は許可するが、表示時は順序どおりに並べる。
    for i in range(len(day_vars) - 1):
        optimizer.add(day_vars[i] <= day_vars[i + 1])
        constraint_count += 1

    daily_loads = []
    for day_index in range(len(candidate_dates)):
        load = Sum([If(day_vars[i] == day_index, durations[i], 0) for i in range(len(tasks))])
        optimizer.add(load <= max_daily_minutes)
        constraint_count += 1
        daily_loads.append(load)

    max_load = Int("max_daily_load")
    optimizer.add(max_load >= 0)
    constraint_count += 1
    for load in daily_loads:
        optimizer.add(max_load >= load)
        constraint_count += 1

    # 第1目的: 1日への集中を抑える。第2目的: 早めに全作業を完了する。
    optimizer.minimize(max_load)
    optimizer.minimize(day_vars[-1])
    optimizer.minimize(Sum(day_vars))

    status = optimizer.check()
    if status != sat:
        raise RuntimeError("Z3が制約を満たす予定を発見できませんでした。条件を緩めてください。")
    model = optimizer.model()

    plan: list[dict[str, Any]] = []
    for i, task in enumerate(tasks):
        day_index = model.eval(day_vars[i]).as_long()
        plan.append({
            **task,
            "scheduled_date": candidate_dates[day_index].isoformat(),
            "day_index": day_index,
        })
    plan.sort(key=lambda item: (item["scheduled_date"], item["order"]))

    solver_info = {
        "solver": "Microsoft Z3 Optimize",
        "status": str(status),
        "candidate_date_count": len(candidate_dates),
        "constraint_count": constraint_count,
        "total_task_minutes": sum(durations),
        "max_daily_minutes": max_daily_minutes,
        "allowed_dates": [d.isoformat() for d in candidate_dates],
        "blocked_dates": sorted(d.isoformat() for d in blocked_dates),
        "objectives": ["1日最大作業時間の最小化", "最終作業日の早期化", "作業日の早期化"],
    }
    return plan, solver_info


def assign_event_times(plan: list[dict[str, Any]], daily_start_time: dt_time) -> list[dict[str, Any]]:
    assigned: list[dict[str, Any]] = []
    offsets: dict[str, int] = {}
    for item in plan:
        date_iso = str(item["scheduled_date"])
        offset = offsets.get(date_iso, 0)
        base = datetime.combine(parse_deadline(date_iso), daily_start_time)
        start_dt = base + timedelta(minutes=offset)
        end_dt = start_dt + timedelta(minutes=int(item["duration_minutes"]))
        offsets[date_iso] = offset + int(item["duration_minutes"])
        assigned.append({**item, "start_datetime": start_dt, "end_datetime": end_dt})
    return assigned


def ics_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def extract_deadline_time(deadline_original: str) -> dt_time | None:
    """原文に明記された時刻だけを採用する。時刻がなければ推測しない。"""
    text = str(deadline_original or "")
    patterns = [
        r"(?<!\d)([01]?\d|2[0-3])[:：]([0-5]\d)(?!\d)",
        r"(?<!\d)([01]?\d|2[0-3])時(?:([0-5]?\d)分)?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2) or 0)
            return dt_time(hour, minute)
    return None


def build_deadline_ics(
    company_name: str,
    course_name: str,
    deadline_iso: str,
    deadline_original: str,
    deadline_type: str,
    source_url: str,
    source_reliability: str,
    confirmation_status: str,
    verified: bool,
) -> bytes:
    """Googleカレンダーへ登録する締切1件だけをICS化する。"""
    parsed_date = parse_deadline(deadline_iso)
    if parsed_date is None:
        raise ValueError("締切日を解析できません。")

    now_utc = datetime.now(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")
    deadline_time = extract_deadline_time(deadline_original)
    verification_label = "確認済み" if verified else "未確認・要確認"
    summary_prefix = "[締切]" if verified else "[未確認][締切]"
    summary = f"{summary_prefix} {company_name}｜{course_name}｜{deadline_type}"
    description_lines = [
        f"締切: {deadline_original or deadline_iso}",
        f"情報源の区分: {source_reliability}",
        f"検証状態: {verification_label}",
        f"ユーザー確認状態: {confirmation_status}",
        "注意: 未確認の場合、日付・年度・コース名を情報源で再確認する必要がある。",
        "本システムではZ3による準備日程はアプリ内だけに表示し、カレンダーには締切のみ登録する。",
    ]
    if source_url:
        description_lines.append(f"情報源: {source_url}")
    description = "\n".join(description_lines)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//JobHunt AI//Deadline Only//JA",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:{uuid4()}@jobhunt-ai.local",
        f"DTSTAMP:{now_utc}",
    ]

    if deadline_time is None:
        # 時刻が資料にない場合は、推測せず終日予定として登録する。
        next_date = parsed_date + timedelta(days=1)
        lines.extend([
            f"DTSTART;VALUE=DATE:{parsed_date.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{next_date.strftime('%Y%m%d')}",
        ])
        description += "\n時刻は情報源に明記されていないため、終日予定として登録した。"
    else:
        start_local = datetime.combine(parsed_date, deadline_time)
        end_local = start_local + timedelta(minutes=1)
        lines.extend([
            f"DTSTART;TZID=Asia/Tokyo:{start_local.strftime('%Y%m%dT%H%M%S')}",
            f"DTEND;TZID=Asia/Tokyo:{end_local.strftime('%Y%m%dT%H%M%S')}",
        ])

    lines.extend([
        f"SUMMARY:{ics_escape(summary)}",
        f"DESCRIPTION:{ics_escape(description)}",
        f"STATUS:{'CONFIRMED' if verified else 'TENTATIVE'}",
        f"X-JOBHUNT-VERIFICATION:{'CONFIRMED' if verified else 'UNCONFIRMED'}",
        "END:VEVENT",
        "END:VCALENDAR",
        "",
    ])
    return "\r\n".join(lines).encode("utf-8")


def build_google_calendar_url(
    company_name: str,
    course_name: str,
    deadline_iso: str,
    deadline_original: str,
    deadline_type: str,
    source_url: str,
) -> str:
    """確認済み締切のGoogleカレンダー予定作成URLを生成する。"""
    parsed_date = parse_deadline(deadline_iso)
    if parsed_date is None:
        raise ValueError("締切日を解析できません。")

    deadline_time = extract_deadline_time(deadline_original)
    if deadline_time is None:
        dates = (
            f"{parsed_date.strftime('%Y%m%d')}/"
            f"{(parsed_date + timedelta(days=1)).strftime('%Y%m%d')}"
        )
    else:
        start_local = datetime.combine(parsed_date, deadline_time)
        end_local = start_local + timedelta(minutes=1)
        dates = (
            f"{start_local.strftime('%Y%m%dT%H%M%S')}/"
            f"{end_local.strftime('%Y%m%dT%H%M%S')}"
        )

    details = [
        f"締切: {deadline_original or deadline_iso}",
        "確認状態: 確認済み",
    ]
    if source_url:
        details.append(f"情報源: {source_url}")
    query = urlencode({
        "action": "TEMPLATE",
        "text": f"[締切] {company_name}｜{course_name}｜{deadline_type}",
        "dates": dates,
        "details": "\n".join(details),
        "ctz": "Asia/Tokyo",
    })
    return f"https://calendar.google.com/calendar/render?{query}"

