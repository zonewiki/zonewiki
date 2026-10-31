"""The settings the tests give Zone (tests/goserver.py): the deployment's
as the server's environment and flags, the wiki's in its settings file
(.leaf/config). A test patches them here (monkeypatch.setattr(settings,
"LEAF_PASSWORD", "pw")); the server restarts with them before its next
request. The names are those of the variables Zone read until 2026-10, a
few values typed as Zone parses them (LEAF_HOSTS a set of names,
LEAF_NOSHARE as tags and folders); passwords and tokens plain, goserver.py
writes their hashes."""

import os
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

ZONE_DATA: Path = Path("/nonexistent")   # each test's wiki (conftest's wiki_dir)
ZONE_BUILD = "test"
LEAF_PASSWORD = ""
LEAF_MCP_TOKEN = ""
# Users: name → (password, MCP token or ""); the settings file gets their
# hashes (goserver.py).
LEAF_USERS: dict[str, tuple[str, str]] = {}
# More of the settings file's text (after the settings above, before the
# users).
CONFIG_FILE = ""
LEAF_BASE_URL = ""
ZONE_HOST_DATA = ""
LEAF_QUERY_LOG = False
LEAF_GIT_REMOTE = ""
# No Host check against the test client's (a test of the check turns it on).
ZONE_ALLOW_REMOTE = True
LEAF_HOSTS: frozenset[str] = frozenset()
NOSHARE_TAGS: frozenset[str] = frozenset({"noshare"})
NOSHARE_FOLDERS: tuple[str, ...] = ()
LEAF_LOCALE = ""
LEAF_DATE_FORMAT = ""
# Whether the server finds git on its PATH.
GIT_ENABLED = shutil.which("git") is not None
# Zone's docs (README.md, docs/spec/) for zone_help; None: none there.
DOCS: Path | None = REPO
# The semantic index, a tests.semantic.FakeIndex; None: semantic search off.
SEMANTIC = None
# Extra environment (ZONE_SEMANTIC_SEARCH …).
ENV: dict[str, str] = {}

# The environment of the tests' own git calls: as Zone's, no detached
# maintenance (it would linger as a zombie).
GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_COUNT": "2",
    "GIT_CONFIG_KEY_0": "maintenance.autoDetach", "GIT_CONFIG_VALUE_0": "false",
    "GIT_CONFIG_KEY_1": "gc.autoDetach", "GIT_CONFIG_VALUE_1": "false",
}
