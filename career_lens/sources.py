"""Source classification, web search, page fetching, and deterministic hints."""

from .common import *
from .common import _has_deadline_language
from .storage import *


def is_likely_official_source(company: str, record: dict[str, Any]) -> bool:
    """利用者が確認したドメインだけを企業公式と判定する。

    URLの語彙やページ内の企業名は、公式性を確定する根拠には
    ならない。そのため、ページ本文を取得でき、かつ企業ごとの
    ドメイン確認履歴が「公式と確認」である場合に限る。
    """
    url = str(record.get("url") or record.get("href") or "")
    if source_type(url) != "企業公式候補":
        return False

    host = host_of(url)
    if not host:
        return False
    if record.get("fetch_success") is not True:
        return False
    domain_status = (
        "公式と確認"
        if record.get("official_domain_confirmed") is True
        else get_official_domain_confirmation(company, url)
    )
    if domain_status != "公式と確認":
        return False
    suspicious_host_words = (
        "blog", "media", "magazine", "news", "internship", "shukatsu",
        "syukatsu", "career-blog", "matome", "ranking",
    )
    if any(word in host for word in suspicious_host_words):
        return False

    title = str(record.get("title") or "")
    snippet = str(record.get("snippet") or record.get("body") or "")
    source_text = str(record.get("source_text") or "")[:5000]
    page_text = normalize_text(f"{title} {snippet} {source_text}")
    company_present = any(
        normalize_text(token) in page_text for token in company_tokens(company)
    )
    if not company_present:
        return False

    parsed = urlparse(url)
    route_text = normalize_text(f"{host} {parsed.path}")
    recruiting_path = any(
        word in route_text
        for word in (
            "recruit", "career", "graduate", "newgraduate", "saiyo",
            "採用", "新卒", "インターン", "募集要項", "entry",
        )
    )
    official_word = any(
        word in page_text for word in ("公式採用", "採用公式", "公式サイト")
    )
    return company_present and (recruiting_path or official_word)


def refresh_official_source_flags(
    company: str, source_records: list[dict[str, Any]]
) -> None:
    """保存されたドメイン確認状態を現在の情報源一覧へ反映する。"""
    for record in source_records:
        url = str(record.get("url") or "")
        category = str(record.get("source_type") or source_type(url))
        domain_status = (
            get_official_domain_confirmation(company, url)
            if category == "企業公式候補"
            else "対象外"
        )
        record["official_domain_status"] = domain_status
        record["official_domain_confirmed"] = domain_status == "公式と確認"
        record["official_source_verified"] = bool(company) and is_likely_official_source(
            company, record
        )
        record["official_source_status"] = (
            "ドメイン・本文確認済み"
            if record["official_source_verified"]
            else (domain_status if category == "企業公式候補" else "対象外")
        )


