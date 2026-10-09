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


def diff_lines(diff: str) -> list[tuple[str, str]]:
    """A unified diff as (class, line) pairs for the diff views: git's file
    headers (everything before a file's first @@) say nothing to a reader
    and are left out; a hunk header becomes a quiet "⋯" between the parts of
    the page that changed."""
    lines, in_header = [], False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            in_header = True
        if line.startswith("@@"):
            in_header = False
            lines.append(("diff-hunk", "⋯"))
        elif not in_header:
            lines.append(("diff-add" if line.startswith("+") else
                          "diff-del" if line.startswith("-") else "diff-ctx", line))
    return lines


def diff_to_html(diff: str) -> str:
    return macro("partials/history.html", "diff")(diff_lines(diff))


def commit_form_html(filepath: str, title: str, on_success: str, *, inline: bool = False) -> str:
    """Commit button form wired up by leaf.js (posts to /api/commit-file)."""
    return macro("partials/page.html", "commit_form")(filepath, title, on_success, inline)


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


def link_sections(backlinks: list[dict], task_backlinks: list[dict], prefer=()) -> list[dict]:
    """The sections at a page's end (and in the right column): Backlinks and
    Linked tasks, each {id, heading, links}. `prefer`: the lists the page
    declares, for the Linked tasks' links."""
    sections = []
    if backlinks:
        sections.append({"id": "backlinks", "heading": "Backlinks", "links": page_links(backlinks)})
    if task_backlinks:
        sections.append({"id": "task-backlinks", "heading": "Linked tasks", "links": [
            {"href": task_href(t["id"], "open", link_list(t, prefer)), "title": plain_task_title(t["title"]),
             "section": t.get("section"), "anchor": t.get("anchor")} for t in task_backlinks]})
    return sections


def page_links(pages: list[dict]) -> list[dict]:
    """[{href, title}] of pages given as {slug, title}."""
    return [{"href": url("page", p["slug"]), "title": p["title"]} for p in pages]


def backlinks_chip(backlinks: list[dict]) -> str:
    """The breadcrumb's ← N chip of a page's backlinks."""
    return macro("partials/page.html", "backlinks_chip")(page_links(backlinks))


def related_section(slug: str, path: Path, backlinks: list[dict]) -> dict | None:
    """The "Related" section: the pages most similar in meaning (semantic
    search only). Pages already linked either way are left out; the point is
    connections you haven't written down."""
    if not retrieval.SEMANTIC:
        return None
    retrieval.SEMANTIC.poke()
    pages = {s: p for p, s in iter_pages()}
    _, outgoing = page_meta(path, slug)
    exclude = outgoing | {b["slug"] for b in backlinks}
    related = [{"slug": other, "title": page_meta(pages[other], other)[0]}
               for other, _score in retrieval.SEMANTIC.related(slug, known=pages, exclude=exclude)
               if not hidden_page(other)]
    return {"id": "related", "heading": "Related", "links": page_links(related)} if related else None


def build_delete_dialog(slug: str, title: str) -> str:
    """The page view's delete confirmation; none for the index page."""
    if is_protected(slug):
        return ""
    return macro("partials/page.html", "delete_dialog")(slug, title, config.GIT_ENABLED)


def _append_heading(slug: str, title: str) -> str:
    """The section heading a scratch note gets on the page it is appended
    to: its title and its day."""
    return f"{title} ({SCRATCH_RE.match(slug).group(1)[:10]})"


def page_menu(slug: str, title: str, rename_error: str = "",
              promote_error: str = "", append_error: str = "") -> dict:
    """What the page view's ⋯ menu and its dialogs need
    (templates/partials/page.html, page_menu)."""
    m = {"slug": slug, "title": title, "git": config.GIT_ENABLED, "scratch": is_scratch(slug),
         "renamable": not is_protected(slug), "rename_error": rename_error}
    if m["scratch"]:
        m.update(
            date=SCRATCH_RE.match(slug).group(1)[:10], folders=page_folders(),
            promote_error=promote_error, append_error=append_error,
            append_heading=_append_heading(slug, title),
            # Every page but scratch notes: the page to link from, to append to.
            wiki_pages=sorted((s, page_meta(p, s)[0]) for p, s in iter_pages()
                              if not is_scratch(s) and not hidden_page(s)))
    return m


def last_changed(slug: str, path: Path, title: str) -> tuple[dict | None, dict | None]:
    """(changed, commit) for the page's meta row: when it last changed
    ({date, rel}), and while it has uncommitted changes what its Commit
    needs ({filepath, title}). Both None without git or history."""
    if not config.GIT_ENABLED:
        return None, None
    filepath = slug + ".md"
    # Single-file status check — much cheaper than scanning the whole tree.
    r = run_git("git", "status", "--porcelain", "--untracked-files=all", "--", filepath)
    if r.stdout.strip() and path.exists():
        mtime_dt = datetime.fromtimestamp(path.stat().st_mtime)
        return ({"date": mtime_dt.strftime("%Y-%m-%d"), "rel": relative_time(mtime_dt)},
                {"filepath": filepath, "title": title})
    entries = git_log(path, max_count=1)
    if entries:
        return {"date": entries[0]["date"], "rel": entries[0]["rel"]}, None
    return None, None


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


def rail_toc_items(article_html: str) -> str:
    """The left column's "On this page" list: the page's [[toc]], or without
    one its ## and ### headings when there are at least
    RAIL_TOC_MIN_HEADINGS (a long page shouldn't need the directive for
    this). "" otherwise; the column stays, so the page keeps its place."""
    m = _TOC_NAV_RE.search(article_html)
    if m:
        return m.group(1)
    heads = _HEADING_RE.findall(article_html)
    if len(heads) < RAIL_TOC_MIN_HEADINGS:
        return ""
    # A heading's own links are flattened: no link inside a link.
    return "<ul>\n" + "\n".join(
        f'<li{" class=\"toc-h3\"" if level == "3" else ""}><a href="#{hid}">'
        f'{re.sub(r"</?a\b[^>]*>", "", label).strip()}</a></li>'
        for level, hid, label in heads) + "\n</ul>"


def outlinks(slug: str, path: Path) -> list[dict]:
    """The right column's "Links to": the existing pages this page links to,
    A–Z by title, so a page nothing links to still shows its connections."""
    _, targets = page_meta(path, slug)
    pages = []
    for target in targets - {slug}:
        if hidden_page(target):
            continue
        target_path = safe_path(target)
        if target_path is not None and target_path.exists():
            pages.append({"slug": target, "title": page_meta(target_path, target)[0]})
    return page_links(sorted(pages, key=lambda p: p["title"].lower()))


def paste_hint(slug: str, copied: bool) -> dict | None:
    """The editor's hint after Move to wiki (`?paste=<slug>`): the moved
    page's link, copied to the clipboard (`copied`) or to copy."""
    path = safe_path(slug) if slug else None
    if not path or not path.exists() or hidden_page(slug):
        return None
    return {"link": page_wikilink(slug, page_title(path.read_text(encoding="utf-8"), slug)), "copied": copied}
