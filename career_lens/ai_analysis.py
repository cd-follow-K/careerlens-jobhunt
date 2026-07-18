"""Whole-document extraction, independent review, adjudication, and result integration."""

from .common import *
from .sources import *
from .research import *
from .ai_client import *
from .storage import *


def community_consensus_for_prompt(
    company: str, target_year: int, recruitment_type: str
) -> tuple[str, list[dict[str, Any]]]:
    """Build an anonymous, advisory-only signal for AI stages."""
    rows = list_community_deadline_consensus(
        company, target_year, recruitment_type, limit=30
    )
    if not rows:
        return "該当する他利用者の確認情報はありません。", []
    lines = []
    for row in rows:
        lines.append(
            " / ".join([
                f"コース: {row.get('course_name') or '未特定'}",
                f"締切: {row.get('deadline') or '不明'}",
                f"他利用者の確認済み: {int(row.get('confirmed_count') or 0)}人",
                f"誤情報判定: {int(row.get('rejected_count') or 0)}人",
                f"URL: {row.get('source_url') or 'なし'}",
            ])
        )
    return "\n".join(lines), rows


def source_material_for_ai(
    source_records: list[dict[str, Any]],
    *,
    max_sources: int = 36,
    max_chars_per_source: int = 3500,
    total_chars: int = 100_000,
) -> str:
    """AI入力を情報源単位・総文字数の両方で制限する。"""
    blocks: list[str] = []
    used_chars = 0
    for record in source_records[:max_sources]:
        body = str(
            record.get("relevant_excerpt") or record.get("source_text", "")
        )[:max_chars_per_source]
        block = "\n".join(
            [
                f"【{record['source_id']}】",
                f"タイトル: {record.get('title', '')}",
                f"URL: {record.get('url', '')}",
                f"情報源種別: {record.get('source_type', '')}",
                f"企業公式性の機械判定: {record.get('official_source_status', '対象外')}",
                f"SNSプラットフォーム: {record.get('social_platform', '')}",
                f"公式アカウント候補判定: {record.get('social_official_candidate', False)}",
                f"検索概要: {str(record.get('snippet', ''))[:500]}",
                "本文（締切・日付周辺を優先した抜粋）:",
                body,
            ]
        )
        if blocks and used_chars + len(block) > total_chars:
            break
        blocks.append(block)
        used_chars += len(block)
    return "\n\n===== 次の情報源 =====\n\n".join(blocks)


def _collect_source_references(
    value: Any, source_ids: set[str], source_urls: set[str]
) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "source_id" and item:
                source_ids.add(str(item))
            elif key == "source_url" and item:
                source_urls.add(normalize_url(str(item)))
            else:
                _collect_source_references(item, source_ids, source_urls)
    elif isinstance(value, list):
        for item in value:
            _collect_source_references(item, source_ids, source_urls)


def select_sources_for_ai_stage(
    source_records: list[dict[str, Any]],
    *payloads: Any,
    max_sources: int,
) -> list[dict[str, Any]]:
    """前段AIが参照した情報源を優先し、不足分を検索順位順で補う。"""
    source_ids: set[str] = set()
    source_urls: set[str] = set()
    for payload in payloads:
        _collect_source_references(payload, source_ids, source_urls)

    selected: list[dict[str, Any]] = []
    selected_urls: set[str] = set()
    for record in source_records:
        record_url = normalize_url(str(record.get("url") or ""))
        if (
            str(record.get("source_id") or "") in source_ids
            or record_url in source_urls
        ):
            selected.append(record)
            selected_urls.add(record_url)
            if len(selected) >= max_sources:
                return selected

    for record in source_records:
        record_url = normalize_url(str(record.get("url") or ""))
        if record_url in selected_urls:
            continue
        selected.append(record)
        selected_urls.add(record_url)
        if len(selected) >= max_sources:
            break
    return selected


