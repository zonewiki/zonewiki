"""Version history in git: commits, a page's log, versions and diffs,
uncommitted files, deleted pages."""

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

import config
from mdtext import page_title, slug_display_name
from pages import iter_pages, update_references


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


# A git that hangs (a stale lock, a slow disk) must not hold its request
# forever: after this long it counts as failed, which every caller handles.
GIT_TIMEOUT = 60


def run_git(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(list(args), cwd=config.LEAF_DATA, capture_output=True, text=True,
                              env=_GIT_ENV, timeout=GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(list(args), 124, "", f"git timed out after {GIT_TIMEOUT}s")


# A pattern search (grep_pages) gets far less time than other git calls: a
# pattern that backtracks badly is killed, instead of a whole minute of work.
GREP_TIMEOUT = 10
# Whether this git has Perl-style patterns (grep -P, built with PCRE); else
# grep_pages uses extended regexes (-E). Found out on the first search.
_grep_perl: bool | None = None


class GrepError(ValueError):
    """A grep_pages pattern git refused (bad syntax) or that took too long."""


def grep_pages(pattern: str, ignore_case: bool = False) -> list[tuple[str, int, str]]:
    """Every line of every page matching a regex, as (slug, line number,
    line), in file order. git matches (`git grep --no-index`, so pages not
    yet committed count too) in its own process, which a timeout can kill;
    Python's re can't be stopped mid-match and would hold up the server.
    Without git, the pattern is matched as plain text."""
    global _grep_perl
    slugs = {path: slug for path, slug in iter_pages()}
    if not config.GIT_ENABLED:
        needle = pattern.lower() if ignore_case else pattern
        hits = []
        for path, slug in sorted(slugs.items(), key=lambda p: p[1]):
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            hits += [(slug, n, line) for n, line in enumerate(lines, 1)
                     if needle in (line.lower() if ignore_case else line)]
        return hits
    for perl in ((True, False) if _grep_perl is None else (_grep_perl,)):
        args = ["git", "grep", "--no-index", "--no-color", "-I", "-n", "-z", "-P" if perl else "-E"]
        if ignore_case:
            args.append("-i")
        try:
            r = subprocess.run([*args, "-e", pattern, "--", "*.md"], cwd=config.LEAF_DATA,
                               capture_output=True, encoding="utf-8", errors="replace",
                               env=_GIT_ENV, timeout=GREP_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise GrepError(f"the search took longer than {GREP_TIMEOUT}s; try a simpler pattern")
        if perl and r.returncode not in (0, 1) and "PCRE" in r.stderr:
            continue                 # this git has no -P: extended regexes instead
        _grep_perl = perl
        break
    if r.returncode not in (0, 1):
        raise GrepError(r.stderr.strip().removeprefix("fatal: ") or f"git grep failed ({r.returncode})")
    root = config.LEAF_DATA
    hits = []
    for row in r.stdout.splitlines():
        parts = row.split("\0", 2)
        if len(parts) == 3 and parts[1].isdigit():
            slug = slugs.get(root / parts[0])
            if slug is not None:     # pages only: not _assets/, _deck/, …
                hits.append((slug, int(parts[1]), parts[2]))
    return hits


def git_setup() -> None:
    if not config.GIT_ENABLED:
        return
    if not (config.LEAF_DATA / ".git").exists():
        run_git("git", "init")
        run_git("git", "config", "user.email", "leaf@localhost")
        run_git("git", "config", "user.name", "Leaf")
        gitignore = config.LEAF_DATA / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("", encoding="utf-8")
        run_git("git", "add", ".")
        run_git("git", "commit", "--allow-empty", "-m", "Initial import")
    else:
        r = run_git("git", "config", "user.name")
        if not r.stdout.strip():
            run_git("git", "config", "user.email", "leaf@localhost")
            run_git("git", "config", "user.name", "Leaf")
    if config.LEAF_GIT_REMOTE:
        existing = run_git("git", "remote", "get-url", "origin")
        if existing.returncode == 0:
            run_git("git", "remote", "set-url", "origin", config.LEAF_GIT_REMOTE)
        else:
            run_git("git", "remote", "add", "origin", config.LEAF_GIT_REMOTE)


def git_commit(paths: Path | list[Path], message: str) -> None:
    """Stage one path (or a list of paths) and commit them together."""
    if not config.GIT_ENABLED:
        return
    if isinstance(paths, Path):
        paths = [paths]
    rels = [str(p.relative_to(config.LEAF_DATA)) for p in paths]
    run_git("git", "add", "--", *rels)
    run_git("git", "commit", "-m", message)


def move_page(old_path: Path, new_path: Path, slug: str, new_slug: str, message: str) -> list[Path]:
    """Move a page's file, point every link to it at the new slug, and
    commit the move with the pages and deck files whose links changed;
    returns those files."""
    new_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.rename(new_path)
    changed = update_references(slug, new_slug)
    git_commit([old_path, new_path, *changed], message)
    return changed


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
    rel = str(path.relative_to(config.LEAF_DATA))
    count_args = ["-n", str(max_count)] if max_count else []
    r = run_git("git", "log", "--follow", *count_args, "--numstat", "--date=format:%Y-%m-%d %H:%M",
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
    rel = str(path.relative_to(config.LEAF_DATA))
    r = run_git("git", "show", f"{sha}:{rel}")
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
    as dicts with 'filepath' (relative to LEAF_DATA), 'mtime' (YYYY-MM-DD)
    and 'rel' (relative time); both empty for a deleted file.

    -z: paths come unquoted (without it git quotes a name with a space or
    an umlaut, "my file.pdf", and committing that path failed)."""
    if not config.GIT_ENABLED:
        return []
    r = run_git("git", "status", "--porcelain", "-z", "--untracked-files=all")
    fields = iter(r.stdout.split("\0"))
    files = []
    for entry in fields:
        status, filepath = entry[:2], entry[3:]
        if status[:1] in ("R", "C"):
            next(fields, None)   # a rename's or copy's old path
        if not status.strip() or not filepath or filepath.endswith("/"):
            continue
        fpath = config.LEAF_DATA / filepath
        mtime = rel = ""
        if fpath.exists():
            mtime_dt = datetime.fromtimestamp(fpath.stat().st_mtime)
            mtime, rel = mtime_dt.strftime("%Y-%m-%d"), relative_time(mtime_dt)
        files.append({"filepath": filepath, "mtime": mtime, "rel": rel})
    return files


def commit_message(filepath: str) -> str:
    """Default commit message for committing a file from the uncommitted list."""
    return f"Save {filepath[:-3]}" if filepath.endswith('.md') else f"Update {filepath}"


def git_diff(sha: str, path: Path, *also: Path) -> str | None:
    """What a commit changed in a file. `also`: its name before the commit,
    when the commit renamed it, so the rename is seen as one (a pure rename
    changes nothing) instead of a new file."""
    rels = [str(p.relative_to(config.LEAF_DATA)) for p in (path, *also)]
    r = run_git("git", "show", "--format=", "--patch", "-M", sha, "--", *dict.fromkeys(rels))
    return r.stdout.strip() if r.returncode == 0 else None


def uncommitted_entries() -> list[dict]:
    """Uncommitted files as {slug, title, filepath, mtime, rel} dicts (slug/title
    resolved for wiki pages; slug is None for assets and other files)."""
    entries = []
    for f in git_uncommitted_files():
        filepath = f["filepath"]
        if filepath.endswith('.md') and not filepath.startswith('_assets'):
            slug = filepath[:-3]
            fpath = config.LEAF_DATA / filepath
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
    r = run_git("git", "log", "--diff-filter=D", "--format=%H\t%P\t%as", "--name-only")
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
            r2 = run_git("git", "show", f"{current_parent}:{line}")
            title = page_title(r2.stdout, slug) if r2.returncode == 0 else slug_display_name(slug)
            deleted.append({"slug": slug, "title": title, "sha": current_sha, "date": current_date})
    return deleted


def version_label(entry: dict, entries: list[dict]) -> str:
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


def version_index(entries: list[dict], sha: str) -> int | None:
    return next((i for i, e in enumerate(entries) if e["sha"].startswith(sha)), None)


def version_path(path: Path, entries: list[dict], sha: str) -> Path:
    """The file's path at a commit: before a rename it had another name."""
    i = version_index(entries, sha)
    return config.LEAF_DATA / entries[i]["path"] if i is not None else path


def is_current(path: Path, entries: list[dict]) -> bool:
    """Whether the newest commit is what the page holds now (no edits on
    disk since, not deleted)."""
    if not entries or not path.exists():
        return False
    return git_show(entries[0]["sha"], version_path(path, entries, entries[0]["sha"])) == \
        path.read_text(encoding="utf-8")
