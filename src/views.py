"""HTML building blocks of Leaf's views: the page shell, breadcrumb and
module switcher, action buttons, menus and dialogs, side columns."""

import html
import re
from datetime import datetime
from pathlib import Path

from flask import g, has_app_context, has_request_context, request

import config
import deck as deck_module
import retrieval
from mdtext import is_scratch, page_title, page_wikilink, plain_task_title, SCRATCH_RE
from pages import (
    deck_home, deck_home_slug, hidden_page, is_protected, iter_pages, page_folders, page_meta,
    safe_path, sharing_on, url,
)
from gitstore import run_git, git_log, relative_time
from render import link_list, task_href


def diff_to_html(diff: str) -> str:
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


def commit_form_html(filepath: str, title: str, on_success: str, *, inline: bool = False) -> str:
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
        return url("page", home)
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
            attrs += f' aria-label="{label} ({config.APP_NAME} home)">{_LEAF_LOGO}'
        else:
            attrs += ">"
        links.append(f'<a href="{_module_href(key, href)}" class="module-link" data-module="{key}"{attrs}'
                     f'<span class="module-label">{label}</span>{counts}</a>')
    return f'<nav class="breadcrumb-home module-switch" aria-label="{config.APP_NAME}">{"".join(links)}</nav>'


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
    page_href = f'{url("page", slug)}{html.escape(query_string)}'
    for i, part in enumerate(parts[:-1]):
        ancestor = '/'.join(parts[:i + 1])
        label = part.replace('-', ' ').title()
        crumbs.append(f'<a href="{url("page", ancestor)}">{html.escape(label)}</a>')
    # Page itself — link if extra crumbs follow or query_string present, plain span otherwise
    if extra:
        crumbs.append(f'<a href="{page_href}">{html.escape(title)}</a>')
        for label, href in extra:
            if href:
                crumbs.append(f'<a href="{html.escape(href)}">{html.escape(label)}</a>')
            else:
                crumbs.append(f'<span>{html.escape(label)}</span>')
    elif query_string:
        crumbs.append(f'<a href="{page_href}">{html.escape(title)}</a>')
    else:
        crumbs.append(f'<span>{html.escape(title)}</span>')
    right = f'<span class="breadcrumb-right">{right_html}</span>' if right_html else ""
    return f'<div class="breadcrumb" data-slug="{html.escape(slug)}" data-title="{html.escape(title)}">{sep.join(crumbs)}{right}</div>'


def hidden_page_view() -> str:
    """What a page not for sharing shows in sharing mode: neither its title
    nor its text, and no stop in the navigation trail."""
    body = (f'<div class="breadcrumb">{home_link()}<span class="breadcrumb-sep">›</span>'
            '<span>Hidden page</span></div>'
            '<header class="page-header"><h1>Hidden page</h1></header>'
            '<article class="page-content"><p class="hidden-page-note">This page is not shown in '
            'sharing mode. Turn sharing mode off (the eye at the top, or <kbd>S</kbd>) to see it.</p></article>')
    return shell("Hidden page", body)


def build_backlinks_html(slug: str, backlinks: list[dict], task_backlinks: list[dict],
                          prefer=()) -> tuple[str, str]:
    """Return (bl_html, bl_chip) for the backlinks sections and the header chip.
    `prefer`: the lists the page declares, for the Linked tasks' links."""
    bl_html = ""
    bl_chip = ""
    if backlinks:
        bl_items = "".join(
            f'<li><a href="{url("page", r["slug"])}">{html.escape(r["title"])}</a></li>'
            for r in backlinks
        )
        bl_html = f'<section class="backlinks" id="backlinks"><h3>Backlinks</h3><ul>{bl_items}</ul></section>'
        bl_popup_items = "".join(
            f'<a href="{url("page", r["slug"])}">{html.escape(r["title"])}</a>'
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
            f'<li><a href="{html.escape(task_href(t["id"], "open", link_list(t, prefer)))}">'
            f'{html.escape(plain_task_title(t["title"]))}</a>'
            + (f' <a class="task-bl-section" href="#{html.escape(t["anchor"])}">§ {html.escape(t["section"])}</a>'
               if t.get("section") else "")
            + '</li>'
            for t in task_backlinks
        )
        bl_html += f'<section class="backlinks" id="task-backlinks"><h3>Linked tasks</h3><ul>{tbl_items}</ul></section>'
    return bl_html, bl_chip


def build_related_html(slug: str, path: Path, backlinks: list[dict]) -> str:
    """The "Related" section: the pages most similar in meaning (semantic
    search only). Pages already linked either way are left out; the point is
    connections you haven't written down."""
    if not retrieval.SEMANTIC:
        return ""
    retrieval.SEMANTIC.poke()
    pages = {s: p for p, s in iter_pages()}
    _, outgoing = page_meta(path, slug)
    exclude = outgoing | {b["slug"] for b in backlinks}
    related = retrieval.SEMANTIC.related(slug, known=pages, exclude=exclude)
    if not related:
        return ""
    items = "".join(
        f'<li><a href="{url("page", other)}">'
        f'{html.escape(page_meta(pages[other], other)[0])}</a></li>'
        for other, _score in related if not hidden_page(other)
    )
    if not items:
        return ""
    return f'<section class="backlinks" id="related"><h3>Related</h3><ul>{items}</ul></section>'


