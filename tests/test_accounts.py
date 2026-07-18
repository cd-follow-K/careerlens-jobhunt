import sqlite3
import tempfile
import unittest
from pathlib import Path

import app
from career_lens import common


class AccountIsolationTest(unittest.TestCase):
    def setUp(self):
        self.original_db_path = common.DB_PATH
        self.temporary_directory = tempfile.TemporaryDirectory()
        common.DB_PATH = Path(self.temporary_directory.name) / "accounts.db"
        app.init_db()
        app.init_auth_db()

    def tearDown(self):
        common.DB_PATH = self.original_db_path
        self.temporary_directory.cleanup()

    def _account(self, username: str) -> dict:
        return app.create_account(username, username, "long-password-123")

    def test_password_is_hashed_and_login_works(self):
        user = self._account("student_a")
        authenticated = app.authenticate_user("student_a", "long-password-123")
        self.assertEqual(authenticated["user_id"], user["user_id"])
        self.assertIsNone(app.authenticate_user("student_a", "wrong-password"))

        with sqlite3.connect(common.DB_PATH) as conn:
            row = conn.execute(
                "SELECT password_salt, password_hash FROM users WHERE user_id = ?",
                (user["user_id"],),
            ).fetchone()
        self.assertNotIn("long-password-123", row)
        self.assertNotEqual(row[0], row[1])

    def test_short_password_is_allowed_but_empty_password_is_rejected(self):
        user = app.create_account("short_pass", "短いパスワード", "1")
        authenticated = app.authenticate_user("short_pass", "1")
        self.assertEqual(authenticated["user_id"], user["user_id"])
        with self.assertRaises(ValueError):
            app.create_account("empty_pass", "空欄", "")

    def test_email_style_login_has_no_character_or_length_rule(self):
        email = "Taro.Yamamoto+CareerLens@example.com"
        user = app.create_account(email, "山本", "x")
        authenticated = app.authenticate_user(email.lower(), "x")
        self.assertEqual(authenticated["user_id"], user["user_id"])
        with self.assertRaises(ValueError):
            app.create_account("", "空欄", "x")

    def test_personal_confirmation_is_isolated_and_community_is_anonymous(self):
        first = self._account("student_a")
        second = self._account("student_b")
        company = "例示株式会社"
        course = "DXコース"
        deadline = "2030-08-01"
        source_url = "https://example.com/recruit"

        app.set_current_user(first["user_id"])
        app.set_research_scope(2030, "インターン")
        app.set_deadline_confirmation(
            company, course, deadline, source_url, "確認済み"
        )
        self.assertEqual(
            app.get_deadline_confirmation(company, course, deadline, source_url),
            "確認済み",
        )

        app.set_current_user(second["user_id"])
        app.set_research_scope(2030, "インターン")
        self.assertEqual(
            app.get_deadline_confirmation(company, course, deadline, source_url),
            "未確認",
        )
        consensus = app.get_community_deadline_consensus(
            company, 2030, "インターン", course, deadline, source_url
        )
        self.assertEqual(consensus["confirmed_count"], 1)
        self.assertEqual(consensus["rejected_count"], 0)
        rows = app.list_community_deadline_consensus(
            company, 2030, "インターン"
        )
        self.assertEqual(len(rows), 1)
        self.assertNotIn("user_id", rows[0])
        self.assertNotIn("username", rows[0])
        prompt, _ = app.community_consensus_for_prompt(
            company, 2030, "インターン"
        )
        self.assertIn("確認済み: 1人", prompt)
        self.assertNotIn("student_a", prompt)

        candidate = {
            "course_name": course,
            "deadline": deadline,
            "deadline_original": "2030年8月1日",
            "deadline_type": "応募締切",
            "source_url": source_url,
            "source_type": "その他就活サイト",
            "source_reliability": "other",
            "evidence": "応募締切は2030年8月1日です。",
            "validation_level": "python_hint",
        }
        self.assertEqual(
            app.manually_confirmed_progressive_options(company, [candidate]),
            [],
            "他利用者の確認だけで本人のカレンダー候補にしてはいけない",
        )

    def test_official_domain_confirmation_is_personal(self):
        first = self._account("student_a")
        second = self._account("student_b")
        url = "https://example.com/recruit"

        app.set_current_user(first["user_id"])
        app.set_official_domain_confirmation("例示株式会社", url, "公式と確認")
        self.assertEqual(
            app.get_official_domain_confirmation("例示株式会社", url),
            "公式と確認",
        )

        app.set_current_user(second["user_id"])
        self.assertEqual(
            app.get_official_domain_confirmation("例示株式会社", url),
            "未確認",
        )

    def test_search_cache_is_personal_but_public_page_cache_can_be_shared(self):
        first = self._account("student_a")
        second = self._account("student_b")
        cached_results = [{"url": "https://example.com/recruit", "title": "採用"}]

        app.set_current_user(first["user_id"])
        app.save_search_cache(
            "例示株式会社", 2030, "本選考", "standard", cached_results
        )
        app.save_page_cache("https://example.com/recruit", "公開本文", True)

        app.set_current_user(second["user_id"])
        self.assertIsNone(
            app.load_search_cache("例示株式会社", 2030, "本選考", "standard")
        )
        shared_page = app.load_page_cache("https://example.com/recruit")
        self.assertEqual(shared_page, ("公開本文", True))


if __name__ == "__main__":
    unittest.main()
