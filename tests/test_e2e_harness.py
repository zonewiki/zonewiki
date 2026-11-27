"""The browser tests measure one way.

A browser test that asks where an element is does it with box_of(…) from
tests/e2e/conftest.py, which first waits until the page stands still (its
requests answered and drawn, fonts in, transitions done) and never hands
back None. Playwright's own locator.bounding_box() measures at once: a list
drawn again by a late answer replaced the card between finding and
measuring it, and the test failed now and then on a busy machine ('NoneType'
object is not subscriptable). Nothing in a test says so when it is written,
so this reads the sources.
"""

import re
from pathlib import Path

E2E = Path(__file__).resolve().parent / "e2e"


def test_browser_tests_measure_with_box_of():
    raw = [f"{path.name}:{n}: {line.strip()}"
           for path in sorted(E2E.glob("*.py"))
           for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
           if re.search(r"\.bounding_box\s*\(", line)]
    # The one in box_of itself.
    assert len(raw) == 1 and raw[0].startswith("conftest.py:"), (
        "measure with box_of(locator) from conftest.py, not locator.bounding_box() "
        "(box_of(locator, moving=True) to measure while something moves):\n" + "\n".join(raw))


def test_a_workers_port_is_its_own_and_never_one_for_port_0(monkeypatch):
    """Each worker's browser tests start and stop their servers on one port
    (session_port): free between two tests, so one the kernel hands out
    for port 0 could be taken meanwhile, by a server of another test or
    worker or by Chromium, and every test the worker had left failed (two
    workers' 512 tests, once). It lies below Linux's ephemeral range, in a
    block of the worker's own."""
    import socket

    from tests.goserver import session_port
    low = int(Path("/proc/sys/net/ipv4/ip_local_port_range").read_text().split()[0])
    ports = {}
    for worker in ("gw0", "gw1", "gw7"):
        monkeypatch.setenv("PYTEST_XDIST_WORKER", worker)
        ports[worker] = session_port()
    assert all(p < low for p in ports.values()), (ports, low)
    assert ports["gw0"] // 100 != ports["gw1"] // 100 != ports["gw7"] // 100
    # One taken: the next of the block.
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw7")
    with socket.socket() as s:
        s.bind(("127.0.0.1", ports["gw7"]))
        s.listen()
        assert session_port() == ports["gw7"] + 1
