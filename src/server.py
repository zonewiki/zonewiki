import hashlib
import hmac
import html
import json
import mimetypes
import os
import re
import shutil
import subprocess
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, request, send_from_directory, session, url_for
from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin
from mdit_py_plugins.deflist import deflist_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin

import deck as deck_module

app = Flask(__name__, static_folder=None)
APP_NAME = "Leaf"

LEAF_PASSWORD = os.environ.get("LEAF_PASSWORD", "")
GIT_REMOTE = os.environ.get("GIT_REMOTE", "")
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32)
app.config["PERMANENT_SESSION_LIFETIME"] = 30 * 24 * 60 * 60  # 30 days
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True

ALLOW_REMOTE  = os.environ.get("ALLOW_REMOTE", "").lower() in ("1", "true", "yes")
ALLOWED_HOSTS = {"localhost:5000", "127.0.0.1:5000"}

PUBLIC_DIR = Path(__file__).parent / "public"

@app.before_request
def check_host():
    if not ALLOW_REMOTE and request.host not in ALLOWED_HOSTS:
        abort(400)

@app.before_request
def check_auth():
    if not LEAF_PASSWORD:
        return
    if request.endpoint in ("login", "logout", "serve_public"):
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
MD_LINK_RE = re.compile(r'\[([^\]]*)\]\(([^)#?\s]+\.md)\)')
# Matches any markdown link href (bare slugs, .md, etc.) — used for backlink scanning
_ANY_MD_LINK_RE = re.compile(r'\[(?:[^\]]*)\]\(([^)#?\s]+)\)')


def slug_display_name(slug: str) -> str:
    """Convert a slug to a human-readable title (e.g. 'my-page' → 'My Page')."""
    return slug.split('/')[-1].replace("-", " ").title()


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), cwd=WIKI_DIR, capture_output=True, text=True)


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


def git_commit(path: Path, message: str) -> None:
    if not GIT_ENABLED:
        return
    rel = str(path.relative_to(WIKI_DIR))
    _git("git", "add", "--", rel)
    _git("git", "commit", "-m", message)


def git_commit_rename(old_path: Path, new_path: Path, modified_refs: list[Path], message: str) -> None:
    if not GIT_ENABLED:
        return
    # Stage the renamed file and only the pages whose wikilinks were updated
    old_rel = str(old_path.relative_to(WIKI_DIR))
    new_rel = str(new_path.relative_to(WIKI_DIR))
    _git("git", "add", "--", old_rel, new_rel)
    for ref_path in modified_refs:
        _git("git", "add", "--", str(ref_path.relative_to(WIKI_DIR)))
    _git("git", "commit", "-m", message)


def git_log(path: Path) -> list[dict]:
    if not GIT_ENABLED:
        return []
    rel = str(path.relative_to(WIKI_DIR))
    r = _git("git", "log", "--follow",
             "--format=%H\x1f%as\x1f%ar\x1f%s", "--", rel)
    entries = []
    for line in r.stdout.splitlines():
        parts = line.split("\x1f", 3)
        if len(parts) == 4:
            entries.append({"sha": parts[0], "date": parts[1],
                            "rel": parts[2], "msg": parts[3]})
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
    """Return list of files with uncommitted changes (relative to WIKI_DIR).
    
    Returns: List of dicts with 'filepath', 'mtime' (ISO date string), and 'rel' (relative time).
    """
    if not GIT_ENABLED:
        return []
    r = _git("git", "status", "--porcelain", "--untracked-files=all")
    files = []
    for line in r.stdout.splitlines():
        if len(line) < 4:
            continue
        status = line[:2]
        filepath = line[3:]
        # Skip directories (git reports untracked dirs with a trailing slash)
        if filepath.endswith('/'):
            continue
        # Include modified (M), added (A), deleted (D), and untracked (??)
        if status.strip():
            fpath = WIKI_DIR / filepath
            if fpath.exists():
                mtime_ts = fpath.stat().st_mtime
                mtime_dt = datetime.fromtimestamp(mtime_ts)
                mtime = mtime_dt.strftime("%Y-%m-%d")
                rel = relative_time(mtime_dt)
            else:
                mtime = ""
                rel = ""
            files.append({"filepath": filepath, "mtime": mtime, "rel": rel})
    return files


def git_commit_file(filepath: str, message: str) -> None:
    """Stage and commit a single file."""
    if not GIT_ENABLED:
        return
    _git("git", "add", "--", filepath)
    _git("git", "commit", "-m", message)


def git_diff(sha: str, path: Path) -> str | None:
    rel = str(path.relative_to(WIKI_DIR))
    r = _git("git", "show", "--format=", "--patch", sha, "--", rel)
    return r.stdout.strip() if r.returncode == 0 else None


git_setup()

# Initialize deck module and register its blueprint
deck_module.init_deck(WIKI_DIR, GIT_ENABLED, git_commit)
app.register_blueprint(deck_module.bp)
DECK_FILE = deck_module.DECK_FILE

HOME_LINK = f"""<a href="/" class="breadcrumb-home" aria-label="{APP_NAME} home">
  <svg class="site-logo" viewBox="0 0 16 16" aria-hidden="true">
    <path d="M12.8 1.7C9.6 1.8 7.1 3 5.5 5.1c-1.8 2.4-2.2 5.3-2.2 7.5 0 .4.3.7.7.7 2.2 0 5.1-.4 7.5-2.2 2.1-1.6 3.3-4.1 3.4-7.3 0-.4-.3-.8-.7-.8-.5-.1-.9-.1-1.4-.1Zm-6 9.5c-.2 0-.4-.1-.5-.3-.2-.3-.2-.7.2-1l5.1-3.8c.3-.2.7-.1 1 .2.2.3.1.7-.2 1l-5.1 3.8c-.2.1-.3.1-.5.1Z" fill="currentColor"/>
    <path d="M3.5 12.5 Q3 15 2 15" stroke="black" stroke-width="1.1" stroke-linecap="round" fill="none"/>
  </svg>
  <span>{APP_NAME}</span>
</a>"""

