"""CareerLens shared configuration, schemas, models, and pure helpers."""

import hashlib
import hmac
import json
import os
import random
import re
import sqlite3
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo
from urllib.parse import urlencode, urlparse

import streamlit as st
from ddgs import DDGS
from dotenv import load_dotenv
from google import genai
from google.genai import types
from z3 import If, Int, Optimize, Sum, sat

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BASE_DIR = PROJECT_ROOT
load_dotenv(PROJECT_ROOT / ".env", override=True)
DB_PATH = PROJECT_ROOT / "jobhunt.db"
CACHE_DB_LOCK = threading.Lock()


MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")


REVIEW_MODEL = os.getenv("GEMINI_REVIEW_MODEL", "gemini-3.1-flash-lite")


JUDGE_MODEL = os.getenv("GEMINI_JUDGE_MODEL", "gemini-2.5-pro")


AI_REQUEST_TIMEOUT_MS = int(os.getenv("GEMINI_REQUEST_TIMEOUT_MS", "60000"))


GEMINI_RETRY_ATTEMPTS = max(1, int(os.getenv("GEMINI_RETRY_ATTEMPTS", "3")))


GEMINI_RETRY_BASE_SECONDS = max(0.5, float(os.getenv("GEMINI_RETRY_BASE_SECONDS", "2")))


EXTRACT_FALLBACK_MODEL = os.getenv("GEMINI_EXTRACT_FALLBACK_MODEL", REVIEW_MODEL)


MAX_PAGE_CHARS = 30_000


MAX_SOURCES_FOR_AI = 24


SEARCH_CACHE_TTL_HOURS = 12


PAGE_CACHE_TTL_HOURS = 24


FAILED_PAGE_CACHE_TTL_HOURS = 1


SEARCH_WORKERS = 4


FETCH_WORKERS = 4


SEARCH_TIMEOUT_SECONDS = 8


FETCH_TIMEOUT_SECONDS = 8


PROGRESSIVE_SEARCH_WORKERS = max(2, int(os.getenv("PROGRESSIVE_SEARCH_WORKERS", "6")))


PROGRESSIVE_FETCH_WORKERS = max(2, int(os.getenv("PROGRESSIVE_FETCH_WORKERS", "6")))


PROGRESSIVE_AI_WORKERS = max(1, int(os.getenv("PROGRESSIVE_AI_WORKERS", "2")))


PROGRESSIVE_MAX_PAGES_STANDARD = max(18, int(os.getenv("PROGRESSIVE_MAX_PAGES_STANDARD", "36")))


PROGRESSIVE_MAX_PAGES_COMPREHENSIVE = max(24, int(os.getenv("PROGRESSIVE_MAX_PAGES_COMPREHENSIVE", "60")))


PROGRESSIVE_MAX_AI_PAGES = max(4, int(os.getenv("PROGRESSIVE_MAX_AI_PAGES", "14")))


PROGRESSIVE_MAX_AI_PAGES_COMPREHENSIVE = max(
    PROGRESSIVE_MAX_AI_PAGES,
    int(os.getenv("PROGRESSIVE_MAX_AI_PAGES_COMPREHENSIVE", "24")),
)


PROGRESSIVE_EXCERPT_CHARS = max(
    1800, int(os.getenv("PROGRESSIVE_EXCERPT_CHARS", "6000"))
)


SOURCE_EXTRACTION_CACHE_TTL_HOURS = 72


DEADLINE_WORDS = (
    "締切", "締め切り", "応募期限", "提出期限", "受付期限", "エントリー期限",
    "応募期間", "エントリー期間", "必着", "までに", "受付終了", "募集終了"
)


DATE_PATTERN = re.compile(
    r"(?:20\d{2}[年./-]\s*)?\d{1,2}[月./-]\s*\d{1,2}日?(?:\s*[（(]?[月火水木金土日][）)]?)?(?:\s*\d{1,2}[:：]\d{2})?"
)


