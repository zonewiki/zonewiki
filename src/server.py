import hashlib
import hmac
import html
import json
import mimetypes
import os
import posixpath
import re
import shutil
import subprocess
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse

from flask import Flask, abort, g, has_app_context, has_request_context, jsonify, redirect, request, send_from_directory, session, url_for
from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin
from mdit_py_plugins.deflist import deflist_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.gfm_autolink import gfm_autolink_plugin
from mdit_py_plugins.tasklists import tasklists_plugin

import deck as deck_module
import embed
import mcp_server
import search

app = Flask(__name__, static_folder=None)
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

LEAF_PASSWORD = os.environ.get("LEAF_PASSWORD", "")
# Bearer token for /mcp (see mcp_endpoint); needed there when LEAF_PASSWORD is set.
LEAF_MCP_TOKEN = os.environ.get("LEAF_MCP_TOKEN", "")
# Leaf's address as the user's browser knows it, for links Leaf hands to an
# LLM (MCP tools). Unset, links use the address the request came in on,
# which for a client in a container is e.g. host.containers.internal.
LEAF_BASE_URL = os.environ.get("LEAF_BASE_URL", "").strip().rstrip("/")
GIT_REMOTE = os.environ.get("GIT_REMOTE", "")
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32)
app.config["PERMANENT_SESSION_LIFETIME"] = 30 * 24 * 60 * 60  # 30 days
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True

ALLOW_REMOTE  = os.environ.get("ALLOW_REMOTE", "").lower() in ("1", "true", "yes")


# ── Sharing mode ──────────────────────────────────────────────────────────────
# For sharing the screen (leaf.js toggleSharing, a cookie): the CSS hides what
# is around the page (html.sharing), and the server leaves out the pages that
# are not for sharing. LEAF_NOSHARE names them: "#tag" entries are tags,
# "folder/" entries folders (everything below them), e.g.
# LEAF_NOSHARE=#noshare,#people,people/,personal/ ; default #noshare. A bare
# word counts as a tag.

SHARING_COOKIE = "leaf-sharing"


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
ALLOWED_HOSTS = {"localhost:5000", "127.0.0.1:5000"}

PUBLIC_DIR = Path(__file__).parent / "public"

@app.before_request
def check_host():
    if not ALLOW_REMOTE and request.host not in ALLOWED_HOSTS:
        abort(400)

# Cross-site request forgery: without it, any page open in the browser could
# post a form to http://localhost:5000/delete/<page> (or /edit, or the deck
# API, which takes text/plain bodies without a CORS preflight), and the Host
# check above passes because the Host really is localhost. Browsers send
# Origin on every cross-site POST, so a write whose Origin names another site,
# or is "null" (a sandboxed iframe), is refused. Leaf's own forms, fetches
# and the pagehide beacon are same-origin; tools like curl send no Origin.
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.before_request
def check_origin():
    if request.method not in UNSAFE_METHODS:
        return
    origin = request.headers.get("Origin")
    if origin is None:
        return
    if origin == "null" or urlparse(origin).netloc != request.host:
        abort(403, description="Cross-site request refused")


@app.before_request
def check_auth():
    if not LEAF_PASSWORD:
        return
    # /mcp checks its own bearer token: an MCP client can't log in.
    if request.endpoint in ("login", "logout", "serve_public", "mcp_endpoint"):
        return
    if not session.get("authenticated"):
        next_url = request.path
        return redirect(url_for("login", next=next_url))

@app.after_request
def set_cache_headers(response):
    if response.content_type and "text/html" in response.content_type:
        response.headers["Cache-Control"] = "no-store"
    return response

WIKI_DIR = Path(os.environ.get("WIKI_DATA", Path(__file__).parent / "data"))
PAGES_DIR = WIKI_DIR
UPLOADS_DIR = WIKI_DIR / "_assets"
PAGES_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


# ── Git version control ────────────────────────────────────────────────────────

GIT_ENABLED = shutil.which("git") is not None
SHA_RE = re.compile(r'^[0-9a-f]{7,40}$')
WIKILINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")
# Matches any markdown link href (bare slugs, .md, etc.) — used for backlink scanning
_ANY_MD_LINK_RE = re.compile(r'\[(?:[^\]]*)\]\(([^)#?\s]+)\)')


def slug_display_name(slug: str) -> str:
    """Convert a slug to a human-readable title (e.g. 'my-page' → 'My Page')."""
    return slug.split('/')[-1].replace("-", " ").title()


# git (2.47+) starts its automatic maintenance after a commit as a detached
# background process. In the container Leaf is PID 1 and never reaps that
# orphan, so every commit left a zombie process behind until the PID limit
# (2048) was reached and nothing could start any more. Not detached, git runs
# the (usually trivial) maintenance itself and reaps its own children.
_GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_COUNT": "2",
    "GIT_CONFIG_KEY_0": "maintenance.autoDetach", "GIT_CONFIG_VALUE_0": "false",
    "GIT_CONFIG_KEY_1": "gc.autoDetach", "GIT_CONFIG_VALUE_1": "false",
}


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), cwd=WIKI_DIR, capture_output=True, text=True, env=_GIT_ENV)


def git_setup() -> None:
    if not GIT_ENABLED:
        return
    if not (WIKI_DIR / ".git").exists():
        _git("git", "init")
        _git("git", "config", "user.email", "leaf@localhost")
        _git("git", "config", "user.name", "Leaf")
        gitignore = WIKI_DIR / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("", encoding="utf-8")
        _git("git", "add", ".")
        _git("git", "commit", "--allow-empty", "-m", "Initial import")
    else:
        r = _git("git", "config", "user.name")
        if not r.stdout.strip():
            _git("git", "config", "user.email", "leaf@localhost")
            _git("git", "config", "user.name", "Leaf")
    if GIT_REMOTE:
        existing = _git("git", "remote", "get-url", "origin")
        if existing.returncode == 0:
            _git("git", "remote", "set-url", "origin", GIT_REMOTE)
        else:
            _git("git", "remote", "add", "origin", GIT_REMOTE)


def git_commit(paths: Path | list[Path], message: str) -> None:
    """Stage one path (or a list of paths) and commit them together."""
    if not GIT_ENABLED:
        return
    if isinstance(paths, Path):
        paths = [paths]
    rels = [str(p.relative_to(WIKI_DIR)) for p in paths]
    _git("git", "add", "--", *rels)
    _git("git", "commit", "-m", message)


_RENAME_BRACES_RE = re.compile(r"\{([^{}]*) => ([^{}]*)\}")


def _numstat_path(p: str) -> str:
    """The path after a commit from a --numstat path, which for a rename
    reads "old => new" or "dir/{old => new}.md"."""
    if " => " not in p:
        return p
    if "{" in p:
        return _RENAME_BRACES_RE.sub(r"\2", p).replace("//", "/")
    return p.split(" => ", 1)[1]


def git_log(path: Path, max_count: int | None = None) -> list[dict]:
    """The commits of a file, newest first, across renames (--follow): sha,
    date, time (YYYY-MM-DD HH:MM), rel (relative date), msg, the lines
    added and deleted, and the file's path at that commit (`path`, relative
    to the wiki), which differs from today's before a rename."""
    if not GIT_ENABLED:
        return []
    rel = str(path.relative_to(WIKI_DIR))
    count_args = ["-n", str(max_count)] if max_count else []
    r = _git("git", "log", "--follow", *count_args, "--numstat", "--date=format:%Y-%m-%d %H:%M",
             "--format=\x1e%H\x1f%as\x1f%ar\x1f%ad\x1f%s", "--", rel)
    entries = []
    for record in r.stdout.split("\x1e")[1:]:
        head, _, stats = record.partition("\n")
        parts = head.split("\x1f", 4)
        if len(parts) != 5:
            continue
        added = deleted = 0
        at = rel
        for line in stats.splitlines():
            cols = line.split("\t", 2)
            if len(cols) == 3:
                added += int(cols[0]) if cols[0].isdigit() else 0
                deleted += int(cols[1]) if cols[1].isdigit() else 0
                at = _numstat_path(cols[2])
        entries.append({"sha": parts[0], "date": parts[1], "rel": parts[2], "time": parts[3],
                        "msg": parts[4], "added": added, "deleted": deleted, "path": at})
    return entries


def git_show(sha: str, path: Path) -> str | None:
    rel = str(path.relative_to(WIKI_DIR))
    r = _git("git", "show", f"{sha}:{rel}")
    return r.stdout if r.returncode == 0 else None


def relative_time(dt: datetime) -> str:
    """Return relative time string like '2 days ago'."""
    now = datetime.now()
    delta = now - dt
    
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        mins = seconds // 60
        return f"{mins} minute{'s' if mins != 1 else ''} ago"
    elif seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    elif seconds < 2592000:  # 30 days
        days = seconds // 86400
        return f"{days} day{'s' if days != 1 else ''} ago"
    elif seconds < 31536000:  # 365 days
        months = seconds // 2592000
        return f"{months} month{'s' if months != 1 else ''} ago"
    else:
        years = seconds // 31536000
        return f"{years} year{'s' if years != 1 else ''} ago"


def git_uncommitted_files() -> list[dict]:
    """Files with uncommitted changes (modified, added, deleted, untracked),
    as dicts with 'filepath' (relative to WIKI_DIR), 'mtime' (YYYY-MM-DD)
    and 'rel' (relative time); both empty for a deleted file.

    -z: paths come unquoted (without it git quotes a name with a space or
    an umlaut, "my file.pdf", and committing that path failed)."""
    if not GIT_ENABLED:
        return []
    r = _git("git", "status", "--porcelain", "-z", "--untracked-files=all")
    fields = iter(r.stdout.split("\0"))
    files = []
    for entry in fields:
        status, filepath = entry[:2], entry[3:]
        if status[:1] in ("R", "C"):
            next(fields, None)   # a rename's or copy's old path
        if not status.strip() or not filepath or filepath.endswith("/"):
            continue
        fpath = WIKI_DIR / filepath
        mtime = rel = ""
        if fpath.exists():
            mtime_dt = datetime.fromtimestamp(fpath.stat().st_mtime)
            mtime, rel = mtime_dt.strftime("%Y-%m-%d"), relative_time(mtime_dt)
        files.append({"filepath": filepath, "mtime": mtime, "rel": rel})
    return files


def _commit_message(filepath: str) -> str:
    """Default commit message for committing a file from the uncommitted list."""
    return f"Save {filepath[:-3]}" if filepath.endswith('.md') else f"Update {filepath}"


def git_diff(sha: str, path: Path, *also: Path) -> str | None:
    """What a commit changed in a file. `also`: its name before the commit,
    when the commit renamed it, so the rename is seen as one (a pure rename
    changes nothing) instead of a new file."""
    rels = [str(p.relative_to(WIKI_DIR)) for p in (path, *also)]
    r = _git("git", "show", "--format=", "--patch", "-M", sha, "--", *dict.fromkeys(rels))
    return r.stdout.strip() if r.returncode == 0 else None


def _diff_to_html(diff: str) -> str:
    """Render a unified diff as classed <span> lines for the diff view.
    git's file headers (everything before a file's first @@) say nothing to
    a reader and are left out; a hunk header becomes a quiet "⋯" between
    the parts of the page that changed."""
    lines = []
    in_header = False
    for line in diff.splitlines():
        if line.startswith('diff --git '):
            in_header = True
        if line.startswith('@@'):
            in_header = False
            lines.append('<span class="diff-hunk">⋯</span>')
            continue
        if in_header:
            continue
        if line.startswith('+'):
            cls = 'diff-add'
        elif line.startswith('-'):
            cls = 'diff-del'
        else:
            cls = 'diff-ctx'
        lines.append(f'<span class="{cls}">{html.escape(line)}</span>')
    return ''.join(lines) or '<span class="diff-ctx">(no changes)</span>'


def _commit_form_html(filepath: str, title: str, on_success: str, *, inline: bool = False) -> str:
    """Commit button form wired up by leaf.js (posts to /api/commit-file).
    `inline` renders it as a link-styled word that sits at the end of the
    page's "Last changed … — uncommitted" line instead of a separate button."""
    btn_cls = "commit-link" if inline else "btn btn-primary"
    return (
        f'<form class="commit-file-form" style="display:inline"'
        f' data-filepath="{html.escape(filepath)}"'
        f' data-title="{html.escape(title)}"'
        f' data-on-success="{html.escape(on_success)}">'
        f'<button type="submit" class="{btn_cls}">Commit</button>'
        f'</form>'
    )


def _uncommitted_entries() -> list[dict]:
    """Uncommitted files as {slug, title, filepath, mtime, rel} dicts (slug/title
    resolved for wiki pages; slug is None for assets and other files)."""
    entries = []
    for f in git_uncommitted_files():
        filepath = f["filepath"]
        if filepath.endswith('.md') and not filepath.startswith('_assets'):
            slug = filepath[:-3]
            fpath = WIKI_DIR / filepath
            title = page_title(fpath.read_text(encoding="utf-8"), slug) if fpath.exists() else slug_display_name(slug)
            entries.append({"slug": slug, "title": title, "filepath": filepath,
                            "mtime": f["mtime"], "rel": f["rel"]})
        else:
            entries.append({"slug": None, "title": filepath, "filepath": filepath,
                            "mtime": f["mtime"], "rel": f["rel"]})
    return entries


git_setup()

# Initialize deck module and register its blueprint
deck_module.init_deck(WIKI_DIR, GIT_ENABLED, git_commit)
app.register_blueprint(deck_module.bp)

_LEAF_LOGO = """<svg class="site-logo" viewBox="0 0 16 16" aria-hidden="true">
    <path d="M12.8 1.7C9.6 1.8 7.1 3 5.5 5.1c-1.8 2.4-2.2 5.3-2.2 7.5 0 .4.3.7.7.7 2.2 0 5.1-.4 7.5-2.2 2.1-1.6 3.3-4.1 3.4-7.3 0-.4-.3-.8-.7-.8-.5-.1-.9-.1-1.4-.1Zm-6 9.5c-.2 0-.4-.1-.5-.3-.2-.3-.2-.7.2-1l5.1-3.8c.3-.2.7-.1 1 .2.2.3.1.7-.2 1l-5.1 3.8c-.2.1-.3.1-.5.1Z" fill="currentColor"/>
    <path d="M3.5 12.5 Q3 15 2 15" stroke="black" stroke-width="1.1" stroke-linecap="round" fill="none"/>
  </svg>"""

# Leaf's three modules: the wiki, the deck and the scratch notes. Every view
# names the one it belongs to (`<body data-module>`, `current_module()`);
# leaf.js marks the matching links as current (aria-current) and makes the
# "New" button create that module's kind of thing.
MODULES = (("wiki", "Wiki", "/"), ("scratch", "Scratch", "/page/_scratch"), ("deck", "Deck", "/deck"))

def _module_href(key: str, href: str) -> str:
    """A module's link: the Deck goes to the deck's home page when a page
    declares one ([[deck … home=1]]), else to /deck."""
    if key == "deck" and (home := deck_home_slug()):
        return f"/page/{html.escape(home)}"
    return href


def _deck_counts_title(c: dict) -> str:
    """The Deck links' tooltip, e.g. "Deck: 5 in Focus · 1 overdue · 1 due
    today" (leaf.js builds the same, deckCountsTitle)."""
    parts = [f"{c['focus']} in Focus"] if c["focus"] else []
    if c["overdue"]:
        parts.append(f"{c['overdue']} overdue")
    if c["today"]:
        parts.append(f"{c['today']} due today")
    return "Deck: " + " · ".join(parts) if parts else "Deck"


def _deck_counts_html() -> tuple[str, str]:
    """(title attribute, counts HTML) for the Deck links: the due count
    (orange) and the Focus count (muted), each hidden at zero. Rendered by the
    server, so the link doesn't change width after the page has painted;
    leaf.js refreshes them. Once per request."""
    if has_app_context() and "deck_counts" in g:
        return g.deck_counts
    c = deck_module.deck_counts()
    hide = lambda n: "" if n else " hidden"
    out = (f' title="{html.escape(_deck_counts_title(c))}"',
           f'<span class="module-counts"><span class="module-due"{hide(c["count"])}>{c["count"] or ""}</span>'
           f'<span class="module-focus"{hide(c["focus"])}>{c["focus"] or ""}</span></span>')
    if has_app_context():
        g.deck_counts = out
    return out


def _module_link_extras(key: str) -> tuple[str, str]:
    """(extra attributes, extra content) of a module link: aria-current on
    the current module's (rendered, not set by script, so the highlight is
    there at first paint), the Deck's counts."""
    attrs, content = _deck_counts_html() if key == "deck" else ("", "")
    if has_request_context() and current_module() == key:
        attrs += ' aria-current="page"'
    return attrs, content


def home_link() -> str:
    """The root of every breadcrumb, the module switcher: the leaf logo with
    "Wiki" (the home page), then Scratch and Deck. Phones and the editor show
    the logo only (style.css); phones have the bottom bar (action_bar_html()).
    The trail code in leaf.js keeps it (`.breadcrumb-home`) as the first
    crumb."""
    links = []
    for key, label, href in MODULES:
        attrs, counts = _module_link_extras(key)
        if key == "wiki":
            attrs += f' aria-label="{label} ({APP_NAME} home)">{_LEAF_LOGO}'
        else:
            attrs += ">"
        links.append(f'<a href="{_module_href(key, href)}" class="module-link" data-module="{key}"{attrs}'
                     f'<span class="module-label">{label}</span>{counts}</a>')
    return f'<nav class="breadcrumb-home module-switch" aria-label="{APP_NAME}">{"".join(links)}</nav>'


def current_module() -> str:
    """The module the current request's view belongs to: "deck", "scratch",
    "wiki", or "" for views of none (search results, login)."""
    path = request.path
    if path == "/deck":
        return "deck"
    if path in ("/search", "/login"):
        return ""
    m = re.match(r"^/(?:page|history)/(.+)$", path)
    if m and (m.group(1) == "_scratch" or is_scratch(m.group(1))):
        return "scratch"
    if path.startswith("/page/") and m.group(1) == deck_home_slug():
        return "deck"
    return "wiki"

def build_breadcrumb(slug: str, title: str, *extra: tuple, right_html: str = "", query_string: str = "") -> str:
    """Build a breadcrumb for a page slug.
    slug/title are the page; extra is zero or more (label, url_or_None) pairs appended after.
    right_html is optional HTML appended inside the breadcrumb div with margin-left: auto.
    query_string is appended to the page's own link (e.g. '?tag=task').
    """
    sep = '<span class="breadcrumb-sep">›</span>'
    parts = slug.split('/')
    crumbs = [home_link()]
    page_href = f'/page/{html.escape(slug)}{html.escape(query_string)}'
    for i, part in enumerate(parts[:-1]):
        ancestor = '/'.join(parts[:i + 1])
        label = part.replace('-', ' ').title()
        crumbs.append(f'<a href="/page/{ancestor}">{html.escape(label)}</a>')
    # Page itself — link if extra crumbs follow or query_string present, plain span otherwise
    if extra:
        crumbs.append(f'<a href="{page_href}">{html.escape(title)}</a>')
        for label, url in extra:
            if url:
                crumbs.append(f'<a href="{html.escape(url)}">{html.escape(label)}</a>')
            else:
                crumbs.append(f'<span>{html.escape(label)}</span>')
    elif query_string:
        crumbs.append(f'<a href="{page_href}">{html.escape(title)}</a>')
    else:
        crumbs.append(f'<span>{html.escape(title)}</span>')
    right = f'<span class="breadcrumb-right">{right_html}</span>' if right_html else ""
    return f'<div class="breadcrumb" data-slug="{html.escape(slug)}" data-title="{html.escape(title)}">{sep.join(crumbs)}{right}</div>'


md = (
    # alerts: GitHub/Obsidian callouts ("> [!NOTE]", TIP, IMPORTANT, WARNING,
    # CAUTION) render as <div class="markdown-alert markdown-alert-…">.
    MarkdownIt(options_update={"html": True, "alerts": True})
    .enable("table")
    .enable("strikethrough")
    .enable("smartquotes")
    .enable("replacements")
    .use(tasklists_plugin)
    .use(footnote_plugin)
    .use(deflist_plugin)
    .use(anchors_plugin, max_level=3)
    # Bare URLs, www. addresses and emails become links (GFM autolinks), for
    # text the editor's paste-a-URL prompt never saw: files edited on disk,
    # an unnamed paste, a URL inside pasted text.
    .use(gfm_autolink_plugin)
)


def _is_external(href: str) -> bool:
    """An http(s) link to another site: it opens in a new tab. A full URL
    to Leaf itself (the host this request came in on) is not external."""
    parts = urlparse(href)
    if parts.scheme not in ("http", "https"):
        return False
    return not (has_request_context() and parts.netloc.lower() == request.host.lower())


def _link_open(self, tokens, idx, options, env):
    tok = tokens[idx]
    if _is_external(tok.attrGet("href") or ""):
        tok.attrSet("target", "_blank")
        tok.attrSet("rel", "noopener noreferrer")
    return self.renderToken(tokens, idx, options, env)


md.add_render_rule("link_open", _link_open)


# ── Helpers ───────────────────────────────────────────────────────────────────


def to_slug(name: str) -> str:
    """Slugify a name, preserving '/' as a path separator."""
    parts = name.strip().split('/')
    slugged = []
    for part in parts:
        s = part.strip().lower()
        s = re.sub(r"[^a-z0-9_]+", "-", s)
        s = s.strip("-")
        if s:
            slugged.append(s)
    return "/".join(slugged)


def is_protected(slug: str) -> bool:
    """Pages that must never be renamed or deleted via the UI."""
    return slug == "index"


def safe_path(slug: str) -> Path | None:
    """Return resolved path only if it's safely inside PAGES_DIR."""
    pages_root = PAGES_DIR.resolve()
    target = (PAGES_DIR / f"{slug}.md").resolve()
    try:
        target.relative_to(pages_root)
        return target
    except ValueError:
        return None


SCRATCH_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})_(.+)$")


def is_scratch(slug: str) -> bool:
    return bool(SCRATCH_RE.match(slug))


def iter_pages():
    """Yield (path, slug) for all pages, skipping _* subdirectories (assets,
    the semantic index) and hidden ones. It prunes them instead of walking
    them: rglob went through every object in .git on each call, and a search
    lists the pages several times."""
    root = str(PAGES_DIR)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(('_', '.'))]
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        prefix = "" if rel_dir == "." else rel_dir + "/"
        for name in filenames:
            if name.endswith(".md"):
                yield Path(dirpath, name), prefix + name[:-3]


def all_slugs() -> list[str]:
    return sorted(slug for _, slug in iter_pages())


def list_deleted_pages() -> list[dict]:
    """Pages that exist in git history but not on disk, newest deletion first."""
    if not GIT_ENABLED:
        return []
    r = _git("git", "log", "--diff-filter=D", "--format=%H\t%P\t%as", "--name-only")
    if r.returncode != 0:
        return []
    existing_slugs = set(slug for _, slug in iter_pages())
    deleted, seen = [], set()
    current_sha = current_parent = current_date = None
    for raw in r.stdout.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if '\t' in line:
            parts = line.split('\t', 2)
            current_sha = parts[0]
            parents = parts[1] if len(parts) > 1 else ''
            current_parent = parents.split()[0] if parents.strip() else None
            current_date = parts[2] if len(parts) > 2 else ''
        elif current_parent and line.endswith('.md') and not line.startswith('.'):
            slug = line[:-3]
            if slug in existing_slugs or slug in seen:
                continue
            if slug.startswith('_assets'):
                continue
            seen.add(slug)
            r2 = _git("git", "show", f"{current_parent}:{line}")
            title = page_title(r2.stdout, slug) if r2.returncode == 0 else slug_display_name(slug)
            deleted.append({"slug": slug, "title": title, "sha": current_sha, "date": current_date})
    return deleted


