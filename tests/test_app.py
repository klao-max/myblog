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
        conn.execute("DELETE FROM site_images")
        conn.execute("DELETE FROM site_gallery_settings")
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

    def test_gallery_rejects_unsupported_type_and_oversized_file(self):
        with self.assertRaises(ValueError):
            app.validate_gallery_upload("script.svg", "image/svg+xml", b"svg")
        with self.assertRaises(ValueError):
            app.validate_gallery_upload("large.jpg", "image/jpeg", b"x" * (app.MAX_MEDIA_BYTES + 1))

    def test_gallery_images_are_ordered_and_filter_disabled_rows(self):
        now = app.now_iso()
        self.conn.execute("INSERT INTO site_images(stored_name, original_name, mime_type, size, sort_order, enabled, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", ("b.jpg", "b.jpg", "image/jpeg", 1, 2, 1, now))
        self.conn.execute("INSERT INTO site_images(stored_name, original_name, mime_type, size, sort_order, enabled, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", ("a.jpg", "a.jpg", "image/jpeg", 1, 1, 1, now))
        self.conn.execute("INSERT INTO site_images(stored_name, original_name, mime_type, size, sort_order, enabled, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", ("off.jpg", "off.jpg", "image/jpeg", 1, 0, 0, now))
        self.conn.commit()
        self.assertEqual([row["stored_name"] for row in app.gallery_images(self.conn)], ["a.jpg", "b.jpg"])

    def test_blog_gallery_includes_carousel_contract(self):
        now = app.now_iso()
        self.conn.execute(
            "INSERT INTO site_images(stored_name, original_name, mime_type, size, sort_order, enabled, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("contract.jpg", "contract.jpg", "image/jpeg", 1, 1, 1, now),
        )
        self.conn.commit()

        rendered = app.blog_gallery(self.conn)

        self.assertRegex(rendered, r'data-effect="(?:fade|slide|zoom)"')
        self.assertIn('data-interval="5"', rendered)
        self.assertIn('class="blog-gallery__slide is-active"', rendered)
        self.assertIn('aria-label="暂停背景轮播"', rendered)

    def test_blog_gallery_falls_back_without_images(self):
        self.assertEqual(app.blog_gallery(self.conn), "")

    def test_blog_gallery_renders_enabled_images_in_order(self):
        now = app.now_iso()
        self.conn.execute(
            "INSERT INTO site_images(stored_name, original_name, mime_type, size, sort_order, enabled, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("second image.jpg", "second <image>.jpg", "image/jpeg", 1, 2, 1, now),
        )
        self.conn.execute(
            "INSERT INTO site_images(stored_name, original_name, mime_type, size, sort_order, enabled, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("first image.jpg", "first image.jpg", "image/jpeg", 1, 1, 1, now),
        )
        self.conn.execute(
            "INSERT INTO site_gallery_settings(id, effect, interval, updated_at) VALUES (1, ?, ?, ?)",
            ("slide", 7, now),
        )
        self.conn.commit()

        rendered = app.blog_gallery(self.conn)

        self.assertIn('data-effect="slide"', rendered)
        self.assertIn('data-interval="7"', rendered)
        self.assertIn('/media/gallery/first%20image.jpg', rendered)
        self.assertIn('/media/gallery/second%20image.jpg', rendered)
        self.assertLess(rendered.index("first%20image.jpg"), rendered.index("second%20image.jpg"))
        self.assertIn('class="blog-gallery__slide is-active"', rendered)
        self.assertIn('class="blog-gallery__slide" aria-hidden="true"', rendered)
        self.assertIn("second &lt;image&gt;.jpg", rendered)


    def _multipart(self, files, csrf="integration-csrf"):
        boundary = "----signal-test"
        chunks = [f"--{boundary}\r\nContent-Disposition: form-data; name=\"csrf\"\r\n\r\n{csrf}\r\n".encode()]
        for name, filename, mime, payload in files:
            chunks.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{filename}\"\r\nContent-Type: {mime}\r\n\r\n").encode() + payload + b"\r\n")
        chunks.append(f"--{boundary}--\r\n".encode())
        return b"".join(chunks), f"multipart/form-data; boundary={boundary}"

    def test_gallery_upload_saves_file(self):
        token = "gallery-session"
        app.SESSIONS[token] = {"admin_id": "1", "csrf": "integration-csrf"}
        body, content_type = self._multipart([("files", "hero.jpg", "image/jpeg", b"\xff\xd8fake-jpeg")])
        handler = make_handler("/admin/media/upload", body, {"Content-Type": content_type, "Content-Length": str(len(body)), "Cookie": app.build_session_cookie(token)})
        app.Handler.do_POST(handler)
        self.assertIn("303 See Other", handler.wfile.getvalue().decode("latin-1"))
        self.assertIn("Location: /admin/media", handler.wfile.getvalue().decode("latin-1"))
        row = self.conn.execute("SELECT original_name, mime_type FROM site_images").fetchone()
        self.assertEqual((row["original_name"], row["mime_type"]), ("hero.jpg", "image/jpeg"))
        stored = next(app.gallery_dir().glob("*"))
        self.assertTrue(stored.is_file())

    def test_gallery_upload_accepts_multipart_body_over_form_limit(self):
        token = "gallery-session-large"
        app.SESSIONS[token] = {"admin_id": "1", "csrf": "integration-csrf"}
        body, content_type = self._multipart([("files", "large.jpg", "image/jpeg", b"x" * (app.MAX_BODY_BYTES + 1))])
        handler = make_handler("/admin/media/upload", body, {"Content-Type": content_type, "Content-Length": str(len(body)), "Cookie": app.build_session_cookie(token)})
        app.Handler.do_POST(handler)
        self.assertIn("303 See Other", handler.wfile.getvalue().decode("latin-1"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM site_images").fetchone()[0], 1)

    def test_gallery_upload_rejects_more_than_nine_files(self):
        token = "gallery-session-many"
        app.SESSIONS[token] = {"admin_id": "1", "csrf": "integration-csrf"}
        body, content_type = self._multipart([("files", f"{i}.jpg", "image/jpeg", b"x") for i in range(10)])
        handler = make_handler("/admin/media/upload", body, {"Content-Type": content_type, "Content-Length": str(len(body)), "Cookie": app.build_session_cookie(token)})
        app.Handler.do_POST(handler)
        response = handler.wfile.getvalue().decode("utf-8")
        self.assertIn("400 Bad Request", response)
        self.assertIn("最多 9 张", response)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM site_images").fetchone()[0], 0)

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

    def test_admin_editor_form_is_rendered_once(self):
        handler = SimpleNamespace(session=lambda: {"csrf": "test-csrf"})
        html = app.edit_form(self.conn, handler, "posts")
        self.assertEqual(html.count('class="editor-form"'), 1)

    def test_blog_listing_renders_timeline_in_newest_to_oldest_order(self):
        self.conn.executemany(
            "INSERT INTO posts(title, slug, excerpt, body, tags, status, published_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("Older note", "older-note", "A short older excerpt", "", "notes", "published", "2026-01-02T00:00:00+00:00", "2026-01-02T00:00:00+00:00", "2026-01-02T00:00:00+00:00"),
                ("Newest note", "newest-note", "The latest excerpt", "", "build", "published", "2026-02-14T00:00:00+00:00", "2026-02-14T00:00:00+00:00", "2026-02-14T00:00:00+00:00"),
            ],
        )
        self.conn.commit()

        html = app.listing(self.conn, "blog")
        timeline_html = html.split('class="article-timeline"', 1)[1].split("</nav>", 1)[0]

        self.assertIn('class="article-timeline"', html)
        self.assertIn("2026.02.14", timeline_html)
        self.assertIn('class="timeline-date"', timeline_html)
        self.assertIn("The latest excerpt", timeline_html)
        self.assertLess(timeline_html.index("Newest note"), timeline_html.index("Older note"))


if __name__ == "__main__":
    unittest.main()
