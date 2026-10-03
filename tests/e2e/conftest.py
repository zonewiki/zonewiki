"""Browser (end-to-end) test harness.

Runs the real Flask app on a background thread and drives it with headless
Chromium via Playwright. Every test gets a fresh temp wiki (with a real git
repo) and a fresh browser context (clean localStorage/sessionStorage).

Skipped automatically when Playwright is not installed, so a plain
`pytest tests/` keeps working everywhere. Run it via the dedicated image:

    ./run.sh e2e
"""

import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed (use ./run.sh e2e)")
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

os.environ.setdefault("WIKI_DATA", tempfile.mkdtemp(prefix="leaf_e2e_"))
os.environ["ALLOW_REMOTE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import server  # noqa: E402
import deck as deck_module  # noqa: E402

SCREENSHOT_DIR = Path(__file__).parent / "_screenshots"

PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1280, "height": 800}
# A real phone: touch input, no hover, coarse pointer.
TOUCH_PHONE = {"viewport": PHONE, "has_touch": True, "is_mobile": True}


# ── Server ────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def base_url():
    srv = make_server("127.0.0.1", 0, server.app, threaded=True)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture()
def wiki(tmp_path, monkeypatch, base_url):
    """Point the running app at a fresh wiki directory with its own git repo."""
    monkeypatch.setattr(server, "WIKI_DIR", tmp_path)
    monkeypatch.setattr(server, "PAGES_DIR", tmp_path)
    monkeypatch.setattr(server, "UPLOADS_DIR", tmp_path / "_assets")
    monkeypatch.setattr(server, "DECK_FILE", tmp_path / "_deck" / "tasks.json")
    monkeypatch.setattr(server, "GIT_REMOTE", "")
    monkeypatch.setattr(deck_module, "DECK_FILE", tmp_path / "_deck" / "tasks.json")
    (tmp_path / "_deck").mkdir(exist_ok=True)   # tests write deck files directly
    monkeypatch.setattr(deck_module, "WIKI_DIR", tmp_path)
    server._search_doc_cache.clear()
    server._page_meta_cache.clear()
    deck_module._task_docs_cache["key"] = None
    deck_module._task_docs_cache["docs"] = []
    (tmp_path / "_assets").mkdir()
    (tmp_path / "index.md").write_text("# Home\n\nWelcome.\n", encoding="utf-8")
    server.git_setup()
    return Wiki(tmp_path)


class Wiki:
    """Small helper around the temp wiki dir for arranging and asserting state."""

    def __init__(self, root: Path):
        self.root = root

    def write(self, slug: str, content: str) -> Path:
        path = self.root / f"{slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def read(self, slug: str) -> str:
        return (self.root / f"{slug}.md").read_text(encoding="utf-8")

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.root, capture_output=True,
                              text=True, check=True, env=server._GIT_ENV).stdout

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()


def open_tab(page, view: str):
    """Switch the deck to a view by its data-filter name (open/due/focus/completed).
    Completed has no tab; its link under the list opens it."""
    if view == "completed":
        page.locator("#task-completed-link").click()
    else:
        page.locator(f"#tasks-tabs [data-filter='{view}']").click()


# ── Browser ───────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def _new_page(browser, base_url, *, viewport=DESKTOP, color_scheme="light", **context_opts):
    ctx = browser.new_context(base_url=base_url, viewport=viewport, color_scheme=color_scheme,
                              **context_opts)
    # Leaf loads nothing from other hosts (no CDN; highlight.js is vendored).
    # Such a request is blocked, keeping runs offline, and fails the test.
    host = base_url.split("//", 1)[1]
    external = []

    def route(r):
        if r.request.url.split("//", 1)[1].startswith(host):
            r.continue_()
        else:
            external.append(r.request.url)
            r.abort()
    ctx.route("**/*", route)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.js_errors = errors
    page.external_requests = external
    return ctx, page


@pytest.fixture()
def page(browser, base_url, wiki):
    ctx, pg = _new_page(browser, base_url)
    yield pg
    ctx.close()
    assert pg.js_errors == [], f"uncaught JS errors: {pg.js_errors}"
    assert pg.external_requests == [], f"requests to other hosts: {pg.external_requests}"


@pytest.fixture()
def new_page(browser, base_url, wiki):
    """Factory for extra pages with a custom viewport / colour scheme / device
    (e.g. new_page(**TOUCH_PHONE))."""
    opened = []

    def make(**kwargs):
        ctx, pg = _new_page(browser, base_url, **kwargs)
        opened.append((ctx, pg))
        return pg

    yield make
    for ctx, pg in opened:
        ctx.close()
        assert pg.js_errors == [], f"uncaught JS errors: {pg.js_errors}"
        assert pg.external_requests == [], f"requests to other hosts: {pg.external_requests}"