def build_search_query_groups(
    company: str, target_year: int, recruitment_type: str
) -> tuple[list[str], list[str]]:
    """検索文を主要情報源とSNSに分け、重複しやすい検索文を削減する。"""
    year_short = str(target_year)[-2:]

    if recruitment_type == "インターン":
        official_queries = [
            f'"{company}" 新卒採用 公式サイト インターン',
            f'"{company}" {target_year}卒 インターン コース 締切 公式',
            f'"{company}" {year_short}卒 インターン 募集要項 コース一覧',
            f'"{company}" オープン・カンパニー 仕事体験 応募締切',
            f'"{company}" インターン 募集要項 filetype:pdf',
        ]
        portal_queries = [
            f'site:job.mynavi.jp "{company}" インターン コース 締切',
            f'site:job.mynavi.jp "{company}" {year_short}卒 仕事体験',
            f'site:onecareer.jp "{company}" インターン コース 締切',
            f'site:onecareer.jp "{company}" インターン 募集一覧',
        ]
        social_queries = [
            f'site:x.com "{company}" インターン 締切 公式',
            f'site:instagram.com "{company}" インターン 応募 公式',
            f'site:note.com "{company}" 採用 インターン 締切',
        ]
    elif recruitment_type == "説明会":
        official_queries = [
            f'"{company}" 新卒採用 公式サイト 説明会',
            f'"{company}" {target_year}卒 説明会 予約 公式',
            f'"{company}" 新卒採用 説明会 公式',
        ]
        portal_queries = [
            f'site:job.mynavi.jp "{company}" 説明会 {target_year}',
            f'site:onecareer.jp "{company}" 説明会 {target_year}',
        ]
        social_queries = [
            f'site:x.com "{company}" 説明会 予約 公式',
            f'site:instagram.com "{company}" 説明会 新卒採用',
        ]
    else:
        official_queries = [
            f'"{company}" 新卒採用 公式サイト 募集要項',
            f'"{company}" {target_year}卒 {recruitment_type} 締切 公式',
            f'"{company}" {year_short}卒 新卒採用 募集要項',
            f'"{company}" 新卒採用 エントリー 公式',
            f'"{company}" 新卒採用 募集要項 filetype:pdf',
        ]
        portal_queries = [
            f'site:job.mynavi.jp "{company}" {target_year} {recruitment_type}',
            f'site:job.mynavi.jp "{company}" {year_short}卒 締切',
            f'site:onecareer.jp "{company}" {target_year}卒 {recruitment_type}',
            f'site:onecareer.jp "{company}" 締切 エントリー',
        ]
        social_queries = [
            f'site:x.com "{company}" {target_year}卒 {recruitment_type} 締切',
            f'site:instagram.com "{company}" {recruitment_type} 応募締切 公式',
            f'site:note.com "{company}" 採用 {recruitment_type} 締切',
        ]

    return official_queries + portal_queries, social_queries


def build_search_queries(company: str, target_year: int, recruitment_type: str) -> list[str]:
    """旧関数名との互換性を保つ。"""
    primary, social = build_search_query_groups(company, target_year, recruitment_type)
    return primary + social


def build_deep_search_query_groups(
    company: str, target_year: int, recruitment_type: str
) -> tuple[list[str], list[str]]:
    """収集漏れ対策用の深掘り検索文。PDF、マイページ、締切表現の揺れを拾う。"""
    short = str(target_year)[-2:]
    common = [
        f'"{company}" {target_year}卒 採用スケジュール',
        f'"{company}" {short}卒 募集コース 一覧',
        f'"{company}" "応募締切" {target_year}',
        f'"{company}" "提出期限" {short}卒',
        f'"{company}" "エントリー期限" 新卒',
        f'"{company}" filetype:pdf 新卒 採用 募集要項',
        f'"{company}" 採用 マイページ 締切',
        f'"{company}" recruit graduate entry deadline',
    ]
    if recruitment_type == "インターン":
        common.extend([
            f'"{company}" {target_year}卒 インターン 募集コース',
            f'"{company}" {short}卒 インターン マイページ エントリー',
            f'"{company}" インターン "コース" "締切"',
            f'"{company}" 仕事体験 オープンカンパニー 応募期限',
            f'"{company}" 夏季 冬季 インターン エントリー',
            f'"{company}" インターンシップ 第1次 第2次 締切',
        ])
    elif recruitment_type == "本選考":
        common.extend([
            f'"{company}" {target_year}卒 本選考 募集職種',
            f'"{company}" {short}卒 本選考 マイページ エントリー',
            f'"{company}" 本選考 ES 締切',
            f'"{company}" 新卒採用 エントリーシート 提出期限',
            f'"{company}" 適性検査 受検期限 新卒',
        ])
    else:
        common.append(f'"{company}" {recruitment_type} 予約期限')

    portal = [
        f'site:job.mynavi.jp "{company}" "応募締切"',
        f'site:job.mynavi.jp "{company}" "提出期限"',
        f'site:onecareer.jp "{company}" "応募締切"',
        f'site:onecareer.jp "{company}" "エントリー期限"',
        f'site:gaishishukatsu.com "{company}" 締切',
        f'site:syukatsu-kaigi.jp "{company}" 締切',
    ]
    social = [
        f'site:x.com "{company}" "締切" 採用',
        f'site:instagram.com "{company}" "締切" 採用',
        f'site:facebook.com "{company}" 新卒 採用 締切',
        f'site:note.com "{company}" 採用 応募期限',
        f'site:youtube.com "{company}" 新卒採用 インターン',
    ]
    return common + portal, social


