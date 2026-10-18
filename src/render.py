"""Markdown to HTML: markdown-it with Leaf's directives (wikilinks,
[[toc]], [[deck]], [[task:…]]) and the post-processing of the result."""

import html
import re
from datetime import date
from urllib.parse import urlencode, urlparse

from flask import has_request_context, request
from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin
from mdit_py_plugins.deflist import deflist_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.gfm_autolink import gfm_autolink_plugin
from mdit_py_plugins.tasklists import tasklists_plugin

import config
import deck

import decksyntax

import deckstore

import pages
from mdtext import (
    heading_id, mask_code, parse_frontmatter, plain_heading, plain_task_title,
    section_anchor, split_wikilink, strip_links, TASK_DONE_ONLY_RE, TASK_META_SRC_RE,
    to_slug, unmask_code, WIKILINK_RE,
)
from pages import all_slugs, hidden_page, page_deck_lists, url


md = (
    # alerts: GitHub/Obsidian callouts ("> [!NOTE]", TIP, IMPORTANT, WARNING,
    # CAUTION) render as <div class="markdown-alert markdown-alert-…">.
    MarkdownIt(options_update={"html": True, "alerts": True})
    .enable("table")
    .enable("strikethrough")
    .enable("smartquotes")
    .enable("replacements")
    # enabled: clickable checkboxes (page view and editor preview toggle them).
    .use(tasklists_plugin, enabled=True)
    .use(footnote_plugin)
    .use(deflist_plugin)
    .use(anchors_plugin, max_level=3)
    # Bare URLs, www. addresses and emails become links (GFM autolinks), for
    # text the editor's paste-a-URL prompt never saw: files edited on disk,
    # an unnamed paste, a URL inside pasted text.
    .use(gfm_autolink_plugin)
)


def _is_external(href: str) -> bool:
    """An http(s) link to another site: it opens in a new tab. A full URL
    to Leaf itself (the host this request came in on) is not external."""
    parts = urlparse(href)
    if parts.scheme not in ("http", "https"):
        return False
    return not (has_request_context() and parts.netloc.lower() == request.host.lower())


def _link_open(self, tokens, idx, options, env):
    tok = tokens[idx]
    if _is_external(tok.attrGet("href") or ""):
        tok.attrSet("target", "_blank")
        tok.attrSet("rel", "noopener noreferrer")
    return self.renderToken(tokens, idx, options, env)


md.add_render_rule("link_open", _link_open)


_IMG_WIDTH_RE = re.compile(r"^(.*)\|(\d+)$", re.S)


def _images(state) -> None:
    """Images, on the parsed tokens: `![alt|300](url)` is 300px wide (never
    wider than the text), and an image alone in its paragraph gets
    `<p class="img-block">`, which on big screens may grow beyond the text
    width (style.css, Side columns); an image within a sentence may not."""
    tokens = state.tokens
    for i, tok in enumerate(tokens):
        if tok.type != "inline" or not tok.children:
            continue
        for img in tok.children:
            m = img.type == "image" and _IMG_WIDTH_RE.match(img.content)
            if m:
                img.content = m.group(1)
                last = img.children[-1] if img.children else None
                if last is not None and last.type == "text":
                    last.content = _IMG_WIDTH_RE.sub(r"\1", last.content)
                img.attrSet("style", f"max-width:100%;width:{m.group(2)}px")
        if (len(tok.children) == 1 and tok.children[0].type == "image"
                and i and tokens[i - 1].type == "paragraph_open"):
            tokens[i - 1].attrSet("class", "img-block")


md.core.ruler.push("leaf_images", _images)


def _html(self, tokens, idx, options, env):
    # Raw HTML as written, but the task-date comments become chips.
    return _render_task_dates(tokens[idx].content)


md.add_render_rule("html_inline", _html)
md.add_render_rule("html_block", _html)


def _heading_open(self, tokens, idx, options, env):
    tok = tokens[idx]
    out = self.renderToken(tokens, idx, options, env)
    if (env or {}).get("edit_slug") and tok.tag in ("h2", "h3"):
        out = f'<div class="section-header section-header-{tok.tag}">{out}'
    return out


def _heading_close(self, tokens, idx, options, env):
    """After a ## or ### heading: its ☑ N chip when tasks link to it, and
    on the page view the ✎ that edits the section (numbered as
    mdtext.split_sections counts them) inside the heading's wrapper."""
    tok, env = tokens[idx], env or {}
    out = self.renderToken(tokens, idx, options, env)
    if tok.tag not in ("h2", "h3"):
        return out
    chip = env.get("section_chips", {}).get(tokens[idx - 2].attrGet("id") or "", "")
    if not env.get("edit_slug"):
        return out.rstrip("\n") + chip + "\n" if chip else out
    env["sections"] += 1
    btn = (f'<a href="{url("edit", env["edit_slug"])}?section={env["sections"]}" '
           f'class="section-edit-btn" title="Edit section">✎</a>')
    return f'{out.rstrip(chr(10))}{chip}{btn}</div>\n'


