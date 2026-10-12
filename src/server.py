"""Leaf's web app: the Flask app, its request checks and the routes of
the wiki (pages, editor, scratch notes, search, uploads, the deck page,
/mcp). Version history routes are in history.py, the deck API in deck.py."""

import hmac
import ipaddress
import json
import mimetypes
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse

from flask import Flask, abort, jsonify, redirect, request, send_from_directory, session, url_for
from flask.sessions import SecureCookieSessionInterface

import config
import deck
import decksyntax
import deckstore
import mcp_server
import retrieval
import history
from mdtext import (
    add_frontmatter_tags, content_hash, is_scratch, mask_code, note_as_section, page_title,
    parse_frontmatter, plain_heading, promoted_slug, SCRATCH_RE, section_anchor,
    section_char_range, slug_display_name, splice_section, strip_h1, to_slug,
    toggle_nth_checkbox, unmask_code,
)
from pages import (
    all_backlink_counts, all_slugs, deck_lists, find_backlinks, fmt_date, hidden_page, is_protected, iter_pages, list_scratch,
    page_deck_lists, page_from_input, page_link_targets, page_meta, page_task_refs,
    safe_path, update_references, url,
)
from gitstore import run_git, git_commit, git_setup, move_page
from render import (
    render_md, render_tags_pills_html,
)
from retrieval import (
    do_search, SCRATCH_FILTER_LIMIT, SEARCH_LIMIT, SEARCH_MAX_LIMIT, SEARCH_PAGE_LIMIT,
)
import views
from views import build_breadcrumb, build_delete_dialog, FORMAT_BAR, hidden_page_view
from embed import exclude_from_git
from mcp_tools import mcp_call_tool, mcp_instructions, mcp_tool_list
from templating import macro


def _is_https() -> bool:
    """The browser's connection is HTTPS: Leaf's own (rare), or a reverse
    proxy's, which says so in X-Forwarded-Proto."""
    proto = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower()
    return request.is_secure or proto == "https"


class _SessionInterface(SecureCookieSessionInterface):
    """The login cookie is Secure (HTTPS only) when the request came over
    HTTPS, so behind an HTTPS proxy the browser never sends it in the clear;
    over plain http (localhost, the LAN) it still works. A client faking the
    header only gets a cookie its own http requests leave out."""

    def get_cookie_secure(self, app) -> bool:
        return _is_https()


