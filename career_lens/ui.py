"""Streamlit views and top-level CareerLens application workflow."""

from .common import *
from .auth import *
from .storage import *
from .sources import *
from .ai_client import *
from .ai_client import _is_quota_exhausted_error, _is_retryable_gemini_error
from .research import *
from .ai_analysis import *
from .verification import *
from .scheduler import *


def access_password_matches(candidate: str, expected: str) -> bool:
    """Compare the shared password without leaking timing information."""
    if not expected:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def require_access_password() -> None:
    """Require a shared password only when APP_ACCESS_PASSWORD is configured."""
    expected = os.getenv("APP_ACCESS_PASSWORD", "").strip()
    if not expected or st.session_state.get("access_granted"):
        return

    st.title("就活情報検証システム")
    st.caption("共有テスト用のパスワードを入力してください．")
    with st.form("access_password_form"):
        candidate = st.text_input("共有パスワード", type="password")
        submitted = st.form_submit_button("ログイン")

    if submitted:
        if access_password_matches(candidate, expected):
            st.session_state["access_granted"] = True
            st.rerun()
        st.error("パスワードが違います．")
    st.stop()


def require_user_account() -> dict[str, Any]:
    """Restore an account or an isolated, non-persistent guest session."""
    current = st.session_state.get("current_user")
    if isinstance(current, dict) and current.get("user_id"):
        if current.get("is_guest") and is_guest_user_id(str(current["user_id"])):
            guest_store = st.session_state.setdefault("guest_session_store", {})
            set_current_user(str(current["user_id"]), guest_store)
            return current
        try:
            valid_user = get_user_by_id(str(current["user_id"]))
        except AuthStorageError as exc:
            st.error(str(exc))
            st.stop()
        if valid_user:
            st.session_state["current_user"] = valid_user
            set_current_user(str(valid_user["user_id"]))
            return valid_user
        st.session_state.pop("current_user", None)

    st.title("CareerLens")
    st.caption("ログインすると、自分専用の検索履歴と確認状態を保存できます。")
    if st.button(
        "ゲストとして利用",
        type="primary",
        use_container_width=True,
        key="guest_login_button",
    ):
        guest = create_guest_user()
        guest_store: dict[str, Any] = {}
        st.session_state["current_user"] = guest
        st.session_state["guest_session_store"] = guest_store
        set_current_user(str(guest["user_id"]), guest_store)
        st.rerun()
    st.caption(
        "ゲスト利用では検索・AI検証・Z3・ICSを試せますが、"
        "検索履歴や確認状態はブラウザセッション終了後に保持されません。"
    )
    st.divider()
    login_tab, register_tab = st.tabs(["ログイン", "アカウント作成"])

    with login_tab:
        with st.form("account_login_form"):
            username = st.text_input("メールアドレス", key="login_username")
            password = st.text_input(
                "パスワード", type="password", key="login_password"
            )
            login_submitted = st.form_submit_button(
                "ログイン", type="primary", use_container_width=True
            )
        if login_submitted:
            failed_attempts = int(st.session_state.get("login_failed_attempts", 0))
            locked_until = float(st.session_state.get("login_locked_until", 0.0))
            if time.time() < locked_until:
                remaining = max(1, int(locked_until - time.time()))
                st.error(f"試行回数が多いため、{remaining}秒後に再度お試しください。")
            else:
                try:
                    user = authenticate_user(username, password)
                except AuthStorageError as exc:
                    st.error(str(exc))
                    st.stop()
                if user:
                    st.session_state.pop("guest_session_store", None)
                    st.session_state["current_user"] = user
                    st.session_state["login_failed_attempts"] = 0
                    st.session_state.pop("login_locked_until", None)
                    set_current_user(str(user["user_id"]))
                    st.rerun()
                failed_attempts += 1
                st.session_state["login_failed_attempts"] = failed_attempts
                if failed_attempts >= 5:
                    st.session_state["login_locked_until"] = time.time() + 60
                    st.session_state["login_failed_attempts"] = 0
                st.error("メールアドレスまたはパスワードが違います。")

    with register_tab:
        with st.form("account_register_form"):
            new_username = st.text_input("メールアドレス", key="register_username")
            display_name = st.text_input("表示名", key="register_display_name")
            new_password = st.text_input(
                "パスワード", type="password",
                key="register_password",
            )
            password_confirmation = st.text_input(
                "パスワードを再入力", type="password",
                key="register_password_confirmation",
            )
            register_submitted = st.form_submit_button(
                "アカウントを作成", type="primary", use_container_width=True
            )
        if register_submitted:
            if new_password != password_confirmation:
                st.error("再入力したパスワードが一致しません。")
            else:
                try:
                    user = create_account(new_username, display_name, new_password)
                except (ValueError, AuthStorageError) as exc:
                    st.error(str(exc))
                else:
                    st.session_state.pop("guest_session_store", None)
                    st.session_state["current_user"] = user
                    set_current_user(str(user["user_id"]))
                    st.rerun()

    st.stop()


def render_account_controls(user: dict[str, Any]) -> None:
    is_guest = bool(user.get("is_guest"))
    with st.sidebar:
        if is_guest:
            st.caption("ゲストとして利用中")
            st.info("履歴・確認状態・日程履歴は永続保存されません。")
        else:
            st.caption(f"ログイン中：{user.get('display_name') or user.get('username')}")
        button_label = "ゲスト利用を終了" if is_guest else "ログアウト"
        if st.button(button_label, use_container_width=True):
            for key in (
                "current_user", "latest_analysis", "latest_schedule",
                "latest_search_performance", "deadline_confirmation_feedback",
                "guest_session_store",
            ):
                st.session_state.pop(key, None)
            set_current_user("legacy-local-user")
            st.rerun()


