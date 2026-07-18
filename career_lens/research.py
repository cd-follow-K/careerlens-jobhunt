"""Progressive producer-consumer research pipeline and candidate integration."""

from .common import *
from .storage import *
from .sources import *
from .sources import _enrich_one_source, _merge_search_outputs, _run_single_search
from .ai_client import *


def ask_ai_extract_one_source(
    company: str,
    target_year: int,
    recruitment_type: str,
    record: dict[str, Any],
    user_id: str | None = None,
) -> dict[str, Any]:
    cached = load_source_extraction_cache(
        company, target_year, recruitment_type, record, user_id
    )
    if cached is not None:
        cached["_cache_hit"] = True
        return cached
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY が設定されていません。")
    excerpt = str(record.get("relevant_excerpt") or record.get("source_text") or "")[:PROGRESSIVE_EXCERPT_CHARS]
    client = make_gemini_client(api_key)
    system_instruction = """
あなたは採用情報1ページの逐次検査担当です。提示されたページだけを読み、推測や一般知識を使わないでください。
企業名、対象年度、募集区分が一致するかを判定し、応募・提出・エントリーの締切だけを抽出してください。
開催日、面接日、説明会日、結果発表日を締切として抽出してはいけません。
複数コースや複数回の締切が明記されている場合は全て返してください。年が書かれていない日付はdeadlineをnullにし、原文表現だけを保持してください。
必ずJSONだけを返してください。
""".strip()
    prompt = f"""
企業: {company}
対象年度: {target_year}年卒
募集区分: {recruitment_type}
情報源ID: {record.get('source_id')}
URL: {record.get('url')}
情報源種別: {record.get('source_type')}
企業公式性の機械判定: {record.get('official_source_status', '対象外')}

--- ページ抜粋 ---
{excerpt}
--- 抜粋終了 ---
""".strip()
    started = time.perf_counter()
    response, used_model, fallback, retries = generate_content_resilient(
        client, primary_model=REVIEW_MODEL, fallback_models=[EXTRACT_FALLBACK_MODEL, MODEL],
        contents=prompt, schema=SOURCE_EXTRACTION_SCHEMA,
        system_instruction=system_instruction, max_output_tokens=5000, thinking_level="minimal"
    )
    if not response.text:
        raise RuntimeError("逐次検査AIから応答を取得できませんでした。")
    result = extract_json(response.text)
    result["_model_used"] = used_model
    result["_fallback"] = fallback
    result["_retry_count"] = retries
    result["_elapsed_seconds"] = round(time.perf_counter() - started, 2)
    result["_cache_hit"] = False
    save_source_extraction_cache(
        company, target_year, recruitment_type, record, result, user_id
    )
    return result


def normalize_source_ai_candidates(
    result: dict[str, Any], record: dict[str, Any], source_text: str,
    target_year: int,
) -> list[dict[str, Any]]:
    if not result.get("page_relevant") or result.get("company_match") == "no":
        return []
    candidates: list[dict[str, Any]] = []
    source_normalized = normalize_text(source_text)
    deterministic_year = source_target_year_status(source_text, target_year)
    ai_year = str(result.get("target_year_match") or "unclear")
    effective_year = deterministic_year if deterministic_year != "unclear" else ai_year
    for item in result.get("deadlines") or []:
        if not isinstance(item, dict):
            continue
        evidence = str(item.get("evidence") or "").strip()
        deadline = item.get("deadline")
        original = str(item.get("deadline_original") or "").strip()
        evidence_supported = bool(evidence and normalize_text(evidence) in source_normalized)
        date_supported = bool(
            (deadline and normalize_text(str(deadline)) in source_normalized)
            or (original and normalize_text(original) in source_normalized)
            or evidence_supported
        )
        if not evidence_supported and not date_supported:
            continue
        context_supported = deadline_context_matches(
            source_text,
            str(deadline or ""),
            original,
            evidence,
        )
        source_label = str(record.get("source_type") or "")
        course_name = str(item.get("course_name") or "コース未特定")
        local_checks = deadline_local_evidence_checks(
            source_text,
            str(record.get("url") or ""),
            target_year,
            course_name,
            str(deadline or ""),
            original,
            evidence,
        )
        effective_year = str(local_checks["target_year_status"])
        body_available = bool(record.get("fetch_success")) or source_label == "手動入力"
        recruitment_match = str(result.get("recruitment_type_match") or "unclear")
        rejected = effective_year == "no" or recruitment_match == "no"
        fully_supported = (
            evidence_supported
            and context_supported
            and effective_year == "yes"
            and bool(local_checks["course_match"])
            and body_available
            and not rejected
        )
        validation_level = (
            "ai_source_rejected"
            if rejected
            else ("ai_source_supported" if fully_supported else "ai_source_needs_confirmation")
        )
        status = (
            "provisional"
            if fully_supported and not source_label.startswith("SNS")
            else "needs_confirmation"
        )
        candidates.append({
            "course_name": course_name,
            "deadline": deadline,
            "deadline_original": original or None,
            "deadline_type": item.get("deadline_type"),
            "evidence": evidence,
            "confidence": float(item.get("confidence") or 0.0),
            "source_id": record.get("source_id"),
            "source_url": record.get("url"),
            "source_type": source_label,
            "source_reliability": source_reliability_from_record(record),
            "deadline_status": status,
            "validation_level": validation_level,
            "method": f"逐次AI検査（{result.get('_model_used', '')}）",
            "target_year_match": effective_year,
            "target_year_python": deterministic_year,
            "local_target_year_match": effective_year == "yes",
            "course_context_match": bool(local_checks["course_match"]),
            "source_body_fetched": body_available,
            "recruitment_type_match": recruitment_match,
            "evidence_supported": evidence_supported,
            "deadline_context_match": context_supported,
        })
    return candidates


