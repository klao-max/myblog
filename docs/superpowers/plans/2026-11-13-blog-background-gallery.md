# 博客背景图片画廊 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `/blog` 顶部主视觉区域增加最多 9 张图片的背景轮播，并提供后台上传、排序、启用状态和切换效果管理。

**Architecture:** 继续使用现有单文件 Python HTTP 服务和 SQLite。图片元数据存入 SQLite，文件保存到 `DATA_DIR/uploads/gallery/`，后台通过 multipart 上传和受保护的管理路由维护图片；公开博客页输出背景图片数据，由 `static/app.js` 驱动自动轮播，CSS 负责遮罩和三种过渡效果。

**Tech Stack:** Python 3.12 standard library (`sqlite3`, `email.parser`, `mimetypes`, `secrets`), SQLite, 原生 HTML/CSS/JavaScript、现有 unittest。

**Spec:** `docs/superpowers/specs/2026-11-13-blog-background-gallery-design.md`

## Global Constraints

- `/blog` 没有启用图片时继续使用现有纯色主视觉。
- 最多保留 9 张图片；只接受 JPEG、PNG、WebP、GIF；单张最大 8 MB。
- 图片文件只写入 `DATA_DIR/uploads/gallery/`，公开访问只能通过 `/media/gallery/<stored-name>`。
- 所有后台写操作必须复用管理员会话和 CSRF 校验。
- 默认效果为淡入淡出，默认间隔 5 秒；悬停或键盘聚焦时暂停，减少动画时不自动切换。
- 不新增第三方运行时依赖。

---

### Task 1: Add gallery storage and upload validation

**Files:**
- Modify: `app.py:102-160` for `site_images` and `site_gallery_settings` schema plus storage helpers
- Modify: `app.py:560-610` for media file serving
- Test: `tests/test_app.py`

**Interfaces:**
- Produces `gallery_dir() -> Path`, `validate_gallery_upload(filename: str, content_type: str, payload: bytes) -> tuple[str, str]`, and `gallery_images(conn: sqlite3.Connection) -> list[sqlite3.Row]` for later tasks.
- `gallery_images` returns enabled rows ordered by `sort_order ASC, id ASC`.

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run the focused tests and confirm the expected failure**

Run: `python3 -m unittest tests.test_app.SignalDeskTests.test_gallery_rejects_unsupported_type_and_oversized_file tests.test_app.SignalDeskTests.test_gallery_images_are_ordered_and_filter_disabled_rows -v`

Expected: FAIL because the gallery tables and helper functions do not exist.

- [ ] **Step 3: Implement the storage and validation helpers**

Add `MAX_MEDIA_BYTES = 8 * 1024 * 1024`, `ALLOWED_MEDIA_TYPES`, the two tables, `gallery_dir()`, `validate_gallery_upload()`, and `gallery_images()`. Create the upload directory from `gallery_dir()` before writes. Reject mismatched content types, unsupported extensions, and payloads over the limit.

- [ ] **Step 4: Add protected media serving and run focused tests**

Handle `/media/gallery/<stored-name>` in `Handler.do_GET` by resolving against `gallery_dir()` and passing the path through `serve_file`. Reject paths that escape the directory. Re-run the two focused tests; expected result: PASS.

- [ ] **Step 5: Commit the storage slice**

```bash
git add app.py tests/test_app.py
git commit -m "feat: add gallery storage and media validation"
```

### Task 2: Add multipart upload and admin media management

**Files:**
- Modify: `app.py:470-548` for `admin_media()` and the admin rail link
- Modify: `app.py:600-760` for multipart parsing and media POST routes
- Test: `tests/test_app.py`

**Interfaces:**
- Produces `parse_multipart_form(body: bytes, content_type: str) -> tuple[dict[str, str], list[dict[str, object]]]` for the upload route.
- Adds `GET /admin/media`, `POST /admin/media/upload`, `POST /admin/media/update`, `POST /admin/media/delete`, and `POST /admin/media/settings`.

- [ ] **Step 1: Write failing route tests**

Add tests that build a real multipart body containing one JPEG payload and assert that an authenticated upload redirects to `/admin/media`, writes one row, and creates a file below `DATA_DIR/uploads/gallery/`. Add a second test with ten files and assert a 400 response containing `最多 9 张`.

- [ ] **Step 2: Run the focused route tests and confirm failure**

Run: `python3 -m unittest tests.test_app.SignalDeskTests.test_gallery_upload_saves_file tests.test_app.SignalDeskTests.test_gallery_upload_rejects_more_than_nine_files -v`

Expected: FAIL because multipart parsing and media routes do not exist.

- [ ] **Step 3: Implement multipart parsing and upload persistence**

Use `email.parser.BytesParser` with a synthetic `Content-Type` header to parse multipart fields and files. Enforce `MAX_BODY_BYTES`, count existing enabled and disabled rows together, validate every file before writing any file, generate a random stored name with `secrets.token_hex`, write bytes to `gallery_dir()`, and insert rows with consecutive `sort_order` values.