def build_breadcrumb(slug: str, title: str, *extra: tuple, right_html: str = "", query_string: str = "") -> str:
    """Build a breadcrumb for a page slug.
    slug/title are the page; extra is zero or more (label, url_or_None) pairs appended after.
    right_html is optional HTML appended inside the breadcrumb div with margin-left: auto.
    query_string is appended to the page's own link (e.g. '?tag=task').
    """
    sep = '<span class="breadcrumb-sep">›</span>'
    parts = slug.split('/')
    crumbs = [HOME_LINK]
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
    MarkdownIt(options_update={"html": True})
    .enable("table")
    .enable("strikethrough")
    .enable("smartquotes")
    .enable("replacements")
    .use(tasklists_plugin)
    .use(footnote_plugin)
    .use(deflist_plugin)
    .use(anchors_plugin, max_level=3)
)


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
DAILY_RE  = re.compile(r"^(\d{4}-\d{2}-\d{2})_daily$")


def is_scratch(slug: str) -> bool:
    return bool(SCRATCH_RE.match(slug))


def iter_pages():
    """Yield (path, slug) for all pages, skipping _* subdirectories."""
    for md_file in PAGES_DIR.rglob("*.md"):
        rel = md_file.relative_to(PAGES_DIR)
        if any(part.startswith('_') for part in rel.parts[:-1]):
            continue
        yield md_file, str(rel.with_suffix(''))


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
    """Return all scratch notes sorted newest first."""
    entries = []
    for md_file in PAGES_DIR.glob("*.md"):
        m = SCRATCH_RE.match(md_file.stem)
        if not m:
            continue
        ts = datetime.strptime(m.group(1), "%Y-%m-%d_%H-%M-%S")
        content = md_file.read_text(encoding="utf-8")
        entries.append({
            "slug": md_file.stem,
            "title": page_title(content, md_file.stem),
            "ts": ts,
            "date": ts.strftime("%Y-%m-%d %H:%M"),
        })
    entries.sort(key=lambda n: n["ts"], reverse=True)
    return entries


def list_daily() -> list[dict]:
    """Return all daily notes sorted newest first."""
    entries = []
    for md_file in PAGES_DIR.glob("*.md"):
        d = DAILY_RE.match(md_file.stem)
        if not d:
            continue
        ts = datetime.strptime(d.group(1), "%Y-%m-%d")
        content = md_file.read_text(encoding="utf-8")
        entries.append({
            "slug": md_file.stem,
            "title": page_title(content, md_file.stem),
            "ts": ts,
            "date": ts.strftime("%Y-%m-%d"),
        })
    entries.sort(key=lambda n: n["ts"], reverse=True)
    return entries


def page_title(content: str, slug: str) -> str:
    m = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    return m.group(1).strip() if m else slug_display_name(slug)


def process_wikilinks(text: str) -> str:
    slugs = set(all_slugs())

    def replace(m: re.Match) -> str:
        inner = m.group(1)
        if "|" in inner:
            page_name, label = inner.split("|", 1)
        else:
            page_name = label = inner
        raw = page_name.strip()
        slug = raw if raw in slugs else to_slug(page_name)
        label = html.escape(label.strip())
        css = "wikilink" if slug in slugs else "wikilink wikilink-missing"
        return f'<a href="/page/{html.escape(slug)}" class="{css}">{label}</a>'

    return WIKILINK_RE.sub(replace, text)

_TOC_PLACEHOLDER = "LEAFTOCPLACEHOLDER"
_TOC_RE = re.compile(r'^\[\[toc\]\][^\S\n]*$', re.IGNORECASE | re.MULTILINE)

# ── [[deck …]] directive ─────────────────────────────────────────────────────────
# Moved to deck.py module — use deck_module functions
_DECK_RE = deck_module._DECK_RE


def _heading_id(text: str) -> str:
    """Match mdit-py-plugins anchors_plugin default slugify."""
    return re.sub(r'[^\w\- ]+', '', text, flags=re.UNICODE).strip().lower().replace(' ', '-')


def _plain_heading(text: str) -> str:
    """Strip inline markdown and wikilinks from a heading to get plain text for anchor IDs."""
    text = re.sub(r'\[\[([^\]|]+)\|([^\]]+)\]\]', r'\2', text)  # [[page|label]] → label
    text = re.sub(r'\[\[([^\]]+)\]\]', r'\1', text)              # [[page]] → page
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)         # [label](url)
    text = re.sub(r'\[([^\]]*)\]\[[^\]]*\]', r'\1', text)        # [label][ref]
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


def _build_toc(text: str) -> str:
    headings = re.findall(r'^(#{2,3})\s+(.+)', text, re.MULTILINE)
    if not headings:
        return ''
    items = []
    for hashes, title in headings:
        level = len(hashes)
        plain = _plain_heading(title.strip())
        hid = _heading_id(plain)
        cls = ' class="toc-h3"' if level == 3 else ''
        label = md.renderInline(_strip_links(title.strip()))
        items.append(f'<li{cls}><a href="#{hid}">{label}</a></li>')
    return '<nav class="toc"><ul>\n' + '\n'.join(items) + '\n</ul></nav>'


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


def _parse_tasks_params(raw: str) -> dict:
    return deck_module._parse_tasks_params(raw)


_DECK_PLACEHOLDER = deck_module._DECK_PLACEHOLDER_BASE


def render_md(text: str, *, slug: str = "") -> str:
    toc_html = ''
    if _TOC_RE.search(text):
        toc_html = _build_toc(text)
        text = _TOC_RE.sub(_TOC_PLACEHOLDER, text)

    # Replace [[deck …]] directives with placeholders (supports multiple per page)
    text, deck_embeds = deck_module.process_deck_embeds(text, slug=slug)

    result = _apply_img_sizes(md.render(process_wikilinks(text)))
    result = re.sub(r'\s+disabled(?:="[^"]*")?', '', result)
    result = _render_task_dates(result)
    if toc_html:
        result = result.replace(f'<p>{_TOC_PLACEHOLDER}</p>', toc_html)
    if deck_embeds:
        result = deck_module.inject_deck_embeds(result, deck_embeds)
    return result