def list_scratch() -> list[dict]:
    """All scratch notes as {slug, title, date}, newest first (in sharing
    mode without the ones not for sharing)."""
    entries = []
    for md_file in PAGES_DIR.glob("*.md"):
        m = SCRATCH_RE.match(md_file.stem)
        if not m or hidden_page(md_file.stem):
            continue
        ts = datetime.strptime(m.group(1), "%Y-%m-%d_%H-%M-%S")
        entries.append({"slug": md_file.stem, "title": _page_meta(md_file, md_file.stem)[0],
                        "ts": ts, "date": ts.strftime("%Y-%m-%d %H:%M")})
    entries.sort(key=lambda n: n["ts"], reverse=True)
    return entries


def sharing_on() -> bool:
    """Whether this request comes from a browser in sharing mode."""
    return has_request_context() and request.cookies.get(SHARING_COOKIE) == "1"


# slug -> (mtime, whether its tags name it not for sharing)
_noshare_tag_cache: dict[str, tuple[float, bool]] = {}


def is_noshare(slug: str) -> bool:
    """A page LEAF_NOSHARE names: in a noshare folder, or with a noshare tag
    in its frontmatter (cached by the file's mtime)."""
    low = slug.lower()
    if any(low.startswith(f) or low == f[:-1] for f in NOSHARE_FOLDERS):
        return True
    if not NOSHARE_TAGS:
        return False
    path = safe_path(slug)
    try:
        mtime = path.stat().st_mtime if path else None
    except OSError:
        mtime = None
    if mtime is None:
        return False
    cached = _noshare_tag_cache.get(slug)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        tags = {t.lower() for t in parse_frontmatter(path.read_text(encoding="utf-8"))[0].get("tags", [])}
    except OSError:
        tags = set()
    result = bool(tags & NOSHARE_TAGS)
    _noshare_tag_cache[slug] = (mtime, result)
    return result


def hidden_page(slug: str) -> bool:
    """A page to leave out of this response: sharing mode is on and the page
    is not for sharing."""
    return sharing_on() and is_noshare(slug)


def hidden_page_view() -> str:
    """What a page not for sharing shows in sharing mode: neither its title
    nor its text, and no stop in the navigation trail."""
    body = (f'<div class="breadcrumb">{home_link()}<span class="breadcrumb-sep">›</span>'
            '<span>Hidden page</span></div>'
            '<header class="page-header"><h1>Hidden page</h1></header>'
            '<article class="page-content"><p class="hidden-page-note">This page is not shown in '
            'sharing mode. Turn sharing mode off (the eye at the top, or <kbd>S</kbd>) to see it.</p></article>')
    return shell("Hidden page", body)


_H1_RE = re.compile(r"^#[ \t]+(\S.*)$")


def page_title(content: str, slug: str) -> str:
    """First "# " heading in the file, else a title derived from the slug.

    Skips a leading frontmatter block and fenced code, so a shell comment in
    a code example ("# install deps") never becomes the page title."""
    lines = content.splitlines()
    start = _body_start(lines)
    for i, line, in_fence in _iter_fenced_lines(lines):
        if i >= start and not in_fence:
            m = _H1_RE.match(line)
            if m:
                return m.group(1).strip()
    return slug_display_name(slug)


def split_wikilink(inner: str) -> tuple[str, str, str]:
    """A wikilink's inside as (page, section, label): `Page`, `Page|Label`,
    `Page#Section` (a heading on that page), `Page#Section|Label`, or
    `#Section` (a heading on this page). The label defaults to the page,
    "Page › Section" with a section, or the section alone."""
    target, bar, label = inner.partition("|")
    page, _, section = target.partition("#")
    page, section = page.strip(), section.strip()
    if not bar:
        label = f"{page} › {section}" if page and section else section or page
    return page, section, label.strip()


def section_anchor(section: str) -> str:
    """The heading id a wikilink's #Section points at (as rendered)."""
    return _heading_id(_plain_heading(section))


def process_wikilinks(text: str) -> str:
    slugs = set(all_slugs())

    def replace(m: re.Match) -> str:
        page_name, section, label = split_wikilink(m.group(1))
        anchor = f"#{html.escape(section_anchor(section))}" if section else ""
        label = html.escape(label)
        if not page_name:
            return f'<a href="{anchor or "#"}" class="wikilink">{label}</a>'
        slug = page_name if page_name in slugs else to_slug(page_name)
        if hidden_page(slug):
            # Sharing mode: the words stay (they are this page's), the link
            # to a page not for sharing goes.
            return f'<span class="wikilink-hidden">{label}</span>'
        css = "wikilink" if slug in slugs else "wikilink wikilink-missing"
        return f'<a href="/page/{html.escape(slug)}{anchor}" class="{css}">{label}</a>'

    return WIKILINK_RE.sub(replace, text)

_TOC_PLACEHOLDER = "LEAFTOCPLACEHOLDER"
_TOC_RE = re.compile(r'^\[\[toc\]\][^\S\n]*$', re.IGNORECASE | re.MULTILINE)


def _heading_id(text: str) -> str:
    """Match mdit-py-plugins anchors_plugin default slugify."""
    return re.sub(r'[^\w\- ]+', '', text, flags=re.UNICODE).strip().lower().replace(' ', '-')


def _plain_heading(text: str) -> str:
    """Strip inline markdown and wikilinks from a heading to get plain text for anchor IDs."""
    text = _strip_links(text)
    text = re.sub(r'!?\[([^\]]*)\]', r'\1', text)                # bare [label]
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'_(.+?)_', r'\1', text)
    text = re.sub(r'`[^`]*`', '', text)
    return text.strip()


def _strip_links(text: str) -> str:
    """Replace wikilinks and markdown links with their label text, keeping other inline markup."""
    text = re.sub(r'\[\[([^\]|]+)\|([^\]]+)\]\]', r'\2', text)  # [[page|label]] → label
    text = re.sub(r'\[\[([^\]]+)\]\]', r'\1', text)              # [[page]] → page
    text = re.sub(r'!?\[([^\]]*)\]\([^)]*\)', r'\1', text)       # [label](url) / ![alt](url)
    text = re.sub(r'\[([^\]]*)\]\[[^\]]*\]', r'\1', text)        # [label][ref]
    return text


def _build_toc(text: str, unmask=lambda s: s) -> str:
    """TOC from ## / ### headings. `text` may have code masked (so headings
    inside fenced code are not picked up); `unmask` restores inline code in
    the heading titles."""
    headings = re.findall(r'^(#{2,3})\s+(.+)', text, re.MULTILINE)
    if not headings:
        return ''
    items = []
    for hashes, title in headings:
        level = len(hashes)
        title = unmask(title)
        plain = _plain_heading(title.strip())
        hid = _heading_id(plain)
        cls = ' class="toc-h3"' if level == 3 else ''
        label = md.renderInline(_strip_links(title.strip()))
        items.append(f'<li{cls}><a href="#{hid}">{label}</a></li>')
    return '<nav class="toc"><ul>\n' + '\n'.join(items) + '\n</ul></nav>'


_IMG_PARA_RE = re.compile(r'<p>(<img\b[^>]*>)</p>')


def _mark_image_paragraphs(html_text: str) -> str:
    """An image alone in its paragraph gets `<p class="img-block">`: on big
    screens such an image may grow beyond the text width (style.css, Side
    columns), an image within a sentence may not."""
    return _IMG_PARA_RE.sub(r'<p class="img-block">\1</p>', html_text)


_IMG_SIZE_RE = re.compile(r'<img\b[^>]*\balt="([^"]*)\|(\d+)"[^>]*>', re.IGNORECASE)

# ── Task metadata ─────────────────────────────────────────────────────────────
# Format stored in markdown: - [ ] task text <!--t:2026-06-12T18:35:00-->
#                             - [x] task text <!--t:2026-06-12T18:35:00 d:2026-06-12T19:00:00-->
_TASK_META_SRC_RE = re.compile(r'<!--t:(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\s+d:(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}))?-->')
_TASK_DONE_ONLY_RE = re.compile(r'<!--d:(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})-->')


def _render_task_dates(html_str: str) -> str:
    """Convert task metadata HTML comments to visible date chips."""
    def replace_full(m: re.Match) -> str:
        created, done = m.group(1), m.group(2)
        chips = f'<span class="task-date task-date-created" title="Created {created}">{created[:10]}</span>'
        if done:
            chips += f'<span class="task-date task-date-done" title="Done {done}">{done[:10]}</span>'
        return chips
    def replace_done_only(m: re.Match) -> str:
        done = m.group(1)
        return f'<span class="task-date task-date-done" title="Done {done}">{done[:10]}</span>'
    html_str = _TASK_META_SRC_RE.sub(replace_full, html_str)
    html_str = _TASK_DONE_ONLY_RE.sub(replace_done_only, html_str)
    return html_str


def _apply_img_sizes(html_str: str) -> str:
    """Rewrite <img src=... alt="text|300"> → same tag with |300 stripped and style added."""
    def fix(m: re.Match) -> str:
        tag = m.group(0)
        alt_text = m.group(1)
        width = m.group(2)
        tag = tag.replace(f'{alt_text}|{width}"', f'{alt_text}"', 1)
        tag = tag[:-1] + f' style="max-width:100%;width:{width}px">'
        return tag
    return _IMG_SIZE_RE.sub(fix, html_str)


# Private-use code points: never typed by users, and not matched by any of the
# directive regexes, so a masked code region is invisible to pre-processing.
_CODE_MASK_RE = re.compile('\ue000(\\d+)\ue001')
_INLINE_CODE_RE = re.compile(r'(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)')


def _mask_code(text: str) -> tuple[str, list[str]]:
    """Replace fenced code blocks and inline code spans with opaque tokens.

    [[wikilinks]], [[toc]] and [[deck]] are rewritten on the raw markdown before
    markdown-it runs, so without this they would also be rewritten inside code
    (showing up as escaped HTML in the block, or embedding a whole deck app in a
    <pre>). Restore with _unmask_code() before rendering."""
    stash: list[str] = []

    def token(s: str) -> str:
        stash.append(s)
        return f'\ue000{len(stash) - 1}\ue001'

    out = []
    for _, line, in_fence in _iter_fenced_lines(text.splitlines(keepends=True)):
        if in_fence:
            body = line.rstrip('\n')
            out.append(token(body) + line[len(body):])
        else:
            out.append(_INLINE_CODE_RE.sub(lambda m: token(m.group(0)), line))
    return ''.join(out), stash


def _unmask_code(text: str, stash: list[str]) -> str:
    return _CODE_MASK_RE.sub(lambda m: stash[int(m.group(1))], text)


# markdown-it's tasklists plugin renders checkboxes disabled; page view and the
# editor preview make them clickable, so drop the attribute — on those inputs
# only, never elsewhere in the document.
_TASK_CHECKBOX_RE = re.compile(r'<input\b[^>]*\btask-list-item-checkbox\b[^>]*>')


def _enable_task_checkboxes(html_str: str) -> str:
    return _TASK_CHECKBOX_RE.sub(
        lambda m: re.sub(r'\s+disabled(?:="[^"]*")?', '', m.group(0)), html_str)


def render_md(text: str, *, show_tags: bool = True, show_tldr: bool = True,
              prefer_lists: list[str] | None = None) -> str:
    """Render a page's markdown. Task chips link to a task in a list the text
    declares with [[deck list=…]] (or one of `prefer_lists`) when the task is
    in it, rather than in its first list A–Z (_link_list)."""
    fm_meta, text = parse_frontmatter(text)
    prefer = [name for name, _ in _page_deck_lists(text)] + list(prefer_lists or [])
    fm_html = _render_frontmatter_html(fm_meta, show_tags=show_tags, show_tldr=show_tldr)

    text, code_stash = _mask_code(text)

    toc_html = ''
    if _TOC_RE.search(text):
        toc_html = _build_toc(text, unmask=lambda s: _unmask_code(s, code_stash))
        text = _TOC_RE.sub(_TOC_PLACEHOLDER, text)

    # Replace [[deck …]] directives with placeholders (supports multiple per page)
    text, deck_embeds = deck_module.process_deck_embeds(text)
    text, task_refs = _mask_task_refs(text)

    text = _unmask_code(process_wikilinks(text), code_stash)
    result = _mark_image_paragraphs(_apply_img_sizes(md.render(text)))
    result = _enable_task_checkboxes(result)
    result = _render_task_dates(result)
    result = _inject_task_refs(result, task_refs, prefer)
    if toc_html:
        result = result.replace(f'<p>{_TOC_PLACEHOLDER}</p>', toc_html)
    if deck_embeds:
        result = deck_module.inject_deck_embeds(result, deck_embeds)
    return fm_html + result


# [[task:<id>]] references (deck.TASK_REF_RE): masked with private-use tokens
# before rendering, so markdown never touches the chip, then swapped for it.
_TASK_REF_TOKEN_RE = re.compile('(\\d+)')
# A reference alone in a list item is a row of its own (a checklist of tasks).
_TASK_REF_ITEM_RE = re.compile(r'<li>(?=(?:\s|<p>)*\d+(?:\s|</p>)*</li>)')


def _mask_task_refs(text: str) -> tuple[str, list[str]]:
    refs: list[str] = []

    def token(m: re.Match) -> str:
        refs.append(m.group(1).lower())
        return f'{len(refs) - 1}'
    return deck_module.TASK_REF_RE.sub(token, text), refs


def _inject_task_refs(html_str: str, refs: list[str], prefer: list[str] = ()) -> str:
    if not refs:
        return html_str
    tasks = deck_module.resolve_task_refs(refs)
    today = date.today().isoformat()
    html_str = _TASK_REF_ITEM_RE.sub('<li class="task-ref-item">', html_str)
    return _TASK_REF_TOKEN_RE.sub(
        lambda m: _task_ref_html(refs[int(m.group(1))], tasks[refs[int(m.group(1))]], today, prefer),
        html_str)


def _short_date(iso: str, today: str) -> str:
    """"Oct 5" (this year) or "Oct 5, 2025", as the deck's cards show dates."""
    try:
        d = date.fromisoformat(iso[:10])
    except ValueError:
        return iso
    text = f"{d:%b} {d.day}"
    return text if iso[:4] == today[:4] else f"{text}, {d.year}"


def _link_list(task: dict, prefer=()) -> str | None:
    """The list a link to a task opens it in: one of `prefer` (the lists the
    page with the link declares) when the task is in it, so a task in two
    lists opens in the one the page is about; else its own (deck.task_list)."""
    tags = {t.lower() for t in task.get("tags") or []}
    hits = sorted(tags & {p.lower() for p in prefer})
    return hits[0] if hits else deck_module.task_list(task)


def _task_ref_html(ref: str, task: dict | None, today: str, prefer=()) -> str:
    """A task as an inline chip: its checkbox (completes it in the deck,
    leaf.js), its title linking to it in the deck, and badges for what the
    deck card shows at a glance (deadline, a later start, repeat, keep)."""
    if task is None:
        return (f'<span class="task-ref task-ref-missing" title="No task with id {html.escape(ref)}">'
                f'task {html.escape(ref)}?</span>')
    title = html.escape(_plain_task_title(task.get("title", "")))
    done, wont = bool(task.get("completed_at")), bool(task.get("wont_do"))
    list_name = _link_list(task, prefer)
    href = html.escape(_task_link(task, list_name))
    cls, badges = "task-ref", []
    if done:
        cls += " task-ref-wontdo" if wont else " task-ref-done"
        mark = "✗ won't do" if wont else "✓"
        badges.append(f'<span class="task-ref-badge">{mark} {_short_date(task["completed_at"], today)}</span>')
    else:
        due, start = task.get("due_date"), task.get("start_date")
        if due:
            state = " overdue" if due < today else " due-today" if due == today else ""
            badges.append(f'<span class="task-ref-badge task-ref-due{state}" title="Due {due}">'
                          f'⏰ {_short_date(due, today)}</span>')
        if start and start >= today:
            state = " start-today" if start == today else " start-later"
            label = "today" if start == today else _short_date(start, today)
            badges.append(f'<span class="task-ref-badge task-ref-start{state}" title="Start {start}">'
                          f'📅 {label}</span>')
        if task.get("recurrence"):
            badges.append('<span class="task-ref-badge" title="Repeats">↻</span>')
        if task.get("keep"):
            last = f" ✓ {_short_date(task['last_done'], today)}" if task.get("last_done") else ""
            badges.append(f'<span class="task-ref-badge" title="Keep task: stays after done">⟲{last}</span>')
    if list_name:
        badges.append(f'<span class="task-ref-badge task-ref-list">{html.escape(list_name)}</span>')
    checked = " checked" if done and not wont else ""
    disabled = " disabled" if wont else ""
    return (f'<span class="{cls}" data-task-id="{html.escape(task["id"])}">'
            f'<input type="checkbox" class="task-ref-check" aria-label="Done: {title}"{checked}{disabled}>'
            f'<a class="task-ref-title" href="{href}">{title}</a>{"".join(badges)}</span>')


def page_task_refs(content: str) -> set[str]:
    """Ids of the tasks a page refers to with [[task:…]], code excluded."""
    masked, _ = _mask_code(content)
    refs = [m.group(1).lower() for m in deck_module.TASK_REF_RE.finditer(masked)]
    return {t["id"] for t in deck_module.resolve_task_refs(refs).values() if t}


def _iter_fenced_lines(lines: list[str]):
    """Yield (index, line, in_fence) for each line, where in_fence is True for
    fence delimiter lines (``` / ~~~) and everything between them."""
    in_fence = False
    fence_marker = ''
    fence_len = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not in_fence:
            if stripped.startswith('```') or stripped.startswith('~~~'):
                in_fence = True
                fence_marker = stripped[0]
                fence_len = len(stripped) - len(stripped.lstrip(fence_marker))
                yield i, line, True
            else:
                yield i, line, False
        else:
            # Closing fence must use same char and be at least as long
            if stripped.startswith(fence_marker * fence_len) and stripped.rstrip(fence_marker) == '':
                in_fence = False
            yield i, line, True


# A task-list item as markdown-it's tasklists plugin renders it: a list marker
# (optionally inside blockquotes), then "[ ]"/"[x]" followed by whitespace and
# text. Only these lines produce checkboxes, so only these may be counted —
# the Nth rendered checkbox must be the Nth match here. Mirrored in leaf.js
# (taskCheckboxOffsets) for toggling inside the editor.
_TASK_ITEM_RE = re.compile(r'^[ \t]*(?:>[ \t]?)*[ \t]*(?:[-*+]|\d{1,9}[.)])[ \t]+\[([ xX])\](?=[ \t]+\S)')


def _task_item_lines(lines: list[str]) -> list[tuple[int, re.Match]]:
    """(line index, match) for every task-list checkbox line, skipping a
    leading frontmatter block and fenced code."""
    start = _body_start(lines)
    found = []
    for i, line, in_fence in _iter_fenced_lines(lines):
        if i < start or in_fence:
            continue
        m = _TASK_ITEM_RE.match(line)
        if m:
            found.append((i, m))
    return found


def toggle_nth_checkbox(content: str, idx: int) -> str:
    """Toggle the Nth task-list checkbox (0-based), in rendered order."""
    lines = content.splitlines(keepends=True)
    items = _task_item_lines(lines)
    if idx >= len(items):
        return content
    li, m = items[idx]
    line = lines[li]
    eol = '\n' if line.endswith('\n') else ''
    body = line.rstrip('\n')

    # Toggle exactly the item's own [ ] / [x], never a later bracket on the line
    is_checking = m.group(1) == ' '
    body = body[:m.start(1)] + ('x' if is_checking else ' ') + body[m.end(1):]

    now = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    meta_m = _TASK_META_SRC_RE.search(body)
    if is_checking:
        if meta_m:
            # Splice done date into existing comment
            body = (body[:meta_m.start()] +
                    f'<!--t:{meta_m.group(1)} d:{now}-->' +
                    body[meta_m.end():])
        else:
            done_m = _TASK_DONE_ONLY_RE.search(body)
            if done_m:
                body = body[:done_m.start()] + f'<!--d:{now}-->' + body[done_m.end():]
            else:
                body = body.rstrip() + f' <!--d:{now}-->'
    else:
        if meta_m:
            # Strip done date, keep created
            body = (body[:meta_m.start()] +
                    f'<!--t:{meta_m.group(1)}-->' +
                    body[meta_m.end():])
        else:
            # Remove standalone done comment
            body = _TASK_DONE_ONLY_RE.sub('', body).rstrip()

    lines[li] = body + eol
    return ''.join(lines)


def split_sections(content: str) -> list[str]:
    """Split content at ## and ### headings not inside fenced code blocks.
    Handles ATX headings with up to 3 leading spaces and setext h2 headings
    (heading text followed by a line of dashes).
    Returns [preamble, section1, section2, ...]
    """
    lines = content.splitlines(keepends=True)
    heading_lines: list[int] = []
    prev_text_line_idx: int | None = None   # for setext h2 detection

    for i, line, in_fence in _iter_fenced_lines(lines):
        stripped = line.strip()
        if in_fence:
            prev_text_line_idx = None
        elif re.match(r'^ {0,3}#{2,3} ', line):
            # ATX heading: ## or ### with up to 3 optional leading spaces
            heading_lines.append(i)
            prev_text_line_idx = None
        elif stripped and re.match(r'^[-=]+\s*$', stripped):
            # Potential setext underline (--- for h2, === for h1) or thematic break.
            # If preceded by a non-empty text line, it's a setext heading.
            # Exclude lines that look like list items (e.g. "- " stripped to "-"):
            # a list bullet is "[-*+] " so if the raw line starts with a list marker
            # followed by a space, it's not a setext underline.
            is_list_item = bool(re.match(r'^[-*+]\s', line))
            prev_is_list = prev_text_line_idx is not None and bool(
                re.match(r'^[ \t]{0,3}[-*+]\s', lines[prev_text_line_idx])
            )
            if re.match(r'^-+\s*$', stripped) and prev_text_line_idx is not None and not is_list_item and not prev_is_list:
                heading_lines.append(prev_text_line_idx)
            # The underline/thematic-break line itself is never a heading text line.
            prev_text_line_idx = None
        elif stripped:
            prev_text_line_idx = i
        else:
            prev_text_line_idx = None

    if not heading_lines:
        return [content]
    sections = [''.join(lines[:heading_lines[0]])]
    for j, pos in enumerate(heading_lines):
        end = heading_lines[j + 1] if j + 1 < len(heading_lines) else len(lines)
        sections.append(''.join(lines[pos:end]))
    return sections