def inject_app_styles() -> None:
    """CareerLensの共通スタイルとスマートフォン向けレイアウトを適用する。"""
    st.markdown(
        """
        <style>
        :root {
            --cl-ink: #172033;
            --cl-muted: #5f6b7a;
            --cl-primary: #3156d3;
            --cl-success: #087a5b;
            --cl-border: #e3e8f0;
            --cl-surface: rgba(255, 255, 255, 0.96);
        }
        .stApp {
            background:
                radial-gradient(circle at 92% 2%, rgba(49, 86, 211, 0.10), transparent 28rem),
                #f7f9fc;
        }
        .block-container {
            max-width: 1180px;
            padding-top: 2.5rem;
            padding-bottom: 5rem;
        }
        .careerlens-hero {
            padding: 1rem 0 1.35rem;
            max-width: 820px;
        }
        .careerlens-kicker {
            color: var(--cl-primary);
            font-size: 0.78rem;
            font-weight: 800;
            letter-spacing: 0;
            margin-bottom: 0.65rem;
        }
        .careerlens-hero h1 {
            color: var(--cl-ink);
            font-size: clamp(2.15rem, 5vw, 4.15rem);
            line-height: 1.08;
            letter-spacing: 0;
            margin: 0;
        }
        .careerlens-hero p {
            color: var(--cl-muted);
            font-size: 1.05rem;
            line-height: 1.8;
            margin: 1rem 0 0;
            max-width: 680px;
        }
        .careerlens-hero .careerlens-subtitle {
            color: var(--cl-ink);
            font-size: 1.2rem;
            font-weight: 750;
            line-height: 1.65;
        }
        .careerlens-hero .careerlens-description {
            margin-top: 0.35rem;
        }
        div[data-testid="stForm"],
        .st-key-deadline_summary_card {
            background: var(--cl-surface);
            border: 1px solid var(--cl-border);
            border-radius: 1rem;
            box-shadow: 0 18px 50px rgba(23, 32, 51, 0.07);
        }
        .st-key-deadline_summary_card {
            border-left: 4px solid var(--cl-primary);
        }
        div[data-testid="stFormSubmitButton"] button,
        div[data-testid="stLinkButton"] a[kind="primary"] {
            min-height: 3rem;
            font-weight: 750;
        }
        div[data-testid="stMetricValue"] {
            color: var(--cl-ink);
            letter-spacing: -0.035em;
        }
        div[data-testid="stStatusWidget"] {
            border-radius: 1rem;
            box-shadow: 0 14px 40px rgba(23, 32, 51, 0.06);
        }
        @media (max-width: 640px) {
            .block-container {
                padding: 1.2rem 1rem 4rem;
            }
            .careerlens-hero {
                padding-top: 0.35rem;
            }
            .careerlens-hero h1 {
                font-size: 2.35rem;
                letter-spacing: -0.04em;
            }
            .careerlens-hero p {
                font-size: 0.96rem;
                line-height: 1.7;
            }
            div[data-testid="stHorizontalBlock"] {
                flex-wrap: wrap;
                gap: 0.75rem;
            }
            div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
                flex: 1 1 100% !important;
                width: 100% !important;
                min-width: 0 !important;
            }
            div[data-testid="stFormSubmitButton"] button,
            div[data-testid="stLinkButton"] a,
            div[data-testid="stDownloadButton"] button {
                min-height: 3.25rem;
                width: 100%;
            }
            div[data-testid="stDataFrame"],
            div[data-testid="stDataEditor"] {
                overflow-x: auto;
            }
        }
        @media (prefers-reduced-motion: reduce) {
            *, *::before, *::after {
                scroll-behavior: auto !important;
                transition-duration: 0.01ms !important;
                animation-duration: 0.01ms !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def display_progressive_candidates(candidates: list[dict[str, Any]]) -> None:
    if not candidates:
        st.caption("現在までに締切候補は検出されていません。")
        return
    rows = []
    for item in candidates[:30]:
        rows.append({
            "コース": item.get("course_name") or "未特定",
            "締切": item.get("deadline") or item.get("deadline_original") or "要確認",
            "種別": item.get("deadline_type") or "",
            "情報源": item.get("source_type") or "",
            "検査": (
                "AI+原文+年度"
                if item.get("validation_level") == "ai_source_supported"
                else (
                    "AI抽出・要確認"
                    if item.get("validation_level") == "ai_source_needs_confirmation"
                    else (
                        "条件不一致"
                        if item.get("validation_level") == "ai_source_rejected"
                        else "Python候補"
                    )
                )
            ),
            "根拠": str(item.get("evidence") or "")[:100],
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)


def display_search_performance(meta: dict[str, Any] | None) -> None:
    if not meta:
        return
    strategy_labels = {
        "fast": "高速",
        "standard": "標準",
        "comprehensive": "網羅",
    }
    cache_text = "検索キャッシュ利用" if meta.get("search_cache_hit") else "新規検索"
    social_text = "SNS検索あり" if meta.get("social_searched") else "SNS検索省略"
    st.info(
        " / ".join(
            [
                f"方式: {strategy_labels.get(str(meta.get('strategy')), meta.get('strategy', ''))}",
                cache_text,
                social_text,
                f"検索文: {int(meta.get('query_count', 0))}件",
                f"検索: {float(meta.get('search_seconds', 0)):.2f}秒",
                f"本文取得: {float(meta.get('fetch_seconds', 0)):.2f}秒",
                f"本文キャッシュ: {int(meta.get('page_cache_hits', 0))}件",
                f"取得失敗: {int(meta.get('failed_pages', 0))}件",
                f"逐次AI検査: {int(meta.get('progressive_ai_pages', 0))}件",
                f"逐次候補: {int(meta.get('progressive_candidate_count', 0))}件",
                f"異なる締切: {int(meta.get('unique_deadline_count', 0))}件",
            ]
        )
    )


def render_scheduler() -> None:
    latest = st.session_state.get("latest_analysis")
    if not isinstance(latest, dict):
        return
    ai_result = latest.get("ai_result")
    verification = latest.get("verification")
    if not isinstance(ai_result, dict) or not isinstance(verification, VerificationResult):
        return

    options = collect_deadline_options(
        ai_result,
        verification,
        latest.get("progressive_candidates") or [],
    )
    st.divider()
    st.header("応募準備スケジュールの自動作成")
    st.caption(
        "Geminiが準備作業を分解し、Pythonが所要時間を検査した後、"
        "Z3が締切・曜日・除外日・1日上限時間の制約を満たす日程を探索します。"
    )
    if not options:
        st.info("日付として確認できる締切がないため、スケジュールを作成できません。")
        return

    labels = []
    for item in options:
        if item.get("machine_verified"):
            status_label = "機械確認済み"
        elif item.get("confirmation_status") == "確認済み":
            status_label = "本人確認済み"
        elif item.get("social_source"):
            status_label = "SNS確認待ち"
        else:
            status_label = "要確認"
        labels.append(
            f"{item['course_name']}｜{item['deadline_type']} {item['deadline']}｜{status_label}"
        )

    with st.form("schedule_form"):
        selected_label = st.selectbox("予定を作成する締切", labels)
        start_date_value = st.date_input("作業開始日", value=date.today())
        weekdays = st.multiselect(
            "作業可能な曜日",
            list(WEEKDAY_LABELS.keys()),
            default=list(WEEKDAY_LABELS.keys()),
        )
        col1, col2 = st.columns(2)
        with col1:
            max_daily_minutes = st.select_slider(
                "1日の就活作業上限",
                options=[60, 90, 120, 150, 180, 240],
                value=120,
                format_func=lambda value: f"{value}分",
            )
        with col2:
            daily_start_time = st.time_input("予定の開始時刻", value=dt_time(19, 0))
        blocked_text = st.text_input(
            "作業できない日（任意）",
            placeholder="例: 2026-07-20, 2026-07-24",
        )
        use_ai_tasks = st.checkbox("Geminiで作業を分解する", value=True)
        schedule_submitted = st.form_submit_button("AI作業分解・Z3日程作成を実行")

    if schedule_submitted:
        selected = options[labels.index(selected_label)]
        deadline_date = parse_deadline(selected["deadline"])
        if deadline_date is None:
            st.error("締切日を解析できません。")
            return
        if selected.get("social_source") and selected.get("confirmation_status") != "確認済み":
            st.error("SNS由来の締切は、抽出結果画面で『確認済み』に変更するまで予定作成に利用できません。")
            return
        if not selected["verified"]:
            st.warning("選択した締切は自動検証が完了していません。情報源を確認してから利用してください。")
        try:
            blocked_dates = parse_blocked_dates(blocked_text)
            if use_ai_tasks:
                with st.spinner("Geminiが応募準備を作業へ分解しています..."):
                    tasks, task_summary = ask_ai_for_tasks(
                        str(ai_result.get("company_name") or latest.get("company") or "企業"),
                        selected["course_name"],
                        str(ai_result.get("recruitment_type") or "その他"),
                        selected["deadline"],
                    )
            else:
                tasks = fallback_tasks(str(ai_result.get("recruitment_type") or "その他"))
                task_summary = "標準テンプレートから作業を作成した。"

            with st.spinner("Z3が制約を満たす予定を探索しています..."):
                plan, solver_info = schedule_tasks_with_z3(
                    tasks=tasks,
                    start_date=start_date_value,
                    deadline_date=deadline_date,
                    allowed_weekdays={WEEKDAY_LABELS[label] for label in weekdays},
                    blocked_dates=blocked_dates,
                    max_daily_minutes=int(max_daily_minutes),
                )
            timed_plan = assign_event_times(plan, daily_start_time)
            constraints = {
                "start_date": start_date_value.isoformat(),
                "deadline": selected["deadline"],
                "allowed_weekdays": weekdays,
                "blocked_dates": sorted(d.isoformat() for d in blocked_dates),
                "max_daily_minutes": int(max_daily_minutes),
                "daily_start_time": daily_start_time.strftime("%H:%M"),
            }
            save_schedule_history(
                company_name=str(ai_result.get("company_name") or latest.get("company") or "企業"),
                course_name=selected["course_name"],
                deadline=selected["deadline"],
                constraints=constraints,
                tasks=tasks,
                plan=plan,
                solver_status=str(solver_info["status"]),
            )
            st.session_state["latest_schedule"] = {
                "selected": selected,
                "tasks": tasks,
                "task_summary": task_summary,
                "plan": plan,
                "timed_plan": timed_plan,
                "solver_info": solver_info,
                "constraints": constraints,
            }
        except Exception as exc:
            st.error(str(exc))

    schedule = st.session_state.get("latest_schedule")
    if not isinstance(schedule, dict):
        return
    selected = schedule["selected"]
    tasks = schedule["tasks"]
    timed_plan = schedule["timed_plan"]
    solver_info = schedule["solver_info"]

    st.subheader("作業分解結果")
    st.caption(str(schedule.get("task_summary") or ""))
    st.dataframe(
        [
            {
                "順序": task["order"],
                "作業": task["task_name"],
                "内容": task["description"],
                "所要時間": f"{task['duration_minutes']}分",
            }
            for task in tasks
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Z3が作成した実行可能な予定")
    st.dataframe(
        [
            {
                "日付": item["scheduled_date"],
                "開始": item["start_datetime"].strftime("%H:%M"),
                "終了": item["end_datetime"].strftime("%H:%M"),
                "作業": item["task_name"],
                "所要時間": f"{item['duration_minutes']}分",
            }
            for item in timed_plan
        ],
        use_container_width=True,
        hide_index=True,
    )
    st.success(
        f"Z3判定: {solver_info['status']}。{solver_info['constraint_count']}個の制約を満たす予定を作成しました。"
    )

    st.info(
        "このZ3日程は実行可能性を確認するための提案です。"
        "Googleカレンダーへの追加は、調査結果上部の締切カードから行えます。"
    )

    with st.expander("Z3へ与えた制約と探索結果"):
        st.write("- 全作業を締切より前に配置する")
        st.write("- 選択した曜日と除外日だけを作業可能日とする")
        st.write("- 1日の合計作業時間を上限以内にする")
        st.write("- 作業の依存順序を維持する")
        st.write("- 1日への作業集中を抑え、可能な範囲で早く完了する")
        st.json(schedule["constraints"])
        st.json(solver_info)


def display_sources(
    source_records: list[dict[str, Any]], company_name: str = ""
) -> None:
    st.subheader("検索・取得した情報源")
    displayed_official_domains: set[str] = set()
    for record in source_records:
        label = str(record.get("source_type", "判定不能"))
        if label == "企業公式候補":
            label = (
                "企業公式（利用者・本文確認済み）"
                if record.get("official_source_verified")
                else "公式性未確認"
            )
        if record.get("page_cache_hit") and record.get("fetch_success"):
            fetched = "本文キャッシュ利用"
        elif record.get("page_cache_hit"):
            fetched = "取得失敗キャッシュ・検索概要利用"
        else:
            fetched = "本文取得済み" if record.get("fetch_success") else "検索概要のみ"
        st.markdown(
            f"**{record.get('source_id')}｜{record.get('title') or 'タイトルなし'}**  "
            f"\n[{record.get('url')}]({record.get('url')})  "
            f"\n判定: {label}／{fetched}／検索スコア: {record.get('score')}"
        )
        snippet = str(record.get("snippet", ""))
        if snippet:
            st.caption(snippet[:300])
        record_host = host_of(str(record.get("url") or ""))
        if (
            company_name
            and record_host not in displayed_official_domains
            and str(record.get("source_type") or "") == "企業公式候補"
        ):
            displayed_official_domains.add(record_host)
            options = ["未確認", "公式と確認", "非公式と確認"]
            current = get_official_domain_confirmation(
                company_name, str(record.get("url") or "")
            )
            if current not in options:
                current = "未確認"
            selected = st.selectbox(
                f"{record_host} の公式性",
                options,
                index=options.index(current),
                key=(
                    f"official_domain_{get_current_user_id()}_"
                    f"{registry_company_key(company_name)}_{record_host}"
                ),
                help="元ページを開き、その企業が運営するドメインかを確認してください。",
            )
            if selected != current:
                set_official_domain_confirmation(
                    company_name, str(record.get("url") or ""), selected
                )
                refresh_official_source_flags(company_name, source_records)
                st.rerun()


def display_research_details(
    source_records: list[dict[str, Any]],
    performance: dict[str, Any] | None = None,
    progressive_candidates: list[dict[str, Any]] | None = None,
    company_name: str = "",
) -> None:
    """情報源は件数だけ要約し、必要な場合に全詳細を開けるようにする。"""
    fetched_count = sum(bool(record.get("fetch_success")) for record in source_records)
    official_count = sum(
        bool(record.get("official_source_verified")) for record in source_records
    )
    summary_only_count = len(source_records) - fetched_count
    st.caption(
        f"情報源 {len(source_records)}件 ｜ 公式確認済み {official_count}件 ｜ "
        f"本文取得 {fetched_count}件 ｜ 検索概要のみ {summary_only_count}件"
    )
    with st.expander(f"調査の詳細を見る（情報源{len(source_records)}件）"):
        display_search_performance(performance)
        display_sources(source_records, company_name=company_name)
        if progressive_candidates:
            st.subheader("逐次検査で検出した締切候補")
            display_progressive_candidates(progressive_candidates)


def collect_social_deadline_items(ai_result: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for course_name, item in iter_course_deadlines(ai_result):
        source_url = str(item.get("source_url") or "")
        if not (is_social_reliability(item.get("source_reliability")) or source_type(source_url) == "SNS"):
            continue
        key = (course_name, str(item.get("deadline") or ""), source_url)
        if key in seen:
            continue
        seen.add(key)
        items.append({"course_name": course_name, **item})

    if not items and is_social_reliability(ai_result.get("source_reliability")):
        items.append({
            "course_name": str(ai_result.get("recruitment_type") or "選考"),
            "deadline": ai_result.get("deadline"),
            "deadline_original": ai_result.get("deadline_original"),
            "deadline_type": ai_result.get("deadline_type"),
            "source_url": ai_result.get("source_url"),
            "evidence": ai_result.get("evidence"),
            "source_reliability": ai_result.get("source_reliability"),
            "deadline_status": ai_result.get("deadline_status"),
        })
    return items


def render_social_confirmations(ai_result: dict[str, Any]) -> None:
    items = collect_social_deadline_items(ai_result)
    if not items:
        return

    company_name = str(
        ai_result.get("_company_input") or ai_result.get("company_name") or "企業名不明"
    )
    st.subheader("SNS情報の確認")
    st.warning(
        "SNSから取得した締切は速報情報として表示しています。投稿元と対象年度を確認し、"
        "確認状態を変更するまではZ3予定作成には使用しません。"
        "ICSには未確認表示付きで追加できます。"
    )
    statuses = ["未確認", "確認済み", "誤情報として除外"]
    for index, item in enumerate(items, start=1):
        course_name = str(item.get("course_name") or "コース名不明")
        deadline_iso = str(item.get("deadline") or "")
        source_url = str(item.get("source_url") or "")
        current = get_deadline_confirmation(company_name, course_name, deadline_iso, source_url)
        if current not in statuses:
            current = "未確認"
        with st.container(border=True):
            st.markdown(
                f"**{index}. {course_name}｜{item.get('deadline_type') or '応募締切'} "
                f"{deadline_iso or '日付不明'}**"
            )
            st.write("**根拠文**", item.get("evidence") or "確認できず")
            if source_url.startswith(("http://", "https://")):
                st.markdown(f"**投稿・ページ:** [{source_url}]({source_url})")
            selected = st.selectbox(
                "確認状態",
                statuses,
                index=statuses.index(current),
                key=(
                    f"sns_confirmation_{get_current_user_id()}_"
                    f"{social_confirmation_key(company_name, course_name, deadline_iso, source_url)}"
                ),
            )
            if selected != current:
                set_deadline_confirmation(
                    company_name, course_name, deadline_iso, source_url, selected
                )
                st.session_state.pop("latest_schedule", None)
                st.rerun()


def _candidate_identity(
    company_name: str, course_name: str, deadline: str, source_url: str
) -> str:
    raw = "|".join([company_name, course_name, deadline, source_url])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def collect_confirmation_candidates(
    company_name: str,
    ai_result: dict[str, Any],
    verification: VerificationResult,
    progressive_candidates: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Collect final and page-level candidates that a user can verify manually."""
    check_map = {
        (str(item.get("course_name")), str(item.get("deadline")), str(item.get("source_url"))): item
        for item in verification.course_deadline_checks
    }
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}

    for course_name, item in iter_course_deadlines(ai_result):
        deadline_iso = str(item.get("deadline") or "")
        if parse_deadline(deadline_iso) is None:
            continue
        source_url = str(item.get("source_url") or "")
        check = check_map.get((course_name, deadline_iso, source_url), {})
        machine_label = "機械確認済み" if check.get("verified") else "機械確認できず"
        if not check.get("verified"):
            if check.get("source_target_year_status") == "no":
                machine_label = "対象年度が不一致"
            elif check.get("ai_review_accepted") is False:
                machine_label = "AI再検証で要確認"
            elif check.get("deadline_context_match") is False:
                machine_label = "締切文脈を確認できず"
        confirmation_status = get_deadline_confirmation(
            company_name, course_name, deadline_iso, source_url
        )
        community = get_community_deadline_consensus(
            company_name,
            int(ai_result.get("target_year") or 0),
            str(ai_result.get("recruitment_type") or ""),
            course_name,
            deadline_iso,
            source_url,
        )
        key = (course_name, deadline_iso, normalize_url(source_url))
        merged[key] = {
            "candidate_id": _candidate_identity(
                company_name, course_name, deadline_iso, source_url
            ),
            "course_name": course_name,
            "deadline": deadline_iso,
            "deadline_type": str(item.get("deadline_type") or "応募締切"),
            "source_url": source_url,
            "evidence": str(item.get("evidence") or ""),
            "machine_label": machine_label,
            "confirmation_status": confirmation_status,
            "community_confirmed_count": int(community.get("confirmed_count") or 0),
            "community_rejected_count": int(community.get("rejected_count") or 0),
            "source_kind": str(check.get("source_type") or item.get("source_reliability") or ""),
        }

    for item in progressive_candidates or []:
        deadline_iso = str(item.get("deadline") or "")
        if parse_deadline(deadline_iso) is None:
            continue
        course_name = str(item.get("course_name") or "コース未特定")
        source_url = str(item.get("source_url") or "")
        key = (course_name, deadline_iso, normalize_url(source_url))
        if key in merged:
            continue
        validation_level = str(item.get("validation_level") or "")
        machine_label = {
            "ai_source_supported": "ページ単位AI・原文確認済み",
            "ai_source_needs_confirmation": "AI抽出・要確認",
            "ai_source_rejected": "機械判定で条件不一致",
            "python_hint": "Python抽出・要確認",
        }.get(validation_level, "機械確認できず")
        confirmation_status = get_deadline_confirmation(
            company_name, course_name, deadline_iso, source_url
        )
        community = get_community_deadline_consensus(
            company_name,
            int(ai_result.get("target_year") or 0),
            str(ai_result.get("recruitment_type") or ""),
            course_name,
            deadline_iso,
            source_url,
        )
        merged[key] = {
            "candidate_id": _candidate_identity(
                company_name, course_name, deadline_iso, source_url
            ),
            "course_name": course_name,
            "deadline": deadline_iso,
            "deadline_type": str(item.get("deadline_type") or "応募締切"),
            "source_url": source_url,
            "evidence": str(item.get("evidence") or ""),
            "machine_label": machine_label,
            "confirmation_status": confirmation_status,
            "community_confirmed_count": int(community.get("confirmed_count") or 0),
            "community_rejected_count": int(community.get("rejected_count") or 0),
            "source_kind": str(item.get("source_type") or item.get("source_reliability") or ""),
        }

    return sorted(
        merged.values(),
        key=lambda item: (item["deadline"], item["course_name"], item["source_url"]),
    )


