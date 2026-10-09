"""Routes for version history and uncommitted changes: a page's
history, revisions, diffs, restore, deleted pages, committing."""

import html

from flask import Blueprint, abort, jsonify, redirect, request

import config
from mdtext import page_title, slug_display_name, strip_h1
from pages import hidden_page, safe_path, url
from gitstore import (
    _commit_message, _git, git_commit, git_diff, git_log, git_show, git_uncommitted_files,
    _is_current, list_deleted_pages, SHA_RE, _uncommitted_entries, _version_index,
    _version_label, _version_path,
)
from render import render_md
from views import (
    build_breadcrumb, _change_counts, _commit_form_html, _diff_to_html, hidden_page_view,
    _index_page, shell, _version_nav,
)


bp = Blueprint("history", __name__)


@bp.get("/page/_deleted")
def deleted_pages_route():
    if not config.GIT_ENABLED:
        abort(404)
    # Sharing mode: a deleted page's tags are gone with it; its folder still counts.
    pages = [p for p in list_deleted_pages() if not hidden_page(p["slug"])]
    if pages:
        items = "".join(
            f'<li>'
            f'<span class="deleted-page-info">'
            f'<a href="{url("revision", p["slug"])}/{html.escape(p["sha"])}" class="deleted-page-title">{html.escape(p["title"])}</a>'
            f'<span class="note-date">{html.escape(p["slug"])} &middot; {html.escape(p["date"])}</span></span>'
            f'<form method="post" action="{url("restore", p["slug"])}/{html.escape(p["sha"])}">'
            f'<button type="submit" class="btn btn-sm">Restore</button>'
            f'</form></li>'
            for p in pages
        )
        body_inner = f"<ul class='notes-list'>{items}</ul>"
    else:
        body_inner = "<p>No deleted pages in history.</p>"
    return _index_page('_deleted', 'Deleted Pages', body_inner)


@bp.get("/history/<path:slug>")
def history_page_route(slug):
    if hidden_page(slug):
        return hidden_page_view()
    if not config.GIT_ENABLED:
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
            f'<a class="hist-main" href="{url("page", slug)}"><span class="hist-when">Now</span>'
            f'<span class="hist-what">Not committed yet</span></a>'
            f'<span class="hist-badge">Current</span></li>')
    for i, e in enumerate(entries):
        is_cur = current and i == 0
        href = url('page', slug) if is_cur else f"{url('revision', slug)}/{e['sha']}"
        rows.append(
            f'<li class="hist-row{" hist-current" if is_cur else ""}">'
            f'<a class="hist-main" href="{href}">'
            f'<span class="hist-when">{e["time"]}<span class="hist-rel">{html.escape(e["rel"])}</span></span>'
            f'<span class="hist-what">{html.escape(_version_label(e, entries))}</span>'
            f'<span class="hist-counts">{_change_counts(e)}</span></a>'
            + ('<span class="hist-badge">Current</span>' if is_cur else "")
            + f'<a class="hist-changes" href="{url("diff", slug)}/{e["sha"]}" title="What this save changed">Changes</a>'
            f'</li>')
    if entries:
        table = (f'<p class="hist-intro">Every saved version of this page, newest first. '
                 f'Open one to read it, step through older ones or bring it back.</p>'
                 f'<ol class="hist-list">{"".join(rows)}</ol>')
    else:
        table = '<p class="hist-empty">No history yet — history is recorded from this point forward.</p>'

    if page_exists:
        back_link = f'<a href="{url("page", slug)}" class="btn">← Page</a>'
        breadcrumb = build_breadcrumb(slug, title, ('History', None))
    else:
        back_link = '<a href="/page/_deleted" class="btn">← Deleted pages</a>'
        breadcrumb = build_breadcrumb('_deleted', 'Deleted Pages', (title, None))
    body = f"""{breadcrumb}
<header class="page-header">
  <h1>History — {html.escape(title)}</h1>
  {back_link}
</header>
{table}"""
    return shell(f"History — {title}", body)


