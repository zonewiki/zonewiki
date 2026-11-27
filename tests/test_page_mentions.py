"""Mentions on pages (docs/spec/navigation.md, Mentions on pages): @name in
a page's text tells that user once, when a commit adds the name: a row on
Updates and a number on the bell, a mail for who turned it on, a part of
the morning's mail. Only who reads the page is told, and never by an
import, a file changed on disk or a write of many pages. A mention is its
user's own, in .zone/watch/<user>.json, until they open the page."""

import json
import re
import subprocess

import pytest

from tests import settings
from tests.conftest import HOST
from tests.test_git import commit_messages, save
from tests.test_task_notify import BASE, alice, login, mail_on, mails, smtp  # noqa: F401  (fixtures)


@pytest.fixture()
def wiki_dir(git_wiki_dir):
    return git_wiki_dir


def kept(wiki_dir, name):
    path = wiki_dir / ".zone/watch" / f"{name}.json"
    return (json.loads(path.read_text()) if path.exists() else {}).get("mentions", {})


def bell(client):
    return client.get("/api/tasks/due-count", headers=HOST).get_json().get("updates", 0)


def rows(client):
    """Updates' mentions: (the page's title, who, where it leads)."""
    html = client.get("/updates", headers=HOST).get_data(as_text=True)
    return [(title, by, href) for href, title, by in re.findall(
        r'<li class="updates-row updates-mention"><a class="updates-row-page" href="([^"]*)">([^<]*)</a> '
        r'<span class="updates-row-what"><strong>([^<]*)</strong> mentioned you</span>', html)]


def as_user(client, name, slug, text):
    """name saves the page (the editor's Done); alice is signed in afterwards."""
    login(client, name)
    assert save(client, slug, text).status_code in (200, 302, 303)
    login(client, "alice")


def test_a_name_on_a_page_tells_that_user_once(alice, smtp, wiki_dir):
    mail_on(alice, "bob", "mentioned")
    save(alice, "roadmap", "# Roadmap\n\nAsk @bob about the budget, and @alice too. Mail bob@example.org or `@dan`.\n")
    assert kept(wiki_dir, "bob") == {"roadmap": {"at": kept(wiki_dir, "bob")["roadmap"]["at"], "by": "alice",
                                                 "sha": kept(wiki_dir, "bob")["roadmap"]["sha"]}}
    assert re.fullmatch(r"[0-9a-f]{7,40}", kept(wiki_dir, "bob")["roadmap"]["sha"])
    # Not herself, not a name in code, not the @ of a mail address.
    assert kept(wiki_dir, "alice") == {} and kept(wiki_dir, "dan") == {}
    (to, text), = mails(smtp, 1)
    text = text.replace("\r\n", "\n")
    assert to == "bob@example.org" and "Subject: alice mentioned you: Roadmap" in text
    assert "alice mentioned you on Zonewiki, on this page:\n\n  Roadmap\n" in text
    assert f"  {BASE}/page/roadmap#:~:text=%40bob\n" in text
    assert f"when someone mentions you. To turn it off, open {BASE}/notifications." in text
    # On his Updates, counted on the bell on every page, and nobody else's.
    assert bell(alice) == 0 and rows(alice) == []
    login(alice, "bob")
    assert bell(alice) == 1 and rows(alice) == [("Roadmap", "alice", "/page/roadmap#:~:text=%40bob")]
    assert '<span class="top-updates-count menu-count">1</span>' in alice.get("/page/index", headers=HOST).get_data(as_text=True)
    # The same name again, or kept through an edit, tells nobody again.
    as_user(alice, "alice", "roadmap", "# Roadmap\n\nAsk @bob about the budget, twice: @bob.\n")
    mails(smtp, 1)
    # Opening the page reads it: gone from Updates, and from his file.
    login(alice, "bob")
    assert alice.get("/page/roadmap", headers=HOST).status_code == 200
    assert bell(alice) == 0 and rows(alice) == [] and kept(wiki_dir, "bob") == {}
    assert "Nobody mentioned you on a page since you last looked." in alice.get("/updates", headers=HOST).get_data(as_text=True)
    # Named anew after the name was taken out: told again.
    as_user(alice, "alice", "roadmap", "# Roadmap\n")
    as_user(alice, "alice", "roadmap", "# Roadmap\n\n@bob, once more.\n")
    assert list(kept(wiki_dir, "bob")) == ["roadmap"]
    mails(smtp, 2)


def test_no_commit_and_no_page_is_changed_by_being_told(alice, smtp, wiki_dir):
    save(alice, "plan", "# Plan\n\n@bob\n")
    before = commit_messages(wiki_dir)
    login(alice, "bob")
    assert bell(alice) == 1
    assert alice.post("/updates/read", headers=HOST).status_code == 204       # Mark all read
    assert bell(alice) == 0 and kept(wiki_dir, "bob") == {}
    assert commit_messages(wiki_dir) == before
    assert "mentions" not in (wiki_dir / ".zone/watch/bob.json").read_text()  # his file keeps the mail he turned on, alone
    mails(smtp, 0)                                                          # he didn't turn the mail on