def render_candidate_confirmation_cards(
    ai_result: dict[str, Any],
    verification: VerificationResult,
    progressive_candidates: list[dict[str, Any]] | None = None,
) -> None:
    company_name = str(
        ai_result.get("_company_input") or ai_result.get("company_name") or "企業名不明"
    )
    candidates = collect_confirmation_candidates(
        company_name, ai_result, verification, progressive_candidates
    )
    if not candidates:
        return

    unconfirmed_count = sum(
        item["confirmation_status"] == "未確認" for item in candidates
    )
    confirmed_count = sum(
        item["confirmation_status"] == "確認済み" for item in candidates
    )
    feedback = st.session_state.pop("deadline_confirmation_feedback", None)
    if feedback:
        st.success(str(feedback))

    st.subheader("締切候補を確認")
    st.caption(
        "自動判定と、あなた自身の確認状態は別に表示しています。"
        "要確認の候補は情報源を開き、企業名・年度・コース・締切を確認してから、"
        "プルダウンを『確認済み』へ変更してください。"
    )

    expander_label = (
        f"確認状態を変更する（未確認 {unconfirmed_count}件／"
        f"確認済み {confirmed_count}件）"
    )
    statuses = ["未確認", "確認済み", "誤情報として除外"]
    with st.expander(expander_label, expanded=unconfirmed_count > 0):
        for candidate in candidates:
            status = str(candidate["confirmation_status"] or "未確認")
            if status not in statuses:
                status = "未確認"
            with st.container(
                border=True,
                key=(
                    f"confirmation_card_{get_current_user_id()}_"
                    f"{candidate['candidate_id']}"
                ),
            ):
                title_col, badge_col = st.columns(
                    [2.3, 1],
                    vertical_alignment="center",
                )
                with title_col:
                    st.markdown(
                        f"**{candidate['course_name']}｜"
                        f"{candidate['deadline_type']}**"
                    )
                    st.markdown(f"### {candidate['deadline']}")
                with badge_col:
                    if status == "確認済み":
                        st.success("あなたの確認：確認済み")
                    elif status == "誤情報として除外":
                        st.error("あなたの確認：除外")
                    else:
                        st.warning("あなたの確認：未確認")

                st.caption(f"自動判定：{candidate['machine_label']}")
                community_confirmed = int(candidate.get("community_confirmed_count") or 0)
                community_rejected = int(candidate.get("community_rejected_count") or 0)
                if community_confirmed or community_rejected:
                    st.caption(
                        f"匿名の共同確認：確認済み {community_confirmed}人／"
                        f"誤情報判定 {community_rejected}人（補助情報）"
                    )
                if candidate["evidence"]:
                    st.write("**根拠**", candidate["evidence"][:300])

                action_col, select_col = st.columns([1, 1.35])
                with action_col:
                    source_url = str(candidate["source_url"] or "")
                    if source_url.startswith(("http://", "https://")):
                        st.link_button(
                            "情報源を確認する →",
                            source_url,
                            use_container_width=True,
                        )
                    else:
                        st.caption("確認できる外部リンクはありません。")
                with select_col:
                    selected_status = st.selectbox(
                        "あなたの確認状態（変更できます）",
                        statuses,
                        index=statuses.index(status),
                        key=(
                            f"confirmation_status_{get_current_user_id()}_"
                            f"{candidate['candidate_id']}"
                        ),
                    )

                if selected_status != status:
                    set_deadline_confirmation(
                        company_name,
                        candidate["course_name"],
                        candidate["deadline"],
                        candidate["source_url"],
                        selected_status,
                    )
                    if selected_status == "確認済み":
                        message = (
                            "確認済みに変更しました。Googleカレンダーの候補へ反映します。"
                        )
                    elif selected_status == "誤情報として除外":
                        message = "誤情報として除外しました。プルダウンから元に戻せます。"
                    else:
                        message = "未確認に戻しました。"
                    st.session_state["deadline_confirmation_feedback"] = message
                    st.session_state.pop("latest_schedule", None)
                    st.rerun()