def toggle_nth_checkbox(content: str, idx: int) -> str:
    """Toggle the Nth checkbox (0-based), skipping any inside fenced code blocks."""
    lines = content.splitlines(keepends=True)
    in_fence = False
    fence_marker = ''
    fence_len = 0
    checkbox_line_indices: list[int] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not in_fence:
            if stripped.startswith('```') or stripped.startswith('~~~'):
                in_fence = True
                fence_marker = stripped[0]
                fence_len = len(stripped) - len(stripped.lstrip(fence_marker))
            elif re.search(r'\[([xX ])\]', line):
                checkbox_line_indices.append(i)
        else:
            if stripped.startswith(fence_marker * fence_len) and stripped.rstrip(fence_marker) == '':
                in_fence = False
    if idx >= len(checkbox_line_indices):
        return content
    li = checkbox_line_indices[idx]
    line = lines[li]
    eol = '\n' if line.endswith('\n') else ''
    body = line.rstrip('\n')

    # Toggle the [ ] / [x]
    is_checking = bool(re.search(r'\[ \]', body))
    body = re.sub(r'\[([xX ])\]', lambda m: '[x]' if m.group(1) == ' ' else '[ ]', body, count=1)

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
    in_fence = False
    fence_marker = ''
    fence_len = 0
    prev_text_line_idx: int | None = None   # for setext h2 detection

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not in_fence:
            # Check for fence opener: 3+ consecutive backticks or tildes
            if stripped.startswith('```') or stripped.startswith('~~~'):
                in_fence = True
                fence_marker = stripped[0]
                fence_len = len(stripped) - len(stripped.lstrip(fence_marker))
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
        else:
            # Closing fence must use same char and be at least as long
            if stripped.startswith(fence_marker * fence_len) and stripped.rstrip(fence_marker) == '':
                in_fence = False
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


def strip_h1(content: str) -> str:
    """Remove the first H1 line so it isn't duplicated below the page header."""
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if line.strip():
            if re.match(r"^#\s+", line):
                del lines[i]
            break
    return "\n".join(lines)


def _resolve_md_link(href: str, source_slug: str) -> str | None:
    """Resolve a relative .md link href to a slug, or None if external."""
    if href.startswith(('http://', 'https://', '//', '/')):
        return None
    target = href[:-3]  # strip .md
    source_dir = source_slug.split('/')[:-1]
    parts = source_dir + target.split('/')
    normalized: list[str] = []
    for part in parts:
        if part == '..':
            if normalized:
                normalized.pop()
        elif part and part != '.':
            normalized.append(part)
    return '/'.join(normalized) if normalized else None


def _resolve_bare_link(href: str, source_slug: str) -> str | None:
    """Resolve a bare relative slug link (no extension) to a slug, or None if external."""
    if href.startswith(('http://', 'https://', '//', '/', '#')) or ':' in href:
        return None
    source_dir = source_slug.split('/')[:-1]
    parts = source_dir + href.split('/')
    normalized: list[str] = []
    for part in parts:
        if part == '..':
            if normalized:
                normalized.pop()
        elif part and part != '.':
            normalized.append(part)
    return '/'.join(normalized) if normalized else None


def _page_link_targets(content: str, file_slug: str, known_slugs: set[str] | None = None) -> set[str]:
    """Collect all slugs that a page links to (wikilinks + markdown links)."""
    targets: set[str] = set()
    for m in WIKILINK_RE.finditer(content):
        targets.add(to_slug(m.group(1).split("|")[0].strip()))
    for m in _ANY_MD_LINK_RE.finditer(content):
        href = m.group(1)
        if href.endswith('.md'):
            resolved = _resolve_md_link(href, file_slug)
        else:
            resolved = _resolve_bare_link(href, file_slug)
        if resolved and (known_slugs is None or resolved in known_slugs):
            targets.add(resolved)
    return targets


def all_backlink_counts() -> dict[str, int]:
    """Return a mapping of slug → number of pages that link to it."""
    counts: dict[str, int] = {}
    known = set(all_slugs())
    for md_file, file_slug in iter_pages():
        content = md_file.read_text(encoding="utf-8")
        for target in _page_link_targets(content, file_slug, known):
            if target != file_slug:
                counts[target] = counts.get(target, 0) + 1
    return counts


def find_backlinks(slug: str) -> list[dict]:
    """Return pages that contain a wikilink or markdown link resolving to slug."""
    results = []
    known = set(all_slugs())
    for md_file, file_slug in iter_pages():
        if file_slug == slug:
            continue
        content = md_file.read_text(encoding="utf-8")
        if slug in _page_link_targets(content, file_slug, known):
            results.append({"slug": file_slug, "title": page_title(content, file_slug)})
    results.sort(key=lambda r: r["title"].lower())
    return results


def find_task_backlinks(slug: str) -> list[dict]:
    """Return deck tasks whose title or description contain a wikilink to slug."""
    return deck_module.find_task_backlinks(slug, to_slug)


def update_references(old_slug: str, new_slug: str) -> list[Path]:
    """Rewrite [[wikilinks]] across all pages after a rename.

    Returns a list of files that were modified.
    """
    modified: list[Path] = []
    for md_file, _ in iter_pages():
        content = md_file.read_text(encoding="utf-8")

        def replace(m: re.Match) -> str:
            inner = m.group(1)
            if "|" in inner:
                page_name, label = inner.split("|", 1)
                raw_name = page_name.strip()
                if raw_name == old_slug or to_slug(page_name) == old_slug:
                    return f"[[{new_slug}|{label}]]"
            else:
                raw_name = inner.strip()
                if raw_name == old_slug or to_slug(inner) == old_slug:
                    return f"[[{new_slug}]]"
            return m.group(0)

        new_content = WIKILINK_RE.sub(replace, content)
        if new_content != content:
            md_file.write_text(new_content, encoding="utf-8")
            modified.append(md_file)
    return modified


def _render_task_title(title: str) -> str:
    """Render wikilinks and markdown links in a task title as safe HTML."""
    def _wl_repl(m: re.Match) -> str:
        inner = m.group(1)
        if "|" in inner:
            page_name, label = inner.split("|", 1)
            s = to_slug(page_name)
            return f'<a href="/page/{html.escape(s)}">{html.escape(label.strip())}</a>'
        s = to_slug(inner)
        return f'<a href="/page/{html.escape(s)}">{html.escape(inner.strip())}</a>'
    result = re.sub(r'\[\[([^\[\]]+)\]\]', _wl_repl, title)
    def _md_repl(m: re.Match) -> str:
        return f'<a href="{html.escape(m.group(2))}">{html.escape(m.group(1))}</a>'
    result = re.sub(r'\[([^\]]*)\]\((/page/[^)]+)\)', _md_repl, result)
    # Escape non-link text segments; link tags were inserted above and are safe
    parts = re.split(r'(<a\b[^>]*>.*?</a>)', result, flags=re.DOTALL)
    return ''.join(part if i % 2 == 1 else html.escape(part) for i, part in enumerate(parts))


