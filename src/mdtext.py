"""Markdown source text: frontmatter, fenced code, code masking, headings
and their anchors, wikilink syntax, sections, task-list checkboxes. Pure
functions on strings, no files."""

import hashlib
import re
from datetime import datetime


WIKILINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")


def slug_display_name(slug: str) -> str:
    """Convert a slug to a human-readable title (e.g. 'my-page' → 'My Page')."""
    return slug.split('/')[-1].replace("-", " ").title()


def to_slug(name: str) -> str:
    """Slugify a name, preserving '/' as a path separator."""
    parts = name.strip().split('/')
    slugged = []
    for part in parts:
        s = part.strip().lower()
        s = re.sub(r"[^a-z0-9_]+", "-", s)
        s = s.strip("-")
        if s:
            slugged.append(s)
    return "/".join(slugged)


SCRATCH_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})_(.+)$")


def is_scratch(slug: str) -> bool:
    return bool(SCRATCH_RE.match(slug))


_H1_RE = re.compile(r"^#[ \t]+(\S.*)$")


def page_title(content: str, slug: str) -> str:
    """First "# " heading in the file, else a title derived from the slug.

    Skips a leading frontmatter block and fenced code, so a shell comment in
    a code example ("# install deps") never becomes the page title."""
    lines = content.splitlines()
    start = _body_start(lines)
    for i, line, in_fence in iter_fenced_lines(lines):
        if i >= start and not in_fence:
            m = _H1_RE.match(line)
            if m:
                return m.group(1).strip()
    return slug_display_name(slug)


def split_wikilink(inner: str) -> tuple[str, str, str]:
    """A wikilink's inside as (page, section, label): `Page`, `Page|Label`,
    `Page#Section` (a heading on that page), `Page#Section|Label`, or
    `#Section` (a heading on this page). The label defaults to the page,
    "Page › Section" with a section, or the section alone."""
    target, bar, label = inner.partition("|")
    page, _, section = target.partition("#")
    page, section = page.strip(), section.strip()
    if not bar:
        label = f"{page} › {section}" if page and section else section or page
    return page, section, label.strip()


def section_anchor(section: str) -> str:
    """The heading id a wikilink's #Section points at (as rendered)."""
    return heading_id(plain_heading(section))


def heading_id(text: str) -> str:
    """Match mdit-py-plugins anchors_plugin default slugify."""
    return re.sub(r'[^\w\- ]+', '', text, flags=re.UNICODE).strip().lower().replace(' ', '-')


def plain_heading(text: str) -> str:
    """Strip inline markdown and wikilinks from a heading to get plain text for anchor IDs."""
    text = strip_links(text)
    text = re.sub(r'!?\[([^\]]*)\]', r'\1', text)                # bare [label]
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'_(.+?)_', r'\1', text)
    text = re.sub(r'`[^`]*`', '', text)
    return text.strip()


def strip_links(text: str) -> str:
    """Replace wikilinks and markdown links with their label text, keeping other inline markup."""
    text = re.sub(r'\[\[([^\]|]+)\|([^\]]+)\]\]', r'\2', text)  # [[page|label]] → label
    text = re.sub(r'\[\[([^\]]+)\]\]', r'\1', text)              # [[page]] → page
    text = re.sub(r'!?\[([^\]]*)\]\([^)]*\)', r'\1', text)       # [label](url) / ![alt](url)
    text = re.sub(r'\[([^\]]*)\]\[[^\]]*\]', r'\1', text)        # [label][ref]
    return text


# Format stored in markdown: - [ ] task text <!--t:2026-06-12T18:35:00-->
#                             - [x] task text <!--t:2026-06-12T18:35:00 d:2026-06-12T19:00:00-->
TASK_META_SRC_RE = re.compile(r'<!--t:(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\s+d:(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}))?-->')


TASK_DONE_ONLY_RE = re.compile(r'<!--d:(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})-->')


# Private-use code points: never typed by users, and not matched by any of the
# directive regexes, so a masked code region is invisible to pre-processing.
_CODE_MASK_RE = re.compile('\ue000(\\d+)\ue001')


_INLINE_CODE_RE = re.compile(r'(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)')