def _display_model_review_block(review: dict[str, Any], heading: str, model_key: str) -> None:
    st.write(f"**{heading}**")
    verdict = str(review.get("overall_verdict") or "確認できず")
    verdict_labels = {
        "approved": "承認",
        "approved_with_warnings": "警告付き承認",
        "rejected": "要再確認",
    }
    if verdict == "approved":
        st.success(f"総合判定: {verdict_labels[verdict]}")
    elif verdict == "approved_with_warnings":
        st.warning(f"総合判定: {verdict_labels[verdict]}")
    else:
        st.error(f"総合判定: {verdict_labels.get(verdict, verdict)}")
    if review.get("summary"):
        st.write(review.get("summary"))

    core_rows = []
    for key, label in (
        ("company_name_review", "企業名"),
        ("target_year_review", "対象年度"),
        ("recruitment_type_review", "募集区分"),
    ):
        item = review.get(key) or {}
        if isinstance(item, dict):
            core_rows.append({
                "項目": label,
                "判定": item.get("verdict") or "確認できず",
                "訂正候補": item.get("corrected_value"),
                "理由": item.get("reason") or "",
            })
    if core_rows:
        st.dataframe(core_rows, use_container_width=True, hide_index=True)

    rows = []
    for item in review.get("deadline_reviews") or []:
        if not isinstance(item, dict):
            continue
        rows.append({
            "コース": item.get("course_name"),
            "抽出締切": item.get("deadline"),
            "判定": item.get("verdict"),
            "訂正候補": item.get("corrected_deadline"),
            "締切区分訂正": item.get("corrected_deadline_type"),
            "理由": item.get("reason"),
            "情報源": item.get("source_url"),
        })
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)

    missing = review.get("missing_deadlines") or []
    if missing:
        st.write("**見落とし候補**")
        st.dataframe(missing, use_container_width=True, hide_index=True)

    for warning in review.get("warnings") or []:
        st.warning(str(warning))


