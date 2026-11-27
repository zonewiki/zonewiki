"""Import pages (Importing pages in docs/spec/editor.md): a zip of notes
uploaded in the browser waits, unpacked, in .zone/imports/<id>/ for its
uploader's Import or Cancel; the zip itself never stays. Who imports
where, what refuses an upload, and that the waiting folder goes in every
case: after Import, Cancel, a refusal, a second upload, an hour, a
restart."""

import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest

from tests import settings
from tests.conftest import HOST, notes_zip

pytestmark = pytest.mark.skipif(not settings.GIT_ENABLED, reason="git not installed")

USERS = {name: (f"{name}-pw", "") for name in ("alice", "bob", "carol", "dana")}


@pytest.fixture()
def wiki(git_wiki_dir, monkeypatch):
    """alice in the group dev, bob, carol a viewer, dana an admin."""
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"groups": "dev"}, "carol": {"role": "viewer"},
                                                    "dana": {"role": "admin"}})
    return git_wiki_dir


def sign_in(client, name):
    client.post("/logout", headers=HOST)
    assert client.post("/login", data={"username": name, "password": USERS[name][0]}, headers=HOST).status_code == 302
    return client


def git(wiki, *args):
    return subprocess.run(["git", *args], cwd=wiki, capture_output=True, text=True, check=True,
                          env=settings.GIT_ENV).stdout


def upload(client, zip_bytes=None, name="My Vault.zip", **fields):
    data = {**fields, "file": (BytesIO(notes_zip() if zip_bytes is None else zip_bytes), name)}
    return client.post("/import", data=data, headers=HOST)


def waiting(wiki):
    """The waiting imports' folders, and every file under them."""
    root = wiki / ".zone/imports"
    if not root.exists():
        return {}
    return {d.name: sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file())
            for d in root.iterdir()}


def import_id(resp):
    ids = set(re.findall(r'name="id" value="([0-9a-f]{32})"', resp.get_data(as_text=True)))
    assert len(ids) == 1, resp.get_data(as_text=True)[:2000]
    return ids.pop()


def test_a_private_import_shows_its_report_and_imports_on_import(client, wiki):
    sign_in(client, "alice")
    resp = upload(client, **{"for": "private"})
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200, body[:500]
    assert "Import “My Vault”" in body and "<strong>2 pages</strong>" in body and "<strong>1 file</strong>" in body
    assert "It is an Obsidian vault" in body   # its .obsidian/ left out, but seen
    assert "<code>private/alice/my-vault/</code>" in body
    # Nothing in the wiki yet; what waits is what was unpacked, never the zip.
    assert not (wiki / "private/alice/my-vault").exists()
    id = import_id(resp)
    assert waiting(wiki) == {id: ["import.json", "notes/My Vault/Garden.md", "notes/My Vault/Roses.md",
                                  "notes/My Vault/pic.png"]}
    assert json.loads((wiki / ".zone/imports" / id / "import.json").read_text())["user"] == "alice"
    resp = client.post("/import/apply", data={"id": id}, headers=HOST)
    assert resp.status_code == 303 and resp.headers["Location"] == "/page/private/alice/my-vault/index"
    assert "[[private/alice/my-vault/roses|Roses]]" in (wiki / "private/alice/my-vault/garden.md").read_text()
    assert (wiki / "private/alice/_assets/pic.png").read_bytes() == b"\x89PNG\r\n\x1a\n"
    assert git(wiki, "log", "-1", "--format=%s %an") == "Import 2 pages and 1 file from My Vault alice\n"
    assert waiting(wiki) == {}
    assert client.get("/page/private/alice/my-vault/index", headers=HOST).status_code == 200


def test_a_groups_import_and_one_into_the_shared_wiki(client, wiki):
    sign_in(client, "alice")
    resp = upload(client, **{"for": "group:dev"})
    assert "<code>groups/dev/my-vault/</code>" in resp.get_data(as_text=True)
    assert client.post("/import/apply", data={"id": import_id(resp)}, headers=HOST).headers["Location"] == \
        "/page/groups/dev/my-vault/index"
    assert (wiki / "groups/dev/my-vault/roses.md").exists() and (wiki / "groups/dev/_assets/pic.png").exists()
    # A group she isn't in is no choice: the shared wiki, as /new does.
    sign_in(client, "bob")
    resp = upload(client, folder="Notes/From Home", **{"for": "group:dev"})
    assert "<code>notes/from-home/</code>" in resp.get_data(as_text=True)
    assert client.post("/import/apply", data={"id": import_id(resp)}, headers=HOST).status_code == 303
    assert (wiki / "notes/from-home/garden.md").exists() and (wiki / "_assets/pic.png").exists()
    assert "[[notes/from-home/index|My Vault]]" in (wiki / "index.md").read_text()


