"""A tab nobody is at asks the server nothing (`whileUsed` in js/util.js): the
uncommitted badge and the Tasks counts are refreshed while someone uses the
page and when the tab comes back, not for ever. A hosted wiki whose tabs are
only open, not used, is idle."""

from urllib.parse import urlparse

from conftest import wait_until

POLLS = ("/api/uncommitted", "/api/tasks/due-count")


def test_a_tab_nobody_is_at_asks_nothing(page, wiki):
    asked = []
    page.on("request", lambda r: asked.append(urlparse(r.url).path))
    polls = lambda: [p for p in asked if p in POLLS]
    page.clock.install()
    page.goto("/page/index")
    page.wait_for_load_state("networkidle")
    at_load = len(polls())

    page.clock.run_for(10 * 60 * 1000)
    page.wait_for_load_state("networkidle")
    assert len(polls()) == at_load, polls()

    # Someone is there again: each asks once at its next turn, and then no more.
    page.mouse.move(60, 60)
    page.clock.run_for(61 * 1000)
    wait_until(page, lambda: sorted(polls()[at_load:]) == sorted(POLLS))
    page.clock.run_for(10 * 60 * 1000)
    page.wait_for_load_state("networkidle")
    assert sorted(polls()[at_load:]) == sorted(POLLS), polls()


def test_a_tab_that_comes_back_asks_again(page, wiki):
    asked = []
    page.on("request", lambda r: asked.append(urlparse(r.url).path))
    polls = lambda: [p for p in asked if p in POLLS]
    page.goto("/page/index")
    page.wait_for_load_state("networkidle")
    at_load = len(polls())
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    wait_until(page, lambda: sorted(polls()[at_load:]) == sorted(POLLS))
