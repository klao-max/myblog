import io
import os
import sqlite3
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
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
        conn.execute("DELETE FROM comments")
        conn.commit()
        for path in app.gallery_dir().iterdir():
            if path.is_file():
                path.unlink()
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

    def test_expired_session_is_rejected(self):
        token = "expired-session"
        app.SESSIONS[token] = {
            "admin_id": "1",
            "csrf": "csrf",
            "created_at": time.time() - app.SESSION_TTL_SECONDS - 1,
            "last_seen": time.time() - app.SESSION_TTL_SECONDS - 1,
        }
        handler = make_handler("/admin", b"", {"Cookie": app.build_session_cookie(token)})

        self.assertEqual(handler.session(), {})
        self.assertNotIn(token, app.SESSIONS)

    def test_password_rotation_revokes_existing_sessions(self):
        self.conn.execute(
            "UPDATE admins SET password_hash = ? WHERE username = 'admin'",
            (app.password_hash("test-password"),),
        )
        self.conn.commit()
        token = "password-session"
        app.SESSIONS[token] = {"admin_id": "1", "csrf": "csrf"}

        app.rotate_admin_password(self.conn, "admin", "test-password", "new-password-123")

        self.assertNotIn(token, app.SESSIONS)

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

    def test_negative_content_length_is_rejected(self):
        handler = make_handler(
            "/admin/login",
            b"username=admin&password=test-password",
            {
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": "-1",
            },
        )

        app.Handler.do_POST(handler)

        self.assertIn("400 Bad Request", handler.wfile.getvalue().decode("latin-1"))

    def test_non_numeric_content_length_is_rejected(self):
        handler = make_handler(
            "/admin/login",
            b"username=admin&password=test-password",
            {
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": "invalid",
            },
        )

        app.Handler.do_POST(handler)

        self.assertIn("400 Bad Request", handler.wfile.getvalue().decode("latin-1"))

    def test_gallery_rejects_unsupported_type_and_oversized_file(self):
        with self.assertRaises(ValueError):
            app.validate_gallery_upload("script.svg", "image/svg+xml", b"svg")
        with self.assertRaises(ValueError):
            app.validate_gallery_upload("large.jpg", "image/jpeg", b"x" * (app.MAX_MEDIA_BYTES + 1))

    def test_gallery_rejects_payload_with_wrong_signature(self):
        with self.assertRaises(ValueError):
            app.validate_gallery_upload("fake.jpg", "image/jpeg", b"<html>not an image</html>")

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

    def test_gallery_upload_removes_file_when_database_commit_fails(self):
        token = "gallery-session-failure"
        app.SESSIONS[token] = {"admin_id": "1", "csrf": "integration-csrf"}
        body, content_type = self._multipart([("files", "failed.jpg", "image/jpeg", b"\xff\xd8fake-jpeg")])
        handler = make_handler(
            "/admin/media/upload",
            body,
            {
                "Content-Type": content_type,
                "Content-Length": str(len(body)),
                "Cookie": app.build_session_cookie(token),
            },
        )
        real_connection = app.db()

        class CommitFailureConnection:
            def __getattr__(self, name):
                return getattr(real_connection, name)

            def commit(self):
                raise sqlite3.OperationalError("forced commit failure")

        with patch.object(app, "db", return_value=CommitFailureConnection()):
            app.Handler.do_POST(handler)

        self.assertIn("503 Service Unavailable", handler.wfile.getvalue().decode("latin-1"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM site_images").fetchone()[0], 0)
        self.assertEqual(list(app.gallery_dir().iterdir()), [])
        real_connection.close()

    def test_gallery_delete_keeps_recovery_file_when_unlink_fails(self):
        token = "gallery-delete-unlink-failure"
        app.SESSIONS[token] = {"admin_id": "1", "csrf": "integration-csrf"}
        stored_name = "delete-me.jpg"
        now = app.now_iso()
        self.conn.execute(
            "INSERT INTO site_images(stored_name, original_name, mime_type, size, sort_order, enabled, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
            (stored_name, stored_name, "image/jpeg", 4, 1, now),
        )
        self.conn.commit()
        target = app.gallery_dir() / stored_name
        target.write_bytes(b"data")
        row_id = self.conn.execute("SELECT id FROM site_images WHERE stored_name = ?", (stored_name,)).fetchone()[0]
        body = urlencode({"csrf": "integration-csrf", "id": str(row_id)}).encode()
        handler = make_handler(
            "/admin/media/delete",
            body,
            {
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(body)),
                "Cookie": app.build_session_cookie(token),
            },
        )
        with patch.object(app, "remove_gallery_file", side_effect=OSError("forced unlink failure")):
            app.Handler.do_POST(handler)

        self.assertIn("303 See Other", handler.wfile.getvalue().decode("latin-1"))
        self.assertIsNone(self.conn.execute("SELECT id FROM site_images WHERE id = ?", (row_id,)).fetchone())
        self.assertFalse(target.exists())
        self.assertEqual(len(list(app.gallery_dir().glob("*.deleting"))), 1)

    def test_gallery_upload_accepts_multipart_body_over_form_limit(self):
        token = "gallery-session-large"
        app.SESSIONS[token] = {"admin_id": "1", "csrf": "integration-csrf"}
        body, content_type = self._multipart([("files", "large.jpg", "image/jpeg", b"\xff\xd8" + b"x" * (app.MAX_BODY_BYTES - 1))])
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

    def test_rate_limit_state_has_an_entry_bound(self):
        app.COMMENT_SUBMISSIONS.clear()

        for index in range(app.MAX_RATE_ENTRIES + 1):
            app.record_comment_submission(f"198.51.100.{index}", now=100.0)

        self.assertLessEqual(len(app.COMMENT_SUBMISSIONS), app.MAX_RATE_ENTRIES)

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
        self.assertIn('</a><time class="timeline-date"', timeline_html)
        self.assertIn("The latest excerpt", timeline_html)
        self.assertLess(timeline_html.index("Newest note"), timeline_html.index("Older note"))

    def test_blog_listing_is_paginated(self):
        self.conn.execute("DELETE FROM posts")
        now = app.now_iso()
        self.conn.executemany(
            "INSERT INTO posts(title, slug, excerpt, body, tags, status, published_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (f"Page {index}", f"page-{index}", "Excerpt", "Body", "", "published", now, now, now)
                for index in range(app.PAGE_SIZE + 1)
            ],
        )
        self.conn.commit()

        first_page = app.listing(self.conn, "blog", page=1)
        second_page = app.listing(self.conn, "blog", page=2)

        self.assertEqual(sum(f"Page {index}" in first_page for index in range(app.PAGE_SIZE + 1)), app.PAGE_SIZE)
        self.assertEqual(sum(f"Page {index}" in second_page for index in range(app.PAGE_SIZE + 1)), 1)
        self.assertIn("下一页", first_page)
        self.assertIn("上一页", second_page)

    def test_public_comment_is_saved_with_ip_and_visible(self):
        now = app.now_iso()
        self.conn.execute(
            "INSERT INTO posts(title, slug, excerpt, body, status, published_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("Commented post", "commented-post", "Excerpt", "Body", "published", now, now, now),
        )
        self.conn.commit()
        body = urlencode({"content": "A useful suggestion", "website": ""}).encode()
        handler = make_handler(
            "/blog/commented-post/comments",
            body,
            {"Content-Type": "application/x-www-form-urlencoded", "Content-Length": str(len(body))},
        )
        handler.client_address = ("203.0.113.5", 12345)

        app.Handler.do_POST(handler)

        row = self.conn.execute("SELECT content, ip_address, visible FROM comments").fetchone()
        self.assertEqual((row["content"], row["ip_address"], row["visible"]), ("A useful suggestion", "203.0.113.5", 1))
        self.assertIn("Location: /blog/commented-post", handler.wfile.getvalue().decode("latin-1"))

    def test_comment_rate_limit_blocks_fourth_submission_in_window(self):
        app.COMMENT_SUBMISSIONS.clear()
        for _ in range(app.COMMENT_MAX_SUBMISSIONS):
            app.record_comment_submission("203.0.113.8", now=100.0)
        self.assertTrue(app.comment_rate_limited("203.0.113.8", now=101.0))
        self.assertFalse(app.comment_rate_limited("203.0.113.8", now=app.COMMENT_WINDOW_SECONDS + 101.0))

    def test_comment_rate_limit_returns_parseable_429(self):
        now = app.now_iso()
        self.conn.execute(
            "INSERT INTO posts(title, slug, excerpt, body, status, published_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("Rate limited post", "rate-limited-post", "Excerpt", "Body", "published", now, now, now),
        )
        self.conn.commit()
        ip_address = "203.0.113.8"
        app.COMMENT_SUBMISSIONS.clear()
        for _ in range(app.COMMENT_MAX_SUBMISSIONS):
            app.record_comment_submission(ip_address)
        body = urlencode({"content": "Fourth suggestion", "website": ""}).encode()
        handler = make_handler(
            "/blog/rate-limited-post/comments",
            body,
            {"Content-Type": "application/x-www-form-urlencoded", "Content-Length": str(len(body))},
        )
        handler.client_address = (ip_address, 12345)

        app.Handler.do_POST(handler)

        response_text = handler.wfile.getvalue().decode("utf-8")
        self.assertIn("429 Too Many Requests", response_text)
        self.assertIn("留言过于频繁，请稍后再试", response_text)

    def test_comment_cards_wrap_unbroken_text(self):
        css = (Path(app.ROOT) / "static" / "style.css").read_text()
        rule_start = css.index(".comment-card p")
        rule_end = css.index("}", rule_start)
        comment_rule = css[rule_start:rule_end]
        self.assertIn("overflow-wrap: anywhere", comment_rule)

    def test_static_assets_are_cacheable(self):
        handler = app.Handler.__new__(app.Handler)
        headers = {}
        handler.send_response = lambda status: None
        handler.send_header = lambda name, value: headers.__setitem__(name, value)
        handler.end_headers = lambda: None
        handler.wfile = io.BytesIO()

        handler.serve_file(Path(app.ROOT) / "static" / "style.css", Path(app.ROOT) / "static")

        self.assertEqual(headers["Cache-Control"], "public, max-age=3600")

    def test_file_responses_are_streamed_in_chunks(self):
        path = self.data_dir / "chunk-test.bin"
        payload = b"x" * (app.FILE_CHUNK_BYTES * 2 + 1)
        path.write_bytes(payload)
        handler = app.Handler.__new__(app.Handler)
        headers = {}
        handler.send_response = lambda status: None
        handler.send_header = lambda name, value: headers.__setitem__(name, value)
        handler.end_headers = lambda: None
        handler.wfile = io.BytesIO()

        with patch.object(Path, "read_bytes", side_effect=AssertionError("whole-file read")):
            handler.serve_file(path)

        self.assertEqual(headers["Content-Length"], str(len(payload)))
        self.assertEqual(handler.wfile.getvalue(), payload)

    def test_readiness_checks_data_directory(self):
        self.assertTrue(app.readiness_check())
        blocked = self.data_dir / "not-a-directory"
        blocked.write_text("file")

        with patch.object(app, "DATA_DIR", blocked):
            self.assertFalse(app.readiness_check())

    def test_response_security_headers_are_present(self):
        handler = app.Handler.__new__(app.Handler)
        headers = {}
        handler.send_response = lambda status: None
        handler.send_header = lambda name, value: headers.__setitem__(name, value)
        handler.end_headers = lambda: None
        handler.wfile = io.BytesIO()

        handler.send_html("<p>test</p>")

        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Referrer-Policy"], "same-origin")

        file_headers = {}
        handler.send_response = lambda status: None
        handler.send_header = lambda name, value: file_headers.__setitem__(name, value)
        handler.end_headers = lambda: None
        handler.wfile = io.BytesIO()
        handler.serve_file(Path(app.ROOT) / "static" / "style.css", Path(app.ROOT) / "static")

        self.assertEqual(file_headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", file_headers["Content-Security-Policy"])
        self.assertEqual(file_headers["Referrer-Policy"], "same-origin")

    def test_empty_content_tables_are_not_reseeded(self):
        self.conn.close()
        reopened = app.db()
        try:
            counts = {
                table: reopened.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("posts", "projects", "games")
            }
        finally:
            reopened.close()

        self.assertEqual(counts, {"posts": 0, "projects": 0, "games": 0})

    def test_legacy_schema_gets_missing_columns(self):
        legacy = __import__("sqlite3").connect(":memory:")
        legacy.execute("CREATE TABLE posts (id INTEGER PRIMARY KEY, title TEXT, slug TEXT)")

        app.initialize_database(legacy)

        columns = {row[1] for row in legacy.execute("PRAGMA table_info(posts)").fetchall()}
        self.assertIn("excerpt", columns)
        self.assertIn("updated_at", columns)
        self.assertEqual(legacy.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], app.SCHEMA_VERSION)
        legacy.close()

    def test_sqlite_uses_wal_journal_mode(self):
        self.assertEqual(self.conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")

    def test_database_lock_returns_service_unavailable(self):
        class LockedConnection:
            def execute(self, *_args):
                raise __import__("sqlite3").OperationalError("database is locked")

            def close(self):
                return None

        body = urlencode({"username": "admin", "password": "test-password"}).encode()
        handler = make_handler(
            "/admin/login",
            body,
            {
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(body)),
            },
        )

        with patch.object(app, "db", return_value=LockedConnection()):
            app.Handler.do_POST(handler)

        self.assertIn("503 Service Unavailable", handler.wfile.getvalue().decode("latin-1"))

    def test_comment_content_is_escaped_on_article_page(self):
        now = app.now_iso()
        self.conn.execute(
            "INSERT INTO posts(title, slug, excerpt, body, status, published_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("Commented post", "commented-post", "Excerpt", "Body", "published", now, now, now),
        )
        post_id = self.conn.execute("SELECT id FROM posts WHERE slug='commented-post'").fetchone()[0]
        self.conn.execute(
            "INSERT INTO comments(post_id, content, ip_address, reply, visible, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (post_id, "<script>alert(1)</script>", "203.0.113.5", "Thanks", 1, now, now),
        )
        self.conn.commit()

        status, html = app.detail(self.conn, "blog", "commented-post")

        self.assertEqual(status, 200)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("Thanks", html)

    def test_admin_can_edit_reply_and_visibility(self):
        now = app.now_iso()
        self.conn.execute(
            "INSERT INTO posts(title, slug, excerpt, body, status, published_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("Commented post", "commented-post", "Excerpt", "Body", "published", now, now, now),
        )
        post_id = self.conn.execute("SELECT id FROM posts WHERE slug='commented-post'").fetchone()[0]
        self.conn.execute(
            "INSERT INTO comments(post_id, content, ip_address, reply, visible, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (post_id, "Original", "203.0.113.5", "", 1, now, now),
        )
        comment_id = self.conn.execute("SELECT id FROM comments").fetchone()[0]
        self.conn.commit()
        token = "comment-admin-session"
        app.SESSIONS[token] = {"admin_id": "1", "csrf": "integration-csrf"}
        body = urlencode({"csrf": "integration-csrf", "id": str(comment_id), "content": "Edited", "reply": "已处理", "visible": ""}).encode()
        handler = make_handler("/admin/comments/save", body, {"Content-Type": "application/x-www-form-urlencoded", "Content-Length": str(len(body)), "Cookie": app.build_session_cookie(token)})

        app.Handler.do_POST(handler)

        row = self.conn.execute("SELECT content, reply, visible FROM comments WHERE id=?", (comment_id,)).fetchone()
        self.assertEqual((row["content"], row["reply"], row["visible"]), ("Edited", "已处理", 0))

    def test_logout_requires_csrf_token(self):
        token = "logout-session"
        app.SESSIONS[token] = {"admin_id": "1", "csrf": "integration-csrf"}
        handler = make_handler(
            "/admin/logout",
            b"",
            {"Content-Length": "0", "Cookie": app.build_session_cookie(token)},
        )

        app.Handler.do_POST(handler)

        self.assertIn("403 Forbidden", handler.wfile.getvalue().decode("latin-1"))
        self.assertIn(token, app.SESSIONS)

    def test_logout_with_csrf_token_revokes_session(self):
        token = "logout-valid-session"
        app.SESSIONS[token] = {"admin_id": "1", "csrf": "integration-csrf"}
        body = urlencode({"csrf": "integration-csrf"}).encode()
        handler = make_handler(
            "/admin/logout",
            body,
            {
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(body)),
                "Cookie": app.build_session_cookie(token),
            },
        )

        app.Handler.do_POST(handler)

        self.assertIn("303 See Other", handler.wfile.getvalue().decode("latin-1"))
        self.assertNotIn(token, app.SESSIONS)

    def test_content_save_rejects_empty_required_fields(self):
        token = "required-field-session"
        app.SESSIONS[token] = {"admin_id": "1", "csrf": "integration-csrf"}
        body = urlencode(
            {
                "csrf": "integration-csrf",
                "title": "",
                "slug": "empty-title",
                "excerpt": "",
                "body": "Body",
                "status": "draft",
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

        self.assertIn("400 Bad Request", handler.wfile.getvalue().decode("latin-1"))
        self.assertIsNone(self.conn.execute("SELECT id FROM posts WHERE slug = 'empty-title'").fetchone())

    def test_content_save_rejects_overlong_title(self):
        token = "long-title-session"
        app.SESSIONS[token] = {"admin_id": "1", "csrf": "integration-csrf"}
        body = urlencode(
            {
                "csrf": "integration-csrf",
                "title": "x" * (app.MAX_TITLE_CHARS + 1),
                "slug": "long-title",
                "excerpt": "Excerpt",
                "body": "Body",
                "status": "draft",
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

        self.assertIn("400 Bad Request", handler.wfile.getvalue().decode("latin-1"))

    def test_non_slug_integrity_error_gets_generic_message(self):
        self.conn.execute(
            "CREATE TRIGGER reject_post_insert BEFORE INSERT ON posts BEGIN SELECT RAISE(ABORT, 'forced integrity failure'); END"
        )
        self.conn.commit()
        token = "integrity-session"
        app.SESSIONS[token] = {"admin_id": "1", "csrf": "integration-csrf"}
        body = urlencode(
            {
                "csrf": "integration-csrf",
                "title": "Valid title",
                "slug": "integrity-test",
                "excerpt": "Excerpt",
                "body": "Body",
                "status": "draft",
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

        try:
            app.Handler.do_POST(handler)
        finally:
            self.conn.execute("DROP TRIGGER reject_post_insert")
            self.conn.commit()

        response = handler.wfile.getvalue().decode("utf-8")
        self.assertIn("409 Conflict", response)
        self.assertIn("数据约束", response)
        self.assertNotIn("slug 可能已经被使用", response)

    def test_admin_comments_uses_row_based_management_list(self):
        now = app.now_iso()
        self.conn.execute(
            "INSERT INTO posts(title, slug, excerpt, body, status, published_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("Commented post", "commented-post", "Excerpt", "Body", "published", now, now, now),
        )
        post_id = self.conn.execute("SELECT id FROM posts WHERE slug='commented-post'").fetchone()[0]
        self.conn.execute(
            "INSERT INTO comments(post_id, content, ip_address, reply, visible, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (post_id, "Original", "203.0.113.5", "", 1, now, now),
        )
        self.conn.commit()

        html = app.admin_comments(self.conn, SimpleNamespace(session=lambda: {"csrf": "test-csrf"}))

        self.assertIn('class="admin-comment-row"', html)
        self.assertIn('class="admin-comment-row-actions"', html)
        self.assertIn('/admin/comments/edit?id=', html)
        self.assertEqual(html.count('action="/admin/comments/delete"'), 1)

    def test_comment_time_shows_minutes_on_public_and_admin_views(self):
        self.conn.execute(
            "INSERT INTO posts(title, slug, excerpt, body, status, published_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("Timed post", "timed-post", "Excerpt", "Body", "published", "2026-11-15T12:34:56+00:00", "2026-11-15T12:34:56+00:00", "2026-11-15T12:34:56+00:00"),
        )
        post_id = self.conn.execute("SELECT id FROM posts WHERE slug='timed-post'").fetchone()[0]
        self.conn.execute(
            "INSERT INTO comments(post_id, content, ip_address, reply, visible, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (post_id, "Minute detail", "203.0.113.9", "", 1, "2026-11-15T12:34:56+00:00", "2026-11-15T12:34:56+00:00"),
        )
        self.conn.commit()

        status, public_html = app.detail(self.conn, "blog", "timed-post")
        admin_html = app.admin_comments(self.conn, SimpleNamespace(session=lambda: {"csrf": "test-csrf"}))

        self.assertEqual(status, 200)
        self.assertIn("2026.11.15 12:34", public_html)
        self.assertIn("2026.11.15 12:34", admin_html)


if __name__ == "__main__":
    unittest.main()