def score_search_result(
    company: str,
    target_year: int,
    recruitment_type: str,
    result: dict[str, str],
) -> int:
    title = result.get("title", "")
    body = result.get("body", "")
    url = result.get("href", "")
    haystack = normalize_text(f"{title} {body} {url}")
    score = 0

    for token in company_tokens(company):
        if normalize_text(token) in haystack:
            score += 6

    if any(normalize_text(keyword) in haystack for keyword in RECRUIT_KEYWORDS):
        score += 4

    year_values = (str(target_year), f"{str(target_year)[-2:]}卒", f"{target_year}卒")
    if any(normalize_text(value) in haystack for value in year_values):
        score += 4

    if normalize_text(recruitment_type) in haystack:
        score += 3

    category = source_type(url)
    if category == "企業公式候補":
        # 公式と確認済みのドメインは検索順位で優先する。
        # 最終的な公式性判定は、本文取得後に改めて行う。
        score += (
            12
            if get_official_domain_confirmation(company, url) == "公式と確認"
            else 3
        )
    elif category == "マイナビ":
        score += 11
    elif category == "ONE CAREER":
        score += 10
    elif category == "その他就活サイト":
        score += 2
    elif category == "SNS":
        score += 3
        if is_social_official_candidate(company, title, body):
            score += 5
        if any(word in haystack for word in ("締切", "応募", "エントリー", "募集")):
            score += 2

    if url.lower().endswith(".pdf"):
        score += 2

    return score


def _run_single_search(query: str, max_results: int) -> tuple[str, list[dict[str, Any]]]:
    try:
        with DDGS(timeout=SEARCH_TIMEOUT_SECONDS) as ddgs:
            results = list(
                ddgs.text(
                    query,
                    region="jp-jp",
                    safesearch="moderate",
                    max_results=max_results,
                    backend="auto",
                )
                or []
            )
        return query, [result for result in results if isinstance(result, dict)]
    except Exception:
        return query, []


def _run_search_batch(
    queries: list[str], max_results: int = 6
) -> list[tuple[str, list[dict[str, Any]]]]:
    if not queries:
        return []
    workers = min(SEARCH_WORKERS, len(queries))
    outputs: list[tuple[str, list[dict[str, Any]]]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(_run_single_search, query, max_results): query
            for query in queries
        }
        for future in as_completed(future_map):
            try:
                outputs.append(future.result())
            except Exception:
                outputs.append((future_map[future], []))
    return outputs


def _merge_search_outputs(
    found: dict[str, dict[str, Any]],
    outputs: list[tuple[str, list[dict[str, Any]]]],
    company: str,
    target_year: int,
    recruitment_type: str,
) -> None:
    for query, results in outputs:
        for result in results:
            url = normalize_url(str(result.get("href", "")))
            if not url.startswith(("http://", "https://")):
                continue
            category = source_type(url)
            score = score_search_result(company, target_year, recruitment_type, result)
            current = found.get(url)
            record = {
                "title": result.get("title", ""),
                "url": url,
                "snippet": result.get("body", ""),
                "search_query": query,
                "score": score,
                "source_type": category,
                "portal_source": category in {"マイナビ", "ONE CAREER", "その他就活サイト"},
                "social_platform": social_platform(url),
                "social_official_candidate": (
                    category == "SNS"
                    and is_social_official_candidate(
                        company, str(result.get("title", "")), str(result.get("body", ""))
                    )
                ),
            }
            record["official_domain_status"] = (
                get_official_domain_confirmation(company, url)
                if category == "企業公式候補"
                else "対象外"
            )
            record["official_domain_confirmed"] = (
                record["official_domain_status"] == "公式と確認"
            )
            record["official_source_verified"] = is_likely_official_source(
                company, record
            )
            if current is None or score > int(current.get("score", -999)):
                found[url] = record