def display_ai_review(ai_result: dict[str, Any]) -> None:
    review = ai_result.get("_ai_review")
    judge = ai_result.get("_ai_judge")
    if not isinstance(review, dict):
        return

    st.subheader("3モデル合議による比較・再検証")
    if review.get("overall_verdict") == "disabled":
        reason = str(
            ai_result.get("_review_unavailable_reason")
            or "この実行では複数AIモデルによる再検証を無効にしています。"
        )
        st.warning(reason)
        return

    extractor_model = ai_result.get("_extractor_model") or MODEL
    extractor_fallback = "（混雑時フォールバック）" if ai_result.get("_extractor_fallback") else ""
    reviewer_model = review.get("_review_model_used") or REVIEW_MODEL
    reviewer_fallback = "（同一モデルへフォールバック）" if review.get("_review_fallback") else ""
    if isinstance(judge, dict):
        judge_model = judge.get("_judge_model_used") or JUDGE_MODEL
        judge_fallback = "（Flashへフォールバック）" if judge.get("_judge_fallback") else ""
        st.caption(
            f"抽出: {extractor_model}{extractor_fallback} ／ 独立検証: {reviewer_model}{reviewer_fallback} ／ "
            f"最終裁定: {judge_model}{judge_fallback}。最終判定には裁定モデルの結果を使用します。"
        )
    else:
        skipped_reason = str(ai_result.get("_judge_skipped_reason") or "Pro裁定はこの実行では無効です。")
        st.caption(
            f"抽出: {extractor_model}{extractor_fallback} ／ 独立検証: {reviewer_model}{reviewer_fallback}。"
            f"{skipped_reason}"
        )

    elapsed_parts = []
    if ai_result.get("_extractor_elapsed_seconds") is not None:
        retry_note = f"・再試行{ai_result.get('_extractor_retry_count')}回" if ai_result.get("_extractor_retry_count") else ""
        elapsed_parts.append(f"抽出 {ai_result.get('_extractor_elapsed_seconds')}秒{retry_note}")
    if review.get("_review_elapsed_seconds") is not None:
        elapsed_parts.append(f"独立検証 {review.get('_review_elapsed_seconds')}秒")
    if isinstance(judge, dict) and judge.get("_judge_elapsed_seconds") is not None:
        elapsed_parts.append(f"Pro裁定 {judge.get('_judge_elapsed_seconds')}秒")
    if elapsed_parts:
        st.caption("AI処理時間: " + " ／ ".join(elapsed_parts))
    input_parts = []
    if ai_result.get("_extractor_input_chars") is not None:
        input_parts.append(f"抽出 {int(ai_result.get('_extractor_input_chars')):,}文字")
    if review.get("_review_input_chars") is not None:
        input_parts.append(f"独立検証 {int(review.get('_review_input_chars')):,}文字")
    if isinstance(judge, dict) and judge.get("_judge_input_chars") is not None:
        input_parts.append(f"Pro裁定 {int(judge.get('_judge_input_chars')):,}文字")
    if input_parts:
        st.caption("AI入力規模: " + " ／ ".join(input_parts))

    with st.expander("第2モデルの独立検証結果", expanded=not isinstance(judge, dict)):
        _display_model_review_block(review, "独立検証AI", "_review_model_used")

    if isinstance(judge, dict):
        with st.expander("Proモデルの最終裁定結果", expanded=True):
            _display_model_review_block(judge, "最終裁定AI", "_judge_model_used")
            st.caption("裁定AIがunsupported・conflict・not_verifiableとした項目は要確認へ降格します。")