def build_delete_dialog(slug: str, title: str) -> str:
    """Return delete-confirmation dialog HTML, or empty string for the index page."""
    if is_protected(slug):
        return ""
    return (
        f'<dialog id="page-delete-dialog" class="scratch-modal">'
        f'<p class="scratch-label">Delete page</p>'
        f'<p class="modal-desc">Delete &ldquo;{html.escape(title)}&rdquo;?'
        f'{" It can be restored from history." if config.GIT_ENABLED else " This cannot be undone."}</p>'
        f'<form method="post" action="{url("delete", slug)}">'
        f'<div class="scratch-actions">'
        f'<button type="button" class="btn" id="page-delete-cancel">Cancel</button>'
        f'<button type="submit" class="btn btn-danger">Delete</button>'
        f'</div></form></dialog>'
    )


def _wiki_pages_datalist() -> str:
    """Every page but scratch notes, as suggestions for the scratch note's
    dialogs (the page to link from, the page to append to)."""
    options = "".join(
        f'<option value="{html.escape(s)}">{html.escape(t)}</option>'
        for s, t in sorted((s, page_meta(p, s)[0]) for p, s in iter_pages()
                           if not is_scratch(s) and not hidden_page(s)))
    return f'<datalist id="wiki-pages">{options}</datalist>'