def _primary_results_sufficient(found: dict[str, dict[str, Any]]) -> bool:
    values = list(found.values())
    categories = {str(item.get("source_type")) for item in values}
    deadline_hint_count = sum(
        1
        for item in values
        if any(
            word in normalize_text(
                f"{item.get('title', '')} {item.get('snippet', '')}"
            )
            for word in ("締切", "応募期限", "提出期限", "エントリー")
        )
    )
    has_official = any(
        item.get("source_type") == "企業公式候補"
        and bool(item.get("official_domain_confirmed"))
        for item in values
    )
    has_portal = bool(categories & {"マイナビ", "ONE CAREER"})
    return len(values) >= 6 and has_official and has_portal and deadline_hint_count >= 2


def _select_ranked_sources(
    ranked: list[dict[str, Any]], include_social: bool
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    quotas = {
        "企業公式候補": 3,
        "マイナビ": 2,
        "ONE CAREER": 2,
        "その他就活サイト": 1,
    }
    if include_social:
        quotas["SNS"] = 3

    for category, quota in quotas.items():
        candidates = [r for r in ranked if r.get("source_type") == category]
        selected.extend(candidates[:quota])

    seen = {r["url"] for r in selected}
    for record in ranked:
        if len(selected) >= MAX_SOURCES_FOR_AI:
            break
        if record["url"] not in seen:
            if not include_social and record.get("source_type") == "SNS":
                continue
            selected.append(record)
            seen.add(record["url"])
    return selected[:MAX_SOURCES_FOR_AI]


def search_web(
    company: str,
    target_year: int,
    recruitment_type: str,
    strategy: str = "standard",
    force_refresh: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    started = time.perf_counter()
    if not force_refresh:
        cached = load_search_cache(company, target_year, recruitment_type, strategy)
        if cached:
            return cached, {
                "search_cache_hit": True,
                "query_count": 0,
                "social_searched": any(r.get("source_type") == "SNS" for r in cached),
                "candidate_count": len(cached),
                "selected_count": len(cached),
                "search_seconds": round(time.perf_counter() - started, 2),
                "strategy": strategy,
            }

    primary_queries, social_queries = build_search_query_groups(
        company, target_year, recruitment_type
    )
    found: dict[str, dict[str, Any]] = {}
    primary_outputs = _run_search_batch(primary_queries, max_results=6)
    _merge_search_outputs(
        found, primary_outputs, company, target_year, recruitment_type
    )
    query_count = len(primary_queries)

    if strategy == "comprehensive":
        search_social = True
    elif strategy == "fast":
        search_social = False
    else:
        search_social = not _primary_results_sufficient(found)

    if search_social:
        social_outputs = _run_search_batch(social_queries, max_results=5)
        _merge_search_outputs(
            found, social_outputs, company, target_year, recruitment_type
        )
        query_count += len(social_queries)

    ranked = sorted(found.values(), key=lambda item: int(item["score"]), reverse=True)
    if not ranked:
        raise RuntimeError("検索結果を取得できませんでした。時間をおいて再実行してください。")

    selected = _select_ranked_sources(ranked, include_social=search_social)
    save_search_cache(company, target_year, recruitment_type, strategy, selected)
    return selected, {
        "search_cache_hit": False,
        "query_count": query_count,
        "social_searched": search_social,
        "candidate_count": len(ranked),
        "selected_count": len(selected),
        "search_seconds": round(time.perf_counter() - started, 2),
        "strategy": strategy,
    }


def fetch_source_text(
    url: str, category: str, force_refresh: bool = False
) -> tuple[str, bool, bool]:
    """本文、取得成功、キャッシュ利用の順で返す。"""
    if not force_refresh:
        cached = load_page_cache(url)
        if cached is not None:
            content, success = cached
            return content, success, True

    try:
        with DDGS(timeout=FETCH_TIMEOUT_SECONDS) as ddgs:
            extracted = ddgs.extract(url, fmt="text_plain")
        content = extracted.get("content", "") if isinstance(extracted, dict) else ""
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="ignore")
        content = re.sub(r"\n{3,}", "\n\n", str(content)).strip()[:MAX_PAGE_CHARS]
        success = len(content) >= 80
        save_page_cache(url, content, success)
        return content, success, False
    except Exception:
        save_page_cache(url, "", False)
        return "", False, False


def _enrich_one_source(
    index: int, record: dict[str, Any], force_refresh: bool, company: str = ""
) -> tuple[int, dict[str, Any], bool]:
    # ドメイン分類規則を更新した場合も、古い検索キャッシュを新基準で再分類する。
    category = source_type(str(record.get("url") or ""))
    display_category = category
    if category == "SNS":
        display_category = (
            "SNS（公式候補）"
            if record.get("social_official_candidate")
            else "SNS（未確認）"
        )
    page_text, fetch_success, cache_hit = fetch_source_text(
        str(record["url"]), display_category, force_refresh=force_refresh
    )
    fallback = str(record.get("snippet", ""))
    source_text = page_text if fetch_success else fallback
    enriched = {
        **record,
        "source_id": f"S{index}",
        "source_type": display_category,
        "source_text": source_text,
        "fetch_success": fetch_success,
        "page_cache_hit": cache_hit,
    }
    enriched["official_domain_status"] = (
        get_official_domain_confirmation(company, str(record.get("url") or ""))
        if company and category == "企業公式候補"
        else "対象外"
    )
    enriched["official_domain_confirmed"] = (
        enriched["official_domain_status"] == "公式と確認"
    )
    enriched["official_source_verified"] = bool(company) and is_likely_official_source(
        company, enriched
    )
    enriched["official_source_status"] = (
        "ドメイン・本文確認済み"
        if enriched["official_source_verified"]
        else (
            enriched["official_domain_status"]
            if category == "企業公式候補"
            else "対象外"
        )
    )
    return index, enriched, cache_hit


def enrich_sources(
    search_results: list[dict[str, Any]], force_refresh: bool = False,
    company: str = "",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    started = time.perf_counter()
    records = search_results[:MAX_SOURCES_FOR_AI]
    if not records:
        return [], {
            "page_cache_hits": 0,
            "fresh_page_fetches": 0,
            "failed_pages": 0,
            "fetch_seconds": 0.0,
        }

    workers = min(FETCH_WORKERS, len(records))
    completed: list[tuple[int, dict[str, Any], bool]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(_enrich_one_source, index, record, force_refresh, company)
            for index, record in enumerate(records, start=1)
        ]
        for future in as_completed(futures):
            try:
                completed.append(future.result())
            except Exception:
                continue

    completed.sort(key=lambda item: item[0])
    enriched = [item[1] for item in completed]
    cache_hits = sum(1 for _, _, hit in completed if hit)
    failed_pages = sum(1 for record in enriched if not record.get("fetch_success"))
    return enriched, {
        "page_cache_hits": cache_hits,
        "fresh_page_fetches": len(enriched) - cache_hits,
        "failed_pages": failed_pages,
        "fetch_seconds": round(time.perf_counter() - started, 2),
    }


def extract_relevant_excerpt(text: str, max_chars: int = PROGRESSIVE_EXCERPT_CHARS) -> str:
    """締切語・日付の周辺を優先して抽出し、AIへ渡す情報量を抑える。"""
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return ""
    spans: list[tuple[int, int]] = []
    lower = text.lower()
    anchors: list[int] = []
    for word in DEADLINE_WORDS + ("インターン", "本選考", "コース", "募集要項", "エントリーシート"):
        start = 0
        while True:
            index = lower.find(word.lower(), start)
            if index < 0:
                break
            anchors.append(index)
            start = index + max(1, len(word))
    anchors.extend(match.start() for match in DATE_PATTERN.finditer(text))
    for index in sorted(set(anchors))[:30]:
        spans.append((max(0, index - 180), min(len(text), index + 320)))
    if not spans:
        return text[:max_chars]
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1] + 40:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    excerpt = " … ".join(text[start:end] for start, end in merged)
    return excerpt[:max_chars]


def _parse_japanese_date(raw: str, target_year: int) -> str | None:
    value = raw.strip().replace("：", ":")
    match = re.search(r"(?:(20\d{2})[年./-]\s*)?(\d{1,2})[月./-]\s*(\d{1,2})", value)
    if not match:
        return None
    year = int(match.group(1)) if match.group(1) else None
    if year is None:
        # 年の省略は誤判定の危険があるため、日付候補としては保持するがISO化しない。
        return None
    try:
        return date(year, int(match.group(2)), int(match.group(3))).isoformat()
    except ValueError:
        return None


def python_deadline_hints(
    company: str, target_year: int, recruitment_type: str, record: dict[str, Any]
) -> list[dict[str, Any]]:
    text = str(record.get("source_text") or "")
    hints: list[dict[str, Any]] = []
    for match in DATE_PATTERN.finditer(text):
        start = max(0, match.start() - 140)
        end = min(len(text), match.end() + 180)
        context = re.sub(r"\s+", " ", text[start:end]).strip()
        normalized = normalize_text(context)
        if not any(normalize_text(word) in normalized for word in DEADLINE_WORDS):
            continue
        raw = match.group(0)
        hints.append({
            "course_name": None,
            "deadline": _parse_japanese_date(raw, target_year),
            "deadline_original": raw,
            "deadline_type": "締切候補（Python抽出）",
            "evidence": context,
            "confidence": 0.35,
            "source_id": record.get("source_id"),
            "source_url": record.get("url"),
            "source_type": record.get("source_type"),
            "validation_level": "python_hint",
            "method": "正規表現・周辺語検査",
        })
    return hints[:8]


def source_needs_ai_extraction(
    company: str, target_year: int, recruitment_type: str, record: dict[str, Any]
) -> bool:
    text = normalize_text(
        f"{record.get('title', '')} {record.get('snippet', '')} "
        f"{record.get('relevant_excerpt', '')} {record.get('source_text', '')[:1200]}"
    )
    company_ok = any(normalize_text(token) in text for token in company_tokens(company))
    deadline_ok = any(normalize_text(word) in text for word in DEADLINE_WORDS)
    recruit_ok = normalize_text(recruitment_type) in text or any(
        normalize_text(word) in text for word in ("採用", "新卒", "インターン", "募集")
    )
    return company_ok and recruit_ok and (deadline_ok or record.get("source_type") in {"企業公式候補", "マイナビ", "ONE CAREER"})


def create_manual_source(source_text: str) -> list[dict[str, Any]]:
    return [
        {
            "source_id": "S1",
            "title": "ユーザーが貼り付けた採用情報",
            "url": "manual://user-input",
            "snippet": source_text[:300],
            "source_text": source_text,
            "score": 0,
            "portal_source": False,
            "source_type": "手動入力",
            "fetch_success": True,
            "search_query": "手動入力",
        }
    ]


def source_reliability_from_record(record: dict[str, Any] | None) -> str:
    if not record:
        return "other"
    label = str(record.get("source_type") or "")
    if label == "企業公式候補":
        return "official" if record.get("official_source_verified") else "other"
    if label == "マイナビ":
        return "mynavi"
    if label == "ONE CAREER":
        return "onecareer"
    if label == "手動入力":
        return "manual"
    if label.startswith("SNS"):
        return "official_social" if record.get("social_official_candidate") else "social_unverified"
    return "other"