def _build_backlinks_html(slug: str, backlinks: list[dict], task_backlinks: list[dict]) -> tuple[str, str]:
    """Return (bl_html, bl_chip) for the backlinks sections and the header chip."""
    from urllib.parse import urlencode
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
        tbl_items = "".join(
            f'<li><a href="/page/deck?{html.escape(urlencode({"search": t["title"]}))}">'
            f'{_render_task_title(t["title"])}</a></li>'
            for t in task_backlinks
        )
        bl_html += f'<section class="backlinks" id="task-backlinks"><h3>Linked tasks</h3><ul>{tbl_items}</ul></section>'
    return bl_html, bl_chip


def _build_delete_dialog(slug: str, title: str) -> str:
    """Return delete-confirmation dialog HTML, or empty string for the index page."""
    if slug == "index":
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


def _build_page_meta(slug: str, path: Path, title: str) -> tuple[str, str]:
    """Return (last_modified_html, commit_btn_html) for the page meta bar.

    If git is disabled or the file has no history yet, both strings are empty.
    When the file has uncommitted changes the commit button is included.
    """
    if not GIT_ENABLED:
        return "", ""
    filepath = slug + ".md"
    uncommitted_files = git_uncommitted_files()
    info = next((f for f in uncommitted_files if f["filepath"] == filepath), None)
    if info:
        last_modified = (
            f'<div class="page-meta">Last changed {html.escape(info["mtime"])} '
            f'({html.escape(info["rel"])}) — uncommitted</div>'
        )
        commit_btn = (
            f'<form class="commit-file-form" style="display:inline"'
            f' data-filepath="{html.escape(filepath)}"'
            f' data-title="{html.escape(title)}"'
            f' data-on-success="reload">'
            f'<button type="submit" class="btn btn-primary btn-sm">Commit</button>'
            f'</form>'
        )
        return last_modified, commit_btn
    entries = git_log(path)
    if entries:
        e = entries[0]
        return f'<div class="page-meta">Last changed {html.escape(e["date"])} ({html.escape(e["rel"])})</div>', ""
    return "", ""


def shell(title: str, body: str, *, slug: str = "", editor: bool = False, extra_head: str | None = None) -> str:
    edit_btn = f'<a href="/edit/{slug}" class="btn">Edit</a>' if slug and not editor else ""
    body_class = ' class="editor-page"' if editor else ""
    safe_title = html.escape(title)
    extra_head_html = extra_head if extra_head else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title} — {APP_NAME}</title>
  <link rel="icon" href="/public/favicon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="/public/style.css">
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css" media="(prefers-color-scheme: light)">
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css" media="(prefers-color-scheme: dark)">
  <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js" defer></script>
  <script src="/public/leaf.js" defer></script>
  {extra_head_html}
</head>
<body{body_class}>
  <dialog id="search-modal" class="search-modal">
    <div class="search-modal-controls">
      <input type="text" id="modal-filter" placeholder="Search pages…" autocomplete="off" spellcheck="false">
      <select id="modal-sort">
        <option value="title-asc">Title A–Z</option>
        <option value="title-desc">Title Z–A</option>
        <option value="mtime-desc">Recently edited</option>
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
      <tr><td><kbd>c t</kbd></td><td>Add to Deck</td></tr>
      <tr><td><kbd>c d</kbd></td><td>Open today's daily note</td></tr>
      <tr><td><kbd>g h</kbd></td><td>Go to Home</td></tr>
      <tr><td><kbd>g s</kbd></td><td>Go to Scratch index</td></tr>
      <tr><td><kbd>g d</kbd></td><td>Go to Deck</td></tr>
      <tr><td><kbd>g y</kbd></td><td>Go to Daily index</td></tr>

      <tr><td><kbd>F2</kbd></td><td>Toggle preview (editor)</td></tr>
      <tr><td><kbd>Ctrl+Delete</kbd></td><td>Delete current page</td></tr>
      <tr><td><kbd>Ctrl+S</kbd></td><td>Done / go to page view (editor)</td></tr>
      <tr><td><kbd>Ctrl+B</kbd></td><td>Bold (editor)</td></tr>
      <tr><td><kbd>Ctrl+Shift+X</kbd></td><td>Toggle checkbox (editor)</td></tr>
      <tr><td><kbd>e</kbd></td><td>Edit current page</td></tr>
      <tr><td><kbd>p</kbd></td><td>Enter presentation mode</td></tr>
      <tr><td><kbd>Ctrl+/</kbd></td><td>Show this help</td></tr>
      <tr><td><kbd>?</kbd></td><td>Show this help (alt)</td></tr>
    </table>
    <p class="scratch-label">Snippets <span class="shortcuts-hint">(type trigger, press Tab)</span></p>
    <table class="shortcuts-table">
      <tr><td><kbd>/date</kbd></td><td>Insert current date</td></tr>
      <tr><td><kbd>/time</kbd></td><td>Insert current time</td></tr>
      <tr><td><kbd>/datetime</kbd></td><td>Insert current date and time</td></tr>
    </table>
  </dialog>
  <dialog id="task-delete-modal" class="scratch-modal">
    <p class="scratch-label">Delete task?</p>
    <p id="task-delete-title" class="task-delete-title"></p>
    <div class="scratch-actions">
      <button type="button" class="btn btn-danger" id="task-delete-confirm">Delete</button>
      <button type="button" class="btn" id="task-delete-cancel">Cancel</button>
    </div>
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
  {'<form method="post" action="/logout" class="signout-form"><button class="btn" type="submit">Sign out</button></form>' if LEAF_PASSWORD else ''}
  <div class="top-badges">
    {'<div class="remote-badge" title="Remote access enabled">🌐</div>' if ALLOW_REMOTE else ''}
    <a id="deck-due-badge" class="deck-due-badge" href="/page/deck?filter=due" style="display: none;" title="Deck due">
      <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="12" height="11" rx="1.5"/><line x1="5" y1="1.5" x2="5" y2="4.5"/><line x1="11" y1="1.5" x2="11" y2="4.5"/><line x1="2" y1="7" x2="14" y2="7"/></svg>
      <span id="deck-due-count">0</span>
    </a>
    <a id="uncommitted-badge" class="uncommitted-badge" href="/uncommitted" style="display: none;" title="Uncommitted changes">
      <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="5.5"/><line x1="8" y1="5.5" x2="8" y2="8"/><line x1="8" y1="8" x2="10" y2="10"/></svg>
      <span id="uncommitted-count">0</span>
    </a>
  </div>
  <main>
    {body}
  </main>