TRUSTED_PORTAL_DOMAINS = {
    "job.mynavi.jp": "マイナビ",
    "onecareer.jp": "ONE CAREER",
}


OTHER_PORTAL_DOMAINS = {
    "rikunabi.com",
    "gaishishukatsu.com",
    "openwork.jp",
    "jobtalk.jp",
    "careerpark-agent.jp",
    "syukatsu-kaigi.jp",
    "unistyleinc.com",
    "wantedly.com",
    "indeed.com",
    "career-tasu.jp",
    "typeshukatsu.jp",
    "internshipguide.jp",
    "en-courage.com",
    "recme.jp",
    "reashu.com",
    "digmee.jp",
    "nikki.ne.jp",
    "careermine.jp",
    "campuscareer.jp",
    "simenavi.com",
    "job-commit.com",
    "noahs-ark.co.jp",
    "adtechmanagement.com",
    "shukatsu-ichiba.com",
    "shukatsu-magazine.com",
    "shukatsu-venture.com",
    "renew-career.com",
    "job-q.me",
    "intelli-gorilla.com",
    "careerbrain.jp",
    "tenshoku.asiro.co.jp",
    "prtimes.jp",
    "nikkei.com",
    "yahoo.co.jp",
    "pinterest.com",
    "cutestat.com",
    "poiintter.com",
    "jposting.net",
    "japancafeeikaiwa.com",
}


SOCIAL_DOMAINS = {
    "x.com",
    "twitter.com",
    "instagram.com",
    "facebook.com",
    "tiktok.com",
    "youtube.com",
    "youtu.be",
    "note.com",
    "ameblo.jp",
    "threads.net",
}


RECRUIT_KEYWORDS = ("採用", "新卒", "募集", "エントリー", "recruit", "career", "graduate")


AI_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "company_name": {"type": ["string", "null"]},
        "industry": {"type": "array", "items": {"type": "string"}},
        "business_summary": {"type": ["string", "null"]},
        "target_year": {"type": ["integer", "null"]},
        "recruitment_type": {"type": ["string", "null"]},
        # 従来機能との互換性のため、最も早い締切を要約値として保持する。
        "deadline": {"type": ["string", "null"], "format": "date"},
        "deadline_original": {"type": ["string", "null"]},
        "deadline_type": {"type": ["string", "null"]},
        "source_id": {"type": ["string", "null"]},
        "source_url": {"type": ["string", "null"]},
        "evidence": {"type": ["string", "null"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "source_reliability": {
            "type": "string",
            "enum": [
                "official", "mynavi", "onecareer", "other", "manual",
                "official_social", "social_unverified"
            ],
        },
        "deadline_status": {
            "type": "string",
            "enum": ["confirmed", "provisional", "needs_confirmation", "not_found"],
        },
        # インターン等で複数コース・複数締切を保持する。
        "courses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "course_name": {"type": "string"},
                    "course_summary": {"type": ["string", "null"]},
                    "eligibility": {"type": ["string", "null"]},
                    "deadlines": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "deadline": {"type": ["string", "null"], "format": "date"},
                                "deadline_original": {"type": ["string", "null"]},
                                "deadline_type": {"type": ["string", "null"]},
                                "source_id": {"type": ["string", "null"]},
                                "source_url": {"type": ["string", "null"]},
                                "evidence": {"type": ["string", "null"]},
                                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                                "source_reliability": {
                                    "type": "string",
                                    "enum": [
                                        "official", "mynavi", "onecareer", "other", "manual",
                                        "official_social", "social_unverified"
                                    ],
                                },
                                "deadline_status": {
                                    "type": "string",
                                    "enum": ["confirmed", "provisional", "needs_confirmation", "not_found"],
                                },
                                "notes": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": [
                                "deadline", "deadline_original", "deadline_type",
                                "source_id", "source_url", "evidence", "confidence",
                                "source_reliability", "deadline_status", "notes"
                            ],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["course_name", "course_summary", "eligibility", "deadlines"],
                "additionalProperties": False,
            },
        },
        "missing_information": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "company_name", "industry", "business_summary", "target_year",
        "recruitment_type", "deadline", "deadline_original", "deadline_type",
        "source_id", "source_url", "evidence", "confidence",
        "source_reliability", "deadline_status", "courses",
        "missing_information", "notes"
    ],
    "additionalProperties": False,
}