def mask_code(text: str) -> tuple[str, list[str]]:
    """Replace fenced code blocks and inline code spans with opaque tokens.

    [[wikilinks]], [[toc]] and [[deck]] are rewritten on the raw markdown before
    markdown-it runs, so without this they would also be rewritten inside code
    (showing up as escaped HTML in the block, or embedding a whole deck app in a
    <pre>). Restore with unmask_code() before rendering."""
    stash: list[str] = []

    def token(s: str) -> str:
        stash.append(s)
        return f'\ue000{len(stash) - 1}\ue001'

    out = []
    for _, line, in_fence in iter_fenced_lines(text.splitlines(keepends=True)):
        if in_fence:
            body = line.rstrip('\n')
            out.append(token(body) + line[len(body):])
        else:
            out.append(_INLINE_CODE_RE.sub(lambda m: token(m.group(0)), line))
    return ''.join(out), stash


def unmask_code(text: str, stash: list[str]) -> str:
    return _CODE_MASK_RE.sub(lambda m: stash[int(m.group(1))], text)


def iter_fenced_lines(lines: list[str]):
    """Yield (index, line, in_fence) for each line, where in_fence is True for
    fence delimiter lines (``` / ~~~) and everything between them."""
    in_fence = False
    fence_marker = ''
    fence_len = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not in_fence:
            if stripped.startswith('```') or stripped.startswith('~~~'):
                in_fence = True
                fence_marker = stripped[0]
                fence_len = len(stripped) - len(stripped.lstrip(fence_marker))
                yield i, line, True
            else:
                yield i, line, False
        else:
            # Closing fence must use same char and be at least as long
            if stripped.startswith(fence_marker * fence_len) and stripped.rstrip(fence_marker) == '':
                in_fence = False
            yield i, line, True


# A task-list item as markdown-it's tasklists plugin renders it: a list marker
# (optionally inside blockquotes), then "[ ]"/"[x]" followed by whitespace and
# text. Only these lines produce checkboxes, so only these may be counted —
# the Nth rendered checkbox must be the Nth match here. Mirrored in js/page.js
# (taskCheckboxOffsets) for toggling inside the editor.
_TASK_ITEM_RE = re.compile(r'^[ \t]*(?:>[ \t]?)*[ \t]*(?:[-*+]|\d{1,9}[.)])[ \t]+\[([ xX])\](?=[ \t]+\S)')


def _task_item_lines(lines: list[str]) -> list[tuple[int, re.Match]]:
    """(line index, match) for every task-list checkbox line, skipping a
    leading frontmatter block and fenced code."""
    start = _body_start(lines)
    found = []
    for i, line, in_fence in iter_fenced_lines(lines):
        if i < start or in_fence:
            continue
        m = _TASK_ITEM_RE.match(line)
        if m:
            found.append((i, m))
    return found


def toggle_nth_checkbox(content: str, idx: int) -> str:
    """Toggle the Nth task-list checkbox (0-based), in rendered order."""
    lines = content.splitlines(keepends=True)
    items = _task_item_lines(lines)
    if idx >= len(items):
        return content
    li, m = items[idx]
    line = lines[li]
    eol = '\n' if line.endswith('\n') else ''
    body = line.rstrip('\n')

    # Toggle exactly the item's own [ ] / [x], never a later bracket on the line
    is_checking = m.group(1) == ' '
    body = body[:m.start(1)] + ('x' if is_checking else ' ') + body[m.end(1):]

    now = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    meta_m = TASK_META_SRC_RE.search(body)
    if is_checking:
        if meta_m:
            # Splice done date into existing comment
            body = (body[:meta_m.start()] +
                    f'<!--t:{meta_m.group(1)} d:{now}-->' +
                    body[meta_m.end():])
        else:
            done_m = TASK_DONE_ONLY_RE.search(body)
            if done_m:
                body = body[:done_m.start()] + f'<!--d:{now}-->' + body[done_m.end():]
            else:
                body = body.rstrip() + f' <!--d:{now}-->'
    else:
        if meta_m:
            # Strip done date, keep created
            body = (body[:meta_m.start()] +
                    f'<!--t:{meta_m.group(1)}-->' +
                    body[meta_m.end():])
        else:
            # Remove standalone done comment
            body = TASK_DONE_ONLY_RE.sub('', body).rstrip()

    lines[li] = body + eol
    return ''.join(lines)