def _build_promote_dialog(slug: str, title: str, error: str = "") -> str:
    """The "Move to wiki" dialog of a scratch note: a folder (suggesting the
    existing ones), the name (the note's title, not its timestamped slug),
    whether to keep the date in front of it, a preview of the page's slug,
    and the page to link it from (leaf.js suggests the folder's index, and
    copies the link on Move). Posts to /promote/<slug>."""
    date = SCRATCH_RE.match(slug).group(1)[:10]
    options = "".join(f'<option value="{html.escape(f)}/">' for f in page_folders())
    error_html = f'<p class="rename-error">{html.escape(error)}</p>' if error else ""
    return (
        f'<dialog id="page-promote-dialog" class="scratch-modal promote-modal" '
        f'data-title="{html.escape(title)}"{" data-open" if error else ""}>'
        f'<p class="scratch-label">Move to wiki</p>'
        f'<form method="post" action="{url("promote", slug)}">'
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
        f'<form method="post" action="{url("append", slug)}">'
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


PAGE_MENU_BTN = ('<button type="button" class="btn page-menu-btn" aria-haspopup="menu" '
                 'aria-expanded="false" aria-controls="page-menu" title="More actions" '
                 'aria-label="More actions">⋯</button>')


def build_page_menu(slug: str, title: str, rename_error: str = "",
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
    if config.GIT_ENABLED:
        items.append(f'<a role="menuitem" href="{url("history", slug)}">History</a>')
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
            f'<form method="post" action="{url("rename", slug)}">'
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


def build_page_meta(slug: str, path: Path, title: str) -> tuple[str, str]:
    """Return (last_modified_html, commit_btn_html) for the page meta bar.

    If git is disabled or the file has no history yet, both strings are empty.
    When the file has uncommitted changes the commit button is included.
    """
    if not config.GIT_ENABLED:
        return "", ""
    filepath = slug + ".md"
    # Single-file status check — much cheaper than scanning the whole tree.
    r = run_git("git", "status", "--porcelain", "--untracked-files=all", "--", filepath)
    if r.stdout.strip() and path.exists():
        mtime_dt = datetime.fromtimestamp(path.stat().st_mtime)
        last_modified = (
            f'<div class="page-meta">Last changed {html.escape(mtime_dt.strftime("%Y-%m-%d"))} '
            f'({html.escape(relative_time(mtime_dt))}) — uncommitted</div>'
        )
        return last_modified, commit_form_html(filepath, title, "reload", inline=True)
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
        f'<nav class="action-buttons" aria-label="{config.APP_NAME}">{"".join(links)}'
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


def shell(title: str, body: str, *, editor: bool = False, action_buttons: bool = True,
          wide: bool = False) -> str:
    """Wrap body in the page skeleton. `action_buttons=False` drops the
    action buttons, new note and search (the editor has its own toolbar; the
    login page has nothing to search or write). `wide`: a page view with
    side columns on big screens (`main.wide`, see view_page)."""
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
    return f"""<!DOCTYPE html>
<html lang="en"{' class="sharing"' if sharing_on() else ""}>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, interactive-widget=resizes-content">
  <meta name="generator" content="{config.APP_NAME} {config.VERSION}">
  <title>{safe_title} — {config.APP_NAME}</title>
  <link rel="icon" href="/public/favicon.svg" type="image/svg+xml">
  <link rel="manifest" href="/public/manifest.json">
  <link rel="apple-touch-icon" href="/public/apple-touch-icon.png">
  <meta name="apple-mobile-web-app-title" content="Leaf">
  <link rel="stylesheet" href="/public/vendor/highlight/github.min.css" media="(prefers-color-scheme: light)">
  <link rel="stylesheet" href="/public/vendor/highlight/github-dark.min.css" media="(prefers-color-scheme: dark)">
  <link rel="stylesheet" href="/public/style.css">
  <script src="/public/vendor/highlight/highlight.min.js" defer></script>
  <script src="/public/leaf.js" defer></script>
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
    <p class="leaf-version">{config.APP_NAME} {config.VERSION}{f" · {html.escape(config.LEAF_BUILD)}" if config.LEAF_BUILD != "unknown" else ""}</p>
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
  {'<div class="remote-badge" title="Remote access enabled">🌐</div>' if config.ALLOW_REMOTE else ''}
  <div class="top-badges">
    <a id="uncommitted-badge" class="uncommitted-badge" href="/uncommitted" style="display: none;" title="Uncommitted changes">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="5.5"/><line x1="8" y1="5.5" x2="8" y2="8"/><line x1="8" y1="8" x2="10" y2="10"/></svg>
        <span id="uncommitted-count">0</span>
      </a>
      {'<form method="post" action="/logout" class="signout-form"><button class="btn" type="submit">Sign out</button></form>' if config.LEAF_PASSWORD else ''}
    </div>
    {top_actions_html() if show_actions else ""}{body}
  </main>
  {action_bar_html() if show_actions else ""}
</body>
</html>"""


def index_page(slug: str, title: str, body_inner: str) -> str:
    """Shared shell for the auto-generated index pages (_scratch, _deleted)."""
    body = f"""{build_breadcrumb(slug, title)}
<header class="page-header"><h1>{html.escape(title)}</h1></header>
<article class="page-content">{body_inner}</article>"""
    return shell(title, body)


def notes_list_html(entries: list[dict], empty_msg: str) -> str:
    if not entries:
        return f"<p>{empty_msg}</p>"
    items = "".join(
        f'<li data-slug="{html.escape(n["slug"])}"><a href="{url("page", n["slug"])}">{html.escape(n["title"])}</a>'
        f'<span class="note-date">{n["date"]}</span></li>'
        for n in entries
    )
    return f"<ul class='notes-list'>{items}</ul>"


# Big screens (style.css, "Side columns") show a page view in three columns:
# the table of contents on the left, the page in the middle and what links
# to it on the right, both sides sticky. The columns are copies of what
# smaller screens show inline: the [[toc]] box and the sections at the end.
_TOC_NAV_RE = re.compile(r'<nav class="toc">(<ul>.*?</ul>)</nav>', re.S)


_HEADING_RE = re.compile(r'<h([23]) id="([^"]+)">(.*?)</h\1>', re.S)
RAIL_TOC_MIN_HEADINGS = 3


def rail_toc_html(article_html: str) -> str:
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


def outlinks_html(slug: str, path: Path) -> str:
    """The right column's "Links to": the existing pages this page links to,
    A–Z by title, so a page nothing links to still shows its connections."""
    _, targets = page_meta(path, slug)
    pages = []
    for target in targets - {slug}:
        if hidden_page(target):
            continue
        target_path = safe_path(target)
        if target_path is not None and target_path.exists():
            pages.append((page_meta(target_path, target)[0], target))
    if not pages:
        return ""
    items = "".join(f'<li><a href="{url("page", t)}">{html.escape(title)}</a></li>'
                    for title, t in sorted(pages, key=lambda p: p[0].lower()))
    return f'<section class="backlinks" data-id="outlinks"><h3>Links to</h3><ul>{items}</ul></section>'


def rail_copy(sections_html: str) -> str:
    """The right column's copy of Backlinks, Linked tasks and Related, its
    ids moved to data-id: the anchors (#backlinks) belong to the originals."""
    return sections_html.replace('<section class="backlinks" id="', '<section class="backlinks" data-id="')


def paste_hint_html(slug: str, copied: bool) -> str:
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


def change_counts(e: dict) -> str:
    parts = []
    if e["added"]:
        parts.append(f'<span class="hist-add">+{e["added"]}</span>')
    if e["deleted"]:
        parts.append(f'<span class="hist-del">−{e["deleted"]}</span>')
    return " ".join(parts)


def version_nav(slug: str, entries: list[dict], i: int, current: bool, kind: str) -> str:
    """← Older · Newer → between versions (kind: "revision" or "diff");
    the newest, when it is the page as it is now, is the page itself."""
    older = (f'<a href="{url(kind, slug)}/{entries[i + 1]["sha"]}" class="btn">← Older</a>'
             if i + 1 < len(entries) else '<span class="btn" aria-disabled="true">← Older</span>')
    if i == 0:
        newer = '<span class="btn" aria-disabled="true">Newer →</span>'
    elif i == 1 and current and kind == "revision":
        newer = f'<a href="{url("page", slug)}" class="btn">Current →</a>'
    else:
        newer = f'<a href="{url(kind, slug)}/{entries[i - 1]["sha"]}" class="btn">Newer →</a>'
    return f'<nav class="version-nav" aria-label="Versions">{older}{newer}</nav>'