def content_hash(text: str) -> str:
    """Return a short hex hash of *text* for change detection."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def section_char_range(full_content: str, section_idx: int) -> tuple[int, int] | None:
    """Return (start_char, end_char) of section *section_idx* within *full_content*.

    section_idx is 1-based (0 = preamble, which is not editable as a section).
    Returns None if the index is invalid.
    """
    sections = split_sections(full_content)
    if not (1 <= section_idx <= len(sections) - 1):
        return None
    # Character offset = sum of lengths of all prior sections
    start = sum(len(s) for s in sections[:section_idx])
    end = start + len(sections[section_idx])
    return start, end


def _inject_section_edit_buttons(html_str: str, slug: str) -> str:
    """Wrap each rendered <h2>/<h3> with a .section-header div and append an edit link."""
    counter = [0]

    def wrap(m: re.Match) -> str:
        counter[0] += 1
        n = counter[0]
        level = m.group(1)[1]  # '2' or '3' (strip leading 'h')
        btn = (
            f'<a href="/edit/{html.escape(slug)}?section={n}" '
            f'class="section-edit-btn" title="Edit section">✎</a>'
        )
        return f'<div class="section-header section-header-h{level}">{m.group(0)}{btn}</div>'

    return re.sub(r'<(h[23])\b[^>]*>.*?</\1>', wrap, html_str, flags=re.DOTALL)


def _inject_section_tasks(html_str: str, task_backlinks: list[dict], prefer=()) -> str:
    """A section that tasks link to (`[[Page#Section]]`) gets a ☑ N chip
    after its heading, opening the list of those tasks (a <details>, no JS)."""
    by_anchor: dict[str, list[dict]] = {}
    for t in task_backlinks:
        if t.get("anchor"):
            by_anchor.setdefault(t["anchor"], []).append(t)
    if not by_anchor:
        return html_str

    def chip(m: re.Match) -> str:
        tasks = by_anchor.get(html.unescape(m.group(2)))
        if not tasks:
            return m.group(0)
        n = len(tasks)
        items = "".join(
            f'<li><a href="{html.escape(_task_href(t["id"], "open", _link_list(t, prefer)))}">'
            f'{html.escape(_plain_task_title(t["title"]))}</a></li>' for t in tasks)
        return (f'{m.group(0)}<details class="section-tasks"><summary title="{n} linked task{"s" if n > 1 else ""}">'
                f'☑ {n}</summary><ul>{items}</ul></details>')

    return re.sub(r'<h([23]) id="([^"]+)">.*?</h\1>', chip, html_str, flags=re.S)


def strip_h1(content: str) -> str:
    """Remove the first H1 line so it isn't duplicated below the page header."""
    lines = content.split("\n")
    for i in range(_body_start(lines), len(lines)):
        if lines[i].strip():
            if re.match(r"^#\s+", lines[i]):
                del lines[i]
            break
    return "\n".join(lines)


_FRONTMATTER_FENCE = '---'


def _frontmatter_end(lines: list[str]) -> int | None:
    """The index of the line closing a leading frontmatter block, or None
    when the text has none (no opening fence, or it is never closed)."""
    if not lines or lines[0].strip() != _FRONTMATTER_FENCE:
        return None
    return next((i for i in range(1, len(lines)) if lines[i].strip() == _FRONTMATTER_FENCE), None)


def _body_start(lines: list[str]) -> int:
    """The index of the first line after a leading frontmatter block."""
    end = _frontmatter_end(lines)
    return 0 if end is None else end + 1


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse a leading frontmatter block (--- ... ---) for `tags` and `tldr`.

    Returns (metadata, content_with_frontmatter_removed). Absent or malformed
    frontmatter (no closing fence) yields an empty metadata dict and the
    original content unchanged.
    """
    lines = content.split("\n")
    end_idx = _frontmatter_end(lines)
    if end_idx is None:
        return {}, content

    meta: dict = {}
    for line in lines[1:end_idx]:
        if ':' not in line:
            continue
        key, _, value = line.partition(':')
        key = key.strip().lower()
        value = value.strip()
        if key == 'tags':
            value = value.strip('[]')
            tags = [t.strip().strip('"\'') for t in value.split(',') if t.strip()]
            if tags:
                meta['tags'] = tags
        elif key == 'tldr':
            value = value.strip('"\'')
            if value:
                meta['tldr'] = value

    rest = lines[end_idx + 1:]
    if rest and rest[0].strip() == '':
        rest = rest[1:]
    return meta, "\n".join(rest)


def _render_tags_pills_html(tags: list[str]) -> str:
    """Render a list of tags as clickable pills linking to search. A pill
    searches `#tag`, the pages and tasks with that tag (search.Query), not
    every page that happens to use the word; a tag with a space in it can't
    be written as #tag, so it stays a word search."""
    if not tags:
        return ''
    pills = ''.join(
        f'<a class="task-tag page-tag" href="/search?{urlencode({"q": t if any(c.isspace() for c in t) else "#" + t})}">'
        f'{html.escape(t)}</a>'
        for t in tags
    )
    return f'<div class="page-tags">{pills}</div>'


def _render_frontmatter_html(meta: dict, *, show_tags: bool = True, show_tldr: bool = True) -> str:
    """Render parsed frontmatter (tags/tldr) as a collapsed-by-default block.

    `show_tags`/`show_tldr` let a caller omit either half — used by view_page,
    which renders both itself in the page-meta row instead of the body.
    """
    if not meta:
        return ''
    parts = []
    if show_tldr:
        tldr = meta.get('tldr')
        if tldr:
            parts.append(f'<p class="page-tldr">{html.escape(tldr)}</p>')
    if show_tags:
        parts.append(_render_tags_pills_html(meta.get('tags', [])))
    parts = [p for p in parts if p]
    if not parts:
        return ''
    return (
        '<details class="page-frontmatter"><summary>tldr</summary>'
        + ''.join(parts) + '</details>'
    )


def _resolve_relative_link(href: str, source_slug: str) -> str | None:
    """Resolve a relative link target (already stripped of .md) against the
    source page's directory, normalizing ../ and ./ segments."""
    source_dir = source_slug.split('/')[:-1]
    normalized: list[str] = []
    for part in source_dir + href.split('/'):
        if part == '..':
            if normalized:
                normalized.pop()
        elif part and part != '.':
            normalized.append(part)
    return '/'.join(normalized) if normalized else None


def _resolve_md_link(href: str, source_slug: str) -> str | None:
    """Resolve a relative .md link href to a slug, or None if external."""
    if href.startswith(('http://', 'https://', '//', '/')):
        return None
    return _resolve_relative_link(href[:-3], source_slug)


def _resolve_bare_link(href: str, source_slug: str) -> str | None:
    """Resolve a bare relative slug link (no extension) to a slug, or None if external."""
    if href.startswith(('http://', 'https://', '//', '/', '#')) or ':' in href:
        return None
    return _resolve_relative_link(href, source_slug)


def _page_link_targets(content: str, file_slug: str, known_slugs: set[str] | None = None) -> set[str]:
    """Collect all slugs that a page links to (wikilinks + markdown links)."""
    targets: set[str] = set()
    for m in WIKILINK_RE.finditer(content):
        page = split_wikilink(m.group(1))[0]
        if page:
            targets.add(to_slug(page))
    for m in _ANY_MD_LINK_RE.finditer(content):
        href = m.group(1)
        if href.endswith('.md'):
            resolved = _resolve_md_link(href, file_slug)
        else:
            resolved = _resolve_bare_link(href, file_slug)
        if resolved and (known_slugs is None or resolved in known_slugs):
            targets.add(resolved)
    return targets


# Cache of per-page derived data, keyed by slug -> (mtime, title, link targets).
# Same invalidation model as _search_doc_cache: re-derived when the on-disk
# mtime changes, so edits made outside the app stay reflected. Link targets are
# cached unfiltered; both consumers only ever look up slugs that exist, so
# filtering against the known-slug set is unnecessary. The last item is the
# params of the page's deck embeds (_page_deck_embeds): the separate lists it
# declares and whether it is the deck's home come from them.
_page_meta_cache: dict[str, tuple[float, str, set[str], list]] = {}


def _page_deck_embeds(content: str) -> list[dict]:
    """The params of each [[deck …]] on a page, outside code."""
    masked, _ = _mask_code(content)
    return [deck_module._parse_tasks_params(m.group(1) or "")
            for m in deck_module._DECK_RE.finditer(masked)]


def _embeds_lists(embeds: list[dict]) -> list[tuple[str, dict]]:
    """(list name, embed params) of each embed that declares a list."""
    return [(name, params) for params in embeds
            if (name := deck_module._clean_list(params.get("list")))]


def _page_deck_lists(content: str) -> list[tuple[str, dict]]:
    """(list name, embed params) of each [[deck list=…]] on a page, outside
    code."""
    return _embeds_lists(_page_deck_embeds(content))


def _page_meta_entry(md_file: Path, slug: str) -> tuple[float, str, set[str], list]:
    """The page's _page_meta_cache entry, re-derived when its mtime changed."""
    # Every page is scanned for list declarations (search, the due badge), so
    # one that vanished meanwhile or isn't UTF-8 must not break anything.
    try:
        mtime = md_file.stat().st_mtime
        cached = _page_meta_cache.get(slug)
        if cached and cached[0] == mtime:
            return cached
        content = md_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0.0, slug_display_name(slug), set(), []
    entry = (mtime, page_title(content, slug), _page_link_targets(content, slug),
             _page_deck_embeds(content))
    _page_meta_cache[slug] = entry
    return entry


def _page_meta(md_file: Path, slug: str) -> tuple[str, set[str]]:
    """Return (title, outgoing link targets) for a page, cached by mtime."""
    _, title, targets, _ = _page_meta_entry(md_file, slug)
    return title, targets


def _deck_lists() -> dict[str, dict]:
    """Every separate list some page declares with [[deck list=<name>]]:
    {name: {"slug": the page, "title": its title, "params": its embed's
    params}}. When several pages declare one, the first slug A–Z is its home."""
    out: dict[str, dict] = {}
    for md_file, slug in sorted(iter_pages(), key=lambda p: p[1]):
        _, title, _, embeds = _page_meta_entry(md_file, slug)
        for name, params in _embeds_lists(embeds):
            out.setdefault(name, {"slug": slug, "title": title, "params": params})
    return out


def _deck_home() -> dict | None:
    """The deck's home: the page with a [[deck … home=1]] embed, the user's
    own deck, as {"slug", "title", "params": that embed's params}; the first
    slug A–Z when several declare one. None: the Deck link goes to /deck."""
    for md_file, slug in sorted(iter_pages(), key=lambda p: p[1]):
        _, title, _, embeds = _page_meta_entry(md_file, slug)
        for params in embeds:
            if deck_module.is_true(params.get("home")):
                return {"slug": slug, "title": title, "params": params}
    return None


def deck_home() -> dict | None:
    """The deck's home (_deck_home), looked up once per request."""
    if not has_app_context():
        return _deck_home()
    if "deck_home" not in g:
        g.deck_home = _deck_home()
    return g.deck_home


def deck_home_slug() -> str | None:
    """The deck home's slug, looked up once per request."""
    home = deck_home()
    return home["slug"] if home else None


deck_module.LISTS_FN = _deck_lists
deck_module.HOME_FN = _deck_home
deck_module.ANCHOR_FN = section_anchor


def all_backlink_counts() -> dict[str, int]:
    """Return a mapping of slug → number of pages that link to it."""
    counts: dict[str, int] = {}
    for md_file, file_slug in iter_pages():
        _, targets = _page_meta(md_file, file_slug)
        for target in targets:
            if target != file_slug:
                counts[target] = counts.get(target, 0) + 1
    return counts


def find_backlinks(slug: str) -> list[dict]:
    """Return pages that contain a wikilink or markdown link resolving to slug."""
    results = []
    for md_file, file_slug in iter_pages():
        if file_slug == slug:
            continue
        title, targets = _page_meta(md_file, file_slug)
        if slug in targets:
            results.append({"slug": file_slug, "title": title})
    results.sort(key=lambda r: r["title"].lower())
    return results


def find_task_backlinks(slug: str) -> list[dict]:
    """Return deck tasks whose title or description contain a wikilink to slug."""
    return deck_module.find_task_backlinks(slug, to_slug)


# A markdown link (not an image): prefix, label, href up to any #anchor/?query
# or title, and the remainder inside the parentheses.
_MD_LINK_REWRITE_RE = re.compile(r'(?<!!)(\[[^\]]*\]\()([^)#?\s]+)([^)]*\))')


def rewrite_page_links(text: str, old_slug: str, new_slug: str, source_slug: str = "",
                       section: str = "") -> str:
    """Point every link to old_slug in text at new_slug.

    With a `section` (a heading's text), links without an #anchor of their
    own point at that heading of new_slug: a scratch note appended to a page
    is that section now.

    Handles [[wikilinks]] (with or without |label) and markdown links in the
    forms backlinks recognise — relative (`old`, `../dir/old`, `old.md`) and
    absolute (`/page/old`) — keeping each link's style, anchor and title.
    Relative hrefs are recomputed from source_slug's directory. Code blocks
    and inline code are left untouched."""
    text, stash = _mask_code(text)

    def wikilink(m: re.Match) -> str:
        target, sep, label = m.group(1).partition("|")
        page_name, hash_, sect = target.partition("#")
        if page_name.strip() and (page_name.strip() == old_slug or to_slug(page_name) == old_slug):
            if not hash_ and section:
                return f"[[{new_slug}#{section}{sep}{label}]]"
            return f"[[{new_slug}{hash_}{sect}{sep}{label}]]"
        return m.group(0)

    source_dir = posixpath.dirname(source_slug)
    anchor = f"#{section_anchor(section)}" if section else ""

    def md_link(m: re.Match) -> str:
        prefix, href, rest = m.groups()
        if not rest.startswith("#"):
            rest = anchor + rest
        if href.startswith("/page/"):
            if href[len("/page/"):] == old_slug:
                return f"{prefix}/page/{new_slug}{rest}"
            return m.group(0)
        is_md = href.endswith(".md")
        target = _resolve_md_link(href, source_slug) if is_md else _resolve_bare_link(href, source_slug)
        if target != old_slug:
            return m.group(0)
        new_href = posixpath.relpath(new_slug, source_dir or ".") + (".md" if is_md else "")
        return f"{prefix}{new_href}{rest}"

    text = WIKILINK_RE.sub(wikilink, text)
    text = _MD_LINK_REWRITE_RE.sub(md_link, text)
    return _unmask_code(text, stash)


def update_references(old_slug: str, new_slug: str, section: str = "") -> list[Path]:
    """Rewrite links to old_slug across all pages and deck tasks after a rename
    (to a `section` of new_slug: see rewrite_page_links).

    Returns the files that were modified (pages and deck storage), so the
    rename commit can include them."""
    modified: list[Path] = []
    for md_file, file_slug in iter_pages():
        content = md_file.read_text(encoding="utf-8")
        new_content = rewrite_page_links(content, old_slug, new_slug, file_slug, section)
        if new_content != content:
            md_file.write_text(new_content, encoding="utf-8")
            modified.append(md_file)
    modified += deck_module.rename_page_refs(
        old_slug, new_slug, lambda text: rewrite_page_links(text, old_slug, new_slug, section=section))
    return modified


def _plain_task_title(title: str) -> str:
    """Task title with wikilinks and /page/ links reduced to their labels.

    Used where the whole title is already inside one link (Linked tasks), so
    it must not contain anchors of its own — nested <a> is invalid HTML and
    browsers split it into separate links."""
    title = re.sub(r'\[\[([^\[\]]+)\]\]', lambda m: split_wikilink(m.group(1))[2], title)
    return re.sub(r'\[([^\]]*)\]\((/page/[^)]+)\)', r'\1', title)


def _build_backlinks_html(slug: str, backlinks: list[dict], task_backlinks: list[dict],
                          prefer=()) -> tuple[str, str]:
    """Return (bl_html, bl_chip) for the backlinks sections and the header chip.
    `prefer`: the lists the page declares, for the Linked tasks' links."""
    bl_html = ""
    bl_chip = ""
    if backlinks:
        bl_items = "".join(
            f'<li><a href="/page/{r["slug"]}">{html.escape(r["title"])}</a></li>'
            for r in backlinks
        )
        bl_html = f'<section class="backlinks" id="backlinks"><h3>Backlinks</h3><ul>{bl_items}</ul></section>'
        bl_popup_items = "".join(
            f'<a href="/page/{r["slug"]}">{html.escape(r["title"])}</a>'
            for r in backlinks
        )
        bl_chip = (
            f'<a href="#backlinks" class="bl-count">← {len(backlinks)}</a>'
            f'<div class="bl-tooltip">{bl_popup_items}</div>'
        )
    if task_backlinks:
        # A task linking to a section names it after its title (§ Section,
        # linking to the heading).
        tbl_items = "".join(
            f'<li><a href="{html.escape(_task_href(t["id"], "open", _link_list(t, prefer)))}">'
            f'{html.escape(_plain_task_title(t["title"]))}</a>'
            + (f' <a class="task-bl-section" href="#{html.escape(t["anchor"])}">§ {html.escape(t["section"])}</a>'
               if t.get("section") else "")
            + '</li>'
            for t in task_backlinks
        )
        bl_html += f'<section class="backlinks" id="task-backlinks"><h3>Linked tasks</h3><ul>{tbl_items}</ul></section>'
    return bl_html, bl_chip


def _build_related_html(slug: str, path: Path, backlinks: list[dict]) -> str:
    """The "Related" section: the pages most similar in meaning (semantic
    search only). Pages already linked either way are left out; the point is
    connections you haven't written down."""
    if not SEMANTIC:
        return ""
    SEMANTIC.poke()
    pages = {s: p for p, s in iter_pages()}
    _, outgoing = _page_meta(path, slug)
    exclude = outgoing | {b["slug"] for b in backlinks}
    related = SEMANTIC.related(slug, known=pages, exclude=exclude)
    if not related:
        return ""
    items = "".join(
        f'<li><a href="/page/{html.escape(other)}">'
        f'{html.escape(_page_meta(pages[other], other)[0])}</a></li>'
        for other, _score in related if not hidden_page(other)
    )
    if not items:
        return ""
    return f'<section class="backlinks" id="related"><h3>Related</h3><ul>{items}</ul></section>'


def _build_delete_dialog(slug: str, title: str) -> str:
    """Return delete-confirmation dialog HTML, or empty string for the index page."""
    if is_protected(slug):
        return ""
    return (
        f'<dialog id="page-delete-dialog" class="scratch-modal">'
        f'<p class="scratch-label">Delete page</p>'
        f'<p class="modal-desc">Delete &ldquo;{html.escape(title)}&rdquo;?'
        f'{" It can be restored from history." if GIT_ENABLED else " This cannot be undone."}</p>'
        f'<form method="post" action="/delete/{html.escape(slug)}">'
        f'<div class="scratch-actions">'
        f'<button type="button" class="btn" id="page-delete-cancel">Cancel</button>'
        f'<button type="submit" class="btn btn-danger">Delete</button>'
        f'</div></form></dialog>'
    )


def page_folders() -> list[str]:
    """The folders that hold pages, A–Z (each parent of a page's slug), for
    filing a scratch note. Hidden pages don't name theirs in sharing mode."""
    folders = set()
    for _, slug in iter_pages():
        if hidden_page(slug):
            continue
        parts = slug.split("/")[:-1]
        folders.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    return sorted(folders)


def promoted_slug(folder: str, name: str, date: str = "") -> str:
    """Where a scratch note moved to the wiki goes: `folder/name`, with
    `date_` (YYYY-MM-DD) in front of the name when the date is kept, as in
    `conversations/2026-07-08_alex-sam-debrief`. The name may not add
    folders of its own: a / in it is a hyphen. Mirrored by promotedSlug in
    leaf.js for the dialog's preview."""
    name = to_slug(name.replace("/", "-"))
    if not name:
        return ""
    if date:
        name = f"{date}_{name}"
    return "/".join(p for p in (to_slug(folder), name) if p)


def _wiki_pages_datalist() -> str:
    """Every page but scratch notes, as suggestions for the scratch note's
    dialogs (the page to link from, the page to append to)."""
    options = "".join(
        f'<option value="{html.escape(s)}">{html.escape(t)}</option>'
        for s, t in sorted((s, _page_meta(p, s)[0]) for p, s in iter_pages()
                           if not is_scratch(s) and not hidden_page(s)))
    return f'<datalist id="wiki-pages">{options}</datalist>'


def _page_from_input(raw: str) -> str:
    """The slug a page field names: a slug, `/page/<slug>` (an #anchor
    dropped) or the page's name."""
    raw = raw.strip().removeprefix("/page/").split("#")[0].strip("/")
    path = safe_path(raw) if raw else None
    return raw if path and path.exists() else to_slug(raw)


def page_wikilink(slug: str, label: str) -> str:
    """`[[slug|label]]`, with what would break a wikilink taken out of the
    label. Mirrored by the Move to wiki dialog in leaf.js, which copies it."""
    label = re.sub(r"[\[\]|]", "", label).strip() or slug
    return f"[[{slug}|{label}]]"


def _build_promote_dialog(slug: str, title: str, error: str = "") -> str:
    """The "Move to wiki" dialog of a scratch note: a folder (suggesting the
    existing ones), the name (the note's title, not its timestamped slug),
    whether to keep the date in front of it, a preview of the page's slug,
    and the page to link it from (leaf.js suggests the folder's index, and
    copies the link on Move). Posts to /promote/<slug>."""
    safe_slug = html.escape(slug)
    date = SCRATCH_RE.match(slug).group(1)[:10]
    options = "".join(f'<option value="{html.escape(f)}/">' for f in page_folders())
    error_html = f'<p class="rename-error">{html.escape(error)}</p>' if error else ""
    return (
        f'<dialog id="page-promote-dialog" class="scratch-modal promote-modal" '
        f'data-title="{html.escape(title)}"{" data-open" if error else ""}>'
        f'<p class="scratch-label">Move to wiki</p>'
        f'<form method="post" action="/promote/{safe_slug}">'
        f'<label class="promote-field">Folder'
        f'<input type="text" name="folder" id="promote-folder" list="promote-folders" '
        f'placeholder="(top level)" autocomplete="off" spellcheck="false"></label>'
        f'<datalist id="promote-folders">{options}</datalist>'
        f'<label class="promote-field">Name'
        f'<input type="text" name="name" id="promote-name" value="{html.escape(title)}" '
        f'autocomplete="off" spellcheck="false" required></label>'
        f'<label class="promote-check"><input type="checkbox" name="keep_date" '
        f'id="promote-keep-date" value="{date}"> Keep the date ({date})</label>'
        f'<p class="promote-preview" aria-live="polite">/page/<code id="promote-slug"></code></p>'
        f'<label class="promote-field">Link from'
        f'<input type="text" name="link_from" id="promote-link-from" list="wiki-pages" '
        f'placeholder="(no link)" autocomplete="off" spellcheck="false"></label>'
        f'<p class="modal-desc">Opens that page in the editor, the link in the clipboard '
        f'to paste where it belongs.</p>'
        f'<input type="hidden" name="copied" id="promote-copied" value="">'
        f'{error_html}'
        f'<div class="scratch-actions">'
        f'<button type="submit" class="btn btn-primary">Move</button>'
        f'<button type="button" class="btn" id="page-promote-cancel">Cancel</button>'
        f'</div></form></dialog>')