</body>
</html>"""


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
            if not next_url.startswith("/"):
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
    return shell("Sign in", body)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── Page routes ───────────────────────────────────────────────────────────────


@app.get("/")
def index():
    return redirect("/page/index")


@app.get("/page/_scratch")
def scratch_page():
    entries = list_scratch()
    if entries:
        items = "".join(
            f'<li><a href="/page/{n["slug"]}">{html.escape(n["title"])}</a>'
            f'<span class="note-date">{n["date"]}</span></li>'
            for n in entries
        )
        body_inner = f"<ul class='notes-list'>{items}</ul>"
    else:
        body_inner = "<p>No entries yet.</p>"
    body = f"""{build_breadcrumb('_scratch', 'Scratch')}
<header class="page-header"><h1>Scratch</h1></header>
<article class="page-content">{body_inner}</article>"""
    return shell("Scratch", body)


@app.get("/page/_daily")
def daily_index_page():
    entries = list_daily()
    if entries:
        items = "".join(
            f'<li><a href="/page/{n["slug"]}">{html.escape(n["title"])}</a>'
            f'<span class="note-date">{n["date"]}</span></li>'
            for n in entries
        )
        body_inner = f"<ul class='notes-list'>{items}</ul>"
    else:
        body_inner = "<p>No daily notes yet.</p>"
    body = f"""{build_breadcrumb('_daily', 'Daily')}
<header class="page-header"><h1>Daily</h1></header>
<article class="page-content">{body_inner}</article>"""
    return shell("Daily", body)


@app.get("/page/_deleted")
def deleted_pages_route():
    if not GIT_ENABLED:
        abort(404)
    pages = list_deleted_pages()
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
    body = f"""{build_breadcrumb('_deleted', 'Deleted Pages')}
<header class="page-header"><h1>Deleted Pages</h1></header>
<article class="page-content">{body_inner}</article>"""
    return shell("Deleted Pages", body)


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
    article_html = render_md(strip_h1(content), slug="_raw")
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


@app.post("/api/raw/append-url")
def api_raw_append_url():
    url = request.form.get("url", "").strip()
    title = request.form.get("title", "").strip() or url
    if not url:
        abort(400)
    path = safe_path("_raw")
    if path is None:
        abort(400)
    content = path.read_text(encoding="utf-8") if path.exists() else "# Raw\n\n"
    if not content.endswith("\n"):
        content += "\n"
    content += f"- [{title}]({url})\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    git_commit(path, f"Add URL to _raw: {title[:60]}")
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
    raw_path = safe_path("_raw")
    if raw_path is not None:
        raw_content = raw_path.read_text(encoding="utf-8") if raw_path.exists() else "# Raw\n\n"
        if not raw_content.endswith("\n"):
            raw_content += "\n"
        raw_content += f"- [{title}](/page/{slug})\n"
        raw_path.write_text(raw_content, encoding="utf-8")
        git_commit(raw_path, f"Add snippet link to _raw: {title[:60]}")
    return jsonify({"slug": slug, "title": title})


@app.post("/api/daily")
def api_daily():
    today = datetime.now().strftime("%Y-%m-%d")
    slug = f"{today}_daily"
    path = safe_path(slug)
    if path is None:
        abort(400)
    if not path.exists():
        path.write_text(f"# {today}\n\n", encoding="utf-8")
        git_commit(path, f"Create daily {today}")
    return jsonify({"slug": slug})


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
    bl_html, bl_chip = _build_backlinks_html(slug, find_backlinks(slug), find_task_backlinks(slug))
    delete_dialog = _build_delete_dialog(slug, title)
    _qs = ('?' + request.query_string.decode()) if request.query_string else ''

    if request.args.get('from') == 'uncommitted' and GIT_ENABLED:
        # Viewing a page from the uncommitted-changes list: show commit button
        filepath = slug + '.md'
        commit_btn = (
            f'<form class="commit-file-form" style="display:inline"'
            f' data-filepath="{html.escape(filepath)}"'
            f' data-title="{html.escape(title)}"'
            f' data-on-success="/uncommitted">'
            f'<button type="submit" class="btn btn-primary">Commit</button>'
            f'</form>'
        )
        body = f"""{build_breadcrumb('uncommitted', 'Uncommitted Changes', (title, None), right_html=bl_chip)}{delete_dialog}
<header class="page-header">
  <h1>{html.escape(title)}</h1>
  <div class="page-header-actions">
    {commit_btn}
    <a href="/uncommitted" class="btn">← Uncommitted</a>
  </div>
</header>
<article class="page-content">
  {render_md(strip_h1(content), slug=slug)}
</article>
{bl_html}"""
    else:
        hist_link = f'<a href="/history/{slug}" class="hist-link">History</a>' if GIT_ENABLED else ""
        last_modified, meta_commit_btn = _build_page_meta(slug, path, title)
        page_meta_html = (
            f'<div class="page-meta-wrapper">{last_modified}{meta_commit_btn}</div>'
            if last_modified or meta_commit_btn else ""
        )
        article_html = _inject_section_edit_buttons(render_md(strip_h1(content), slug=slug), slug)
        body = f"""{build_breadcrumb(slug, title, right_html=bl_chip, query_string=_qs)}{delete_dialog}
<header class="page-header">
  <h1>{html.escape(title)}</h1>
  <div class="page-header-actions">
    {hist_link}
    <button id="present-btn" class="btn" title="Presentation mode">Present</button>
    <a href="/edit/{slug}" class="btn">Edit</a>
  </div>
  {page_meta_html}
</header>
<article class="page-content">
  {article_html}
</article>
{bl_html}"""

    return shell(title, body)


@app.get("/edit/<path:slug>")
def edit_page(slug):
    path = safe_path(slug)
    if path is None:
        abort(400)
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
    can_rename = slug != "index"
    can_delete = slug != "index" and not is_new
    rename_btn = '<button type="button" class="btn" id="rename-toggle">Rename…</button>' if can_rename and not is_new and section is None else ""
    delete_btn = f'<button type="submit" form="delete-form" class="btn btn-danger" id="delete-btn" data-title="{safe_title}" data-git="{"1" if GIT_ENABLED else ""}">Delete</button>' if can_delete and section is None else ""
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
    form_action = f'/edit/{safe_slug}'
    # Always embed file_hash for conflict detection (full-file and section edits)
    if not is_new:
        full_file_hash = content_hash(path.read_text(encoding="utf-8")) if section is None else file_hash_val
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
    body = f"""<p class="editor-mobile-msg">The editor is not available on small screens. <a href="/page/{safe_slug}">View page instead.</a></p>
