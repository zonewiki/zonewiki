"""The settings the tests give Zone (tests/goserver.py): the deployment's
as the server's environment and flags, the wiki's in its settings file
(.zone/config). A test patches them here (monkeypatch.setattr(settings,
"PASSWORD", "pw")); the server restarts with them before its next
request. A few values are typed as Zone parses them (HOSTS a set of names,
noshare as NOSHARE_TAGS and NOSHARE_FOLDERS); passwords and tokens plain,
goserver.py writes their hashes."""

import os
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

ZONE_DATA: Path = Path("/nonexistent")   # each test's wiki (conftest's wiki_dir)
ZONE_BUILD = "test"
PASSWORD = ""
MCP_TOKEN = ""
# Users: name → (password, MCP token or ""); the settings file gets their
# hashes (goserver.py).
USERS: dict[str, tuple[str, str]] = {}
# More of a user's settings (role, disabled): name → {key: value}.
USER_SETTINGS: dict[str, dict[str, str]] = {}
# More of the settings file's text (after the settings above, before the
# users).
CONFIG_FILE = ""
BASE_URL = ""
ZONE_HOST_DATA = ""
QUERY_LOG = False
GIT_REMOTE = ""
# No Host check against the test client's (a test of the check turns it on).
ZONE_ALLOW_REMOTE = True
# No login on purpose (no-login = yes in the settings file), as the tests
# reach the server like another device would (ZONE_ALLOW_REMOTE): without
# it, and without a password or users, Zone shows its first-run page.
NO_LOGIN = True
HOSTS: frozenset[str] = frozenset()
NOSHARE_TAGS: frozenset[str] = frozenset({"noshare"})
NOSHARE_FOLDERS: tuple[str, ...] = ()
LOCALE = ""
DATE_FORMAT = ""
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