- [ ] **Step 4: Implement the media admin page and mutation routes**

Render a media grid with thumbnail, original filename, enable checkbox, move up/down controls, delete control, multi-file input, and effect select (`fade`, `slide`, `zoom`). Every POST must call `self.session()` and compare `csrf`, then update SQLite and redirect back to `/admin/media`. Delete must remove the database row and stored file.

- [ ] **Step 5: Run focused tests and commit**

Run: `python3 -m unittest tests.test_app.SignalDeskTests.test_gallery_upload_saves_file tests.test_app.SignalDeskTests.test_gallery_upload_rejects_more_than_nine_files -v`

Expected: PASS.

```bash
git add app.py tests/test_app.py
git commit -m "feat: add admin gallery management"
```

### Task 3: Render the background gallery on the blog page

**Files:**
- Modify: `app.py:432-446` to query gallery settings and render the blog visual
- Test: `tests/test_app.py`

**Interfaces:**
- Produces `blog_gallery(conn: sqlite3.Connection) -> str`, which returns an empty string when no enabled image exists and otherwise returns a section containing image URLs, `data-effect`, and `data-interval`.

- [ ] **Step 1: Write failing rendering tests**

Add tests for no-image fallback and enabled-image output. Assert that no-image output is empty, while enabled images appear in sort order with `/media/gallery/` URLs and the saved effect name.

- [ ] **Step 2: Run focused tests and confirm failure**

Run: `python3 -m unittest tests.test_app.SignalDeskTests.test_blog_gallery_falls_back_without_images tests.test_app.SignalDeskTests.test_blog_gallery_renders_enabled_images_in_order -v`

Expected: FAIL because `blog_gallery` does not exist.

- [ ] **Step 3: Implement the gallery renderer**

Load settings with defaults `effect='fade'` and `interval=5`, use `gallery_images(conn)`, escape every title and URL component, render the first image as active, and place the existing blog title/intro over the gallery backdrop with a contrast overlay.

- [ ] **Step 4: Wire the renderer into `listing()` and run tests**

Insert `blog_gallery(conn)` between the public header and blog intro only for `kind == "blog"`; leave projects and games unchanged. Re-run the focused tests; expected result: PASS.

### Task 4: Implement carousel behavior and visual treatment

**Files:**
- Modify: `static/app.js`
- Modify: `static/style.css`
- Test: `tests/test_app.py` for emitted effect classes and interval data

- [ ] **Step 1: Add the failing contract test**

Assert that the rendered gallery contains `data-effect="fade|slide|zoom"`, `data-interval="5"`, an active first image, and the pause button label.

- [ ] **Step 2: Run the focused test and confirm failure**

Run: `python3 -m unittest tests.test_app.SignalDeskTests.test_blog_gallery_includes_carousel_contract -v`

Expected: FAIL until the renderer emits the complete contract.

- [ ] **Step 3: Add the minimal JavaScript controller**

Select `.blog-gallery`, rotate `.blog-gallery__slide` elements on a timer, apply `is-active` and effect classes, pause on `mouseenter`, `focusin`, and the visible pause button, resume on `mouseleave` and `focusout`, and skip timers when `matchMedia('(prefers-reduced-motion: reduce)').matches` is true.

- [ ] **Step 4: Add responsive CSS and accessibility states**

Style the gallery as a fixed-height hero with a dark gradient overlay, readable title text, `object-fit: cover`, mobile height reduction, visible keyboard focus, and effect-specific transitions for fade, slide, and zoom. Keep the plain blog layout when the gallery is absent.

- [ ] **Step 5: Run the focused contract test and the full suite**

Run: `python3 -m unittest tests.test_app.SignalDeskTests.test_blog_gallery_includes_carousel_contract -v` and then `python3 -m unittest discover -s tests -v`.

Expected: all tests PASS.

### Task 5: Verify the integrated feature and document operations

**Files:**
- Modify: `README.md` with media management and Docker persistence instructions
- Modify: `docs/superpowers/specs/2026-11-13-blog-background-gallery-design.md` only if implementation decisions change

- [ ] **Step 1: Run syntax and test checks**

```bash
python3 -m py_compile app.py tests/test_app.py
python3 -m unittest discover -s tests -v
```

- [ ] **Step 2: Run a local integration check**

Start the app with `ADMIN_PASSWORD` and `SESSION_SECRET`, open `/admin/media`, upload one image, confirm it appears in `/blog`, change the effect, reload, and confirm the selected effect remains active. Check the no-image state after deleting the last image.

- [ ] **Step 3: Review responsive behavior**

Check the blog page at desktop width and a 390px viewport. Confirm the title remains readable over every image, controls are keyboard reachable, and no horizontal overflow is introduced outside the intentionally scrollable admin media grid.

- [ ] **Step 4: Update README and commit the feature**

```bash
git add app.py static/app.js static/style.css tests/test_app.py README.md docs/superpowers/specs/2026-11-13-blog-background-gallery-design.md
git commit -m "feat: add blog background image gallery"
```
