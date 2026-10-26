"""The settings the tests give Leaf, as the server's environment and flags
(tests/goserver.py). A test patches them here (monkeypatch.setattr(settings,
"LEAF_PASSWORD", "pw")); the server restarts with them before its next
request. The names are .env's, a few values typed as Leaf parses them
(LEAF_HOSTS a set of names, LEAF_NOSHARE as tags and folders)."""

import os
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

LEAF_DATA: Path = Path("/nonexistent")   # each test's wiki (conftest's wiki_dir)
LEAF_BUILD = "test"
LEAF_PASSWORD = ""
LEAF_MCP_TOKEN = ""
LEAF_BASE_URL = ""
LEAF_HOST_DATA = ""
LEAF_QUERY_LOG = False
LEAF_GIT_REMOTE = ""
# No Host check against the test client's (a test of the check turns it on).
LEAF_ALLOW_REMOTE = True
LEAF_HOSTS: frozenset[str] = frozenset()
NOSHARE_TAGS: frozenset[str] = frozenset({"noshare"})
NOSHARE_FOLDERS: tuple[str, ...] = ()
LEAF_LOCALE = ""
LEAF_DATE_FORMAT = ""
# Whether the server finds git on its PATH.
GIT_ENABLED = shutil.which("git") is not None
# Leaf's docs (README.md, docs/spec/) for leaf_help; None: none there.
DOCS: Path | None = REPO
# The semantic index, a tests.semantic.FakeIndex; None: semantic search off.
SEMANTIC = None
# Extra environment (LEAF_SEMANTIC_SEARCH …).
ENV: dict[str, str] = {}

# The environment of the tests' own git calls: as Leaf's, no detached
# maintenance (it would linger as a zombie).
GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_COUNT": "2",
    "GIT_CONFIG_KEY_0": "maintenance.autoDetach", "GIT_CONFIG_VALUE_0": "false",
    "GIT_CONFIG_KEY_1": "gc.autoDetach", "GIT_CONFIG_VALUE_1": "false",
}