def merge_progressive_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for item in candidates:
        key = (
            normalize_text(str(item.get("course_name") or "未特定")),
            str(item.get("deadline") or item.get("deadline_original") or ""),
            normalize_text(str(item.get("deadline_type") or "")),
            normalize_url(str(item.get("source_url") or "")),
        )
        current = merged.get(key)
        ai_levels = {"ai_source_supported", "ai_source_needs_confirmation"}
        rank = (
            2 if item.get("validation_level") in ai_levels else 1,
            float(item.get("confidence") or 0.0),
        )
        current_rank = (
            2 if current and current.get("validation_level") in ai_levels else 0,
            float(current.get("confidence") or 0.0) if current else 0.0,
        )
        if current is None or rank > current_rank:
            merged[key] = item
    return list(merged.values())


def progressive_coverage_summary(
    candidates: list[dict[str, Any]], records: list[dict[str, Any]]
) -> dict[str, Any]:
    ai_candidates = [c for c in candidates if c.get("validation_level") == "ai_source_supported"]
    unique_deadlines = {str(c.get("deadline") or c.get("deadline_original") or "") for c in ai_candidates}
    unique_courses = {normalize_text(str(c.get("course_name") or "未特定")) for c in ai_candidates}
    official_records = [
        r for r in records
        if r.get("source_type") == "企業公式候補"
        and bool(r.get("official_source_verified"))
    ]
    portal_records = [r for r in records if r.get("source_type") in {"マイナビ", "ONE CAREER"}]
    return {
        "ai_candidate_count": len(ai_candidates),
        "unique_deadline_count": len({v for v in unique_deadlines if v}),
        "unique_course_count": len(unique_courses),
        "official_source_count": len(official_records),
        "portal_source_count": len(portal_records),
        "sufficient": bool(ai_candidates and official_records and (portal_records or len(ai_candidates) >= 2)),
    }


def build_progress_snapshot(
    *, phase: str, query_done: int, query_total: int, pages_done: int, pages_scheduled: int,
    ai_done: int, ai_scheduled: int, candidates: list[dict[str, Any]], latest_message: str
) -> dict[str, Any]:
    return {
        "phase": phase, "query_done": query_done, "query_total": query_total,
        "pages_done": pages_done, "pages_scheduled": pages_scheduled,
        "ai_done": ai_done, "ai_scheduled": ai_scheduled,
        "candidate_count": len(candidates), "latest_message": latest_message,
        "candidates": candidates,
    }


