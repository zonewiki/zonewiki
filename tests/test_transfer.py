"""Moving a whole wiki: zonewiki export writes it to one file, zonewiki
import makes a wiki from such a file (Moving a wiki in
docs/spec/deployment.md). The archive is taken for what anyone may have made:
what it holds besides the history, the settings file and the users' own
files is never used, and those files are written anew from what they hold."""

import io
import json
import os
import shutil
import stat
import subprocess
import tarfile
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests import goserver, settings
from tests.conftest import HOST, create_task, write_page
from tests.goserver import GoClient, GoServer, binary

USERS = {"alice": ("alice-pw", "alice-token"), "bob": ("bob-pw", "")}


def zonewiki(*args, data, stdin: bytes | None = None, cwd=None) -> subprocess.CompletedProcess:
    """The binary's command on the wiki at data; its stderr as text."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("ZONE_")}
    r = subprocess.run([str(binary()), *map(str, args)], env={**env, "ZONE_DATA": str(data)}, input=stdin,
                       capture_output=True, timeout=120, cwd=cwd)
    r.stderr = r.stderr.decode()
    return r


def git(wiki, *args) -> str:
    return subprocess.run(["git", *args], cwd=wiki, capture_output=True, text=True, check=True,
                          env=settings.GIT_ENV).stdout.strip()


def commit(wiki, message="By hand") -> None:
    git(wiki, "add", "-A")
    git(wiki, "commit", "-qm", message, "--allow-empty")


def login(client, name):
    return client.post("/login", data={"username": name, "password": USERS[name][0]}, headers=HOST)


@pytest.fixture()
def source(git_wiki_dir, monkeypatch, tmp_path_factory):
    """A wiki with two users, pages (one private), an upload and a task,
    and Zonewiki running on it."""
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}})
    monkeypatch.setattr(settings, "HOSTS", frozenset({"wiki.example"}))
    monkeypatch.setattr(settings, "LOCALE", "de-AT")
    monkeypatch.setattr(settings, "QUERY_LOG", True)
    monkeypatch.setattr(settings, "CONFIG_FILE", "storage-max = 1GB\nnotice = Moving soon\n")
    wiki = git_wiki_dir
    write_page(wiki, "garden/roses", "# Roses\n\nPrune in March.\n")
    write_page(wiki, "private/alice/secret", "# Secret\n\nOnly Alice.\n")
    (wiki / "_assets" / "pic.bin").write_bytes(bytes(range(256)) * 4)
    with GoClient(tmp_path_factory.mktemp("go") / "source.log") as c:
        c.live.ensure()
        assert login(c, "alice").status_code == 302
        assert create_task(c, title="Water the roses").status_code in (200, 201)
        commit(wiki, "Pages")
        yield wiki


@pytest.fixture()
def out(tmp_path_factory) -> Path:
    """A folder beside the wiki, for archives and imported wikis (the test's
    tmp_path is the source wiki itself)."""
    return tmp_path_factory.mktemp("moved")


def export(wiki, out: Path) -> Path:
    archive = out / "wiki.tar.gz"
    r = zonewiki("export", "--output", archive, data=wiki)
    assert r.returncode == 0, r.stderr
    return archive


@contextmanager
def served(wiki: Path, monkeypatch, tmp_path_factory):
    """A client of Zonewiki on the wiki at wiki, with the settings file it has."""
    config = wiki / ".zone" / "config"
    text = config.read_text(encoding="utf-8") if config.exists() else ""
    monkeypatch.setattr(settings, "ZONE_DATA", wiki)
    monkeypatch.setattr(goserver, "config_text", lambda: text)
    with GoClient(tmp_path_factory.mktemp("go") / "moved.log") as c:
        c.live.ensure()
        yield c


def rebuilt(archive: Path, change) -> Path:
    """The archive with change(its wiki folder) made to what it holds."""
    folder = archive.parent / (archive.name + ".open")
    with tarfile.open(archive) as t:
        t.extractall(folder, filter="tar")
    change(folder / "wiki")
    changed = archive.parent / ("changed-" + archive.name)
    with tarfile.open(changed, "w:gz") as t:
        t.add(folder / "wiki", arcname="wiki")
    return changed


def names(archive: Path) -> list[str]:
    with tarfile.open(archive) as t:
        return t.getnames()


class TestExport:
    def test_the_archive_is_a_clone_with_the_settings_file(self, source, out):
        archive = out / "wiki.tar.gz"
        r = zonewiki("export", "--output", archive, data=source)
        assert r.returncode == 0, r.stderr
        assert f"Exported the wiki to {archive}" in r.stdout.decode()
        assert "hashes of their passwords" in r.stdout.decode()
        assert stat.S_IMODE(archive.stat().st_mode) == 0o600
        held = names(archive)
        for name in ("wiki/.git/HEAD", "wiki/index.md", "wiki/garden/roses.md", "wiki/private/alice/secret.md",
                     "wiki/_assets/pic.bin", "wiki/.zone-format", "wiki/.zone/config"):
            assert name in held, name
        # Of .zone/, the settings file alone: no session key, no index.
        assert [n for n in held if n.startswith("wiki/.zone/")] == ["wiki/.zone/config"]
        assert all(n == "wiki" or n.startswith("wiki/") for n in held)
        # Nothing of the export is left beside it, or in the wiki.
        assert [p.name for p in out.iterdir()] == ["wiki.tar.gz"]
        assert not [p for p in (source / ".zone").iterdir() if "export" in p.name]
        with tarfile.open(archive) as t:
            config = t.extractfile("wiki/.zone/config").read().decode()
            assert stat.S_IMODE(t.getmember("wiki/.zone/config").mode) == 0o600
            assert "origin" not in t.extractfile("wiki/.git/config").read().decode()
        assert "[user alice]" in config and "hosts = wiki.example" in config

    def test_a_file_that_is_there_is_not_written_over(self, source, out):
        archive = export(source, out)
        before = archive.read_bytes()
        r = zonewiki("export", "--output", archive, data=source)
        assert r.returncode == 1 and "is there already" in r.stderr
        assert archive.read_bytes() == before

    def test_the_default_name_is_dated_in_the_current_folder(self, source, out):
        r = zonewiki("export", data=source, cwd=out)
        assert r.returncode == 0, r.stderr
        (made,) = out.iterdir()
        assert made.name.startswith("zonewiki-20") and made.name.endswith(".tar.gz")

    def test_to_stdout_says_nothing_into_the_archive(self, source, out):
        r = zonewiki("export", "--output", "-", data=source)
        assert r.returncode == 0, r.stderr
        assert "Exported the wiki to stdout" in r.stderr
        with tarfile.open(fileobj=io.BytesIO(r.stdout)) as t:
            assert "wiki/garden/roses.md" in t.getnames()
        # Put together in the wiki's .zone/, and gone again.
        assert not [p for p in (source / ".zone").iterdir() if "export" in p.name]
        assert git(source, "status", "--porcelain") == ""

    def test_text_that_is_not_committed_is_named(self, source, out):
        write_page(source, "draft", "# Draft\n")
        (source / "garden" / "roses.md").write_text("# Roses\n\nChanged on disk.\n")
        r = zonewiki("export", "--output", out / "wiki.tar.gz", data=source)
        assert r.returncode == 0, r.stderr
        assert "2 files hold changes that aren't committed yet" in r.stderr
        assert "draft.md" in r.stderr and "garden/roses.md" in r.stderr
        with tarfile.open(out / "wiki.tar.gz") as t:
            assert "wiki/draft.md" not in t.getnames()
            assert b"Prune in March" in t.extractfile("wiki/garden/roses.md").read()

    def test_a_wiki_without_history_has_nothing_to_export(self, wiki_dir, out):
        r = zonewiki("export", "--output", out / "wiki.tar.gz", data=wiki_dir)
        assert r.returncode == 1 and "keeps no history" in r.stderr
        assert not list(out.iterdir())

    def test_a_link_in_the_history_is_a_file_in_the_archive(self, source, out):
        os.symlink("/etc/hostname", source / "link.md")
        commit(source, "A link")
        archive = export(source, out)
        with tarfile.open(archive) as t:
            member = t.getmember("wiki/link.md")
            assert member.isfile() and t.extractfile(member).read() == b"/etc/hostname"
        dest = out / "wiki"
        assert zonewiki("import", archive, data=dest).returncode == 0
        assert not (dest / "link.md").is_symlink() and (dest / "link.md").read_text() == "/etc/hostname"


class TestImport:
    def test_the_wiki_moves_with_its_history_and_its_users(self, source, out, monkeypatch, tmp_path_factory):
        archive = export(source, out)
        dest = out / "wiki"   # not there yet: the import makes it
        r = zonewiki("import", archive, data=dest)
        assert r.returncode == 0, r.stderr
        said = r.stdout.decode()
        assert f"Imported the wiki into {dest}" in said and "with 2 users (alice, bob)" in said
        assert "in format " in said
        assert "this host has its own: hosts, storage-max" in said
        assert "Everyone signs in again" in said

        assert (dest / "garden" / "roses.md").read_text() == "# Roses\n\nPrune in March.\n"
        assert (dest / "_assets" / "pic.bin").read_bytes() == bytes(range(256)) * 4
        assert (dest / ".zone-format").read_text() == (source / ".zone-format").read_text()
        assert not (dest / ".zone-import").exists()
        assert git(dest, "rev-parse", "HEAD") == git(source, "rev-parse", "HEAD")
        assert git(dest, "log", "--format=%s") == git(source, "log", "--format=%s")
        assert git(dest, "status", "--porcelain") == "" and git(dest, "remote") == ""
        subprocess.run(["git", "fsck", "--strict"], cwd=dest, check=True, capture_output=True, env=settings.GIT_ENV)

        config = dest / ".zone" / "config"
        text = config.read_text()
        assert stat.S_IMODE(config.stat().st_mode) == 0o600
        assert "[user alice]" in text and "[user bob]" in text and "role = admin" in text
        assert "locale = de-AT" in text and "notice = Moving soon" in text and "query-log = yes" in text
        assert "hosts" not in text and "storage-max" not in text
        # Of .zone/, what the import wrote: no key, no sessions of the old host.
        assert sorted(p.name for p in (dest / ".zone").iterdir()) == ["config", "imported"]

        with served(dest, monkeypatch, tmp_path_factory) as c:
            assert c.get("/page/garden/roses", headers=HOST).status_code == 302   # nobody is signed in
            assert login(c, "alice").status_code == 302
            assert b"Prune in March" in c.get("/page/garden/roses", headers=HOST).data
            assert b"Only Alice" in c.get("/page/private/alice/secret", headers=HOST).data
            assert b"Water the roses" in c.get("/api/tasks", headers=HOST).data
            assert c.get("/history/garden/roses", headers=HOST).status_code == 200
            mcp = c.post("/mcp", headers={**HOST, "Authorization": "Bearer alice-token"}, json={
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "read_page", "arguments": {"slug": "garden/roses"}}})
            assert mcp.status_code == 200 and b"Prune in March" in mcp.data
            assert login(c, "bob").status_code == 302
            assert b"Prune in March" in c.get("/page/garden/roses", headers=HOST).data
            private = c.get("/page/private/alice/secret", headers=HOST)
            assert private.status_code in (403, 404) and b"Only Alice" not in private.data

    def test_from_stdin(self, source, out):
        r = zonewiki("export", "--output", "-", data=source)
        dest = out / "wiki"
        r = zonewiki("import", "-", data=dest, stdin=r.stdout)
        assert r.returncode == 0, r.stderr
        assert (dest / "garden" / "roses.md").exists()

    def test_only_into_an_empty_folder(self, source, out):
        archive = export(source, out)
        dest = out / "wiki"
        dest.mkdir()
        (dest / "index.md").write_text("# Mine\n")
        (dest / "notes").mkdir()
        r = zonewiki("import", archive, data=dest)
        assert r.returncode == 1 and "isn't empty" in r.stderr and "index.md" in r.stderr
        assert (dest / "index.md").read_text() == "# Mine\n"
        assert sorted(p.name for p in dest.iterdir()) == ["index.md", "notes"]
        # The wiki it came from is one too.
        r = zonewiki("import", archive, data=source)
        assert r.returncode == 1 and "isn't empty" in r.stderr
        assert (source / "garden" / "roses.md").exists()

    def test_a_newer_format_is_refused(self, source, out):
        (source / ".zone-format").write_text("99\n")
        commit(source, "From the future")
        dest = out / "wiki"
        r = zonewiki("import", export(source, out), data=dest)
        assert r.returncode == 1 and "format 99" in r.stderr and "newer Zonewiki" in r.stderr
        assert list(dest.iterdir()) == []

    def test_an_archive_from_before_the_format_file_starts_without_init(self, source, out, monkeypatch,
                                                                         tmp_path_factory):
        git(source, "rm", "-q", ".zone-format")
        commit(source, "As an older Zonewiki left it")
        dest = out / "wiki"
        r = zonewiki("import", export(source, out), data=dest)
        assert r.returncode == 0, r.stderr
        assert "in format" not in r.stdout.decode() and not (dest / ".zone-format").exists()
        # The folder holds files and no .zone-format: Zonewiki starts on it
        # all the same, since an import made it, and notes the format.
        monkeypatch.setattr(settings, "ZONE_DATA", dest)
        env = {k: v for k, v in goserver._settings().items() if k != "ZONE_INIT"}
        GoServer(env, tmp_path_factory.mktemp("go") / "old.log", flags=goserver._flags(),
                 config=(dest / ".zone" / "config").read_text()).stop()
        assert (dest / ".zone-format").exists()
        assert "Note the format" in git(dest, "log", "-1", "--format=%s")

    def test_a_settings_file_zonewiki_would_not_start_with_is_refused(self, source, out):
        def broken(wiki):
            (wiki / ".zone" / "config").write_text("locale = de-AT\nnonsense = 1\n")
        dest = out / "wiki"
        r = zonewiki("import", rebuilt(export(source, out), broken), data=dest)
        assert r.returncode == 1 and "the archive's settings file" in r.stderr and "line 2" in r.stderr
        assert list(dest.iterdir()) == []

    def test_an_archive_without_a_settings_file(self, source, out):
        dest = out / "wiki"
        r = zonewiki("import", rebuilt(export(source, out), lambda wiki: shutil.rmtree(wiki / ".zone")), data=dest)
        assert r.returncode == 0, r.stderr
        assert "held no settings file" in r.stdout.decode()
        assert not (dest / ".zone" / "config").exists()

    def test_users_only_leaves_the_hosts_settings(self, source, out):
        dest = out / "wiki"
        r = zonewiki("import", "--users-only", export(source, out), data=dest)
        assert r.returncode == 0, r.stderr
        assert "this host has its own: hosts, query-log, storage-max, notice" in r.stdout.decode()
        text = (dest / ".zone" / "config").read_text()
        assert "[user alice]" in text and "[user bob]" in text and "role = admin" in text and "locale = de-AT" in text
        for key in ("hosts", "query-log", "storage-max", "notice"):
            assert key not in text, key

    def test_users_only_brings_addresses_not_confirmed(self, source, out, monkeypatch):
        """On the host a wiki moves to, a user's address waits for its
        owner (docs/spec/users.md, Mail addresses); a plain import keeps it."""
        def with_addresses(wiki):
            config = wiki / ".zone" / "config"
            config.write_text(config.read_text().replace("[user alice]\n", "[user alice]\nemail = alice@example.org\n")
                              .replace("[user bob]\n", "[user bob]\nemail-pending = bob@example.org\n"))
        archive = rebuilt(export(source, out), with_addresses)
        dest = out / "wiki"
        assert zonewiki("import", "--users-only", archive, data=dest).returncode == 0
        text = (dest / ".zone" / "config").read_text()
        assert "email-pending = alice@example.org" in text and "email-pending = bob@example.org" in text
        assert "\nemail = " not in text
        plain = out / "plain"
        assert zonewiki("import", archive, data=plain).returncode == 0
        assert "\nemail = alice@example.org" in (plain / ".zone" / "config").read_text()

    def test_users_only_takes_no_login_without_users(self, source, out):
        """A wiki with one password and no users: with --users-only its
        login is the host's to give (a first user)."""
        def one_password(wiki):
            (wiki / ".zone" / "config").write_text(
                f"password = {goserver.password_hash('pw')}\nmcp-token = {goserver.token_hash('t')}\nlocale = fr\n")
        archive = rebuilt(export(source, out), one_password)
        dest = out / "wiki"
        r = zonewiki("import", "--users-only", archive, data=dest)
        assert r.returncode == 0, r.stderr
        assert (dest / ".zone" / "config").read_text().strip().splitlines()[-1] == "locale = fr"
        assert "password" not in (dest / ".zone" / "config").read_text()
        assert "no users" in r.stdout.decode()
        # Without it, the wiki keeps its password and token.
        kept = out / "kept"
        assert zonewiki("import", archive, data=kept).returncode == 0
        assert "password = pbkdf2" in (kept / ".zone" / "config").read_text()
        assert "mcp-token = sha256:" in (kept / ".zone" / "config").read_text()

    def test_no_more_than_max_size(self, source, out):
        archive = export(source, out)
        dest = out / "wiki"
        r = zonewiki("import", "--max-size", "1KB", archive, data=dest)
        assert r.returncode == 1 and "takes more than 1 KB (--max-size)" in r.stderr
        assert list(dest.iterdir()) == []
        r = zonewiki("import", "--max-size", "lots", archive, data=dest)
        assert r.returncode == 1 and "isn't a size" in r.stderr
        r = zonewiki("import", "--max-size", "50MB", archive, data=dest)
        assert r.returncode == 0, r.stderr

    def test_max_size_counts_the_files_a_small_history_unpacks_to(self, source, out):
        (source / "_assets" / "zeros.bin").write_bytes(bytes(8 << 20))
        commit(source, "A file that compresses to nothing")
        archive = export(source, out)
        assert archive.stat().st_size < 1 << 20
        dest = out / "wiki"
        r = zonewiki("import", "--max-size", "2MB", archive, data=dest)
        assert r.returncode == 1 and "takes more than 2 MB (--max-size)" in r.stderr
        assert list(dest.iterdir()) == []
        assert zonewiki("import", "--max-size", "50MB", archive, data=dest).returncode == 0

    def test_work_unpacks_elsewhere_and_leaves_nothing(self, source, out):
        archive = export(source, out)
        work = out / "work"
        work.mkdir()
        dest = out / "wiki"
        r = zonewiki("import", "--work", work, archive, data=dest)
        assert r.returncode == 0, r.stderr
        assert list(work.iterdir()) == [] and (dest / "garden" / "roses.md").exists()

    def test_what_is_no_archive(self, source, out):
        dest = out / "wiki"
        (out / "notes.tar.gz").write_text("# Not an archive\n")
        r = zonewiki("import", out / "notes.tar.gz", data=dest)
        assert r.returncode == 1 and "not a Zonewiki export" in r.stderr
        assert list(dest.iterdir()) == []
        r = zonewiki("import", out / "missing.tar.gz", data=dest)
        assert r.returncode == 1 and "missing.tar.gz" in r.stderr
        assert zonewiki("import", data=dest).returncode == 2


