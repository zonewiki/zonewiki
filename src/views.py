"""HTML building blocks of Leaf's views: the page shell, breadcrumb and
module switcher, action buttons, menus and dialogs, side columns."""

import re
from datetime import datetime
from pathlib import Path

from flask import g, has_app_context, has_request_context, request, session

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
from templating import macro, render


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
    """Commit button form wired up by js/git.js (posts to /api/commit-file)."""
    return macro("partials/page.html", "commit_form")(filepath, title, on_success, inline)


# Leaf's three modules: the wiki, the deck and the scratch notes. Every view
# names the one it belongs to (`<body data-module>`, `current_module()`);
# the server marks the matching links as current (aria-current), and
# js/actions.js makes the "New" button create that module's kind of thing.
MODULES = (("wiki", "Wiki", "/"), ("scratch", "Scratch", "/page/_scratch"), ("deck", "Deck", "/deck"))


def _module_href(key: str, href: str) -> str:
    """A module's link: the Deck goes to the deck's home page when a page
    declares one ([[deck … home=1]]), else to /deck."""
    if key == "deck" and (home := deck_home_slug()):
        return url("page", home)
    return href


def _deck_counts_title(c: dict) -> str:
    """The Deck links' tooltip, e.g. "Deck: 5 in Focus · 1 overdue · 1 due
    today" (js/chrome.js builds the same, deckCountsTitle)."""
    parts = [f"{c['focus']} in Focus"] if c["focus"] else []
    if c["overdue"]:
        parts.append(f"{c['overdue']} overdue")
    if c["today"]:
        parts.append(f"{c['today']} due today")
    return "Deck: " + " · ".join(parts) if parts else "Deck"


def _deck_counts() -> dict:
    """The Deck links' counts (deck.deck_counts) with their tooltip, once per
    request. Rendered by the server, so the link doesn't change width after
    the page has painted; js/chrome.js refreshes them."""
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


def breadcrumb(crumbs: list[tuple], key: str | None = None, title: str = "", right_html: str = "") -> str:
    """A breadcrumb: the module switcher, then `crumbs` as (label, href or
    None). `key` and `title` name the view for the navigation trail (js/chrome.js)."""
    return render("partials/breadcrumb.html", crumbs=crumbs, key=key, title=title, right_html=right_html,
                  module_links=module_links(), app_name=config.APP_NAME)


def build_breadcrumb(slug: str, title: str, *extra: tuple, right_html: str = "", query_string: str = "") -> str:
    """A page's breadcrumb: its folders, the page, and `extra` (label, url or
    None) crumbs after it. The page is a link when more follows or with a
    `query_string` (added to it, e.g. '?tag=task')."""
    parts = slug.split("/")
    crumbs = [(part.replace("-", " ").title(), url("page", "/".join(parts[:i + 1])))
              for i, part in enumerate(parts[:-1])]
    crumbs.append((title, url("page", slug) + query_string if extra or query_string else None))
    return breadcrumb(crumbs + list(extra), slug, title, right_html)


def hidden_page_view() -> str:
    """What a page not for sharing shows in sharing mode: neither its title
    nor its text, and no stop in the navigation trail."""
    return page("hidden.html", "Hidden page", breadcrumb_html=breadcrumb([("Hidden page", None)]))


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
    """What templates/layout.html needs besides the view's own content.
    `action_buttons=False` drops the action buttons, new note and search
    (the login page has nothing to search or write); the editor has its own
    toolbar instead. `wide`: a page view with side columns on big screens."""
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
            # Only signed in: the login page has no one to sign out.
            "sign_out": bool(config.LEAF_PASSWORD) and bool(session.get("authenticated")),
            "module_links": module_links() if show_actions else []}


def page(template: str, title: str, *, editor: bool = False, action_buttons: bool = True,
         wide: bool = False, **context) -> str:
    """A view from its template, which extends layout.html."""
    return render(template, **_shell_context(title, editor, action_buttons, wide), **context)


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