def _append_heading(slug: str, title: str) -> str:
    """The section heading a scratch note gets on the page it is appended
    to: its title and its day."""
    return f"{title} ({SCRATCH_RE.match(slug).group(1)[:10]})"


def _build_append_dialog(slug: str, title: str, error: str = "") -> str:
    """The "Append to page" dialog of a scratch note: the page (suggesting
    every page but scratch notes) and the heading of the section the note
    becomes there. Posts to /append/<slug>."""
    error_html = f'<p class="rename-error">{html.escape(error)}</p>' if error else ""
    return (
        f'<dialog id="page-append-dialog" class="scratch-modal promote-modal"'
        f'{" data-open" if error else ""}>'
        f'<p class="scratch-label">Append to page</p>'
        f'<p class="modal-desc">The note becomes a section at the end of the page '
        f'and leaves Scratch. Links to it follow.</p>'
        f'<form method="post" action="/append/{html.escape(slug)}">'
        f'<label class="promote-field">Page'
        f'<input type="text" name="page" id="append-page" list="wiki-pages" '
        f'autocomplete="off" spellcheck="false" required></label>'
        f'<label class="promote-field">Section heading'
        f'<input type="text" name="heading" id="append-heading" '
        f'value="{html.escape(_append_heading(slug, title))}" autocomplete="off" required></label>'
        f'{error_html}'
        f'<div class="scratch-actions">'
        f'<button type="submit" class="btn btn-primary">Append</button>'
        f'<button type="button" class="btn" id="page-append-cancel">Cancel</button>'
        f'</div></form></dialog>')


def note_as_section(content: str, heading: str) -> tuple[str, list[str]]:
    """A scratch note's text as a `## heading` section of another page, and
    its frontmatter tags (for that page's). The note's own title goes (the
    heading replaces it), a tldr becomes an italic first line, and its
    headings move down so the highest is a ###: they are subsections now."""
    meta, body = parse_frontmatter(content)
    lines = strip_h1(body).strip("\n").split("\n")
    levels = [len(m.group(1)) for _, line, fenced in _iter_fenced_lines(lines)
              if not fenced and (m := re.match(r"^(#{1,6})[ \t]", line))]
    shift = max(0, 3 - min(levels)) if levels else 0
    if shift:
        for i, line, fenced in list(_iter_fenced_lines(lines)):
            m = None if fenced else re.match(r"^(#{1,6})([ \t])", line)
            if m:
                lines[i] = "#" * min(6, len(m.group(1)) + shift) + line[len(m.group(1)):]
    parts = [f"## {heading}"]
    if meta.get("tldr"):
        parts.append(f"_{meta['tldr']}_")
    text = "\n".join(lines).strip("\n")
    if text:
        parts.append(text)
    return "\n\n".join(parts) + "\n", meta.get("tags", [])


def add_frontmatter_tags(content: str, tags: list[str]) -> str:
    """content with tags added to its frontmatter `tags:` (those it lacks,
    case ignored), adding the line, or the block, when there is none."""
    have = parse_frontmatter(content)[0].get("tags", [])
    new = [t for t in tags if t.lower() not in {h.lower() for h in have}]
    if not new:
        return content
    line = f"tags: {', '.join(have + new)}"
    lines = content.split("\n")
    end = _frontmatter_end(lines)
    if end is None:
        return f"---\n{line}\n---\n{content}"
    for i in range(1, end):
        if lines[i].partition(":")[0].strip().lower() == "tags":
            lines[i] = line
            break
    else:
        lines.insert(1, line)
    return "\n".join(lines)


PAGE_MENU_BTN = ('<button type="button" class="btn page-menu-btn" aria-haspopup="menu" '
                 'aria-expanded="false" aria-controls="page-menu" title="More actions" '
                 'aria-label="More actions">⋯</button>')


def _build_page_menu(slug: str, title: str, rename_error: str = "",
                     promote_error: str = "", append_error: str = "") -> str:
    """The page view's ⋯ menu (Present, History, Move to wiki… and Append to
    page… for a scratch note, Rename…, Delete…) and the dialogs behind them. It looks like the deck's ⋯ menu (style.css).
    The menu is one hidden element that leaf.js
    opens under whichever ⋯ was pressed (header or right column); Present
    shows only on a page with headings. Outside the page grid, since the
    middle column is a size container, which would anchor position: fixed."""
    safe_slug = html.escape(slug)
    items = ['<button type="button" role="menuitem" id="present-btn" hidden>'
             'Present<kbd class="menu-key">p</kbd></button>']
    if GIT_ENABLED:
        items.append(f'<a role="menuitem" href="/history/{safe_slug}">History</a>')
    rename_dialog = ""
    if is_scratch(slug):
        items.append('<button type="button" role="menuitem" data-action="promote">Move to wiki…</button>')
        items.append('<button type="button" role="menuitem" data-action="append">Append to page…</button>')
        rename_dialog += _wiki_pages_datalist()
        rename_dialog += _build_promote_dialog(slug, title, promote_error)
        rename_dialog += _build_append_dialog(slug, title, append_error)
    if not is_protected(slug):
        items.append('<button type="button" role="menuitem" data-action="rename">Rename…</button>')
        items.append('<button type="button" role="menuitem" data-action="delete" class="danger">'
                     'Delete…<kbd class="menu-key">Ctrl+Del</kbd></button>')
        error_html = (f'<p class="rename-error">{html.escape(rename_error)}</p>'
                      if rename_error else "")
        rename_dialog += (
            f'<dialog id="page-rename-dialog" class="scratch-modal"'
            f'{" data-open" if rename_error else ""}>'
            f'<p class="scratch-label">Rename page</p>'
            f'<form method="post" action="/rename/{safe_slug}">'
            f'<input type="hidden" name="from" value="page">'
            f'<input type="text" name="new_name" id="page-rename-input" value="{safe_slug}" '
            f'aria-label="New name for “{html.escape(title)}”" autocomplete="off" spellcheck="false" required>'
            f'{error_html}'
            f'<div class="scratch-actions">'
            f'<button type="submit" class="btn btn-primary">Rename</button>'
            f'<button type="button" class="btn" id="page-rename-cancel">Cancel</button>'
            f'</div></form></dialog>')
    return (f'<div class="page-menu-backdrop" hidden></div>'
            f'<div class="page-menu" id="page-menu" role="menu" aria-label="Page actions" hidden>'
            f'{"".join(items)}</div>{rename_dialog}')


def _build_page_meta(slug: str, path: Path, title: str) -> tuple[str, str]:
    """Return (last_modified_html, commit_btn_html) for the page meta bar.

    If git is disabled or the file has no history yet, both strings are empty.
    When the file has uncommitted changes the commit button is included.
    """
    if not GIT_ENABLED:
        return "", ""
    filepath = slug + ".md"
    # Single-file status check — much cheaper than scanning the whole tree.
    r = _git("git", "status", "--porcelain", "--untracked-files=all", "--", filepath)
    if r.stdout.strip() and path.exists():
        mtime_dt = datetime.fromtimestamp(path.stat().st_mtime)
        last_modified = (
            f'<div class="page-meta">Last changed {html.escape(mtime_dt.strftime("%Y-%m-%d"))} '
            f'({html.escape(relative_time(mtime_dt))}) — uncommitted</div>'
        )
        return last_modified, _commit_form_html(filepath, title, "reload", inline=True)
    entries = git_log(path, max_count=1)
    if entries:
        e = entries[0]
        return f'<div class="page-meta">Last changed {html.escape(e["date"])} ({html.escape(e["rel"])})</div>', ""
    return "", ""


_SVG = ('<svg width="20" height="20" viewBox="0 0 16 16" fill="none" stroke="currentColor" '
        'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">')

_SEARCH_ICON = _SVG + '<circle cx="7" cy="7" r="4.5"/><line x1="10.5" y1="10.5" x2="14" y2="14"/></svg>'

# The action bar (`.action-buttons`), the phone's bottom tab bar: the modules
# plus New (leaf.js: a page in the wiki, a task in the deck, a scratch note in
# scratch) and Search (openSearchModal). Hidden on desktop, which has the
# module switcher in the breadcrumb and the top actions. Styled in style.css.
_MODULE_ICONS = {
    "wiki": _SVG + '<path d="M13 2.5C8 2.6 4.5 5.5 4.5 11.5c6 0 8.5-3.5 8.5-9z"/>'
            '<path d="M3 13.5 9 7.5"/></svg>',
    "deck": _SVG + '<rect x="2.5" y="2.5" width="11" height="11" rx="2"/>'
            '<polyline points="5.25 8.25 7.25 10.25 10.75 6"/></svg>',
    "scratch": _SVG + '<path d="M9.5 1.5H4a1 1 0 0 0-1 1v11a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1V5z"/>'
               '<polyline points="9.5 1.5 9.5 5 13 5"/><line x1="5.5" y1="8.5" x2="10.5" y2="8.5"/>'
               '<line x1="5.5" y1="11" x2="9" y2="11"/></svg>',
}
def top_actions_html() -> str:
    """The desktop's action buttons, at the top right of the page (in line
    with the breadcrumb), in three groups: sharing mode (a mode, set apart by
    a divider), the two "new" actions (a scratch note, a task in the deck's
    home) and search, the one used most, as a labelled pill with its
    shortcut. Name and shortcut are in each tooltip. Phones have the action
    bar at the bottom instead (see the 640px media query)."""
    return (
        '<div class="top-actions">'
        f'<button type="button" class="top-sharing" aria-pressed="false" aria-label="Sharing mode" title="Sharing mode: hide what is around the page (S)">{_SVG}'
        '<path d="M1.5 8s2.4-4.5 6.5-4.5S14.5 8 14.5 8 12.1 12.5 8 12.5 1.5 8 1.5 8z"/><circle cx="8" cy="8" r="2"/></svg></button>'
        '<span class="top-sep" aria-hidden="true"></span>'
        f'<button type="button" class="top-scratch" aria-label="New scratch note" title="New scratch note (c s)">{_SVG}'
        '<path d="M9.5 1.5H4a1 1 0 0 0-1 1v11a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1V5z"/><polyline points="9.5 1.5 9.5 5 13 5"/>'
        '<line x1="8" y1="7.5" x2="8" y2="12"/><line x1="5.75" y1="9.75" x2="10.25" y2="9.75"/></svg></button>'
        # A checkbox with a + at its corner: "new task", not "task done".
        f'<button type="button" class="top-task" aria-label="Add a task to your deck" title="Add a task to your deck (c t)">{_SVG}'
        '<path d="M11.5 8.5v-4a2 2 0 0 0-2-2h-5a2 2 0 0 0-2 2v5a2 2 0 0 0 2 2h4"/>'
        '<polyline points="4.75 7 6.5 8.75 9.25 5.5"/>'
        '<line x1="12.5" y1="10" x2="12.5" y2="15"/><line x1="10" y1="12.5" x2="15" y2="12.5"/></svg></button>'
        f'<button type="button" class="top-search" aria-label="Search" title="Search (Ctrl+P)">{_SEARCH_ICON}'
        '<span class="top-search-label" aria-hidden="true">Search</span>'
        '<kbd class="top-search-key" aria-hidden="true">Ctrl P</kbd></button>'
        '</div>')


def action_bar_html() -> str:
    """The action bar's HTML (see above)."""
    links = []
    for key, label, href in MODULES:
        attrs, counts = _module_link_extras(key)
        links.append(f'<a href="{_module_href(key, href)}" class="action-module module-link" data-module="{key}"'
                     f'{attrs}>{_MODULE_ICONS[key]}<span class="action-label">{label}</span>{counts}</a>')
    return (
        f'<nav class="action-buttons" aria-label="{APP_NAME}">{"".join(links)}'
        f'<button type="button" class="action-new" aria-label="New" title="New">{_SVG}'
        '<line x1="8" y1="3" x2="8" y2="13"/><line x1="3" y1="8" x2="13" y2="8"/></svg>'
        '<span class="action-label">New</span></button>'
        f'<button type="button" class="action-search" aria-label="Search" title="Search (Ctrl+P)">{_SEARCH_ICON}'
        '<span class="action-label">Search</span></button>'
        '</nav>')


# Editor formatting bar: one button per markdown construct, applied to the
# selection by leaf.js (`data-fmt`, see applyFormat). Line icons, not
# letters: a letter's height depends on the font. `None` = a separator.
_FMT_ICON = ('<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" '
             'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">')
_FORMAT_BUTTONS = (
    ("bold", "Bold (Ctrl+B)", _FMT_ICON.replace('1.4', '2') + '<path d="M4.5 2.5h4.25a2.75 2.75 0 0 1 0 5.5H4.5z"/>'
     '<path d="M4.5 8h5a2.75 2.75 0 0 1 0 5.5h-5z"/></svg>'),
    ("italic", "Italic (Ctrl+I)", _FMT_ICON + '<line x1="6.5" y1="2.5" x2="12.5" y2="2.5"/>'
     '<line x1="3.5" y1="13.5" x2="9.5" y2="13.5"/><line x1="9.75" y1="2.5" x2="6.25" y2="13.5"/></svg>'),
    ("strike", "Strikethrough", _FMT_ICON + '<path d="M10.7 2.7H6.3a2.2 2.2 0 0 0-1.6 3.7"/>'
     '<path d="M9.3 8a2.7 2.7 0 0 1 0 5.3H4"/><line x1="2.5" y1="8" x2="13.5" y2="8"/></svg>'),
    ("heading", "Heading (## / ### / none)", _FMT_ICON + '<line x1="4" y1="8" x2="12" y2="8"/>'
     '<line x1="4" y1="2.7" x2="4" y2="13.3"/><line x1="12" y1="2.7" x2="12" y2="13.3"/></svg>'),
    None,
    ("ul", "Bulleted list", _FMT_ICON + '<line x1="6" y1="4" x2="14" y2="4"/><line x1="6" y1="8" x2="14" y2="8"/>'
     '<line x1="6" y1="12" x2="14" y2="12"/><circle cx="2.5" cy="4" r=".6" fill="currentColor"/>'
     '<circle cx="2.5" cy="8" r=".6" fill="currentColor"/><circle cx="2.5" cy="12" r=".6" fill="currentColor"/></svg>'),
    ("ol", "Numbered list", _FMT_ICON + '<line x1="6" y1="4" x2="14" y2="4"/><line x1="6" y1="8" x2="14" y2="8"/>'
     '<line x1="6" y1="12" x2="14" y2="12"/><path d="M1.8 2.8 2.8 2.2v3.3"/><path d="M1.8 10.4c.3-.5 1.7-.6 1.7.4 0 .7-1.7 1.4-1.7 2.2h1.8"/></svg>'),
    ("task", "Checklist (Ctrl+Shift+X ticks)", _FMT_ICON + '<rect x="1.5" y="2.5" width="4" height="4" rx=".8"/>'
     '<path d="m2.4 4.5 1 1 1.4-2"/><rect x="1.5" y="9.5" width="4" height="4" rx=".8"/>'
     '<line x1="8" y1="4.5" x2="14.5" y2="4.5"/><line x1="8" y1="11.5" x2="14.5" y2="11.5"/></svg>'),
    ("quote", "Quote", _FMT_ICON + '<line x1="2.5" y1="3" x2="2.5" y2="13"/><line x1="6" y1="5" x2="14" y2="5"/>'
     '<line x1="6" y1="8" x2="14" y2="8"/><line x1="6" y1="11" x2="11" y2="11"/></svg>'),
    None,
    ("code", "Inline code", _FMT_ICON + '<polyline points="5 4.5 1.5 8 5 11.5"/><polyline points="11 4.5 14.5 8 11 11.5"/></svg>'),
    ("codeblock", "Code block", _FMT_ICON + '<rect x="1.5" y="2" width="13" height="12" rx="1.5"/>'
     '<polyline points="6 6 4 8 6 10"/><polyline points="10 6 12 8 10 10"/></svg>'),
    ("table", "Table", _FMT_ICON + '<rect x="1.5" y="2.5" width="13" height="11" rx="1.2"/><line x1="1.5" y1="6.2" x2="14.5" y2="6.2"/>'
     '<line x1="1.5" y1="9.9" x2="14.5" y2="9.9"/><line x1="8" y1="2.5" x2="8" y2="13.5"/></svg>'),
    None,
    ("link", "Link (Ctrl+K)", _FMT_ICON + '<path d="M6.5 9.5a3 3 0 0 0 4.2 0l2.3-2.3a3 3 0 0 0-4.2-4.2l-.9.9"/>'
     '<path d="M9.5 6.5a3 3 0 0 0-4.2 0L3 8.8A3 3 0 0 0 7.2 13l.9-.9"/></svg>'),
    ("wikilink", "Link to a wiki page", '<span class="fmt-wiki">[[ ]]</span>'),
    ("taskref", "Link to a deck task", _FMT_ICON + '<rect x="1.5" y="2.5" width="13" height="11" rx="2"/>'
     '<path d="m4 8 1.5 1.5L8.5 6.5"/><line x1="10" y1="8" x2="12.5" y2="8"/></svg>'),
    ("maketask", "Make a deck task from the line or selection", _FMT_ICON
     + '<rect x="1.5" y="2.5" width="13" height="11" rx="2"/><line x1="8" y1="5.25" x2="8" y2="10.75"/>'
     '<line x1="5.25" y1="8" x2="10.75" y2="8"/></svg>'),
    ("attach", "Attach a file or image", _FMT_ICON + '<path d="M13.5 7.5 8 13a3.5 3.5 0 0 1-5-5l5.6-5.6a2.3 2.3 0 0 1 3.3 3.3L6.3 11.3a1.2 1.2 0 0 1-1.7-1.7L9.8 4.4"/></svg>'),
)
FORMAT_BAR = (
    '<div class="format-bar" id="format-bar" role="toolbar" aria-label="Formatting">'
    + "".join('<span class="fmt-sep" aria-hidden="true"></span>' if b is None else
              f'<button type="button" class="fmt-btn" data-fmt="{b[0]}" title="{b[1]}" '
              f'aria-label="{b[1]}">{b[2]}</button>'
              for b in _FORMAT_BUTTONS)
    + '<input type="file" id="fmt-attach-input" multiple hidden>'
    '</div>')


def shell(title: str, body: str, *, slug: str = "", editor: bool = False,
          extra_head: str | None = None, action_buttons: bool = True,
          wide: bool = False) -> str:
    """Wrap body in the page skeleton. `action_buttons=False` drops the
    action buttons, new note and search (the editor has its own toolbar; the
    login page has nothing to search or write). `wide`: a page view with
    side columns on big screens (`main.wide`, see view_page)."""
    edit_btn = f'<a href="/edit/{slug}" class="btn">Edit</a>' if slug and not editor else ""
    body_class = ' class="editor-page"' if editor else ""
    module = "" if editor else current_module()
    body_attrs = body_class + (f' data-module="{module}"' if module else "")
    # New tasks from any page (c t, the New task button) get the tags the
    # deck's home gives them, and are keep tasks if it makes them (leaf.js).
    home = deck_home()
    if home:
        body_attrs += f' data-deck-tags="{html.escape(",".join(deck_module.new_task_tags(home["params"])))}"'
        if deck_module.is_true(home["params"].get("keep")):
            body_attrs += ' data-deck-keep="1"'
        # Its "+ New" on Focus (its view, or the default one) starts the
        # task today; elsewhere a new task has no start date.
        if deck_module.embed_view(home["params"]) in ("", "focus"):
            body_attrs += ' data-deck-today="1"'
    show_actions = action_buttons and not editor
    safe_title = html.escape(title)
    extra_head_html = extra_head if extra_head else ""
    return f"""<!DOCTYPE html>
<html lang="en"{' class="sharing"' if sharing_on() else ""}>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, interactive-widget=resizes-content">
  <meta name="generator" content="{APP_NAME} {VERSION}">
  <title>{safe_title} — {APP_NAME}</title>
  <link rel="icon" href="/public/favicon.svg" type="image/svg+xml">
  <link rel="manifest" href="/public/manifest.json">
  <link rel="apple-touch-icon" href="/public/apple-touch-icon.png">
  <meta name="apple-mobile-web-app-title" content="Leaf">
  <link rel="stylesheet" href="/public/vendor/highlight/github.min.css" media="(prefers-color-scheme: light)">
  <link rel="stylesheet" href="/public/vendor/highlight/github-dark.min.css" media="(prefers-color-scheme: dark)">
  <link rel="stylesheet" href="/public/style.css">
  <script src="/public/vendor/highlight/highlight.min.js" defer></script>
  <script src="/public/leaf.js" defer></script>
  {extra_head_html}
</head>
<body{body_attrs}>
  <dialog id="search-modal" class="search-modal">
    <div class="search-modal-controls">
      <input type="text" id="modal-filter" placeholder="Search pages…" autocomplete="off" spellcheck="false">
      <select id="modal-sort">
        <option value="mtime-desc">Recently edited</option>
        <option value="title-asc">Title A–Z</option>
        <option value="title-desc">Title Z–A</option>
        <option value="links-desc">Most linked</option>
      </select>
      <label class="unlinked-label">
        <input type="checkbox" id="modal-unlinked"> Unlinked only
      </label>
    </div>
    <div class="modal-results">
      <ul id="modal-page-list" class="page-list"></ul>
      <div id="modal-content-matches" class="content-matches hidden"></div>
    </div>
    <div id="modal-preview" class="search-preview" aria-live="polite"></div>
  </dialog>
  <dialog id="scratch-modal" class="scratch-modal">
    <p class="scratch-label">New scratch note</p>
    <form id="scratch-form">
      <input type="text" id="scratch-title" placeholder="Title…" autocomplete="off" spellcheck="false">
      <div class="scratch-actions">
        <button type="submit" class="btn btn-primary">Create &amp; Edit</button>
        <button type="button" class="btn" id="scratch-cancel">Cancel</button>
      </div>
    </form>
  </dialog>
  <dialog id="new-page-modal" class="scratch-modal">
    <p class="scratch-label">New page</p>
    <form method="post" action="/new" id="new-page-form">
      <input type="text" id="new-page-input" name="slug" placeholder="Page name…" autocomplete="off" spellcheck="false" required>
      <div class="scratch-actions">
        <button type="submit" class="btn btn-primary">Create &amp; Edit</button>
        <button type="button" class="btn" id="new-page-cancel">Cancel</button>
      </div>
    </form>
  </dialog>
  <dialog id="shortcuts-modal" class="shortcuts-modal">
    <p class="scratch-label">Keyboard shortcuts</p>
    <table class="shortcuts-table">
      <tr><td><kbd>Ctrl+P</kbd></td><td>Open search</td></tr>
      <tr><td><kbd>Ctrl+N</kbd></td><td>New page</td></tr>
      <tr><td><kbd>c s</kbd></td><td>New scratch note</td></tr>
      <tr><td><kbd>c t</kbd></td><td>New task in your deck</td></tr>
      <tr><td><kbd>g h</kbd></td><td>Go to Home</td></tr>
      <tr><td><kbd>g s</kbd></td><td>Go to Scratch index</td></tr>
      <tr><td><kbd>g d</kbd></td><td>Go to Deck (every task)</td></tr>
      <tr><td><kbd>g t</kbd></td><td>Go to your deck</td></tr>

      <tr><td><kbd>F2</kbd></td><td>Toggle preview (editor)</td></tr>
      <tr><td><kbd>Ctrl+Delete</kbd></td><td>Delete current page</td></tr>
      <tr><td><kbd>Ctrl+S</kbd></td><td>Done / go to page view (editor)</td></tr>
      <tr><td><kbd>Ctrl+B</kbd> / <kbd>Ctrl+I</kbd></td><td>Bold / italic (editor)</td></tr>
      <tr><td><kbd>Ctrl+K</kbd></td><td>Link (editor)</td></tr>
      <tr><td><kbd>Ctrl+Shift+X</kbd></td><td>Toggle checkbox (editor)</td></tr>
      <tr><td><kbd>S</kbd></td><td>Sharing mode: hide what is around the page</td></tr>
      <tr><td><kbd>e</kbd></td><td>Edit current page</td></tr>
      <tr><td><kbd>]</kbd> / <kbd>[</kbd></td><td>Next / previous section</td></tr>
      <tr><td><kbd>p</kbd></td><td>Enter presentation mode</td></tr>
      <tr><td><kbd>Ctrl+/</kbd></td><td>Show this help</td></tr>
      <tr><td><kbd>?</kbd></td><td>Show this help (alt)</td></tr>
      <tr><td><kbd>n</kbd> / <kbd>N</kbd></td><td>Next / previous search highlight (page opened from search)</td></tr>
      <tr><td><kbd>v</kbd></td><td>One line per task / full cards (deck)</td></tr>
      <tr><td><kbd>Shift+F10</kbd> / menu key</td><td>Actions of the selected task (deck; right-click too)</td></tr>
      <tr><td><kbd>Esc</kbd></td><td>Clear search highlights</td></tr>
    </table>
    <p class="scratch-label">Snippets <span class="shortcuts-hint">(type trigger, press Tab)</span></p>
    <table class="shortcuts-table">
      <tr><td><kbd>/date</kbd></td><td>Insert current date</td></tr>
      <tr><td><kbd>/time</kbd></td><td>Insert current time</td></tr>
      <tr><td><kbd>/datetime</kbd></td><td>Insert current date and time</td></tr>
    </table>
    <p class="scratch-label">Search syntax</p>
    <table class="shortcuts-table">
      <tr><td><kbd>"exact phrase"</kbd></td><td>Words in this order</td></tr>
      <tr><td><kbd>-word</kbd></td><td>Leave out results with it (also <kbd>-"phrase"</kbd>)</td></tr>
      <tr><td><kbd>#tag</kbd></td><td>Only pages and tasks with this tag</td></tr>
      <tr><td><kbd>-#tag</kbd></td><td>None with this tag</td></tr>
      <tr><td><kbd>edited:2026-09</kbd></td><td>Last edited then (year, month or day); alone: all of it, newest first</td></tr>
      <tr><td><kbd>after:</kbd> / <kbd>before:</kbd></td><td>Edited on or after / before a day, month or year</td></tr>
    </table>
    <p class="leaf-version">{APP_NAME} {VERSION}{f" · {html.escape(LEAF_BUILD)}" if LEAF_BUILD != "unknown" else ""}</p>
  </dialog>
  <dialog id="url-name-modal" class="scratch-modal">
    <p class="scratch-label">Paste link</p>
    <code id="url-name-href" class="url-name-href"></code>
    <input type="text" id="url-name-input" placeholder="Display name (optional)" autocomplete="off" spellcheck="false">
    <div class="scratch-actions">
      <button type="button" class="btn btn-primary" id="url-name-confirm">Insert</button>
      <button type="button" class="btn" id="url-name-cancel">Plain URL</button>
    </div>
  </dialog>
  <dialog id="upload-conflict-modal" class="scratch-modal">
    <p class="scratch-label">File already exists</p>
    <p id="upload-conflict-msg" class="upload-conflict-msg"></p>
    <input type="text" id="upload-conflict-name" autocomplete="off" spellcheck="false">
    <div class="scratch-actions">
      <button type="button" class="btn btn-primary" id="upload-conflict-confirm">Upload</button>
      <button type="button" class="btn" id="upload-conflict-cancel">Cancel</button>
    </div>
  </dialog>
  <main{' class="wide"' if wide else ""}>
  {'<div class="remote-badge" title="Remote access enabled">🌐</div>' if ALLOW_REMOTE else ''}
  <div class="top-badges">
    <a id="uncommitted-badge" class="uncommitted-badge" href="/uncommitted" style="display: none;" title="Uncommitted changes">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="5.5"/><line x1="8" y1="5.5" x2="8" y2="8"/><line x1="8" y1="8" x2="10" y2="10"/></svg>
        <span id="uncommitted-count">0</span>
      </a>
      {'<form method="post" action="/logout" class="signout-form"><button class="btn" type="submit">Sign out</button></form>' if LEAF_PASSWORD else ''}
    </div>
    {top_actions_html() if show_actions else ""}{body}
  </main>
  {action_bar_html() if show_actions else ""}
</body>
</html>"""