class TestUntrustedArchive:
    """An import reads the history's objects and refs and the settings
    file. What else an archive holds (hooks, git's config, a work tree) is
    not used, whatever it says."""

    def test_hooks_config_and_work_tree_stay_in_the_archive(self, source, out, monkeypatch, tmp_path_factory):
        ran = out / "ran"

        def planted(wiki):
            hooks = wiki / ".git" / "hooks"
            hooks.mkdir(exist_ok=True)
            for hook in ("post-checkout", "reference-transaction", "post-index-change", "pre-auto-gc",
                         "post-merge", "pre-commit", "post-commit"):
                (hooks / hook).write_text(f"#!/bin/sh\ntouch {ran}-hook\n")
                (hooks / hook).chmod(0o755)
            with open(wiki / ".git" / "config", "a") as f:
                f.write(f"[core]\n\tfsmonitor = touch {ran}-fsmonitor; false\n\tpager = touch {ran}-pager\n"
                        f"\tsshCommand = touch {ran}-ssh\n\thooksPath = {hooks}\n"
                        f"[uploadpack]\n\tpackObjectsHook = touch {ran}-pack\n"
                        f"[alias]\n\tstatus = !touch {ran}-alias\n"
                        f"[filter \"x\"]\n\tsmudge = touch {ran}-filter\n\tclean = touch {ran}-filter\n")
            (wiki / ".git" / "info").mkdir(exist_ok=True)
            (wiki / ".git" / "info" / "attributes").write_text("* filter=x\n")
            (wiki / "planted.md").write_text("# Never committed\n")
            (wiki / "index.md").write_text("# Changed in the archive's work tree\n")
            (wiki / ".zone" / "secret_key").write_text("the archive's own session key")

        dest = out / "wiki"
        r = zonewiki("import", rebuilt(export(source, out), planted), data=dest)
        assert r.returncode == 0, r.stderr
        assert not (dest / "planted.md").exists()
        assert (dest / "index.md").read_text() == (source / "index.md").read_text()
        assert not (dest / ".zone" / "secret_key").exists()
        config = (dest / ".git" / "config").read_text()
        for word in ("fsmonitor", "pager", "sshCommand", "hooksPath", "packObjectsHook", "alias", "filter"):
            assert word not in config, word
        hooks = dest / ".git" / "hooks"
        assert not [h.name for h in hooks.iterdir() if not h.name.endswith(".sample")] if hooks.exists() else True
        assert not (dest / ".git" / "info" / "attributes").exists() or "filter=x" not in (
            dest / ".git" / "info" / "attributes").read_text()
        # Nor when Zonewiki then runs on it and commits.
        with served(dest, monkeypatch, tmp_path_factory) as c:
            assert login(c, "alice").status_code == 302
            assert create_task(c, title="After the move").status_code in (200, 201)
        assert not [p.name for p in out.iterdir() if p.name.startswith("ran")]

    @pytest.mark.parametrize("target", [".git/HEAD", ".git/packed-refs", ".git/refs/heads", ".git/objects",
                                        ".zone/config"])
    def test_a_link_where_the_history_or_the_settings_should_be(self, source, out, target):
        outside = out / "outside"
        outside.mkdir()
        (outside / "HEAD").write_text("ref: refs/heads/main\n")

        def linked(wiki):
            path = wiki / target
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
            os.symlink(outside if target.endswith(("heads", "objects")) else outside / "HEAD", path)

        dest = out / "wiki"
        r = zonewiki("import", rebuilt(export(source, out), linked), data=dest)
        assert r.returncode == 1, r.stdout
        assert "a link or a device" in r.stderr or "no git repository" in r.stderr
        assert list(dest.iterdir()) == []

    def test_names_that_lead_out_of_the_archive(self, source, out):
        archive = export(source, out)
        evil = out / "evil.tar.gz"
        with tarfile.open(archive) as old, tarfile.open(evil, "w:gz") as new:
            for member in old.getmembers():
                new.addfile(member, old.extractfile(member) if member.isfile() else None)
            for name in ("wiki/.git/objects/../../../../escaped", "../escaped"):
                info = tarfile.TarInfo(name)
                info.size = 1
                new.addfile(info, io.BytesIO(b"x"))
        dest = out / "wiki"
        r = zonewiki("import", evil, data=dest)
        assert r.returncode == 1 and "not a Zonewiki export" in r.stderr
        assert list(dest.iterdir()) == [] and not (out / "escaped").exists() and not (out.parent / "escaped").exists()

    def test_a_branch_name_git_would_not_take(self, source, out):
        def odd(wiki):
            (wiki / ".git" / "HEAD").write_text("ref: refs/heads/--upload-pack=touch\n")
        dest = out / "wiki"
        r = zonewiki("import", rebuilt(export(source, out), odd), data=dest)
        assert r.returncode == 1, r.stdout
        assert list(dest.iterdir()) == []

    def test_a_repository_with_no_branch_checked_out(self, source, out):
        def detached(wiki):
            (wiki / ".git" / "HEAD").write_text(git(wiki, "rev-parse", "HEAD") + "\n")
        dest = out / "wiki"
        r = zonewiki("import", rebuilt(export(source, out), detached), data=dest)
        assert r.returncode == 1 and "no branch checked out" in r.stderr
        assert list(dest.iterdir()) == []


