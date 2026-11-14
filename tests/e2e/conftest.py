"""Browser (end-to-end) test harness.

Runs Zone's server, a process per test (tests/goserver.py), and drives it
with headless Chromium via Playwright. Every test gets a fresh temp wiki
(with a real git repo) and a fresh browser context (clean
localStorage/sessionStorage).

Skipped automatically when Playwright is not installed, so a plain
`pytest tests/` keeps working everywhere. Run it via the dedicated image:

    ./run.sh e2e
"""

import faulthandler
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed (use ./run.sh e2e)")
from playwright.sync_api import Error as PlaywrightError, expect, sync_playwright  # noqa: E402

from tests import settings  # noqa: E402
from tests.goserver import LiveServer, free_port  # noqa: E402

SCREENSHOT_DIR = Path(__file__).parent / "_screenshots"

PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1280, "height": 800}
# A real phone: touch input, no hover, coarse pointer.
TOUCH_PHONE = {"viewport": PHONE, "has_touch": True, "is_mobile": True}


# ── A test that never ends ───────────────────────────────────────────────────

# The slowest test takes under 10 seconds. One still running after
# TEST_TIMEOUT is waiting for an answer that won't come: under load
# Playwright's driver or the browser sometimes dies (new_page then fails
# with "Target crashed" or "Connection closed while reading from the
# driver"), and the next call of its sync API waits forever. It held a whole
# run up for 19 minutes once, with nothing to say which test it was.
TEST_TIMEOUT = 120


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item, nextitem):
    """Stop the process of a test (its setup and teardown too) that runs
    longer than TEST_TIMEOUT, saying which one and where it stands: a hung
    browser call can't be interrupted from inside. With pytest-xdist that is
    one worker: the run reports its test as failed (the worker crashed) and
    goes on with a new worker."""
    def stuck():
        # pytest holds the process's stderr while a test runs: hand it back,
        # or what is written here goes nowhere.
        try:
            item.config.pluginmanager.getplugin("capturemanager").suspend_global_capture(in_=True)
        except Exception:
            pass
        sys.stderr.write(f"\n{item.nodeid} ran longer than {TEST_TIMEOUT} s: its browser or Playwright's driver no "
                         "longer answers. Stopping this test process; where it stood:\n")
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        sys.stderr.flush()
        os._exit(70)
    timer = threading.Timer(TEST_TIMEOUT, stuck)
    timer.daemon = True
    timer.start()
    try:
        return (yield)
    finally:
        timer.cancel()


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


_EMBED_RE = re.compile(r"\[\[tasks:\s*([^\]]*)\]\]", re.I)


def make_list(page, name: str, private: bool = False):
    """Make a task list as New list… does (private: the signed-in user's
    own), through the route its dialog posts to."""
    resp = page.request.post("/tasks/new-list", form={"name": name, **({"private": "1"} if private else {})})
    assert resp.ok and "newlist_error" not in resp.url, resp.url
    return resp


def list_page(page, wiki: "Wiki", slug: str, content: str) -> Path:
    """wiki.write, and the lists the page's [[tasks: list=…]] embeds show,
    made through the routes the browser uses (as tests/conftest.py's
    list_page): each list if it isn't there yet (private for a page in
    private/), with the embed's list settings (view, keep, limit, plan,
    labels, new) when it was made here, and pinned as a pin= in the embed
    says. A list is no longer declared by a page nor pinned by one: pin= in
    the text means nothing to Zone, it tells this helper what the test
    wants. Code is skipped."""
    path = wiki.write(slug, content)
    text = re.sub(r"```.*?```|`[^`\n]*`", "", content, flags=re.S)
    for m in _EMBED_RE.finditer(text):
        p = dict(w.split("=", 1) for w in m.group(1).split() if "=" in w)
        name = p.get("list", "").strip("#").lower()
        if not name:
            continue
        made = page.request.post("/tasks/new-list", form={"name": name, **({"private": "1"} if slug.startswith("private/") else {})})
        own = {k: p[k] for k in ("view", "keep", "limit", "plan", "labels", "new") if k in p}
        if own and "newlist_error" not in made.url:
            assert page.request.post("/api/tasks/settings", data={"list": name, "set": own}).ok
        if p.get("pin"):
            pin = "default" if p["pin"].lower() == "default" else "1"
            assert page.request.post("/tasks/pin-list", form={"list": name, "pin": pin}).ok
    return path


# How long an expect() waits for what it expects (Playwright's own default
# is 5 s), and wait_until below: on a machine busy with other work a browser
# gets little of a CPU, and what takes 100 ms alone can take seconds. A
# passing test waits no longer for it; only a failing one takes this long
# to say so.
WAIT_MS = 15_000
expect.set_options(timeout=WAIT_MS)

# Whether the page takes clicks and keys as Zone's code means them: its
# entry module has run (<html data-ready>, public/zonewiki.js) and every
# task list that started shows its tasks (data-loaded, js/taskdata.js). A
# page without Zone's script (a download, an error page) is ready as it is;
# one still being read is not yet known to be such a page (wait_for_url
# returns when the address is the new one, before any of the page is there).
READY_JS = """() => {
  if (document.readyState === 'loading') return false;
  if (!document.querySelector('script[src$="/zonewiki.js"]')) return true;
  if (!document.documentElement.dataset.ready) return false;
  return [...document.querySelectorAll('.tasks-app[data-ready]:not([data-headless])')].every(a => a.dataset.loaded);
}"""


def wait_ready(page):
    """Wait until the page is ready (READY_JS). For a navigation the test
    didn't start with goto, reload or wait_for_url, which wait themselves: a
    link clicked, a form that leaves the page."""
    for _ in range(5):
        try:
            page.wait_for_function(READY_JS)
            return
        except PlaywrightError as e:
            # The page went on to another one meanwhile (a redirect in the
            # browser): wait for that one.
            if "context was destroyed" not in str(e) and "navigat" not in str(e).lower():
                raise
    page.wait_for_function(READY_JS)


def _wait_when_there(page):
    """page.goto, page.reload and page.wait_for_url wait until the page is
    ready before they return, so no test acts on a page whose code hasn't
    started: a click then did what the plain HTML does, or met a task list
    without its tasks. ready=False: return as Playwright does (a test that
    holds a request back, or looks at the page while it loads)."""
    for name in ("goto", "reload", "wait_for_url"):
        plain = getattr(page, name)

        def waiting(*args, ready=True, _plain=plain, **kwargs):
            result = _plain(*args, **kwargs)
            if ready:
                wait_ready(page)
            return result
        setattr(page, name, waiting)


def wait_until(page, predicate, timeout_ms: int = WAIT_MS):
    """Wait until predicate() holds, looking every 100 ms; fails when it
    never does. For what expect() can't watch: the API's answer, a file on
    disk, a request a route held back. It waits through the page, so the
    page's routes and events go on meanwhile (time.sleep would stop them)."""
    for _ in range(timeout_ms // 100):
        if predicate():
            return
        page.wait_for_timeout(100)
    assert predicate(), "condition not met in time"


def open_tab(page, view: str):
    """Switch the tasks to a view by its name (open/plan/focus/closed).
    Completed has no tab; its link under the list opens it. Open and Plan
    are the Open tab with its Plan switch off and on."""
    if view == "closed":
        page.locator("#task-closed-link").click()
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
    _wait_when_there(page)
    return ctx, page


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
