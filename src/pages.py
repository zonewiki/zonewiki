"""The wiki's pages as files: slugs and paths, listing, sharing mode, the
link graph (link targets, backlinks, rewriting links on a rename), the
deck's lists and home as pages declare them, and url() for links."""

import os
import posixpath
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from flask import g, has_app_context, has_request_context, request

import config
import decksyntax
import deckstore
from mdtext import (
    mask_code, page_title, parse_frontmatter, SCRATCH_RE, section_anchor, slug_display_name,
    split_wikilink, to_slug, unmask_code, WIKILINK_RE,
)


# For sharing the screen (js/actions.js toggleSharing, a cookie): the CSS hides what
# is around the page (html.sharing), and the server leaves out the pages that
# are not for sharing, which LEAF_NOSHARE names (config.NOSHARE_TAGS,
# config.NOSHARE_FOLDERS).
SHARING_COOKIE = "leaf-sharing"


# Matches any markdown link href (bare slugs, .md, etc.) — used for backlink scanning
_ANY_MD_LINK_RE = re.compile(r'\[(?:[^\]]*)\]\(([^)#?\s]+)[^)]*\)')


def url(*parts: str) -> str:
    """A path in Leaf from its parts, each percent-encoded ("/" kept): a page
    whose file name has a space, "#" or a quote still gets a working link,
    and the result is safe in an HTML attribute as it is.
    url("page", slug) → /page/<slug>."""
    return "/" + "/".join(quote(p, safe="/") for p in parts)


def is_protected(slug: str) -> bool:
    """Pages that must never be renamed or deleted via the UI."""
    return slug == "index"


def safe_path(slug: str) -> Path | None:
    """Return resolved path only if it's safely inside LEAF_DATA."""
    pages_root = config.LEAF_DATA.resolve()
    target = (config.LEAF_DATA / f"{slug}.md").resolve()
    try:
        target.relative_to(pages_root)
        return target
    except ValueError:
        return None


def iter_pages():
    """Yield (path, slug) for all pages, skipping _* subdirectories (assets,
    the semantic index) and hidden ones. It prunes them instead of walking
    them: rglob went through every object in .git on each call, and a search
    lists the pages several times."""
    root = str(config.LEAF_DATA)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(('_', '.'))]
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        prefix = "" if rel_dir == "." else rel_dir + "/"
        for name in filenames:
            if name.endswith(".md"):
                yield Path(dirpath, name), prefix + name[:-3]


def all_slugs() -> list[str]:
    return sorted(slug for _, slug in iter_pages())


def list_scratch() -> list[dict]:
    """All scratch notes as {slug, title, date}, newest first (in sharing
    mode without the ones not for sharing)."""
    entries = []
    for md_file in config.LEAF_DATA.glob("*.md"):
        m = SCRATCH_RE.match(md_file.stem)
        if not m or hidden_page(md_file.stem):
            continue
        ts = datetime.strptime(m.group(1), "%Y-%m-%d_%H-%M-%S")
        entries.append({"slug": md_file.stem, "title": page_meta(md_file, md_file.stem)[0],
                        "ts": ts, "date": ts.strftime("%Y-%m-%d %H:%M")})
    entries.sort(key=lambda n: n["ts"], reverse=True)
    return entries


def sharing_on() -> bool:
    """Whether this request comes from a browser in sharing mode."""
    return has_request_context() and request.cookies.get(SHARING_COOKIE) == "1"


# slug -> (mtime, whether its tags name it not for sharing)
_noshare_tag_cache: dict[str, tuple[float, bool]] = {}