class TestUnfinishedImport:
    def test_the_next_import_starts_over(self, source, out):
        archive = export(source, out)
        dest = out / "wiki"
        (dest / ".zone-import" / "wiki").mkdir(parents=True)
        (dest / "half.md").write_text("# Moved in before the import was killed\n")
        (dest / "garden").mkdir()
        r = zonewiki("import", archive, data=dest)
        assert r.returncode == 0, r.stderr
        assert "didn't finish" in r.stderr and "starting over" in r.stderr
        assert not (dest / "half.md").exists() and not (dest / ".zone-import").exists()
        assert (dest / "garden" / "roses.md").exists()

    def test_zonewiki_does_not_start_on_half_a_wiki(self, out):
        (out / ".zone-import").mkdir()
        (out / "index.md").write_text("# Home\n")
        env = {k: v for k, v in os.environ.items() if not k.startswith("ZONE_")}
        r = subprocess.run([str(binary()), "--addr", "127.0.0.1:0"], env={**env, "ZONE_DATA": str(out), "ZONE_INIT": "1"},
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 1 and "an import didn't finish" in r.stderr
        assert sorted(p.name for p in out.iterdir()) == [".zone-import", "index.md"]


@pytest.mark.skipif(not shutil.which("tar"), reason="needs tar")
def test_an_export_of_the_hosted_zonewiki_from_before_this_command(source, out):
    """Zonewiki CP's export was a shell script: git clone --no-hardlinks, the
    settings file copied in, tar. Those archives import: with the clone's
    origin, its hooks and config, which stay behind."""
    made = out / "export"
    made.mkdir()
    subprocess.run(["git", "clone", "--quiet", "--no-hardlinks", str(source), str(made / "wiki")], check=True,
                   env=settings.GIT_ENV)
    (made / "wiki" / ".zone").mkdir()
    shutil.copy(source / ".zone" / "config", made / "wiki" / ".zone" / "config")
    subprocess.run(["tar", "-C", str(made), "-czf", str(out / "old.tar.gz"), "wiki"], check=True)
    dest = out / "wiki"
    r = zonewiki("import", out / "old.tar.gz", data=dest)
    assert r.returncode == 0, r.stderr
    assert "with 2 users (alice, bob)" in r.stdout.decode()
    assert git(dest, "rev-parse", "HEAD") == git(source, "rev-parse", "HEAD")
    assert git(dest, "remote") == "" and git(dest, "branch", "-r") == ""


class TestWhatUsersKeep:
    """What each user keeps beside the wiki moves with it: what they watch
    and want mailed (.zone/watch/), what they have seen of the tasks' news
    (.zone/seen/). An import reads each file as anyone's making."""

    LOOK = {"at": "2026-11-20T10:00", "after": "0a1b2c3d4e5f", "comment": "-"}

    def keeps(self, wiki, folder, name, value):
        path = wiki / ".zone" / folder / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value))

    def kept(self, wiki, folder, name):
        path = wiki / ".zone" / folder / f"{name}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def test_watches_looks_and_mail_choices_move(self, source, out, monkeypatch, tmp_path_factory):
        head = git(source, "rev-parse", "HEAD")
        watch = {"watch": {"garden/": 1790000001, "garden/roses": 1790000000},
                 "seen": {"garden/roses": {"at": 1790000002, "sha": head}}, "mail": ["assigned", "digest"],
                 "mentions": {"garden/roses": {"at": 1790000003, "by": "bob", "sha": head[:7]}}}
        seen = {"0123abcd-0000-4000-8000-000000000000": self.LOOK}
        self.keeps(source, "watch", "alice", watch)
        self.keeps(source, "seen", "alice", seen)
        self.keeps(source, "watch", "carl", watch)          # removed since: no user of the wiki
        archive = export(source, out)
        held = names(archive)
        assert "wiki/.zone/watch/alice.json" in held and "wiki/.zone/seen/alice.json" in held
        assert not [n for n in held if "carl" in n]
        dest = out / "wiki"
        r = zonewiki("import", archive, data=dest)
        assert r.returncode == 0, r.stderr
        assert "What they watch, have read and want mailed came along (1 user)." in r.stdout.decode()
        assert self.kept(dest, "watch", "alice") == watch and self.kept(dest, "seen", "alice") == seen
        assert self.kept(dest, "watch", "bob") is None
        for folder in ("watch", "seen"):
            assert stat.S_IMODE((dest / ".zone" / folder / "alice.json").stat().st_mode) == 0o600
        with served(dest, monkeypatch, tmp_path_factory) as c:
            assert login(c, "alice").status_code == 302
            assert b">Unwatch</button>" in c.get("/page/garden/roses", headers=HOST).data
            assert login(c, "bob").status_code == 302
            assert b">Watch</button>" in c.get("/page/garden/roses", headers=HOST).data

    def test_a_users_file_is_written_anew_from_what_it_holds(self, source, out):
        self.keeps(source, "watch", "alice", {"watch": {"garden/roses": 5}})
        archive = export(source, out)

        def change(wiki):
            self.keeps(wiki, "watch", "alice", {
                "watch": {"ok/page": 5, "folder/": 6, "../../etc/passwd": 5, "a//b": 5, "/abs": 5, "never": -1, "nul\u0000l": 5},
                "seen": {"ok/page": {"at": 1, "sha": "zz; rm -rf"}, "folder/in": {"at": 2, "sha": "abcdef12"},
                         "unwatched": {"at": 1, "sha": "abcdef12"}},
                "mail": ["digest", "nonsense", "assigned", "digest"], "admin": True, "extra": {"x": ["y"]},
                "mentions": {"ok/page": {"at": 7, "by": "bob", "sha": "abcdef1"}, "../up": {"at": 7, "by": "bob"},
                             "folder/": {"at": 7, "by": "bob"}, "by/script": {"at": 7, "by": "<b>bob</b>"},
                             "no/time": {"at": 0, "by": "bob"}, "bad/sha": {"at": 7, "by": "bob", "sha": "$(rm)"}}})
            self.keeps(wiki, "watch", "bob", "{not json")
            self.keeps(wiki, "watch", "mallory", {"watch": {"ok/page": 5}})      # no user of the wiki
            self.keeps(wiki, "seen", "alice", {
                "not a task": self.LOOK, "0123abcd": {"at": "yesterday"}, "89abcdef": {**self.LOOK, "after": "<script>"},
                "4567abcd": {**self.LOOK, "comment": "../x"}, "cdef0123": self.LOOK, "fedc3210": {"at": "2026-11-20T10:00"}})
            (wiki / ".zone" / "seen" / "bob.json").symlink_to("/etc/hostname")  # a link where a file should be
            self.keeps(wiki, "other", "alice", {"watch": {"ok/page": 5}})        # no folder of the users' files
            (wiki / ".zone" / "watch" / "alice.txt").write_text("{}")

        dest = out / "wiki"
        r = zonewiki("import", rebuilt(archive, change), data=dest)
        assert r.returncode == 0, r.stderr
        assert self.kept(dest, "watch", "alice") == {
            "watch": {"ok/page": 5, "folder/": 6}, "seen": {"folder/in": {"at": 2, "sha": "abcdef12"}},
            "mail": ["assigned", "digest"], "mentions": {"ok/page": {"at": 7, "by": "bob", "sha": "abcdef1"}}}
        assert self.kept(dest, "seen", "alice") == {"cdef0123": self.LOOK, "fedc3210": {"at": "2026-11-20T10:00"}}
        assert sorted(p.name for p in (dest / ".zone" / "watch").iterdir()) == ["alice.json"]
        assert sorted(p.name for p in (dest / ".zone" / "seen").iterdir()) == ["alice.json"]
        assert sorted(p.name for p in (dest / ".zone").iterdir()) == ["config", "imported", "seen", "watch"]
        assert not (dest / ".zone" / "seen" / "alice.json").is_symlink()

    def test_a_users_file_too_big_refuses_the_archive(self, source, out):
        archive = export(source, out)
        big = rebuilt(archive, lambda wiki: self.keeps(wiki, "watch", "alice", " " * (1 << 20) + "{}"))
        dest = out / "wiki"
        r = zonewiki("import", big, data=dest)
        assert r.returncode == 1 and "more than fits" in r.stderr
        assert not dest.exists() or not list(dest.iterdir())

    def test_no_users_no_files(self, source, out):
        self.keeps(source, "watch", "alice", {"watch": {"garden/roses": 5}})
        archive = export(source, out)
        dest = out / "wiki"
        r = zonewiki("import", rebuilt(archive, lambda wiki: (wiki / ".zone" / "config").unlink()), data=dest)
        assert r.returncode == 0, r.stderr
        assert "came along" not in r.stdout.decode() and not (dest / ".zone" / "watch").exists()
