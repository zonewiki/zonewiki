"""Version history in git: commits, a page's log, versions and diffs,
uncommitted files, deleted pages."""

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

import config
from mdtext import page_title, slug_display_name
from pages import iter_pages


SHA_RE = re.compile(r'^[0-9a-f]{7,40}$')


# git (2.47+) starts its automatic maintenance after a commit as a detached
# background process. In the container Leaf is PID 1 and never reaps that
# orphan, so every commit left a zombie process behind until the PID limit
# (2048) was reached and nothing could start any more. Not detached, git runs
# the (usually trivial) maintenance itself and reaps its own children.
_GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_COUNT": "2",
    "GIT_CONFIG_KEY_0": "maintenance.autoDetach", "GIT_CONFIG_VALUE_0": "false",
    "GIT_CONFIG_KEY_1": "gc.autoDetach", "GIT_CONFIG_VALUE_1": "false",
}


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), cwd=config.WIKI_DIR, capture_output=True, text=True, env=_GIT_ENV)


def git_setup() -> None:
    if not config.GIT_ENABLED:
        return
    if not (config.WIKI_DIR / ".git").exists():
        _git("git", "init")
        _git("git", "config", "user.email", "leaf@localhost")
        _git("git", "config", "user.name", "Leaf")
        gitignore = config.WIKI_DIR / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("", encoding="utf-8")
        _git("git", "add", ".")
        _git("git", "commit", "--allow-empty", "-m", "Initial import")
    else:
        r = _git("git", "config", "user.name")
        if not r.stdout.strip():
            _git("git", "config", "user.email", "leaf@localhost")
            _git("git", "config", "user.name", "Leaf")
    if config.GIT_REMOTE:
        existing = _git("git", "remote", "get-url", "origin")
        if existing.returncode == 0:
            _git("git", "remote", "set-url", "origin", config.GIT_REMOTE)
        else:
            _git("git", "remote", "add", "origin", config.GIT_REMOTE)


def git_commit(paths: Path | list[Path], message: str) -> None:
    """Stage one path (or a list of paths) and commit them together."""
    if not config.GIT_ENABLED:
        return
    if isinstance(paths, Path):
        paths = [paths]
    rels = [str(p.relative_to(config.WIKI_DIR)) for p in paths]
    _git("git", "add", "--", *rels)
    _git("git", "commit", "-m", message)


_RENAME_BRACES_RE = re.compile(r"\{([^{}]*) => ([^{}]*)\}")


def _numstat_path(p: str) -> str:
    """The path after a commit from a --numstat path, which for a rename
    reads "old => new" or "dir/{old => new}.md"."""
    if " => " not in p:
        return p
    if "{" in p:
        return _RENAME_BRACES_RE.sub(r"\2", p).replace("//", "/")
    return p.split(" => ", 1)[1]


def git_log(path: Path, max_count: int | None = None) -> list[dict]:
    """The commits of a file, newest first, across renames (--follow): sha,
    date, time (YYYY-MM-DD HH:MM), rel (relative date), msg, the lines
    added and deleted, and the file's path at that commit (`path`, relative
    to the wiki), which differs from today's before a rename."""
    if not config.GIT_ENABLED:
        return []
    rel = str(path.relative_to(config.WIKI_DIR))
    count_args = ["-n", str(max_count)] if max_count else []
    r = _git("git", "log", "--follow", *count_args, "--numstat", "--date=format:%Y-%m-%d %H:%M",
             "--format=\x1e%H\x1f%as\x1f%ar\x1f%ad\x1f%s", "--", rel)
    entries = []
    for record in r.stdout.split("\x1e")[1:]:
        head, _, stats = record.partition("\n")
        parts = head.split("\x1f", 4)
        if len(parts) != 5:
            continue
        added = deleted = 0
        at = rel
        for line in stats.splitlines():
            cols = line.split("\t", 2)
            if len(cols) == 3:
                added += int(cols[0]) if cols[0].isdigit() else 0
                deleted += int(cols[1]) if cols[1].isdigit() else 0
                at = _numstat_path(cols[2])
        entries.append({"sha": parts[0], "date": parts[1], "rel": parts[2], "time": parts[3],
                        "msg": parts[4], "added": added, "deleted": deleted, "path": at})
    return entries