def is_noshare(slug: str) -> bool:
    """A page LEAF_NOSHARE names: in a noshare folder, or with a noshare tag
    in its frontmatter (cached by the file's mtime)."""
    low = slug.lower()
    if any(low.startswith(f) or low == f[:-1] for f in config.NOSHARE_FOLDERS):
        return True
    if not config.NOSHARE_TAGS:
        return False
    path = safe_path(slug)
    try:
        mtime = path.stat().st_mtime if path else None
    except OSError:
        mtime = None
    if mtime is None:
        return False
    cached = _noshare_tag_cache.get(slug)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        tags = {t.lower() for t in parse_frontmatter(path.read_text(encoding="utf-8"))[0].get("tags", [])}
    except OSError:
        tags = set()
    result = bool(tags & config.NOSHARE_TAGS)
    _noshare_tag_cache[slug] = (mtime, result)
    return result


def hidden_page(slug: str) -> bool:
    """A page to leave out of this response: sharing mode is on and the page
    is not for sharing."""
    return sharing_on() and is_noshare(slug)


def page_task_refs(content: str) -> set[str]:
    """Ids of the tasks a page refers to with [[task:…]], code excluded."""
    masked, _ = mask_code(content)
    refs = [m.group(1).lower() for m in decksyntax.TASK_REF_RE.finditer(masked)]
    return {t["id"] for t in deckstore.resolve_task_refs(refs).values() if t}


def _resolve_relative_link(href: str, source_slug: str) -> str | None:
    """Resolve a relative link target (already stripped of .md) against the
    source page's directory, normalizing ../ and ./ segments."""
    source_dir = source_slug.split('/')[:-1]
    normalized: list[str] = []
    for part in source_dir + href.split('/'):
        if part == '..':
            if normalized:
                normalized.pop()
        elif part and part != '.':
            normalized.append(part)
    return '/'.join(normalized) if normalized else None


def _resolve_md_link(href: str, source_slug: str) -> str | None:
    """Resolve a relative .md link href to a slug, or None if external."""
    if href.startswith(('http://', 'https://', '//', '/')):
        return None
    return _resolve_relative_link(href[:-3], source_slug)


def _resolve_bare_link(href: str, source_slug: str) -> str | None:
    """Resolve a bare relative slug link (no extension) to a slug, or None if external."""
    if href.startswith(('http://', 'https://', '//', '/', '#')) or ':' in href:
        return None
    return _resolve_relative_link(href, source_slug)


def page_link_targets(content: str, file_slug: str, known_slugs: set[str] | None = None) -> set[str]:
    """Collect all slugs that a page links to (wikilinks + markdown links)."""
    targets: set[str] = set()
    for m in WIKILINK_RE.finditer(content):
        page = split_wikilink(m.group(1))[0]
        if page:
            targets.add(to_slug(page))
    for m in _ANY_MD_LINK_RE.finditer(content):
        href = m.group(1)
        if href.startswith('/page/'):
            # Absolute, as rewrite_page_links re-points it on a rename.
            resolved = href[len('/page/'):] or None
        elif href.endswith('.md'):
            resolved = _resolve_md_link(href, file_slug)
        else:
            resolved = _resolve_bare_link(href, file_slug)
        if resolved and (known_slugs is None or resolved in known_slugs):
            targets.add(resolved)
    return targets


# Cache of per-page derived data, keyed by slug -> (mtime, title, link targets).
# Same invalidation model as _search_doc_cache: re-derived when the on-disk
# mtime changes, so edits made outside the app stay reflected. Link targets are
# cached unfiltered; both consumers only ever look up slugs that exist, so
# filtering against the known-slug set is unnecessary. The last item is the
# params of the page's deck embeds (_page_deck_embeds): the separate lists it
# declares and whether it is the deck's home come from them.
_page_meta_cache: dict[str, tuple[float, str, set[str], list]] = {}


def _page_deck_embeds(content: str) -> list[dict]:
    """The params of each [[deck …]] on a page, outside code."""
    masked, _ = mask_code(content)
    return [decksyntax.parse_deck_params(m.group(1) or "")
            for m in decksyntax.DECK_RE.finditer(masked)]


def _embeds_lists(embeds: list[dict]) -> list[tuple[str, dict]]:
    """(list name, embed params) of each embed that declares a list."""
    return [(name, params) for params in embeds
            if (name := decksyntax.clean_list(params.get("list")))]