def _is_local_path(url: str) -> bool:
    """True if url is a same-origin path. "//host" and "/\\host" start with "/"
    but browsers resolve them to another origin, so reject those too."""
    return url.startswith("/") and not url.startswith(("//", "/\\"))


# ── Auth routes ───────────────────────────────────────────────────────────────


@app.get("/login")
@app.post("/login")
def login():
    if not LEAF_PASSWORD:
        return redirect("/")
    error = ""
    if request.method == "POST":
        pw = request.form.get("password", "")
        if hmac.compare_digest(pw, LEAF_PASSWORD):
            session.permanent = True
            session["authenticated"] = True
            next_url = request.args.get("next", "/")
            if not _is_local_path(next_url):
                next_url = "/"
            return redirect(next_url)
        error = "Incorrect password."
    error_html = f'<p class="login-error">{html.escape(error)}</p>' if error else ""
    body = f"""<div class="login-wrap">
  <form class="login-form" method="post" action="/login{f'?next={html.escape(request.args.get("next", ""))}' if request.args.get("next") else ''}">
    <h2>Sign in to {html.escape(APP_NAME)}</h2>
    <input type="password" name="password" placeholder="Password" autofocus autocomplete="current-password">
    {error_html}
    <button type="submit" class="btn btn-primary">Sign in</button>
  </form>
</div>"""
    return shell("Sign in", body, action_buttons=False)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── Page routes ───────────────────────────────────────────────────────────────


@app.get("/")
def index():
    return redirect("/page/index")


def _index_page(slug: str, title: str, body_inner: str) -> str:
    """Shared shell for the auto-generated index pages (_scratch, _deleted)."""
    body = f"""{build_breadcrumb(slug, title)}
<header class="page-header"><h1>{title}</h1></header>
<article class="page-content">{body_inner}</article>"""
    return shell(title, body)


def _notes_list_html(entries: list[dict], empty_msg: str) -> str:
    if not entries:
        return f"<p>{empty_msg}</p>"
    items = "".join(
        f'<li data-slug="{html.escape(n["slug"])}"><a href="/page/{n["slug"]}">{html.escape(n["title"])}</a>'
        f'<span class="note-date">{n["date"]}</span></li>'
        for n in entries
    )
    return f"<ul class='notes-list'>{items}</ul>"


@app.get("/page/_scratch")
def scratch_page():
    # The filter runs the search (/api/search?scratch=1: its ranking and
    # query syntax) over scratch notes only; leaf.js (initScratchFilter)
    # shows the matches in ranked order with their excerpts. ?q= keeps the
    # query (Back returns to the filtered list).
    notes = list_scratch()
    q = request.args.get("q", "")
    filt = (f'<div class="scratch-filter"><input type="search" id="scratch-filter" '
            f'value="{html.escape(q)}" placeholder="Filter scratch notes…" '
            f'title="Search syntax works: &quot;phrase&quot;, -word, #tag, edited:2026-09 …" '
            f'aria-label="Filter scratch notes" autocomplete="off" spellcheck="false">'
            f'<span class="scratch-filter-count" id="scratch-filter-count" aria-live="polite"></span></div>'
            if notes else "")
    return _index_page('_scratch', 'Scratch', filt + _notes_list_html(notes, 'No entries yet.'))


@app.get("/page/_deleted")
def deleted_pages_route():
    if not GIT_ENABLED:
        abort(404)
    # Sharing mode: a deleted page's tags are gone with it; its folder still counts.
    pages = [p for p in list_deleted_pages() if not hidden_page(p["slug"])]
    if pages:
        items = "".join(
            f'<li>'
            f'<span class="deleted-page-info">'
            f'<a href="/revision/{html.escape(p["slug"])}/{html.escape(p["sha"])}" class="deleted-page-title">{html.escape(p["title"])}</a>'
            f'<span class="note-date">{html.escape(p["slug"])} &middot; {html.escape(p["date"])}</span></span>'
            f'<form method="post" action="/restore/{html.escape(p["slug"])}/{html.escape(p["sha"])}">'
            f'<button type="submit" class="btn btn-sm">Restore</button>'
            f'</form></li>'
            for p in pages
        )
        body_inner = f"<ul class='notes-list'>{items}</ul>"
    else:
        body_inner = "<p>No deleted pages in history.</p>"
    return _index_page('_deleted', 'Deleted Pages', body_inner)


@app.get("/api/page-preview/<path:slug>")
def api_page_preview(slug):
    """A page rendered for the search modal's preview pane: {title, html}.
    Heading ids become data-pid (the page underneath has its own ids), and
    deck embeds are left out (an app has no place in a preview)."""
    path = safe_path(slug)
    if path is None or not path.exists() or hidden_page(slug):
        abort(404)
    content = path.read_text(encoding="utf-8")
    masked, stash = _mask_code(content)
    masked = deck_module._DECK_RE.sub("*(deck)*", masked)
    html_out = render_md(strip_h1(_unmask_code(masked, stash)))
    html_out = re.sub(r'(<h[1-6]\b[^>]*?) id="', r'\1 data-pid="', html_out)
    return jsonify({"title": page_title(content, slug), "html": html_out})


@app.get("/page/_raw")
def raw_page():
    path = safe_path("_raw")
    if path is None:
        abort(400)
    if not path.exists():
        path.write_text("# Raw\n\n", encoding="utf-8")
        git_commit(path, "Create _raw")
    content = path.read_text(encoding="utf-8")
    title = page_title(content, "_raw")
    raw_dir = PAGES_DIR / "_raw"
    subpages = []
    if raw_dir.is_dir():
        for f in sorted(raw_dir.glob("*.md"), reverse=True):
            sc = f.read_text(encoding="utf-8")
            sub_slug = f"_raw/{f.stem}"
            subpages.append({"slug": sub_slug, "title": page_title(sc, sub_slug)})
    if subpages:
        items = "".join(
            f'<li><a href="/page/{p["slug"]}">{html.escape(p["title"])}</a></li>'
            for p in subpages
        )
        sub_html = f'<section class="backlinks"><h3>Snippets</h3><ul>{items}</ul></section>'
    else:
        sub_html = ''
    article_html = render_md(strip_h1(content))
    body = f"""{build_breadcrumb('_raw', title)}
<header class="page-header">
  <h1>{html.escape(title)}</h1>
  <div class="page-header-actions">
    <a href="/edit/_raw" class="btn">Edit</a>
  </div>
</header>
<article class="page-content">{article_html}</article>
{sub_html}"""
    return shell(title, body)


def _append_to_raw(line: str, message: str) -> None:
    """Add a line to the _raw inbox page (created when missing), committed."""
    path = safe_path("_raw")
    content = path.read_text(encoding="utf-8") if path.exists() else "# Raw\n\n"
    if not content.endswith("\n"):
        content += "\n"
    path.write_text(content + line + "\n", encoding="utf-8")
    git_commit(path, message)


@app.post("/api/raw/append-url")
def api_raw_append_url():
    url = request.form.get("url", "").strip()
    title = request.form.get("title", "").strip() or url
    if not url:
        abort(400)
    _append_to_raw(f"- [{title}]({url})", f"Add URL to _raw: {title[:60]}")
    return jsonify({"ok": True})


@app.post("/api/raw/create-page")
def api_raw_create_page():
    text = request.form.get("text", "").strip()
    title = request.form.get("title", "").strip()
    if not text:
        abort(400)
    uid = uuid.uuid4().hex
    if not title:
        title = text.split("\n")[0].strip()[:80] or uid
    slug = f"_raw/{uid}"
    path = safe_path(slug)
    if path is None:
        abort(400)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {title}\n\n{text}\n", encoding="utf-8")
    git_commit(path, f"Create raw snippet {uid[:8]}: {title[:60]}")
    _append_to_raw(f"- [{title}](/page/{slug})", f"Add snippet link to _raw: {title[:60]}")
    return jsonify({"slug": slug, "title": title})


@app.post("/api/scratch")
def api_scratch():
    title = request.form.get("title", "").strip() or "Untitled"
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    slug = f"{ts}_{to_slug(title)}"
    path = safe_path(slug)
    if path is None:
        abort(400)
    path.write_text(f"# {title}\n\n", encoding="utf-8")
    git_commit(path, f"Create {slug}")
    return jsonify({"slug": slug})


@app.get("/page/<path:slug>")
def view_page(slug):
    # Strip accidental .md suffix
    if slug.endswith('.md'):
        slug = slug[:-3]
    path = safe_path(slug)
    if path is None:
        abort(400)
    if hidden_page(slug):
        return hidden_page_view()

    if not path.exists():
        # Show a restore prompt if the page was deleted from git history
        if GIT_ENABLED:
            rel = slug + ".md"
            r = _git("git", "log", "-1", "--format=%H\t%P\t%as", "--diff-filter=D", "--", rel)
            if r.returncode == 0 and r.stdout.strip():
                parts = r.stdout.strip().split('\t', 2)
                parent_sha = parts[1].split()[0] if len(parts) > 1 and parts[1].strip() else None
                del_date = parts[2] if len(parts) > 2 else ''
                if parent_sha:
                    r2 = _git("git", "show", f"{parent_sha}:{rel}")
                    title = page_title(r2.stdout, slug) if r2.returncode == 0 else slug_display_name(slug)
                    body = f"""{build_breadcrumb(slug, title)}
<header class="page-header"><h1>{html.escape(title)}</h1></header>
<article class="page-content">
<p>This page was deleted on {html.escape(del_date)}.</p>
<form method="post" action="/restore/{html.escape(slug)}/{html.escape(parent_sha)}">
  <button type="submit" class="btn">Restore page</button>
</form>
</article>"""
                    return shell(title, body)
        return redirect(f"/edit/{slug}")

    content = path.read_text(encoding="utf-8")
    title = page_title(content, slug)
    backlinks = [b for b in find_backlinks(slug) if not hidden_page(b["slug"])]
    # Tasks the page shows inline already aren't repeated under Linked tasks.
    inline = page_task_refs(content)
    task_backlinks = [t for t in find_task_backlinks(slug) if t["id"] not in inline]
    # A section a task links to is named as the page's heading says it.
    heading_names = {section_anchor(h): _plain_heading(h)
                     for h in re.findall(r"^#{2,3}\s+(.+?)\s*#*\s*$", _mask_code(content)[0], re.M)}
    for t in task_backlinks:
        if t.get("anchor") in heading_names:
            t["section"] = heading_names[t["anchor"]]
    page_lists = [name for name, _ in _page_deck_lists(content)]
    bl_html, bl_chip = _build_backlinks_html(slug, backlinks, task_backlinks, page_lists)
    bl_html += _build_related_html(slug, path, backlinks)
    delete_dialog = _build_delete_dialog(slug, title)
    _qs = ('?' + request.query_string.decode()) if request.query_string else ''

    from_uncommitted = request.args.get('from') == 'uncommitted' and GIT_ENABLED
    if from_uncommitted:
        # Viewing a page from the uncommitted-changes list: show commit button
        breadcrumb = build_breadcrumb('uncommitted', 'Uncommitted Changes', (title, None), right_html=bl_chip)
        actions = (
            f'{_commit_form_html(slug + ".md", title, "/uncommitted")}\n'
            f'    <a href="/uncommitted" class="btn">← Uncommitted</a>'
        )
        page_meta_html = ""
        article_html = render_md(strip_h1(content))
        rail_actions = page_menu = ""
    else:
        breadcrumb = build_breadcrumb(slug, title, right_html=bl_chip, query_string=_qs)
        # Edit, and the rarer actions in a ⋯ menu (_build_page_menu).
        actions = f'<a href="/edit/{slug}" class="btn">Edit</a>\n    {PAGE_MENU_BTN}'
        page_menu = _build_page_menu(slug, title, request.args.get("rename_error", ""),
                                     request.args.get("promote_error", ""),
                                     request.args.get("append_error", ""))
        # The same, at the top of the right column on big screens, shown
        # once the header has scrolled away (leaf.js).
        rail_actions = (
            f'<div class="rail-actions" hidden>'
            f'<a href="/edit/{slug}" class="btn">Edit</a>{PAGE_MENU_BTN}</div>')
        last_modified, meta_commit_btn = _build_page_meta(slug, path, title)
        fm_meta, _ = parse_frontmatter(content)
        tags_html = _render_tags_pills_html(fm_meta.get('tags', []))
        # The tldr toggle is a checkbox rendered *before* the meta row with its
        # label inside it, and the text as a sibling *after* it. Keeping the
        # three as siblings (rather than a <details> wrapper) is what lets the
        # label stay put on the tags line while the expanded text takes its own
        # full-width line below — CSS-only, no overlay, no JS.
        tldr_text = fm_meta.get('tldr', '')
        if tldr_text:
            tldr_checkbox = '<input type="checkbox" id="tldr-toggle" class="tldr-toggle">'
            tldr_label = '<label class="tldr-label" for="tldr-toggle">tldr</label>'
            tldr_body = f'<p class="page-tldr">{html.escape(tldr_text)}</p>'
        else:
            tldr_checkbox = tldr_label = tldr_body = ''
        page_meta_html = (
            f'{tldr_checkbox}'
            f'<div class="page-meta-wrapper">'
            f'<div class="page-meta-left">{tags_html}{tldr_label}</div>'
            f'<div class="page-meta-right">{last_modified}{meta_commit_btn}</div>'
            f'</div>'
            f'{tldr_body}'
            if last_modified or meta_commit_btn or tags_html or tldr_text else ""
        )
        article_html = _inject_section_tasks(_inject_section_edit_buttons(
            render_md(strip_h1(content), show_tags=False, show_tldr=False), slug
        ), task_backlinks, page_lists)

    body = f"""{breadcrumb}{delete_dialog}
<div class="page-grid">
{_rail_toc_html(article_html)}<div class="page-col">
<header class="page-header">
  <h1>{html.escape(title)}</h1>
  <div class="page-header-actions">
    {actions}
  </div>
</header>
{page_meta_html}
<article class="page-content">
  {article_html}
</article>
<div class="page-foot">{bl_html}</div>
</div>
<aside class="rail rail-context" aria-label="Links">{rail_actions}{_rail_copy(bl_html)}{_outlinks_html(slug, path)}</aside>
</div>{page_menu}"""
    return shell(title, body, wide=True)


# Big screens (style.css, "Side columns") show a page view in three columns:
# the table of contents on the left, the page in the middle and what links
# to it on the right, both sides sticky. The columns are copies of what
# smaller screens show inline: the [[toc]] box and the sections at the end.
_TOC_NAV_RE = re.compile(r'<nav class="toc">(<ul>.*?</ul>)</nav>', re.S)


_HEADING_RE = re.compile(r'<h([23]) id="([^"]+)">(.*?)</h\1>', re.S)
RAIL_TOC_MIN_HEADINGS = 3


def _rail_toc_html(article_html: str) -> str:
    """The left column: "On this page", the page's [[toc]], or without one
    its ## and ### headings when there are at least RAIL_TOC_MIN_HEADINGS
    (a long page shouldn't need the directive for this). Empty otherwise;
    the column stays, so the page keeps its place."""
    m = _TOC_NAV_RE.search(article_html)
    if m:
        items = m.group(1)
    else:
        heads = _HEADING_RE.findall(article_html)
        if len(heads) < RAIL_TOC_MIN_HEADINGS:
            return f'<aside class="rail rail-left">{_RAIL_TOP}</aside>\n'
        # A heading's own links are flattened: no link inside a link.
        items = "<ul>\n" + "\n".join(
            f'<li{" class=\"toc-h3\"" if level == "3" else ""}><a href="#{hid}">'
            f'{re.sub(r"</?a\b[^>]*>", "", label).strip()}</a></li>'
            for level, hid, label in heads) + "\n</ul>"
    return (f'<aside class="rail rail-left"><nav class="rail-toc" aria-label="On this page">'
            f'<p class="rail-label">On this page</p>{items}</nav>{_RAIL_TOP}</aside>\n')


# "Back to top" under the TOC; leaf.js shows it once the page has scrolled
# a screen down, and scrolls up without leaving a #hash in the URL.
_RAIL_TOP = '<a class="rail-top" href="#" hidden>↑ Back to top</a>'


def _outlinks_html(slug: str, path: Path) -> str:
    """The right column's "Links to": the existing pages this page links to,
    A–Z by title, so a page nothing links to still shows its connections."""
    _, targets = _page_meta(path, slug)
    pages = []
    for target in targets - {slug}:
        if hidden_page(target):
            continue
        target_path = safe_path(target)
        if target_path is not None and target_path.exists():
            pages.append((_page_meta(target_path, target)[0], target))
    if not pages:
        return ""
    items = "".join(f'<li><a href="/page/{html.escape(t)}">{html.escape(title)}</a></li>'
                    for title, t in sorted(pages, key=lambda p: p[0].lower()))
    return f'<section class="backlinks" data-id="outlinks"><h3>Links to</h3><ul>{items}</ul></section>'


def _rail_copy(sections_html: str) -> str:
    """The right column's copy of Backlinks, Linked tasks and Related, its
    ids moved to data-id: the anchors (#backlinks) belong to the originals."""
    return sections_html.replace('<section class="backlinks" id="', '<section class="backlinks" data-id="')