def compact_result_for_judge(result: dict[str, Any]) -> dict[str, Any]:
    """裁定に不要な長文を除き、締切・根拠・判定だけを残す。"""
    compact_courses: list[dict[str, Any]] = []
    for course in result.get("courses") or []:
        if not isinstance(course, dict):
            continue
        deadlines = []
        for item in course.get("deadlines") or []:
            if not isinstance(item, dict):
                continue
            deadlines.append({
                key: item.get(key)
                for key in (
                    "deadline", "deadline_original", "deadline_type",
                    "source_id", "source_url", "evidence",
                    "source_reliability", "deadline_status",
                )
            })
        compact_courses.append({
            "course_name": course.get("course_name"),
            "deadlines": deadlines,
        })
    return {
        "company_name": result.get("company_name"),
        "target_year": result.get("target_year"),
        "recruitment_type": result.get("recruitment_type"),
        "courses": compact_courses,
        "notes": list(result.get("notes") or [])[:12],
    }


def ask_ai(
    company: str,
    target_year: int,
    recruitment_type: str,
    source_records: list[dict[str, Any]],
    progressive_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY が設定されていません。")

    client = make_gemini_client(api_key)
    materials = source_material_for_ai(
        source_records,
        max_sources=36,
        max_chars_per_source=3500,
        total_chars=100_000,
    )
    candidate_material = progressive_candidates_for_prompt(progressive_candidates or [])
    community_material, community_rows = community_consensus_for_prompt(
        company, target_year, recruitment_type
    )

    system_instruction = """
あなたは就職活動情報の抽出担当です。
提示された情報源に明記された内容だけを使ってください。
推測、一般知識、別年度の情報による補完は禁止です。
情報源の優先順位は、企業公式サイト・公式採用サイト、マイナビ、ONE CAREER、企業公式SNS候補、その他SNS・就活サイトの順です。
情報源種別が「企業公式候補」でも、企業公式性の機械判定が「自動確認済み」でない限りofficialとして扱ってはいけません。
SNSは速報性のある補助情報源として使用できますが、SNSだけで確認した締切を確定情報にしてはいけません。
SNSのタイトルや検索概要に「公式」とあっても、システム上は公式候補に過ぎないため、必ずユーザー確認が必要です。
SNSから締切を抽出した場合、source_reliabilityはofficial_socialまたはsocial_unverified、deadline_statusは必ずneeds_confirmationとしてください。
他利用者の確認件数は探索漏れを減らす補助信号に限ります。原文の代わりにせず、件数だけで締切を確定・除外してはいけません。
企業公式情報で締切を確認できない場合に限り、マイナビまたはONE CAREERの公開情報を暫定情報として使ってください。
公式サイトとSNSで日付が異なる場合は公式サイトを優先し、不一致をnotesへ明記してください。
対象年度、本選考、インターン、説明会を厳密に区別してください。
締切は応募・提出期限だけを抽出し、開催日や面接日と混同しないでください。
募集区分がインターンの場合、情報源に掲載された全コースを可能な限り列挙してください。
コースごとに締切が異なる場合は、各コースを別々に保持してください。
同一コースに第1次締切・第2次締切など複数の応募期限がある場合は、deadlines配列に全て入れてください。
コース名を確認できない締切を、推測で特定のコースに割り当てないでください。
各締切について、根拠となる短い原文、情報源ID、URLを必ず返してください。
確認できない項目は null にしてください。
必ずJSONだけを返してください。
""".strip()

    user_prompt = f"""
調査対象企業: {company}
対象年度: {target_year}年卒
募集区分: {recruitment_type}

以下の複数情報源を比較し、対象条件に合う情報だけを抽出してください。
--- 情報源開始 ---
{materials}
--- 情報源終了 ---

--- 逐次検査で得た候補開始 ---
{candidate_material}
--- 逐次検査で得た候補終了 ---
上記候補は検索漏れ防止の手掛かりであり、独立した根拠ではありません。必ず対応する情報源本文で再確認し、根拠があるものだけ採用してください。

--- 他利用者による匿名確認の集計開始 ---
{community_material}
--- 他利用者による匿名確認の集計終了 ---
この集計には利用者名や検索履歴は含まれません。根拠本文の探索候補としてのみ考慮してください。

次のJSON形式で返してください。
{{
  "company_name": "情報源中の正式企業名またはnull",
  "industry": ["業界1", "業界2"],
  "business_summary": "情報源に基づく事業内容の要約またはnull",
  "target_year": {target_year},
  "recruitment_type": "本選考、インターン、説明会等またはnull",
  "deadline": "全コースのうち最も早い締切をYYYY-MM-DDで記載、なければnull",
  "deadline_original": "最も早い締切の原文表現またはnull",
  "deadline_type": "最も早い締切の種類またはnull",
  "source_id": "最も早い締切の情報源IDまたはnull",
  "source_url": "最も早い締切のURLまたはnull",
  "evidence": "最も早い締切の根拠原文またはnull",
  "confidence": 0.0,
  "source_reliability": "official、mynavi、onecareer、other、manual、official_social、social_unverifiedのいずれか",
  "deadline_status": "confirmed、provisional、needs_confirmation、not_foundのいずれか",
  "courses": [
    {{
      "course_name": "情報源に記載された正式なコース名",
      "course_summary": "コース内容の短い要約またはnull",
      "eligibility": "対象者・応募条件またはnull",
      "deadlines": [
        {{
          "deadline": "YYYY-MM-DDまたはnull",
          "deadline_original": "締切の原文表現またはnull",
          "deadline_type": "応募締切、ES提出、第1次締切等またはnull",
          "source_id": "S1等またはnull",
          "source_url": "根拠情報源のURLまたはnull",
          "evidence": "そのコースと締切を結び付ける短い原文またはnull",
          "confidence": 0.0,
          "source_reliability": "official、mynavi、onecareer、other、manual、official_social、social_unverifiedのいずれか",
          "deadline_status": "confirmed、provisional、needs_confirmation、not_foundのいずれか",
          "notes": ["年度違い、追加募集、情報源間の不一致等"]
        }}
      ]
    }}
  ],
  "missing_information": ["確認できなかった項目"],
  "notes": ["年度違い、情報源間の不一致、注意点"]
}}
""".strip()

    started_at = time.perf_counter()
    response, used_model, fallback_used, retry_count = generate_content_resilient(
        client,
        primary_model=MODEL,
        fallback_models=[EXTRACT_FALLBACK_MODEL],
        contents=user_prompt,
        schema=AI_RESULT_SCHEMA,
        system_instruction=system_instruction,
        max_output_tokens=20_000,
        thinking_level="low",
    )
    if not response.text:
        raise RuntimeError("Geminiから有効な応答を取得できませんでした。")
    result = postprocess_ai_result(extract_json(response.text))
    result["_extractor_model_requested"] = MODEL
    result["_extractor_model"] = used_model
    result["_extractor_fallback"] = fallback_used
    result["_extractor_retry_count"] = retry_count
    result["_extractor_elapsed_seconds"] = round(time.perf_counter() - started_at, 2)
    result["_extractor_input_chars"] = len(user_prompt)
    result["_community_consensus"] = community_rows
    return result


def ask_ai_review(
    company: str,
    target_year: int,
    recruitment_type: str,
    source_records: list[dict[str, Any]],
    extracted_result: dict[str, Any],
) -> dict[str, Any]:
    """別モデルに原文と抽出結果を独立に照合させる。"""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY が設定されていません。")

    client = make_gemini_client(api_key)
    review_sources = select_sources_for_ai_stage(
        source_records, extracted_result, max_sources=18
    )
    materials = source_material_for_ai(
        review_sources,
        max_sources=18,
        max_chars_per_source=2500,
        total_chars=45_000,
    )
    extractor_json = json.dumps(
        compact_result_for_judge(extracted_result), ensure_ascii=False, indent=2
    )
    community_material, _ = community_consensus_for_prompt(
        company, target_year, recruitment_type
    )
    system_instruction = """
あなたは就職活動情報の検証担当です。抽出担当AIの回答を信用せず、必ず情報源本文を先に確認してください。
企業名、対象年度、募集区分、各コース名、応募締切、締切種別、根拠URLを独立に照合してください。
開催日、面接日、説明会日を応募締切と誤認していないか確認してください。
年度違い、本選考とインターンの混同、コースと締切の誤結合、根拠のない日付を重点的に検査してください。
抽出結果にない締切を原文から発見した場合はmissing_deadlinesへ記載してください。
公式・マイナビ・ONE CAREER・SNSの信頼度差だけで事実を決めず、原文中の明示を優先してください。
他利用者の匿名確認件数は補助信号に限り、原文に根拠がない項目をsupportedにしてはいけません。
推測や一般知識による補完は禁止です。確認不能な場合はnot_verifiableとしてください。
必ずJSONだけを返してください。
""".strip()
    user_prompt = f"""
調査対象企業: {company}
対象年度: {target_year}年卒
募集区分: {recruitment_type}

--- 情報源開始 ---
{materials}
--- 情報源終了 ---

--- 抽出担当AIの結果開始 ---
{extractor_json}
--- 抽出担当AIの結果終了 ---

--- 他利用者による匿名確認の集計開始 ---
{community_material}
--- 他利用者による匿名確認の集計終了 ---

情報源を独立に読み、抽出結果と比較してください。
各締切についてsupported、unsupported、conflict、not_verifiableのいずれかを判定してください。
conflictの場合は、原文で確認できる訂正候補をcorrected_*へ記載してください。
""".strip()

    requested_model = REVIEW_MODEL
    started_at = time.perf_counter()
    response, used_model, fallback_used, retry_count = generate_content_resilient(
        client,
        primary_model=requested_model,
        fallback_models=[MODEL],
        contents=user_prompt,
        schema=AI_REVIEW_SCHEMA,
        system_instruction=system_instruction,
        max_output_tokens=12_000,
        thinking_level="minimal",
    )

    if not response.text:
        raise RuntimeError("検証担当AIから有効な応答を取得できませんでした。")
    review = extract_json(response.text)
    review["_review_model_requested"] = requested_model
    review["_review_model_used"] = used_model
    review["_review_fallback"] = fallback_used
    review["_review_retry_count"] = retry_count
    review["_review_elapsed_seconds"] = round(time.perf_counter() - started_at, 2)
    review["_review_input_chars"] = len(user_prompt)
    review["_review_source_count"] = len(review_sources)
    return review


def ask_ai_judge(
    company: str,
    target_year: int,
    recruitment_type: str,
    source_records: list[dict[str, Any]],
    extracted_result: dict[str, Any],
    reviewer_result: dict[str, Any],
) -> dict[str, Any]:
    """Proモデルが抽出AIと検証AIの不一致を裁定する。"""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY が設定されていません。")

    client = make_gemini_client(api_key)
    judge_sources = select_sources_for_ai_stage(
        source_records, extracted_result, reviewer_result, max_sources=8
    )
    materials = source_material_for_ai(
        judge_sources,
        max_sources=8,
        max_chars_per_source=1800,
        total_chars=16_000,
    )
    extractor_json = json.dumps(
        compact_result_for_judge(extracted_result), ensure_ascii=False, indent=2
    )
    reviewer_json = json.dumps(reviewer_result, ensure_ascii=False, indent=2)
    community_material, _ = community_consensus_for_prompt(
        company, target_year, recruitment_type
    )
    system_instruction = """
あなたは就職活動情報の最終裁定担当です。
抽出担当AIと検証担当AIのどちらも無条件に信用せず、情報源本文を最優先してください。
両AIが一致していても根拠が弱ければnot_verifiableまたはrejectedとしてください。
両AIが不一致の場合は、企業名、対象年度、募集区分、コース名、締切日、締切区分、根拠URLを原文から再確認してください。
開催日、面接日、説明会日を応募締切と誤認しないでください。
公式サイト、マイナビ、ONE CAREER、SNSの順序は信頼度の参考にとどめ、最終的には原文の明示を基準にしてください。
推測や一般知識による補完は禁止です。確認できない場合はnot_verifiableとしてください。
他利用者の匿名確認件数は補助信号に限り、情報源本文より優先してはいけません。
必ずJSONだけを返してください。
""".strip()
    user_prompt = f"""
調査対象企業: {company}
対象年度: {target_year}年卒
募集区分: {recruitment_type}

--- 情報源開始 ---
{materials}
--- 情報源終了 ---

--- 抽出担当AIの結果開始 ---
{extractor_json}
--- 抽出担当AIの結果終了 ---

--- 検証担当AIの結果開始 ---
{reviewer_json}
--- 検証担当AIの結果終了 ---

--- 他利用者による匿名確認の集計開始 ---
{community_material}
--- 他利用者による匿名確認の集計終了 ---

情報源を独立に再確認し、最終裁定を行ってください。
各締切はsupported、unsupported、conflict、not_verifiableのいずれかで判定してください。
訂正できる場合はcorrected_*へ記載し、抽出漏れはmissing_deadlinesへ記載してください。
""".strip()

    requested_model = JUDGE_MODEL
    started_at = time.perf_counter()
    response, used_model, fallback_used, retry_count = generate_content_resilient(
        client,
        primary_model=requested_model,
        fallback_models=[REVIEW_MODEL],
        contents=user_prompt,
        schema=AI_REVIEW_SCHEMA,
        system_instruction=system_instruction,
        max_output_tokens=12_000,
        thinking_level="low",
    )

    if not response.text:
        raise RuntimeError("裁定担当AIから有効な応答を取得できませんでした。")
    judge = extract_json(response.text)
    judge["_judge_model_requested"] = requested_model
    judge["_judge_model_used"] = used_model
    judge["_judge_fallback"] = fallback_used
    judge["_judge_retry_count"] = retry_count
    judge["_judge_elapsed_seconds"] = round(time.perf_counter() - started_at, 2)
    judge["_judge_input_chars"] = len(user_prompt)
    judge["_judge_source_count"] = len(judge_sources)
    return judge


def find_source_by_id_or_url(
    source_id: str | None,
    source_url: str | None,
    source_records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    normalized_url = normalize_url(str(source_url or ""))
    for record in source_records:
        if source_id and str(record.get("source_id") or "") == str(source_id):
            return record
        if normalized_url and normalize_url(str(record.get("url") or "")) == normalized_url:
            return record
    return None


def _review_match_key(course_name: str, deadline: str, source_url: str) -> tuple[str, str, str]:
    return normalize_text(course_name), str(deadline or ""), normalize_url(source_url)


def apply_ai_review(
    ai_result: dict[str, Any],
    review: dict[str, Any],
    source_records: list[dict[str, Any]],
    *,
    role: str = "reviewer",
) -> dict[str, Any]:
    """AIの判断を保持し、否定・矛盾項目を要確認へ降格する。

    role="reviewer" は第2モデル、role="judge" はPro裁定モデルを表す。
    裁定モデルの結果は最終判定として各締切へ上書きする。
    """
    storage_key = "_ai_judge" if role == "judge" else "_ai_review"
    ai_result[storage_key] = review
    role_label = "裁定AI" if role == "judge" else "検証AI"
    notes = list(ai_result.get("notes") or [])

    core_map = {
        "company_name_review": "企業名",
        "target_year_review": "対象年度",
        "recruitment_type_review": "募集区分",
    }
    for key, label in core_map.items():
        item = review.get(key) or {}
        if isinstance(item, dict) and item.get("verdict") in {"disagree", "not_verifiable"}:
            notes.append(f"{role_label}: {label}は{item.get('verdict')}。{item.get('reason') or ''}".strip())

    exact_reviews: dict[tuple[str, str, str], dict[str, Any]] = {}
    loose_reviews: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in review.get("deadline_reviews") or []:
        if not isinstance(item, dict):
            continue
        key = _review_match_key(
            str(item.get("course_name") or ""),
            str(item.get("deadline") or ""),
            str(item.get("source_url") or ""),
        )
        exact_reviews[key] = item
        loose_reviews.setdefault((key[0], key[1]), []).append(item)

    for course in ai_result.get("courses") or []:
        if not isinstance(course, dict):
            continue
        course_name = str(course.get("course_name") or "")
        for deadline_info in course.get("deadlines") or []:
            if not isinstance(deadline_info, dict):
                continue
            key = _review_match_key(
                course_name,
                str(deadline_info.get("deadline") or ""),
                str(deadline_info.get("source_url") or ""),
            )
            review_item = exact_reviews.get(key)
            if review_item is None:
                candidates = loose_reviews.get((key[0], key[1]), [])
                review_item = candidates[0] if len(candidates) == 1 else None
            if review_item is None:
                deadline_info["_ai_review_verdict"] = "not_reviewed"
                deadline_info["_ai_review_reason"] = f"{role_label}の対応項目を特定できませんでした。"
                continue

            verdict = str(review_item.get("verdict") or "not_verifiable")
            deadline_info["_ai_review_verdict"] = verdict
            deadline_info["_ai_review_reason"] = str(review_item.get("reason") or "")
            deadline_info["_ai_review_evidence"] = review_item.get("evidence")
            deadline_info["_ai_review_model"] = review.get("_judge_model_used") if role == "judge" else review.get("_review_model_used")
            if verdict in {"unsupported", "conflict", "not_verifiable"}:
                deadline_info["deadline_status"] = "needs_confirmation"
                deadline_notes = list(deadline_info.get("notes") or [])
                deadline_notes.append(f"{role_label}判定: {verdict}。{review_item.get('reason') or ''}".strip())
                deadline_info["notes"] = list(dict.fromkeys(deadline_notes))

    # 要約締切にも検証AIの判定を反映する（本選考・説明会等を含む）。
    summary_key = _review_match_key(
        str(ai_result.get("recruitment_type") or ""),
        str(ai_result.get("deadline") or ""),
        str(ai_result.get("source_url") or ""),
    )
    summary_review = exact_reviews.get(summary_key)
    if summary_review is None:
        same_deadline_reviews = [
            item for item in review.get("deadline_reviews") or []
            if isinstance(item, dict)
            and str(item.get("deadline") or "") == str(ai_result.get("deadline") or "")
            and (
                not ai_result.get("source_url")
                or normalize_url(str(item.get("source_url") or ""))
                == normalize_url(str(ai_result.get("source_url") or ""))
            )
        ]
        if len(same_deadline_reviews) == 1:
            summary_review = same_deadline_reviews[0]
    if isinstance(summary_review, dict):
        summary_verdict = str(summary_review.get("verdict") or "not_verifiable")
        ai_result["_ai_review_verdict"] = summary_verdict
        ai_result["_ai_review_reason"] = str(summary_review.get("reason") or "")
        ai_result["_ai_review_evidence"] = summary_review.get("evidence")
        ai_result["_ai_review_model"] = review.get("_judge_model_used") if role == "judge" else review.get("_review_model_used")
        if summary_verdict in {"unsupported", "conflict", "not_verifiable"}:
            ai_result["deadline_status"] = "needs_confirmation"

    # 検証AIが原文から発見した未抽出候補は削除せず、要確認候補として追加する。
    course_map = {
        normalize_text(str(course.get("course_name") or "")): course
        for course in ai_result.get("courses") or [] if isinstance(course, dict)
    }
    for missing in review.get("missing_deadlines") or []:
        if not isinstance(missing, dict):
            continue
        deadline = str(missing.get("deadline") or "")
        if parse_deadline(deadline) is None:
            continue
        course_name = str(missing.get("course_name") or "コース名不明").strip() or "コース名不明"
        source_record = find_source_by_id_or_url(
            str(missing.get("source_id") or ""),
            str(missing.get("source_url") or ""),
            source_records,
        )
        source_url = str(missing.get("source_url") or (source_record or {}).get("url") or "")
        course = course_map.setdefault(
            normalize_text(course_name),
            {
                "course_name": course_name,
                "course_summary": None,
                "eligibility": None,
                "deadlines": [],
            },
        )
        existing = any(
            str(item.get("deadline") or "") == deadline
            and normalize_url(str(item.get("source_url") or "")) == normalize_url(source_url)
            for item in course.get("deadlines") or [] if isinstance(item, dict)
        )
        if existing:
            continue
        course.setdefault("deadlines", []).append({
            "deadline": deadline,
            "deadline_original": missing.get("deadline_original"),
            "deadline_type": missing.get("deadline_type"),
            "source_id": missing.get("source_id"),
            "source_url": source_url or None,
            "evidence": missing.get("evidence"),
            "confidence": 0.0,
            "source_reliability": source_reliability_from_record(source_record),
            "deadline_status": "needs_confirmation",
            "notes": [
                f"{role_label}が見落とし候補として提示。Python検証とユーザー確認が必要。",
                str(missing.get("reason") or ""),
            ],
            "_ai_review_verdict": "reviewer_added",
            "_ai_review_reason": str(missing.get("reason") or ""),
            "_ai_review_model": review.get("_judge_model_used") if role == "judge" else review.get("_review_model_used"),
        })

    ai_result["courses"] = list(course_map.values())
    for warning in review.get("warnings") or []:
        notes.append(f"{role_label}: {warning}")
    ai_result["notes"] = list(dict.fromkeys(str(note) for note in notes if str(note).strip()))
    return postprocess_ai_result(ai_result)
