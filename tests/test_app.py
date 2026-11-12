import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("ADMIN_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="signal-desk-tests-")
os.environ["DATABASE_PATH"] = str(Path(os.environ["DATA_DIR"]) / "blog.db")

import app


class SignalDeskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = Path(os.environ["DATA_DIR"])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data_dir, ignore_errors=True)

    def setUp(self):
        app.SESSIONS.clear()
        if hasattr(app, "LOGIN_FAILURES"):
            app.LOGIN_FAILURES.clear()
        conn = app.db()
        conn.execute("DELETE FROM posts")
        conn.execute("DELETE FROM projects")
        conn.execute("DELETE FROM games")
        conn.commit()
        self.conn = conn

    def tearDown(self):
        self.conn.close()

    def test_production_configuration_rejects_missing_secrets(self):
        with self.assertRaises(ValueError):
            app.validate_runtime_config("production", None, None)

    def test_admin_password_can_be_rotated(self):
        app.rotate_admin_password(self.conn, "admin", "test-password", "new-test-password")
        row = self.conn.execute("SELECT password_hash FROM admins WHERE username='admin'").fetchone()
        self.assertTrue(app.password_matches("new-test-password", row["password_hash"]))

    def test_session_cookie_is_signed_and_tampering_is_rejected(self):
        token = "session-id"
        cookie = app.build_session_cookie(token, "production")
        signed = cookie.split("=", 1)[1].split(";", 1)[0]
        self.assertEqual(app.verify_session_cookie(signed), token)
        self.assertIsNone(app.verify_session_cookie(signed + "x"))
        self.assertIn("Secure", cookie)

    def test_external_urls_require_http_or_https(self):
        self.assertEqual(app.validate_external_url("https://example.com/a"), "https://example.com/a")
        with self.assertRaises(ValueError):
            app.validate_external_url("javascript:alert(1)")

    def test_slugs_cannot_escape_route_segments(self):
        self.assertEqual(app.validate_slug("hello-world"), "hello-world")
        with self.assertRaises(ValueError):
            app.validate_slug("../admin")

    def test_archived_project_is_not_publicly_rendered(self):
        now = app.now_iso()
        self.conn.execute(
            "INSERT INTO projects(name, slug, summary, description, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("Archived", "archived", "old", "old", "archived", now, now),
        )
        self.conn.commit()
        status, _ = app.detail(self.conn, "projects", "archived")
        self.assertEqual(status, 404)

    def test_publish_timestamp_tracks_publish_and_retract_transitions(self):
        self.assertEqual(app.next_published_at("draft", None, "published", "T1"), "T1")
        self.assertEqual(app.next_published_at("published", "T1", "published", "T2"), "T1")
        self.assertIsNone(app.next_published_at("published", "T1", "draft", "T2"))
        self.assertEqual(app.next_published_at("draft", "T1", "published", "T2"), "T2")

    def test_published_game_requires_a_bundle(self):
        with self.assertRaises(ValueError):
            app.validate_game_bundle("missing-game", "published")
        self.assertEqual(app.validate_game_bundle("signal-runner", "published"), "signal-runner")

    def test_oversized_form_is_rejected(self):
        with self.assertRaises(app.RequestTooLarge):
            app.parse_form_data("x" * (app.MAX_BODY_BYTES + 1), app.MAX_BODY_BYTES + 1)

    def test_login_failures_are_rate_limited(self):
        for now in range(app.LOGIN_MAX_ATTEMPTS):
            app.record_login_failure("127.0.0.1", float(now))
        self.assertTrue(app.is_login_rate_limited("127.0.0.1", 10.0))
        self.assertFalse(app.is_login_rate_limited("127.0.0.2", 10.0))
        self.assertFalse(app.is_login_rate_limited("127.0.0.1", app.LOGIN_WINDOW_SECONDS + 1))

    def test_seed_and_markdown_basics(self):
        self.conn.execute(
            "INSERT INTO posts(title, slug, excerpt, body, status, published_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("Post", "post", "Excerpt", "<script>alert(1)</script>", "published", app.now_iso(), app.now_iso(), app.now_iso()),
        )
        self.conn.commit()
        self.assertNotIn("<script>", app.markdown("<script>alert(1)</script>"))


if __name__ == "__main__":
    unittest.main()
