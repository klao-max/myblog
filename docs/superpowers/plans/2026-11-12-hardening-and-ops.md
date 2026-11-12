# Signal Desk Hardening and Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Harden the single-container Signal Desk application for safe production deployment, correct publishing behavior, and repeatable regression checks.

**Architecture:** Keep the current standard-library stack and single entry point for this pass, but isolate new validation, configuration, rate-limit, and session-signing helpers behind small functions. Add focused tests around pure helpers and SQLite-backed rendering/update behavior before changing production code.

**Tech Stack:** Python 3.12 standard library, `sqlite3`, `http.server`, Docker Compose, `unittest`.

**Spec:** `PRODUCT.md`, `requirements.md`, and the project health findings recorded in the analysis response.

## Global Constraints

- Preserve the current standard-library-only runtime and Docker deployment model.
- Do not expose secrets in source, docs, test output, or reports.
- Published public content must be explicitly published and valid.
- External links must accept only `http` and `https` URLs.
- Requests must have bounded bodies and login failures must be rate-limited per process.
- Every behavior change must have a regression test that failed before the implementation.

### Task 1: Secure runtime configuration and sessions

**Files:**
- Modify: `app.py:19-28, 422-445, 530-543, 584-588`
- Modify: `compose.yaml:1-15`
- Modify: `README.md:5-20, 67-72`
- Test: `tests/test_app.py`

- [x] Write failing tests for production configuration validation, signed session cookies, and secure-cookie flags.
- [x] Run the focused tests and confirm they fail for the missing helpers/behavior.
- [x] Implement environment-aware configuration validation, HMAC-bound session cookies, and production cookie flags.
- [x] Run the focused tests and confirm they pass.

### Task 2: Validate content boundaries and publication state

**Files:**
- Modify: `app.py:175-177, 323-337, 548-579`
- Test: `tests/test_app.py`

- [x] Write failing tests for URL protocol rejection, slug rejection, archived project hiding, game bundle validation, and publish/retract timestamps.
- [x] Run the focused tests and confirm the expected failures.
- [x] Implement centralized validators and correct state transitions.
- [x] Run the focused tests and confirm they pass.

### Task 3: Bound requests and login attempts

**Files:**
- Modify: `app.py:28, 466-470, 525-548`
- Test: `tests/test_app.py`

- [x] Write failing tests for oversized form rejection and repeated login throttling.
- [x] Run the focused tests and confirm failure.
- [x] Implement body-size checks and a bounded per-client login failure window.
- [x] Run the focused tests and confirm they pass.

### Task 4: Deployment and regression coverage

**Files:**
- Modify: `Dockerfile`, `compose.yaml`, `README.md`
- Create: `tests/test_app.py`

- [x] Add tests for seed data, escaping, and the corrected public routes.
- [x] Add a non-root Docker user and healthcheck, and correct local-port documentation.
- [x] Run compilation, all tests, and static checks.
- [x] Review the diff for accidental secret/default exposure.