<div class="editor-wrap">
  <div class="editor-pane">
    {build_breadcrumb(slug, title, breadcrumb_extra)}
    <div class="editor-toolbar">
      <span class="slug-label">{safe_slug}</span>
      <span id="save-dot" class="save-dot">●</span>
      <button type="submit" form="editor-form" class="btn btn-primary">Done</button>
      {rename_btn}
      {delete_btn}
    </div>
    {delete_form}
    {rename_bar}
    <form id="editor-form" method="post" action="{form_action}">
      {section_hidden_fields}
      <textarea id="editor-textarea" name="content"
                spellcheck="false" autocorrect="off" autocapitalize="off">{safe_content}</textarea>
    </form>
  </div>
  <div class="preview-pane">
    <p class="pane-label">Preview</p>
    <div id="editor-preview" class="page-content"></div>
  </div>
</div>"""
    return shell(f"Edit — {title}", body, editor=True)


def _splice_section(path: Path, file_hash: str, start_char: int,
                    end_char: int, new_content: str) -> tuple[str, str, int, int] | None:
    """Replace content[start_char:end_char] in the file, guarded by a hash check.

    Returns (merged_full_content, new_file_hash, new_start_char, new_end_char)
    or None if the file doesn't exist or the hash doesn't match.
    """
    if not path.exists():
        return None
    full = path.read_text(encoding="utf-8")
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


@app.post("/edit/<path:slug>")
def save_page(slug):
    path = safe_path(slug)
    if path is None:
        abort(400)
    content = request.form.get("content", "").replace('\r\n', '\n').replace('\r', '\n')
    file_hash = request.form.get("file_hash", "")
    start_char = request.form.get("start_char", type=int)
    end_char = request.form.get("end_char", type=int)
    if file_hash and start_char is not None and end_char is not None:
        result = _splice_section(path, file_hash, start_char, end_char, content)
        if result is None:
            return redirect(f"/edit/{slug}?conflict=1")
        content = result[0]
    elif file_hash and path.exists():
        # Full-file edit with hash — check for conflict
        current = path.read_text(encoding="utf-8")
        if content_hash(current) != file_hash:
            return redirect(f"/edit/{slug}?conflict=1")
    # Skip write if content is unchanged, but still commit if file is dirty
    if path.exists() and path.read_text(encoding="utf-8") == content:
        git_commit(path, f"Update {slug}")
        return redirect(f"/page/{slug}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    git_commit(path, f"Update {slug}")
    return redirect(f"/page/{slug}")


@app.post("/api/save/<path:slug>")
def api_save(slug):
    path = safe_path(slug)
    if path is None:
        abort(400)
    raw_content = request.form.get("content", "").replace('\r\n', '\n').replace('\r', '\n')
    file_hash = request.form.get("file_hash", "")
    start_char = request.form.get("start_char", type=int)
    end_char = request.form.get("end_char", type=int)
    force_section = request.form.get("force_section", type=int)
    is_section_edit = bool(file_hash and start_char is not None and end_char is not None)
    is_force_section = force_section is not None and force_section >= 1
    if is_force_section:
        # Force-splice: re-read file, find section by index, splice content in
        if not path.exists():
            abort(404)
        current = path.read_text(encoding="utf-8")
        rng = section_char_range(current, force_section)
        if rng is None:
            abort(400)
        fs_start, fs_end = rng
        content = current[:fs_start] + raw_content + current[fs_end:]
        new_hash = content_hash(content)
        new_start = fs_start
        new_end = fs_start + len(raw_content)
    elif is_section_edit:
        result = _splice_section(path, file_hash, start_char, end_char, raw_content)
        if result is None:
            return jsonify({"error": "file_changed"}), 409
        content, new_hash, new_start, new_end = result
    else:
        # Full-file edit — check hash if provided (conflict detection)
        if file_hash and path.exists():
            current = path.read_text(encoding="utf-8")
            if content_hash(current) != file_hash:
                return jsonify({"error": "file_changed", "file_hash": content_hash(current)}), 409
        content = raw_content
    has_section_response = is_section_edit or is_force_section
    # Skip write if content is unchanged
    if path.exists() and path.read_text(encoding="utf-8") == content:
        if has_section_response:
            return jsonify({"file_hash": new_hash, "start_char": new_start, "end_char": new_end})
        if file_hash:
            return jsonify({"file_hash": content_hash(content)})
        return "", 204
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
    if slug == "index":
        abort(400)
    old_path = safe_path(slug)
    if old_path is None or not old_path.exists():
        abort(404)
    raw = request.form.get("new_name", "").strip()
    # Slugify but preserve underscores so scratch timestamps survive a rename
    new_slug = "/".join(
        re.sub(r"[^a-z0-9_]+", "-", p.strip().lower()).strip("-")
        for p in raw.split("/")
        if re.sub(r"[^a-z0-9_]+", "-", p.strip().lower()).strip("-")
    )
    if not new_slug or new_slug == slug:
        return redirect(f"/edit/{slug}")
    new_path = safe_path(new_slug)
    if new_path is None:
        abort(400)
    if new_path.exists():
        return redirect(f"/edit/{slug}?rename_error=A+page+named+%22{new_slug}%22+already+exists")
    new_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.rename(new_path)
    modified_refs = update_references(slug, new_slug)
    git_commit_rename(old_path, new_path, modified_refs, f"Rename {slug} to {new_slug}")
    return redirect(f"/page/{new_slug}")


@app.post("/delete/<path:slug>")
def delete_page(slug):
    if slug == "index":
        abort(400)
    path = safe_path(slug)
    if path is None or not path.exists():
        abort(404)
    path.unlink()
    git_commit(path, f"Delete {slug}")
    next_url = request.form.get("next", "")
    if (not next_url.startswith("/")
            or f"/page/{slug}" in next_url
            or f"/edit/{slug}" in next_url):
        next_url = "/page/index"
    return redirect(next_url)


# ── History routes ────────────────────────────────────────────────────────────


@app.get("/history/<path:slug>")
def history_page_route(slug):
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

    if entries:
        rows = "".join(
            f'<tr>'
            f'<td class="hist-date">{e["date"]}</td>'
            f'<td class="hist-rel">{html.escape(e["rel"])}</td>'
            f'<td class="hist-msg">{html.escape(e["msg"])}</td>'
            f'<td style="white-space:nowrap">'
            f'<a href="/diff/{slug}/{e["sha"]}" class="btn">Diff</a> '
            f'<a href="/revision/{slug}/{e["sha"]}" class="btn">View</a></td>'
            f'</tr>'
            for e in entries
        )
        table = f'<table class="hist-table"><tbody>{rows}</tbody></table>'
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


@app.get("/revision/<path:slug>/<sha>")
def revision_view(slug, sha):
    if not GIT_ENABLED:
        abort(404)
    if not SHA_RE.match(sha):
        abort(400)
    path = safe_path(slug)
    if path is None:
        abort(400)
    content = git_show(sha, path)
    restore_sha = sha
    if content is None:
        # Deletion commit — file absent at this SHA; use parent
        r_par = _git("git", "rev-parse", "--verify", f"{sha}^")
        if r_par.returncode == 0:
            restore_sha = r_par.stdout.strip()
            content = git_show(restore_sha, path)
        if content is None:
            abort(404)
    title = page_title(content, slug)
    page_exists = path.exists()
    if page_exists:
        back_link = f'<a href="/history/{slug}" class="btn">← History</a>'
        breadcrumb = build_breadcrumb(slug, title, ('History', f'/history/{slug}'), (sha[:7], None))
    else:
        back_link = f'<a href="/page/_deleted" class="btn">← Deleted pages</a>'
        breadcrumb = build_breadcrumb('_deleted', 'Deleted Pages', (title, None))
    body = f"""{breadcrumb}
