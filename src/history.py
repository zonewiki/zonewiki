"""Routes for version history and uncommitted changes: a page's
history, revisions, diffs, restore, deleted pages, committing."""

from flask import Blueprint, abort, jsonify, redirect, request

import config
import views
from mdtext import page_title, slug_display_name, strip_h1
from pages import hidden_page, safe_path, url
from gitstore import (
    commit_message, run_git, git_commit, git_diff, git_log, git_show, git_uncommitted_files,
    is_current, list_deleted_pages, SHA_RE, uncommitted_entries, version_index,
    version_label, version_path,
)
from render import render_md
from views import build_breadcrumb, hidden_page_view


bp = Blueprint("history", __name__)


@bp.get("/page/_deleted")
def deleted_pages_route():
    if not config.GIT_ENABLED:
        abort(404)
    # Sharing mode: a deleted page's tags are gone with it; its folder still counts.
    pages = [p for p in list_deleted_pages() if not hidden_page(p["slug"])]
    return views.page("history/deleted.html", "Deleted Pages", pages=pages,
                      breadcrumb_html=build_breadcrumb("_deleted", "Deleted Pages"))


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

    current = is_current(path, entries)
    rows = [{"e": e, "label": version_label(e, entries), "current": current and i == 0,
             "href": url("page", slug) if current and i == 0 else f"{url('revision', slug)}/{e['sha']}"}
            for i, e in enumerate(entries)]
    breadcrumb = (build_breadcrumb(slug, title, ("History", None)) if page_exists
                  else build_breadcrumb("_deleted", "Deleted Pages", (title, None)))
    return views.page("history/history.html", f"History — {title}", slug=slug, page_title=title,
                      page_exists=page_exists, rows=rows, uncommitted=page_exists and not current,
                      breadcrumb_html=breadcrumb)


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
    i = version_index(entries, sha)
    content = git_show(sha, version_path(path, entries, sha))
    restore_sha = sha
    if content is None:
        # Deletion commit — file absent at this SHA; use parent
        r_par = run_git("git", "rev-parse", "--verify", f"{sha}^")
        if r_par.returncode == 0:
            restore_sha = r_par.stdout.strip()
            content = git_show(restore_sha, version_path(path, entries, sha))
        if content is None:
            abort(404)
    title = page_title(content, slug)
    page_exists = path.exists()
    current = is_current(path, entries)
    when = entries[i]["time"] if i is not None else sha[:7]
    if page_exists:
        breadcrumb = build_breadcrumb(slug, title, ("History", url("history", slug)), (when, None))
    else:
        breadcrumb = build_breadcrumb("_deleted", "Deleted Pages", (title, None))
    # Where this version stands: n versions back (the newest is 0 while it is
    # the page as it is now).
    back = (i + (0 if current else 1) if page_exists else i) if i is not None else 0
    return views.page("history/revision.html", f"{when} — {title}", slug=slug, sha=sha,
                      restore_sha=restore_sha, page_title=title, page_exists=page_exists,
                      entries=entries, i=i, current=current, when=when, back=back,
                      article_html=render_md(strip_h1(content)), breadcrumb_html=breadcrumb)


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
    i = version_index(entries, sha)
    before = [config.LEAF_DATA / entries[i + 1]["path"]] if i is not None and i + 1 < len(entries) else []
    diff = git_diff(sha, version_path(path, entries, sha), *before)
    if diff is None:
        abort(404)
    title = slug_display_name(slug)
    page_exists = path.exists()
    when = entries[i]["time"] if i is not None else sha[:7]
    if page_exists:
        breadcrumb = build_breadcrumb(slug, title, ("History", url("history", slug)), ("Changes", None))
    else:
        breadcrumb = build_breadcrumb("_deleted", "Deleted Pages", (title, url("history", slug)), ("Changes", None))
    return views.page("history/diff.html", f"Changes {when} — {title}", slug=slug, sha=sha, page_title=title,
                      page_exists=page_exists, entries=entries, i=i, when=when,
                      what=version_label(entries[i], entries) if i is not None else "",
                      diff_lines=views.diff_lines(diff), breadcrumb_html=breadcrumb)


@bp.post("/restore/<path:slug>/<sha>")
def restore_revision(slug, sha):
    if not config.GIT_ENABLED:
        abort(404)
    if not SHA_RE.match(sha):
        abort(400)
    path = safe_path(slug)
    if path is None:
        abort(400)
    content = git_show(sha, version_path(path, git_log(path), sha))
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
    files = [f for f in uncommitted_entries() if not (f["slug"] and hidden_page(f["slug"]))]
    return views.page("history/uncommitted.html", "Uncommitted Changes", files=files,
                      breadcrumb_html=build_breadcrumb("uncommitted", "Uncommitted Changes"))


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

    rel = str(path.relative_to(config.LEAF_DATA))
    status_r = run_git("git", "status", "--porcelain", "--", rel)
    if status_r.stdout.startswith("??"):
        r = run_git("git", "diff", "--no-index", "--", "/dev/null", rel)
    else:
        r = run_git("git", "diff", "--", rel)
    diff = r.stdout or "(no changes)"

    title = page_title(path.read_text(encoding="utf-8"), slug)
    return views.page("history/diff_uncommitted.html", f"Diff — {title}", page_title=title, filepath=rel,
                      diff_lines=views.diff_lines(diff),
                      breadcrumb_html=build_breadcrumb("uncommitted", "Uncommitted Changes",
                                                       (title, "/uncommitted"), ("Diff", None)))


@bp.get("/api/uncommitted")
def api_uncommitted():
    """Return list of files with uncommitted changes."""
    return jsonify([
        {"slug": e["slug"], "title": e["title"], "filepath": e["filepath"]}
        for e in uncommitted_entries() if not (e["slug"] and hidden_page(e["slug"]))
    ])


@bp.post("/api/commit-all")
def api_commit_all():
    """Commit all uncommitted files, one commit per file."""
    if not config.GIT_ENABLED:
        return jsonify({"error": "Git not enabled"}), 400
    
    committed = []
    for f in git_uncommitted_files():
        filepath = f["filepath"]
        git_commit(config.LEAF_DATA / filepath, commit_message(filepath))
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
    git_commit(config.LEAF_DATA / filepath, commit_message(filepath))
    return jsonify({"success": True})