def test_only_its_uploader_sees_applies_or_cancels_a_waiting_import(client, wiki):
    sign_in(client, "alice")
    id = import_id(upload(client))
    for who in ("bob", "dana"):   # an admin neither
        sign_in(client, who)
        assert client.post("/import/apply", data={"id": id}, headers=HOST).status_code == 404
        assert client.post("/import/cancel", data={"id": id}, headers=HOST).status_code == 404
        assert id not in client.get("/import", headers=HOST).get_data(as_text=True)
    assert list(waiting(wiki)) == [id] and not (wiki / "my-vault").exists()
    sign_in(client, "alice")
    assert client.post("/import/apply", data={"id": id}, headers=HOST).status_code == 303


def test_a_viewer_neither_sees_the_form_nor_uploads(client, wiki):
    sign_in(client, "carol")
    assert client.get("/import", headers=HOST).status_code == 403
    assert upload(client).status_code == 403 and waiting(wiki) == {}
    assert "Import pages…" not in client.get("/new", headers=HOST).get_data(as_text=True)
    assert "/import" not in {p["slug"] for p in client.get("/api/pages-list", headers=HOST).get_json()}
    sign_in(client, "bob")
    new = client.get("/new", headers=HOST, query_string={"for": "private"}).get_data(as_text=True)
    assert 'formaction="/import"' in new and "Import pages…" in new
    assert "/import" in {p["slug"] for p in client.get("/api/pages-list", headers=HOST).get_json()}
    form = client.get("/import", headers=HOST, query_string={"private": "1"}).get_data(as_text=True)
    assert 'enctype="multipart/form-data"' in form and 'name="private" value="1" checked' in form


@pytest.mark.parametrize("zip_bytes,words", [
    (notes_zip({"../evil.md": "# Evil\n"}), "not a zip of notes: the name"),
    (b"just some text, no zip", "not a zip of notes"),
    (notes_zip({"vault/pic.png": b"png"}), "no markdown notes"),
    (b"", "Choose the zip file"),
])
def test_a_refused_zip_leaves_nothing_waiting(client, wiki, zip_bytes, words):
    sign_in(client, "alice")
    resp = upload(client, zip_bytes)
    body = resp.get_data(as_text=True)
    assert resp.status_code == 400 and words in body.replace("&#34;", '"'), body[:2000]
    assert waiting(wiki) == {} and not (wiki / "my-vault").exists() and not (wiki.parent / "evil.md").exists()
    assert 'enctype="multipart/form-data"' in body   # the form again, to try another


def test_a_folder_there_already_is_refused(client, wiki):
    (wiki / "my-vault").mkdir()
    (wiki / "my-vault/old.md").write_text("# Old\n")
    sign_in(client, "alice")
    resp = upload(client)
    assert resp.status_code == 409 or resp.status_code == 400
    assert "is there already" in resp.get_data(as_text=True) and waiting(wiki) == {}
    resp = upload(client, folder="private/bob/x")
    assert resp.status_code == 404 and waiting(wiki) == {}


def test_too_big_a_zip_is_refused_and_says_so(client, wiki, monkeypatch):
    monkeypatch.setattr(settings, "CONFIG_FILE", "upload-max = 2KB\n")
    sign_in(client, "alice")
    resp = upload(client, notes_zip({"a.md": os.urandom(5000)}))   # random bytes don't pack
    assert resp.status_code == 413 and "at most 2 KB" in resp.get_data(as_text=True)
    assert waiting(wiki) == {}
    # Small as a zip, but more than four times upload-max unpacked.
    resp = upload(client, notes_zip({"a.md": "x" * 20000}))
    assert resp.status_code == 413 and "unpack to at most 8 KB" in resp.get_data(as_text=True)
    assert waiting(wiki) == {}


def test_a_full_wiki_refuses_the_upload(client, wiki, monkeypatch):
    (wiki / "big.md").write_text("# Big\n\n" + "x" * 5000)
    monkeypatch.setattr(settings, "CONFIG_FILE", "storage-max = 6KB\n")
    sign_in(client, "alice")
    resp = upload(client, notes_zip({"a.md": "x" * 3000}))
    assert resp.status_code == 507 and "room" in resp.get_data(as_text=True)
    assert waiting(wiki) == {}