md.add_render_rule("heading_open", _heading_open)
md.add_render_rule("heading_close", _heading_close)


def process_wikilinks(text: str, slugs: set[str] | None = None) -> str:
    """`slugs`: the wiki's pages, when the caller renders many texts at once."""
    slugs = set(all_slugs()) if slugs is None else slugs

    def replace(m: re.Match) -> str:
        page_name, section, label = split_wikilink(m.group(1))
        anchor = f"#{html.escape(section_anchor(section))}" if section else ""
        label = html.escape(label)
        if not page_name:
            return f'<a href="{anchor or "#"}" class="wikilink">{label}</a>'
        slug = page_name if page_name in slugs else to_slug(page_name)
        if hidden_page(slug):
            # Sharing mode: the words stay (they are this page's), the link
            # to a page not for sharing goes.
            return f'<span class="wikilink-hidden">{label}</span>'
        css = "wikilink" if slug in slugs else "wikilink wikilink-missing"
        return f'<a href="{url("page", slug)}{anchor}" class="{css}">{label}</a>'

    return WIKILINK_RE.sub(replace, text)


_TOC_PLACEHOLDER = "LEAFTOCPLACEHOLDER"
TOC_RE = re.compile(r'^\[\[toc\]\][^\S\n]*$', re.IGNORECASE | re.MULTILINE)


def _build_toc(text: str, unmask=lambda s: s) -> str:
    """TOC from ## / ### headings. `text` may have code masked (so headings
    inside fenced code are not picked up); `unmask` restores inline code in
    the heading titles."""
    headings = re.findall(r'^(#{2,3})\s+(.+)', text, re.MULTILINE)
    if not headings:
        return ''
    items = []
    for hashes, title in headings:
        level = len(hashes)
        title = unmask(title)
        plain = plain_heading(title.strip())
        hid = heading_id(plain)
        cls = ' class="toc-h3"' if level == 3 else ''
        label = md.renderInline(strip_links(title.strip()))
        items.append(f'<li{cls}><a href="#{hid}">{label}</a></li>')
    return '<nav class="toc"><ul>\n' + '\n'.join(items) + '\n</ul></nav>'


def _render_task_dates(html_str: str) -> str:
    """Task metadata comments (<!--t:… d:…-->, written when a checkbox is
    ticked, mdtext.toggle_nth_checkbox) as visible date chips."""
    def replace_full(m: re.Match) -> str:
        created, done = m.group(1), m.group(2)
        chips = f'<span class="task-date task-date-created" title="Created {created}">{created[:10]}</span>'
        if done:
            chips += f'<span class="task-date task-date-done" title="Done {done}">{done[:10]}</span>'
        return chips
    def replace_done_only(m: re.Match) -> str:
        done = m.group(1)
        return f'<span class="task-date task-date-done" title="Done {done}">{done[:10]}</span>'
    html_str = TASK_META_SRC_RE.sub(replace_full, html_str)
    html_str = TASK_DONE_ONLY_RE.sub(replace_done_only, html_str)
    return html_str


def render_md(text: str, *, show_tags: bool = True, show_tldr: bool = True,
              prefer_lists: list[str] | None = None, edit_slug: str = "",
              section_tasks: list[dict] = ()) -> str:
    """Render a page's markdown. Task chips link to a task in a list the text
    declares with [[deck list=…]] (or one of `prefer_lists`) when the task is
    in it, rather than in its first list A–Z (link_list). The page view
    passes `edit_slug`, which gives each ## and ### heading its ✎ (edit
    the section), and `section_tasks` (find_task_backlinks), which gives a
    heading that tasks link to its ☑ N chip."""
    fm_meta, text = parse_frontmatter(text)
    prefer = [name for name, _ in page_deck_lists(text)] + list(prefer_lists or [])
    fm_html = _render_frontmatter_html(fm_meta, show_tags=show_tags, show_tldr=show_tldr)

    text, code_stash = mask_code(text)

    toc_html = ''
    if TOC_RE.search(text):
        toc_html = _build_toc(text, unmask=lambda s: unmask_code(s, code_stash))
        text = TOC_RE.sub(_TOC_PLACEHOLDER, text)

    # Replace [[deck …]] directives with placeholders (supports multiple per page)
    text, deck_embeds = deck.process_deck_embeds(text)
    text, task_refs = _mask_task_refs(text)

    text = unmask_code(process_wikilinks(text), code_stash)
    env = {"edit_slug": edit_slug, "sections": 0,
           "section_chips": _section_task_chips(section_tasks, prefer)}
    result = md.render(text, env)
    result = _inject_task_refs(result, task_refs, prefer)
    if toc_html:
        result = result.replace(f'<p>{_TOC_PLACEHOLDER}</p>', toc_html)
    if deck_embeds:
        result = deck.inject_deck_embeds(result, deck_embeds)
    return fm_html + result