def _secret_key() -> bytes:
    """The key that signs session cookies: LEAF_SECRET_KEY, else one Leaf made
    once and keeps in .leaf/secret_key in the wiki folder (owner-only, kept
    out of git), so a restart doesn't sign everyone out. A wiki folder Leaf
    can't write to gets a new key per start."""
    if os.environ.get("LEAF_SECRET_KEY"):
        return os.environ["LEAF_SECRET_KEY"].encode()
    path = config.LEAF_DATA / ".leaf" / "secret_key"
    try:
        key = path.read_bytes().strip()
        if len(key) >= 32:
            return key
    except OSError:
        pass
    key = os.urandom(32).hex().encode()
    try:
        path.parent.mkdir(exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
    except OSError:
        pass
    return key


app = Flask(__name__, static_folder=None)
app.secret_key = _secret_key()
app.session_interface = _SessionInterface()
app.config["PERMANENT_SESSION_LIFETIME"] = 30 * 24 * 60 * 60  # 30 days
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True
ALLOWED_HOSTS = {"localhost:5000", "127.0.0.1:5000"}
PUBLIC_DIR = Path(__file__).parent / "public"


# DNS rebinding: a page on evil.example whose name later resolves to
# 127.0.0.1 reaches Leaf with "Host: evil.example". Only localhost, and the
# names in LEAF_HOSTS (a reverse proxy's public name, any port), get in.
# LEAF_ALLOW_REMOTE without LEAF_HOSTS turns the check off (LAN access by IP).
@app.before_request
def check_host():
    if config.LEAF_ALLOW_REMOTE and not config.LEAF_HOSTS:
        return
    if request.host not in ALLOWED_HOSTS and config.host_name(request.host) not in config.LEAF_HOSTS:
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
    if not config.LEAF_PASSWORD:
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


# Clickjacking: another site could load Leaf invisibly in a frame and trick a
# signed-in user into clicking Delete or Commit. Only Leaf's own pages may
# frame it (a page's raw HTML may show an upload in an <iframe>).
@app.after_request
def deny_framing(response):
    csp = response.headers.get("Content-Security-Policy")
    if not csp:
        response.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    elif "frame-ancestors" not in csp:
        response.headers["Content-Security-Policy"] = csp + "; frame-ancestors 'self'"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"   # browsers without CSP
    return response


def ensure_home_page() -> None:
    """Give a wiki without index.md (a new one) its home page, committed:
    "/" opens it, and it can't be renamed or deleted."""
    path = config.LEAF_DATA / "index.md"
    if not path.exists():
        path.write_text("# Home\n\n", encoding="utf-8")
        git_commit(path, "Create index")


git_setup()
exclude_from_git(config.LEAF_DATA)   # .leaf/: the secret key (_secret_key)
ensure_home_page()


# The deck's files are in the wiki; the semantic index's worker reads tasks,
# so it starts once the deck is set up.
deckstore.init_deck(config.LEAF_DATA, config.GIT_ENABLED, git_commit)
app.register_blueprint(deck.bp)
app.register_blueprint(history.bp)
retrieval.start_semantic()


def _is_local_path(url: str) -> bool:
    """True if url is a same-origin path. "//host" and "/\\host" start with "/"
    but browsers resolve them to another origin, so reject those too."""
    return url.startswith("/") and not url.startswith(("//", "/\\"))


def _client_key() -> str:
    """Who is guessing, for the throttle: the client's address. Behind a
    reverse proxy (the request comes from a loopback or private address)
    that is the address the proxy saw and appended last to X-Forwarded-For,
    so clients aren't all one."""
    addr = request.remote_addr or ""
    forwarded = [a.strip() for a in request.headers.get("X-Forwarded-For", "").split(",") if a.strip()]
    try:
        local = ipaddress.ip_address(addr).is_private
    except ValueError:
        local = False
    return forwarded[-1] if forwarded and local else addr


class Throttle:
    """Wrong passwords (and MCP tokens) slow the guesser down: after `free`
    failures in a row from one client, each further try waits, 1 minute,
    then doubling up to an hour. A success clears the client; a client quiet
    for a day is forgotten. Per client, so a guesser elsewhere doesn't lock
    the owner out. In memory: a restart forgets."""

    def __init__(self, free: int = 5, first: float = 60, longest: float = 3600):
        self.free, self.first, self.longest = free, first, longest
        self._fails: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def wait(self, key: str) -> float:
        """Seconds before `key` may try again (0: now)."""
        with self._lock:
            count, last = self._fails.get(key, (0, 0.0))
        if count < self.free:
            return 0.0
        delay = min(self.first * 2 ** (count - self.free), self.longest)
        return max(0.0, last + delay - time.time())

    def failed(self, key: str) -> None:
        now = time.time()
        with self._lock:
            self._fails = {k: v for k, v in self._fails.items() if now - v[1] < 86400}
            count, _ = self._fails.get(key, (0, 0.0))
            self._fails[key] = (count + 1, now)

    def succeeded(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


LOGIN_THROTTLE = Throttle()
MCP_THROTTLE = Throttle()


def _same_secret(given: str, secret: str) -> bool:
    """Constant-time comparison; as bytes, so non-ASCII passwords work."""
    return hmac.compare_digest(given.encode(), secret.encode())


def _wait_message(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    return f"Too many wrong attempts. Try again in {minutes} minute{'s' if minutes > 1 else ''}."


@app.get("/login")
@app.post("/login")
def login():
    if not config.LEAF_PASSWORD:
        return redirect("/")
    error, status = "", 200
    if request.method == "POST":
        key = _client_key()
        wait = LOGIN_THROTTLE.wait(key)
        if wait:
            # Refused unchecked, even the right password: guessing doesn't
            # go on in the meantime.
            error, status = _wait_message(wait), 429
        elif _same_secret(request.form.get("password", ""), config.LEAF_PASSWORD):
            LOGIN_THROTTLE.succeeded(key)
            session.permanent = True
            session["authenticated"] = True
            next_url = request.args.get("next", "/")
            if not _is_local_path(next_url):
                next_url = "/"
            return redirect(next_url)
        else:
            LOGIN_THROTTLE.failed(key)
            error = "Incorrect password."
    return views.page("login.html", "Sign in", action_buttons=False, error=error,
                      next_url=request.args.get("next", "")), status


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
def index():
    return redirect("/page/index")


@app.get("/page/_scratch")
def scratch_page():
    # The filter runs the search (/api/search?scratch=1: its ranking and
    # query syntax) over scratch notes only; js/search.js (initScratchFilter)
    # shows the matches in ranked order with their excerpts. ?q= keeps the
    # query (Back returns to the filtered list).
    return views.page("scratch.html", "Scratch", notes=list_scratch(), q=request.args.get("q", ""),
                      breadcrumb_html=build_breadcrumb("_scratch", "Scratch"))


@app.get("/api/page-preview/<path:slug>")
def api_page_preview(slug):
    """A page rendered for the search modal's preview pane: {title, html}.
    Heading ids become data-pid (the page underneath has its own ids), and
    deck embeds are left out (an app has no place in a preview)."""
    path = safe_path(slug)
    if path is None or not path.exists() or hidden_page(slug):
        abort(404)
    content = path.read_text(encoding="utf-8")
    masked, stash = mask_code(content)
    masked = decksyntax.DECK_RE.sub("*(deck)*", masked)
    html_out = render_md(strip_h1(unmask_code(masked, stash)))
    html_out = re.sub(r'(<h[1-6]\b[^>]*?) id="', r'\1 data-pid="', html_out)
    return jsonify({"title": page_title(content, slug), "html": html_out})


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
        # A page deleted in git history offers to come back.
        if config.GIT_ENABLED:
            rel = slug + ".md"
            r = run_git("git", "log", "-1", "--format=%H\t%P\t%as", "--diff-filter=D", "--", rel)
            if r.returncode == 0 and r.stdout.strip():
                parts = r.stdout.strip().split('\t', 2)
                parent_sha = parts[1].split()[0] if len(parts) > 1 and parts[1].strip() else None
                if parent_sha:
                    r2 = run_git("git", "show", f"{parent_sha}:{rel}")
                    title = page_title(r2.stdout, slug) if r2.returncode == 0 else slug_display_name(slug)
                    return views.page("deleted_page.html", title, slug=slug, sha=parent_sha,
                                      date=parts[2] if len(parts) > 2 else "",
                                      breadcrumb_html=build_breadcrumb(slug, title))
        return redirect(url("edit", slug))

    content = path.read_text(encoding="utf-8")
    title = page_title(content, slug)
    backlinks = [b for b in find_backlinks(slug) if not hidden_page(b["slug"])]
    # Tasks the page shows inline already aren't repeated under Linked tasks.
    inline = page_task_refs(content)
    task_backlinks = [t for t in deckstore.find_task_backlinks(slug) if t["id"] not in inline]
    # A section a task links to is named as the page's heading says it.
    heading_names = {section_anchor(h): plain_heading(h)
                     for h in re.findall(r"^#{2,3}\s+(.+?)\s*#*\s*$", mask_code(content)[0], re.M)}
    for t in task_backlinks:
        if t.get("anchor") in heading_names:
            t["section"] = heading_names[t["anchor"]]
    page_lists = [name for name, _ in page_deck_lists(content)]
    sections = views.link_sections(backlinks, task_backlinks, page_lists)
    if related := views.related_section(slug, path, backlinks):
        sections.append(related)
    chip = views.backlinks_chip(backlinks)

    # Viewing a page from the uncommitted-changes list: Commit instead of Edit.
    from_uncommitted = request.args.get("from") == "uncommitted" and config.GIT_ENABLED
    if from_uncommitted:
        breadcrumb = build_breadcrumb("uncommitted", "Uncommitted Changes", (title, None), right_html=chip)
        article_html = render_md(strip_h1(content))
        meta_row_html, menu = "", None
    else:
        qs = ("?" + request.query_string.decode()) if request.query_string else ""
        breadcrumb = build_breadcrumb(slug, title, right_html=chip, query_string=qs)
        menu = views.page_menu(slug, title, request.args.get("rename_error", ""),
                               request.args.get("promote_error", ""), request.args.get("append_error", ""))
        changed, commit = views.last_changed(slug, path, title)
        fm_meta, _ = parse_frontmatter(content)
        meta_row_html = macro("partials/page.html", "meta_row")(
            render_tags_pills_html(fm_meta.get("tags", [])), fm_meta.get("tldr", ""), changed, commit)
        article_html = render_md(strip_h1(content), show_tags=False, show_tldr=False,
                                 edit_slug=slug, section_tasks=task_backlinks)
    return views.page(
        "view.html", title, wide=True, slug=slug, from_uncommitted=from_uncommitted,
        breadcrumb_html=breadcrumb, delete_dialog_html=build_delete_dialog(slug, title),
        rail_toc_html=views.rail_toc_items(article_html), meta_row_html=meta_row_html,
        article_html=article_html, link_sections=sections, outlinks=views.outlinks(slug, path),
        menu=menu)


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

    editable = not is_new and section is None   # a whole existing page
    # Nothing but the title yet (a new page, a new scratch note): the cursor
    # starts after it, ready for the body.
    only_title = section is None and re.fullmatch(r"# [^\n]*\n*", content) is not None
    return views.page(
        "edit.html", f"Edit — {title}", editor=True, slug=slug, page_title=title, content=content,
        is_new=is_new, only_title=only_title, section=section, start_char=start_char, end_char=end_char,
        # The file's hash for conflict detection, whole page or section edit.
        file_hash="" if is_new else file_hash_val if section is not None else content_hash(content),
        can_rename=editable and not is_protected(slug), can_delete=editable and not is_protected(slug),
        git=config.GIT_ENABLED, rename_error=request.args.get("rename_error", ""),
        paste=views.paste_hint(request.args.get("paste", ""), request.args.get("copied") == "1"),
        breadcrumb_html=build_breadcrumb(slug, title, (f"§ {section}", None) if section is not None else ("Edit", None)),
        format_bar_html=FORMAT_BAR, deck_html=deck.build_deck_embed({}, headless=True))


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
        result = splice_section(current, file_hash, start_char, end_char, content)
        if result is None:
            return redirect(f"{url('edit', slug)}?conflict=1")
        content = result[0]
    elif file_hash and current is not None:
        # Full-file edit with hash — check for conflict
        if content_hash(current) != file_hash:
            return redirect(f"{url('edit', slug)}?conflict=1")
    # Skip write if content is unchanged, but still commit if file is dirty
    if current != content:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    git_commit(path, f"Update {slug}")
    return redirect(url("page", slug))


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
        content, new_hash, new_start, new_end = splice_section(
            current, content_hash(current), fs_start, fs_end, raw_content)
    elif is_section_edit:
        result = splice_section(current, file_hash, start_char, end_char, raw_content)
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
    return views.page("new.html", "New Page")


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
    return redirect(url("edit", slug))


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
    back = url("page", slug) if request.form.get("from") == "page" else url("edit", slug)
    # to_slug preserves underscores, so scratch timestamps survive a rename
    new_slug = to_slug(raw)
    if not new_slug or new_slug == slug:
        return redirect(back)
    new_path = safe_path(new_slug)
    if new_path is None:
        abort(400)
    if new_path.exists():
        return redirect(f"{back}?rename_error=A+page+named+%22{new_slug}%22+already+exists")
    move_page(old_path, new_path, slug, new_slug, f"Rename {slug} to {new_slug}")
    return redirect(url("page", new_slug))


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
    back = f"{url('page', slug)}?promote_error="
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
    link_slug = page_from_input(raw_link) if raw_link else ""
    link_path = safe_path(link_slug) if link_slug else None
    if raw_link and (not link_path or not link_path.exists() or link_slug == slug):
        return redirect(back + quote(f"There is no page “{raw_link}” to link from"))
    move_page(old_path, new_path, slug, new_slug, f"Promote {slug} to {new_slug}")
    # The page to link it from opens in the editor, the link to paste where
    # it belongs (js/pagemenu.js copied it on Move). A page linking to the note
    # already links here now: nothing to paste.
    if link_path and new_slug not in page_link_targets(
            link_path.read_text(encoding="utf-8"), link_slug):
        copied = "&copied=1" if request.form.get("copied") == "1" else ""
        return redirect(f"{url('edit', link_slug)}?paste={quote(new_slug)}{copied}")
    return redirect(url("page", new_slug))


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
    back = f"{url('page', slug)}?append_error="
    raw = request.form.get("page", "").strip()
    target = page_from_input(raw)
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
            r"^#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", mask_code(page)[0], re.M)):
        return redirect(back + quote(f"“{target}” already has a section “{heading}”"))
    section, tags = note_as_section(old_path.read_text(encoding="utf-8"), heading)
    page = add_frontmatter_tags(page, tags)
    target_path.write_text(page.rstrip("\n") + "\n\n" + section, encoding="utf-8")
    old_path.unlink()
    modified_refs = update_references(slug, target, heading)
    git_commit([old_path, target_path, *modified_refs], f"Append {slug} to {target}")
    return redirect(f"{url('page', target)}#{anchor}")


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
        title, _ = page_meta(md_file, slug)
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
    special = [{"slug": "_deleted", "title": "Deleted Pages", "links": 0}] if config.GIT_ENABLED else []
    return jsonify([{**p, "mtime": 0, "builtin": True, "special": True} for p in special]
                   + pages + [{**p, "mtime": 0, "builtin": True} for p in builtin])


def _mcp_authorized() -> bool:
    """/mcp needs LEAF_MCP_TOKEN as a bearer token. The tools write (pages,
    tasks), so /mcp is off until a token is set: open, it would let anything
    that reaches the port change the wiki, password or not."""
    auth = request.headers.get("Authorization", "")
    return bool(config.LEAF_MCP_TOKEN) and auth.startswith("Bearer ") and _same_secret(
        auth[len("Bearer "):].strip(), config.LEAF_MCP_TOKEN)


@app.route("/mcp", methods=["GET", "POST", "DELETE"])
def mcp_endpoint():
    """MCP over HTTP (see mcp_server.py): one JSON-RPC message per POST,
    answered with JSON. GET (a server-sent event stream) and DELETE (ending
    a session) aren't offered: there are no notifications and no sessions."""
    if not config.LEAF_MCP_TOKEN:
        return jsonify({"error": "MCP is off: set LEAF_MCP_TOKEN in .env and send it "
                                 "as 'Authorization: Bearer <token>'"}), 403
    # Wrong tokens count against the client like wrong passwords (their own
    # throttle: a client's MCP mistakes don't lock its browser out).
    key = _client_key()
    wait = MCP_THROTTLE.wait(key)
    if wait:
        return jsonify({"error": _wait_message(wait)}), 429, {"Retry-After": str(int(wait) + 1)}
    if not _mcp_authorized():
        MCP_THROTTLE.failed(key)
        return (jsonify({"error": "Unauthorized: wrong or missing bearer token"}), 401,
                {"WWW-Authenticate": "Bearer"})
    MCP_THROTTLE.succeeded(key)
    if request.method != "POST":
        return "", 405, {"Allow": "POST"}
    try:
        message = json.loads(request.get_data(as_text=True))
    except ValueError:
        return jsonify({"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": "Parse error"}}), 400
    if isinstance(message, list):  # a batch (protocol 2025-03-26)
        replies = [r for m in message if (r := mcp_server.handle(m, mcp_call_tool, config.VERSION, mcp_instructions, mcp_tool_list))]
        return (jsonify(replies), 200) if replies else ("", 202)
    reply = mcp_server.handle(message, mcp_call_tool, config.VERSION, mcp_instructions, mcp_tool_list)
    return (jsonify(reply), 200) if reply else ("", 202)


