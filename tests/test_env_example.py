"""`.env.example` names only settings Zone reads, and every one it reads."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Set by run.sh or the build, not by the user, or said to a test run or the
# bench (ZONE_TEST_RACE, ZONE_BENCH_*): .env.example needn't list them.
INTERNAL = {"ZONE_BUILD", "ZONE_HOST_DATA", "ZONE_COPY_REPO", "ZONE_MODEL_DIR", "ZONE_TEST_RACE", "ZONE_BENCH_SLACK",
            "ZONE_BENCH_SIZE"}

def _example_keys() -> set[str]:
    text = (REPO / ".env.example").read_text(encoding="utf-8")
    return set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=", text, re.M))


def _used_keys() -> set[str]:
    text = "".join(p.read_text(encoding="utf-8")
                   for p in [*(REPO / "go").glob("*.go"), REPO / "run.sh", REPO / "compose.yaml",
                             REPO / "compose.nas.yaml"] if not p.name.endswith("_test.go"))
    return set(re.findall(r"\bZONE_[A-Z0-9_]+", text)) - INTERNAL


def test_example_names_only_zones_settings():
    assert {k for k in _example_keys() if not k.startswith("ZONE_")} == {"TZ"}


def test_example_has_no_unknown_keys():
    """A typo (ZONE_ALOW_REMOTE) or a missing prefix (DATA) would be ignored."""
    unknown = _example_keys() - _used_keys() - {"TZ"}
    assert not unknown, f".env.example names settings nothing reads: {sorted(unknown)}"


def test_example_lists_every_setting():
    missing = _used_keys() - _example_keys()
    assert not missing, f"settings missing from .env.example: {sorted(missing)}"
