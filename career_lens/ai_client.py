"""Gemini client configuration, retries, fallbacks, and error handling."""

from .common import *


def make_gemini_client(api_key: str) -> genai.Client:
    """Geminiクライアントを、明示的なタイムアウトと単回試行で作成する。"""
    try:
        return genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=AI_REQUEST_TIMEOUT_MS,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
    except Exception:
        # 古いSDKでも動作できるよう、HttpOptions非対応時は通常クライアントへ戻す。
        return genai.Client(api_key=api_key)


def build_generation_config(
    model: str,
    schema: dict[str, Any],
    *,
    system_instruction: str,
    max_output_tokens: int,
    thinking_level: str = "low",
) -> types.GenerateContentConfig:
    """構造化出力用設定を生成する。Gemini 3では低思考量を優先する。"""
    base_kwargs: dict[str, Any] = {
        "system_instruction": system_instruction,
        "response_mime_type": "application/json",
        "response_json_schema": schema,
        "max_output_tokens": max_output_tokens,
    }
    if model.startswith("gemini-3"):
        try:
            base_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_level=thinking_level
            )
        except Exception:
            pass
    elif model.startswith("gemini-2.5-pro"):
        # 最終裁定では無制限の思考を避け、応答時間を抑える。
        try:
            base_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_budget=2048
            )
        except Exception:
            pass
    return types.GenerateContentConfig(**base_kwargs)


def _gemini_error_status_code(exc: Exception) -> int | None:
    """Google Gen AI SDK例外からHTTPステータス相当値を安全に取得する。"""
    for attr in ("status_code", "code"):
        value = getattr(exc, attr, None)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass
    match = re.search(r"\b(4\d{2}|5\d{2})\b", str(exc))
    return int(match.group(1)) if match else None


def _is_retryable_gemini_error(exc: Exception) -> bool:
    status = _gemini_error_status_code(exc)
    message = str(exc).upper()
    return status in {429, 500, 502, 503, 504} or any(
        token in message
        for token in ("UNAVAILABLE", "RESOURCE_EXHAUSTED", "DEADLINE_EXCEEDED", "HIGH DEMAND")
    )


def _is_fallbackable_gemini_error(exc: Exception) -> bool:
    status = _gemini_error_status_code(exc)
    return _is_retryable_gemini_error(exc) or status == 404


def _is_quota_exhausted_error(exc: Exception) -> bool:
    status = _gemini_error_status_code(exc)
    message = str(exc).upper()
    return status == 429 or "RESOURCE_EXHAUSTED" in message or "QUOTA EXCEEDED" in message


def gemini_error_summary(exc: Exception, stage: str) -> str:
    if _is_quota_exhausted_error(exc):
        return (
            f"{stage}はGemini無料枠の一時的な入力上限に達したため省略しました。"
            "既に得られたAI結果とPython検証で処理を継続します。"
        )
    if _is_retryable_gemini_error(exc):
        return (
            f"{stage}はAIサービスの一時的な混雑またはタイムアウトにより省略しました。"
            "既に得られた結果で処理を継続します。"
        )
    return f"{stage}を完了できませんでした: {type(exc).__name__}"


def generate_content_resilient(
    client: genai.Client,
    *,
    primary_model: str,
    fallback_models: list[str] | tuple[str, ...],
    contents: str,
    schema: dict[str, Any],
    system_instruction: str,
    max_output_tokens: int,
    thinking_level: str,
) -> tuple[Any, str, bool, int]:
    """一時障害だけを再試行し、429では同じ長文を連続送信しない。"""
    models: list[str] = []
    for candidate in (primary_model, *fallback_models):
        candidate = str(candidate or "").strip()
        if candidate and candidate not in models:
            models.append(candidate)

    last_error: Exception | None = None
    retry_count = 0
    for model_index, candidate in enumerate(models):
        for attempt in range(GEMINI_RETRY_ATTEMPTS):
            try:
                response = client.models.generate_content(
                    model=candidate,
                    contents=contents,
                    config=build_generation_config(
                        candidate,
                        schema,
                        system_instruction=system_instruction,
                        max_output_tokens=max_output_tokens,
                        thinking_level=thinking_level,
                    ),
                )
                return response, candidate, model_index > 0, retry_count
            except Exception as exc:
                last_error = exc
                if _is_quota_exhausted_error(exc):
                    # TPM超過時の同一モデル再試行は上限を悪化させるため、
                    # 直ちに次モデルへ移る。次モデルも失敗した場合は呼出元で縮退する。
                    break
                retryable = _is_retryable_gemini_error(exc)
                if retryable and attempt < GEMINI_RETRY_ATTEMPTS - 1:
                    delay = GEMINI_RETRY_BASE_SECONDS * (2 ** attempt) + random.uniform(0.0, 0.5)
                    time.sleep(delay)
                    retry_count += 1
                    continue
                if not _is_fallbackable_gemini_error(exc):
                    raise
                break

    if last_error is not None:
        raise last_error
    raise RuntimeError("利用可能なGeminiモデルを呼び出せませんでした。")


def review_requires_pro_judge(review: dict[str, Any]) -> bool:
    """独立検証で不一致・根拠不足がある場合だけPro裁定を要求する。"""
    if str(review.get("overall_verdict") or "") != "approved":
        return True
    for key in (
        "company_name_review",
        "target_year_review",
        "recruitment_type_review",
    ):
        item = review.get(key) or {}
        if isinstance(item, dict) and str(item.get("verdict") or "") != "agree":
            return True
    for item in review.get("deadline_reviews") or []:
        if isinstance(item, dict) and str(item.get("verdict") or "") != "supported":
            return True
    if review.get("missing_deadlines"):
        return True
    if review.get("warnings"):
        return True
    return False