SOURCE_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "page_relevant": {"type": "boolean"},
        "company_match": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "target_year_match": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "recruitment_type_match": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "deadlines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "course_name": {"type": ["string", "null"]},
                    "deadline": {"type": ["string", "null"], "format": "date"},
                    "deadline_original": {"type": ["string", "null"]},
                    "deadline_type": {"type": ["string", "null"]},
                    "evidence": {"type": ["string", "null"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": [
                    "course_name", "deadline", "deadline_original",
                    "deadline_type", "evidence", "confidence"
                ],
                "additionalProperties": False,
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "page_relevant", "company_match", "target_year_match",
        "recruitment_type_match", "deadlines", "warnings"
    ],
    "additionalProperties": False,
}


AI_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "overall_verdict": {
            "type": "string",
            "enum": ["approved", "approved_with_warnings", "rejected"],
        },
        "company_name_review": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["agree", "disagree", "not_verifiable"]},
                "corrected_value": {"type": ["string", "null"]},
                "reason": {"type": "string"},
            },
            "required": ["verdict", "corrected_value", "reason"],
            "additionalProperties": False,
        },
        "target_year_review": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["agree", "disagree", "not_verifiable"]},
                "corrected_value": {"type": ["integer", "null"]},
                "reason": {"type": "string"},
            },
            "required": ["verdict", "corrected_value", "reason"],
            "additionalProperties": False,
        },
        "recruitment_type_review": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["agree", "disagree", "not_verifiable"]},
                "corrected_value": {"type": ["string", "null"]},
                "reason": {"type": "string"},
            },
            "required": ["verdict", "corrected_value", "reason"],
            "additionalProperties": False,
        },
        "deadline_reviews": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "course_name": {"type": "string"},
                    "deadline": {"type": ["string", "null"], "format": "date"},
                    "deadline_type": {"type": ["string", "null"]},
                    "source_id": {"type": ["string", "null"]},
                    "source_url": {"type": ["string", "null"]},
                    "verdict": {
                        "type": "string",
                        "enum": ["supported", "unsupported", "conflict", "not_verifiable"],
                    },
                    "corrected_course_name": {"type": ["string", "null"]},
                    "corrected_deadline": {"type": ["string", "null"], "format": "date"},
                    "corrected_deadline_type": {"type": ["string", "null"]},
                    "evidence": {"type": ["string", "null"]},
                    "reason": {"type": "string"},
                },
                "required": [
                    "course_name", "deadline", "deadline_type", "source_id", "source_url",
                    "verdict", "corrected_course_name", "corrected_deadline",
                    "corrected_deadline_type", "evidence", "reason"
                ],
                "additionalProperties": False,
            },
        },
        "missing_deadlines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "course_name": {"type": "string"},
                    "deadline": {"type": "string", "format": "date"},
                    "deadline_original": {"type": ["string", "null"]},
                    "deadline_type": {"type": ["string", "null"]},
                    "source_id": {"type": ["string", "null"]},
                    "source_url": {"type": ["string", "null"]},
                    "evidence": {"type": ["string", "null"]},
                    "reason": {"type": "string"},
                },
                "required": [
                    "course_name", "deadline", "deadline_original", "deadline_type",
                    "source_id", "source_url", "evidence", "reason"
                ],
                "additionalProperties": False,
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": [
        "overall_verdict", "company_name_review", "target_year_review",
        "recruitment_type_review", "deadline_reviews", "missing_deadlines",
        "warnings", "summary"
    ],
    "additionalProperties": False,
}


