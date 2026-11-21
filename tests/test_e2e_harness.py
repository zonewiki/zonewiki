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
