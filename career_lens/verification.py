"""Grounded verification of company, year, course, evidence, and deadlines."""

from .common import *
from .storage import *
from .sources import *


def verify_one_deadline(
    company_name: str,
    target_year: int,
    course_name: str,
    deadline_info: dict[str, Any],
    source_records: list[dict[str, Any]],
) -> dict[str, Any]:
    """コース別締切1件を、根拠URL・根拠文・日付について検証する。"""
    selected_source = find_selected_source(deadline_info, source_records)
    source_url_valid = selected_source is not None
    selected_text = str(selected_source.get("source_text", "")) if selected_source else ""
    selected_norm = normalize_text(selected_text)

    evidence = str(deadline_info.get("evidence") or "")
    evidence_in_source = bool(evidence) and normalize_text(evidence) in selected_norm

    deadline_original = str(deadline_info.get("deadline_original") or "")
    deadline_iso = str(deadline_info.get("deadline") or "")
    deadline_in_source = False
    if deadline_original:
        deadline_in_source = normalize_text(deadline_original) in selected_norm
    if not deadline_in_source and deadline_iso:
        deadline_in_source = any(
            normalize_text(value) in selected_norm for value in date_variants(deadline_iso)
        )
    deadline_context_match = deadline_context_matches(
        selected_text, deadline_iso, deadline_original, evidence
    )
    selected_url = str((selected_source or {}).get("url") or "")
    local_checks = deadline_local_evidence_checks(
        selected_text,
        selected_url,
        target_year,
        course_name,
        deadline_iso,
        deadline_original,
        evidence,
    )
    source_year_status = str(local_checks["target_year_status"])
    course_context_match = bool(local_checks["course_match"])

    parsed = parse_deadline(deadline_info.get("deadline"))
    deadline_is_future = None if parsed is None else parsed >= date.today()
    source_kind = str(selected_source.get("source_type", "")) if selected_source else ""
    source_body_fetched = bool(
        selected_source
        and (
            selected_source.get("fetch_success") is True
            or source_kind == "手動入力"
        )
    )
    social_source = source_kind.startswith("SNS") or is_social_reliability(
        deadline_info.get("source_reliability")
    )
    confirmation_status = get_deadline_confirmation(
        company_name, course_name, deadline_iso, str(deadline_info.get("source_url") or "")
    )
    community_consensus = get_community_deadline_consensus(
        company_name,
        target_year,
        get_research_scope()[1],
        course_name,
        deadline_iso,
        str(deadline_info.get("source_url") or ""),
    )
    official_verified = bool(
        selected_source
        and source_kind == "企業公式候補"
        and selected_source.get("official_source_verified")
    )
    trusted_source = (
        source_kind in {"マイナビ", "ONE CAREER", "手動入力"}
        or official_verified
    ) and source_body_fetched
    source_accepted = trusted_source
    historical_verified = bool(deadline_info.get("_registry_last_verified")) and not bool(
        deadline_info.get("_registry_seen_latest", True)
    )
    ai_review_verdict = str(deadline_info.get("_ai_review_verdict") or "")
    ai_review_accepted = ai_review_verdict in {"", "supported", "reviewer_added"}

    current_verified = all([
        bool(course_name.strip()),
        source_url_valid,
        source_body_fetched,
        evidence_in_source,
        deadline_in_source if deadline_iso else False,
        deadline_context_match,
        source_year_status == "yes",
        course_context_match,
        source_accepted,
        ai_review_accepted,
    ])
    # 旧基準の履歴は保持するが、新基準で再検証できない限り確定扱いしない。
    verified = current_verified
    return {
        "course_name": course_name,
        "deadline": deadline_info.get("deadline"),
        "deadline_original": deadline_info.get("deadline_original"),
        "deadline_type": deadline_info.get("deadline_type"),
        "source_url": deadline_info.get("source_url"),
        "source_type": source_kind or "確認できず",
        "social_source": social_source,
        "confirmation_status": confirmation_status,
        "user_confirmed": confirmation_status == "確認済み",
        "community_confirmed_count": int(
            community_consensus.get("confirmed_count") or 0
        ),
        "community_rejected_count": int(
            community_consensus.get("rejected_count") or 0
        ),
        "source_url_valid": source_url_valid,
        "evidence_in_source": evidence_in_source,
        "source_body_fetched": source_body_fetched,
        "deadline_in_source": deadline_in_source,
        "deadline_context_match": deadline_context_match,
        "course_context_match": course_context_match,
        "source_target_year_status": source_year_status,
        "official_source_verified": official_verified,
        "deadline_is_future": deadline_is_future,
        "verified": verified,
        "historical_verified": historical_verified,
        "seen_latest": bool(deadline_info.get("_registry_seen_latest", True)),
        "first_seen": deadline_info.get("_registry_first_seen"),
        "last_seen": deadline_info.get("_registry_last_seen"),
        "seen_count": int(deadline_info.get("_registry_seen_count") or 1),
        "ai_review_verdict": ai_review_verdict or "無効・未実施",
        "ai_review_reason": deadline_info.get("_ai_review_reason") or "",
        "ai_review_accepted": ai_review_accepted,
    }