@app.get("/edit/<path:slug>")
def edit_page(slug):
    path = safe_path(slug)
    if path is None:
        abort(400)
    if hidden_page(slug):
        return hidden_page_view()
    if path.exists():
        content = path.read_text(encoding="utf-8")
        title = page_title(content, slug)
        is_new = False
    else:
        title = slug_display_name(slug)
        content = f"# {title}\n\n"
        is_new = True

    section = request.args.get('section', type=int)
    file_hash_val = ""
    start_char = 0
    end_char = 0
    if section is not None and not is_new:
        char_range = section_char_range(content, section)
        if char_range is not None:
            file_hash_val = content_hash(content)
            start_char, end_char = char_range
            content = content[start_char:end_char].rstrip('\n')
        else:
            section = None  # invalid index, fall back to full edit

    safe_content = html.escape(content)
    safe_slug = html.escape(slug)
    safe_title = html.escape(title)
    can_rename = not is_protected(slug)
    can_delete = not is_protected(slug) and not is_new
    rename_btn = '<button type="button" class="btn" id="rename-toggle">Rename…</button>' if can_rename and not is_new and section is None else ""
    delete_btn = f'<button type="submit" form="delete-form" class="btn btn-danger" id="delete-btn" data-title="{safe_title}" data-git="{"1" if GIT_ENABLED else ""}">Delete</button>' if can_delete and section is None else ""
    # Phones: Rename/Delete fold into a ⋯ menu so Preview and Done keep the
    # toolbar; on wider screens .editor-more is display:contents (inline).
    more_btn = ('<button type="button" class="btn mobile-only" id="editor-more-btn" aria-haspopup="true" '
                'aria-expanded="false" aria-controls="editor-more" title="More actions">⋯</button>'
                if rename_btn or delete_btn else "")
    delete_form = f'<form id="delete-form" method="post" action="/delete/{safe_slug}" style="display:none"></form>' if can_delete and section is None else ""
    rename_error = request.args.get("rename_error", "")
    rename_error_html = f'<span class="rename-error">{html.escape(rename_error)}</span>' if rename_error else ""
    rename_bar = f"""<div class="rename-bar{' hidden' if not rename_error else ''}" id="rename-bar">
    <form method="post" action="/rename/{safe_slug}" class="rename-form">
      <label>New name</label>
      <input type="text" name="new_name" id="rename-input" value="{safe_slug}" autocomplete="off">
      <button type="submit" class="btn btn-primary">Rename</button>
      <button type="button" class="btn" id="rename-cancel">Cancel</button>
      {rename_error_html}
    </form>
  </div>""" if can_rename and not is_new and section is None else ""
    paste_hint = _paste_hint_html(request.args.get("paste", ""),
                                  request.args.get("copied") == "1")
    form_action = f'/edit/{safe_slug}'
    # Always embed file_hash for conflict detection (full-file and section edits).
    # For a full-file edit, `content` still holds the full file text.
    if not is_new:
        full_file_hash = content_hash(content) if section is None else file_hash_val
    else:
        full_file_hash = ""
    section_hidden_fields = f'<input type="hidden" name="file_hash" id="section-file-hash" value="{full_file_hash}">'
    if section is not None:
        section_hidden_fields += (
            f'<input type="hidden" name="start_char" id="section-start-char" value="{start_char}">'
            f'<input type="hidden" name="end_char" id="section-end-char" value="{end_char}">'
            f'<input type="hidden" name="section_idx" id="section-idx" value="{section}">'
        )
    breadcrumb_extra = (f'§ {section}', None) if section is not None else ('Edit', None)
    body = f"""<div class="editor-wrap">
  <div class="editor-pane">
    {build_breadcrumb(slug, title, breadcrumb_extra)}
    <div class="editor-toolbar">
      <span class="slug-label" title="{safe_slug}">{safe_slug}</span>
      <span id="save-dot" class="save-dot">●</span>
      <button type="button" class="btn mobile-only" id="mobile-preview-btn">Preview</button>
      <button type="submit" form="editor-form" class="btn btn-primary">Done</button>
      {more_btn}
      <span class="editor-more" id="editor-more">{rename_btn}{delete_btn}</span>
    </div>
    {delete_form}
    {rename_bar}
    {paste_hint}
    {FORMAT_BAR}
    <form id="editor-form" method="post" action="{form_action}">
      {section_hidden_fields}
      <textarea id="editor-textarea" name="content" data-new="{'1' if is_new else ''}"
                spellcheck="false" autocorrect="off" autocapitalize="off">{safe_content}</textarea>
    </form>
  </div>
  <div class="preview-pane">
    <p class="pane-label">Preview</p>
    <div id="editor-preview" class="page-content"></div>
  </div>
</div>"""
    # A hidden deck app: Make task (leaf.js) opens its edit modal, and uses
    # the deck's quick-add parser (deck-core.js) for several lines at once.
    body += deck_module._build_deck_embed({}, headless=True)
    return shell(f"Edit — {title}", body, editor=True)


def _paste_hint_html(slug: str, copied: bool) -> str:
    """The editor's hint after Move to wiki (`?paste=<slug>`): the moved
    page's link, copied to the clipboard (`copied`) or to copy, for pasting
    where it belongs on this page. Copy and × in leaf.js."""
    path = safe_path(slug) if slug else None
    if not path or not path.exists() or hidden_page(slug):
        return ""
    title = page_title(path.read_text(encoding="utf-8"), slug)
    link = html.escape(page_wikilink(slug, title))
    if copied:
        said, copy = "<strong>Link copied.</strong> Paste it where it belongs on this page.", "Copy"
    else:
        said, copy = "<strong>Moved to the wiki.</strong> Copy its link and paste it where it belongs.", "Copy link"
    return (f'<div class="paste-hint" role="status" data-link="{link}">'
            f'<svg class="paste-hint-icon" viewBox="0 0 16 16" aria-hidden="true">'
            f'<rect x="3.5" y="2.5" width="9" height="12" rx="1.5"/>'
            f'<path d="M6 2.5h4v2H6z"/><path d="M6 8.5l1.5 1.5L10.5 7"/></svg>'
            f'<div class="paste-hint-text"><p>{said}</p>'
            f'<code title="{link}">{link}</code></div>'
            f'<button type="button" class="btn paste-hint-copy">{copy}</button>'
            f'<button type="button" class="paste-hint-close" aria-label="Close" title="Close">×</button></div>')


def _splice_section(full: str | None, file_hash: str, start_char: int,
                    end_char: int, new_content: str) -> tuple[str, str, int, int] | None:
    """Replace full[start_char:end_char], guarded by a hash check.

    full is the file's current content (None if the file doesn't exist).
    Returns (merged_full_content, new_file_hash, new_start_char, new_end_char)
    or None if the file doesn't exist or the hash doesn't match.
    """
    if full is None:
        return None
    if content_hash(full) != file_hash:
        return None
    if not (0 <= start_char <= end_char <= len(full)):
        return None
    # Preserve the trailing whitespace pattern from the original range
    orig_slice = full[start_char:end_char]
    orig_trailing = orig_slice[len(orig_slice.rstrip('\n')):]
    replacement = new_content.rstrip('\n') + orig_trailing
    merged = full[:start_char] + replacement + full[end_char:]
    new_end = start_char + len(replacement)
    return merged, content_hash(merged), start_char, new_end


def _posted_content() -> str:
    """The editor's posted text, with the browser's CRLF line ends as LF."""
    return request.form.get("content", "").replace('\r\n', '\n').replace('\r', '\n')


@app.post("/edit/<path:slug>")
def save_page(slug):
    path = safe_path(slug)
    if path is None:
        abort(400)
    content = _posted_content()
    file_hash = request.form.get("file_hash", "")
    start_char = request.form.get("start_char", type=int)
    end_char = request.form.get("end_char", type=int)
    current = path.read_text(encoding="utf-8") if path.exists() else None
    if file_hash and start_char is not None and end_char is not None:
        result = _splice_section(current, file_hash, start_char, end_char, content)
        if result is None:
            return redirect(f"/edit/{slug}?conflict=1")
        content = result[0]
    elif file_hash and current is not None:
        # Full-file edit with hash — check for conflict
        if content_hash(current) != file_hash:
            return redirect(f"/edit/{slug}?conflict=1")
    # Skip write if content is unchanged, but still commit if file is dirty
    if current != content:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    git_commit(path, f"Update {slug}")
    return redirect(f"/page/{slug}")


@app.post("/api/save/<path:slug>")
def api_save(slug):
    path = safe_path(slug)
    if path is None:
        abort(400)
    raw_content = _posted_content()
    file_hash = request.form.get("file_hash", "")
    start_char = request.form.get("start_char", type=int)
    end_char = request.form.get("end_char", type=int)
    force_section = request.form.get("force_section", type=int)
    is_section_edit = bool(file_hash and start_char is not None and end_char is not None)
    is_force_section = force_section is not None and force_section >= 1
    current = path.read_text(encoding="utf-8") if path.exists() else None
    if is_force_section:
        # Force-splice: find section by index in the current file, splice content in
        if current is None:
            abort(404)
        rng = section_char_range(current, force_section)
        if rng is None:
            abort(400)
        fs_start, fs_end = rng
        # Same splice as a normal section save, just without the hash guard
        # (keeps the section's trailing blank lines so the next heading isn't
        # glued onto the last line of the posted content).
        content, new_hash, new_start, new_end = _splice_section(
            current, content_hash(current), fs_start, fs_end, raw_content)
    elif is_section_edit:
        result = _splice_section(current, file_hash, start_char, end_char, raw_content)
        if result is None:
            return jsonify({"error": "file_changed"}), 409
        content, new_hash, new_start, new_end = result
    else:
        # Full-file edit — check hash if provided (conflict detection)
        if file_hash and current is not None and content_hash(current) != file_hash:
            return jsonify({"error": "file_changed", "file_hash": content_hash(current)}), 409
        content = raw_content
    has_section_response = is_section_edit or is_force_section
    # Skip write if content is unchanged
    if current != content:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    if has_section_response:
        return jsonify({"file_hash": new_hash, "start_char": new_start, "end_char": new_end})
    if file_hash:
        return jsonify({"file_hash": content_hash(content)})
    return "", 204


@app.get("/new")
def new_page():
    body = """<h1>New Page</h1>
<form class="new-page-form" method="post" action="/new">
  <label for="page-name">Page name</label>
  <input type="text" id="page-name" name="slug"
         placeholder="e.g. My New Page" autofocus required>
  <button type="submit" class="btn btn-primary">Create</button>
</form>"""
    return shell("New Page", body)


@app.post("/new")
def create_page():
    raw = request.form.get("slug", "")
    slug = to_slug(raw)
    if not slug:
        abort(400)
    path = safe_path(slug)
    if path is None:
        abort(400)
    if not path.exists():
        title = raw.strip().title()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {title}\n\n", encoding="utf-8")
        git_commit(path, f"Create {slug}")
    return redirect(f"/edit/{slug}")


@app.post("/rename/<path:slug>")
def rename_page(slug):
    if is_protected(slug):
        abort(400)
    old_path = safe_path(slug)
    if old_path is None or not old_path.exists():
        abort(404)
    raw = request.form.get("new_name", "").strip()
    # Back to where the rename was asked for: the editor, or the page view's
    # ⋯ menu (which reopens its dialog with the error).
    back = f"/page/{slug}" if request.form.get("from") == "page" else f"/edit/{slug}"
    # to_slug preserves underscores, so scratch timestamps survive a rename
    new_slug = to_slug(raw)
    if not new_slug or new_slug == slug:
        return redirect(back)
    new_path = safe_path(new_slug)
    if new_path is None:
        abort(400)
    if new_path.exists():
        return redirect(f"{back}?rename_error=A+page+named+%22{new_slug}%22+already+exists")
    _move_page(old_path, new_path, slug, new_slug, f"Rename {slug} to {new_slug}")
    return redirect(f"/page/{new_slug}")


def _move_page(old_path: Path, new_path: Path, slug: str, new_slug: str, message: str) -> None:
    """Move a page's file, point every link to it at the new slug, and
    commit the move with the pages and deck files whose links changed."""
    new_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.rename(new_path)
    git_commit([old_path, new_path, *update_references(slug, new_slug)], message)


@app.post("/promote/<path:slug>")
def promote_page(slug):
    """Move a scratch note into the wiki: a rename to a real name, chosen in
    the "Move to wiki" dialog (folder, name, maybe the date), links to it
    following as for any rename. Its own commit message, so the history
    says what happened."""
    if not is_scratch(slug):
        abort(400)
    old_path = safe_path(slug)
    if old_path is None or not old_path.exists():
        abort(404)
    date = request.form.get("keep_date", "")
    date = SCRATCH_RE.match(slug).group(1)[:10] if date else ""
    new_slug = promoted_slug(request.form.get("folder", ""), request.form.get("name", ""), date)
    back = f"/page/{slug}?promote_error="
    if not new_slug:
        return redirect(back + quote("Give the page a name"))
    if is_scratch(new_slug):
        return redirect(back + quote("That name would still be a scratch note"))
    new_path = safe_path(new_slug)
    if new_path is None:
        abort(400)
    if new_path.exists():
        return redirect(back + quote(f"A page named “{new_slug}” already exists"))
    raw_link = request.form.get("link_from", "").strip()
    link_slug = _page_from_input(raw_link) if raw_link else ""
    link_path = safe_path(link_slug) if link_slug else None
    if raw_link and (not link_path or not link_path.exists() or link_slug == slug):
        return redirect(back + quote(f"There is no page “{raw_link}” to link from"))
    _move_page(old_path, new_path, slug, new_slug, f"Promote {slug} to {new_slug}")
    # The page to link it from opens in the editor, the link to paste where
    # it belongs (leaf.js copied it on Move). A page linking to the note
    # already links here now: nothing to paste.
    if link_path and new_slug not in _page_link_targets(
            link_path.read_text(encoding="utf-8"), link_slug):
        copied = "&copied=1" if request.form.get("copied") == "1" else ""
        return redirect(f"/edit/{link_slug}?paste={quote(new_slug)}{copied}")
    return redirect(f"/page/{new_slug}")


@app.post("/append/<path:slug>")
def append_page(slug):
    """Append a scratch note to a page as a section (note_as_section, its
    tags into the page's frontmatter), and delete the note. Links to the
    note point at that section, all in one commit."""
    if not is_scratch(slug):
        abort(400)
    old_path = safe_path(slug)
    if old_path is None or not old_path.exists():
        abort(404)
    back = f"/page/{slug}?append_error="
    raw = request.form.get("page", "").strip()
    target = _page_from_input(raw)
    # [ ] | would break the wikilinks that now point at the section.
    heading = re.sub(r"[\[\]|]", "", request.form.get("heading", "")).strip()
    target_path = safe_path(target) if target else None
    if not target_path or not target_path.exists():
        return redirect(back + quote(f"There is no page “{raw}”" if raw else "Choose a page"))
    if target == slug:
        return redirect(back + quote("Choose another page than this note"))
    if not heading:
        return redirect(back + quote("Give the section a heading"))
    page = target_path.read_text(encoding="utf-8")
    anchor = section_anchor(heading)
    if any(section_anchor(h) == anchor for h in re.findall(
            r"^#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", _mask_code(page)[0], re.M)):
        return redirect(back + quote(f"“{target}” already has a section “{heading}”"))
    section, tags = note_as_section(old_path.read_text(encoding="utf-8"), heading)
    page = add_frontmatter_tags(page, tags)
    target_path.write_text(page.rstrip("\n") + "\n\n" + section, encoding="utf-8")
    old_path.unlink()
    modified_refs = update_references(slug, target, heading)
    git_commit([old_path, target_path, *modified_refs], f"Append {slug} to {target}")
    return redirect(f"/page/{target}#{anchor}")


@app.post("/delete/<path:slug>")
def delete_page(slug):
    if is_protected(slug):
        abort(400)
    path = safe_path(slug)
    if path is None or not path.exists():
        abort(404)
    path.unlink()
    git_commit(path, f"Delete {slug}")
    next_url = request.form.get("next", "")
    if (not _is_local_path(next_url)
            or f"/page/{slug}" in next_url
            or f"/edit/{slug}" in next_url):
        next_url = "/page/index"
    return redirect(next_url)


# ── History routes ────────────────────────────────────────────────────────────


def _version_label(entry: dict, entries: list[dict]) -> str:
    """What a commit did to the page, in words: Leaf's own commit messages
    ("Update p", "Rename a to b", "Restore p to 1a2b3c4", …) read alike in a
    list, so they are named by kind. Others are shown as written."""
    msg = entry["msg"]
    if re.fullmatch(r"(Update|Save) \S+", msg):
        return "Edited"
    if re.fullmatch(r"Create \S+", msg):
        return "Created"
    if re.fullmatch(r"Delete \S+", msg):
        return "Deleted"
    if msg == "Initial import":
        return "Imported"
    m = re.fullmatch(r"Rename (\S+) to (\S+)", msg)
    if m:
        if entry["path"] == f"{m.group(2)}.md":
            return f"Renamed from “{m.group(1)}”"
        return f"Link updated (“{m.group(1)}” renamed)"
    m = re.fullmatch(r"Promote (\S+) to (\S+)", msg)
    if m:
        if entry["path"] == f"{m.group(2)}.md":
            return "Moved from Scratch"
        return "Link updated (a scratch note moved to the wiki)"
    m = re.fullmatch(r"Append (\S+) to (\S+)", msg)
    if m:
        if entry["path"] == f"{m.group(2)}.md":
            return "Scratch note appended"
        if entry["path"] == f"{m.group(1)}.md":
            return f"Appended to “{m.group(2)}”"
        return "Link updated (a scratch note appended to a page)"
    m = re.fullmatch(r"Restore \S+ to ([0-9a-f]{7,40})", msg)
    if m:
        old = next((e for e in entries if e["sha"].startswith(m.group(1))), None)
        return f"Restored the version of {old['time']}" if old else "Restored an older version"
    return msg


def _version_index(entries: list[dict], sha: str) -> int | None:
    return next((i for i, e in enumerate(entries) if e["sha"].startswith(sha)), None)


def _version_path(path: Path, entries: list[dict], sha: str) -> Path:
    """The file's path at a commit: before a rename it had another name."""
    i = _version_index(entries, sha)
    return WIKI_DIR / entries[i]["path"] if i is not None else path


def _is_current(path: Path, entries: list[dict]) -> bool:
    """Whether the newest commit is what the page holds now (no edits on
    disk since, not deleted)."""
    if not entries or not path.exists():
        return False
    return git_show(entries[0]["sha"], _version_path(path, entries, entries[0]["sha"])) == \
        path.read_text(encoding="utf-8")


def _change_counts(e: dict) -> str:
    parts = []
    if e["added"]:
        parts.append(f'<span class="hist-add">+{e["added"]}</span>')
    if e["deleted"]:
        parts.append(f'<span class="hist-del">−{e["deleted"]}</span>')
    return " ".join(parts)


@app.get("/history/<path:slug>")
def history_page_route(slug):
    if hidden_page(slug):
        return hidden_page_view()
    if not GIT_ENABLED:
        abort(404)
    path = safe_path(slug)
    if path is None:
        abort(400)
    page_exists = path.exists()
    if page_exists:
        content = path.read_text(encoding="utf-8")
        title = page_title(content, slug)
    else:
        title = slug_display_name(slug)
    entries = git_log(path)
    if not entries and not page_exists:
        abort(404)

    # One row per saved version, newest first. The row opens that version;
    # the page as it is now is marked Current and opens the page itself.
    # "Changes" shows what that save changed.
    current = _is_current(path, entries)
    rows = []
    if page_exists and not current:
        rows.append(
            f'<li class="hist-row hist-current">'
            f'<a class="hist-main" href="/page/{slug}"><span class="hist-when">Now</span>'
            f'<span class="hist-what">Not committed yet</span></a>'
            f'<span class="hist-badge">Current</span></li>')
    for i, e in enumerate(entries):
        is_cur = current and i == 0
        href = f"/page/{slug}" if is_cur else f"/revision/{slug}/{e['sha']}"
        rows.append(
            f'<li class="hist-row{" hist-current" if is_cur else ""}">'
            f'<a class="hist-main" href="{href}">'
            f'<span class="hist-when">{e["time"]}<span class="hist-rel">{html.escape(e["rel"])}</span></span>'
            f'<span class="hist-what">{html.escape(_version_label(e, entries))}</span>'
            f'<span class="hist-counts">{_change_counts(e)}</span></a>'
            + ('<span class="hist-badge">Current</span>' if is_cur else "")
            + f'<a class="hist-changes" href="/diff/{slug}/{e["sha"]}" title="What this save changed">Changes</a>'
            f'</li>')
    if entries:
        table = (f'<p class="hist-intro">Every saved version of this page, newest first. '
                 f'Open one to read it, step through older ones or bring it back.</p>'
                 f'<ol class="hist-list">{"".join(rows)}</ol>')
    else:
        table = '<p class="hist-empty">No history yet — history is recorded from this point forward.</p>'

    if page_exists:
        back_link = f'<a href="/page/{slug}" class="btn">← Page</a>'
        breadcrumb = build_breadcrumb(slug, title, ('History', None))
    else:
        back_link = f'<a href="/page/_deleted" class="btn">← Deleted pages</a>'
        breadcrumb = build_breadcrumb('_deleted', 'Deleted Pages', (title, None))
    body = f"""{breadcrumb}
<header class="page-header">
  <h1>History — {html.escape(title)}</h1>
  {back_link}
</header>
{table}"""
    return shell(f"History — {title}", body)


def _version_nav(slug: str, entries: list[dict], i: int, current: bool, kind: str) -> str:
    """← Older · Newer → between versions (kind: "revision" or "diff");
    the newest, when it is the page as it is now, is the page itself."""
    older = (f'<a href="/{kind}/{slug}/{entries[i + 1]["sha"]}" class="btn">← Older</a>'
             if i + 1 < len(entries) else '<span class="btn" aria-disabled="true">← Older</span>')
    if i == 0:
        newer = '<span class="btn" aria-disabled="true">Newer →</span>'
    elif i == 1 and current and kind == "revision":
        newer = f'<a href="/page/{slug}" class="btn">Current →</a>'
    else:
        newer = f'<a href="/{kind}/{slug}/{entries[i - 1]["sha"]}" class="btn">Newer →</a>'
    return f'<nav class="version-nav" aria-label="Versions">{older}{newer}</nav>'


@app.get("/revision/<path:slug>/<sha>")
def revision_view(slug, sha):
    if hidden_page(slug):
        return hidden_page_view()
    if not GIT_ENABLED:
        abort(404)
    if not SHA_RE.match(sha):
        abort(400)
    path = safe_path(slug)
    if path is None:
        abort(400)
    entries = git_log(path)
    i = _version_index(entries, sha)
    content = git_show(sha, _version_path(path, entries, sha))
    restore_sha = sha
    if content is None:
        # Deletion commit — file absent at this SHA; use parent
        r_par = _git("git", "rev-parse", "--verify", f"{sha}^")
        if r_par.returncode == 0:
            restore_sha = r_par.stdout.strip()
            content = git_show(restore_sha, _version_path(path, entries, sha))
        if content is None:
            abort(404)
    title = page_title(content, slug)
    page_exists = path.exists()
    current = _is_current(path, entries)
    when = entries[i]["time"] if i is not None else sha[:7]
    if page_exists:
        back_link = f'<a href="/history/{slug}" class="btn">← History</a>'
        breadcrumb = build_breadcrumb(slug, title, ('History', f'/history/{slug}'), (when, None))
    else:
        back_link = f'<a href="/page/_deleted" class="btn">← Deleted pages</a>'
        breadcrumb = build_breadcrumb('_deleted', 'Deleted Pages', (title, None))
    # Where this version stands: the current one, n versions back, or the last
    # version of a deleted page. Restoring is offered for every other one.
    if i is None:
        where = f"Version <code>{sha[:7]}</code>"
    elif current and i == 0:
        where = f"The current version, saved {when}"
    elif not page_exists and restore_sha != sha:
        where = f"The last version before the page was deleted ({when})"
    else:
        back = i + (0 if current else 1) if page_exists else i
        where = (f"The version of {when}"
                 + (f" · {back} {'version' if back == 1 else 'versions'} back" if page_exists and back else ""))
    can_restore = not (current and i == 0)
    restore = (f'<form method="post" action="/restore/{slug}/{restore_sha}" class="version-restore" '
               f'data-confirm="Make this version the page again? The page as it is now stays in its history.">'
               f'<button type="submit" class="btn">Restore this version</button></form>'
               if can_restore else "")
    changes = (f'<a href="/diff/{slug}/{sha}" class="version-link">What this save changed</a>'
               if i is not None else "")
    nav = _version_nav(slug, entries, i, current, "revision") if i is not None else ""
    body = f"""{breadcrumb}
<header class="page-header">
  <h1>{html.escape(title)}</h1>
  <div class="page-header-actions">{back_link}</div>
</header>
<div class="version-bar">
  <p class="version-where">{where}</p>
  <div class="version-actions">{changes}{restore}</div>
  {nav}
</div>
<article class="page-content">
  {render_md(strip_h1(content))}
</article>"""
    return shell(f"{when} — {title}", body)