def test_the_waiting_folder_goes_on_cancel_and_on_a_second_upload(client, wiki):
    sign_in(client, "alice")
    first = import_id(upload(client))
    second = import_id(upload(client, name="Other.zip"))
    assert list(waiting(wiki)) == [second]   # one waits per user
    assert client.post("/import/apply", data={"id": first}, headers=HOST).status_code == 404
    sign_in(client, "bob")
    bobs = import_id(upload(client))
    sign_in(client, "alice")
    resp = client.post("/import/cancel", data={"id": second}, headers=HOST)
    assert resp.status_code == 303 and resp.headers["Location"] == "/import"
    assert list(waiting(wiki)) == [bobs] and not (wiki / "other").exists()


def test_the_waiting_folder_goes_after_an_hour(client, wiki):
    sign_in(client, "alice")
    id = import_id(upload(client))
    meta = wiki / ".zone/imports" / id / "import.json"
    data = json.loads(meta.read_text())
    data["at"] = (datetime.now(timezone.utc) - timedelta(minutes=59)).isoformat()
    meta.write_text(json.dumps(data))
    client.get("/import", headers=HOST)
    assert list(waiting(wiki)) == [id]
    data["at"] = (datetime.now(timezone.utc) - timedelta(minutes=61)).isoformat()
    meta.write_text(json.dumps(data))
    assert client.post("/import/apply", data={"id": id}, headers=HOST).status_code == 404
    assert waiting(wiki) == {} and not (wiki / "my-vault").exists()


def test_the_waiting_folder_goes_at_a_restart(client, wiki, monkeypatch):
    sign_in(client, "alice")
    id = import_id(upload(client))
    (wiki / ".zone/imports/0123/notes").mkdir(parents=True)   # an upload cut off by a crash
    monkeypatch.setattr(settings, "CONFIG_FILE", "upload-max = 50MB\n")   # the server restarts with it
    sign_in(client, "alice")
    assert waiting(wiki) == {}
    assert client.post("/import/apply", data={"id": id}, headers=HOST).status_code == 404


def test_an_import_refused_at_import_keeps_what_waits(client, wiki):
    sign_in(client, "alice")
    id = import_id(upload(client))
    (wiki / "my-vault").mkdir()
    (wiki / "my-vault/meanwhile.md").write_text("# Meanwhile\n")
    resp = client.post("/import/apply", data={"id": id}, headers=HOST)
    assert resp.status_code in (400, 409) and "is there already" in resp.get_data(as_text=True)
    assert list(waiting(wiki)) == [id]


def test_a_note_named_as_a_guide_becomes_no_guide(client, wiki):
    """AGENTS.md and CLAUDE.md in a vault would be a folder's guide: renamed,
    and the report says why, so alice's import goes through and dana's makes
    no guide either."""
    vault = {"Vault/AGENTS.md": "# For my agents\n", "Vault/sub/CLAUDE.md": "# Claude\n", "Vault/a.md": "[[AGENTS]]\n"}
    for who in ("alice", "dana"):
        sign_in(client, who)
        resp = upload(client, notes_zip(vault), folder=f"{who}-vault")
        body = resp.get_data(as_text=True)
        assert "a page of that name would be a guide for assistants" in body.replace("&#39;", "'")
        assert client.post("/import/apply", data={"id": import_id(resp)}, headers=HOST).status_code == 303
        assert (wiki / f"{who}-vault/agents-.md").exists() and (wiki / f"{who}-vault/sub/claude-.md").exists()
        assert not (wiki / f"{who}-vault/agents.md").exists()
        assert f"[[{who}-vault/agents-|AGENTS]]" in (wiki / f"{who}-vault/a.md").read_text()


def test_the_import_is_undone_whole(client, wiki):
    sign_in(client, "alice")
    resp = upload(client)
    client.post("/import/apply", data={"id": import_id(resp)}, headers=HOST)
    sha = git(wiki, "rev-parse", "HEAD").strip()
    resp = client.post("/changes/undo", data={"sha": sha}, headers=HOST)
    assert resp.status_code in (302, 303)
    assert sorted(p.name for p in (wiki / "my-vault").rglob("*")) == [] and not (wiki / "_assets/pic.png").exists(), resp.headers
    assert "my-vault" not in (wiki / "index.md").read_text()
    # The empty folders the undo leaves are no clash: it can be imported again.
    resp = upload(client)
    assert resp.status_code == 200 and client.post("/import/apply", data={"id": import_id(resp)}, headers=HOST).status_code == 303
