from __future__ import annotations

import hashlib
import hmac
import html
import mimetypes
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse


ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.getenv("DATABASE_PATH", DATA_DIR / "blog.db"))
PORT = int(os.getenv("PORT", "8000"))
HOST = os.getenv("HOST", "0.0.0.0")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
APP_ENV = os.getenv("APP_ENV", "development").lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
SESSION_SECRET = os.getenv("SESSION_SECRET")

MAX_BODY_BYTES = 256 * 1024
LOGIN_WINDOW_SECONDS = 300
LOGIN_MAX_ATTEMPTS = 10

SESSIONS: dict[str, dict[str, str]] = {}
LOGIN_FAILURES: dict[str, list[float]] = {}


class RequestTooLarge(ValueError):
    pass


def validate_runtime_config(environment: str, admin_password: str | None, session_secret: str | None) -> None:
    if not admin_password or not session_secret:
        raise ValueError("ADMIN_PASSWORD and SESSION_SECRET are required")
    if environment == "production" and admin_password in {"admin123!", "change-this-password"}:
        raise ValueError("a non-default ADMIN_PASSWORD is required in production")
    if environment == "production" and session_secret in {"local-development-secret", "change-this-session-secret"}:
        raise ValueError("a non-default SESSION_SECRET is required in production")


def _session_signature(token: str) -> str:
    if not SESSION_SECRET:
        raise ValueError("SESSION_SECRET is not configured")
    return hmac.new(SESSION_SECRET.encode(), token.encode(), hashlib.sha256).hexdigest()


def build_session_cookie(token: str, environment: str | None = None) -> str:
    environment = environment or APP_ENV
    value = f"{token}.{_session_signature(token)}"
    flags = "Path=/; HttpOnly; SameSite=Lax"
    if environment == "production":
        flags += "; Secure"
    return f"signal_session={value}; {flags}"


def clear_session_cookie(environment: str | None = None) -> str:
    flags = "Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
    if (environment or APP_ENV) == "production":
        flags += "; Secure"
    return f"signal_session=; {flags}"