_HEADING_ID_RE = re.compile(r'(<h[1-6])\s+id="[^"]*"')


def render_notes(texts: list[str], *, inline: bool = False) -> list[str]:
    """Deck task notes (or, `inline`, comments: one line each) rendered as a
    page is: the same markdown, wikilinks, [[task:…]] chips and the dates a
    ticked checkbox records. Without what only a page has (frontmatter,
    [[toc]], [[deck]] embeds, ✎ per section) and without heading ids, which
    would clash with the page's own anchors."""
    slugs = set(all_slugs())
    out = []
    for text in texts:
        text, code_stash = mask_code(text)
        text, task_refs = _mask_task_refs(text)
        text = unmask_code(process_wikilinks(text, slugs), code_stash)
        result = md.renderInline(text) if inline else md.render(text)
        out.append(_HEADING_ID_RE.sub(r'\1', _inject_task_refs(result, task_refs)))
    return out


# [[task:<id>]] references (decksyntax.TASK_REF_RE): masked with private-use tokens
# before rendering, so markdown never touches the chip, then swapped for it.
_TASK_REF_TOKEN_RE = re.compile('\ue002(\\d+)\ue003')


# A reference alone in a list item is a row of its own (a checklist of tasks).
_TASK_REF_ITEM_RE = re.compile(r'<li>(?=(?:\s|<p>)*\ue002\d+\ue003(?:\s|</p>)*</li>)')


def _mask_task_refs(text: str) -> tuple[str, list[str]]:
    refs: list[str] = []

    def token(m: re.Match) -> str:
        refs.append(m.group(1).lower())
        return f'\ue002{len(refs) - 1}\ue003'
    return decksyntax.TASK_REF_RE.sub(token, text), refs


def _inject_task_refs(html_str: str, refs: list[str], prefer: list[str] = ()) -> str:
    if not refs:
        return html_str
    tasks = deckstore.resolve_task_refs(refs)
    today = date.today().isoformat()
    html_str = _TASK_REF_ITEM_RE.sub('<li class="task-ref-item">', html_str)
    return _TASK_REF_TOKEN_RE.sub(
        lambda m: _task_ref_html(refs[int(m.group(1))], tasks[refs[int(m.group(1))]], today, prefer),
        html_str)


def _short_date(iso: str, today: str) -> str:
    """"Oct 5" (this year) or "Oct 5, 2025", as the deck's cards show dates,
    in a <time class="local-date"> that the browser rewrites in LEAF_LOCALE
    (localizeDates in js/util.js); "2025-10-05" with LEAF_DATE_FORMAT=iso."""
    try:
        d = date.fromisoformat(iso[:10])
    except ValueError:
        return html.escape(iso)
    if config.LEAF_DATE_FORMAT == "iso":
        text = d.isoformat()
    else:
        text = f"{d:%b} {d.day}"
        text = text if iso[:4] == today[:4] else f"{text}, {d.year}"
    return f'<time class="local-date" datetime="{d.isoformat()}">{text}</time>'


def link_list(task: dict, prefer=()) -> str | None:
    """The list a link to a task opens it in: one of `prefer` (the lists the
    page with the link declares) when the task is in it, so a task in two
    lists opens in the one the page is about; else its own (pages.task_list)."""
    tags = {t.lower() for t in task.get("tags") or []}
    hits = sorted(tags & {p.lower() for p in prefer})
    return hits[0] if hits else pages.task_list(task)