def git_show(sha: str, path: Path) -> str | None:
    rel = str(path.relative_to(config.WIKI_DIR))
    r = _git("git", "show", f"{sha}:{rel}")
    return r.stdout if r.returncode == 0 else None


def relative_time(dt: datetime) -> str:
    """Return relative time string like '2 days ago'."""
    now = datetime.now()
    delta = now - dt
    
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        mins = seconds // 60
        return f"{mins} minute{'s' if mins != 1 else ''} ago"
    elif seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    elif seconds < 2592000:  # 30 days
        days = seconds // 86400
        return f"{days} day{'s' if days != 1 else ''} ago"
    elif seconds < 31536000:  # 365 days
        months = seconds // 2592000
        return f"{months} month{'s' if months != 1 else ''} ago"
    else:
        years = seconds // 31536000
        return f"{years} year{'s' if years != 1 else ''} ago"


def git_uncommitted_files() -> list[dict]:
    """Files with uncommitted changes (modified, added, deleted, untracked),
    as dicts with 'filepath' (relative to WIKI_DIR), 'mtime' (YYYY-MM-DD)
    and 'rel' (relative time); both empty for a deleted file.

    -z: paths come unquoted (without it git quotes a name with a space or
    an umlaut, "my file.pdf", and committing that path failed)."""
    if not config.GIT_ENABLED:
        return []
    r = _git("git", "status", "--porcelain", "-z", "--untracked-files=all")
    fields = iter(r.stdout.split("\0"))
    files = []
    for entry in fields:
        status, filepath = entry[:2], entry[3:]
        if status[:1] in ("R", "C"):
            next(fields, None)   # a rename's or copy's old path
        if not status.strip() or not filepath or filepath.endswith("/"):
            continue
        fpath = config.WIKI_DIR / filepath
        mtime = rel = ""
        if fpath.exists():
            mtime_dt = datetime.fromtimestamp(fpath.stat().st_mtime)
            mtime, rel = mtime_dt.strftime("%Y-%m-%d"), relative_time(mtime_dt)
        files.append({"filepath": filepath, "mtime": mtime, "rel": rel})
    return files


def _commit_message(filepath: str) -> str:
    """Default commit message for committing a file from the uncommitted list."""
    return f"Save {filepath[:-3]}" if filepath.endswith('.md') else f"Update {filepath}"


def git_diff(sha: str, path: Path, *also: Path) -> str | None:
    """What a commit changed in a file. `also`: its name before the commit,
    when the commit renamed it, so the rename is seen as one (a pure rename
    changes nothing) instead of a new file."""
    rels = [str(p.relative_to(config.WIKI_DIR)) for p in (path, *also)]
    r = _git("git", "show", "--format=", "--patch", "-M", sha, "--", *dict.fromkeys(rels))
    return r.stdout.strip() if r.returncode == 0 else None


def _uncommitted_entries() -> list[dict]:
    """Uncommitted files as {slug, title, filepath, mtime, rel} dicts (slug/title
    resolved for wiki pages; slug is None for assets and other files)."""
    entries = []
    for f in git_uncommitted_files():
        filepath = f["filepath"]
        if filepath.endswith('.md') and not filepath.startswith('_assets'):
            slug = filepath[:-3]
            fpath = config.WIKI_DIR / filepath
            title = page_title(fpath.read_text(encoding="utf-8"), slug) if fpath.exists() else slug_display_name(slug)
            entries.append({"slug": slug, "title": title, "filepath": filepath,
                            "mtime": f["mtime"], "rel": f["rel"]})
        else:
            entries.append({"slug": None, "title": filepath, "filepath": filepath,
                            "mtime": f["mtime"], "rel": f["rel"]})
    return entries