def split_sections(content: str) -> list[str]:
    """Split content at ## and ### headings not inside fenced code blocks.
    Handles ATX headings with up to 3 leading spaces and setext h2 headings
    (heading text followed by a line of dashes).
    Returns [preamble, section1, section2, ...]
    """
    lines = content.splitlines(keepends=True)
    heading_lines: list[int] = []
    prev_text_line_idx: int | None = None   # for setext h2 detection

    for i, line, in_fence in iter_fenced_lines(lines):
        stripped = line.strip()
        if in_fence:
            prev_text_line_idx = None
        elif re.match(r'^ {0,3}#{2,3} ', line):
            # ATX heading: ## or ### with up to 3 optional leading spaces
            heading_lines.append(i)
            prev_text_line_idx = None
        elif stripped and re.match(r'^[-=]+\s*$', stripped):
            # Potential setext underline (--- for h2, === for h1) or thematic break.
            # If preceded by a non-empty text line, it's a setext heading.
            # Exclude lines that look like list items (e.g. "- " stripped to "-"):
            # a list bullet is "[-*+] " so if the raw line starts with a list marker
            # followed by a space, it's not a setext underline.
            is_list_item = bool(re.match(r'^[-*+]\s', line))
            prev_is_list = prev_text_line_idx is not None and bool(
                re.match(r'^[ \t]{0,3}[-*+]\s', lines[prev_text_line_idx])
            )
            if re.match(r'^-+\s*$', stripped) and prev_text_line_idx is not None and not is_list_item and not prev_is_list:
                heading_lines.append(prev_text_line_idx)
            # The underline/thematic-break line itself is never a heading text line.
            prev_text_line_idx = None
        elif stripped:
            prev_text_line_idx = i
        else:
            prev_text_line_idx = None

    if not heading_lines:
        return [content]
    sections = [''.join(lines[:heading_lines[0]])]
    for j, pos in enumerate(heading_lines):
        end = heading_lines[j + 1] if j + 1 < len(heading_lines) else len(lines)
        sections.append(''.join(lines[pos:end]))
    return sections


def content_hash(text: str) -> str:
    """Return a short hex hash of *text* for change detection."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def section_char_range(full_content: str, section_idx: int) -> tuple[int, int] | None:
    """Return (start_char, end_char) of section *section_idx* within *full_content*.

    section_idx is 1-based (0 = preamble, which is not editable as a section).
    Returns None if the index is invalid.
    """
    sections = split_sections(full_content)
    if not (1 <= section_idx <= len(sections) - 1):
        return None
    # Character offset = sum of lengths of all prior sections
    start = sum(len(s) for s in sections[:section_idx])
    end = start + len(sections[section_idx])
    return start, end


def strip_h1(content: str) -> str:
    """Remove the first H1 line so it isn't duplicated below the page header."""
    lines = content.split("\n")
    for i in range(_body_start(lines), len(lines)):
        if lines[i].strip():
            if re.match(r"^#\s+", lines[i]):
                del lines[i]
            break
    return "\n".join(lines)


_FRONTMATTER_FENCE = '---'


def _frontmatter_end(lines: list[str]) -> int | None:
    """The index of the line closing a leading frontmatter block, or None
    when the text has none (no opening fence, or it is never closed)."""
    if not lines or lines[0].strip() != _FRONTMATTER_FENCE:
        return None
    return next((i for i in range(1, len(lines)) if lines[i].strip() == _FRONTMATTER_FENCE), None)


def _body_start(lines: list[str]) -> int:
    """The index of the first line after a leading frontmatter block."""
    end = _frontmatter_end(lines)
    return 0 if end is None else end + 1


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse a leading frontmatter block (--- ... ---) for `tags` and `tldr`.

    Returns (metadata, content_with_frontmatter_removed). Absent or malformed
    frontmatter (no closing fence) yields an empty metadata dict and the
    original content unchanged.
    """
    lines = content.split("\n")
    end_idx = _frontmatter_end(lines)
    if end_idx is None:
        return {}, content

    meta: dict = {}
    for line in lines[1:end_idx]:
        if ':' not in line:
            continue
        key, _, value = line.partition(':')
        key = key.strip().lower()
        value = value.strip()
        if key == 'tags':
            value = value.strip('[]')
            tags = [t.strip().strip('"\'') for t in value.split(',') if t.strip()]
            if tags:
                meta['tags'] = tags
        elif key == 'tldr':
            value = value.strip('"\'')
            if value:
                meta['tldr'] = value

    rest = lines[end_idx + 1:]
    if rest and rest[0].strip() == '':
        rest = rest[1:]
    return meta, "\n".join(rest)


def plain_task_title(title: str) -> str:
    """Task title with wikilinks and /page/ links reduced to their labels.

    Used where the whole title is already inside one link (Linked tasks), so
    it must not contain anchors of its own — nested <a> is invalid HTML and
    browsers split it into separate links."""
    title = re.sub(r'\[\[([^\[\]]+)\]\]', lambda m: split_wikilink(m.group(1))[2], title)
    return re.sub(r'\[([^\]]*)\]\((/page/[^)]+)\)', r'\1', title)


def promoted_slug(folder: str, name: str, date: str = "") -> str:
    """Where a scratch note moved to the wiki goes: `folder/name`, with
    `date_` (YYYY-MM-DD) in front of the name when the date is kept, as in
    `conversations/2026-07-08_alex-sam-debrief`. The name may not add
    folders of its own: a / in it is a hyphen. Mirrored by promotedSlug in
    js/pagemenu.js for the dialog's preview."""
    name = to_slug(name.replace("/", "-"))
    if not name:
        return ""
    if date:
        name = f"{date}_{name}"
    return "/".join(p for p in (to_slug(folder), name) if p)