<header class="page-header">
  <h1>{html.escape(title)}</h1>
  <div class="page-header-actions">
    <form method="post" action="/restore/{slug}/{restore_sha}" style="display:inline">
      <button type="submit" class="btn btn-primary">Restore</button>
    </form>
    {back_link}
  </div>
</header>
<div class="revision-banner">Revision <code>{sha[:7]}</code> — restoring will create a new commit.</div>
<article class="page-content">
  {render_md(strip_h1(content), slug=slug)}
</article>"""
    return shell(f"Revision {sha[:7]} — {title}", body)


@app.get("/diff/<path:slug>/<sha>")
def diff_view(slug, sha):
    if not GIT_ENABLED:
        abort(404)
    if not SHA_RE.match(sha):
        abort(400)
    path = safe_path(slug)
    if path is None:
        abort(400)
    diff = git_diff(sha, path)
    if diff is None:
        abort(404)
    title = slug_display_name(slug)
    lines = []
    for line in diff.splitlines():
        if line.startswith('+') and not line.startswith('+++'):
            cls = 'diff-add'
        elif line.startswith('-') and not line.startswith('---'):
            cls = 'diff-del'
        elif line.startswith('@@'):
            cls = 'diff-hunk'
        else:
            cls = 'diff-ctx'
        lines.append(f'<span class="{cls}">{html.escape(line)}</span>')
    diff_html = ''.join(lines) or '<span class="diff-ctx">(no changes)</span>'
    page_exists = path.exists()
    if page_exists:
        back_link = f'<a href="/history/{slug}" class="btn">← History</a>'
        breadcrumb = build_breadcrumb(slug, title, ('History', f'/history/{slug}'), (f'Diff {sha[:7]}', None))
    else:
        back_link = f'<a href="/page/_deleted" class="btn">← Deleted pages</a>'
        breadcrumb = build_breadcrumb('_deleted', 'Deleted Pages', (title, f'/history/{slug}'), (f'Diff {sha[:7]}', None))
    body = f"""{breadcrumb}
<header class="page-header">
  <h1>Diff <code>{sha[:7]}</code> — {html.escape(title)}</h1>
  <div class="page-header-actions">
    <form method="post" action="/restore/{slug}/{sha}" style="display:inline">
      <button type="submit" class="btn btn-primary">Restore</button>
    </form>
    {back_link}
  </div>
</header>
<pre class="diff-view">{diff_html}</pre>"""
    return shell(f"Diff {sha[:7]} — {title}", body)


@app.post("/restore/<path:slug>/<sha>")
def restore_revision(slug, sha):
    if not GIT_ENABLED:
        abort(404)
    if not SHA_RE.match(sha):
        abort(400)
    path = safe_path(slug)
    if path is None:
        abort(400)
    content = git_show(sha, path)
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
        content = md_file.read_text(encoding="utf-8")
        stat = md_file.stat()
        mtime = stat.st_mtime
        pages.append({
            "slug": slug,
            "title": page_title(content, slug),
            "links": counts.get(slug, 0),
            "mtime": int(mtime),
            "created": fmt_date(getattr(stat, "st_birthtime", stat.st_ctime)),
            "edited": fmt_date(mtime),
        })
    pages.sort(key=lambda p: p["title"].lower())
    # Inject virtual special pages
    pages.insert(0, {"slug": "_scratch", "title": "Scratch", "links": counts.get("_scratch", 0), "mtime": 0})
    pages.insert(0, {"slug": "_daily", "title": "Daily", "links": counts.get("_daily", 0), "mtime": 0})
    if GIT_ENABLED:
        pages.insert(0, {"slug": "_deleted", "title": "Deleted Pages", "links": 0, "mtime": 0})
    return jsonify(pages)


# ── Search ────────────────────────────────────────────────────────────────────


def do_search(q: str) -> list[dict]:
    q_lower = q.lower()
    results = []

    for md_file, slug in iter_pages():
        content = md_file.read_text(encoding="utf-8")
        content_lower = content.lower()
        title = page_title(content, slug)

        score = (10 if q_lower in title.lower() else 0) + content_lower.count(q_lower)
        if score == 0:
            continue

        # Build excerpt around first match
        idx = content_lower.find(q_lower)
        if idx == -1:
            excerpt = html.escape(content[:150].replace("\n", " ")) + "…"
        else:
            start = max(0, idx - 60)
            end = min(len(content), idx + 90)
            snippet = content[start:end].replace("\n", " ")
            # Find match inside snippet and bold it
            snip_lower = snippet.lower()
            mi = snip_lower.find(q_lower)
            if mi != -1:
                pre = html.escape(snippet[:mi])
                match = html.escape(snippet[mi : mi + len(q)])
                post = html.escape(snippet[mi + len(q) :])
                excerpt = ("…" if start > 0 else "") + f"{pre}<mark>{match}</mark>{post}" + ("…" if end < len(content) else "")
            else:
                excerpt = html.escape(snippet)

        results.append({"slug": slug, "title": title, "excerpt": excerpt, "score": score})

    # Include task results from deck
    task_results = deck_module.search_tasks(q)
    for tr in task_results:
        results.append(tr)

    results.sort(key=lambda r: -r["score"])
    for r in results:
        del r["score"]
    return results[:15]


@app.get("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    return jsonify(do_search(q))


@app.get("/search")
def search_page():
    q = request.args.get("q", "").strip()
    results = do_search(q) if q else []

    if results:
        cards = "".join(
            f"""<div class="search-result-card">
  <a href="/page/{r['slug']}">{html.escape(r['title'])}</a>
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
    idx = int(request.form.get("idx", -1))
    if idx < 0:
        abort(400)
    content = path.read_text(encoding="utf-8")
    path.write_text(toggle_nth_checkbox(content, idx), encoding="utf-8")
    return "", 204


