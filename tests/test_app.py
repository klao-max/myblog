import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("ADMIN_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="signal-desk-tests-")
os.environ["DATABASE_PATH"] = str(Path(os.environ["DATA_DIR"]) / "blog.db")

import app


class RequestHeaders(dict):
    def get_all(self, key, default=None):
        value = self.get(key, default)
        return [value] if value is not None else default


def make_handler(path: str, body: bytes, headers: dict[str, str] | None = None):
    handler = app.Handler.__new__(app.Handler)
    handler.path = path
    handler.requestline = f"POST {path} HTTP/1.1"
    handler.request_version = "HTTP/1.1"
    handler.command = "POST"
    handler.client_address = ("127.0.0.1", 12345)
    handler.server = SimpleNamespace(server_name="localhost", server_port=8000)
    handler.headers = RequestHeaders(headers or {})
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    return handler


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

    def test_new_published_post_saves_with_cover_field(self):
        token = "integration-session"
        app.SESSIONS[token] = {"admin_id": "1", "csrf": "integration-csrf"}
        body = urlencode(
            {
                "csrf": "integration-csrf",
                "title": "A new post",
                "slug": "a-new-post",
                "excerpt": "An excerpt",
                "body": "Post body",
                "tags": "testing",
                "cover": "https://example.com/cover.png",
                "status": "published",
            }
        ).encode()

        handler = make_handler(
            "/admin/posts/save",
            body,
            {
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(body)),
                "Cookie": app.build_session_cookie(token),
            },
        )
        app.Handler.do_POST(handler)

        row = self.conn.execute("SELECT cover, status FROM posts WHERE slug = ?", ("a-new-post",)).fetchone()
        self.assertEqual(dict(row), {"cover": "https://example.com/cover.png", "status": "published"})
        response_text = handler.wfile.getvalue().decode("latin-1")
        self.assertIn("303 See Other", response_text)
        self.assertIn("Location: /admin/posts", response_text)

    def test_oversized_request_returns_parseable_413_and_server_remains_available(self):
        body = b"x" * (app.MAX_BODY_BYTES + 1)
        handler = make_handler(
            "/admin/login",
            body,
            {
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(body)),
            },
        )
        app.Handler.do_POST(handler)
        response_text = handler.wfile.getvalue().decode("utf-8")

        follow_up = make_handler("/admin/login", b"")
        follow_up.command = "GET"
        follow_up.requestline = "GET /admin/login HTTP/1.1"
        app.Handler.do_GET(follow_up)
        follow_up_text = follow_up.wfile.getvalue().decode("utf-8")

        self.assertIn("413 Request Entity Too Large", response_text)
        self.assertIn("请求内容过大", response_text)
        self.assertIn("200 OK", follow_up_text)

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
