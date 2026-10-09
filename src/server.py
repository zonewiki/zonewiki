"""Leaf's web app: the Flask app, its request checks and the routes of
the wiki (pages, editor, scratch notes, search, uploads, the deck page,
/mcp). Version history routes are in history.py, the deck API in deck.py."""

import hmac
import html
import json
import mimetypes
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse

from flask import Flask, abort, jsonify, redirect, request, send_from_directory, session, url_for

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
from gitstore import run_git, git_commit, git_setup
from render import (
    render_md, render_tags_pills_html,
)
from retrieval import (
    do_search, SCRATCH_FILTER_LIMIT, SEARCH_LIMIT, SEARCH_MAX_LIMIT, SEARCH_PAGE_LIMIT,
)
import views
from views import (
    build_breadcrumb, build_delete_dialog, FORMAT_BAR, hidden_page_view,
    home_link, index_page, notes_list_html, shell,
)
from mcp_tools import mcp_call_tool


app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32)
app.config["PERMANENT_SESSION_LIFETIME"] = 30 * 24 * 60 * 60  # 30 days
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True
ALLOWED_HOSTS = {"localhost:5000", "127.0.0.1:5000"}
PUBLIC_DIR = Path(__file__).parent / "public"


@app.before_request
def check_host():
    if not config.ALLOW_REMOTE and request.host not in ALLOWED_HOSTS:
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


git_setup()


# The deck's files are in the wiki; the semantic index's worker reads tasks,
# so it starts once the deck is set up.
deckstore.init_deck(config.WIKI_DIR, config.GIT_ENABLED, git_commit)
app.register_blueprint(deck.bp)
app.register_blueprint(history.bp)
retrieval.start_semantic()


def _is_local_path(url: str) -> bool:
    """True if url is a same-origin path. "//host" and "/\\host" start with "/"
    but browsers resolve them to another origin, so reject those too."""
    return url.startswith("/") and not url.startswith(("//", "/\\"))


@app.get("/login")
@app.post("/login")
def login():
    if not config.LEAF_PASSWORD:
        return redirect("/")
    error = ""
    if request.method == "POST":
        pw = request.form.get("password", "")
        if hmac.compare_digest(pw, config.LEAF_PASSWORD):
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
    <h2>Sign in to {html.escape(config.APP_NAME)}</h2>
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


@app.get("/")
def index():
    return redirect("/page/index")


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
    return index_page('_scratch', 'Scratch', filt + notes_list_html(notes, 'No entries yet.'))


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
    raw_dir = config.WIKI_DIR / "_raw"
    subpages = []
    if raw_dir.is_dir():
        for f in sorted(raw_dir.glob("*.md"), reverse=True):
            sc = f.read_text(encoding="utf-8")
            sub_slug = f"_raw/{f.stem}"
            subpages.append({"slug": sub_slug, "title": page_title(sc, sub_slug)})
    if subpages:
        items = "".join(
            f'<li><a href="{url("page", p["slug"])}">{html.escape(p["title"])}</a></li>'
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
        meta_row_html = views.macro("partials/page.html", "meta_row")(
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
    return views.page(
        "edit.html", f"Edit — {title}", editor=True, slug=slug, page_title=title, content=content,
        is_new=is_new, section=section, start_char=start_char, end_char=end_char,
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
    _move_page(old_path, new_path, slug, new_slug, f"Rename {slug} to {new_slug}")
    return redirect(url("page", new_slug))


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
    _move_page(old_path, new_path, slug, new_slug, f"Promote {slug} to {new_slug}")
    # The page to link it from opens in the editor, the link to paste where
    # it belongs (leaf.js copied it on Move). A page linking to the note
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
    """With LEAF_MCP_TOKEN set, /mcp needs it as a bearer token. Without
    one it is as open as the wiki's pages are, so a LEAF_PASSWORD-protected
    wiki keeps /mcp closed until a token is set."""
    if config.LEAF_MCP_TOKEN:
        auth = request.headers.get("Authorization", "")
        return auth.startswith("Bearer ") and hmac.compare_digest(
            auth[len("Bearer "):].strip().encode(), config.LEAF_MCP_TOKEN.encode())
    return not config.LEAF_PASSWORD


@app.route("/mcp", methods=["GET", "POST", "DELETE"])
def mcp_endpoint():
    """MCP over HTTP (see mcp_server.py): one JSON-RPC message per POST,
    answered with JSON. GET (a server-sent event stream) and DELETE (ending
    a session) aren't offered: there are no notifications and no sessions."""
    if not _mcp_authorized():
        hint = ("set LEAF_MCP_TOKEN and send it as 'Authorization: Bearer <token>'"
                if not config.LEAF_MCP_TOKEN else "wrong or missing bearer token")
        return jsonify({"error": f"Unauthorized: {hint}"}), 401, {"WWW-Authenticate": "Bearer"}
    if request.method != "POST":
        return "", 405, {"Allow": "POST"}
    try:
        message = json.loads(request.get_data(as_text=True))
    except ValueError:
        return jsonify({"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": "Parse error"}}), 400
    if isinstance(message, list):  # a batch (protocol 2025-03-26)
        replies = [r for m in message if (r := mcp_server.handle(m, mcp_call_tool, config.VERSION))]
        return (jsonify(replies), 200) if replies else ("", 202)
    reply = mcp_server.handle(message, mcp_call_tool, config.VERSION)
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


# /deck is a built-in page showing one full main deck (or, with ?list=, that
# separate list). Every link to "the deck" goes here: search results, Linked
# tasks, the due badge, g d / c t. It used to redirect to whichever page
# embedded [[deck]], so where a link landed depended on the wiki's pages, and
# its ?view=/?task= applied to every embed there. [[deck …]] embeds are for
# custom views on ordinary pages; nothing links to them.

@app.get("/deck")
def deck_page():
    list_name = decksyntax.clean_list(request.args.get("list"))
    sep = '<span class="breadcrumb-sep">›</span>'
    if list_name:
        # A list looks the same here as on the page that declares it: that
        # embed's params (keep, labels, limit, view, tag) apply.
        home = deck_lists().get(list_name)
        params = dict(home["params"]) if home else {}
        params["list"] = list_name
        embed = deck.build_deck_embed(params)
        title = f"{list_name} · Deck"
        crumbs = [home_link(), '<a href="/deck">Deck</a>', f"<span>{html.escape(list_name)}</span>"]
        heading = html.escape(list_name)
        if home:
            page_link = (f'<a href="{url("page", home["slug"])}">'
                         f'{html.escape(page_meta(safe_path(home["slug"]), home["slug"])[0])}</a>')
            note = f'<p class="deck-list-home">A separate list, from {page_link}.</p>'
        else:
            note = (f'<p class="deck-list-home">No page declares <code>[[deck list={heading}]]</code>: '
                    f'these are the tasks tagged #{heading}.</p>')
    else:
        embed = deck.build_deck_embed({})
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
    # Always bind to all interfaces so the container's network bridge can
    # forward connections. LAN exposure is controlled by run.sh's port
    # mapping (127.0.0.1:5000:5000 = localhost only, 0.0.0.0:5000:5000 = LAN).
    # The host-header check (ALLOWED_HOSTS) is the DNS-rebinding defence.
    app.run(host="0.0.0.0", port=5000)
