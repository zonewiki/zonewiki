import hmac
import html
import mimetypes
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, request, send_from_directory, session, url_for
from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin
from mdit_py_plugins.tasklists import tasklists_plugin

app = Flask(__name__, static_folder="public", static_url_path="/public")
APP_NAME = "Leaf"

LEAF_PASSWORD = os.environ.get("LEAF_PASSWORD", "")
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32)
app.config["PERMANENT_SESSION_LIFETIME"] = 30 * 24 * 60 * 60  # 30 days

ALLOWED_HOSTS = {"localhost:5000", "127.0.0.1:5000"}

@app.before_request
def check_host():
    if request.host not in ALLOWED_HOSTS:
        abort(400)

@app.before_request
def check_auth():
    if not LEAF_PASSWORD:
        return
    if request.endpoint in ("login", "logout", "static"):
        return
    if not session.get("authenticated"):
        next_url = request.path
        return redirect(url_for("login", next=next_url))

WIKI_DIR = Path(os.environ.get("WIKI_DATA", Path(__file__).parent / "data"))
PAGES_DIR = WIKI_DIR
UPLOADS_DIR = WIKI_DIR / "assets"
PAGES_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


# ── Git version control ────────────────────────────────────────────────────────

GIT_ENABLED = shutil.which("git") is not None
SHA_RE = re.compile(r'^[0-9a-f]{7,40}$')


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
            gitignore.write_text("assets/\n", encoding="utf-8")
        _git("git", "add", ".")
        _git("git", "commit", "--allow-empty", "-m", "Initial import")
    else:
        r = _git("git", "config", "user.name")
        if not r.stdout.strip():
            _git("git", "config", "user.email", "leaf@localhost")
            _git("git", "config", "user.name", "Leaf")


def git_commit(path: Path, message: str) -> None:
    if not GIT_ENABLED:
        return
    rel = str(path.relative_to(WIKI_DIR))
    _git("git", "add", "--", rel)
    _git("git", "commit", "-m", message)


def git_commit_rename(old_path: Path, new_path: Path, message: str) -> None:
    if not GIT_ENABLED:
        return
    _git("git", "add", "--",
         str(old_path.relative_to(WIKI_DIR)),
         str(new_path.relative_to(WIKI_DIR)))
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


git_setup()

HOME_LINK = f"""<a href="/" class="breadcrumb-home" aria-label="{APP_NAME} home">
  <svg class="site-logo" viewBox="0 0 16 16" aria-hidden="true">
    <path d="M12.8 1.7C9.6 1.8 7.1 3 5.5 5.1c-1.8 2.4-2.2 5.3-2.2 7.5 0 .4.3.7.7.7 2.2 0 5.1-.4 7.5-2.2 2.1-1.6 3.3-4.1 3.4-7.3 0-.4-.3-.8-.7-.8-.5-.1-.9-.1-1.4-.1Zm-6 9.5c-.2 0-.4-.1-.5-.3-.2-.3-.2-.7.2-1l5.1-3.8c.3-.2.7-.1 1 .2.2.3.1.7-.2 1l-5.1 3.8c-.2.1-.3.1-.5.1Z" fill="currentColor"/>
  </svg>
  <span>{APP_NAME}</span>
</a>"""

md = MarkdownIt(options_update={"html": True}).enable("table").use(tasklists_plugin).use(anchors_plugin)


# ── Helpers ───────────────────────────────────────────────────────────────────


def to_slug(name: str) -> str:
    """Slugify a name, preserving '/' as a path separator."""
    parts = name.strip().split('/')
    slugged = []
    for part in parts:
        s = part.strip().lower()
        s = re.sub(r"[^a-z0-9]+", "-", s)
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


def list_scratches() -> list[dict]:
    """Return all scratch pages sorted newest first."""
    scratches = []
    for md_file in PAGES_DIR.glob("*.md"):
        m = SCRATCH_RE.match(md_file.stem)
        if not m:
            continue
        ts_str, _ = m.group(1), m.group(2)
        content = md_file.read_text(encoding="utf-8")
        ts = datetime.strptime(ts_str, "%Y-%m-%d_%H-%M-%S")
        scratches.append({
            "slug": md_file.stem,
            "title": page_title(content, md_file.stem),
            "ts": ts,
            "date": ts.strftime("%Y-%m-%d %H:%M"),
        })
    scratches.sort(key=lambda n: n["ts"], reverse=True)
    return scratches


