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

import deck as deck_module
from mdtext import (
    _heading_id, _mask_code, parse_frontmatter, _plain_heading, _plain_task_title,
    section_anchor, split_wikilink, _strip_links, _TASK_DONE_ONLY_RE, _TASK_META_SRC_RE,
    to_slug, _unmask_code, WIKILINK_RE,
)
from pages import all_slugs, hidden_page, _page_deck_lists, url


md = (
    # alerts: GitHub/Obsidian callouts ("> [!NOTE]", TIP, IMPORTANT, WARNING,
    # CAUTION) render as <div class="markdown-alert markdown-alert-…">.
    MarkdownIt(options_update={"html": True, "alerts": True})
    .enable("table")
    .enable("strikethrough")
    .enable("smartquotes")
    .enable("replacements")
    .use(tasklists_plugin)
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


def process_wikilinks(text: str) -> str:
    slugs = set(all_slugs())

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
_TOC_RE = re.compile(r'^\[\[toc\]\][^\S\n]*$', re.IGNORECASE | re.MULTILINE)


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
        plain = _plain_heading(title.strip())
        hid = _heading_id(plain)
        cls = ' class="toc-h3"' if level == 3 else ''
        label = md.renderInline(_strip_links(title.strip()))
        items.append(f'<li{cls}><a href="#{hid}">{label}</a></li>')
    return '<nav class="toc"><ul>\n' + '\n'.join(items) + '\n</ul></nav>'


_IMG_PARA_RE = re.compile(r'<p>(<img\b[^>]*>)</p>')


def _mark_image_paragraphs(html_text: str) -> str:
    """An image alone in its paragraph gets `<p class="img-block">`: on big
    screens such an image may grow beyond the text width (style.css, Side
    columns), an image within a sentence may not."""
    return _IMG_PARA_RE.sub(r'<p class="img-block">\1</p>', html_text)


_IMG_SIZE_RE = re.compile(r'<img\b[^>]*\balt="([^"]*)\|(\d+)"[^>]*>', re.IGNORECASE)


def _render_task_dates(html_str: str) -> str:
    """Convert task metadata HTML comments to visible date chips."""
    def replace_full(m: re.Match) -> str:
        created, done = m.group(1), m.group(2)
        chips = f'<span class="task-date task-date-created" title="Created {created}">{created[:10]}</span>'
        if done:
            chips += f'<span class="task-date task-date-done" title="Done {done}">{done[:10]}</span>'
        return chips
    def replace_done_only(m: re.Match) -> str:
        done = m.group(1)
        return f'<span class="task-date task-date-done" title="Done {done}">{done[:10]}</span>'
    html_str = _TASK_META_SRC_RE.sub(replace_full, html_str)
    html_str = _TASK_DONE_ONLY_RE.sub(replace_done_only, html_str)
    return html_str


def _apply_img_sizes(html_str: str) -> str:
    """Rewrite <img src=... alt="text|300"> → same tag with |300 stripped and style added."""
    def fix(m: re.Match) -> str:
        tag = m.group(0)
        alt_text = m.group(1)
        width = m.group(2)
        tag = tag.replace(f'{alt_text}|{width}"', f'{alt_text}"', 1)
        tag = tag[:-1] + f' style="max-width:100%;width:{width}px">'
        return tag
    return _IMG_SIZE_RE.sub(fix, html_str)


# markdown-it's tasklists plugin renders checkboxes disabled; page view and the
# editor preview make them clickable, so drop the attribute — on those inputs
# only, never elsewhere in the document.
_TASK_CHECKBOX_RE = re.compile(r'<input\b[^>]*\btask-list-item-checkbox\b[^>]*>')


def _enable_task_checkboxes(html_str: str) -> str:
    return _TASK_CHECKBOX_RE.sub(
        lambda m: re.sub(r'\s+disabled(?:="[^"]*")?', '', m.group(0)), html_str)


def render_md(text: str, *, show_tags: bool = True, show_tldr: bool = True,
              prefer_lists: list[str] | None = None) -> str:
    """Render a page's markdown. Task chips link to a task in a list the text
    declares with [[deck list=…]] (or one of `prefer_lists`) when the task is
    in it, rather than in its first list A–Z (_link_list)."""
    fm_meta, text = parse_frontmatter(text)
    prefer = [name for name, _ in _page_deck_lists(text)] + list(prefer_lists or [])
    fm_html = _render_frontmatter_html(fm_meta, show_tags=show_tags, show_tldr=show_tldr)

    text, code_stash = _mask_code(text)

    toc_html = ''
    if _TOC_RE.search(text):
        toc_html = _build_toc(text, unmask=lambda s: _unmask_code(s, code_stash))
        text = _TOC_RE.sub(_TOC_PLACEHOLDER, text)

    # Replace [[deck …]] directives with placeholders (supports multiple per page)
    text, deck_embeds = deck_module.process_deck_embeds(text)
    text, task_refs = _mask_task_refs(text)

    text = _unmask_code(process_wikilinks(text), code_stash)
    result = _mark_image_paragraphs(_apply_img_sizes(md.render(text)))
    result = _enable_task_checkboxes(result)
    result = _render_task_dates(result)
    result = _inject_task_refs(result, task_refs, prefer)
    if toc_html:
        result = result.replace(f'<p>{_TOC_PLACEHOLDER}</p>', toc_html)
    if deck_embeds:
        result = deck_module.inject_deck_embeds(result, deck_embeds)
    return fm_html + result


# [[task:<id>]] references (deck.TASK_REF_RE): masked with private-use tokens
# before rendering, so markdown never touches the chip, then swapped for it.
_TASK_REF_TOKEN_RE = re.compile('(\\d+)')


# A reference alone in a list item is a row of its own (a checklist of tasks).
_TASK_REF_ITEM_RE = re.compile(r'<li>(?=(?:\s|<p>)*\d+(?:\s|</p>)*</li>)')


def _mask_task_refs(text: str) -> tuple[str, list[str]]:
    refs: list[str] = []

    def token(m: re.Match) -> str:
        refs.append(m.group(1).lower())
        return f'{len(refs) - 1}'
    return deck_module.TASK_REF_RE.sub(token, text), refs


def _inject_task_refs(html_str: str, refs: list[str], prefer: list[str] = ()) -> str:
    if not refs:
        return html_str
    tasks = deck_module.resolve_task_refs(refs)
    today = date.today().isoformat()
    html_str = _TASK_REF_ITEM_RE.sub('<li class="task-ref-item">', html_str)
    return _TASK_REF_TOKEN_RE.sub(
        lambda m: _task_ref_html(refs[int(m.group(1))], tasks[refs[int(m.group(1))]], today, prefer),
        html_str)


def _short_date(iso: str, today: str) -> str:
    """"Oct 5" (this year) or "Oct 5, 2025", as the deck's cards show dates."""
    try:
        d = date.fromisoformat(iso[:10])
    except ValueError:
        return iso
    text = f"{d:%b} {d.day}"
    return text if iso[:4] == today[:4] else f"{text}, {d.year}"


def _link_list(task: dict, prefer=()) -> str | None:
    """The list a link to a task opens it in: one of `prefer` (the lists the
    page with the link declares) when the task is in it, so a task in two
    lists opens in the one the page is about; else its own (deck.task_list)."""
    tags = {t.lower() for t in task.get("tags") or []}
    hits = sorted(tags & {p.lower() for p in prefer})
    return hits[0] if hits else deck_module.task_list(task)


def _task_ref_html(ref: str, task: dict | None, today: str, prefer=()) -> str:
    """A task as an inline chip: its checkbox (completes it in the deck,
    leaf.js), its title linking to it in the deck, and badges for what the
    deck card shows at a glance (deadline, a later start, repeat, keep)."""
    if task is None:
        return (f'<span class="task-ref task-ref-missing" title="No task with id {html.escape(ref)}">'
                f'task {html.escape(ref)}?</span>')
    title = html.escape(_plain_task_title(task.get("title", "")))
    done, wont = bool(task.get("completed_at")), bool(task.get("wont_do"))
    list_name = _link_list(task, prefer)
    href = html.escape(_task_link(task, list_name))
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


def _inject_section_edit_buttons(html_str: str, slug: str) -> str:
    """Wrap each rendered <h2>/<h3> with a .section-header div and append an edit link."""
    counter = [0]

    def wrap(m: re.Match) -> str:
        counter[0] += 1
        n = counter[0]
        level = m.group(1)[1]  # '2' or '3' (strip leading 'h')
        btn = (
            f'<a href="{url("edit", slug)}?section={n}" '
            f'class="section-edit-btn" title="Edit section">✎</a>'
        )
        return f'<div class="section-header section-header-h{level}">{m.group(0)}{btn}</div>'

    return re.sub(r'<(h[23])\b[^>]*>.*?</\1>', wrap, html_str, flags=re.DOTALL)


def _inject_section_tasks(html_str: str, task_backlinks: list[dict], prefer=()) -> str:
    """A section that tasks link to (`[[Page#Section]]`) gets a ☑ N chip
    after its heading, opening the list of those tasks (a <details>, no JS)."""
    by_anchor: dict[str, list[dict]] = {}
    for t in task_backlinks:
        if t.get("anchor"):
            by_anchor.setdefault(t["anchor"], []).append(t)
    if not by_anchor:
        return html_str

    def chip(m: re.Match) -> str:
        tasks = by_anchor.get(html.unescape(m.group(2)))
        if not tasks:
            return m.group(0)
        n = len(tasks)
        items = "".join(
            f'<li><a href="{html.escape(_task_href(t["id"], "open", _link_list(t, prefer)))}">'
            f'{html.escape(_plain_task_title(t["title"]))}</a></li>' for t in tasks)
        return (f'{m.group(0)}<details class="section-tasks"><summary title="{n} linked task{"s" if n > 1 else ""}">'
                f'☑ {n}</summary><ul>{items}</ul></details>')

    return re.sub(r'<h([23]) id="([^"]+)">.*?</h\1>', chip, html_str, flags=re.S)


def _render_tags_pills_html(tags: list[str]) -> str:
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
        parts.append(_render_tags_pills_html(meta.get('tags', [])))
    parts = [p for p in parts if p]
    if not parts:
        return ''
    return (
        '<details class="page-frontmatter"><summary>tldr</summary>'
        + ''.join(parts) + '</details>'
    )


def _task_href(task_id: str, tab: str, list_name: str | None,
               year: str | None = None) -> str:
    """Link that opens one task in the deck (or its list's page) on `tab`;
    `year` is the Completed year of a task in an earlier year's archive."""
    params = {"task": task_id, "view": tab}
    if list_name:
        params["list"] = list_name
    if year:
        params["year"] = year
    return "/deck?" + urlencode(params)


def _task_link(task: dict, list_name: str | None) -> str:
    """_task_href for a task as it is now: an open one on Open, a done one
    on Completed, in its year's archive when that is an earlier year."""
    if not task.get("completed_at"):
        return _task_href(task["id"], "open", list_name)
    return _task_href(task["id"], "completed", list_name, deck_module._past_year(task))