def render_deadline_summary(
    ai_result: dict[str, Any],
    verification: VerificationResult,
    progressive_candidates: list[dict[str, Any]] | None = None,
) -> None:
    """最優先の締切とカレンダー操作を結果の先頭に表示する。"""
    options = collect_deadline_options(
        ai_result,
        verification,
        progressive_candidates or [],
    )
    if not options:
        return

    company_name = str(
        ai_result.get("company_name")
        or ai_result.get("_company_input")
        or "企業"
    )
    labels = []
    for item in options:
        if item.get("machine_verified"):
            status_label = "機械確認済み"
        elif item.get("confirmation_status") == "確認済み":
            status_label = "本人確認済み"
        else:
            status_label = "要確認"
        labels.append(
            f"{item['course_name']}｜{item['deadline_type']} "
            f"{item['deadline']}｜{status_label}"
        )
    default_index = next(
        (index for index, item in enumerate(options) if item.get("verified")),
        0,
    )

    with st.container(border=True, key="deadline_summary_card"):
        st.subheader(company_name)
        calendar_label = st.selectbox(
            "Googleカレンダーへ追加する締切",
            labels,
            index=default_index,
            key="deadline_summary_calendar_select",
        )
        calendar_item = options[labels.index(calendar_label)]
        calendar_verified = bool(calendar_item.get("verified"))
        parsed_date = parse_deadline(str(calendar_item["deadline"]))
        if parsed_date is None:
            return
        original = str(
            calendar_item.get("deadline_original") or calendar_item["deadline"]
        )
        deadline_time = extract_deadline_time(original)
        date_label = f"{parsed_date.year}年{parsed_date.month}月{parsed_date.day}日"
        time_label = deadline_time.strftime("%H:%M") if deadline_time else "終日"
        headline = "確認済み締切" if calendar_verified else "締切候補"

        heading_col, status_col = st.columns([2.2, 1], vertical_alignment="center")
        with heading_col:
            st.caption(
                f"{calendar_item['course_name']} ・ "
                f"{calendar_item.get('deadline_type') or '応募締切'}"
            )
        with status_col:
            if calendar_verified:
                st.success("確認済み")
            else:
                st.warning("要確認")

        deadline_col, action_col = st.columns(
            [1.35, 1],
            vertical_alignment="center",
        )
        with deadline_col:
            st.metric(headline, date_label, time_label, delta_color="off")
            if deadline_time is None:
                st.caption("時刻の記載がないため、終日予定として扱います。")
        with action_col:
            deadline_ics = build_deadline_ics(
                company_name=company_name,
                course_name=str(calendar_item["course_name"]),
                deadline_iso=str(calendar_item["deadline"]),
                deadline_original=original,
                deadline_type=str(calendar_item.get("deadline_type") or "応募締切"),
                source_url=str(calendar_item.get("source_url") or ""),
                source_reliability=str(calendar_item.get("source_reliability") or "other"),
                confirmation_status=str(calendar_item.get("confirmation_status") or "未確認"),
                verified=calendar_verified,
            )
            if calendar_verified:
                google_calendar_url = build_google_calendar_url(
                    company_name=company_name,
                    course_name=str(calendar_item["course_name"]),
                    deadline_iso=str(calendar_item["deadline"]),
                    deadline_original=original,
                    deadline_type=str(calendar_item.get("deadline_type") or "応募締切"),
                    source_url=str(calendar_item.get("source_url") or ""),
                )
                st.link_button(
                    "Googleカレンダーで確認・追加",
                    google_calendar_url,
                    type="primary",
                    use_container_width=True,
                )
                st.caption("Googleカレンダーが開きます。内容を確認して「保存」を押してください。")
            else:
                source_url = str(calendar_item.get("source_url") or "")
                if source_url.startswith(("http://", "https://")):
                    st.link_button(
                        "情報源を確認する",
                        source_url,
                        type="primary",
                        use_container_width=True,
                    )
                st.caption("確認済みに変更すると、Googleカレンダーへの追加を利用できます。")

            suffix = "confirmed" if calendar_verified else "UNCONFIRMED"
            filename_company = re.sub(
                r"[^0-9A-Za-z一-龥ぁ-んァ-ヶ_-]",
                "_",
                company_name,
            )
            st.download_button(
                "ICSをダウンロード",
                data=deadline_ics,
                file_name=(
                    f"{filename_company}_{calendar_item['deadline']}_"
                    f"{suffix}_deadline.ics"
                ),
                mime="text/calendar",
                use_container_width=True,
            )


def display_result(
    ai_result: dict[str, Any],
    verification: VerificationResult,
    source_records: list[dict[str, Any]],
    progressive_candidates: list[dict[str, Any]] | None = None,
) -> None:
    render_deadline_summary(ai_result, verification, progressive_candidates)
    render_candidate_confirmation_cards(
        ai_result,
        verification,
        progressive_candidates,
    )
    st.subheader("調査結果の詳細")

    col1, col2 = st.columns(2)
    with col1:
        st.write("**企業名**", ai_result.get("company_name") or "確認できず")
        st.write("**業界**", "、".join(ai_result.get("industry") or []) or "確認できず")
        st.write("**対象年度**", ai_result.get("target_year") or "確認できず")
        st.write("**募集区分**", ai_result.get("recruitment_type") or "確認できず")
    with col2:
        deadline_label = "最も早い締切" if ai_result.get("recruitment_type") == "インターン" else "締切"
        st.write(f"**{deadline_label}**", ai_result.get("deadline") or "確認できず")
        st.write("**締切の種類**", ai_result.get("deadline_type") or "確認できず")
        verification_label = (
            "締切未確認"
            if verification.deadline_count == 0 and not ai_result.get("deadline")
            else ("合格" if verification.passed else "要確認")
        )
        st.write("**自動検証**", verification_label)
        st.write("**情報源信頼度**", ai_result.get("source_reliability") or "確認できず")
        st.write("**締切ステータス**", ai_result.get("deadline_status") or "確認できず")

    st.write("**事業内容**")
    st.write(ai_result.get("business_summary") or "確認できず")

    display_ai_review(ai_result)

    courses = ai_result.get("courses") or []
    if isinstance(courses, list) and courses:
        st.subheader("コース別の応募締切")
        st.caption(
            "締切は企業を最上位単位として蓄積し、対象年度・募集区分を分離して管理します。"
            "今回の検索で取得できなかった過去の締切も削除せず、『今回未取得』として保持します。"
        )
        rows: list[dict[str, Any]] = []
        check_map = {
            (str(item.get("course_name")), str(item.get("deadline")), str(item.get("source_url"))): item
            for item in verification.course_deadline_checks
        }
        for course in courses:
            if not isinstance(course, dict):
                continue
            course_name = str(course.get("course_name") or "コース名不明")
            deadlines = course.get("deadlines") or []
            if not deadlines:
                rows.append({
                    "コース名": course_name,
                    "締切": "確認できず",
                    "締切区分": "確認できず",
                    "情報源": "確認できず",
                    "検証": "要確認",
                    "AI再検証": "対象なし",
                })
                continue
            for item in deadlines:
                if not isinstance(item, dict):
                    continue
                key = (course_name, str(item.get("deadline")), str(item.get("source_url")))
                check = check_map.get(key, {})
                confirmation_status = str(check.get("confirmation_status") or "対象外")
                if confirmation_status == "誤情報として除外":
                    validation_label = "本人が誤情報として除外"
                elif confirmation_status == "確認済み" and not check.get("verified"):
                    validation_label = "本人確認済み"
                elif check.get("social_source"):
                    validation_label = (
                        "ユーザー確認済み" if confirmation_status == "確認済み"
                        else ("除外" if confirmation_status == "誤情報として除外" else "SNS確認待ち")
                    )
                else:
                    if check.get("historical_verified"):
                        validation_label = "旧基準では検証済み・再確認必要"
                    else:
                        validation_label = "確認済み" if check.get("verified") else "要確認"
                rows.append({
                    "コース名": course_name,
                    "締切": item.get("deadline") or "確認できず",
                    "原文表現": item.get("deadline_original") or "確認できず",
                    "締切区分": item.get("deadline_type") or "確認できず",
                    "信頼度": item.get("source_reliability") or "確認できず",
                    "検証": validation_label,
                    "AI再検証": check.get("ai_review_verdict") or "無効・未実施",
                    "AI再検証理由": check.get("ai_review_reason") or "",
                    "年度原文確認": check.get("source_target_year_status") or "unclear",
                    "締切文脈": "確認" if check.get("deadline_context_match") else "要確認",
                    "公式性": (
                        "自動確認済み"
                        if check.get("official_source_verified")
                        else ("未確認" if check.get("source_type") == "企業公式候補" else "対象外")
                    ),
                    "ユーザー確認": confirmation_status,
                    "他利用者の確認": int(check.get("community_confirmed_count") or 0),
                    "他利用者の誤情報判定": int(check.get("community_rejected_count") or 0),
                    "今回の検索": "取得" if item.get("_registry_seen_latest", True) else "今回未取得（過去に取得）",
                    "取得回数": int(item.get("_registry_seen_count") or 1),
                    "最終取得": item.get("_registry_last_seen") or "今回",
                    "情報源": item.get("source_url") or "確認できず",
                })
        st.dataframe(rows, use_container_width=True, hide_index=True)
        st.caption(
            f"抽出コース数: {verification.course_count}／締切数: {verification.deadline_count}／"
            f"確認済み: {verification.verified_deadline_count}／要確認: {verification.unverified_deadline_count}／"
            f"SNS由来: {verification.sns_deadline_count}／SNS確認済み: {verification.confirmed_sns_deadline_count}／"
            f"SNS除外: {verification.rejected_sns_deadline_count}／"
            f"AI支持: {verification.ai_review_supported_count}／AI問題検出: {verification.ai_review_problem_count}"
        )

        for course in courses:
            if not isinstance(course, dict):
                continue
            with st.expander(str(course.get("course_name") or "コース名不明")):
                st.write("**内容**", course.get("course_summary") or "確認できず")
                st.write("**応募条件**", course.get("eligibility") or "確認できず")
                for item in course.get("deadlines") or []:
                    if not isinstance(item, dict):
                        continue
                    st.write("---")
                    st.write("**締切**", item.get("deadline") or "確認できず")
                    st.write("**根拠文**", item.get("evidence") or "確認できず")
                    url = str(item.get("source_url") or "")
                    if url.startswith(("http://", "https://")):
                        st.markdown(f"**情報源:** [{url}]({url})")

    st.write("**要約締切の根拠文**")
    st.info(ai_result.get("evidence") or "根拠文を確認できませんでした。")

    source_url = str(ai_result.get("source_url") or "")
    if source_url.startswith(("http://", "https://")):
        st.markdown(f"**要約締切に採用した情報源:** [{source_url}]({source_url})")
    else:
        st.write("**要約締切に採用した情報源**", source_url or "確認できず")

    st.write(
        "**対象年度と締切文脈を本文で確認できた情報源数**",
        verification.supporting_source_count,
    )

    if verification.warnings:
        st.write("**検証時の警告**")
        for warning in verification.warnings:
            st.warning(warning)
    else:
        st.success("自動検証で明確な問題は検出されませんでした。")

    notes = ai_result.get("notes") or []
    if notes:
        st.write("**AIが検出した注意点**")
        for note in notes:
            st.write(f"- {note}")

    with st.expander("JSON・検証結果・取得本文を表示"):
        st.json(ai_result)
        st.json(asdict(verification))
        for record in source_records:
            st.write(f"### {record.get('source_id')} {record.get('title')}")
            st.text(str(record.get("source_text", ""))[:5000])