TASK_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "minItems": 3,
            "maxItems": 7,
            "items": {
                "type": "object",
                "properties": {
                    "task_name": {"type": "string"},
                    "description": {"type": "string"},
                    "duration_minutes": {"type": "integer", "minimum": 30, "maximum": 240},
                    "order": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["task_name", "description", "duration_minutes", "order"],
                "additionalProperties": False,
            },
        },
        "reasoning_summary": {"type": "string"},
    },
    "required": ["tasks", "reasoning_summary"],
    "additionalProperties": False,
}


WEEKDAY_LABELS = {
    "月": 0, "火": 1, "水": 2, "木": 3, "金": 4, "土": 5, "日": 6
}


@dataclass
class VerificationResult:
    company_match: bool
    target_year_match: bool
    recruitment_type_match: bool
    source_url_valid: bool
    evidence_in_source: bool
    deadline_in_source: bool
    deadline_context_match: bool
    source_target_year_status: str
    deadline_is_future: bool | None
    official_source_like: bool
    official_source_verified: bool
    supporting_source_count: int
    course_count: int
    deadline_count: int
    verified_deadline_count: int
    unverified_deadline_count: int
    sns_deadline_count: int
    confirmed_sns_deadline_count: int
    rejected_sns_deadline_count: int
    ai_review_enabled: bool
    ai_review_core_match: bool
    ai_review_overall_verdict: str
    ai_review_supported_count: int
    ai_review_problem_count: int
    course_deadline_checks: list[dict[str, Any]]
    passed: bool
    warnings: list[str]


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def normalize_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def extract_json(text: str) -> dict[str, Any]:
    """GeminiのJSON応答を辞書として安全に取り出す。"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("AI応答からJSONオブジェクトを取得できませんでした。")
        parsed = json.loads(match.group(0))

    # モデルが誤って [{...}] の形で返した場合は、1件目の辞書を採用する。
    if isinstance(parsed, list):
        dict_items = [item for item in parsed if isinstance(item, dict)]
        if len(dict_items) == 1:
            parsed = dict_items[0]
        elif len(dict_items) > 1:
            # 本システムは企業1社につき結果1件を想定しているため、
            # 最も項目数が多い辞書を採用する。
            parsed = max(dict_items, key=len)
        else:
            raise ValueError("AI応答がJSON配列でしたが、辞書形式の結果が含まれていません。")

    if not isinstance(parsed, dict):
        raise ValueError(
            f"AI応答の最上位形式が不正です。期待: object、実際: {type(parsed).__name__}"
        )
    return parsed


def postprocess_ai_result(ai_result: dict[str, Any]) -> dict[str, Any]:
    """コースと締切を重複排除し、最も早い締切をPython側で再計算する。"""
    courses = ai_result.get("courses") or []
    if not isinstance(courses, list):
        ai_result["courses"] = []
        return ai_result

    merged: dict[str, dict[str, Any]] = {}
    for course in courses:
        if not isinstance(course, dict):
            continue
        course_name = str(course.get("course_name") or "").strip()
        if not course_name:
            continue
        key = normalize_text(course_name)
        target = merged.setdefault(
            key,
            {
                "course_name": course_name,
                "course_summary": course.get("course_summary"),
                "eligibility": course.get("eligibility"),
                "deadlines": [],
            },
        )
        if not target.get("course_summary") and course.get("course_summary"):
            target["course_summary"] = course.get("course_summary")
        if not target.get("eligibility") and course.get("eligibility"):
            target["eligibility"] = course.get("eligibility")

        existing_keys = {
            (
                str(item.get("deadline") or ""),
                normalize_text(str(item.get("deadline_type") or "")),
                normalize_url(str(item.get("source_url") or "")),
            )
            for item in target["deadlines"]
            if isinstance(item, dict)
        }
        for item in course.get("deadlines") or []:
            if not isinstance(item, dict):
                continue
            item_key = (
                str(item.get("deadline") or ""),
                normalize_text(str(item.get("deadline_type") or "")),
                normalize_url(str(item.get("source_url") or "")),
            )
            if item_key not in existing_keys:
                target["deadlines"].append(item)
                existing_keys.add(item_key)

    cleaned_courses = list(merged.values())
    for course in cleaned_courses:
        course["deadlines"].sort(
            key=lambda item: (str(item.get("deadline") or "9999-12-31"), str(item.get("deadline_type") or ""))
        )
    cleaned_courses.sort(
        key=lambda course: (
            str((course.get("deadlines") or [{}])[0].get("deadline") or "9999-12-31"),
            str(course.get("course_name") or ""),
        )
    )
    ai_result["courses"] = cleaned_courses

    # インターンでは、全コースのうち最も早い締切を要約欄へ設定する。
    if normalize_text(str(ai_result.get("recruitment_type") or "")) == normalize_text("インターン"):
        dated_items: list[dict[str, Any]] = []
        for course in cleaned_courses:
            for item in course.get("deadlines") or []:
                if isinstance(item, dict) and parse_deadline(item.get("deadline")) is not None:
                    dated_items.append(item)
        if dated_items:
            future_items = [
                item for item in dated_items
                if (parse_deadline(item.get("deadline")) or date.min) >= date.today()
            ]
            earliest = min(future_items or dated_items, key=lambda item: str(item.get("deadline")))
            for field in (
                "deadline", "deadline_original", "deadline_type", "source_id",
                "source_url", "evidence", "confidence", "source_reliability",
                "deadline_status", "_registry_first_seen", "_registry_last_seen",
                "_registry_seen_count", "_registry_seen_latest", "_registry_last_verified",
                "_ai_review_verdict", "_ai_review_reason", "_ai_review_evidence", "_ai_review_model",
            ):
                ai_result[field] = earliest.get(field)
        else:
            for field in (
                "deadline", "deadline_original", "deadline_type", "source_id",
                "source_url", "evidence",
            ):
                ai_result[field] = None
            ai_result["deadline_status"] = "not_found"
    return ai_result


def company_tokens(company: str) -> list[str]:
    cleaned = company
    for word in ("株式会社", "有限会社", "合同会社", "ホールディングス", "グループ"):
        cleaned = cleaned.replace(word, "")
    tokens = [cleaned.strip(), company.strip()]
    return [token for token in dict.fromkeys(tokens) if len(token) >= 2]


def host_of(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def domain_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def source_type(url: str) -> str:
    host = host_of(url)
    for domain, label in TRUSTED_PORTAL_DOMAINS.items():
        if domain_matches(host, domain):
            return label
    if any(domain_matches(host, domain) for domain in SOCIAL_DOMAINS):
        return "SNS"
    if any(domain_matches(host, domain) for domain in OTHER_PORTAL_DOMAINS):
        return "その他就活サイト"
    return "企業公式候補"


def source_target_year_status(text: str, target_year: int) -> str:
    """原文中の卒業年度表現をyes/no/unclearで返す。"""
    normalized = normalize_text(text)
    short = str(target_year)[-2:]
    explicit_years: set[int] = set()
    for match in re.finditer(
        r"(20\d{2})(?:年度|年)?(?:卒|新卒|採用|入社|3月卒業)", normalized
    ):
        explicit_years.add(int(match.group(1)))
    for match in re.finditer(r"(?<!\d)(\d{2})卒", normalized):
        value = int(match.group(1))
        if 20 <= value <= 40:
            explicit_years.add(2000 + value)
    if explicit_years:
        if explicit_years == {target_year}:
            return "yes"
        # 対象年度と他年度が同じ根拠範囲にある場合は自動確定しない。
        return "unclear" if target_year in explicit_years else "no"

    target_markers = (
        f"{target_year}卒", f"{target_year}年卒", f"{short}卒",
        f"{target_year}年度新卒", f"{target_year}年新卒",
        f"{target_year}年度採用", f"{target_year}年3月卒業",
        f"{target_year}年4月入社", f"{target_year}年度入社",
    )
    if any(normalize_text(marker) in normalized for marker in target_markers):
        return "yes"

    mynavi_match = re.search(r"job\.mynavi\.jp/(\d{2})/", normalized)
    if mynavi_match:
        return "yes" if int(mynavi_match.group(1)) == int(short) else "no"
    onecareer_match = re.search(
        r"onecareer\.jp/.{0,80}/(20\d{2})(?:/|$)", normalized
    )
    if onecareer_match:
        return "yes" if int(onecareer_match.group(1)) == target_year else "no"
    if normalize_text(f"マイナビ{target_year}") in normalized:
        return "yes"

    return "unclear"


def _has_deadline_language(text: str) -> bool:
    normalized = normalize_text(text)
    if any(normalize_text(word) in normalized for word in DEADLINE_WORDS):
        return True
    return bool(re.search(
        r"(?:応募|提出|エントリー|受付|予約).{0,24}(?:まで|期限|締切|終了)",
        normalized,
    ))


def deadline_context_matches(
    source_text: str,
    deadline_iso: str,
    deadline_original: str,
    evidence: str,
) -> bool:
    """日付が単に存在するだけでなく、締切を表す文脈にあるか確認する。"""
    source_normalized = normalize_text(source_text)
    if not source_normalized:
        return False

    variants = []
    if deadline_original:
        variants.append(normalize_text(deadline_original))
    if deadline_iso:
        variants.extend(normalize_text(value) for value in date_variants(deadline_iso))
    variants = [value for value in dict.fromkeys(variants) if value]
    if not variants:
        return False

    evidence_normalized = normalize_text(evidence)
    if (
        evidence_normalized
        and evidence_normalized in source_normalized
        and _has_deadline_language(evidence)
        and any(value in evidence_normalized for value in variants)
    ):
        return True

    for value in variants:
        start = 0
        while True:
            index = source_normalized.find(value, start)
            if index < 0:
                break
            window = source_normalized[max(0, index - 120):index + len(value) + 120]
            if _has_deadline_language(window):
                return True
            start = index + max(1, len(value))
    return False


GENERIC_COURSE_NAMES = {
    "", "選考", "本選考", "インターン", "インターンシップ",
    "コース名不明", "コース未特定", "未特定", "全コース共通", "共通",
}


def deadline_evidence_window(
    source_text: str,
    deadline_iso: str,
    deadline_original: str,
    evidence: str,
    radius: int = 800,
) -> str:
    """締切の根拠文または日付を中心とする局所文脈を返す。"""
    source_normalized = normalize_text(source_text)
    if not source_normalized:
        return ""

    anchors: list[tuple[int, int]] = []
    evidence_normalized = normalize_text(evidence)
    if evidence_normalized:
        index = source_normalized.find(evidence_normalized)
        if index >= 0:
            anchors.append((index, len(evidence_normalized)))

    variants: list[str] = []
    if deadline_original:
        variants.append(normalize_text(deadline_original))
    if deadline_iso:
        variants.extend(normalize_text(value) for value in date_variants(deadline_iso))
    for value in dict.fromkeys(value for value in variants if value):
        start = 0
        while True:
            index = source_normalized.find(value, start)
            if index < 0:
                break
            anchors.append((index, len(value)))
            start = index + max(1, len(value))

    if not anchors:
        return ""

    # 締切語を含む窓を優先し、複数日付の取り違えを抑える。
    windows = [
        source_normalized[max(0, index - radius):index + length + radius]
        for index, length in anchors
    ]
    for window in windows:
        if _has_deadline_language(window):
            return window
    return windows[0]


def course_name_matches_context(course_name: str, context: str) -> bool:
    """構造化したコース名が締切の局所文脈にあるか確認する。"""
    normalized_course = normalize_text(course_name)
    normalized_context = normalize_text(context)
    if normalized_course in {normalize_text(value) for value in GENERIC_COURSE_NAMES}:
        return True
    if normalized_course and normalized_course in normalized_context:
        return True

    reduced = normalized_course
    for word in (
        "インターンシップ", "インターン", "プログラム", "コース",
        "オープンカンパニー", "仕事体験",
    ):
        reduced = reduced.replace(normalize_text(word), "")
    return len(reduced) >= 2 and reduced in normalized_context


def deadline_local_evidence_checks(
    source_text: str,
    source_url: str,
    target_year: int,
    course_name: str,
    deadline_iso: str,
    deadline_original: str,
    evidence: str,
) -> dict[str, Any]:
    """同一の根拠範囲で年度・コース・締切文脈を確認する。"""
    window = deadline_evidence_window(
        source_text, deadline_iso, deadline_original, evidence
    )
    local_year_status = source_target_year_status(window, target_year)
    # マイナビとONE CAREERはURL自体に対象年度を含む場合がある。
    if local_year_status == "unclear" and source_type(source_url) in {
        "マイナビ", "ONE CAREER"
    }:
        url_year_status = source_target_year_status(source_url, target_year)
        if url_year_status != "unclear":
            local_year_status = url_year_status
    return {
        "context": window,
        "target_year_status": local_year_status,
        "course_match": course_name_matches_context(course_name, window),
        "deadline_context_match": bool(window and _has_deadline_language(window)),
    }


def social_platform(url: str) -> str | None:
    host = host_of(url)
    mapping = {
        "x.com": "X",
        "twitter.com": "X",
        "instagram.com": "Instagram",
        "facebook.com": "Facebook",
        "youtube.com": "YouTube",
        "youtu.be": "YouTube",
        "tiktok.com": "TikTok",
        "note.com": "note",
        "ameblo.jp": "Ameba",
        "threads.net": "Threads",
    }
    for domain, label in mapping.items():
        if domain_matches(host, domain):
            return label
    return None


def is_social_official_candidate(company: str, title: str, snippet: str) -> bool:
    haystack = normalize_text(f"{title} {snippet}")
    company_present = any(normalize_text(token) in haystack for token in company_tokens(company))
    official_word = any(word in haystack for word in ("公式", "official", "採用公式", "新卒採用"))
    return company_present and official_word


def parse_deadline(value: Any) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def date_variants(deadline_iso: str) -> list[str]:
    parsed = parse_deadline(deadline_iso)
    if parsed is None:
        return []
    return [
        deadline_iso,
        f"{parsed.year}年{parsed.month}月{parsed.day}日",
        f"{parsed.year}/{parsed.month}/{parsed.day}",
        f"{parsed.month}月{parsed.day}日",
    ]


def find_selected_source(
    ai_result: dict[str, Any], source_records: list[dict[str, Any]]
) -> dict[str, Any] | None:
    source_id = str(ai_result.get("source_id") or "")
    source_url = normalize_url(str(ai_result.get("source_url") or ""))
    for record in source_records:
        if source_id and record.get("source_id") == source_id:
            return record
        if source_url and normalize_url(str(record.get("url", ""))) == source_url:
            return record
    return None


def iter_course_deadlines(ai_result: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """全コースの全締切を、(コース名, 締切情報) の形で返す。"""
    result: list[tuple[str, dict[str, Any]]] = []
    courses = ai_result.get("courses") or []
    if not isinstance(courses, list):
        return result
    for course in courses:
        if not isinstance(course, dict):
            continue
        course_name = str(course.get("course_name") or "コース名不明")
        deadlines = course.get("deadlines") or []
        if not isinstance(deadlines, list):
            continue
        for deadline_info in deadlines:
            if isinstance(deadline_info, dict):
                result.append((course_name, deadline_info))
    return result
