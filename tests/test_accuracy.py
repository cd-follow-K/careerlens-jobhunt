import unittest
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import app
from career_lens import common


class AccuracyRulesTest(unittest.TestCase):
    def test_google_calendar_url_uses_all_day_when_time_is_missing(self):
        url = app.build_google_calendar_url(
            "例示株式会社",
            "DXコース",
            "2030-08-01",
            "2030年8月1日",
            "応募締切",
            "https://example.com/recruit",
        )
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query["action"], ["TEMPLATE"])
        self.assertEqual(query["dates"], ["20300801/20300802"])
        self.assertEqual(query["ctz"], ["Asia/Tokyo"])
        self.assertIn("[締切] 例示株式会社", query["text"][0])
        self.assertIn("https://example.com/recruit", query["details"][0])

    def test_google_calendar_url_preserves_explicit_deadline_time(self):
        url = app.build_google_calendar_url(
            "例示株式会社",
            "DXコース",
            "2030-08-01",
            "2030年8月1日 17:00締切",
            "応募締切",
            "",
        )
        query = parse_qs(urlparse(url).query)
        self.assertEqual(
            query["dates"],
            ["20300801T170000/20300801T170100"],
        )

    def test_shared_password_comparison(self):
        self.assertTrue(app.access_password_matches("shared-test", "shared-test"))
        self.assertFalse(app.access_password_matches("wrong", "shared-test"))
        self.assertFalse(app.access_password_matches("", ""))

    def test_user_confirmation_promotes_progressive_candidate(self):
        original_db_path = common.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as temporary_directory:
                common.DB_PATH = Path(temporary_directory) / "test.db"
                app.init_db()
                candidate = {
                    "course_name": "DXコース",
                    "deadline": "2030-08-01",
                    "deadline_original": "2030年8月1日",
                    "deadline_type": "応募締切",
                    "source_url": "https://example.com/recruit",
                    "source_type": "その他就活サイト",
                    "source_reliability": "other",
                    "evidence": "応募締切は2030年8月1日です。",
                    "validation_level": "python_hint",
                }
                self.assertEqual(
                    app.manually_confirmed_progressive_options(
                        "例示株式会社", [candidate]
                    ),
                    [],
                )
                app.set_deadline_confirmation(
                    "例示株式会社",
                    "DXコース",
                    "2030-08-01",
                    "https://example.com/recruit",
                    "確認済み",
                )
                options = app.manually_confirmed_progressive_options(
                    "例示株式会社", [candidate]
                )
                self.assertEqual(len(options), 1)
                self.assertTrue(options[0]["verified"])
                self.assertFalse(options[0]["machine_verified"])
                self.assertEqual(options[0]["confirmation_status"], "確認済み")
        finally:
            common.DB_PATH = original_db_path

    def test_portal_is_not_official(self):
        self.assertEqual(
            app.source_type("https://job.career-tasu.jp/corp/00000530/intern"),
            "その他就活サイト",
        )
        self.assertEqual(
            app.source_type("https://typeshukatsu.jp/company/1598/recruitment/583"),
            "その他就活サイト",
        )

    def test_official_source_requires_user_confirmed_domain_and_page_body(self):
        original_db_path = common.DB_PATH
        official = {
            "url": "https://www.softbank.jp/recruit/graduate/internship",
            "title": "ソフトバンク 新卒採用",
            "snippet": "2028年卒向けインターンシップ募集要項",
            "source_text": "ソフトバンク株式会社の新卒採用情報です。",
            "fetch_success": True,
        }
        blog = {
            "url": "https://career-blog.adtechmanagement.com/softbank-intern",
            "title": "ソフトバンクのインターン締切まとめ",
            "snippet": "就活生向け解説",
        }
        try:
            with tempfile.TemporaryDirectory() as temporary_directory:
                common.DB_PATH = Path(temporary_directory) / "test.db"
                app.init_db()
                self.assertFalse(
                    app.is_likely_official_source("ソフトバンク", official)
                )
                app.set_official_domain_confirmation(
                    "ソフトバンク", official["url"], "公式と確認"
                )
                self.assertTrue(
                    app.is_likely_official_source("ソフトバンク", official)
                )
                self.assertFalse(app.is_likely_official_source("ソフトバンク", blog))
        finally:
            common.DB_PATH = original_db_path

    def test_target_year_is_checked_from_source(self):
        self.assertEqual(
            app.source_target_year_status("2028年卒向け新卒採用", 2028), "yes"
        )
        self.assertEqual(
            app.source_target_year_status("2027年卒向け新卒採用", 2028), "no"
        )
        self.assertEqual(
            app.source_target_year_status("新卒採用情報", 2028), "unclear"
        )
        self.assertEqual(
            app.source_target_year_status(
                "https://job.mynavi.jp/28/pc/corpinfo/displayInternship", 2028
            ),
            "yes",
        )
        self.assertEqual(
            app.source_target_year_status(
                "https://www.onecareer.jp/companies/73/experiences/2027/212", 2028
            ),
            "no",
        )

    def test_collection_limits_are_expanded(self):
        self.assertGreaterEqual(app.PROGRESSIVE_MAX_PAGES_STANDARD, 36)
        self.assertGreaterEqual(app.PROGRESSIVE_MAX_PAGES_COMPREHENSIVE, 60)
        self.assertGreaterEqual(app.PROGRESSIVE_MAX_AI_PAGES, 14)
        self.assertGreaterEqual(app.PROGRESSIVE_MAX_AI_PAGES_COMPREHENSIVE, 24)
        self.assertGreaterEqual(app.MAX_PAGE_CHARS, 30_000)
        self.assertGreaterEqual(app.PROGRESSIVE_EXCERPT_CHARS, 6_000)
        self.assertGreaterEqual(app.MAX_SOURCES_FOR_AI, 24)

    def test_ai_material_has_total_budget(self):
        records = [
            {
                "source_id": f"S{i}",
                "title": f"情報源{i}",
                "url": f"https://example.com/{i}",
                "source_type": "その他就活サイト",
                "source_text": "応募締切は2026年8月1日です。" * 500,
            }
            for i in range(20)
        ]
        material = app.source_material_for_ai(
            records,
            max_sources=20,
            max_chars_per_source=1000,
            total_chars=5000,
        )
        self.assertLessEqual(len(material), 6000)
        self.assertIn("【S0】", material)

    def test_later_ai_stage_prioritizes_referenced_sources(self):
        records = [
            {"source_id": "S1", "url": "https://example.com/1"},
            {"source_id": "S2", "url": "https://example.com/2"},
            {"source_id": "S3", "url": "https://example.com/3"},
        ]
        payload = {"courses": [{"deadlines": [{"source_id": "S3"}]}]}
        selected = app.select_sources_for_ai_stage(records, payload, max_sources=2)
        self.assertEqual(selected[0]["source_id"], "S3")
        self.assertEqual(len(selected), 2)

    def test_quota_error_is_detected_and_degraded_result_is_valid(self):
        error = RuntimeError("429 RESOURCE_EXHAUSTED: Quota exceeded")
        self.assertTrue(app._is_quota_exhausted_error(error))
        result = app.build_degraded_ai_result(
            "例示株式会社", 2028, "インターン", "無料枠上限"
        )
        self.assertTrue(result["_degraded_mode"])
        self.assertEqual(result["company_name"], "例示株式会社")
        self.assertEqual(result["courses"], [])

    def test_deadline_context_excludes_event_date(self):
        valid = "2028年卒向けです。エントリーシートの提出期限は2026年7月21日です。"
        event = "2028年卒向けです。インターンシップの開催日は2026年7月21日です。"
        self.assertTrue(
            app.deadline_context_matches(
                valid,
                "2026-07-21",
                "2026年7月21日",
                "エントリーシートの提出期限は2026年7月21日です。",
            )
        )
        self.assertFalse(
            app.deadline_context_matches(
                event,
                "2026-07-21",
                "2026年7月21日",
                "インターンシップの開催日は2026年7月21日です。",
            )
        )

    def test_verified_deadline_needs_year_evidence_and_deadline_context(self):
        source = {
            "source_id": "S1",
            "url": "manual://user-input",
            "source_type": "手動入力",
            "title": "採用情報",
            "snippet": "",
            "fetch_success": True,
            "source_text": (
                "東京建物株式会社では2028年卒を対象とする。"
                "総合職のES応募締切は2026年7月21日である。"
            ),
        }
        item = {
            "deadline": "2026-07-21",
            "deadline_original": "2026年7月21日",
            "deadline_type": "ES応募締切",
            "source_id": "S1",
            "source_url": "manual://user-input",
            "evidence": "総合職のES応募締切は2026年7月21日である。",
            "source_reliability": "manual",
        }
        result = app.verify_one_deadline(
            "東京建物株式会社", 2028, "総合職", item, [source]
        )
        self.assertTrue(result["verified"])
        self.assertEqual(result["source_target_year_status"], "yes")
        self.assertTrue(result["deadline_context_match"])

        source["source_text"] = (
            "東京建物株式会社では2027年卒を対象とする。"
            "インターンの開催日は2026年7月21日である。"
        )
        item["evidence"] = "インターンの開催日は2026年7月21日である。"
        result = app.verify_one_deadline(
            "東京建物株式会社", 2028, "総合職", item, [source]
        )
        self.assertFalse(result["verified"])
        self.assertEqual(result["source_target_year_status"], "no")
        self.assertFalse(result["deadline_context_match"])

    def test_old_history_is_not_automatically_reverified(self):
        item = {
            "deadline": "2026-07-21",
            "deadline_original": "2026年7月21日",
            "deadline_type": "応募締切",
            "source_id": "HISTORY",
            "source_url": "https://example.invalid/recruit",
            "evidence": "応募締切は2026年7月21日",
            "_registry_last_verified": True,
            "_registry_seen_latest": False,
        }
        result = app.verify_one_deadline(
            "例示株式会社", 2028, "総合職", item, []
        )
        self.assertTrue(result["historical_verified"])
        self.assertFalse(result["verified"])

    def test_unconfirmed_official_candidate_is_not_trusted(self):
        source = {
            "source_id": "S1",
            "url": "https://unknown.example/recruit",
            "source_type": "企業公式候補",
            "official_source_verified": False,
            "title": "例示株式会社 新卒採用",
            "snippet": "2028年卒",
            "source_text": "2028年卒の応募締切は2026年8月1日です。",
        }
        item = {
            "deadline": "2026-08-01",
            "deadline_original": "2026年8月1日",
            "deadline_type": "応募締切",
            "source_id": "S1",
            "source_url": source["url"],
            "evidence": source["source_text"],
            "source_reliability": "official",
        }
        result = app.verify_one_deadline(
            "例示株式会社", 2028, "本選考", item, [source]
        )
        self.assertFalse(result["official_source_verified"])
        self.assertFalse(result["verified"])

    def test_wrong_course_deadline_pairing_is_not_machine_verified(self):
        source = {
            "source_id": "S1",
            "url": "manual://user-input",
            "source_type": "手動入力",
            "fetch_success": True,
            "source_text": (
                "2028年卒向けのAコースは、"
                "応募締切を2030年8月1日とする。"
            ),
        }
        item = {
            "deadline": "2030-08-01",
            "deadline_original": "2030年8月1日",
            "deadline_type": "応募締切",
            "source_id": "S1",
            "source_url": "manual://user-input",
            "evidence": "Aコースは、応募締切を2030年8月1日とする。",
        }
        result = app.verify_one_deadline(
            "例示株式会社", 2028, "Bコース", item, [source]
        )
        self.assertFalse(result["course_context_match"])
        self.assertFalse(result["verified"])

    def test_target_year_must_be_near_the_deadline_evidence(self):
        source = {
            "source_id": "S1",
            "url": "manual://user-input",
            "source_type": "手動入力",
            "fetch_success": True,
            "source_text": (
                "2028年卒向けの別コース情報。"
                + "この間には別の説明がある。" * 200
                + "2027年卒向けDXコースの応募締切は2030年8月1日である。"
            ),
        }
        item = {
            "deadline": "2030-08-01",
            "deadline_original": "2030年8月1日",
            "deadline_type": "応募締切",
            "source_id": "S1",
            "source_url": "manual://user-input",
            "evidence": "2027年卒向けDXコースの応募締切は2030年8月1日である。",
        }
        result = app.verify_one_deadline(
            "例示株式会社", 2028, "DXコース", item, [source]
        )
        self.assertEqual(result["source_target_year_status"], "no")
        self.assertFalse(result["verified"])

    def test_search_snippet_cannot_be_machine_verified(self):
        source = {
            "source_id": "S1",
            "url": "https://job.mynavi.jp/28/example",
            "source_type": "マイナビ",
            "fetch_success": False,
            "source_text": (
                "例示株式会社の2028年卒向けDXコースは、"
                "応募締切を2030年8月1日とする。"
            ),
        }
        item = {
            "deadline": "2030-08-01",
            "deadline_original": "2030年8月1日",
            "deadline_type": "応募締切",
            "source_id": "S1",
            "source_url": source["url"],
            "evidence": source["source_text"],
        }
        result = app.verify_one_deadline(
            "例示株式会社", 2028, "DXコース", item, [source]
        )
        self.assertFalse(result["source_body_fetched"])
        self.assertFalse(result["verified"])

    def test_no_deadline_is_not_a_passed_verification(self):
        ai_result = {
            "company_name": "例示株式会社",
            "target_year": 2028,
            "recruitment_type": "本選考",
            "deadline": None,
            "courses": [],
        }
        result = app.verify_result(
            "例示株式会社", 2028, "本選考", [], ai_result
        )
        self.assertEqual(result.deadline_count, 0)
        self.assertFalse(result.passed)
        self.assertTrue(any("応募締切を確認できません" in w for w in result.warnings))


if __name__ == "__main__":
    unittest.main()
