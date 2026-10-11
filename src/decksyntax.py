"""The deck's syntax in page text: [[deck …]] embeds and their params,
[[task:<id>]] references, and how tags are written. Pure functions."""

import re


def clean_tags(tags) -> list[str]:
    """Tags as the filter field can match them: one word each (whitespace
    inside becomes "-", so "local llm" → "local-llm"), no leading "#", no
    empties, no case-insensitive duplicates (the first spelling wins). The
    filter splits on spaces, so a tag with a space could never be found."""
    out, seen = [], set()
    for tag in tags or []:
        tag = re.sub(r"\s+", "-", str(tag).strip().lstrip("#").strip()).strip("-")
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            out.append(tag)
    return out


def clean_list(name) -> str | None:
    """A list name as stored and matched: trimmed, lowercase, one word
    (whitespace becomes "-", like tags), no leading "#". Empty means no list."""
    name = re.sub(r"\s+", "-", str(name or "").strip().lstrip("#").strip().lower()).strip("-")
    return name or None


DECK_RE = re.compile(
    r'\[\[deck(?:\s+([^\]]*))?\]\]', re.IGNORECASE
)


def parse_deck_params(raw: str) -> dict:
    """Parse key=value pairs from [[deck key=val ...]]."""
    params: dict = {}
    if not raw:
        return params
    for token in raw.split():
        if "=" in token:
            k, v = token.split("=", 1)
            params[k.strip().lower()] = v.strip()
    return params


# The deck's views, by the names `[[deck view=…]]` and `?view=` use.
VIEWS = ("focus", "open", "plan", "completed")


def embed_view(params: dict) -> str:
    """The view an embed opens on (`view=`), "" for the default or a name
    that isn't one of VIEWS."""
    raw = params.get("view", "").strip().lower()
    return raw if raw in VIEWS else ""


def is_true(value) -> bool:
    """A yes/no embed param (`keep=1`, `home=yes`)."""
    return str(value or "").strip().lower() in ("1", "true", "yes")


def tag_param_matches(task: dict, raw: str) -> bool:
    """Whether a task passes an embed's `tag=` param, as deck.js reads it
    (DeckCore.tagParamToQuery): `a|b` any of them, else comma-separated tags
    that all must be there, a leading `-` excluding one."""
    raw = (raw or "").strip()
    if not raw:
        return True
    tags = {t.lower() for t in task.get("tags") or []}
    if "|" in raw:
        return bool(tags & {t.strip().lstrip("#").lower() for t in raw.split("|") if t.strip()})
    for t in (t.strip().lstrip("#").lower() for t in raw.split(",")):
        if t.startswith("-"):
            if t[1:] in tags:
                return False
        elif t and t not in tags:
            return False
    return True


def new_task_tags(params: dict) -> list[str]:
    """The tags a new task gets in an embed with these params, as its own
    "+ New" gives them (deck.js filterDefaultTags over the locked filter
    words): the `list=` tag, then the single tags of `tag=`. An `a|b` group
    names no one tag, and an excluded `-tag` adds nothing."""
    tags = [clean_list(params.get("list")) or ""]
    raw = (params.get("tag") or "").strip()
    if raw and "|" not in raw:
        tags += [t for t in (t.strip() for t in raw.split(",")) if not t.startswith("-")]
    return clean_tags(tags)


def in_scope(task: dict, scope: str | None) -> bool:
    """Whether a task belongs to a deck view: scope is a list name (tasks
    tagged with it) or None for every task (the main deck, tag embeds)."""
    if not scope:
        return True
    return scope in {t.lower() for t in task.get("tags") or []}


WIKILINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")
_MD_LINK_RE = re.compile(r'\[([^\]]*)\]\(/page/([^)]+)\)')


# A page refers to one task as [[task:<id>]]: the first SHORT_ID_LEN
# characters of its id (like a short git hash), or the whole id. The server
# renders it as a live chip (render._task_ref_html).
TASK_REF_RE = re.compile(r"\[\[task:([0-9a-fA-F][0-9a-fA-F-]{3,35})\]\]")


SHORT_ID_LEN = 8


def short_id(task_id: str) -> str:
    return task_id[:SHORT_ID_LEN]