@app.get("/diff/<path:slug>/<sha>")
def diff_view(slug, sha):
    if hidden_page(slug):
        return hidden_page_view()
    if not GIT_ENABLED:
        abort(404)
    if not SHA_RE.match(sha):
        abort(400)
    path = safe_path(slug)
    if path is None:
        abort(400)
    entries = git_log(path)
    i = _version_index(entries, sha)
    before = [WIKI_DIR / entries[i + 1]["path"]] if i is not None and i + 1 < len(entries) else []
    diff = git_diff(sha, _version_path(path, entries, sha), *before)
    if diff is None:
        abort(404)
    title = slug_display_name(slug)
    diff_html = _diff_to_html(diff)
    page_exists = path.exists()
    when = entries[i]["time"] if i is not None else sha[:7]
    if page_exists:
        back_link = f'<a href="/history/{slug}" class="btn">← History</a>'
        breadcrumb = build_breadcrumb(slug, title, ('History', f'/history/{slug}'), ('Changes', None))
    else:
        back_link = f'<a href="/page/_deleted" class="btn">← Deleted pages</a>'
        breadcrumb = build_breadcrumb('_deleted', 'Deleted Pages', (title, f'/history/{slug}'), ('Changes', None))
    what = html.escape(_version_label(entries[i], entries)) if i is not None else ""
    nav = _version_nav(slug, entries, i, False, "diff") if i is not None else ""
    view = (f'<a href="/revision/{slug}/{sha}" class="version-link">View this version</a>'
            if i is not None else "")
    body = f"""{breadcrumb}
<header class="page-header">
  <h1>Changes — {html.escape(title)}</h1>
  <div class="page-header-actions">{back_link}</div>
</header>
<div class="version-bar">
  <p class="version-where">What the save of {when} changed{f" ({what[:1].lower() + what[1:]})" if what else ""}: <span class="hist-del">red</span> was taken out, <span class="hist-add">green</span> put in.</p>
  <div class="version-actions">{view}</div>
  {nav}
</div>
<pre class="diff-view">{diff_html}</pre>"""
    return shell(f"Changes {when} — {title}", body)


@app.post("/restore/<path:slug>/<sha>")
def restore_revision(slug, sha):
    if not GIT_ENABLED:
        abort(404)
    if not SHA_RE.match(sha):
        abort(400)
    path = safe_path(slug)
    if path is None:
        abort(400)
    content = git_show(sha, _version_path(path, git_log(path), sha))
    if content is None:
        abort(404)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    git_commit(path, f"Restore {slug} to {sha[:7]}")
    return redirect(f"/page/{slug}")


def fmt_date(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


@app.get("/pages")
def pages_list():
    return redirect("/")


@app.get("/api/pages-list")
def api_pages_list():
    counts = all_backlink_counts()
    pages = []
    for md_file, slug in iter_pages():
        if hidden_page(slug):
            continue
        title, _ = _page_meta(md_file, slug)
        stat = md_file.stat()
        mtime = stat.st_mtime
        pages.append({
            "slug": slug,
            "title": title,
            "links": counts.get(slug, 0),
            "mtime": int(mtime),
            "created": fmt_date(getattr(stat, "st_birthtime", stat.st_ctime)),
            "edited": fmt_date(mtime),
        })
    pages.sort(key=lambda p: p["title"].lower())
    # Built-in pages (`builtin`). Deck and Scratch are modules, reached from
    # the module switcher, so the empty-query list leaves them out; typing
    # still finds them. `special` pins Deleted
    # Pages to the top of the search modal's empty-query list, the one way
    # there without typing. `href` is for entries that are not /page/<slug>
    # (the deck is at /deck).
    builtin = [
        {"slug": "/deck", "title": "Deck", "href": "/deck", "links": 0},
        {"slug": "_scratch", "title": "Scratch", "links": counts.get("_scratch", 0)},
    ]
    special = [{"slug": "_deleted", "title": "Deleted Pages", "links": 0}] if GIT_ENABLED else []
    return jsonify([{**p, "mtime": 0, "builtin": True, "special": True} for p in special]
                   + pages + [{**p, "mtime": 0, "builtin": True} for p in builtin])


# ── Search ────────────────────────────────────────────────────────────────────


# Cache of tokenized page documents, keyed by slug -> (mtime, doc, deck key). Avoids
# re-reading and re-tokenizing every file on each search; invalidated per-file
# when the on-disk mtime changes so edits made outside the app stay reflected.
_search_doc_cache: dict[str, tuple[float, dict, tuple]] = {}


def _task_refs_as_titles(content: str) -> str:
    """Page text with each [[task:…]] replaced by its task's title: what
    the reader sees there, and what search and embeddings should match."""
    return deck_module.expand_task_refs(
        content, lambda ref, task: _plain_task_title(task.get("title", "")) if task else "")


def _per_page(cache: dict, build, key=None, fresh=lambda value, key: True) -> list:
    """build(slug, content, mtime) for every page, cached per page until its
    file changes (by mtime, so edits made outside the app count), or until
    fresh(value, key) says the value is stale. Entries of pages that are
    gone are dropped. One bad file must not fail every search: a page
    deleted meanwhile is skipped, one that isn't UTF-8 is read with
    stand-ins for the undecodable bytes."""
    out, seen = [], set()
    for md_file, slug in iter_pages():
        try:
            mtime = md_file.stat().st_mtime
        except OSError:
            continue
        cached = cache.get(slug)
        if not (cached and cached[0] == mtime and fresh(cached[1], cached[2])):
            try:
                content = md_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            cached = cache[slug] = (mtime, build(slug, content, mtime), key)
        seen.add(slug)
        out.append(cached[1])
    for stale in [s for s in cache if s not in seen]:
        del cache[stale]
    return out


def _page_search_doc(slug: str, content: str, mtime: float) -> dict:
    """A page as a BM25 document: its text, its title (TITLE_WEIGHT extra
    times) and the words of its slug once, so a page is found by its file
    and folder names too ("robin" finds .../2026-06-18_update-alex-robin)
    without those outranking what the page says."""
    title = page_title(content, slug)
    text = search.strip_link_targets(_task_refs_as_titles(content))
    return {
        "slug": slug,
        "title": title,
        "mtime": mtime,
        "task_refs": bool(deck_module.TASK_REF_RE.search(content)),
        "tags": _page_tags(content),
        "excerpt_text": text,
        "tokens": search.tokenize(text)
        + search.tokenize(title) * search.TITLE_WEIGHT
        + search.tokenize(slug),
    }


def _page_search_docs() -> list[dict]:
    """Every page as a BM25 document (_page_search_doc). A page with
    [[task:…]] is indexed with the tasks' titles, so it is stale when the
    deck changes too."""
    deck_key = deck_module._deck_mtimes()
    return _per_page(_search_doc_cache, _page_search_doc, key=deck_key,
                     fresh=lambda doc, key: not doc["task_refs"] or key == deck_key)


# The BM25 index over the current docs, rebuilt only when a doc changes.
# Both doc caches hand back the same dict objects until their file changes,
# so the docs' identities are the key (the cached index keeps them alive, so
# an id can't be reused while it is the key).
_search_index_cache: dict = {"key": None, "index": None}


def _cached_index(cache: dict, docs: list[dict]) -> search.SearchIndex:
    """The BM25 index over docs, kept in cache until a doc changes."""
    key = tuple(map(id, docs))
    if cache["key"] != key:
        cache["index"] = search.SearchIndex(docs)
        cache["key"] = key
    return cache["index"]


def _search_index() -> search.SearchIndex:
    return _cached_index(_search_index_cache, _page_search_docs() + deck_module.task_search_docs())


# ── Semantic search (opt-in, see embed.py) ───────────────────────────────────

def _semantic_text(content: str, slug: str) -> tuple[str, str]:
    """A page as (title, body) for embedding: the tldr and tags lead, the
    frontmatter block and the H1 (the title already) are dropped, and so are
    [[deck …]] and [[toc]] directives. Those are markup, not content: pages
    holding nothing but a deck embed came out as each other's closest match."""
    meta, body = parse_frontmatter(_task_refs_as_titles(content))
    lead = "\n".join(filter(None, [meta.get("tldr", ""), " ".join(meta.get("tags", []))]))
    body = _TOC_RE.sub("", deck_module._DECK_RE.sub("", strip_h1(body)))
    return page_title(content, slug), f"{lead}\n\n{body}" if lead else body


# None unless LEAF_EMBEDDINGS is set: then search is hybrid (BM25 fused with
# vector similarity) and pages list their nearest neighbours as Related.
SEMANTIC = embed.from_env(
    WIKI_DIR, lambda: [(s, p) for p, s in iter_pages()], _semantic_text,
    # A task is embedded as its search text: title, notes and #tags.
    tasks_fn=lambda: [(d["task_id"], d["excerpt_text"]) for d in deck_module.task_search_docs()])
if SEMANTIC:
    SEMANTIC.start()

# How deep each ranking goes into the fusion; the fused list is cut to 15.
FUSE_DEPTH = 30


def _by_similarity(*hit_lists: list[tuple[dict, float]]) -> list[dict]:
    """One semantic ranking from several (doc, similarity) lists, e.g. page
    sections and tasks: all come from the same model, so their cosine
    similarities compare directly."""
    return [d for d, _s in sorted((h for hits in hit_lists for h in hits), key=lambda h: -h[1])]


def _page_tags(content: str) -> list[str]:
    """A page's frontmatter tags, lowercased, for #tag queries."""
    return [t.lower() for t in parse_frontmatter(content)[0].get("tags", [])]


def _doc_text(d: dict) -> str:
    """A page, section or task doc's searchable text."""
    return d["excerpt_text"] if "excerpt_text" in d else f"{d['title']}\n{d['heading']}\n{d['text']}"


def _doc_date(d: dict) -> str | None:
    """A doc's day for the date filters: a page's (or section's) last edit,
    a task's last activity."""
    return fmt_date(d["mtime"]) if "mtime" in d else d.get("edited")


def _accepts(query: search.Query, d: dict) -> bool:
    """Whether a page, section or task doc passes the query's filters."""
    return query.accepts(_doc_text(d), d["tokens"], d.get("tags"), _doc_date(d))


def _passes(query: search.Query, d: dict, keep=None) -> bool:
    """Whether a doc takes part in a search: it passes `keep` (when given)
    and the query's filters."""
    return (keep is None or keep(d)) and (not query.has_filters or _accepts(query, d))


def _has_phrase(d: dict, phrase: str) -> bool:
    # The squashed text is cached on the doc, which lives until its file changes.
    if "_squashed" not in d:
        d["_squashed"] = search.squash(_doc_text(d))
    return phrase in d["_squashed"]


def _phrase_first(ranked: list[dict], query: search.Query, index: search.SearchIndex,
                  keep=None) -> list[dict]:
    """ranked with the docs containing the query as a phrase moved to the
    front (in BM25 order), including ones the ranking had cut off: a page
    saying exactly what was typed beats one that has the words apart."""
    phrase = query.phrase
    if not phrase:
        return ranked
    hits = [d for _s, d in index.search(query.ranking_text, limit=len(index.docs))
            if _passes(query, d, keep) and _has_phrase(d, phrase)]
    if not hits:
        return ranked
    first = {id(d) for d in hits}
    return hits + [d for d in ranked if id(d) not in first]


def _bm25(index: search.SearchIndex, query: search.Query, depth: int, keep=None) -> list[dict]:
    """The best `depth` docs by BM25 for the query's ranking text, among those
    that pass its filters (and `keep`, when given)."""
    text = query.ranking_text
    if not query.has_filters and keep is None:
        return [d for _s, d in index.search(text, limit=depth)]
    return [d for _s, d in index.search(text, limit=len(index.docs))
            if _passes(query, d, keep)][:depth]


def _ranked_docs(query: search.Query, index: search.SearchIndex, keep=None,
                 limit: int = 15) -> list[dict]:
    """The `limit` best docs for the query. Keyword ranking only, unless semantic
    search is on and ready: then BM25 and vector similarity are fused
    (search.fuse). Docs that fail the query's filters, or `keep` when
    given, take no part."""
    q = query.ranking_text
    if not q.strip():
        if not query.dates:
            return []
        # Only date filters ("edited:2026-09"): everything that passes,
        # newest first. There are no words to rank by.
        docs = [d for d in index.docs if _passes(query, d, keep)]
        return sorted(docs, key=lambda d: _doc_date(d) or "", reverse=True)[:limit]
    # Each ranking goes as deep as the fused list must be long.
    depth = max(FUSE_DEPTH, limit) if SEMANTIC else limit
    bm25 = _bm25(index, query, depth, keep)
    if not SEMANTIC:
        return _phrase_first(bm25, query, index, keep)[:limit]
    SEMANTIC.poke()
    allowed = [d for d in index.docs if _passes(query, d, keep)]
    pages = {d["slug"]: d for d in allowed if d.get("type") != "task"}
    tasks = {d["task_id"]: d for d in allowed if d.get("type") == "task"}
    hits = SEMANTIC.search(q, known=pages, limit=depth)
    task_hits = SEMANTIC.search_tasks(q, known=tasks, limit=depth)
    if not hits and not task_hits:
        return _phrase_first(bm25, query, index, keep)[:limit]
    semantic = _by_similarity([(pages[slug], sc) for slug, sc, _t in hits],
                              [(tasks[i], sc) for i, sc in task_hits])
    return _phrase_first(search.fuse([bm25, semantic[:depth]]), query, index, keep)[:limit]


def _best_sections(query: search.Query, slugs: set[str]) -> dict[str, dict]:
    """For each page in slugs, the section a result should open: one whose
    own text has the query as a phrase, else the best BM25-scored section
    whose own text has a query word, else, for a page found by meaning, its
    most similar section. A page matched only by its title gets none (every
    section would match equally), so its link opens the top of the page."""
    q = query.ranking_text
    index = _chunk_index()
    ranked = [d for _s, d in index.search(q, limit=len(index.docs))
              if d.get("type") != "task" and d.get("slug") in slugs]
    best: dict[str, dict] = {}
    if query.phrase:
        for d in ranked:
            if d["slug"] not in best and query.phrase in search.squash(f"{d['heading']}\n{d['text']}"):
                best[d["slug"]] = d
    for d in ranked:
        if d["slug"] not in best and search.matches(f"{d['heading']}\n{d['text']}", q):
            best[d["slug"]] = d
    if SEMANTIC and len(best) < len(slugs):
        rest = slugs - set(best)
        by_key = {d["key"]: d for d in index.docs if d.get("type") != "task" and d["slug"] in rest}
        for key, _sc in SEMANTIC.search_chunks(q, known=rest, limit=len(by_key) or 1):
            if key in by_key:
                best.setdefault(by_key[key]["slug"], by_key[key])
    return best


def _result_meta(d: dict) -> str:
    """The small line under a result's title, telling apart results whose
    titles look alike: a page's folder and last edit, a task's list and
    status ("Deck · done 2026-05-01")."""
    if d.get("type") == "task":
        task = d["task"]
        status = _task_status(task)
        if status != "open":
            status += f" {task['completed_at'][:10]}"
        list_name = deck_module.task_list(task)
        where = f"Deck › {list_name}" if list_name else "Deck"
        return f"{where} · {status}"
    folder = d["slug"].rpartition("/")[0]
    edited = f"edited {fmt_date(d['mtime'])}"
    return f"{folder} · {edited}" if folder else edited


SEARCH_LIMIT = 15       # results in the search modal (/api/search default)
SEARCH_PAGE_LIMIT = 50  # results on the /search page ("All results")
SEARCH_MAX_LIMIT = 100
SCRATCH_FILTER_LIMIT = 1000   # the Scratch page's filter lists every match


def do_search(raw: str, unlinked: bool = False, limit: int = SEARCH_LIMIT,
              scratch: bool = False) -> list[dict]:
    """Search results for the browser, at most `limit`. `unlinked` keeps
    only pages no other page links to (the search modal's "Unlinked
    only"), no tasks. `scratch` keeps only scratch notes (the Scratch
    page's filter)."""
    if not raw.strip():
        return []
    query = search.Query(raw)
    q = query.ranking_text  # what excerpts mark: the words, without operators
    tests = []
    if unlinked:
        linked = {slug for slug, n in all_backlink_counts().items() if n > 0}
        tests.append(lambda d: d.get("type") != "task" and d["slug"] not in linked)
    if scratch:
        tests.append(lambda d: d.get("type") != "task" and is_scratch(d["slug"]))
    if sharing_on():
        tests.append(lambda d: d.get("type") == "task" or not is_noshare(d["slug"]))
    keep = (lambda d: all(t(d) for t in tests)) if tests else None
    docs = _ranked_docs(query, _search_index(), keep, limit)
    sections = _best_sections(query, {d["slug"] for d in docs if d.get("type") != "task"})
    results = []
    names = deck_module.list_names()
    for d in docs:
        section = sections.get(d["slug"]) if d.get("type") != "task" else None
        text = f"{section['heading']}\n{section['text']}".strip() if section else d["excerpt_text"]
        r = {
            "slug": d["slug"],
            "title": d["title"],
            "excerpt": search.make_excerpt(text, q),
        }
        for k in ("type", "task_id", "status", "year"):
            if d.get(k) is not None:
                r[k] = d[k]
        if d.get("type") == "task" and (list_name := deck_module.task_list(d["task"], names)):
            r["list"] = list_name
        r["meta"] = _result_meta(d)
        r["href"] = _result_href(r)
        if d.get("type") != "task":
            # The page highlights what matched (leaf.js); before the #anchor.
            words = search.matched_words(d["excerpt_text"], q)
            if words:
                r["href"] += "?" + urlencode([("hl", w) for w in words])
        if section:
            r["href"] += section["anchor"]
        results.append(r)
    return results


# ── Retrieval for LLMs (the MCP server) ───────────────────────────────────────
# Like search, but it returns sections rather than pages, with their full
# text: an LLM needs the passages, and the best ones may share a page. BM25
# runs over sections here, fused with semantic section hits when that is on.

_chunk_doc_cache: dict[str, tuple[float, list[dict], None]] = {}


def _section_anchor(heading: str, level: int) -> str:
    """'#<id>' of a section's heading as the rendered page has it, or "" when
    there is none: no heading, or one deeper than anchors_plugin's max_level
    (3), whose heading carries no id."""
    if not heading or level > 3:
        return ""
    return f"#{_heading_id(_plain_heading(heading))}"


def _page_chunks(slug: str, content: str, mtime: float) -> list[dict]:
    """A page's sections (embed.chunk_sections) as BM25 documents. A
    section's `key` is the one its vector has in the semantic index."""
    title, body = _semantic_text(content, slug)
    tags = _page_tags(content)
    chunks = []
    for heading, text, level in embed.chunk_sections(body) or [("", "", 0)]:
        chunk = f"{heading}\n{text}" if heading else text
        chunks.append({
            "slug": slug, "title": title, "heading": heading, "text": text,
            "anchor": _section_anchor(heading, level), "tags": tags,
            "mtime": mtime, "key": embed.chunk_key(title, chunk),
            "tokens": search.tokenize(chunk) + search.tokenize(title) * search.TITLE_WEIGHT
            + search.tokenize(slug),
        })
    return chunks


def _page_chunk_docs() -> list[dict]:
    """Every page's sections as BM25 documents, cached per page by mtime."""
    return [d for chunks in _per_page(_chunk_doc_cache, _page_chunks) for d in chunks]


_chunk_index_cache: dict = {"key": None, "index": None}


def _chunk_index() -> search.SearchIndex:
    """BM25 over page sections and deck tasks (a task is one result, like a
    section), as the browser search ranks pages and tasks together."""
    return _cached_index(_chunk_index_cache, _page_chunk_docs() + deck_module.task_search_docs())


def _in_folder(slug: str, folder: str | None) -> bool:
    """Whether slug is the page `folder` or below it (case ignored)."""
    if not folder:
        return True
    slug, folder = slug.lower(), folder.lower()
    return slug == folder or slug.startswith(folder + "/")


def _clean_folder(raw) -> str | None:
    """A folder argument as a slug prefix: '/page/tickets/', 'tickets/' and
    'tickets' all become 'tickets'. Empty means the whole wiki."""
    if not isinstance(raw, str):
        raise mcp_server.ToolError("under must be a string")
    raw = raw.strip()
    if "/page/" in raw:
        raw = raw.split("/page/", 1)[1]
    return raw.strip("/") or None


def retrieve(raw: str, limit: int = 8, under: str | None = None) -> list[dict]:
    """The sections and tasks most relevant to the query, best first: section
    dicts (slug, title, heading, text) and task docs (type "task", with the
    task). The query syntax applies (search.Query). With `under`, only
    sections of pages in that folder, and no tasks."""
    query = search.Query(raw)
    q = query.ranking_text
    if not q.strip():
        return []
    index = _chunk_index()
    keep = (lambda d: d.get("type") != "task" and _in_folder(d["slug"], under)) if under else None
    ranked = _bm25(index, query, FUSE_DEPTH, keep)
    if SEMANTIC:
        SEMANTIC.poke()
        sections = [d for d in index.docs if d.get("type") != "task" and _in_folder(d["slug"], under)
                    and _passes(query, d)]
        by_key = {d["key"]: d for d in sections}
        hits = [(by_key[k], sc) for k, sc in SEMANTIC.search_chunks(
            q, known={d["slug"] for d in sections}, limit=FUSE_DEPTH) if k in by_key]
        task_hits = []
        if not under:
            tasks = {d["task_id"]: d for d in index.docs if d.get("type") == "task" and _passes(query, d)}
            task_hits = [(tasks[i], sc) for i, sc in SEMANTIC.search_tasks(q, known=tasks, limit=FUSE_DEPTH)]
        if hits or task_hits:
            ranked = search.fuse([ranked, _by_similarity(hits, task_hits)[:FUSE_DEPTH]])
    return _phrase_first(ranked, query, index, keep)[:limit]


def _mcp_int(args: dict, name: str, default: int, top: int) -> int:
    value = args.get(name, default)
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value)
    if not isinstance(value, int) or isinstance(value, bool):
        raise mcp_server.ToolError(f"{name} must be a whole number")
    return max(1, min(value, top))


def _mcp_page_slug(raw: str) -> str | None:
    """The existing page a slug-ish string names: a slug, '/page/<slug>', a
    full URL, '<slug>.md', or the page's name ('Git Remote')."""
    raw = raw.strip()
    if "/page/" in raw:
        raw = raw.split("/page/", 1)[1]
    raw = raw.split("#", 1)[0].strip("/")
    if raw.endswith(".md"):
        raw = raw[:-3]
    for slug in (raw, to_slug(raw)):
        path = safe_path(slug) if slug else None
        if path is not None and path.is_file():
            return slug
    return None