def verify_result(
    company_input: str,
    target_year: int,
    recruitment_type: str,
    source_records: list[dict[str, Any]],
    ai_result: dict[str, Any],
) -> VerificationResult:
    refresh_official_source_flags(company_input, source_records)
    warnings: list[str] = []
    all_source_text = "\n".join(str(record.get("source_text", "")) for record in source_records)
    all_source_norm = normalize_text(all_source_text)

    company_ai = str(ai_result.get("company_name") or "")
    company_match = bool(company_ai) and (
        normalize_text(company_input) in normalize_text(company_ai)
        or normalize_text(company_ai) in normalize_text(company_input)
        or any(normalize_text(token) in all_source_norm for token in company_tokens(company_input))
    )
    if not company_match:
        warnings.append("入力企業名と抽出企業名の一致を確認できません。")

    ai_year = ai_result.get("target_year")
    try:
        target_year_match = int(ai_year) == int(target_year)
    except (TypeError, ValueError):
        target_year_match = False
    if not target_year_match:
        warnings.append("対象年度が一致していません。")

    ai_type = str(ai_result.get("recruitment_type") or "")
    recruitment_type_match = bool(ai_type) and (
        normalize_text(recruitment_type) in normalize_text(ai_type)
        or normalize_text(ai_type) in normalize_text(recruitment_type)
    )
    if not recruitment_type_match:
        warnings.append("募集区分が一致していません。")

    # 要約値（最も早い締切）も従来どおり検証する。
    selected_source = find_selected_source(ai_result, source_records)
    source_url_valid = selected_source is not None
    summary_historical_verified = bool(ai_result.get("_registry_last_verified")) and not bool(
        ai_result.get("_registry_seen_latest", True)
    )
    if not source_url_valid and ai_result.get("deadline"):
        if summary_historical_verified:
            warnings.append("要約締切は過去の検索で検証済みですが、今回の検索では同じ情報源を取得できませんでした。")
        else:
            warnings.append("AIが示した要約締切の根拠URLを検索結果の中で確認できません。")

    selected_text = str(selected_source.get("source_text", "")) if selected_source else ""
    selected_norm = normalize_text(selected_text)
    evidence = str(ai_result.get("evidence") or "")
    evidence_in_source = bool(evidence) and normalize_text(evidence) in selected_norm

    deadline_original = str(ai_result.get("deadline_original") or "")
    deadline_iso = str(ai_result.get("deadline") or "")
    deadline_in_source = False
    if deadline_original:
        deadline_in_source = normalize_text(deadline_original) in selected_norm
    if not deadline_in_source and deadline_iso:
        deadline_in_source = any(normalize_text(v) in selected_norm for v in date_variants(deadline_iso))
    deadline_context_match = deadline_context_matches(
        selected_text, deadline_iso, deadline_original, evidence
    )
    summary_local_checks = deadline_local_evidence_checks(
        selected_text,
        str((selected_source or {}).get("url") or ""),
        target_year,
        str(ai_result.get("recruitment_type") or "選考"),
        deadline_iso,
        deadline_original,
        evidence,
    )
    summary_source_year_status = (
        str(summary_local_checks["target_year_status"])
        if selected_source else "unclear"
    )

    parsed_deadline = parse_deadline(ai_result.get("deadline"))
    deadline_is_future = None if parsed_deadline is None else parsed_deadline >= date.today()
    if deadline_is_future is False:
        warnings.append("最も早い締切は既に過ぎています。")

    selected_type = str(selected_source.get("source_type", "")) if selected_source else ""
    summary_source_body_fetched = bool(
        selected_source
        and (
            selected_source.get("fetch_success") is True
            or selected_type == "手動入力"
        )
    )
    official_source_verified = bool(
        selected_source
        and selected_type == "企業公式候補"
        and selected_source.get("official_source_verified")
    )
    official_source_like = official_source_verified or selected_type == "手動入力"
    if deadline_iso and not deadline_context_match:
        warnings.append("要約締切の日付を、締切を表す文脈の中で確認できません。")
    if deadline_iso and summary_source_year_status != "yes":
        warnings.append("要約締切の情報源では対象年度を明示的に確認できません。")
    if deadline_iso and selected_type == "企業公式候補" and not official_source_verified:
        warnings.append("情報源ドメインの企業公式性は利用者確認が必要です。")
    if deadline_iso and not summary_source_body_fetched:
        warnings.append("要約締切は検索概要のみで、元ページ本文を確認できません。")
    if deadline_iso and not evidence_in_source:
        warnings.append("要約締切の根拠文を取得本文中で確認できません。")

    supporting_source_count = 0
    if deadline_iso:
        for record in source_records:
            record_text = str(record.get("source_text", ""))
            record_type = str(record.get("source_type") or "")
            body_fetched = bool(record.get("fetch_success")) or record_type == "手動入力"
            reliable_source = (
                record_type in {"マイナビ", "ONE CAREER", "手動入力"}
                or bool(record.get("official_source_verified"))
            )
            local = deadline_local_evidence_checks(
                record_text,
                str(record.get("url") or ""),
                target_year,
                "選考",
                deadline_iso,
                deadline_original,
                "",
            )
            company_in_source = any(
                normalize_text(token) in normalize_text(record_text)
                for token in company_tokens(company_input)
            )
            if all([
                body_fetched,
                reliable_source,
                company_in_source,
                local["target_year_status"] == "yes",
                local["deadline_context_match"],
            ]):
                supporting_source_count += 1

    course_deadline_checks = []
    for course_name, deadline_info in iter_course_deadlines(ai_result):
        course_deadline_checks.append(
            verify_one_deadline(
                company_input, target_year, course_name, deadline_info, source_records
            )
        )
    courses = ai_result.get("courses") or []
    course_count = len([c for c in courses if isinstance(c, dict)]) if isinstance(courses, list) else 0
    deadline_count = len(course_deadline_checks)
    verified_deadline_count = sum(bool(item.get("verified")) for item in course_deadline_checks)
    unverified_deadline_count = deadline_count - verified_deadline_count
    sns_deadline_count = sum(bool(item.get("social_source")) for item in course_deadline_checks)
    confirmed_sns_deadline_count = sum(
        bool(item.get("social_source")) and item.get("confirmation_status") == "確認済み"
        for item in course_deadline_checks
    )
    rejected_sns_deadline_count = sum(
        bool(item.get("social_source")) and item.get("confirmation_status") == "誤情報として除外"
        for item in course_deadline_checks
    )

    if sns_deadline_count and confirmed_sns_deadline_count < sns_deadline_count:
        warnings.append(
            f"SNS由来の締切{sns_deadline_count}件にはユーザー確認が必要です。"
        )

    if deadline_count == 0 and not deadline_iso:
        warnings.append("対象条件に対応する応募締切を確認できませんでした。")

    if recruitment_type == "インターン":
        if course_count == 0:
            warnings.append("インターンのコースを確認できませんでした。")
        if deadline_count == 0:
            warnings.append("コース別の応募締切を確認できませんでした。")
        if unverified_deadline_count:
            warnings.append(f"コース別締切{unverified_deadline_count}件は原文または情報源を十分に確認できません。")

    for item in course_deadline_checks:
        if item.get("deadline_is_future") is False:
            warnings.append(f"{item.get('course_name')}の締切{item.get('deadline')}は既に過ぎています。")
        if not item.get("deadline_context_match"):
            warnings.append(
                f"{item.get('course_name')}の日付は締切文脈を確認できないため要確認です。"
            )
        if item.get("source_target_year_status") != "yes":
            warnings.append(
                f"{item.get('course_name')}の情報源では対象年度を明示的に確認できません。"
            )
        if not item.get("course_context_match"):
            warnings.append(
                f"{item.get('course_name')}と締切の対応を同一の根拠範囲で確認できません。"
            )
        if not item.get("source_body_fetched"):
            warnings.append(
                f"{item.get('course_name')}は元ページ本文を取得できず、検索概要のみです。"
            )
        if not item.get("evidence_in_source"):
            warnings.append(
                f"{item.get('course_name')}の根拠文を取得本文中で確認できません。"
            )

    # 締切が抽出されている場合、それぞれが検証できることを合格条件とする。
    course_deadlines_ok = (
        (deadline_count > 0 and unverified_deadline_count == 0)
        or (deadline_count == 0 and bool(deadline_iso))
    )
    summary_social = selected_type.startswith("SNS") or is_social_reliability(
        ai_result.get("source_reliability")
    )
    if summary_social:
        summary_confirmation = "未確認"
        summary_url = str(ai_result.get("source_url") or "")
        for item in course_deadline_checks:
            if (
                str(item.get("deadline") or "") == deadline_iso
                and str(item.get("source_url") or "") == summary_url
                and item.get("confirmation_status") in {"確認済み", "誤情報として除外"}
            ):
                summary_confirmation = str(item.get("confirmation_status"))
                break
        if summary_confirmation == "未確認" and not course_deadline_checks:
            summary_confirmation = get_deadline_confirmation(
                company_input,
                str(ai_result.get("recruitment_type") or "選考"),
                deadline_iso,
                summary_url,
            )
    else:
        summary_confirmation = "対象外"
    trusted_summary_source = (
        selected_type in {"マイナビ", "ONE CAREER", "手動入力"}
        or official_source_verified
        or (summary_social and summary_confirmation == "確認済み")
    )
    summary_ai_review_verdict = str(ai_result.get("_ai_review_verdict") or "")
    summary_ai_review_ok = summary_ai_review_verdict in {"", "supported", "reviewer_added"}
    summary_deadline_ok = (
        bool(deadline_iso)
        and (
            source_url_valid
            and summary_source_body_fetched
            and evidence_in_source
            and deadline_in_source
            and deadline_context_match
            and summary_source_year_status == "yes"
            and trusted_summary_source
            and summary_ai_review_ok
        )
    )

    review = ai_result.get("_ai_judge") or ai_result.get("_ai_review")
    ai_review_enabled = isinstance(review, dict) and review.get("overall_verdict") != "disabled"
    if ai_review_enabled:
        core_verdicts = [
            str((review.get(key) or {}).get("verdict") or "not_verifiable")
            for key in ("company_name_review", "target_year_review", "recruitment_type_review")
        ]
        ai_review_core_match = all(value == "agree" for value in core_verdicts)
        ai_review_overall_verdict = str(review.get("overall_verdict") or "not_verifiable")
        review_deadlines = [item for item in review.get("deadline_reviews") or [] if isinstance(item, dict)]
        ai_review_supported_count = sum(item.get("verdict") == "supported" for item in review_deadlines)
        ai_review_problem_count = sum(
            item.get("verdict") in {"unsupported", "conflict", "not_verifiable"}
            for item in review_deadlines
        )
        if not ai_review_core_match:
            warnings.append("抽出AIと検証AIの間で企業名・対象年度・募集区分の一致を確認できません。")
        if ai_review_overall_verdict == "rejected":
            warnings.append("検証AIは抽出結果を棄却しました。")
    else:
        ai_review_core_match = True
        ai_review_overall_verdict = "disabled"
        ai_review_supported_count = 0
        ai_review_problem_count = 0

    warnings = list(dict.fromkeys(warnings))
    passed = all([
        company_match,
        target_year_match,
        recruitment_type_match,
        summary_deadline_ok,
        course_deadlines_ok,
        ai_review_core_match,
        ai_review_overall_verdict != "rejected",
        bool(deadline_iso or deadline_count),
        course_count > 0 if recruitment_type == "インターン" else True,
    ])

    return VerificationResult(
        company_match=company_match,
        target_year_match=target_year_match,
        recruitment_type_match=recruitment_type_match,
        source_url_valid=source_url_valid,
        evidence_in_source=evidence_in_source,
        deadline_in_source=deadline_in_source,
        deadline_context_match=deadline_context_match,
        source_target_year_status=summary_source_year_status,
        deadline_is_future=deadline_is_future,
        official_source_like=official_source_like,
        official_source_verified=official_source_verified,
        supporting_source_count=supporting_source_count,
        course_count=course_count,
        deadline_count=deadline_count,
        verified_deadline_count=verified_deadline_count,
        unverified_deadline_count=unverified_deadline_count,
        sns_deadline_count=sns_deadline_count,
        confirmed_sns_deadline_count=confirmed_sns_deadline_count,
        rejected_sns_deadline_count=rejected_sns_deadline_count,
        ai_review_enabled=ai_review_enabled,
        ai_review_core_match=ai_review_core_match,
        ai_review_overall_verdict=ai_review_overall_verdict,
        ai_review_supported_count=ai_review_supported_count,
        ai_review_problem_count=ai_review_problem_count,
        course_deadline_checks=course_deadline_checks,
        passed=passed,
        warnings=warnings,
    )


def build_degraded_ai_result(
    company: str,
    target_year: int,
    recruitment_type: str,
    reason: str,
) -> dict[str, Any]:
    """統合AIが利用できない場合も、逐次候補とPython検証を表示できる形を作る。"""
    return {
        "company_name": company,
        "industry": [],
        "business_summary": None,
        "target_year": target_year,
        "recruitment_type": recruitment_type,
        "deadline": None,
        "deadline_original": None,
        "deadline_type": None,
        "source_id": None,
        "source_url": None,
        "evidence": None,
        "confidence": 0.0,
        "source_reliability": "other",
        "deadline_status": "not_found",
        "courses": [],
        "missing_information": ["統合AIによる全体抽出"],
        "notes": [reason],
        "_extractor_model_requested": MODEL,
        "_extractor_model": None,
        "_extractor_fallback": False,
        "_extractor_retry_count": 0,
        "_extractor_elapsed_seconds": None,
        "_degraded_mode": True,
    }