def run_analysis(
    mode: str,
    company: str,
    target_year: int,
    recruitment_type: str,
    source_records: list[dict[str, Any]],
    enable_multi_ai_review: bool = True,
    pro_judge_mode: str = "conditional",
    progressive_candidates: list[dict[str, Any]] | None = None,
) -> None:
    set_research_scope(target_year, recruitment_type)
    extractor_degraded = False
    try:
        ai_result = ask_ai(
            company, target_year, recruitment_type, source_records,
            progressive_candidates=progressive_candidates
        )
    except Exception as exc:
        if not (_is_quota_exhausted_error(exc) or _is_retryable_gemini_error(exc)):
            raise
        extractor_degraded = True
        ai_result = build_degraded_ai_result(
            company,
            target_year,
            recruitment_type,
            gemini_error_summary(exc, "統合抽出AI"),
        )
    ai_result = merge_progressive_candidates_into_ai_result(
        ai_result, progressive_candidates or []
    )
    ai_result["_company_input"] = company
    extractor_snapshot = json.loads(json.dumps(ai_result, ensure_ascii=False))
    if enable_multi_ai_review and not extractor_degraded:
        try:
            review = ask_ai_review(
                company, target_year, recruitment_type, source_records, extractor_snapshot
            )
        except Exception as exc:
            reason = gemini_error_summary(exc, "独立検証AI")
            ai_result["_ai_review"] = {
                "overall_verdict": "disabled",
                "_review_model_requested": REVIEW_MODEL,
                "_review_model_used": None,
                "_review_fallback": False,
                "warnings": [reason],
                "summary": reason,
            }
            ai_result["_review_unavailable_reason"] = reason
            ai_result["_ai_judge"] = None
            ai_result["_judge_skipped_reason"] = "独立検証を完了できなかったためPro裁定を省略しました。"
            ai_result.setdefault("notes", []).append(reason)
        else:
            ai_result = apply_ai_review(ai_result, review, source_records, role="reviewer")
            judge_needed = (
                pro_judge_mode == "always"
                or (pro_judge_mode == "conditional" and review_requires_pro_judge(review))
            )
            if judge_needed:
                try:
                    judge = ask_ai_judge(
                        company, target_year, recruitment_type, source_records,
                        extractor_snapshot, review
                    )
                except Exception as exc:
                    reason = gemini_error_summary(exc, "Pro最終裁定")
                    ai_result["_ai_judge"] = None
                    ai_result["_judge_skipped_reason"] = reason
                    ai_result.setdefault("notes", []).append(reason)
                else:
                    ai_result = apply_ai_review(
                        ai_result, judge, source_records, role="judge"
                    )
                    ai_result["_judge_skipped_reason"] = None
            else:
                ai_result["_ai_judge"] = None
                ai_result["_judge_skipped_reason"] = (
                    "第1・第2モデルが一致し、根拠不足も検出されなかったためPro裁定を省略しました。"
                    if pro_judge_mode == "conditional"
                    else "Pro裁定を使用しない設定です。"
                )
    else:
        unavailable_reason = (
            "統合抽出AIが無料枠上限等で縮退したため、追加AI検証を省略しました。"
            if extractor_degraded else "複数AIモデルによる再検証は無効です。"
        )
        ai_result["_ai_review"] = {
            "overall_verdict": "disabled",
            "_review_model_requested": REVIEW_MODEL,
            "_review_model_used": None,
            "_review_fallback": False,
            "warnings": [unavailable_reason],
            "summary": "",
        }
        ai_result["_review_unavailable_reason"] = unavailable_reason
        ai_result["_ai_judge"] = None

    # 今回取得した締切を先に検証・蓄積し、過去に取得した締切と統合する。
    current_verification = verify_result(
        company,
        target_year,
        recruitment_type,
        source_records,
        ai_result,
    )
    save_deadlines_to_registry(
        company, target_year, recruitment_type, ai_result, current_verification
    )
    ai_result = merge_registry_into_ai_result(
        company, target_year, recruitment_type, ai_result
    )
    ai_result["_company_input"] = company
    verification = verify_result(
        company,
        target_year,
        recruitment_type,
        source_records,
        ai_result,
    )
    save_history(
        mode,
        company,
        target_year,
        recruitment_type,
        source_records,
        ai_result,
        verification,
    )
    st.session_state["latest_analysis"] = {
        "mode": mode,
        "company": company,
        "target_year": target_year,
        "recruitment_type": recruitment_type,
        "source_records": source_records,
        "ai_result": ai_result,
        "verification": verification,
        "progressive_candidates": progressive_candidates or [],
    }
    st.session_state.pop("latest_schedule", None)
    display_result(
        ai_result, verification, source_records, progressive_candidates
    )