def verify_session_cookie(value: str) -> str | None:
    try:
        token, signature = value.rsplit(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(signature, _session_signature(token)):
        return None
    return token


def _login_attempts(ip: str, now: float | None = None) -> list[float]:
    now = now if now is not None else time.time()
    attempts = [stamp for stamp in LOGIN_FAILURES.get(ip, []) if now - stamp < LOGIN_WINDOW_SECONDS]
    LOGIN_FAILURES[ip] = attempts
    return attempts


def record_login_failure(ip: str, now: float | None = None) -> None:
    now = now if now is not None else time.time()
    _login_attempts(ip, now).append(now)


def clear_login_failures(ip: str) -> None:
    LOGIN_FAILURES.pop(ip, None)


def is_login_rate_limited(ip: str, now: float | None = None) -> bool:
    return len(_login_attempts(ip, now)) >= LOGIN_MAX_ATTEMPTS

SCHEMA = """
CREATE TABLE IF NOT EXISTS admins (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  slug TEXT UNIQUE NOT NULL,
  excerpt TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '',
  cover TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'draft',
  published_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  slug TEXT UNIQUE NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  tech_stack TEXT NOT NULL DEFAULT '',
  github_url TEXT NOT NULL DEFAULT '',
  demo_url TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  featured INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS games (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  slug TEXT UNIQUE NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  controls TEXT NOT NULL DEFAULT '',
  source_url TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'published',
  featured INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    seed(conn)
    return conn


def normalize_password(password: str) -> str:
    return password.replace("！", "!")


def password_hash(password: str, salt: str | None = None) -> str:
    password = normalize_password(password)
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 210_000).hex()
    return f"{salt}${digest}"


def password_matches(password: str, stored: str) -> bool:
    password = normalize_password(password)
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 210_000).hex()
    return hmac.compare_digest(candidate, digest)


def rotate_admin_password(conn: sqlite3.Connection, username: str, current_password: str, new_password: str) -> None:
    if len(new_password) < 12:
        raise ValueError("新密码至少需要 12 个字符")
    row = conn.execute("SELECT id, password_hash FROM admins WHERE username = ?", (username,)).fetchone()
    if not row or not password_matches(current_password, row["password_hash"]):
        raise ValueError("当前密码不正确")
    conn.execute("UPDATE admins SET password_hash = ? WHERE id = ?", (password_hash(new_password), row["id"]))
    conn.commit()


def seed(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    admin = cur.execute("SELECT id FROM admins WHERE username = ?", (ADMIN_USERNAME,)).fetchone()
    if not admin:
        cur.execute(
            "INSERT INTO admins(username, password_hash, created_at) VALUES (?, ?, ?)",
            (ADMIN_USERNAME, password_hash(ADMIN_PASSWORD), now_iso()),
        )
    if cur.execute("SELECT COUNT(*) AS n FROM posts").fetchone()["n"] == 0:
        cur.execute(
            """INSERT INTO posts(title, slug, excerpt, body, tags, status, published_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'published', ?, ?, ?)""",
            (
                "把作品变成可访问的链接",
                "turn-work-into-links",
                "一个个人站点的价值，不只是存档，也是一条让别人走进作品的路。",
                "# 把作品变成可访问的链接\n\n博客、项目和游戏放在同一处之后，发布就变成了一种持续的整理。每一次更新，都是给未来的自己留下一条更清楚的线索。\n\n> 先让作品可见，再慢慢把它做得更好。\n\n## 第一版的原则\n\n- 写作保持简单\n- 项目带着源代码走\n- 游戏打开就能玩",
                "随笔,产品,建站",
                now_iso(),
                now_iso(),
                now_iso(),
            ),
        )
    if cur.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"] == 0:
        cur.execute(
            """INSERT INTO projects(name, slug, summary, description, tech_stack, github_url, demo_url, status, featured, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, ?)""",
            (
                "Signal Desk",
                "signal-desk",
                "一个把文章、项目和小实验放在一起的个人工作台。",
                "这是这个博客的第一块基础设施：公开内容保持轻，后台编辑保持快。",
                "Python, SQLite, Docker",
                "https://github.com/your-name/signal-desk",
                "",
                now_iso(),
                now_iso(),
            ),
        )
    if cur.execute("SELECT COUNT(*) AS n FROM games").fetchone()["n"] == 0:
        cur.execute(
            """INSERT INTO games(name, slug, summary, description, controls, source_url, status, featured, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'published', 1, ?, ?)""",
            (
                "Signal Runner",
                "signal-runner",
                "用方向键接住光点，看看你能坚持多久。",
                "一个不需要安装的十秒钟小游戏，放在这里是为了提醒自己：小作品也值得有一个入口。",
                "方向键 / WASD 移动，R 重新开始",
                "",
                now_iso(),
                now_iso(),
            ),
        )
    conn.commit()


def esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def slugify(text: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", text.strip().lower()).strip("-")
    return value or f"entry-{secrets.token_hex(3)}"


def validate_slug(value: str) -> str:
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9\u4e00-\u9fff]+(?:-[a-z0-9\u4e00-\u9fff]+)*", value):
        raise ValueError("slug 只能包含字母、数字、中文和连字符")
    return value


def validate_external_url(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("外链必须使用 http 或 https")
    return value


def next_published_at(previous_status: str, previous_published_at: str | None, new_status: str, timestamp: str) -> str | None:
    if new_status != "published":
        return None
    if previous_status == "published" and previous_published_at:
        return previous_published_at
    return timestamp


def validate_game_bundle(slug: str, status: str) -> str:
    slug = validate_slug(slug)
    if status == "published" and not (ROOT / "games" / slug / "index.html").is_file():
        raise ValueError("已发布游戏必须存在 games/<slug>/index.html")
    return slug


def fmt_date(value: str | None) -> str:
    if not value:
        return "未发布"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y.%m.%d")
    except ValueError:
        return value[:10]


def tags(value: str | None) -> list[str]:
    return [x.strip() for x in (value or "").replace("，", ",").split(",") if x.strip()]


def markdown(value: str) -> str:
    lines = value.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    in_code = False
    code_lines: list[str] = []
    list_open = False
    for line in lines:
        if line.strip().startswith("```"):
            if in_code:
                out.append(f"<pre><code>{esc(chr(10).join(code_lines))}</code></pre>")
                code_lines = []
                in_code = False
            else:
                if list_open:
                    out.append("</ul>")
                    list_open = False
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not line.strip():
            if list_open:
                out.append("</ul>")
                list_open = False
            continue
        if line.startswith(">"):
            if list_open:
                out.append("</ul>")
                list_open = False
            out.append(f"<blockquote>{inline(line[1:].strip())}</blockquote>")
        elif line.startswith("### "):
            out.append(f"<h3>{inline(line[4:])}</h3>")
        elif line.startswith("## "):
            out.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("# "):
            out.append(f"<h2>{inline(line[2:])}</h2>")
        elif re.match(r"^[-*] ", line):
            if not list_open:
                out.append("<ul>")
                list_open = True
            out.append(f"<li>{inline(line[2:])}</li>")
        else:
            if list_open:
                out.append("</ul>")
                list_open = False
            out.append(f"<p>{inline(line)}</p>")
    if list_open:
        out.append("</ul>")
    if in_code:
        out.append(f"<pre><code>{esc(chr(10).join(code_lines))}</code></pre>")
    return "\n".join(out)


def inline(value: str) -> str:
    safe = esc(value)
    safe = re.sub(r"\[([^]]+)\]\((https?://[^)]+)\)", r'<a href="\2" target="_blank" rel="noreferrer">\1</a>', safe)
    safe = re.sub(r"`([^`]+)`", r"<code>\1</code>", safe)
    safe = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", safe)
    return safe


def cookie_value(headers, key: str) -> str | None:
    raw = headers.get("Cookie", "")
    jar = SimpleCookie()
    jar.load(raw)
    return jar[key].value if key in jar else None


def render_page(title: str, content: str, *, admin: bool = False, active: str = "") -> str:
    if admin:
        nav = f"""
        <header class=\"admin-top\"><a class=\"brand brand-admin\" href=\"/admin\"><span class=\"brand-dot\"></span>个人工作台</a>
        <div class=\"admin-top-actions\"><a class=\"quiet-link\" href=\"/admin/password\">修改密码</a><a class=\"quiet-link\" href=\"/\" target=\"_blank\">查看站点 ↗</a><form method=\"post\" action=\"/admin/logout\"><button class=\"button button-ghost\">退出</button></form></div></header>
        <div class=\"admin-layout\"><aside class=\"admin-rail\"><p class=\"rail-label\">工作区</p>
        <a class=\"rail-link {"is-active" if active == "overview" else ""}\" href=\"/admin\">概览</a>
        <a class=\"rail-link {"is-active" if active == "posts" else ""}\" href=\"/admin/posts\">文章 <span>⌘</span></a>
        <a class=\"rail-link {"is-active" if active == "projects" else ""}\" href=\"/admin/projects\">项目 <span>↗</span></a>
        <a class=\"rail-link {"is-active" if active == "games" else ""}\" href=\"/admin/games\">游戏 <span>▶</span></a>
        <div class=\"rail-bottom\"><p class=\"rail-label\">状态</p><p class=\"rail-note\">本地 SQLite<br>Docker-ready</p></div></aside><main class=\"admin-main\">{content}</main></div>
        """
    else:
        nav = f"""
        <header class=\"site-header wrap\"><a class=\"brand\" href=\"/\"><span class=\"brand-dot\"></span>Signal Desk</a>
        <nav class=\"site-nav\"><a class="{'is-active' if active == 'blog' else ''}" href=\"/blog\">文章</a><a class="{'is-active' if active == 'projects' else ''}" href=\"/projects\">项目</a><a class="{'is-active' if active == 'games' else ''}" href=\"/games\">游戏</a><a href=\"/about\">关于</a></nav></header>
        """
    body_class = "admin-body" if admin else "public-body"
    return f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><meta name=\"description\" content=\"个人博客、项目和浏览器游戏\"><title>{esc(title)} · Signal Desk</title><link rel=\"stylesheet\" href=\"/static/style.css\"></head><body class=\"{body_class}\">{nav}{content}<script src=\"/static/app.js\" defer></script></body></html>"""


def status_badge(status: str) -> str:
    labels = {"published": "已发布", "draft": "草稿", "active": "进行中", "paused": "暂停", "archived": "归档"}
    return f'<span class="status status-{esc(status)}"><i></i>{labels.get(status, status)}</span>'


def public_card(kind: str, title: str, summary: str, href: str, meta: str, extra: str = "") -> str:
    return f"""<article class=\"content-card\"><div class=\"card-top\"><span class=\"card-kind\">{esc(kind)}</span><span class=\"card-meta\">{esc(meta)}</span></div><h3><a href=\"{href}\">{esc(title)}</a></h3><p>{esc(summary)}</p>{extra}<a class=\"text-link\" href=\"{href}\">打开内容 <span>↗</span></a></article>"""


def home(conn: sqlite3.Connection) -> str:
    posts = conn.execute("SELECT * FROM posts WHERE status='published' ORDER BY published_at DESC, id DESC LIMIT 3").fetchall()
    projects = conn.execute("SELECT * FROM projects WHERE status != 'archived' ORDER BY featured DESC, updated_at DESC LIMIT 3").fetchall()
    games = conn.execute("SELECT * FROM games WHERE status='published' ORDER BY featured DESC, updated_at DESC LIMIT 3").fetchall()
    post_html = "".join(public_card("文章", r["title"], r["excerpt"], f"/blog/{quote(r['slug'])}", fmt_date(r["published_at"]), f'<div class="tag-line">{" ".join("#" + esc(t) for t in tags(r["tags"]))}</div>') for r in posts)
    project_html = "".join(public_card("项目", r["name"], r["summary"], f"/projects/{quote(r['slug'])}", r["status"], f'<div class="tag-line">{" · ".join(esc(t) for t in tags(r["tech_stack"]))}</div>') for r in projects)
    game_html = "".join(public_card("游戏", r["name"], r["summary"], f"/games/{quote(r['slug'])}", "可玩", '<span class="play-mark">▶ 立即开始</span>') for r in games)
    return render_page("首页", f"""
    <main><section class=\"hero wrap\"><div class=\"hero-copy\"><p class=\"kicker\">PERSONAL WORKSHOP · 2026</p><h1>写下来，<br><em>做出来。</em></h1><p class=\"hero-lead\">这里收集文章、项目和一些可以直接打开的小游戏。一个人的工作台，也可以是一张公开的地图。</p><div class=\"hero-actions\"><a class=\"button button-primary\" href=\"/blog\">阅读文章 <span>↗</span></a><a class=\"button button-secondary\" href=\"/projects\">查看项目</a></div></div><div class=\"hero-note\"><span class=\"note-line\"></span><p>当前状态</p><strong>持续更新中</strong><small>文章 / 项目 / 游戏</small></div></section>
    <section class=\"lane-section wrap\"><div class=\"section-head\"><div><p class=\"section-index\">01 — 文章</p><h2>把想法写成可回看的东西。</h2></div><a class=\"text-link\" href=\"/blog\">全部文章 ↗</a></div><div class=\"card-grid\">{post_html or '<p class="empty-state">还没有发布文章。</p>'}</div></section>
    <section class=\"lane-section wrap section-dark\"><div class=\"section-head\"><div><p class=\"section-index\">02 — 项目</p><h2>正在做的，和已经做完的。</h2></div><a class=\"text-link\" href=\"/projects\">全部项目 ↗</a></div><div class=\"card-grid\">{project_html or '<p class="empty-state">还没有项目。</p>'}</div></section>
    <section class=\"lane-section wrap\"><div class=\"section-head\"><div><p class=\"section-index\">03 — 游戏</p><h2>小东西也应该有自己的入口。</h2></div><a class=\"text-link\" href=\"/games\">全部游戏 ↗</a></div><div class=\"card-grid\">{game_html or '<p class="empty-state">还没有游戏。</p>'}</div></section>
    <footer class=\"site-footer wrap\"><span>Signal Desk / 个人工作台</span><span>Built slowly, shipped often.</span></footer></main>""", active="")


def listing(conn: sqlite3.Connection, kind: str) -> str:
    if kind == "blog":
        rows = conn.execute("SELECT * FROM posts WHERE status='published' ORDER BY published_at DESC, id DESC").fetchall()
        cards = "".join(public_card("文章", r["title"], r["excerpt"], f"/blog/{quote(r['slug'])}", fmt_date(r["published_at"]), f'<div class="tag-line">{" ".join("#" + esc(t) for t in tags(r["tags"]))}</div>') for r in rows)
        title, intro = "文章", "把正在思考的事情，写成可以被重新打开的页面。"
    elif kind == "projects":
        rows = conn.execute("SELECT * FROM projects WHERE status != 'archived' ORDER BY featured DESC, updated_at DESC").fetchall()
        cards = "".join(public_card("项目", r["name"], r["summary"], f"/projects/{quote(r['slug'])}", r["status"], f'<div class="tag-line">{" · ".join(esc(t) for t in tags(r["tech_stack"]))}</div>') for r in rows)
        title, intro = "项目", "代码是想法留下的另一种形状。"
    else:
        rows = conn.execute("SELECT * FROM games WHERE status='published' ORDER BY featured DESC, updated_at DESC").fetchall()
        cards = "".join(public_card("游戏", r["name"], r["summary"], f"/games/{quote(r['slug'])}", "可玩", '<span class="play-mark">▶ 立即开始</span>') for r in rows)
        title, intro = "游戏", "一些小而完整的实验，打开浏览器就能开始。"
    return render_page(title, f"""<main class=\"subpage wrap\"><div class=\"subpage-intro\"><p class=\"kicker\">SIGNAL DESK / {kind.upper()}</p><h1>{title}</h1><p>{intro}</p></div><div class=\"card-grid card-grid-wide\">{cards or '<p class="empty-state">这里暂时是空的，稍后会更新。</p>'}</div></main><footer class=\"site-footer wrap\"><span>Signal Desk</span><span>Keep making.</span></footer>""", active=kind)


def detail(conn: sqlite3.Connection, kind: str, slug: str) -> tuple[int, str]:
    table = {"blog": "posts", "projects": "projects", "games": "games"}[kind]
    row = conn.execute(f"SELECT * FROM {table} WHERE slug = ?", (slug,)).fetchone()
    if not row or (kind == "blog" and row["status"] != "published") or (kind == "games" and row["status"] != "published") or (kind == "projects" and row["status"] == "archived"):
        return 404, render_page("找不到内容", '<main class="subpage wrap"><div class="empty-state large"><p>这条内容不存在，或者还在草稿箱里。</p><a class="button button-primary" href="/">回到首页</a></div></main>')
    if kind == "blog":
        body = f"""<main class=\"article-page wrap\"><a class=\"back-link\" href=\"/blog\">← 返回文章</a><div class=\"article-head\"><p class=\"kicker\">{fmt_date(row['published_at'])} · {" / ".join(tags(row['tags']))}</p><h1>{esc(row['title'])}</h1><p class=\"article-lead\">{esc(row['excerpt'])}</p></div><article class=\"article-body\">{markdown(row['body'])}</article></main>"""
        return 200, render_page(row["title"], body, active="blog")
    if kind == "projects":
        links = (f'<a class="button button-primary" target="_blank" rel="noreferrer" href="{esc(row["github_url"])}">打开 GitHub ↗</a>' if row["github_url"] else "") + (f'<a class="button button-secondary" target="_blank" rel="noreferrer" href="{esc(row["demo_url"])}">查看 Demo ↗</a>' if row["demo_url"] else "")
        body = f"""<main class=\"detail-page wrap\"><a class=\"back-link\" href=\"/projects\">← 返回项目</a><div class=\"detail-head\"><p class=\"kicker\">PROJECT / {esc(row['status']).upper()}</p><h1>{esc(row['name'])}</h1><p class=\"article-lead\">{esc(row['summary'])}</p><div class=\"hero-actions\">{links}</div></div><div class=\"detail-grid\"><div><h2>关于这个项目</h2><div class=\"rich-copy\">{markdown(row['description'])}</div></div><aside class=\"detail-aside\"><p>技术栈</p><div class=\"tag-stack\">{"".join(f'<span>{esc(t)}</span>' for t in tags(row['tech_stack'])) or '<span>持续整理中</span>'}</div><p>最后更新</p><strong>{fmt_date(row['updated_at'])}</strong></aside></div></main>"""
        return 200, render_page(row["name"], body, active="projects")
    body = f"""<main class=\"detail-page wrap\"><a class=\"back-link\" href=\"/games\">← 返回游戏</a><div class=\"game-intro\"><div><p class=\"kicker\">PLAYABLE EXPERIMENT</p><h1>{esc(row['name'])}</h1><p class=\"article-lead\">{esc(row['summary'])}</p></div><a class=\"button button-primary\" href=\"/games/{quote(row['slug'])}/play\">开始游戏 <span>▶</span></a></div><div class=\"game-panel\"><div class=\"game-panel-top\"><span>PLAY AREA</span><span>{esc(row['controls'])}</span></div><div class=\"game-placeholder\"><span class=\"play-orb\">▶</span><p>准备好了吗？</p><a class=\"text-link\" href=\"/games/{quote(row['slug'])}/play\">打开游戏 ↗</a></div></div><div class=\"detail-grid\"><div><h2>关于这个游戏</h2><div class=\"rich-copy\">{markdown(row['description'])}</div></div><aside class=\"detail-aside\"><p>操作方式</p><strong>{esc(row['controls']) or '打开后查看'}</strong>{f'<p>源码</p><a class="text-link" href="{esc(row["source_url"])}" target="_blank" rel="noreferrer">GitHub ↗</a>' if row['source_url'] else ''}</aside></div></main>"""
    return 200, render_page(row["name"], body, active="games")


def about() -> str:
    return render_page("关于", """<main class="subpage wrap about-page"><p class="kicker">ABOUT / SIGNAL DESK</p><h1>一个人的工作台，<br>也可以对外开放。</h1><div class="about-columns"><p>这里是我的博客、项目归档和小游戏入口。文章记录判断，项目记录行动，游戏记录那些还没必要被解释太多的念头。</p><p>如果你想联系我，最直接的方式是从某个项目的 GitHub 仓库开始。</p></div></main><footer class="site-footer wrap"><span>Signal Desk</span><span>Keep making.</span></footer>""")


def admin_shell(conn: sqlite3.Connection) -> dict[str, object]:
    return {
        "posts": conn.execute("SELECT * FROM posts ORDER BY updated_at DESC, id DESC").fetchall(),
        "projects": conn.execute("SELECT * FROM projects ORDER BY updated_at DESC, id DESC").fetchall(),
        "games": conn.execute("SELECT * FROM games ORDER BY updated_at DESC, id DESC").fetchall(),
    }


def admin_dashboard(conn: sqlite3.Connection) -> str:
    data = admin_shell(conn)
    post_count = len(data["posts"])
    project_count = len(data["projects"])
    game_count = len(data["games"])
    latest = list(data["posts"][:4])
    rows = "".join(f'<a class="activity-row" href="/admin/posts/edit?id={r["id"]}"><span>{esc(r["title"])}</span><span>{status_badge(r["status"])}</span><time>{fmt_date(r["updated_at"])}</time></a>' for r in latest)
    content = f"""<div class=\"admin-heading\"><div><p class=\"kicker\">WORKSPACE / OVERVIEW</p><h1>今天，先把一个想法发布出去。</h1><p>这里是你的内容控制台。文章、项目和游戏都从这里进入公开站点。</p></div><a class=\"button button-primary\" href=\"/admin/posts/new\">写新文章 <span>＋</span></a></div><section class=\"stat-strip\"><div><span>文章</span><strong>{post_count:02d}</strong><small>含草稿</small></div><div><span>项目</span><strong>{project_count:02d}</strong><small>持续维护</small></div><div><span>游戏</span><strong>{game_count:02d}</strong><small>可直接打开</small></div></section><section class=\"admin-section\"><div class=\"admin-section-head\"><div><p class=\"section-index\">最近活动</p><h2>文章编辑轨迹</h2></div><a class=\"quiet-link\" href=\"/admin/posts\">管理全部 ↗</a></div><div class=\"activity-list\">{rows or '<p class="empty-state">还没有文章，先写下第一篇。</p>'}</div></section><section class=\"quick-grid\"><a href=\"/admin/projects/new\"><span>↗</span><strong>添加项目</strong><small>放上 GitHub 链接</small></a><a href=\"/admin/games/new\"><span>▶</span><strong>添加游戏</strong><small>登记一个可玩的实验</small></a></section>"""
    return render_page("后台概览", content, admin=True, active="overview")


def admin_list(conn: sqlite3.Connection, kind: str) -> str:
    table = {"posts": "posts", "projects": "projects", "games": "games"}[kind]
    rows = conn.execute(f"SELECT * FROM {table} ORDER BY updated_at DESC, id DESC").fetchall()
    if kind == "posts":
        title, action, label = "文章", "/admin/posts/new", "写新文章"
        row_html = "".join(f'<a class="manage-row" href="/admin/posts/edit?id={r["id"]}"><span class="manage-title">{esc(r["title"])}</span><span>{status_badge(r["status"])}</span><span class="manage-muted">{", ".join(tags(r["tags"]))}</span><time>{fmt_date(r["updated_at"])}</time><b>编辑 ↗</b></a>' for r in rows)
    elif kind == "projects":
        title, action, label = "项目", "/admin/projects/new", "添加项目"
        row_html = "".join(f'<a class="manage-row" href="/admin/projects/edit?id={r["id"]}"><span class="manage-title">{esc(r["name"])}</span><span>{status_badge(r["status"])}</span><span class="manage-muted">{", ".join(tags(r["tech_stack"]))}</span><time>{fmt_date(r["updated_at"])}</time><b>编辑 ↗</b></a>' for r in rows)
    else:
        title, action, label = "游戏", "/admin/games/new", "添加游戏"
        row_html = "".join(f'<a class="manage-row" href="/admin/games/edit?id={r["id"]}"><span class="manage-title">{esc(r["name"])}</span><span>{status_badge(r["status"])}</span><span class="manage-muted">{esc(r["controls"])}</span><time>{fmt_date(r["updated_at"])}</time><b>编辑 ↗</b></a>' for r in rows)
    content = f"""<div class=\"admin-heading\"><div><p class=\"kicker\">WORKSPACE / {kind.upper()}</p><h1>{title}</h1><p>保持内容清楚、状态可见，发布动作只保留一个重点。</p></div><a class=\"button button-primary\" href=\"{action}\">{label} <span>＋</span></a></div><section class=\"manage-table\"><div class=\"manage-head\"><span>名称</span><span>状态</span><span>附加信息</span><span>更新时间</span><span></span></div>{row_html or '<p class="empty-state">还没有内容。点击右上角开始添加。</p>'}</section>"""
    return render_page(f"管理{title}", content, admin=True, active=kind)


def form_input(label: str, name: str, value: str = "", *, required: bool = False, placeholder: str = "", textarea: bool = False, rows: int = 8, input_type: str = "text") -> str:
    req = " required" if required else ""
    if textarea:
        control = f'<textarea id="{name}" name="{name}" rows="{rows}" placeholder="{esc(placeholder)}"{req}>{esc(value)}</textarea>'
    else:
        control = f'<input id="{name}" name="{name}" type="{esc(input_type)}" value="{esc(value)}" placeholder="{esc(placeholder)}"{req}>'
    return f'<label class="field"><span>{esc(label)}</span>{control}</label>'


def select_input(label: str, name: str, value: str, options: list[tuple[str, str]]) -> str:
    opts = "".join(f'<option value="{esc(v)}" {"selected" if value == v else ""}>{esc(l)}</option>' for v, l in options)
    return f'<label class="field"><span>{esc(label)}</span><select name="{name}" id="{name}">{opts}</select></label>'


def csrf_for(handler: BaseHTTPRequestHandler) -> str:
    token = handler.session().get("csrf", "")
    return f'<input type="hidden" name="csrf" value="{esc(token)}">'


def edit_form(conn: sqlite3.Connection, handler: BaseHTTPRequestHandler, kind: str, row: sqlite3.Row | None = None) -> str:
    is_new = row is None
    prefix = {"posts": "文章", "projects": "项目", "games": "游戏"}[kind]
    if kind == "posts":
        row = row or {"title": "", "slug": "", "excerpt": "", "body": "", "tags": "", "cover": "", "status": "draft"}
        fields = form_input("标题", "title", row["title"], required=True, placeholder="例如：我为什么做这个博客") + form_input("Slug", "slug", row["slug"], placeholder="留空会根据标题生成") + form_input("摘要", "excerpt", row["excerpt"], required=True, placeholder="列表页会显示这一句") + form_input("正文", "body", row["body"], textarea=True, rows=18, placeholder="# 小标题\n\n正文支持简单 Markdown。") + form_input("标签", "tags", row["tags"], placeholder="技术, 随笔, 生活") + form_input("封面地址（可选）", "cover", row["cover"], placeholder="https://...") + select_input("发布状态", "status", row["status"], [("draft", "草稿"), ("published", "已发布")])
        hint = "发布后会出现在首页和文章列表。"
    elif kind == "projects":
        row = row or {"name": "", "slug": "", "summary": "", "description": "", "tech_stack": "", "github_url": "", "demo_url": "", "status": "active", "featured": 0}
        fields = form_input("项目名称", "name", row["name"], required=True, placeholder="例如：我的工具箱") + form_input("Slug", "slug", row["slug"], placeholder="留空会根据名称生成") + form_input("一句话简介", "summary", row["summary"], required=True, placeholder="它解决了什么问题？") + form_input("详细说明", "description", row["description"], textarea=True, rows=12, placeholder="项目背景、做法和结果。") + form_input("技术栈", "tech_stack", row["tech_stack"], placeholder="Python, Canvas, SQLite") + form_input("GitHub 地址", "github_url", row["github_url"], placeholder="https://github.com/...") + form_input("Demo 地址（可选）", "demo_url", row["demo_url"], placeholder="https://...") + select_input("项目状态", "status", row["status"], [("active", "进行中"), ("paused", "暂停"), ("archived", "归档")]) + f'<label class="check-field"><input type="checkbox" name="featured" {"checked" if row["featured"] else ""}><span>在首页精选</span></label>'
        hint = "GitHub 地址会在公开项目页作为主链接展示。"
    else:
        row = row or {"name": "", "slug": "", "summary": "", "description": "", "controls": "", "source_url": "", "status": "published", "featured": 0}
        fields = form_input("游戏名称", "name", row["name"], required=True, placeholder="例如：Signal Runner") + form_input("Slug", "slug", row["slug"], placeholder="必须对应 games/<slug>/index.html") + form_input("一句话简介", "summary", row["summary"], required=True, placeholder="玩家打开前看到的介绍") + form_input("详细说明", "description", row["description"], textarea=True, rows=10, placeholder="玩法、灵感和目标。") + form_input("操作方式", "controls", row["controls"], required=True, placeholder="方向键移动，R 重开") + form_input("源码地址（可选）", "source_url", row["source_url"], placeholder="https://github.com/...") + select_input("发布状态", "status", row["status"], [("published", "已发布"), ("draft", "草稿")]) + f'<label class="check-field"><input type="checkbox" name="featured" {"checked" if row["featured"] else ""}><span>在首页精选</span></label>'
        hint = "将游戏文件放到 games/<slug>/index.html 后，公开页会自动提供运行入口。"
    action = f"/admin/{kind}/save"
    return render_page(f"{'编辑' if not is_new else '新建'}{prefix}", f"""<div class=\"admin-heading compact\"><div><p class=\"kicker\">WORKSPACE / {kind.upper()}</p><h1>{'编辑' if not is_new else '新建'}{prefix}</h1><p>{hint}</p></div><a class=\"quiet-link\" href=\"/admin/{kind}\">← 返回列表</a></div><form class=\"editor-form\" method=\"post\" action=\"{action}\">{csrf_for(handler)}{f'<input type="hidden" name="id" value="{row["id"]}">' if not is_new else ''}<div class=\"form-main\">{fields}</div><aside class=\"form-aside\"><div class=\"save-card\"><p>发布动作</p><strong>{'更新已有内容' if not is_new else '保存为草稿'}</strong><button class=\"button button-primary button-wide\" type=\"submit\">保存{prefix} <span>↗</span></button><p class=\"form-help\">保存后可在公开站点检查结果。</p></div></aside></form>""", admin=True, active=kind)


def parse_form_data(data: str, length: int) -> dict[str, str]:
    if length > MAX_BODY_BYTES:
        raise RequestTooLarge(f"request body exceeds {MAX_BODY_BYTES} bytes")
    parsed = parse_qs(data, keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items()}


class Handler(BaseHTTPRequestHandler):
    server_version = "SignalDesk/0.1"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def session(self) -> dict[str, str]:
        raw_token = cookie_value(self.headers, "signal_session")
        token = verify_session_cookie(raw_token) if raw_token else None
        return SESSIONS.get(token, {}) if token else {}

    def is_admin(self) -> bool:
        return bool(self.session().get("admin_id"))

    def send_html(self, body: str, status: int = 200, *, cookie: str | None = None) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(encoded)

    def redirect(self, path: str, *, cookie: str | None = None) -> None:
        self.send_response(303)
        self.send_header("Location", path)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def serve_file(self, path: Path, root: Path | None = None) -> None:
        if root is not None:
            try:
                path = path.resolve()
                root = root.resolve()
                path.relative_to(root)
            except ValueError:
                self.send_error(404)
                return
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        content = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def parse_form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_BODY_BYTES:
            raise RequestTooLarge(f"request body exceeds {MAX_BODY_BYTES} bytes")
        data = self.rfile.read(length).decode("utf-8") if length else ""
        return parse_form_data(data, length)


    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path.rstrip("/")) or "/"
        conn = db()
        try:
            if path.startswith("/static/"):
                self.serve_file(ROOT / path.lstrip("/"), ROOT / "static")
                return
            if path.startswith("/games/") and path.endswith("/play"):
                slug = path.split("/")[2]
                try:
                    slug = validate_slug(slug)
                except ValueError:
                    self.send_error(404)
                    return
                game = conn.execute("SELECT status FROM games WHERE slug = ?", (slug,)).fetchone()
                if not game or game["status"] != "published":
                    self.send_error(404)
                    return
                self.serve_file(ROOT / "games" / slug / "index.html", ROOT / "games")
                return
            if path == "/":
                self.send_html(home(conn)); return
            if path == "/blog":
                self.send_html(listing(conn, "blog")); return
            if path == "/projects":
                self.send_html(listing(conn, "projects")); return
            if path == "/games":
                self.send_html(listing(conn, "games")); return
            if path == "/about":
                self.send_html(about()); return
            for kind in ("blog", "projects", "games"):
                if path.startswith(f"/{kind}/"):
                    slug = path.split("/", 2)[2]
                    status, body = detail(conn, kind, slug)
                    self.send_html(body, status); return
            if path == "/admin/login":
                if self.is_admin():
                    self.redirect("/admin"); return
                self.send_html(render_page("登录后台", """<main class="login-page"><div class="login-card"><p class="kicker">SIGNAL DESK / ADMIN</p><h1>回到工作台。</h1><p>单管理员入口。发布内容之前，先确认这是你。</p><form method="post" action="/admin/login"><label class="field"><span>用户名</span><input name="username" autocomplete="username" required value="admin"></label><label class="field"><span>密码</span><input name="password" type="password" autocomplete="current-password" required></label><button class="button button-primary button-wide" type="submit">登录后台 ↗</button></form><p class="login-help">密码由部署环境变量 ADMIN_PASSWORD 提供。</p></div></main>""")); return
            if path == "/admin/password":
                if not self.is_admin(): self.redirect("/admin/login"); return
                content = f'''<div class="admin-heading compact"><div><p class="kicker">WORKSPACE / SECURITY</p><h1>修改密码</h1><p>修改后，当前登录会话仍然有效。</p></div><a class="quiet-link" href="/admin">← 返回后台</a></div><form class="editor-form" method="post" action="/admin/password">{csrf_for(self)}<div class="form-main">{form_input("当前密码", "current_password", textarea=False, input_type="password")}{form_input("新密码", "new_password", textarea=False, input_type="password")}{form_input("确认新密码", "confirm_password", textarea=False, input_type="password")}</div><aside class="form-aside"><div class="save-card"><p>安全设置</p><strong>至少 12 个字符</strong><button class="button button-primary button-wide" type="submit">更新密码 ↗</button></div></aside></form>'''
                self.send_html(render_page("修改密码", content, admin=True, active="overview")); return
            if path == "/admin" or path == "/admin/":
                if not self.is_admin(): self.redirect("/admin/login"); return
                self.send_html(admin_dashboard(conn)); return
            if path in ("/admin/posts", "/admin/projects", "/admin/games"):
                if not self.is_admin(): self.redirect("/admin/login"); return
                self.send_html(admin_list(conn, path.split("/")[-1])); return
            m = re.match(r"^/admin/(posts|projects|games)/(new|edit)$", path)
            if m:
                if not self.is_admin(): self.redirect("/admin/login"); return
                kind, action = m.groups()
                row = None
                if action == "edit":
                    query = parse_qs(urlparse(self.path).query)
                    row_id = query.get("id", [""])[0]
                    table = {"posts": "posts", "projects": "projects", "games": "games"}[kind]
                    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
                    if not row:
                        self.send_html(render_page("找不到内容", '<main class="subpage wrap"><div class="empty-state large"><p>这条内容已经不存在。</p></div></main>', admin=True), 404); return
                self.send_html(edit_form(conn, self, kind, row)); return
            self.send_error(404)
        finally:
            conn.close()

    def do_POST(self) -> None:
        path = unquote(urlparse(self.path).path.rstrip("/")) or "/"
        try:
            form = self.parse_form()
        except RequestTooLarge:
            self.send_error(413, "请求内容过大")
            return
        conn = db()
        client_ip = self.client_address[0]
        try:
            if path == "/admin/login":
                if is_login_rate_limited(client_ip):
                    self.send_error(429, "登录尝试过多，请稍后再试")
                    return
                row = conn.execute("SELECT * FROM admins WHERE username = ?", (form.get("username", ""),)).fetchone()
                if row and password_matches(form.get("password", ""), row["password_hash"]):
                    clear_login_failures(client_ip)
                    token = secrets.token_urlsafe(32)
                    SESSIONS[token] = {"admin_id": str(row["id"]), "csrf": secrets.token_urlsafe(24)}
                    self.redirect("/admin", cookie=build_session_cookie(token))
                else:
                    record_login_failure(client_ip)
                    self.send_html(render_page("登录失败", """<main class="login-page"><div class="login-card"><p class="kicker">SIGNAL DESK / ADMIN</p><h1>密码不对。</h1><p class="error-copy">请重新输入管理员账号和密码。</p><a class="button button-primary button-wide" href="/admin/login">返回登录</a></div></main>"""), 401)
                return
            if path == "/admin/logout":
                raw_token = cookie_value(self.headers, "signal_session")
                token = verify_session_cookie(raw_token) if raw_token else None
                if token:
                    SESSIONS.pop(token, None)
                self.redirect("/", cookie=clear_session_cookie())
                return
            if not self.is_admin():
                self.redirect("/admin/login"); return
            if form.get("csrf") != self.session().get("csrf"):
                self.send_html(render_page("请求失效", '<main class="subpage wrap"><div class="empty-state large"><p>编辑会话已失效，请返回后台重试。</p><a class="button button-primary" href="/admin">回到后台</a></div></main>', admin=True), 403); return
            if path == "/admin/password":
                if form.get("new_password") != form.get("confirm_password"):
                    raise ValueError("两次输入的新密码不一致")
                username = ADMIN_USERNAME or "admin"
                rotate_admin_password(conn, username, form.get("current_password", ""), form.get("new_password", ""))
                self.redirect("/admin")
                return
            m = re.match(r"^/admin/(posts|projects|games)/save$", path)
            if m:
                kind = m.group(1)
                data = dict(form)
                data["slug"] = data.get("slug") or slugify(data.get("title") or data.get("name") or "entry")
                data["featured"] = 1 if form.get("featured") == "on" else 0
                timestamp = now_iso()
                if kind == "posts":
                    status = data.get("status", "draft")
                    if status not in {"draft", "published"}:
                        raise ValueError("文章状态无效")
                    data["slug"] = validate_slug(data["slug"])
                    columns = ["title", "slug", "excerpt", "body", "tags", "cover", "status"]
                    if form.get("id"):
                        existing = conn.execute("SELECT status, published_at FROM posts WHERE id = ?", (form["id"],)).fetchone()
                        if not existing:
                            raise ValueError("文章不存在")
                        published_at = next_published_at(existing["status"], existing["published_at"], status, timestamp)
                        conn.execute("UPDATE posts SET title=?, slug=?, excerpt=?, body=?, tags=?, cover=?, status=?, published_at=?, updated_at=? WHERE id=?", tuple(data.get(c, "") for c in columns) + (published_at, timestamp, form["id"]))
                    else:
                        published_at = timestamp if status == "published" else None
                        conn.execute("INSERT INTO posts(title, slug, excerpt, body, tags, status, published_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(data.get(c, "") for c in columns) + (published_at, timestamp, timestamp))
                elif kind == "projects":
                    status = data.get("status", "active")
                    if status not in {"active", "paused", "archived"}:
                        raise ValueError("项目状态无效")
                    data["slug"] = validate_slug(data["slug"])
                    data["github_url"] = validate_external_url(data.get("github_url", ""))
                    data["demo_url"] = validate_external_url(data.get("demo_url", ""))
                    values = tuple(data.get(c, "") for c in ["name", "slug", "summary", "description", "tech_stack", "github_url", "demo_url", "status"])
                    if form.get("id"):
                        conn.execute("UPDATE projects SET name=?, slug=?, summary=?, description=?, tech_stack=?, github_url=?, demo_url=?, status=?, featured=?, updated_at=? WHERE id=?", values + (data["featured"], timestamp, form["id"]))
                    else:
                        conn.execute("INSERT INTO projects(name, slug, summary, description, tech_stack, github_url, demo_url, status, featured, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values + (data["featured"], timestamp, timestamp))
                else:
                    status = data.get("status", "draft")
                    if status not in {"published", "draft"}:
                        raise ValueError("游戏状态无效")
                    data["slug"] = validate_game_bundle(data["slug"], status)
                    data["source_url"] = validate_external_url(data.get("source_url", ""))
                    values = tuple(data.get(c, "") for c in ["name", "slug", "summary", "description", "controls", "source_url", "status"])
                    if form.get("id"):
                        conn.execute("UPDATE games SET name=?, slug=?, summary=?, description=?, controls=?, source_url=?, status=?, featured=?, updated_at=? WHERE id=?", values + (data["featured"], timestamp, form["id"]))
                    else:
                        conn.execute("INSERT INTO games(name, slug, summary, description, controls, source_url, status, featured, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values + (data["featured"], timestamp, timestamp))
                conn.commit()
                self.redirect(f"/admin/{kind}")
                return
            self.send_error(404)
        except ValueError as exc:
            self.send_html(render_page("保存失败", f'<main class="subpage wrap"><div class="empty-state large"><p>{esc(exc)}</p><a class="button button-primary" href="/admin">回到后台</a></div></main>', admin=True), 400)
        except sqlite3.IntegrityError:
            self.send_html(render_page("保存失败", '<main class="subpage wrap"><div class="empty-state large"><p>保存失败：slug 可能已经被使用。</p><a class="button button-primary" href="/admin">回到后台</a></div></main>', admin=True), 409)
        finally:
            conn.close()


def main() -> None:
    validate_runtime_config(APP_ENV, ADMIN_PASSWORD, SESSION_SECRET)
    db().close()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Signal Desk running on http://127.0.0.1:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()