def page_title(content: str, slug: str) -> str:
    m = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    return m.group(1).strip() if m else slug.split('/')[-1].replace("-", " ").title()


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
        return f'<a href="/page/{slug}" class="{css}">{label}</a>'

    return re.sub(r"\[\[([^\[\]]+)\]\]", replace, text)


_TOC_PLACEHOLDER = "LEAFTOCPLACEHOLDER"
_TOC_RE = re.compile(r'^\[\[toc\]\]\s*$', re.IGNORECASE | re.MULTILINE)


def _heading_id(text: str) -> str:
    """Match mdit-py-plugins anchors_plugin default slugify."""
    return re.sub(r'[^\w\- ]+', '', text, flags=re.UNICODE).strip().lower().replace(' ', '-')


def _build_toc(text: str) -> str:
    headings = re.findall(r'^(#{2,3})\s+(.+)', text, re.MULTILINE)
    if not headings:
        return ''
    items = []
    for hashes, title in headings:
        level = len(hashes)
        hid = _heading_id(title.strip())
        cls = ' class="toc-h3"' if level == 3 else ''
        items.append(f'<li{cls}><a href="#{hid}">{html.escape(title.strip())}</a></li>')
    return '<nav class="toc"><ul>\n' + '\n'.join(items) + '\n</ul></nav>'


def render_md(text: str) -> str:
    toc_html = ''
    if _TOC_RE.search(text):
        toc_html = _build_toc(text)
        text = _TOC_RE.sub(_TOC_PLACEHOLDER, text)
    result = md.render(process_wikilinks(text))
    result = re.sub(r'\s+disabled(?:="[^"]*")?', '', result)
    if toc_html:
        result = result.replace(f'<p>{_TOC_PLACEHOLDER}</p>', toc_html)
    return result


def toggle_nth_checkbox(content: str, idx: int) -> str:
    matches = list(re.finditer(r'\[([xX ])\]', content))
    if idx >= len(matches):
        return content
    m = matches[idx]
    new_char = 'x' if m.group(1) == ' ' else ' '
    return content[:m.start()] + f'[{new_char}]' + content[m.end():]


def strip_h1(content: str) -> str:
    """Remove the first H1 line so it isn't duplicated below the page header."""
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if line.strip():
            if re.match(r"^#\s+", line):
                del lines[i]
            break
    return "\n".join(lines)


def all_backlink_counts() -> dict[str, int]:
    """Return a mapping of slug → number of pages that link to it."""
    counts: dict[str, int] = {}
    for md_file, file_slug in iter_pages():
        content = md_file.read_text(encoding="utf-8")
        seen: set[str] = set()
        for m in re.finditer(r"\[\[([^\[\]]+)\]\]", content):
            target = to_slug(m.group(1).split("|")[0].strip())
            if target != file_slug and target not in seen:
                counts[target] = counts.get(target, 0) + 1
                seen.add(target)
    return counts


def find_backlinks(slug: str) -> list[dict]:
    """Return pages that contain a wikilink resolving to slug."""
    results = []
    for md_file, file_slug in iter_pages():
        if file_slug == slug:
            continue
        content = md_file.read_text(encoding="utf-8")
        for m in re.finditer(r"\[\[([^\[\]]+)\]\]", content):
            page_name = m.group(1).split("|")[0].strip()
            if to_slug(page_name) == slug:
                results.append({"slug": file_slug, "title": page_title(content, file_slug)})
                break
    results.sort(key=lambda r: r["title"].lower())
    return results


def update_references(old_slug: str, new_slug: str) -> None:
    """Rewrite [[wikilinks]] across all pages after a rename."""
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

        new_content = re.sub(r"\[\[([^\[\]]+)\]\]", replace, content)
        if new_content != content:
            md_file.write_text(new_content, encoding="utf-8")


