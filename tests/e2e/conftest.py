"""Browser (end-to-end) test harness.

Runs Zone's server, a process per test (tests/goserver.py), and drives it
with headless Chromium via Playwright. Every test gets a fresh temp wiki
(with a real git repo) and a fresh browser context (clean
localStorage/sessionStorage).

Skipped automatically when Playwright is not installed, so a plain
`pytest tests/` keeps working everywhere. Run it via the dedicated image:

    ./run.sh e2e
"""

import subprocess
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed (use ./run.sh e2e)")
from playwright.sync_api import sync_playwright  # noqa: E402

from tests import settings  # noqa: E402
from tests.goserver import LiveServer, free_port  # noqa: E402

SCREENSHOT_DIR = Path(__file__).parent / "_screenshots"

PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1280, "height": 800}
# A real phone: touch input, no hover, coarse pointer.
TOUCH_PHONE = {"viewport": PHONE, "has_touch": True, "is_mobile": True}


# ── Server ────────────────────────────────────────────────────────────────────


# The server of the running test. A setting the test changes restarts it at
# once (_WatchedSettings): requests the test makes itself (page.request)
# don't pass _new_page's route.
_go: LiveServer | None = None


class _WatchedSettings(type(settings)):
    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if _go:
            _go.ensure()


settings.__class__ = _WatchedSettings


@pytest.fixture(scope="session")
def base_url():
    # One port for the session; each test's wiki fixture starts a server on it.
    yield f"http://127.0.0.1:{free_port()}"


@pytest.fixture()
def wiki(git_wiki_dir, base_url, tmp_path_factory):
    """A server on a fresh wiki directory with its own git repo
    (tests/conftest.py)."""
    global _go
    _go = LiveServer(tmp_path_factory.mktemp("go") / "server.log", int(base_url.rsplit(":", 1)[1]))
    _go.ensure()
    yield Wiki(git_wiki_dir)
    _go.stop()
    _go = None


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
                              text=True, check=True, env=settings.GIT_ENV).stdout

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()


def open_tab(page, view: str):
    """Switch the tasks to a view by its name (open/plan/focus/completed).
    Completed has no tab; its link under the list opens it. Open and Plan
    are the Open tab with its Plan switch off and on."""
    if view == "completed":
        page.locator("#task-completed-link").click()
    elif view in ("open", "plan"):
        page.locator("#tasks-tabs [data-view='open']").click()
        switch = page.locator(".tasks-plan-switch").first
        if (switch.get_attribute("aria-checked") == "true") != (view == "plan"):
            switch.click()
    else:
        page.locator(f"#tasks-tabs [data-view='{view}']").click()


def pick_date(picker, d: str, tap: bool = False):
    """Click (or tap) date `d` (YYYY-MM-DD) in the task app's date picker, once it
    is open, paging toward it as needed: the picker opens on the month of the
    date it holds, which near a month's end may be after `d` or before it."""
    from playwright.sync_api import expect
    expect(picker).to_be_visible()
    for _ in range(13):
        cell = picker.locator(f".dp-day[data-date='{d}']")
        if cell.count():
            cell.tap() if tap else cell.click()
            return
        shown = picker.locator(".dp-day[data-date]").first.get_attribute("data-date")
        nav = picker.locator(f".dp-nav[data-nav='{-1 if d < shown else 1}']")
        nav.tap() if tap else nav.click()
    raise AssertionError(f"{d} not in the picker")


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
    # Zone loads nothing from other hosts (no CDN; highlight.js is vendored).
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
    # Task lists open with full cards in tests, as before the per-tab defaults
    # (Open compact): most tests exercise cards, not density. A test of the
    # real defaults calls real_density(page) first.
    ctx.add_init_script("""
      if (!sessionStorage.getItem('zone-test-real-density')) {
        const get = Storage.prototype.getItem;
        Storage.prototype.getItem = function (key) {
          const value = get.call(this, key);
          return value === null && String(key).startsWith('zone-tasks-compact:') ? '0' : value;
        };
      }
    """)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.js_errors = errors
    page.external_requests = external
    # Zone's own pages must run under its Content-Security-Policy (script-src
    # 'self': no inline scripts or handlers). Chromium reports a blocked one
    # on the console; a test that blocks one on purpose clears the list.
    violations = []
    page.on("console", lambda msg: violations.append(msg.text)
            if "Content Security Policy" in msg.text else None)
    page.csp_violations = violations
    return ctx, page


def real_density(page):
    """Use the task app's real density defaults (Open compact …) on this page's
    tab from the next load on; the test setup otherwise keeps full cards."""
    if page.url.startswith("about:"):
        page.goto("/page/index")
    page.evaluate("sessionStorage.setItem('zone-test-real-density', '1')")


@pytest.fixture()
def page(browser, base_url, wiki):
    ctx, pg = _new_page(browser, base_url)
    yield pg
    ctx.close()
    assert pg.js_errors == [], f"uncaught JS errors: {pg.js_errors}"
    assert pg.external_requests == [], f"requests to other hosts: {pg.external_requests}"
    assert pg.csp_violations == [], f"blocked by the Content-Security-Policy: {pg.csp_violations}"


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
        assert pg.csp_violations == [], f"blocked by the Content-Security-Policy: {pg.csp_violations}"