def test_only_who_reads_the_page_is_told(alice, smtp, wiki_dir):
    mail_on(alice, "bob", "mentioned")
    save(alice, "private/alice/diary", "# Diary\n\n@bob must never know.\n")
    assert kept(wiki_dir, "bob") == {}
    login(alice, "bob")
    assert bell(alice) == 0 and rows(alice) == []
    mails(smtp, 0)
    # A page he was told of and no longer sees tells him nothing.
    login(alice, "alice")
    save(alice, "open", "# Open\n\n@bob\n")
    mails(smtp, 1)
    (wiki_dir / "private/alice").mkdir(parents=True, exist_ok=True)
    (wiki_dir / "open.md").rename(wiki_dir / "private/alice/open.md")
    login(alice, "bob")
    assert bell(alice) == 0 and rows(alice) == []


def test_a_viewer_is_told_and_one_who_is_no_user_is_not(alice, smtp, wiki_dir):
    mail_on(alice, "carol", "mentioned,assigned")          # a viewer: the mention's mail is hers to have
    assert json.loads((wiki_dir / ".zone/watch/carol.json").read_text())["mail"] == ["mentioned"]
    save(alice, "handbook", "# Handbook\n\n@carol reads this, @nobody and @zed don't exist.\n")
    (to, text), = mails(smtp, 1)
    assert to == "carol@example.org" and "Subject: alice mentioned you: Handbook" in text
    login(alice, "carol")
    assert rows(alice) == [("Handbook", "alice", "/page/handbook#:~:text=%40carol")]
    page = alice.get("/notifications", headers=HOST).get_data(as_text=True)
    assert "<legend>Mentions</legend>" in page and "someone mentions me (@name) on a page" in page
    assert alice.post("/updates/read", headers=HOST).status_code == 204       # a viewer's own: allowed
    assert rows(alice) == []


def test_what_tells_nobody(alice, smtp, wiki_dir):
    git = lambda *args: subprocess.run(["git", *args], cwd=wiki_dir, check=True, capture_output=True, env=settings.GIT_ENV)
    # A file changed on disk and committed by hand.
    (wiki_dir / "byhand.md").write_text("# By hand\n\n@bob\n")
    git("add", "-A")
    git("-c", "user.name=x", "-c", "user.email=x@x", "commit", "-qm", "By hand")
    assert kept(wiki_dir, "bob") == {}
    # A text not committed yet (an auto-save): nothing until it is.
    resp = alice.post("/api/save/draft", json={"content": "# Draft\n\n@bob\n"}, headers=HOST)
    assert resp.status_code == 200 and kept(wiki_dir, "bob") == {}
    save(alice, "draft", "# Draft\n\n@bob\n")
    assert list(kept(wiki_dir, "bob")) == ["draft"]
    # A page that only moves names nobody anew.
    login(alice, "bob")
    alice.post("/updates/read", headers=HOST)
    login(alice, "alice")
    assert alice.post("/rename/draft", data={"new_name": "drafts/first"}, headers=HOST).status_code in (200, 302, 303)
    assert (wiki_dir / "drafts/first.md").exists() and kept(wiki_dir, "bob") == {}
    mails(smtp, 0)


def test_an_assistants_write_tells_as_its_users(alice, smtp, wiki_dir):
    from tests.test_privacy import mcp
    out = mcp(alice, "write_page", token="alice-token", slug="notes/standup", content="# Standup\n\n@bob takes the release.\n")
    assert "notes/standup" in out
    assert kept(wiki_dir, "bob")["notes/standup"]["by"] == "alice"
    # Appended by bob's assistant to a page that names alice: she is told, he isn't.
    mcp(alice, "append_to_page", token="bob-token", slug="notes/standup", content="@alice reviews it, says @bob.\n")
    assert kept(wiki_dir, "alice")["notes/standup"]["by"] == "bob" and list(kept(wiki_dir, "bob")) == ["notes/standup"]
    # An assistant asked what is new is told of the mention; its reading the page is no look of its user's.
    said = mcp(alice, "history", token="bob-token")
    assert "Pages that mention the user (@bob), not opened by them since: notes/standup (by alice, " in said
    assert "Standup" in mcp(alice, "read_page", token="bob-token", slug="notes/standup")
    assert list(kept(wiki_dir, "bob")) == ["notes/standup"]
    assert "mention the user" not in mcp(alice, "history", token="dan-token")


def test_the_boxes_say_that_a_mention_is_on_a_page_too(alice, smtp, wiki_dir):
    login(alice, "bob")
    html = alice.get("/notifications", headers=HOST).get_data(as_text=True)
    assert "which pages mention me" in html and "someone mentions me (@name), on a page or in a task" in html
    assert html.index("<legend>Mentions</legend>") < html.index("<legend>Tasks</legend>") < html.index("<legend>Each morning</legend>")


