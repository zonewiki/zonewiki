"""Scratch notes: quick notes with a timestamped name (SCRATCH_RE), kept
in the Scratch module until moved into the wiki or appended to a page.
Creating, moving and appending them, shared by the web routes (server.py)
and the MCP tools (mcp_tools.py), so both make the same files and commits.
`suffix` follows a commit's message (the MCP tools' summary and "(MCP)")."""

import re
from datetime import datetime, timedelta

from gitstore import git_commit, move_page
from mdtext import (add_frontmatter_tags, is_scratch, mask_code, note_as_section, promoted_slug,
                    SCRATCH_RE, section_anchor, to_slug)
from pages import safe_path, update_references


class ScratchError(ValueError):
    """A move or append refused, with a message for the user."""


def note_date(slug: str) -> str:
    """The day a scratch note was created (YYYY-MM-DD)."""
    return SCRATCH_RE.match(slug).group(1)[:10]


def append_heading(slug: str, title: str) -> str:
    """The section heading a scratch note gets on the page it is appended
    to: its title and its day."""
    return f"{title} ({note_date(slug)})"


def create(title: str, content: str | None = None, suffix: str = "") -> str:
    """A new scratch note named after title (a / in it is a hyphen, so it
    makes no folder), with content, or just `# title`; returns its slug.
    Two notes of one name in the same second: the second gets the next
    second's stamp, so neither overwrites the other."""
    title = title.strip() or "Untitled"
    name = to_slug(title.replace("/", "-")) or "untitled"
    now = datetime.now()
    while True:
        slug = f"{now:%Y-%m-%d_%H-%M-%S}_{name}"
        path = safe_path(slug)
        if path is None:
            raise ScratchError(f"{title!r} can't name a scratch note")
        if not path.exists():
            break
        now += timedelta(seconds=1)
    text = f"# {title}\n\n" if content is None else content.rstrip("\n") + "\n"
    path.write_text(text, encoding="utf-8")
    git_commit(path, f"Create {slug}{suffix}")
    return slug


def _note_path(slug: str):
    path = safe_path(slug) if is_scratch(slug) else None
    if path is None or not path.exists():
        raise ScratchError(f"There is no scratch note “{slug}”")
    return path


def move(slug: str, folder: str, name: str, keep_date: bool = False, suffix: str = "") -> str:
    """Move a scratch note into the wiki (Move to wiki): a rename to
    `folder/name` (promoted_slug), links to it following. Its own commit
    message, so the history says "Moved from Scratch". Returns the new slug."""
    old_path = _note_path(slug)
    new_slug = promoted_slug(folder, name, note_date(slug) if keep_date else "")
    if not new_slug:
        raise ScratchError("Give the page a name")
    if is_scratch(new_slug):
        raise ScratchError("That name would still be a scratch note")
    new_path = safe_path(new_slug)
    if new_path is None:
        raise ScratchError(f"“{new_slug}”: pages can't go in folders starting with '_' or '.'")
    if new_path.exists():
        raise ScratchError(f"A page named “{new_slug}” already exists")
    move_page(old_path, new_path, slug, new_slug, f"Promote {slug} to {new_slug}{suffix}")
    return new_slug


def append(slug: str, target: str, heading: str, suffix: str = "") -> str:
    """Append a scratch note to the page target as a `## heading` section
    (note_as_section, its tags into the page's frontmatter), and delete the
    note. Links to the note point at that section, all in one commit.
    Returns the section's anchor."""
    old_path = _note_path(slug)
    # [ ] | would break the wikilinks that now point at the section.
    heading = re.sub(r"[\[\]|]", "", heading).strip()
    target_path = safe_path(target) if target else None
    if not target_path or not target_path.exists():
        raise ScratchError(f"There is no page “{target}”" if target else "Choose a page")
    if target == slug:
        raise ScratchError("Choose another page than this note")
    if not heading:
        raise ScratchError("Give the section a heading")
    page = target_path.read_text(encoding="utf-8")
    anchor = section_anchor(heading)
    if any(section_anchor(h) == anchor for h in re.findall(
            r"^#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", mask_code(page)[0], re.M)):
        raise ScratchError(f"“{target}” already has a section “{heading}”")
    section, tags = note_as_section(old_path.read_text(encoding="utf-8"), heading)
    page = add_frontmatter_tags(page, tags)
    target_path.write_text(page.rstrip("\n") + "\n\n" + section, encoding="utf-8")
    old_path.unlink()
    modified_refs = update_references(slug, target, heading)
    git_commit([old_path, target_path, *modified_refs], f"Append {slug} to {target}{suffix}")
    return anchor