@app.post("/api/preview")
def api_preview():
    text = request.get_data(as_text=True)
    preview_slug = request.args.get("slug", "")
    return render_md(text, slug=preview_slug)


@app.get("/api/pages")
def api_pages():
    return jsonify(all_slugs())


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


@app.get("/_assets/<filename>")
def serve_upload(filename):
    return send_from_directory(UPLOADS_DIR, filename)


# ── Uncommitted changes ───────────────────────────────────────────────────────

@app.get("/uncommitted")
def uncommitted_page():
    """Show all files with uncommitted changes."""
    if not GIT_ENABLED:
        abort(404)

    files = git_uncommitted_files()
    file_list = []
    for f in files:
        filepath = f["filepath"]
        if filepath.endswith('.md') and not filepath.startswith('_assets'):
            slug = filepath[:-3]
            fpath = WIKI_DIR / filepath
            title = page_title(fpath.read_text(encoding="utf-8"), slug) if fpath.exists() else slug_display_name(slug)
            file_list.append({"slug": slug, "title": title, "filepath": filepath, "mtime": f["mtime"], "rel": f["rel"]})
        else:
            file_list.append({"slug": None, "title": filepath, "filepath": filepath, "mtime": f["mtime"], "rel": f["rel"]})

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
        commit_all_btn = '<button type="button" class="btn btn-primary" id="commit-all-btn" style="margin-top: 1rem;">Commit All</button>'
    else:
        table = '<p class="hist-empty">No uncommitted changes</p>'
        commit_all_btn = ''

    body = f"""{build_breadcrumb('uncommitted', 'Uncommitted Changes')}
<header class="page-header">
  <h1>Uncommitted Changes</h1>
</header>
{table}
{commit_all_btn}"""
    return shell("Uncommitted Changes", body)


@app.get("/diff-uncommitted/<path:slug>")
def diff_uncommitted(slug):
    """Show uncommitted changes for a file."""
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

    lines = []
    for line in diff.splitlines():
        if line.startswith('+') and not line.startswith('+++'):
            cls = 'diff-add'
        elif line.startswith('-') and not line.startswith('---'):
            cls = 'diff-del'
        elif line.startswith('@@'):
            cls = 'diff-hunk'
        else:
            cls = 'diff-ctx'
        lines.append(f'<span class="{cls}">{html.escape(line)}</span>')
    diff_html = ''.join(lines) or '<span class="diff-ctx">(no changes)</span>'

    commit_btn = (
        f'<form class="commit-file-form" style="display:inline"'
        f' data-filepath="{html.escape(rel)}"'
        f' data-title="{html.escape(title)}"'
        f' data-on-success="/uncommitted">'
        f'<button type="submit" class="btn btn-primary">Commit</button>'
        f'</form>'
    )
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
    files = git_uncommitted_files()
    result = []
    for f in files:
        filepath = f["filepath"]
        # Convert filepath to slug (remove .md extension)
        if filepath.endswith('.md') and not filepath.startswith('_assets'):
            slug = filepath[:-3]
            # Try to get title from file if it exists
            path = WIKI_DIR / filepath
            if path.exists():
                content = path.read_text(encoding="utf-8")
                title = page_title(content, slug)
            else:
                title = slug_display_name(slug)
            result.append({"slug": slug, "title": title, "filepath": filepath})
        else:
            # Asset or other file
            result.append({"slug": None, "title": filepath, "filepath": filepath})
    return jsonify(result)


@app.post("/api/commit-all")
def api_commit_all():
    """Commit all uncommitted files, one commit per file."""
    if not GIT_ENABLED:
        return jsonify({"error": "Git not enabled"}), 400
    
    files = git_uncommitted_files()
    committed = []
    
    for f in files:
        filepath = f["filepath"]
        # Generate appropriate commit message
        if filepath.endswith('.md'):
            slug = filepath[:-3]
            message = f"Save {slug}"
        else:
            message = f"Update {filepath}"
        
        git_commit_file(filepath, message)
        committed.append(filepath)
    
    return jsonify({"committed": len(committed), "files": committed})


@app.post("/api/commit-file")
def api_commit_file():
    """Commit a single file."""
    if not GIT_ENABLED:
        return jsonify({"error": "Git not enabled"}), 400
    
    data = request.get_json()
    filepath = data.get("filepath", "")
    message = data.get("message", "")
    
    if not filepath:
        return jsonify({"error": "No filepath provided"}), 400
    
    # Verify file is in uncommitted list
    uncommitted = git_uncommitted_files()
    uncommitted_filepaths = [f["filepath"] for f in uncommitted]
    if filepath not in uncommitted_filepaths:
        return jsonify({"error": "File is not uncommitted"}), 400
    
    git_commit_file(filepath, message or f"Update {filepath}")
    return jsonify({"success": True})


# ── Task App ──────────────────────────────────────────────────────────────────
# All task/deck logic has been moved to deck.py module.
# Routes are registered via the deck blueprint above.

# Backward-compatibility alias for tests
_read_deck = deck_module._read_deck


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Always bind to all interfaces so Docker's network bridge can forward
    # connections. LAN exposure is controlled by compose.yaml's port mapping
    # (127.0.0.1:5000:5000 = localhost only, 0.0.0.0:5000:5000 = LAN).
    # The host-header check (ALLOWED_HOSTS) is the DNS-rebinding defence.
    app.run(host="0.0.0.0", port=5000)