def shell(title: str, body: str, *, slug: str = "", editor: bool = False, extra_head: str = "") -> str:
    edit_btn = f'<a href="/edit/{slug}" class="btn">Edit</a>' if slug and not editor else ""
    body_class = ' class="editor-page"' if editor else ""
    safe_title = html.escape(title)
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
  {extra_head}
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
    <p class="scratch-label">New scratch</p>
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
      <tr><td><kbd>Ctrl+1</kbd></td><td>New scratch</td></tr>
      <tr><td><kbd>Ctrl+Alt+0</kbd></td><td>Go to Home</td></tr>
      <tr><td><kbd>Ctrl+Alt+1</kbd></td><td>Go to Scratch</td></tr>
      <tr><td><kbd>Ctrl+Alt+2</kbd></td><td>Go to Tasks</td></tr>
      <tr><td><kbd>Ctrl+Alt+3</kbd></td><td>Go to State</td></tr>
      <tr><td><kbd>F2</kbd></td><td>Toggle preview (editor)</td></tr>
      <tr><td><kbd>Ctrl+S</kbd></td><td>Save (editor)</td></tr>
      <tr><td><kbd>Ctrl+Shift+X</kbd></td><td>Toggle checkbox (editor)</td></tr>
      <tr><td><kbd>e</kbd></td><td>Edit current page</td></tr>
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
  {'<form method="post" action="/logout" class="signout-form"><button class="btn" type="submit">Sign out</button></form>' if LEAF_PASSWORD else ''}
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
    scratches = list_scratches()
    if scratches:
        items = "".join(
            f'<li><a href="/page/{n["slug"]}">{html.escape(n["title"])}</a>'
            f'<span class="note-date">{n["date"]}</span></li>'
            for n in scratches
        )
        body_inner = f"<ul class='notes-list'>{items}</ul>"
    else:
        body_inner = "<p>No scratches yet.</p>"
    body = f"""<div class="breadcrumb">
  {HOME_LINK}
  <span class="breadcrumb-sep">›</span>
  <span>Scratch</span>
</div>
<header class="page-header"><h1>Scratch</h1></header>
<article class="page-content">{body_inner}</article>"""
    return shell("Scratch", body)


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
        return redirect(f"/edit/{slug}")

    content = path.read_text(encoding="utf-8")
    title = page_title(content, slug)
    backlinks = find_backlinks(slug)
    if backlinks:
        bl_items = "".join(
            f'<li><a href="/page/{r["slug"]}">{html.escape(r["title"])}</a></li>'
            for r in backlinks
        )
        bl_html = f'<section class="backlinks"><h3>Backlinks</h3><ul>{bl_items}</ul></section>'
    else:
        bl_html = '<section class="backlinks backlinks-empty"><h3>Backlinks</h3><p>No pages link here.</p></section>'

    # Build nested breadcrumb
    parts = slug.split('/')
    sep = '<span class="breadcrumb-sep">›</span>'
    crumbs = [HOME_LINK]
    for i, part in enumerate(parts[:-1]):
        ancestor = '/'.join(parts[:i + 1])
        crumbs.append(f'<a href="/page/{ancestor}">{html.escape(part)}</a>')
    crumbs.append(f'<span>{html.escape(title)}</span>')
    breadcrumb = f'<div class="breadcrumb">{sep.join(crumbs)}</div>'

    hist_link = f'<a href="/history/{slug}" class="hist-link">History</a>' if GIT_ENABLED else ""
    body = f"""{breadcrumb}
<header class="page-header">
  <h1>{html.escape(title)}</h1>
  <div class="page-header-actions">
    {hist_link}
    <a href="/edit/{slug}" class="btn">Edit</a>
  </div>
</header>
<article class="page-content">
  {render_md(strip_h1(content))}
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
    else:
        title = slug.split('/')[-1].replace("-", " ").title()
        content = f"# {title}\n\n"
    safe_content = html.escape(content)
    safe_slug = html.escape(slug)
    safe_title = html.escape(title)
    can_rename = slug != "index"
    can_delete = slug != "index"
    rename_btn = '<button type="button" class="btn" id="rename-toggle">Rename…</button>' if can_rename else ""
    delete_btn = f'<button type="submit" form="delete-form" class="btn btn-danger" id="delete-btn" data-title="{safe_title}">Delete</button>' if can_delete else ""
    hist_btn = f'<a href="/history/{safe_slug}" class="hist-link">History</a>' if GIT_ENABLED else ""
    delete_form = f'<form id="delete-form" method="post" action="/delete/{safe_slug}" style="display:none"></form>' if can_delete else ""
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
  </div>""" if can_rename else ""
    body = f"""<p class="editor-mobile-msg">The editor is not available on small screens. <a href="/page/{safe_slug}">View page instead.</a></p>
<div class="editor-wrap">
  <div class="editor-pane">
    <div class="editor-toolbar">
      <span class="slug-label">{safe_slug}</span>
      <button type="submit" form="editor-form" class="btn btn-primary">Save</button>
      <a href="/page/{safe_slug}" class="btn">Cancel</a>
      {rename_btn}
      {hist_btn}
      {delete_btn}
    </div>
    {delete_form}
    {rename_bar}
    <form id="editor-form" method="post" action="/edit/{safe_slug}">
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


@app.post("/edit/<path:slug>")
def save_page(slug):
    path = safe_path(slug)
    if path is None:
        abort(400)
    content = request.form.get("content", "")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    git_commit(path, f"Update {slug}")
    return redirect(f"/page/{slug}")


@app.post("/api/save/<path:slug>")
def api_save(slug):
    path = safe_path(slug)
    if path is None:
        abort(400)
    content = request.form.get("content", "")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    git_commit(path, f"Update {slug}")
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
    old_path.rename(new_path)
    update_references(slug, new_slug)
    git_commit_rename(old_path, new_path, f"Rename {slug} to {new_slug}")
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
    return redirect("/page/index")


# ── History routes ────────────────────────────────────────────────────────────


@app.get("/history/<path:slug>")
def history_page_route(slug):
    if not GIT_ENABLED:
        abort(404)
    path = safe_path(slug)
    if path is None:
        abort(400)
    if not path.exists():
        return redirect(f"/edit/{slug}")
    content = path.read_text(encoding="utf-8")
    title = page_title(content, slug)
    entries = git_log(path)

    if entries:
        rows = "".join(
            f'<tr>'
            f'<td class="hist-date">{e["date"]}</td>'
            f'<td class="hist-rel">{html.escape(e["rel"])}</td>'
            f'<td class="hist-msg">{html.escape(e["msg"])}</td>'
            f'<td><a href="/revision/{slug}/{e["sha"]}" class="btn">View</a></td>'
            f'</tr>'
            for e in entries
        )
        table = f'<table class="hist-table"><tbody>{rows}</tbody></table>'
    else:
        table = '<p class="hist-empty">No history yet — history is recorded from this point forward.</p>'

    sep = '<span class="breadcrumb-sep">›</span>'
    body = f"""<div class="breadcrumb">
  {HOME_LINK}{sep}<a href="/page/{slug}">{html.escape(title)}</a>{sep}<span>History</span>