@app.get("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    scratch = request.args.get("scratch") == "1"
    limit = request.args.get("limit", SEARCH_LIMIT, type=int)
    limit = max(1, min(limit, SCRATCH_FILTER_LIMIT if scratch else SEARCH_MAX_LIMIT))
    resp = jsonify(do_search(q, unlinked=request.args.get("unlinked") == "1", limit=limit,
                             scratch=scratch))
    # The search dialog's indicator (js/search.js) follows it: indexing → on.
    resp.headers["X-Leaf-Semantic"] = retrieval.semantic_status()
    return resp


# /deck is a built-in page showing one full main deck (or, with ?list=, that
# separate list). Every link to "the deck" goes here: search results, Linked
# tasks, the due badge, g d / c t. It used to redirect to whichever page
# embedded [[deck]], so where a link landed depended on the wiki's pages, and
# its ?view=/?task= applied to every embed there. [[deck …]] embeds are for
# custom views on ordinary pages; nothing links to them.

@app.get("/deck")
def deck_page():
    list_name = decksyntax.clean_list(request.args.get("list"))
    if list_name:
        # A list looks the same here as on the page that declares it: that
        # embed's params (keep, labels, limit, view, tag) apply.
        declared = deck_lists().get(list_name)
        params = {**(declared["params"] if declared else {}), "list": list_name}
        home = declared and {"slug": declared["slug"],
                             "title": page_meta(safe_path(declared["slug"]), declared["slug"])[0]}
        title = f"{list_name} · Deck"
        crumbs = [("Deck", "/deck"), (list_name, None)]
    else:
        params, home, title, crumbs = {}, None, "Deck", [("Deck", None)]
    # The breadcrumb's key names the view for the navigation trail (js/chrome.js);
    # no page has it as its slug.
    key = "/deck" + (f"?list={list_name}" if list_name else "")
    return views.page("deck_page.html", title, heading=list_name or "Deck", list_name=list_name,
                      home=home, deck_html=deck.build_deck_embed(params),
                      breadcrumb_html=views.breadcrumb(crumbs, key, title))


@app.get("/search")
def search_page():
    q = request.args.get("q", "").strip()
    unlinked = request.args.get("unlinked") == "1"
    results = do_search(q, unlinked=unlinked, limit=SEARCH_PAGE_LIMIT) if q else []
    return views.page("search.html", f"Search: {q}", q=q, results=results, unlinked=unlinked,
                      limit=SEARCH_PAGE_LIMIT)


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
    # own (js/page.js rerenderTaskRef) keeps linking to the page's list.
    lists = [n for n in (decksyntax.clean_list(x) for x in request.args.get("lists", "").split(",")) if n]
    return render_md(text, prefer_lists=lists)


@app.get("/api/version")
def api_version():
    return jsonify({"version": config.VERSION, "build": config.LEAF_BUILD})


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
        f.save(config.UPLOADS_DIR / filename)
        git_commit(config.UPLOADS_DIR / filename, f"Upload {filename}")
        return jsonify({"url": f"/_assets/{filename}"})
    # Client may request a specific filename (e.g. after a conflict dialog)
    requested_name = request.form.get("filename", "").strip()
    filename = Path(requested_name).name if requested_name else original_name
    if not filename or filename.startswith("."):
        abort(400)   # "..", ".git" and friends: not an asset name
    overwrite = request.form.get("overwrite") == "1"
    dest = config.UPLOADS_DIR / filename
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
    resp = send_from_directory(config.UPLOADS_DIR, filename, as_attachment=not inline)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    if mime == "image/svg+xml":
        resp.headers["Content-Security-Policy"] = _SVG_CSP
    return resp


if __name__ == "__main__":
    # waitress, a production server (Flask's own is for development: a thread
    # per connection without limit or timeouts). One process, a fixed pool of
    # threads: the deck's lock, the caches, the login throttle and the
    # semantic-search worker live in this process's memory, so Leaf must never
    # run as several worker processes. Idle connections are closed after a
    # minute, and connections beyond the limit wait.
    # All interfaces, so the container's network bridge can forward
    # connections; exposure is the port mapping's (compose.yaml: LEAF_BIND,
    # 127.0.0.1 = localhost only), the Host check the DNS-rebinding defence.
    from waitress import serve
    print(f"Leaf {config.VERSION} ({config.LEAF_BUILD}) listening on port 5000", flush=True)
    serve(app, host="0.0.0.0", port=5000, threads=8, connection_limit=100,
          channel_timeout=60, ident="Leaf")
