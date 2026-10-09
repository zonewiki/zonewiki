"""HTML building blocks of Leaf's views: the page shell, breadcrumb and
module switcher, action buttons, menus and dialogs, side columns."""

import html
import re
from datetime import datetime
from pathlib import Path

from flask import g, has_app_context, has_request_context, request
from jinja2 import Environment, FileSystemLoader

import config
import deck
import decksyntax
import retrieval
from mdtext import is_scratch, page_title, page_wikilink, plain_task_title, SCRATCH_RE
from pages import (
    deck_home, deck_home_slug, hidden_page, is_protected, iter_pages, page_folders, page_meta,
    safe_path, sharing_on, url,
)
from gitstore import run_git, git_log, relative_time
from render import link_list, task_href


# Leaf's views are Jinja2 templates in src/templates, auto-escaped. A
# variable holding HTML that Leaf built itself ends in _html and is the only
# kind marked |safe there. Not Flask's render_template: helpers here render
# outside a request too (tests, and Python callers that embed the result).
_env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                   autoescape=True, trim_blocks=True, lstrip_blocks=True)
_env.globals.update(url=url, config=config)


def render(name: str, **context) -> str:
    """A template rendered to HTML."""
    return _env.get_template(name).render(**context)


def macro(template: str, name: str):
    """A macro of a template, to call from Python (returns HTML)."""
    return getattr(_env.get_template(template).module, name)


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


def _deck_counts() -> dict:
    """The Deck links' counts (deck.deck_counts) with their tooltip, once per
    request. Rendered by the server, so the link doesn't change width after
    the page has painted; leaf.js refreshes them."""
    if has_app_context() and "deck_counts" in g:
        return g.deck_counts
    c = dict(deck.deck_counts())
    c["title"] = _deck_counts_title(c)
    if has_app_context():
        g.deck_counts = c
    return c


def module_links() -> list[dict]:
    """The three module links (templates/partials/modules.html): where each
    goes, whether it is the current view's (aria-current at first paint),
    and the Deck's counts."""
    current = current_module() if has_request_context() else None
    return [{"key": key, "label": label, "href": _module_href(key, href), "current": current == key,
             "counts": _deck_counts() if key == "deck" else None}
            for key, label, href in MODULES]


def home_link() -> str:
    """The root of every breadcrumb, the module switcher."""
    return macro("partials/modules.html", "switch")(module_links(), config.APP_NAME)


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
    """A breadcrumb for a page slug: its folders, the page, and `extra`
    (label, url or None) crumbs after it. `right_html` sits at its right
    end; `query_string` is added to the page's own link (e.g. '?tag=task')."""
    parts = slug.split("/")
    folders = [(part.replace("-", " ").title(), url("page", "/".join(parts[:i + 1])))
               for i, part in enumerate(parts[:-1])]
    return render("partials/breadcrumb.html", slug=slug, title=title, folders=folders, extra=extra,
                  right_html=right_html, query_string=query_string, module_links=module_links(),
                  app_name=config.APP_NAME)


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


FORMAT_BAR = render("partials/format_bar.html")


def _shell_context(title: str, editor: bool, action_buttons: bool, wide: bool) -> dict:
    """What templates/layout.html needs besides the view's own content."""
    home = deck_home()
    deck_new = None
    if home:
        # Its "+ New" on Focus (its view, or the default one) starts the
        # task today; elsewhere a new task has no start date.
        deck_new = {"tags": ",".join(decksyntax.new_task_tags(home["params"])),
                    "keep": decksyntax.is_true(home["params"].get("keep")),
                    "today": decksyntax.embed_view(home["params"]) in ("", "focus")}
    show_actions = action_buttons and not editor
    return {"title": title, "editor": editor, "wide": wide, "show_actions": show_actions,
            "module": "" if editor else current_module(), "deck_new": deck_new,
            "sharing": sharing_on(), "app_name": config.APP_NAME, "version": config.VERSION,
            "build": config.LEAF_BUILD, "allow_remote": config.ALLOW_REMOTE,
            "sign_out": bool(config.LEAF_PASSWORD),
            "module_links": module_links() if show_actions else []}


def shell(title: str, body: str, *, editor: bool = False, action_buttons: bool = True,
          wide: bool = False) -> str:
    """Wrap body (HTML) in the page skeleton, templates/layout.html.
    `action_buttons=False` drops the action buttons, new note and search
    (the editor has its own toolbar; the login page has nothing to search or
    write). `wide`: a page view with side columns on big screens
    (`main.wide`, see view_page)."""
    return render("layout.html", body_html=body, **_shell_context(title, editor, action_buttons, wide))


def page(template: str, title: str, *, editor: bool = False, action_buttons: bool = True,
         wide: bool = False, **context) -> str:
    """A view from its template, which extends layout.html."""
    return render(template, **_shell_context(title, editor, action_buttons, wide), **context)


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