def list_deleted_pages() -> list[dict]:
    """Pages that exist in git history but not on disk, newest deletion first."""
    if not config.GIT_ENABLED:
        return []
    r = _git("git", "log", "--diff-filter=D", "--format=%H\t%P\t%as", "--name-only")
    if r.returncode != 0:
        return []
    existing_slugs = set(slug for _, slug in iter_pages())
    deleted, seen = [], set()
    current_sha = current_parent = current_date = None
    for raw in r.stdout.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if '\t' in line:
            parts = line.split('\t', 2)
            current_sha = parts[0]
            parents = parts[1] if len(parts) > 1 else ''
            current_parent = parents.split()[0] if parents.strip() else None
            current_date = parts[2] if len(parts) > 2 else ''
        elif current_parent and line.endswith('.md') and not line.startswith('.'):
            slug = line[:-3]
            if slug in existing_slugs or slug in seen:
                continue
            if slug.startswith('_assets'):
                continue
            seen.add(slug)
            r2 = _git("git", "show", f"{current_parent}:{line}")
            title = page_title(r2.stdout, slug) if r2.returncode == 0 else slug_display_name(slug)
            deleted.append({"slug": slug, "title": title, "sha": current_sha, "date": current_date})
    return deleted


def _version_label(entry: dict, entries: list[dict]) -> str:
    """What a commit did to the page, in words: Leaf's own commit messages
    ("Update p", "Rename a to b", "Restore p to 1a2b3c4", …) read alike in a
    list, so they are named by kind. Others are shown as written."""
    msg = entry["msg"]
    if re.fullmatch(r"(Update|Save) \S+", msg):
        return "Edited"
    if re.fullmatch(r"Create \S+", msg):
        return "Created"
    if re.fullmatch(r"Delete \S+", msg):
        return "Deleted"
    if msg == "Initial import":
        return "Imported"
    m = re.fullmatch(r"Rename (\S+) to (\S+)", msg)
    if m:
        if entry["path"] == f"{m.group(2)}.md":
            return f"Renamed from “{m.group(1)}”"
        return f"Link updated (“{m.group(1)}” renamed)"
    m = re.fullmatch(r"Promote (\S+) to (\S+)", msg)
    if m:
        if entry["path"] == f"{m.group(2)}.md":
            return "Moved from Scratch"
        return "Link updated (a scratch note moved to the wiki)"
    m = re.fullmatch(r"Append (\S+) to (\S+)", msg)
    if m:
        if entry["path"] == f"{m.group(2)}.md":
            return "Scratch note appended"
        if entry["path"] == f"{m.group(1)}.md":
            return f"Appended to “{m.group(2)}”"
        return "Link updated (a scratch note appended to a page)"
    m = re.fullmatch(r"Restore \S+ to ([0-9a-f]{7,40})", msg)
    if m:
        old = next((e for e in entries if e["sha"].startswith(m.group(1))), None)
        return f"Restored the version of {old['time']}" if old else "Restored an older version"
    return msg


def _version_index(entries: list[dict], sha: str) -> int | None:
    return next((i for i, e in enumerate(entries) if e["sha"].startswith(sha)), None)


def _version_path(path: Path, entries: list[dict], sha: str) -> Path:
    """The file's path at a commit: before a rename it had another name."""
    i = _version_index(entries, sha)
    return config.WIKI_DIR / entries[i]["path"] if i is not None else path


def _is_current(path: Path, entries: list[dict]) -> bool:
    """Whether the newest commit is what the page holds now (no edits on
    disk since, not deleted)."""
    if not entries or not path.exists():
        return False
    return git_show(entries[0]["sha"], _version_path(path, entries, entries[0]["sha"])) == \
        path.read_text(encoding="utf-8")