def main() -> None:
    st.set_page_config(
        page_title="CareerLens｜就活締切リサーチ",
        page_icon="🔎",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    inject_app_styles()
    init_db()
    init_auth_db()
    require_access_password()
    current_user = require_user_account()
    render_account_controls(current_user)

    if current_user.get("is_guest"):
        st.info(
            "ゲスト利用中です。主要機能は利用できますが、検索履歴、確認状態、"
            "公式ドメイン判定、Z3日程履歴はセッション終了後に保持されません。"
        )

    st.markdown(
        """
        <section class="careerlens-hero">
          <div class="careerlens-kicker">CAREERLENS</div>
          <h1>RESEARCH. VERIFY. APPLY.</h1>
          <p class="careerlens-subtitle">企業の応募締切を、根拠つきで見つける。</p>
          <p class="careerlens-description">公式サイト・就活サイト・SNSを横断し、
          AIとルールベース検証で締切の信頼性を確認します。</p>
        </section>
        """,
        unsafe_allow_html=True,
    )

    mode = st.radio(
        "入力方法",
        ["企業名から自動検索", "採用情報を手動で貼り付け"],
        horizontal=True,
    )

    search_strategy = "standard"
    force_refresh = False
    enable_progressive_validation = True
    with st.form("research_form"):
        company_col, year_col, type_col = st.columns([2.2, 1, 1.25])
        with company_col:
            company = st.text_input(
                "企業名",
                placeholder="例：東京建物株式会社",
            )
        with year_col:
            target_year = st.number_input(
                "対象年度",
                min_value=2026,
                max_value=2035,
                value=2028,
            )
        with type_col:
            recruitment_type = st.selectbox(
                "募集区分",
                ["本選考", "インターン", "説明会", "その他"],
            )

        source_text = ""
        enable_multi_ai_review = True
        pro_judge_mode = "conditional"
        with st.expander("詳細な検索設定"):
            enable_multi_ai_review = st.checkbox(
                "別モデルで抽出結果を再検証する",
                value=True,
                help="別のAIが原文を独立に読み、年度・区分・コース・締切を再検証します。",
            )
            pro_judge_label = st.selectbox(
                "Proモデルの利用方法",
                [
                    "不一致・根拠不足がある場合だけ実行（推奨）",
                    "必ず実行（厳密検証・時間がかかる）",
                    "使用しない",
                ],
                disabled=not enable_multi_ai_review,
            )
            pro_judge_mode = {
                "不一致・根拠不足がある場合だけ実行（推奨）": "conditional",
                "必ず実行（厳密検証・時間がかかる）": "always",
                "使用しない": "off",
            }[pro_judge_label] if enable_multi_ai_review else "off"

            if mode == "企業名から自動検索":
                strategy_label = st.selectbox(
                    "検索方式",
                    [
                        "標準（主要情報源で不足した場合のみSNSを検索）",
                        "高速（企業公式・マイナビ・ONE CAREERを優先）",
                        "網羅（SNSも必ず検索）",
                    ],
                )
                search_strategy = {
                    "標準（主要情報源で不足した場合のみSNSを検索）": "standard",
                    "高速（企業公式・マイナビ・ONE CAREERを優先）": "fast",
                    "網羅（SNSも必ず検索）": "comprehensive",
                }[strategy_label]
                force_refresh = st.checkbox(
                    "キャッシュを使わず最新情報を再検索する",
                    value=False,
                    help="通常は12時間以内の検索結果と24時間以内の本文を再利用します。",
                )
                enable_progressive_validation = st.checkbox(
                    "収集と検査を同時並行で行う（推奨）",
                    value=True,
                    help="取得できたページから順に検査し、締切の見落としを補います。",
                )

        if mode == "採用情報を手動で貼り付け":
            source_text = st.text_area(
                "採用ページ・募集要項の本文",
                height=280,
                placeholder="企業の公式採用ページや募集要項から取得した文章を貼り付けてください。",
            )

        submitted = st.form_submit_button(
            "締切を調査する →",
            type="primary",
            use_container_width=True,
        )

    with st.expander("このサービスの仕組み", expanded=False):
        st.caption(
            "検索と本文取得を並行し、複数AIによる照合、Python検証、締切履歴の統合を行います。"
            f"抽出: {MODEL}／検証: {REVIEW_MODEL}／裁定: {JUDGE_MODEL}"
        )

    if not submitted:
        latest = st.session_state.get("latest_analysis")
        if isinstance(latest, dict):
            set_research_scope(
                int(latest.get("target_year") or 0),
                str(latest.get("recruitment_type") or ""),
            )
            refreshed = verify_result(
                str(latest.get("company") or ""),
                int(latest.get("target_year") or 0),
                str(latest.get("recruitment_type") or ""),
                latest.get("source_records") or [],
                latest.get("ai_result") or {},
            )
            latest["verification"] = refreshed
            display_result(
                latest["ai_result"],
                refreshed,
                latest["source_records"],
                latest.get("progressive_candidates") or [],
            )
            display_research_details(
                latest.get("source_records") or [],
                st.session_state.get("latest_search_performance"),
                latest.get("progressive_candidates") or [],
                company_name=str(latest.get("company") or ""),
            )
        render_scheduler()
        return

    if not company.strip():
        st.error("企業名を入力してください。")
        return

    if mode == "採用情報を手動で貼り付け" and len(source_text.strip()) < 50:
        st.error("検証に使う原文を50文字以上入力してください。")
        return

    set_research_scope(int(target_year), recruitment_type)

    try:
        progressive_candidates: list[dict[str, Any]] = []
        if mode == "企業名から自動検索":
            if enable_progressive_validation:
                status = st.status(
                    "**Searching...** 採用情報を調査しています",
                    expanded=True,
                )
                metrics_placeholder = st.empty()
                candidates_placeholder = st.empty()
                progress_bar = st.progress(0.04)
                last_progress = 0.04

                def update_progress(snapshot: dict[str, Any]) -> None:
                    nonlocal last_progress
                    total = max(1, int(snapshot.get("query_total", 0)) + int(snapshot.get("pages_scheduled", 0)) + int(snapshot.get("ai_scheduled", 0)))
                    done = int(snapshot.get("query_done", 0)) + int(snapshot.get("pages_done", 0)) + int(snapshot.get("ai_done", 0))
                    last_progress = max(last_progress, min(0.95, done / total))
                    progress_bar.progress(last_progress)

                    query_done = int(snapshot.get("query_done", 0))
                    query_total = int(snapshot.get("query_total", 0))
                    pages_done = int(snapshot.get("pages_done", 0))
                    pages_total = int(snapshot.get("pages_scheduled", 0))
                    ai_done = int(snapshot.get("ai_done", 0))
                    ai_total = int(snapshot.get("ai_scheduled", 0))
                    phase = str(snapshot.get("phase") or "search")
                    search_mark = "✓" if query_total and query_done >= query_total else "●"
                    fetch_mark = "✓" if pages_total and pages_done >= pages_total else ("●" if phase in {"fetch", "validate", "complete"} else "○")
                    validate_mark = "✓" if ai_total and ai_done >= ai_total else ("●" if phase in {"validate", "complete"} else "○")
                    metrics_placeholder.markdown(
                        f"{search_mark} **採用ページを検索**　{query_done}/{query_total}  \n"
                        f"{fetch_mark} **ページ本文を確認**　{pages_done}/{pages_total}  \n"
                        f"{validate_mark} **締切を照合・検証**　{ai_done}/{ai_total}  \n\n"
                        f"**締切候補 {snapshot.get('candidate_count', 0)}件**　"
                        f"{snapshot.get('latest_message', '')}"
                    )
                    with candidates_placeholder.container():
                        if snapshot.get("candidates"):
                            st.caption("見つかった候補を取得順に表示しています")
                            display_progressive_candidates(snapshot.get("candidates") or [])
                    status.update(
                        label=(
                            "**Searching...** "
                            + str(snapshot.get("latest_message") or "採用情報を調査しています")
                        )
                    )

                source_records, progressive_candidates, performance = progressive_research(
                    company, int(target_year), recruitment_type,
                    strategy=search_strategy, force_refresh=force_refresh,
                    progress_callback=update_progress,
                )
                progress_bar.progress(1.0)
                status.update(
                    label=f"**調査完了** 情報源{len(source_records)}件、締切候補{len(progressive_candidates)}件",
                    state="complete", expanded=False
                )
            else:
                with st.spinner("Searching... 主要情報源と候補ページを調査しています"):
                    search_results, search_meta = search_web(
                        company, int(target_year), recruitment_type,
                        strategy=search_strategy, force_refresh=force_refresh,
                    )
                    source_records, fetch_meta = enrich_sources(
                        search_results,
                        force_refresh=force_refresh,
                        company=company,
                    )
                    performance = {**search_meta, **fetch_meta}
            st.session_state["latest_search_performance"] = performance
        else:
            st.session_state.pop("latest_search_performance", None)
            source_records = create_manual_source(source_text)

        with st.spinner("Verifying... 情報源を統合し、締切を最終確認しています"):
            run_analysis(
                "auto_search" if mode == "企業名から自動検索" else "manual",
                company, int(target_year), recruitment_type, source_records,
                enable_multi_ai_review=enable_multi_ai_review,
                pro_judge_mode=pro_judge_mode,
                progressive_candidates=progressive_candidates,
            )
        display_research_details(
            source_records,
            st.session_state.get("latest_search_performance"),
            progressive_candidates,
            company_name=company,
        )
        render_scheduler()
    except Exception as exc:
        st.exception(exc)