def page_deck_lists(content: str) -> list[tuple[str, dict]]:
    """(list name, embed params) of each [[deck list=…]] on a page, outside
    code."""
    return _embeds_lists(_page_deck_embeds(content))


def _page_meta_entry(md_file: Path, slug: str) -> tuple[float, str, set[str], list]:
    """The page's _page_meta_cache entry, re-derived when its mtime changed."""
    # Every page is scanned for list declarations (search, the due badge), so
    # one that vanished meanwhile or isn't UTF-8 must not break anything.
    try:
        mtime = md_file.stat().st_mtime
        cached = _page_meta_cache.get(slug)
        if cached and cached[0] == mtime:
            return cached
        content = md_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0.0, slug_display_name(slug), set(), []
    entry = (mtime, page_title(content, slug), page_link_targets(content, slug),
             _page_deck_embeds(content))
    _page_meta_cache[slug] = entry
    return entry


def page_meta(md_file: Path, slug: str) -> tuple[str, set[str]]:
    """Return (title, outgoing link targets) for a page, cached by mtime."""
    _, title, targets, _ = _page_meta_entry(md_file, slug)
    return title, targets


def deck_lists() -> dict[str, dict]:
    """Every separate list some page declares with [[deck list=<name>]]:
    {name: {"slug": the page, "title": its title, "params": its embed's
    params}}. When several pages declare one, the first slug A–Z is its home."""
    out: dict[str, dict] = {}
    for md_file, slug in sorted(iter_pages(), key=lambda p: p[1]):
        _, title, _, embeds = _page_meta_entry(md_file, slug)
        for name, params in _embeds_lists(embeds):
            out.setdefault(name, {"slug": slug, "title": title, "params": params})
    return out


def list_names() -> set[str]:
    """Every declared list name."""
    return set(deck_lists())


def task_list(task: dict, names: set[str] | None = None) -> str | None:
    """The separate list a task belongs to: the first (A–Z) of its tags that
    a page declares as a list ([[deck list=<tag>]]), or None. Otherwise a
    list tag is a tag: the main deck shows its tasks like any other."""
    names = list_names() if names is None else names
    hits = sorted({t.lower() for t in task.get("tags") or []} & names)
    return hits[0] if hits else None


def lookup_deck_home() -> dict | None:
    """The deck's home: the page with a [[deck … home=1]] embed, the user's
    own deck, as {"slug", "title", "params": that embed's params}; the first
    slug A–Z when several declare one. None: the Deck link goes to /deck."""
    for md_file, slug in sorted(iter_pages(), key=lambda p: p[1]):
        _, title, _, embeds = _page_meta_entry(md_file, slug)
        for params in embeds:
            if decksyntax.is_true(params.get("home")):
                return {"slug": slug, "title": title, "params": params}
    return None


def deck_home() -> dict | None:
    """The deck's home (lookup_deck_home), looked up once per request."""
    if not has_app_context():
        return lookup_deck_home()
    if "deck_home" not in g:
        g.deck_home = lookup_deck_home()
    return g.deck_home


def deck_home_slug() -> str | None:
    """The deck home's slug, looked up once per request."""
    home = deck_home()
    return home["slug"] if home else None


def all_backlink_counts() -> dict[str, int]:
    """Return a mapping of slug → number of pages that link to it."""
    counts: dict[str, int] = {}
    for md_file, file_slug in iter_pages():
        _, targets = page_meta(md_file, file_slug)
        for target in targets:
            if target != file_slug:
                counts[target] = counts.get(target, 0) + 1
    return counts


def find_backlinks(slug: str) -> list[dict]:
    """Return pages that contain a wikilink or markdown link resolving to slug."""
    results = []
    for md_file, file_slug in iter_pages():
        if file_slug == slug:
            continue
        title, targets = page_meta(md_file, file_slug)
        if slug in targets:
            results.append({"slug": file_slug, "title": title})
    results.sort(key=lambda r: r["title"].lower())
    return results


