"""`.env.example` names only settings Leaf reads, and every one it reads."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Set by run.sh or the build, not by the user: .env.example needn't list them.
INTERNAL = {"LEAF_BUILD", "LEAF_HOST_DATA", "LEAF_COPY_REPO", "LEAF_MODEL_DIR"}

# The wiki's settings, in .env until 2026-10: the code names them only to
# refuse them there and to import them into the settings file
# (go/configfile.go's configKeys, go/commands.go).
FORMER = {"LEAF_USERS", "LEAF_USER_", "LEAF_SECRET_KEY"} | set(re.findall(
    r'\{"[a-z-]+", "(LEAF_[A-Z0-9_]+)"\}', (REPO / "go" / "configfile.go").read_text()))


def _example_keys() -> set[str]:
    text = (REPO / ".env.example").read_text(encoding="utf-8")
    return set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=", text, re.M))


def _used_keys() -> set[str]:
    text = "".join(p.read_text(encoding="utf-8")
                   for p in [*(REPO / "go").glob("*.go"), REPO / "run.sh", REPO / "compose.yaml",
                             REPO / "compose.nas.yaml"] if not p.name.endswith("_test.go"))
    # The old names of renamed settings, read only to say so.
    renamed = set(re.findall(r'\{"(LEAF_[A-Z0-9_]+)", "LEAF_', (REPO / "go" / "embed.go").read_text()))
    return set(re.findall(r"\bLEAF_[A-Z0-9_]+", text)) - INTERNAL - renamed - FORMER


def test_former_settings_are_found():
    assert {"LEAF_PASSWORD", "LEAF_LOCALE", "LEAF_SEMANTIC_MIN_SCORE"} <= FORMER


def test_example_has_no_unknown_keys():
    """A typo (LEAF_MCP_TOPEN) or a missing prefix (SECRET_KEY) would be ignored."""
    unknown = _example_keys() - _used_keys() - {"TZ"}
    assert not unknown, f".env.example names settings nothing reads: {sorted(unknown)}"


def test_example_lists_every_setting():
    missing = _used_keys() - _example_keys()
    assert not missing, f"settings missing from .env.example: {sorted(missing)}"