def _task_ref_html(ref: str, task: dict | None, today: str, prefer=()) -> str:
    """A task as an inline chip: its checkbox (completes it in the deck,
    js/page.js), its title linking to it in the deck, and badges for what the
    deck card shows at a glance (deadline, a later start, repeat, keep)."""
    if task is None:
        return (f'<span class="task-ref task-ref-missing" title="No task with id {html.escape(ref)}">'
                f'task {html.escape(ref)}?</span>')
    title = html.escape(plain_task_title(task.get("title", "")))
    done, wont = bool(task.get("completed_at")), bool(task.get("wont_do"))
    list_name = link_list(task, prefer)
    href = html.escape(task_link(task, list_name))
    cls, badges = "task-ref", []
    if done:
        cls += " task-ref-wontdo" if wont else " task-ref-done"
        mark = "✗ won't do" if wont else "✓"
        badges.append(f'<span class="task-ref-badge">{mark} {_short_date(task["completed_at"], today)}</span>')
    else:
        due, start = task.get("due_date"), task.get("start_date")
        if due:
            state = " overdue" if due < today else " due-today" if due == today else ""
            badges.append(f'<span class="task-ref-badge task-ref-due{state}" title="Due {due}">'
                          f'⏰ {_short_date(due, today)}</span>')
        if start and start >= today:
            state = " start-today" if start == today else " start-later"
            label = "today" if start == today else _short_date(start, today)
            badges.append(f'<span class="task-ref-badge task-ref-start{state}" title="Start {start}">'
                          f'📅 {label}</span>')
        if task.get("recurrence"):
            badges.append('<span class="task-ref-badge" title="Repeats">↻</span>')
        if task.get("keep"):
            last = f" ✓ {_short_date(task['last_done'], today)}" if task.get("last_done") else ""
            badges.append(f'<span class="task-ref-badge" title="Keep task: stays after done">⟲{last}</span>')
    if list_name:
        badges.append(f'<span class="task-ref-badge task-ref-list">{html.escape(list_name)}</span>')
    checked = " checked" if done and not wont else ""
    disabled = " disabled" if wont else ""
    return (f'<span class="{cls}" data-task-id="{html.escape(task["id"])}">'
            f'<input type="checkbox" class="task-ref-check" aria-label="Done: {title}"{checked}{disabled}>'
            f'<a class="task-ref-title" href="{href}">{title}</a>{"".join(badges)}</span>')


def _section_task_chips(task_backlinks: list[dict], prefer=()) -> dict[str, str]:
    """{heading id: its ☑ N chip}: a section that tasks link to
    (`[[Page#Section]]`) lists them in a <details> after its heading (no JS)."""
    by_anchor: dict[str, list[dict]] = {}
    for t in task_backlinks:
        if t.get("anchor"):
            by_anchor.setdefault(t["anchor"], []).append(t)
    chips = {}
    for anchor, tasks in by_anchor.items():
        n = len(tasks)
        items = "".join(
            f'<li><a href="{html.escape(task_href(t["id"], "open", link_list(t, prefer)))}">'
            f'{html.escape(plain_task_title(t["title"]))}</a></li>' for t in tasks)
        chips[anchor] = (f'<details class="section-tasks"><summary title="{n} linked task{"s" if n > 1 else ""}">'
                         f'☑ {n}</summary><ul>{items}</ul></details>')
    return chips


def render_tags_pills_html(tags: list[str]) -> str:
    """Render a list of tags as clickable pills linking to search. A pill
    searches `#tag`, the pages and tasks with that tag (search.Query), not
    every page that happens to use the word; a tag with a space in it can't
    be written as #tag, so it stays a word search."""
    if not tags:
        return ''
    pills = ''.join(
        f'<a class="task-tag page-tag" href="/search?{urlencode({"q": t if any(c.isspace() for c in t) else "#" + t})}">'
        f'{html.escape(t)}</a>'
        for t in tags
    )
    return f'<div class="page-tags">{pills}</div>'


def _render_frontmatter_html(meta: dict, *, show_tags: bool = True, show_tldr: bool = True) -> str:
    """Render parsed frontmatter (tags/tldr) as a collapsed-by-default block.

    `show_tags`/`show_tldr` let a caller omit either half — used by view_page,
    which renders both itself in the page-meta row instead of the body.
    """
    if not meta:
        return ''
    parts = []
    if show_tldr:
        tldr = meta.get('tldr')
        if tldr:
            parts.append(f'<p class="page-tldr">{html.escape(tldr)}</p>')
    if show_tags:
        parts.append(render_tags_pills_html(meta.get('tags', [])))
    parts = [p for p in parts if p]
    if not parts:
        return ''
    return (
        '<details class="page-frontmatter"><summary>tldr</summary>'
        + ''.join(parts) + '</details>'
    )


def task_href(task_id: str, tab: str, list_name: str | None,
               year: str | None = None) -> str:
    """Link that opens one task in the deck (or its list's page) on `tab`;
    `year` is the Completed year of a task in an earlier year's archive."""
    params = {"task": task_id, "view": tab}
    if list_name:
        params["list"] = list_name
    if year:
        params["year"] = year
    return "/deck?" + urlencode(params)


def task_link(task: dict, list_name: str | None) -> str:
    """task_href for a task as it is now: an open one on Open, a done one
    on Completed, in its year's archive when that is an earlier year."""
    if not task.get("completed_at"):
        return task_href(task["id"], "open", list_name)
    return task_href(task["id"], "completed", list_name, deckstore.past_year(task))