@bp.get("/revision/<path:slug>/<sha>")
def revision_view(slug, sha):
    if hidden_page(slug):
        return hidden_page_view()
    if not config.GIT_ENABLED:
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
        back_link = f'<a href="{url("history", slug)}" class="btn">← History</a>'
        breadcrumb = build_breadcrumb(slug, title, ('History', url("history", slug)), (when, None))
    else:
        back_link = '<a href="/page/_deleted" class="btn">← Deleted pages</a>'
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
    restore = (f'<form method="post" action="{url("restore", slug)}/{restore_sha}" class="version-restore" '
               f'data-confirm="Make this version the page again? The page as it is now stays in its history.">'
               f'<button type="submit" class="btn">Restore this version</button></form>'
               if can_restore else "")
    changes = (f'<a href="{url("diff", slug)}/{sha}" class="version-link">What this save changed</a>'
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


@bp.get("/diff/<path:slug>/<sha>")
def diff_view(slug, sha):
    if hidden_page(slug):
        return hidden_page_view()
    if not config.GIT_ENABLED:
        abort(404)
    if not SHA_RE.match(sha):
        abort(400)
    path = safe_path(slug)
    if path is None:
        abort(400)
    entries = git_log(path)
    i = _version_index(entries, sha)
    before = [config.WIKI_DIR / entries[i + 1]["path"]] if i is not None and i + 1 < len(entries) else []
    diff = git_diff(sha, _version_path(path, entries, sha), *before)
    if diff is None:
        abort(404)
    title = slug_display_name(slug)
    diff_html = _diff_to_html(diff)
    page_exists = path.exists()
    when = entries[i]["time"] if i is not None else sha[:7]
    if page_exists:
        back_link = f'<a href="{url("history", slug)}" class="btn">← History</a>'
        breadcrumb = build_breadcrumb(slug, title, ('History', url("history", slug)), ('Changes', None))
    else:
        back_link = '<a href="/page/_deleted" class="btn">← Deleted pages</a>'
        breadcrumb = build_breadcrumb('_deleted', 'Deleted Pages', (title, url("history", slug)), ('Changes', None))
    what = html.escape(_version_label(entries[i], entries)) if i is not None else ""
    nav = _version_nav(slug, entries, i, False, "diff") if i is not None else ""
    view = (f'<a href="{url("revision", slug)}/{sha}" class="version-link">View this version</a>'
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


@bp.post("/restore/<path:slug>/<sha>")
def restore_revision(slug, sha):
    if not config.GIT_ENABLED:
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
    return redirect(url("page", slug))


@bp.get("/uncommitted")
def uncommitted_page():
    """Show all files with uncommitted changes."""
    if not config.GIT_ENABLED:
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
            '<td style="white-space:nowrap">'
            + (f'<a href="{url("diff-uncommitted", f["slug"])}" class="btn">Diff</a> ' if f["slug"] else '')
            + (f'<a href="{url("page", f["slug"])}?from=uncommitted" class="btn">View</a> ' if f["slug"] else '')
            + '</td>'
            '</tr>'
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


@bp.get("/diff-uncommitted/<path:slug>")
def diff_uncommitted(slug):
    """Show uncommitted changes for a file."""
    if hidden_page(slug):
        return hidden_page_view()
    if not config.GIT_ENABLED:
        abort(404)

    path = safe_path(slug)
    if path is None or not path.exists():
        abort(404)

    rel = str(path.relative_to(config.WIKI_DIR))
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


@bp.get("/api/uncommitted")
def api_uncommitted():
    """Return list of files with uncommitted changes."""
    return jsonify([
        {"slug": e["slug"], "title": e["title"], "filepath": e["filepath"]}
        for e in _uncommitted_entries() if not (e["slug"] and hidden_page(e["slug"]))
    ])


@bp.post("/api/commit-all")
def api_commit_all():
    """Commit all uncommitted files, one commit per file."""
    if not config.GIT_ENABLED:
        return jsonify({"error": "Git not enabled"}), 400
    
    committed = []
    for f in git_uncommitted_files():
        filepath = f["filepath"]
        git_commit(config.WIKI_DIR / filepath, _commit_message(filepath))
        committed.append(filepath)
    return jsonify({"committed": len(committed), "files": committed})


@bp.post("/api/commit-file")
def api_commit_file():
    """Commit a single file."""
    if not config.GIT_ENABLED:
        return jsonify({"error": "Git not enabled"}), 400
    
    data = request.get_json(silent=True)
    filepath = data.get("filepath", "") if isinstance(data, dict) else ""
    if not filepath:
        return jsonify({"error": "No filepath provided"}), 400

    # Verify file is in uncommitted list
    if filepath not in [f["filepath"] for f in git_uncommitted_files()]:
        return jsonify({"error": "File is not uncommitted"}), 400

    # Message policy lives server-side; the client only sends the filepath.
    git_commit(config.WIKI_DIR / filepath, _commit_message(filepath))
    return jsonify({"success": True})