def test_an_import_of_notes_tells_nobody(alice, smtp, wiki_dir):
    """A folder of notes brought in holds text written elsewhere: its names tell nobody."""
    import io
    import zipfile
    from tests.test_import import import_id, upload
    mail_on(alice, "bob", "mentioned")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Vault/Meeting.md", "# Meeting\n\n@bob said yes.\n")
        z.writestr("Vault/Other.md", "# Other\n\nSee [[Meeting]].\n")
    resp = upload(alice, buf.getvalue(), name="Vault.zip")
    assert alice.post("/import/apply", data={"id": import_id(resp)}, headers=HOST).status_code == 303
    made = [p for p in wiki_dir.rglob("*.md") if "@bob said yes" in p.read_text()]
    assert len(made) == 1, list(wiki_dir.rglob("*.md"))
    assert kept(wiki_dir, "bob") == {}
    mails(smtp, 0)
    # Written there afterwards, a name on one of its pages tells as on any page.
    slug = made[0].relative_to(wiki_dir).as_posix().removesuffix(".md")
    if not slug.startswith("private/"):
        save(alice, slug, "# Meeting\n\n@bob said yes. And @dan?\n")
        assert list(kept(wiki_dir, "dan")) == [slug] and kept(wiki_dir, "bob") == {}


def names(client, page=None):
    """Who the editor's completion offers for @ on that page."""
    resp = client.get("/api/mentionable", query_string={} if page is None else {"page": page}, headers=HOST)
    assert resp.status_code == 200
    return resp.get_json()["names"]


def test_a_name_that_tells_is_a_chip(alice, smtp, wiki_dir):
    """@name of a user who reads the page is a chip, the reader's own name
    marked; any other @word is the text it was, and so is a name in code,
    in a mail address, or for who isn't signed in."""
    save(alice, "team", "# Team\n\n## Ask @bob\n\nAsk @bob, @Carol and @alice, not @nobody or @dan-x. Mail bob@example.org, run `@bob`.\n\n"
                        "[to @bob](https://example.org/x)\n")
    html = alice.get("/page/team", headers=HOST).get_data(as_text=True)
    assert 'Ask <span class="mention">@bob</span>, <span class="mention">@Carol</span> and ' \
           '<span class="mention mention-me" title="That is you">@alice</span>, not @nobody or @dan-x.' in html
    assert "bob@example.org" in html and "<code>@bob</code>" in html and 'mention">@nobody' not in html
    # In a heading and in a link's text too; the heading's id and the side column's label are of the text as written.
    assert re.search(r'<h2 id="ask-bob"[^>]*>Ask <span class="mention">@bob</span>', html)
    assert '<a href="https://example.org/x"' in html and 'to <span class="mention">@bob</span></a>' in html
    # bob reads it with his own name marked.
    login(alice, "bob")
    html = alice.get("/page/team", headers=HOST).get_data(as_text=True)
    assert '<span class="mention mention-me" title="That is you">@bob</span>, <span class="mention">@Carol</span>' in html
    # On her private page nobody else is told: only her own name is a chip.
    login(alice, "alice")
    save(alice, "private/alice/diary", "# Diary\n\n@bob and @alice.\n")
    html = alice.get("/page/private/alice/diary", headers=HOST).get_data(as_text=True)
    assert '@bob and <span class="mention mention-me" title="That is you">@alice</span>.' in html
    # The editor's preview shows what the page will.
    resp = alice.post("/api/preview?page=team", data="Hi @bob and @nobody", headers={**HOST, "Content-Type": "text/plain"})
    assert 'Hi <span class="mention">@bob</span> and @nobody' in resp.get_data(as_text=True)


def test_a_tasks_text_has_the_chip_too(alice, smtp, wiki_dir):
    from tests.test_task_notify import new
    new(alice, title="Review", description="Ask @bob, not @carol (a viewer) or @nobody.")
    resp = alice.post("/api/render-notes", json={"notes": ["Ask @bob, not @carol or @nobody."]}, headers=HOST)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert 'Ask <span class="mention">@bob</span>, not @carol or @nobody.' in resp.get_json()["notes"][0]


def test_the_completion_offers_who_reads_the_page(alice, smtp, wiki_dir):
    # A shared page: every user who can be told, a viewer too, without who asks.
    assert names(alice, "roadmap") == ["bob", "carol", "dan"]
    login(alice, "bob")
    assert names(alice, "roadmap") == ["alice", "carol", "dan"]
    # His private page: nobody would be told. Someone else's: nothing is said.
    assert names(alice, "private/bob/notes") == []
    assert names(alice, "private/alice/diary") == []
    # A task's text (no page): who a task can be given to.
    assert names(alice) == ["alice", "dan"] and names(alice, "") == ["alice", "dan"]
    assert names(alice, "../etc/passwd") == []
    # No names in sharing mode, and none for who isn't signed in.
    alice.set_cookie("zone-sharing", "1")
    assert names(alice, "roadmap") == []
    alice.delete_cookie("zone-sharing")
    alice.post("/logout", headers=HOST)
    resp = alice.get("/api/mentionable?page=roadmap", headers=HOST)
    assert resp.status_code in (302, 401, 403) or resp.get_json()["names"] == []