# A markdown link (not an image): prefix, label, href up to any #anchor/?query
# or title, and the remainder inside the parentheses.
_MD_LINK_REWRITE_RE = re.compile(r'(?<!!)(\[[^\]]*\]\()([^)#?\s]+)([^)]*\))')


def rewrite_page_links(text: str, old_slug: str, new_slug: str, source_slug: str = "",
                       section: str = "") -> str:
    """Point every link to old_slug in text at new_slug.

    With a `section` (a heading's text), links without an #anchor of their
    own point at that heading of new_slug: a scratch note appended to a page
    is that section now.

    Handles [[wikilinks]] (with or without |label) and markdown links in the
    forms backlinks recognise — relative (`old`, `../dir/old`, `old.md`) and
    absolute (`/page/old`) — keeping each link's style, anchor and title.
    Relative hrefs are recomputed from source_slug's directory. Code blocks
    and inline code are left untouched."""
    text, stash = mask_code(text)

    def wikilink(m: re.Match) -> str:
        target, sep, label = m.group(1).partition("|")
        page_name, hash_, sect = target.partition("#")
        if page_name.strip() and (page_name.strip() == old_slug or to_slug(page_name) == old_slug):
            if not hash_ and section:
                return f"[[{new_slug}#{section}{sep}{label}]]"
            return f"[[{new_slug}{hash_}{sect}{sep}{label}]]"
        return m.group(0)

    source_dir = posixpath.dirname(source_slug)
    anchor = f"#{section_anchor(section)}" if section else ""

    def md_link(m: re.Match) -> str:
        prefix, href, rest = m.groups()
        if not rest.startswith("#"):
            rest = anchor + rest
        if href.startswith("/page/"):
            if href[len("/page/"):] == old_slug:
                return f"{prefix}/page/{new_slug}{rest}"
            return m.group(0)
        is_md = href.endswith(".md")
        target = _resolve_md_link(href, source_slug) if is_md else _resolve_bare_link(href, source_slug)
        if target != old_slug:
            return m.group(0)
        new_href = posixpath.relpath(new_slug, source_dir or ".") + (".md" if is_md else "")
        return f"{prefix}{new_href}{rest}"

    text = WIKILINK_RE.sub(wikilink, text)
    text = _MD_LINK_REWRITE_RE.sub(md_link, text)
    return unmask_code(text, stash)


def update_references(old_slug: str, new_slug: str, section: str = "") -> list[Path]:
    """Rewrite links to old_slug across all pages and deck tasks after a rename
    (to a `section` of new_slug: see rewrite_page_links).

    Returns the files that were modified (pages and deck storage), so the
    rename commit can include them."""
    modified: list[Path] = []
    for md_file, file_slug in iter_pages():
        content = md_file.read_text(encoding="utf-8")
        new_content = rewrite_page_links(content, old_slug, new_slug, file_slug, section)
        if new_content != content:
            md_file.write_text(new_content, encoding="utf-8")
            modified.append(md_file)
    modified += deckstore.rename_page_refs(
        old_slug, new_slug, lambda text: rewrite_page_links(text, old_slug, new_slug, section=section))
    return modified


def page_folders() -> list[str]:
    """The folders that hold pages, A–Z (each parent of a page's slug), for
    filing a scratch note. Hidden pages don't name theirs in sharing mode."""
    folders = set()
    for _, slug in iter_pages():
        if hidden_page(slug):
            continue
        parts = slug.split("/")[:-1]
        folders.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    return sorted(folders)


def page_from_input(raw: str) -> str:
    """The slug a page field names: a slug, `/page/<slug>` (an #anchor
    dropped) or the page's name."""
    raw = raw.strip().removeprefix("/page/").split("#")[0].strip("/")
    path = safe_path(raw) if raw else None
    return raw if path and path.exists() else to_slug(raw)


def fmt_date(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