def page_wikilink(slug: str, label: str) -> str:
    """`[[slug|label]]`, with what would break a wikilink taken out of the
    label. Mirrored by the Move to wiki dialog in js/pagemenu.js, which copies it."""
    label = re.sub(r"[\[\]|]", "", label).strip() or slug
    return f"[[{slug}|{label}]]"


def note_as_section(content: str, heading: str) -> tuple[str, list[str]]:
    """A scratch note's text as a `## heading` section of another page, and
    its frontmatter tags (for that page's). The note's own title goes (the
    heading replaces it), a tldr becomes an italic first line, and its
    headings move down so the highest is a ###: they are subsections now."""
    meta, body = parse_frontmatter(content)
    lines = strip_h1(body).strip("\n").split("\n")
    levels = [len(m.group(1)) for _, line, fenced in iter_fenced_lines(lines)
              if not fenced and (m := re.match(r"^(#{1,6})[ \t]", line))]
    shift = max(0, 3 - min(levels)) if levels else 0
    if shift:
        for i, line, fenced in list(iter_fenced_lines(lines)):
            m = None if fenced else re.match(r"^(#{1,6})([ \t])", line)
            if m:
                lines[i] = "#" * min(6, len(m.group(1)) + shift) + line[len(m.group(1)):]
    parts = [f"## {heading}"]
    if meta.get("tldr"):
        parts.append(f"_{meta['tldr']}_")
    text = "\n".join(lines).strip("\n")
    if text:
        parts.append(text)
    return "\n\n".join(parts) + "\n", meta.get("tags", [])


def add_frontmatter_tags(content: str, tags: list[str]) -> str:
    """content with tags added to its frontmatter `tags:` (those it lacks,
    case ignored), adding the line, or the block, when there is none."""
    have = parse_frontmatter(content)[0].get("tags", [])
    new = [t for t in tags if t.lower() not in {h.lower() for h in have}]
    if not new:
        return content
    line = f"tags: {', '.join(have + new)}"
    lines = content.split("\n")
    end = _frontmatter_end(lines)
    if end is None:
        return f"---\n{line}\n---\n{content}"
    for i in range(1, end):
        if lines[i].partition(":")[0].strip().lower() == "tags":
            lines[i] = line
            break
    else:
        lines.insert(1, line)
    return "\n".join(lines)


def splice_section(full: str | None, file_hash: str, start_char: int,
                    end_char: int, new_content: str) -> tuple[str, str, int, int] | None:
    """Replace full[start_char:end_char], guarded by a hash check.

    full is the file's current content (None if the file doesn't exist).
    Returns (merged_full_content, new_file_hash, new_start_char, new_end_char)
    or None if the file doesn't exist or the hash doesn't match.
    """
    if full is None:
        return None
    if content_hash(full) != file_hash:
        return None
    if not (0 <= start_char <= end_char <= len(full)):
        return None
    # Preserve the trailing whitespace pattern from the original range
    orig_slice = full[start_char:end_char]
    orig_trailing = orig_slice[len(orig_slice.rstrip('\n')):]
    replacement = new_content.rstrip('\n') + orig_trailing
    merged = full[:start_char] + replacement + full[end_char:]
    new_end = start_char + len(replacement)
    return merged, content_hash(merged), start_char, new_end


def page_tags(content: str) -> list[str]:
    """A page's frontmatter tags, lowercased, for #tag queries."""
    return [t.lower() for t in parse_frontmatter(content)[0].get("tags", [])]
