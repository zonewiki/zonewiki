"""Leaf's settings: from the environment (.env through run.sh), and the
wiki's directories. Other modules read them as config.X at call time,
so a test can point Leaf at another wiki by patching them here."""

import os
import shutil
import subprocess
from pathlib import Path


APP_NAME = "Leaf"
# Leaf's version (semver), bumped by hand when a change is worth a number.
VERSION = "1.0.0"


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
# Bearer token for /mcp (see mcp_endpoint); needed there when LEAF_PASSWORD is set.
LEAF_MCP_TOKEN = os.environ.get("LEAF_MCP_TOKEN", "")
# Leaf's address as the user's browser knows it, for links Leaf hands to an
# LLM (MCP tools). Unset, links use the address the request came in on,
# which for a client in a container is e.g. host.containers.internal.
LEAF_BASE_URL = os.environ.get("LEAF_BASE_URL", "").strip().rstrip("/")
# A remote for the wiki's git repository (gitstore.git_setup).
GIT_REMOTE = os.environ.get("GIT_REMOTE", "")
# Listen on the LAN: no Host check (server.check_host).
ALLOW_REMOTE = os.environ.get("ALLOW_REMOTE", "").lower() in ("1", "true", "yes")


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

# The wiki: pages at the top, uploads in _assets/ (deck.py has _deck/).
WIKI_DIR = Path(os.environ.get("WIKI_DATA", Path(__file__).parent / "data"))
UPLOADS_DIR = WIKI_DIR / "_assets"
WIKI_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

GIT_ENABLED = shutil.which("git") is not None
