# Signal Desk Reliability Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修复审计中影响公网稳定性和数据一致性的高优先级问题，同时保持现有路由、页面和内容模型兼容。

**Architecture:** 在现有 `app.py` 单体结构中增加小型边界函数和显式迁移步骤，避免无关重构。请求解析、数据库初始化、图库事务和后台输入校验各自拥有可直接测试的行为边界。

**Tech Stack:** Python 3.12 标准库 HTTP server、SQLite、`unittest`、Docker Compose。

**Spec:** `docs/superpowers/specs/2026-11-18-reliability-hardening-design.md`

## Global Constraints

- 不新增第三方运行时依赖。
- 保持现有公开页面、后台路由、表单字段和 HTML 主要结构。
- 迁移只追加表、索引和缺失列，不删除现有数据。
- 每个生产代码改动先有一个会失败的行为测试。
- 完成后运行 unittest、py_compile、Compose config 和 git diff check。

### Task 1: 请求边界与安全响应测试

**Files:**
- Modify: `tests/test_app.py`
- Modify: `app.py:728-793`

**Interfaces:**
- Produces `parse_content_length(value: str | None) -> int` for URL-encoded and multipart paths.
- `send_html` and `serve_file` emit `X-Content-Type-Options`, `Content-Security-Policy`, and `Referrer-Policy`.

- [x] **Step 1: Write failing tests**

Add tests that call `parse_form()` with `Content-Length: -1` and a non-numeric value, assert 400 from `do_POST`, and assert HTML and static responses include the three security headers.

- [x] **Step 2: Run focused tests**

Run `python3 -m unittest tests.test_app.SignalDeskTests.test_negative_content_length_is_rejected tests.test_app.SignalDeskTests.test_response_security_headers -v`.
Expected result is failure because negative values currently reach `read(-1)` and headers are absent.

- [x] **Step 3: Implement the smallest fix**

Parse the header once, reject missing/invalid/negative values according to the existing empty-body behavior, preserve 413 for values above the existing limits, and add the headers in both response helpers.

- [x] **Step 4: Run focused and full tests**

Run the focused tests, then `python3 -m unittest discover -s tests -v`.

### Task 2: SQLite initialization, migrations, seed, and lock handling

**Files:**
- Modify: `tests/test_app.py`
- Modify: `app.py:117-331, 883-1067`

**Interfaces:**
- Produces versioned `schema_migrations` initialization and idempotent migration helpers.
- `db()` returns a connection configured with explicit `busy_timeout` and WAL.
- Write routes map `sqlite3.OperationalError` lock failures to 503.

- [x] **Step 1: Write failing tests**

Add tests for an empty content table staying empty after a second `db()` call, an old schema gaining missing columns through initialization, `PRAGMA journal_mode` being `wal`, a forced lock returning 503, and a forced migration failure stopping initialization.

- [x] **Step 2: Run focused tests**

Run the new tests individually and confirm failures on seed replay, missing columns, journal mode, and unhandled lock errors.

- [x] **Step 3: Implement migration and seed marker**

Create `schema_migrations`, define the current version, add migrations for existing tables and indexes, and run `seed()` only when the database has no migration marker. Keep admin creation idempotent.

- [x] **Step 4: Configure and handle SQLite locking**

Set `timeout` and `PRAGMA busy_timeout`, request WAL, keep write transactions short, and catch `sqlite3.OperationalError` around POST writes with a 503 response and log context.

- [x] **Step 5: Run focused and full tests**

Run the focused tests and the complete unittest suite.

### Task 3: Gallery transaction consistency

**Files:**
- Modify: `tests/test_app.py`
- Modify: `app.py:973-1001`

**Interfaces:**
- Uploads use temporary files in the gallery directory and rename only after database commit.
- Failed upload commits remove all files created by that request.

- [x] **Step 1: Write failing tests**

Add a test that forces `commit()` to fail during upload and asserts no gallery file and no `site_images` row remain. Add a deletion failure test that asserts the database row remains recoverable when unlink fails.

- [x] **Step 2: Run focused tests**

Run the new gallery tests and confirm the current implementation leaves an orphan file and deletes metadata before unlinking.

- [x] **Step 3: Implement temporary-file upload flow**

Write each payload to a uniquely named temporary file, insert metadata in one transaction, commit, then atomically rename. On any exception roll back and unlink every temporary or finalized file from that request.

- [x] **Step 4: Implement recoverable deletion**

Delete the file first only when the database operation can be rolled back safely, otherwise retain metadata and expose a logged cleanup failure. Keep public routes from serving missing files.

- [x] **Step 5: Run focused and full tests**

Run gallery tests and the full unittest suite.

### Task 4: Session lifecycle, logout CSRF, and server-side input validation

**Files:**
- Modify: `tests/test_app.py`
- Modify: `app.py:48-115, 741-747, 923-1067`

**Interfaces:**
- Session entries carry creation and last-seen timestamps and expire after a fixed TTL.
- Logout requires the existing CSRF token.
- Content save routes reject empty required fields and overlong values before SQL execution.

- [x] **Step 1: Write failing tests**

Add tests for expired sessions, password rotation invalidating existing sessions, logout without CSRF returning 403, empty title/name/summary/controls returning 400, and overlong field rejection.

- [x] **Step 2: Run focused tests**

Run the new tests and confirm current sessions remain valid, logout succeeds without a token, and empty fields save.

- [x] **Step 3: Implement session lifecycle**

Store timestamps in `SESSIONS`, remove expired entries during lookup, add a fixed TTL constant, and clear active sessions after password rotation.

- [x] **Step 4: Enforce CSRF and field rules**

Move logout through the shared admin and CSRF gate, then add resource-specific required and maximum-length validation before each insert or update.

- [x] **Step 5: Run focused and full tests**

Run focused tests and the complete unittest suite.

### Task 5: Growth and release safeguards

**Files:**
- Modify: `tests/test_app.py`
- Modify: `app.py:555-660, 767-786`
- Modify: `compose.yaml`
- Modify: `Dockerfile`
- Create: `.github/workflows/test.yml`

**Interfaces:**
- Public and admin lists accept bounded page parameters and use matching indexes.
- `serve_file` streams fixed-size chunks.
- Readiness checks database and data-directory writability.
- CI runs unit tests, compile checks, Compose config, and image build.

- [x] **Step 1: Write failing tests**

Add tests that list more than one page, assert bounded item counts, verify file responses read in chunks, and check the readiness function for a writable and unwritable data directory.

- [x] **Step 2: Run focused tests**

Confirm current listing renders every row and `serve_file` calls `read_bytes()`.

- [x] **Step 3: Implement bounded queries and indexes**

Add validated `page` and `limit` parameters with conservative defaults, add indexes for published/status and updated-time ordering, and keep existing URLs serving page one.

- [x] **Step 4: Stream files and add readiness**

Use `stat().st_size` and bounded reads, retain current containment checks, and add a readiness helper used by the Compose healthcheck.

- [x] **Step 5: Pin release inputs and add CI**

Pin base images to explicit Python and Caddy release tags, add a minimal GitHub Actions workflow, and keep the workflow free of secrets or deployment actions.

- [x] **Step 6: Run complete verification**

Run all tests, `python3 -m py_compile app.py tests/test_app.py`, `docker compose -f compose.yaml config --quiet`, and `git diff --check`.