</div>
<header class="page-header">
  <h1>History — {html.escape(title)}</h1>
  <a href="/page/{slug}" class="btn">← Page</a>
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
    if content is None:
        abort(404)
    title = page_title(content, slug)
    sep = '<span class="breadcrumb-sep">›</span>'
    body = f"""<div class="breadcrumb">
  {HOME_LINK}{sep}<a href="/page/{slug}">{html.escape(title)}</a>{sep}<a href="/history/{slug}">History</a>{sep}<span>{sha[:7]}</span>
</div>
<header class="page-header">
  <h1>{html.escape(title)}</h1>
  <div class="page-header-actions">
    <form method="post" action="/restore/{slug}/{sha}" style="display:inline">
      <button type="submit" class="btn btn-primary">Restore</button>
    </form>
    <a href="/history/{slug}" class="btn">← History</a>
  </div>
</header>
<div class="revision-banner">Revision <code>{sha[:7]}</code> — restoring will create a new commit.</div>
<article class="page-content">
  {render_md(strip_h1(content))}
</article>"""
    return shell(f"Revision {sha[:7]} — {title}", body)


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

    results.sort(key=lambda r: -r["score"])
    for r in results:
        del r["score"]
    return results[:10]


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
    return render_md(text)


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
    stem = Path(original_name).stem
    ext = Path(original_name).suffix.lower()
    # Find a unique filename, appending -2, -3, ... if needed
    filename = stem + ext
    counter = 2
    while (UPLOADS_DIR / filename).exists():
        filename = f"{stem}-{counter}{ext}"
        counter += 1
    f.save(UPLOADS_DIR / filename)
    return jsonify({"url": f"/uploads/{filename}"})


@app.get("/uploads/<filename>")
def serve_upload(filename):
    return send_from_directory(UPLOADS_DIR, filename)


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