_RECURRENCE_UNITS = {"daily": "day", "weekly": "week", "monthly": "month", "yearly": "year"}


def _task_status(task: dict) -> str:
    if not task.get("completed_at"):
        return "open"
    return "won't do" if task.get("wont_do") else "done"


def _mcp_task_text(task: dict, base: str) -> str:
    """One deck task for an LLM: title, a line of facts (status, tags, dates,
    repeat, list), its link in the deck, its notes and its comments."""
    status = _task_status(task)
    facts = [f"status: {status}" + (f" {task['completed_at'][:10]}" if status != "open" else "")]
    if task.get("tags"):
        facts.append("tags: " + " ".join(f"#{t}" for t in task["tags"]))
    if task.get("start_date"):
        facts.append(f"start: {task['start_date']}")
    if task.get("due_date"):
        facts.append(f"due: {task['due_date']}")
    rec = task.get("recurrence")
    if isinstance(rec, dict) and rec.get("type") in _RECURRENCE_UNITS:
        n = rec.get("interval") or 1
        every = rec["type"] if n == 1 else f"every {n} {_RECURRENCE_UNITS[rec['type']]}s"
        facts.append(f"repeats {every}" + (" after completion" if rec.get("from") == "completion" else ""))
    if task.get("keep"):
        facts.append("keep task (back to the list when done"
                     + (f", last done {task['last_done']})" if task.get("last_done") else ")"))
    list_name = deck_module.task_list(task)
    if list_name:
        facts.append(f"list: {list_name}")
    url = base + _task_link(task, list_name)
    text = f"Task: {task.get('title', '')}\n{' · '.join(facts)}\nurl: {url}"
    notes = (task.get("description") or "").strip()
    if notes:
        text += f"\n\n{notes}"
    # Comments: the user's status lines, oldest first, each with its time.
    comments = [c for c in task.get("comments") or [] if isinstance(c, dict) and c.get("text")]
    if comments:
        text += "\n\nComments:\n" + "\n".join(
            f"- {str(c.get('at', ''))[:16].replace('T', ' ')}: {c['text']}" for c in comments)
    return text


def _mcp_task_refs(content: str, base: str) -> str:
    """For read_page: what each [[task:…]] on the page stands for, since the
    raw markdown holds only the id."""
    masked, _ = _mask_code(content)
    refs = list(dict.fromkeys(m.group(1).lower() for m in deck_module.TASK_REF_RE.finditer(masked)))
    if not refs:
        return ""
    tasks = deck_module.resolve_task_refs(refs)
    lines = []
    for ref in refs:
        task = tasks[ref]
        if task is None:
            lines.append(f"- [[task:{ref}]]: no such task (deleted)")
            continue
        facts = [_task_status(task)]
        facts += [f"{k} {task[k + '_date']}" for k in ("start", "due") if task.get(k + "_date")]
        url = base + _task_link(task, deck_module.task_list(task))
        lines.append(f"- [[task:{ref}]]: {task.get('title', '')} ({', '.join(facts)}) {url}")
    return "\n\n---\nDeck tasks referenced on this page ([[task:…]]):\n" + "\n".join(lines)


def _mcp_search_tasks(args: dict, base: str) -> str:
    """Deck tasks filtered by tag and status, ranked by the query when there
    is one, else open ones by deadline and start date, done ones newest
    first."""
    query, tag = (args.get(k, "") for k in ("query", "tag"))
    if not all(isinstance(v, str) for v in (query, tag)):
        raise mcp_server.ToolError("query and tag must be strings")
    status = args.get("status", "open")
    if status not in ("open", "done", "all"):
        raise mcp_server.ToolError("status must be open, done or all")
    limit = _mcp_int(args, "limit", 50, 200)
    want = {t.strip().lstrip("#").lower() for t in tag.split(",") if t.strip().lstrip("#")}

    def matches(task: dict) -> bool:
        if status != "all" and (status == "open") == bool(task.get("completed_at")):
            return False
        return want <= {t.lower() for t in task.get("tags") or []}

    parsed = search.Query(query)
    docs = [d for d in deck_module.task_search_docs()
            if matches(d["task"]) and _passes(parsed, d)]
    if parsed.text.strip():
        ranked = [d for _s, d in search.SearchIndex(docs).search(parsed.text, limit=max(len(docs), 1))]
        if SEMANTIC:
            SEMANTIC.poke()
            by_id = {d["task_id"]: d for d in docs}
            hits = [by_id[i] for i, _sc in SEMANTIC.search_tasks(parsed.text, known=by_id, limit=len(by_id) or 1)]
            ranked = search.fuse([ranked, hits])
        docs = ranked
    else:
        open_ = sorted((d for d in docs if not d["task"].get("completed_at")),
                       key=lambda d: (d["task"].get("due_date") or "9999",
                                      d["task"].get("start_date") or "9999",
                                      d["task"].get("title", "").lower()))
        done = sorted((d for d in docs if d["task"].get("completed_at")),
                      key=lambda d: d["task"]["completed_at"], reverse=True)
        docs = open_ + done
    if not docs:
        return f"Today is {date.today()}. No matching tasks."
    shown = docs[:limit]
    count = f"{len(docs)} matching tasks" + (f", the first {limit} shown" if len(docs) > limit else "")
    body = "\n\n---\n\n".join(_mcp_task_text(d["task"], base) for d in shown)
    return f"Today is {date.today()}. {count}:\n\n{body}"


def _mcp_call_tool(name: str, args: dict) -> str:
    base = LEAF_BASE_URL or request.host_url.rstrip("/")
    if name == "search_wiki":
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            raise mcp_server.ToolError("query must be a non-empty string")
        under = _clean_folder(args.get("under", ""))
        if under and not any(_in_folder(slug, under) for _p, slug in iter_pages()):
            raise mcp_server.ToolError(f"No pages under {under!r}. list_pages shows the slugs, "
                                       "whose folders are the part before the last '/'.")
        hits = retrieve(query, _mcp_int(args, "limit", 8, 20), under)
        if not hits:
            return "No matching sections or tasks. Try other words, or list_pages to browse."
        out = []
        for i, d in enumerate(hits, 1):
            if d.get("type") == "task":
                out.append(f"[{i}] " + _mcp_task_text(d["task"], base))
                continue
            anchor = d["anchor"]
            where = f"{d['title']} › {d['heading']}" if d["heading"] else d["title"]
            out.append(f"[{i}] {where}\nslug: {d['slug']}\nedited: {fmt_date(d['mtime'])}\n"
                       f"url: {base}/page/{d['slug']}{anchor}\n\n{d['text']}")
        return "\n\n---\n\n".join(out)
    if name == "search_tasks":
        return _mcp_search_tasks(args, base)
    if name == "read_page":
        raw = args.get("slug")
        if not isinstance(raw, str) or not raw.strip():
            raise mcp_server.ToolError("slug must be a non-empty string")
        slug = _mcp_page_slug(raw)
        if slug is None:
            raise mcp_server.ToolError(f"No page {raw!r}. Use search_wiki or list_pages to find its slug.")
        content = safe_path(slug).read_text(encoding="utf-8", errors="replace")
        return f"slug: {slug}\nurl: {base}/page/{slug}\n\n{content}" + _mcp_task_refs(content, base)
    if name == "list_pages":
        limit = _mcp_int(args, "limit", 100, 1000)
        under = _clean_folder(args.get("under", ""))
        pages = []
        for md_file, slug in iter_pages():
            if not _in_folder(slug, under):
                continue
            try:
                mtime = md_file.stat().st_mtime
            except OSError:
                continue
            pages.append((mtime, slug, _page_meta(md_file, slug)[0]))
        pages.sort(reverse=True)
        lines = [f"{slug} | {title} | edited {fmt_date(mtime)}" for mtime, slug, title in pages[:limit]]
        if not pages:
            return f"No pages under {under!r}." if under else "The wiki has no pages."
        shown = f"{len(lines)} of {len(pages)}" if len(lines) < len(pages) else f"all {len(pages)}"
        where = f" under {under}/" if under else ""
        return f"Pages{where}, most recently edited first ({shown}); slug | title | last edit:\n" + "\n".join(lines)
    raise mcp_server.ToolError(f"Unknown tool: {name}")


def _mcp_authorized() -> bool:
    """With LEAF_MCP_TOKEN set, /mcp needs it as a bearer token. Without
    one it is as open as the wiki's pages are, so a LEAF_PASSWORD-protected
    wiki keeps /mcp closed until a token is set."""
    if LEAF_MCP_TOKEN:
        auth = request.headers.get("Authorization", "")
        return auth.startswith("Bearer ") and hmac.compare_digest(
            auth[len("Bearer "):].strip().encode(), LEAF_MCP_TOKEN.encode())
    return not LEAF_PASSWORD


@app.route("/mcp", methods=["GET", "POST", "DELETE"])
def mcp_endpoint():
    """MCP over HTTP (see mcp_server.py): one JSON-RPC message per POST,
    answered with JSON. GET (a server-sent event stream) and DELETE (ending
    a session) aren't offered: there are no notifications and no sessions."""
    if not _mcp_authorized():
        hint = ("set LEAF_MCP_TOKEN and send it as 'Authorization: Bearer <token>'"
                if not LEAF_MCP_TOKEN else "wrong or missing bearer token")
        return jsonify({"error": f"Unauthorized: {hint}"}), 401, {"WWW-Authenticate": "Bearer"}
    if request.method != "POST":
        return "", 405, {"Allow": "POST"}
    try:
        message = json.loads(request.get_data(as_text=True))
    except ValueError:
        return jsonify({"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": "Parse error"}}), 400
    if isinstance(message, list):  # a batch (protocol 2025-03-26)
        replies = [r for m in message if (r := mcp_server.handle(m, _mcp_call_tool, VERSION))]
        return (jsonify(replies), 200) if replies else ("", 202)
    reply = mcp_server.handle(message, _mcp_call_tool, VERSION)
    return (jsonify(reply), 200) if reply else ("", 202)


@app.get("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    scratch = request.args.get("scratch") == "1"
    limit = request.args.get("limit", SEARCH_LIMIT, type=int)
    limit = max(1, min(limit, SCRATCH_FILTER_LIMIT if scratch else SEARCH_MAX_LIMIT))
    return jsonify(do_search(q, unlinked=request.args.get("unlinked") == "1", limit=limit,
                             scratch=scratch))


# ── The deck page ─────────────────────────────────────────────────────────────
# /deck is a built-in page showing one full main deck (or, with ?list=, that
# separate list). Every link to "the deck" goes here: search results, Linked
# tasks, the due badge, g d / c t. It used to redirect to whichever page
# embedded [[deck]], so where a link landed depended on the wiki's pages, and
# its ?view=/?task= applied to every embed there. [[deck …]] embeds are for
# custom views on ordinary pages; nothing links to them.

@app.get("/deck")
def deck_page():
    list_name = deck_module._clean_list(request.args.get("list"))
    sep = '<span class="breadcrumb-sep">›</span>'
    if list_name:
        # A list looks the same here as on the page that declares it: that
        # embed's params (keep, labels, limit, view, tag) apply.
        home = deck_module.LISTS_FN().get(list_name)
        params = dict(home["params"]) if home else {}
        params["list"] = list_name
        embed = deck_module._build_deck_embed(params)
        title = f"{list_name} · Deck"
        crumbs = [home_link(), '<a href="/deck">Deck</a>', f"<span>{html.escape(list_name)}</span>"]
        heading = html.escape(list_name)
        if home:
            page_link = (f'<a href="/page/{html.escape(home["slug"])}">'
                         f'{html.escape(_page_meta(safe_path(home["slug"]), home["slug"])[0])}</a>')
            note = f'<p class="deck-list-home">A separate list, from {page_link}.</p>'
        else:
            note = (f'<p class="deck-list-home">No page declares <code>[[deck list={heading}]]</code>: '
                    f'these are the tasks tagged #{heading}.</p>')
    else:
        embed = deck_module._build_deck_embed({})
        title = "Deck"
        crumbs = [home_link(), "<span>Deck</span>"]
        heading = "Deck"
        note = ""
    # data-slug keys the navigation trail (leaf.js); no page has this slug.
    key = "/deck" + (f"?list={list_name}" if list_name else "")
    body = f"""<div class="breadcrumb" data-slug="{html.escape(key)}" data-title="{html.escape(title)}">{sep.join(crumbs)}</div>
<header class="page-header"><h1>{heading}</h1></header>
<article class="page-content">{note}{embed}</article>"""
    return shell(title, body)


def _task_href(task_id: str, tab: str, list_name: str | None,
               year: str | None = None) -> str:
    """Link that opens one task in the deck (or its list's page) on `tab`;
    `year` is the Completed year of a task in an earlier year's archive."""
    params = {"task": task_id, "view": tab}
    if list_name:
        params["list"] = list_name
    if year:
        params["year"] = year
    return "/deck?" + urlencode(params)


def _task_link(task: dict, list_name: str | None) -> str:
    """_task_href for a task as it is now: an open one on Open, a done one
    on Completed, in its year's archive when that is an earlier year."""
    if not task.get("completed_at"):
        return _task_href(task["id"], "open", list_name)
    return _task_href(task["id"], "completed", list_name, deck_module._past_year(task))


def _result_href(r: dict) -> str:
    """Destination URL for a search result. Task results open the deck page on
    the tab matching the task's state, with that one task selected and
    highlighted (`?task=<id>`, handled by deck.js) rather than the bare _deck
    slug. By id, not title: titles can repeat or contain #words."""
    if r.get("type") == "task":
        tab = "completed" if r.get("status") == "done" else "open"
        return _task_href(r["task_id"], tab, r.get("list"), r.get("year"))
    return f"/page/{r['slug']}"


@app.get("/search")
def search_page():
    q = request.args.get("q", "").strip()
    unlinked = request.args.get("unlinked") == "1"
    results = do_search(q, unlinked=unlinked, limit=SEARCH_PAGE_LIMIT) if q else []

    if results:
        shown = (f"The first {len(results)} results" if len(results) == SEARCH_PAGE_LIMIT
                 else f"{len(results)} result" + ("" if len(results) == 1 else "s"))
        scope = " · unlinked pages only" if unlinked else ""
        cards = f'<p class="search-count">{shown}{scope}</p>' + "".join(
            f"""<div class="search-result-card">
  <a href="{html.escape(r['href'])}">{html.escape(r['title'])}</a>
  <span class="result-meta">{html.escape(r['meta'])}</span>
  <p>{r['excerpt']}</p>
</div>"""
            for r in results
        )
    else:
        cards = "<p>No results found.</p>"

    body = f"""<div class="search-results">
  <h1>Search: {html.escape(q)}</h1>
  {cards}
</div>"""
    return shell(f"Search: {q}", body)


# ── API ───────────────────────────────────────────────────────────────────────


@app.post("/api/toggle-checkbox/<path:slug>")
def api_toggle_checkbox(slug):
    path = safe_path(slug)
    if path is None or not path.exists():
        abort(404)
    idx = request.form.get("idx", type=int)
    if idx is None or idx < 0:
        abort(400)
    content = path.read_text(encoding="utf-8")
    path.write_text(toggle_nth_checkbox(content, idx), encoding="utf-8")
    return "", 204


@app.post("/api/preview")
def api_preview():
    text = request.get_data(as_text=True)
    # ?lists=a,b: the lists the page declares, so a chip re-rendered on its
    # own (leaf.js rerenderTaskRef) keeps linking to the page's list.
    lists = [n for n in (deck_module._clean_list(x) for x in request.args.get("lists", "").split(",")) if n]
    return render_md(text, prefer_lists=lists)


@app.get("/api/version")
def api_version():
    return jsonify({"version": VERSION, "build": LEAF_BUILD})


@app.get("/api/pages")
def api_pages():
    return jsonify([s for s in all_slugs() if not hidden_page(s)])


@app.post("/api/upload")
def api_upload():
    f = request.files.get("file")
    if not f:
        abort(400)
    original_name = Path(f.filename).name if f.filename else ""
    if not original_name:
        ext = mimetypes.guess_extension(f.mimetype) or ".bin"
        if ext == ".jpeg":
            ext = ".jpg"
        original_name = "upload" + ext
    # unique=1: generate a UUID filename (used for clipboard screenshots)
    if request.form.get("unique") == "1":
        ext = Path(original_name).suffix.lower() or (mimetypes.guess_extension(f.mimetype) or ".bin")
        if ext == ".jpeg":
            ext = ".jpg"
        filename = uuid.uuid4().hex + ext
        f.save(UPLOADS_DIR / filename)
        git_commit(UPLOADS_DIR / filename, f"Upload {filename}")
        return jsonify({"url": f"/_assets/{filename}"})
    # Client may request a specific filename (e.g. after a conflict dialog)
    requested_name = request.form.get("filename", "").strip()
    filename = Path(requested_name).name if requested_name else original_name
    if not filename or filename.startswith("."):
        abort(400)   # "..", ".git" and friends: not an asset name
    overwrite = request.form.get("overwrite") == "1"
    dest = UPLOADS_DIR / filename
    if dest.exists() and not overwrite:
        return jsonify({"conflict": True, "filename": filename}), 409
    f.save(dest)
    git_commit(dest, f"Upload {filename}")
    return jsonify({"url": f"/_assets/{filename}"})


@app.get("/public/<path:filename>")
def serve_public(filename):
    return send_from_directory(PUBLIC_DIR, filename)


# Uploads are shown inline only when the browser treats them as passive media.
# Anything else (HTML, JS, …) would run with Leaf's origin, and so could read
# and rewrite the wiki, so it is served as a download. SVG stays inline for
# <img> tags but gets a sandbox CSP, which disables its scripts when the file
# is opened directly.
_INLINE_ASSET_PREFIXES = ("image/", "audio/", "video/")
_INLINE_ASSET_TYPES = {"application/pdf", "text/plain"}
_SVG_CSP = "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"


@app.get("/_assets/<filename>")
def serve_upload(filename):
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    inline = mime.startswith(_INLINE_ASSET_PREFIXES) or mime in _INLINE_ASSET_TYPES
    resp = send_from_directory(UPLOADS_DIR, filename, as_attachment=not inline)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    if mime == "image/svg+xml":
        resp.headers["Content-Security-Policy"] = _SVG_CSP
    return resp


# ── Uncommitted changes ───────────────────────────────────────────────────────

@app.get("/uncommitted")
def uncommitted_page():
    """Show all files with uncommitted changes."""
    if not GIT_ENABLED:
        abort(404)

    file_list = [f for f in _uncommitted_entries() if not (f["slug"] and hidden_page(f["slug"]))]
    if file_list:
        rows = "".join(
            f'<tr>'
            f'<td class="hist-date">{html.escape(f["mtime"])}</td>'
            f'<td class="hist-rel">{html.escape(f["rel"])}</td>'
            f'<td class="hist-msg">{html.escape(f["title"])}'
            + (f'<br><span class="hist-filepath">{html.escape(f["filepath"])}</span>' if f["title"] != f["filepath"] else '')
            + '</td>'
            f'<td style="white-space:nowrap">'
            + (f'<a href="/diff-uncommitted/{html.escape(f["slug"])}" class="btn">Diff</a> ' if f["slug"] else '')
            + (f'<a href="/page/{html.escape(f["slug"])}?from=uncommitted" class="btn">View</a> ' if f["slug"] else '')
            + '</td>'
            f'</tr>'
            for f in file_list
        )
        table = f'<table class="hist-table"><tbody>{rows}</tbody></table>'
        commit_all_btn = '<button type="button" class="btn btn-primary" id="commit-all-btn">Commit All</button>'
    else:
        table = '<p class="hist-empty">No uncommitted changes</p>'
        commit_all_btn = ''

    body = f"""{build_breadcrumb('uncommitted', 'Uncommitted Changes')}
<header class="page-header">
  <h1>Uncommitted Changes</h1>
  <div class="page-header-actions">{commit_all_btn}</div>
</header>
{table}"""
    return shell("Uncommitted Changes", body)


@app.get("/diff-uncommitted/<path:slug>")
def diff_uncommitted(slug):
    """Show uncommitted changes for a file."""
    if hidden_page(slug):
        return hidden_page_view()
    if not GIT_ENABLED:
        abort(404)

    path = safe_path(slug)
    if path is None or not path.exists():
        abort(404)

    rel = str(path.relative_to(WIKI_DIR))
    status_r = _git("git", "status", "--porcelain", "--", rel)
    if status_r.stdout.startswith("??"):
        r = _git("git", "diff", "--no-index", "--", "/dev/null", rel)
    else:
        r = _git("git", "diff", "--", rel)
    diff = r.stdout or "(no changes)"

    content = path.read_text(encoding="utf-8")
    title = page_title(content, slug)
    diff_html = _diff_to_html(diff)
    commit_btn = _commit_form_html(rel, title, "/uncommitted")
    body = f"""{build_breadcrumb('uncommitted', 'Uncommitted Changes', (title, '/uncommitted'), ('Diff', None))}
<header class="page-header">
  <h1>Uncommitted Changes — {html.escape(title)}</h1>
  <div class="page-header-actions">
    {commit_btn}
    <a href="/uncommitted" class="btn">← Uncommitted</a>
  </div>
</header>
<div class="diff-view">{diff_html}</div>"""
    return shell(f"Diff — {title}", body)


@app.get("/api/uncommitted")
def api_uncommitted():
    """Return list of files with uncommitted changes."""
    return jsonify([
        {"slug": e["slug"], "title": e["title"], "filepath": e["filepath"]}
        for e in _uncommitted_entries() if not (e["slug"] and hidden_page(e["slug"]))
    ])


@app.post("/api/commit-all")
def api_commit_all():
    """Commit all uncommitted files, one commit per file."""
    if not GIT_ENABLED:
        return jsonify({"error": "Git not enabled"}), 400
    
    committed = []
    for f in git_uncommitted_files():
        filepath = f["filepath"]
        git_commit(WIKI_DIR / filepath, _commit_message(filepath))
        committed.append(filepath)
    return jsonify({"committed": len(committed), "files": committed})


@app.post("/api/commit-file")
def api_commit_file():
    """Commit a single file."""
    if not GIT_ENABLED:
        return jsonify({"error": "Git not enabled"}), 400
    
    data = request.get_json(silent=True)
    filepath = data.get("filepath", "") if isinstance(data, dict) else ""
    if not filepath:
        return jsonify({"error": "No filepath provided"}), 400

    # Verify file is in uncommitted list
    if filepath not in [f["filepath"] for f in git_uncommitted_files()]:
        return jsonify({"error": "File is not uncommitted"}), 400

    # Message policy lives server-side; the client only sends the filepath.
    git_commit(WIKI_DIR / filepath, _commit_message(filepath))
    return jsonify({"success": True})


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Always bind to all interfaces so the container's network bridge can
    # forward connections. LAN exposure is controlled by run.sh's port
    # mapping (127.0.0.1:5000:5000 = localhost only, 0.0.0.0:5000:5000 = LAN).
    # The host-header check (ALLOWED_HOSTS) is the DNS-rebinding defence.
    app.run(host="0.0.0.0", port=5000)