def progressive_research(
    company: str, target_year: int, recruitment_type: str,
    strategy: str = "standard", force_refresh: bool = False,
    progress_callback: Any | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Web収集・本文取得・逐次検査をパイプライン化し、収集漏れを抑える。"""
    started = time.perf_counter()
    current_user_id = get_current_user_id()
    cache_strategy = f"v71-accuracy-progressive-{strategy}"
    cached = None if force_refresh else load_search_cache(
        company, target_year, recruitment_type, cache_strategy
    )
    primary_queries, social_queries = build_search_query_groups(
        company, target_year, recruitment_type
    )
    deep_queries, deep_social_queries = build_deep_search_query_groups(
        company, target_year, recruitment_type
    )
    if strategy == "fast":
        query_waves = [("主要検索", primary_queries)]
        max_pages = 14
        max_ai_pages = min(5, PROGRESSIVE_MAX_AI_PAGES)
    elif strategy == "comprehensive":
        query_waves = [
            ("主要検索", primary_queries), ("深掘り検索", deep_queries),
            ("SNS・補助検索", social_queries + deep_social_queries),
        ]
        max_pages = PROGRESSIVE_MAX_PAGES_COMPREHENSIVE
        max_ai_pages = PROGRESSIVE_MAX_AI_PAGES_COMPREHENSIVE
    else:
        query_waves = [("主要検索", primary_queries), ("深掘り検索", deep_queries)]
        max_pages = PROGRESSIVE_MAX_PAGES_STANDARD
        max_ai_pages = PROGRESSIVE_MAX_AI_PAGES

    found: dict[str, dict[str, Any]] = {}
    source_records: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    scheduled_urls: set[str] = set()
    ai_scheduled_urls: set[str] = set()
    page_cache_hits = 0
    failed_pages = 0
    query_done = 0
    query_total = 0
    pages_done = 0
    ai_done = 0
    source_index = 0
    search_cache_hit = cached is not None
    social_searched = False

    def emit(phase: str, message: str) -> None:
        if progress_callback:
            progress_callback(build_progress_snapshot(
                phase=phase, query_done=query_done, query_total=query_total,
                pages_done=pages_done, pages_scheduled=len(scheduled_urls),
                ai_done=ai_done, ai_scheduled=len(ai_scheduled_urls),
                candidates=merge_progressive_candidates(candidates), latest_message=message,
            ))

    search_executor = ThreadPoolExecutor(max_workers=PROGRESSIVE_SEARCH_WORKERS)
    fetch_executor = ThreadPoolExecutor(max_workers=PROGRESSIVE_FETCH_WORKERS)
    ai_executor = ThreadPoolExecutor(max_workers=PROGRESSIVE_AI_WORKERS)
    search_futures: dict[Any, tuple[str, str]] = {}
    fetch_futures: dict[Any, dict[str, Any]] = {}
    ai_futures: dict[Any, dict[str, Any]] = {}

    def schedule_queries(label: str, queries: list[str]) -> None:
        nonlocal query_total, social_searched
        unique = []
        seen = {q for _, q in search_futures.values()}
        for q in queries:
            if q not in seen:
                unique.append(q)
                seen.add(q)
        if label.startswith("SNS"):
            social_searched = True
        query_total += len(unique)
        for query in unique:
            future = search_executor.submit(_run_single_search, query, 8)
            search_futures[future] = (label, query)

    def schedule_best_pages() -> None:
        nonlocal source_index
        available_slots = max(0, PROGRESSIVE_FETCH_WORKERS - len(fetch_futures))
        if available_slots <= 0 or len(scheduled_urls) >= max_pages:
            return
        ranked = sorted(found.values(), key=lambda x: int(x.get("score", 0)), reverse=True)
        for record in ranked:
            url = str(record.get("url") or "")
            if not url or url in scheduled_urls:
                continue
            source_index += 1
            scheduled_urls.add(url)
            future = fetch_executor.submit(
                _enrich_one_source, source_index, record, force_refresh, company
            )
            fetch_futures[future] = record
            available_slots -= 1
            if available_slots <= 0 or len(scheduled_urls) >= max_pages:
                break

    try:
        if cached:
            # キャッシュ利用時は検索を再実行せず、保存済み候補の本文取得・逐次検査だけを行う。
            query_waves.clear()
            social_decided = True
            for record in cached[:max_pages]:
                if isinstance(record, dict) and record.get("url"):
                    found[str(record["url"])] = record
            emit("cache", f"保存済みの検索候補{len(found)}件から逐次検査を開始")
            schedule_best_pages()
        else:
            label, queries = query_waves.pop(0)
            schedule_queries(label, queries)
            emit("search", "主要情報源の検索を開始")

        social_decided = strategy in {"fast", "comprehensive"}
        while search_futures or fetch_futures or ai_futures or query_waves:
            progressed = False
            if search_futures:
                done, _ = wait(set(search_futures), timeout=0.15, return_when=FIRST_COMPLETED)
                for future in done:
                    label, query = search_futures.pop(future)
                    query_done += 1
                    try:
                        query_result, results = future.result()
                    except Exception:
                        query_result, results = query, []
                    before = set(found)
                    _merge_search_outputs(
                        found, [(query_result, results)], company, target_year, recruitment_type
                    )
                    new_count = len(set(found) - before)
                    emit("search", f"{label}: {query_done}/{query_total}、新規候補{new_count}件")
                    progressed = True
                schedule_best_pages()

            if fetch_futures:
                done, _ = wait(set(fetch_futures), timeout=0.05, return_when=FIRST_COMPLETED)
                for future in done:
                    fetch_futures.pop(future, None)
                    try:
                        _, record, cache_hit = future.result()
                    except Exception:
                        failed_pages += 1
                        continue
                    pages_done += 1
                    page_cache_hits += int(cache_hit)
                    if not record.get("fetch_success"):
                        failed_pages += 1
                    record["relevant_excerpt"] = extract_relevant_excerpt(
                        str(record.get("source_text") or "")
                    )
                    source_records.append(record)
                    candidates.extend(python_deadline_hints(
                        company, target_year, recruitment_type, record
                    ))
                    if (
                        len(ai_scheduled_urls) < max_ai_pages
                        and source_needs_ai_extraction(
                            company, target_year, recruitment_type, record
                        )
                    ):
                        url = str(record.get("url") or "")
                        if url not in ai_scheduled_urls:
                            ai_scheduled_urls.add(url)
                            ai_future = ai_executor.submit(
                                ask_ai_extract_one_source, company, target_year,
                                recruitment_type, record, current_user_id
                            )
                            ai_futures[ai_future] = record
                    emit("fetch", f"本文取得{pages_done}件。{record.get('title', '')[:45]}を検査キューへ追加")
                    progressed = True
                schedule_best_pages()

            if ai_futures:
                done, _ = wait(set(ai_futures), timeout=0.05, return_when=FIRST_COMPLETED)
                for future in done:
                    record = ai_futures.pop(future)
                    ai_done += 1
                    try:
                        result = future.result()
                        candidates.extend(normalize_source_ai_candidates(
                            result,
                            record,
                            str(record.get("source_text") or ""),
                            target_year,
                        ))
                        msg = f"逐次AI検査{ai_done}件完了: {record.get('title', '')[:45]}"
                    except Exception as exc:
                        msg = f"逐次AI検査を継続できない情報源あり: {type(exc).__name__}"
                    emit("validate", msg)
                    progressed = True

            # 検索ウェーブを段階的に追加する。検索中も既取得ページの検査は継続される。
            if not search_futures and query_waves:
                label, queries = query_waves.pop(0)
                schedule_queries(label, queries)
                emit("search", f"{label}を追加し、既取得情報の検査と並行実行")
                progressed = True
            elif (
                not search_futures and not query_waves and strategy == "standard"
                and not social_decided
            ):
                coverage = progressive_coverage_summary(
                    merge_progressive_candidates(candidates), source_records
                )
                social_decided = True
                if not coverage["sufficient"]:
                    schedule_queries("SNS・補助検索", social_queries + deep_social_queries)
                    emit("search", "主要情報源だけでは不足したためSNS・補助検索を追加")
                    progressed = True

            if not progressed:
                time.sleep(0.03)

        merged_candidates = merge_progressive_candidates(candidates)
        ranked_cache = sorted(found.values(), key=lambda x: int(x.get("score", 0)), reverse=True)[:max_pages]
        save_search_cache(
            company, target_year, recruitment_type, cache_strategy, ranked_cache
        )
        source_records.sort(key=lambda r: int(r.get("score", 0)), reverse=True)
        coverage = progressive_coverage_summary(merged_candidates, source_records)
        meta = {
            "strategy": strategy,
            "search_cache_hit": search_cache_hit,
            "social_searched": social_searched,
            "query_count": query_total,
            "candidate_count": len(found),
            "selected_count": len(source_records),
            "page_cache_hits": page_cache_hits,
            "failed_pages": failed_pages,
            "progressive_ai_pages": ai_done,
            "progressive_candidate_count": len(merged_candidates),
            "unique_deadline_count": coverage["unique_deadline_count"],
            "unique_course_count": coverage["unique_course_count"],
            "search_seconds": round(time.perf_counter() - started, 2),
            "fetch_seconds": 0.0,
        }
        emit("complete", f"収集{len(source_records)}件、逐次候補{len(merged_candidates)}件で完了")
        return source_records, merged_candidates, meta
    finally:
        search_executor.shutdown(wait=True, cancel_futures=True)
        fetch_executor.shutdown(wait=True, cancel_futures=True)
        ai_executor.shutdown(wait=True, cancel_futures=True)


def progressive_candidates_for_prompt(candidates: list[dict[str, Any]]) -> str:
    useful = [
        c for c in candidates
        if c.get("validation_level") == "ai_source_supported" or c.get("deadline")
    ]
    compact = []
    for item in useful[:40]:
        compact.append({
            "course_name": item.get("course_name"),
            "deadline": item.get("deadline"),
            "deadline_original": item.get("deadline_original"),
            "deadline_type": item.get("deadline_type"),
            "source_id": item.get("source_id"),
            "source_url": item.get("source_url"),
            "evidence": item.get("evidence"),
            "validation_level": item.get("validation_level"),
            "method": item.get("method"),
        })
    return json.dumps(compact, ensure_ascii=False, indent=2)


def merge_progressive_candidates_into_ai_result(
    ai_result: dict[str, Any], candidates: list[dict[str, Any]]
) -> dict[str, Any]:
    """全体抽出が見落とした、ページ単位AI検査済み候補を要確認情報として補完する。"""
    courses = ai_result.setdefault("courses", [])
    if not isinstance(courses, list):
        courses = []
        ai_result["courses"] = courses
    for candidate in candidates:
        if candidate.get("validation_level") not in {
            "ai_source_supported", "ai_source_needs_confirmation"
        }:
            continue
        if candidate.get("target_year_match") == "no" or candidate.get("recruitment_type_match") == "no":
            continue
        deadline = candidate.get("deadline")
        original = candidate.get("deadline_original")
        if not deadline and not original:
            continue
        course_name = str(candidate.get("course_name") or "コース未特定")
        duplicate = False
        for course in courses:
            if normalize_text(str(course.get("course_name") or "")) != normalize_text(course_name):
                continue
            for item in course.get("deadlines") or []:
                if (
                    str(item.get("deadline") or item.get("deadline_original") or "")
                    == str(deadline or original or "")
                    and normalize_url(str(item.get("source_url") or ""))
                    == normalize_url(str(candidate.get("source_url") or ""))
                ):
                    duplicate = True
                    break
        if duplicate:
            continue
        target = next((
            c for c in courses
            if normalize_text(str(c.get("course_name") or "")) == normalize_text(course_name)
        ), None)
        if target is None:
            target = {
                "course_name": course_name, "course_summary": None,
                "eligibility": None, "deadlines": []
            }
            courses.append(target)
        fully_supported = candidate.get("validation_level") == "ai_source_supported"
        target.setdefault("deadlines", []).append({
            "deadline": deadline,
            "deadline_original": original,
            "deadline_type": candidate.get("deadline_type"),
            "source_id": candidate.get("source_id"),
            "source_url": candidate.get("source_url"),
            "evidence": candidate.get("evidence"),
            "confidence": min(float(candidate.get("confidence") or 0.0), 0.85),
            "source_reliability": candidate.get("source_reliability") or "other",
            "deadline_status": (
                candidate.get("deadline_status")
                or ("provisional" if fully_supported else "needs_confirmation")
            ),
            "notes": [
                "ページ単位の逐次AI検査で検出。全体抽出の見落とし補完候補。",
                *([] if fully_supported else [
                    "年度または締切文脈を機械的に確定できないため要確認。"
                ]),
            ],
        })
    ai_result.setdefault("notes", []).append(
        f"逐次収集・検査で{len(candidates)}件の候補を比較した。"
    )
    return postprocess_ai_result(ai_result)
