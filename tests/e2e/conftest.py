"""Browser (end-to-end) test harness.

Runs the real Flask app on a background thread and drives it with headless
Chromium via Playwright. Every test gets a fresh temp wiki (with a real git
repo) and a fresh browser context (clean localStorage/sessionStorage).

Skipped automatically when Playwright is not installed, so a plain
`pytest tests/` keeps working everywhere. Run it via the dedicated image:

    ./run.sh e2e
"""

import subprocess
import threading
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed (use ./run.sh e2e)")
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

import server  # noqa: E402  (tests/conftest.py set up its import)
import gitstore  # noqa: E402

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
def wiki(git_wiki_dir, base_url):
    """Point the running app at a fresh wiki directory with its own git repo
    (tests/conftest.py)."""
    return Wiki(git_wiki_dir)


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
                              text=True, check=True, env=gitstore._GIT_ENV).stdout

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
    # Decks open with full cards in tests, as before the per-tab defaults
    # (Open compact): most tests exercise cards, not density. A test of the
    # real defaults calls real_density(page) first.
    ctx.add_init_script("""
      if (!sessionStorage.getItem('leaf-test-real-density')) {
        const get = Storage.prototype.getItem;
        Storage.prototype.getItem = function (key) {
          const value = get.call(this, key);
          return value === null && String(key).startsWith('leaf-deck-compact:') ? '0' : value;
        };
      }
    """)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.js_errors = errors
    page.external_requests = external
    return ctx, page


def real_density(page):
    """Use the deck's real density defaults (Open compact …) on this page's
    tab from the next load on; the test setup otherwise keeps full cards."""
    if page.url.startswith("about:"):
        page.goto("/page/index")
    page.evaluate("sessionStorage.setItem('leaf-test-real-density', '1')")


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
