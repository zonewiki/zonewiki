"""Leaf's settings: from the environment (.env through run.sh), and the
wiki's directories. Other modules read them as config.X at call time,
so a test can point Leaf at another wiki by patching them here."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


APP_NAME = "Leaf"
# Leaf's version (semver), bumped by hand when a change is worth a number.
VERSION = "1.1.0"


def _source_build(root: Path = Path(__file__).resolve().parent.parent) -> str:
    """The build Leaf runs from, as "<short hash> <commit date>" ("+dirty"
    when the source has uncommitted changes), read from the git checkout this
    file lives in. The container image has no .git, so run.sh bakes the same
    string into $LEAF_BUILD at build time instead. Shown next to VERSION."""
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                              text=True, timeout=5, check=True).stdout.strip()
    try:
        version = git("log", "-1", "--format=%h %cd", "--date=format:%Y-%m-%d %H:%M")
        if version and git("status", "--porcelain", "--", "src"):
            version += " +dirty"
        return version or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


LEAF_BUILD = os.environ.get("LEAF_BUILD") or _source_build()

# Optional login gate (server.check_auth).
LEAF_PASSWORD = os.environ.get("LEAF_PASSWORD", "")
# Bearer token for /mcp (see mcp_endpoint); without it /mcp is off.
LEAF_MCP_TOKEN = os.environ.get("LEAF_MCP_TOKEN", "")
# Leaf's address as the user's browser knows it, for links Leaf hands to an
# LLM (MCP tools). Unset, links use the address the request came in on,
# which for a client in a container is e.g. host.containers.internal.
LEAF_BASE_URL = os.environ.get("LEAF_BASE_URL", "").strip().rstrip("/")
# The wiki folder as the host sees it (run.sh passes it), for the file paths
# the MCP tools give; inside the container LEAF_DATA is only /wiki-data.
# Unset, the MCP tools use LEAF_DATA itself (mcp_tools._host_file).
LEAF_HOST_DATA = os.environ.get("LEAF_HOST_DATA", "").strip().rstrip("/")
# Keep search_wiki's queries in .leaf/queries.jsonl (retrieval.log_query),
# to turn into questions for tools/eval_retrieval.py. Off by default.
LEAF_QUERY_LOG = os.environ.get("LEAF_QUERY_LOG", "").lower() in ("1", "true", "yes")
# A remote for the wiki's git repository (gitstore.git_setup).
LEAF_GIT_REMOTE = os.environ.get("LEAF_GIT_REMOTE", "")
# Listen on the LAN: no Host check (server.check_host), unless LEAF_HOSTS
# names the hosts.
LEAF_ALLOW_REMOTE = os.environ.get("LEAF_ALLOW_REMOTE", "").lower() in ("1", "true", "yes")
# The names Leaf may be reached by besides localhost, e.g. behind a reverse
# proxy: LEAF_HOSTS=wiki.example.org,leaf.home.arpa (any port; server.check_host).
# Set, the Host check stays on even with LEAF_ALLOW_REMOTE.
def host_name(host: str) -> str:
    """A Host header's name without its port, lowercase ("[::1]:5000" →
    "[::1]", "Wiki.Example.org:443" → "wiki.example.org")."""
    host = host.strip().lower()
    if host.startswith("["):
        return host.split("]", 1)[0] + "]"
    return host.split(":", 1)[0]


LEAF_HOSTS = frozenset(host_name(h) for h in os.environ.get("LEAF_HOSTS", "").split(",") if h.strip())


# Sharing mode leaves out the pages LEAF_NOSHARE names (pages.is_noshare):
# "#tag" entries are tags, "folder/" entries folders (everything below them),
# e.g. LEAF_NOSHARE=#noshare,#people,people/,personal/ ; default #noshare. A
# bare word counts as a tag.
def _parse_noshare(spec: str) -> tuple[frozenset[str], tuple[str, ...]]:
    tags, folders = set(), []
    for item in (x.strip() for x in spec.split(",")):
        if not item:
            continue
        if item.endswith("/") and not item.startswith("#"):
            folders.append(item.strip("/").lower() + "/")
        else:
            tags.add(item.lstrip("#").lower())
    return frozenset(tags), tuple(folders)


# Empty counts as unset (run.sh passes unset settings as empty strings).
NOSHARE_TAGS, NOSHARE_FOLDERS = _parse_noshare(os.environ.get("LEAF_NOSHARE") or "#noshare")

# How dates read and the day a week starts on: a BCP 47 tag like de-AT or
# en-GB, handed to the browser's Intl (<body data-locale>, deck-core.js).
# Unset: US-English dates, weeks from Monday. The UI's words stay English.
def _check_locale(tag: str) -> str:
    """The tag, or exit: a typo shouldn't leave every date in the default."""
    tag = tag.strip()
    if tag and not re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*", tag):
        sys.exit(f"Leaf: LEAF_LOCALE={tag} isn't a locale. Use a tag like de-AT or en-GB.")
    return tag


LEAF_LOCALE = _check_locale(os.environ.get("LEAF_LOCALE", ""))

# iso: dates as YYYY-MM-DD (ISO 8601) whatever the locale; its weekday and
# month names stay (<body data-date-format>, deck-core.js). Unset: the locale's.
def _check_date_format(value: str) -> str:
    value = value.strip().lower()
    if value not in ("", "iso"):
        sys.exit(f"Leaf: LEAF_DATE_FORMAT={value} isn't known. Use iso, or leave it unset.")
    return value


LEAF_DATE_FORMAT = _check_date_format(os.environ.get("LEAF_DATE_FORMAT", ""))

# The wiki: pages at the top, uploads in _assets/ (deck.py has _deck/).
# Required, no default (not a folder in the checkout), and it must exist:
# Leaf doesn't make a wiki where a mistyped LEAF_DATA points.
if not os.environ.get("LEAF_DATA"):
    sys.exit("Leaf: LEAF_DATA isn't set. Set it to your wiki's folder, e.g. LEAF_DATA=~/leaf python src/server.py")
LEAF_DATA = Path(os.environ["LEAF_DATA"])
if not LEAF_DATA.is_dir():
    sys.exit(f"Leaf: no wiki at {LEAF_DATA}. Create the folder, or set LEAF_DATA to your wiki.")
UPLOADS_DIR = LEAF_DATA / "_assets"
UPLOADS_DIR.mkdir(exist_ok=True)

GIT_ENABLED = shutil.which("git") is not None
