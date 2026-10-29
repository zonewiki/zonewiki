"""Users ([user …] in .leaf/config, docs/spec/users.md): a login per user, the session
naming its user, per-user MCP tokens, and commits authored by whoever made
the change."""

import subprocess
from io import BytesIO

import pytest

from tests import settings
from tests.conftest import HOST, content_hash

USERS = {"alice": ("alice-pw", "alice-token"), "bob": ("bob-pw", "")}


@pytest.fixture()
def users(monkeypatch):
    monkeypatch.setattr(settings, "LEAF_USERS", dict(USERS))


def login(client, name, password):
    return client.post("/login", data={"username": name, "password": password}, headers=HOST)


def mcp(client, token, tool="list_pages", **args):
    return client.post("/mcp", headers={**HOST, "Authorization": f"Bearer {token}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})


def authors(wiki):
    """Each commit's author and subject, newest first."""
    out = subprocess.run(["git", "log", "--format=%an|%s"], cwd=wiki, capture_output=True, text=True,
                         check=True, env=settings.GIT_ENV).stdout
    return [tuple(line.split("|", 1)) for line in out.splitlines()]


class TestLogin:

    def test_login_form_asks_for_the_user_only_with_users(self, client, users, monkeypatch):
        body = client.get("/login", headers=HOST).data.decode()
        assert 'name="username"' in body and 'autocomplete="username"' in body
        monkeypatch.setattr(settings, "LEAF_USERS", {})
        monkeypatch.setattr(settings, "LEAF_PASSWORD", "pw")
        assert 'name="username"' not in client.get("/login", headers=HOST).data.decode()

    def test_each_user_signs_in_with_their_own_password(self, client, users):
        assert client.get("/page/index", headers=HOST).status_code == 302
        assert login(client, "alice", "bob-pw").status_code == 200
        resp = login(client, "Alice", "alice-pw")   # names are lowercase; case and spaces don't matter
        assert resp.status_code == 302
        body = client.get("/page/index", headers=HOST).data.decode()
        assert '<span class="signout-user" title="Signed in as alice">alice</span>' in body
        client.post("/logout", headers=HOST)
        assert login(client, "bob", "bob-pw").status_code == 302
        assert ">bob</span>" in client.get("/page/index", headers=HOST).data.decode()

    def test_wrong_login_keeps_the_name_and_says_so(self, client, users):
        for name, password in [("alice", "nope"), ("carol", "alice-pw"), ("", "")]:
            resp = login(client, name, password)
            body = resp.data.decode()
            assert resp.status_code == 200 and "Incorrect user name or password." in body
        body = login(client, '"><b>x', "pw").data.decode()
        assert 'value="&#34;&gt;&lt;b&gt;x"' in body and "<b>x" not in body
        assert client.get("/page/index", headers=HOST).status_code == 302

    def test_wrong_logins_are_throttled(self, client, users):
        for _ in range(5):
            login(client, "alice", "guess")
        resp = login(client, "alice", "alice-pw")
        assert resp.status_code == 429 and "Try again in" in resp.data.decode()

    def test_a_removed_user_is_signed_out(self, client, users, monkeypatch):
        login(client, "alice", "alice-pw")
        assert client.get("/page/index", headers=HOST).status_code == 200
        monkeypatch.setattr(settings, "LEAF_USERS", {"bob": USERS["bob"]})
        assert client.get("/page/index", headers=HOST).status_code == 302

    def test_switching_from_a_password_to_users_signs_out(self, client, monkeypatch):
        monkeypatch.setattr(settings, "LEAF_PASSWORD", "pw")
        client.post("/login", data={"password": "pw"}, headers=HOST)
        assert client.get("/page/index", headers=HOST).status_code == 200
        monkeypatch.setattr(settings, "LEAF_PASSWORD", "")
        monkeypatch.setattr(settings, "LEAF_USERS", dict(USERS))
        assert client.get("/page/index", headers=HOST).status_code == 302

    def test_no_name_without_users(self, client, monkeypatch):
        monkeypatch.setattr(settings, "LEAF_PASSWORD", "pw")
        client.post("/login", data={"password": "pw"}, headers=HOST)
        body = client.get("/page/index", headers=HOST).data.decode()
        assert "signout-form" in body and "signout-user" not in body


class TestMCP:

    def test_each_user_has_their_own_token(self, client, users):
        assert mcp(client, "alice-token").status_code == 200
        assert mcp(client, "wrong").status_code == 401
        assert mcp(client, "").status_code == 401   # bob has none: no empty token gets in

    def test_off_without_any_token(self, client, monkeypatch):
        monkeypatch.setattr(settings, "LEAF_USERS", {"bob": USERS["bob"]})
        resp = mcp(client, "x")
        assert resp.status_code == 403 and "./run.sh user token NAME" in resp.get_json()["error"]


class TestAuthors:

    def test_commits_are_by_whoever_made_the_change(self, client, git_wiki_dir, users):
        login(client, "alice", "alice-pw")
        client.post("/new", data={"slug": "plans"}, headers=HOST)
        client.post("/api/deck", json={"title": "Call Sam"}, headers=HOST)
        client.post("/api/scratch", data={"title": "Idea"}, headers=HOST)
        client.post("/api/upload", data={"file": (BytesIO(b"\x89PNG"), "a.png")},
                    content_type="multipart/form-data", headers=HOST)
        mcp(client, "alice-token", "write_page", slug="from-mcp", content="# From MCP\n")
        client.post("/logout", headers=HOST)
        login(client, "bob", "bob-pw")
        client.post("/rename/plans", data={"new_name": "plans-2026"}, headers=HOST)
        log = authors(git_wiki_dir)
        assert log[0] == ("bob", "Rename plans to plans-2026")
        by = {subject: author for author, subject in log[1:]}
        assert by["Create plans"] == "alice" and by["Upload a.png"] == "alice"
        assert by["Create deck task"] == "alice"
        assert [a for a, s in log if s.startswith("Create from-mcp")] == ["alice"]   # alice's token
        assert [a for a, s in log if s.endswith("_idea")] == ["alice"]               # the scratch note
        assert log[-1][0] == "Leaf"                                                  # Leaf's start-up commit

    def test_without_users_leaf_is_the_author(self, client, git_wiki_dir):
        client.post("/new", data={"slug": "plans"}, headers=HOST)
        assert authors(git_wiki_dir)[0] == ("Leaf", "Create plans")

    def test_history_names_the_author(self, client, git_wiki_dir, users):
        login(client, "alice", "alice-pw")
        client.post("/new", data={"slug": "plans"}, headers=HOST)
        body = client.get("/history/plans", headers=HOST).data.decode()
        assert '<span class="hist-by"> · alice</span>' in body
        client.post("/logout", headers=HOST)
        text = mcp(client, "alice-token", "history", slug="plans").get_json()["result"]["content"][0]["text"]
        assert "Create plans (by alice)" in text

    def test_comments_name_their_author(self, client, git_wiki_dir, users):
        login(client, "alice", "alice-pw")
        task = client.post("/api/deck", json={"title": "Call Sam"}, headers=HOST).get_json()
        resp = client.post(f"/api/deck/{task['id']}/comments", json={"text": "Left a message"}, headers=HOST)
        [comment] = resp.get_json()["comments"]
        assert comment["by"] == "alice"
        resp = client.put(f"/api/deck/{task['id']}/comments/{comment['id']}", json={"text": "Called"}, headers=HOST)
        assert resp.get_json()["comments"][0]["by"] == "alice"   # an edit keeps the author
        assert '"by": "alice"' in (git_wiki_dir / "_deck" / "tasks.json").read_text()
        client.post("/logout", headers=HOST)
        text = mcp(client, "alice-token", "search_tasks", query="Sam").get_json()["result"]["content"][0]["text"]
        assert "(alice): Called" in text

    def test_task_history_names_who_did_what(self, client, git_wiki_dir, users):
        login(client, "alice", "alice-pw")
        task = client.post("/api/deck", json={"title": "Water plants", "due_date": "2030-01-01",
                                              "recurrence": {"type": "weekly", "interval": 1}}, headers=HOST).get_json()
        client.put(f"/api/deck/{task['id']}", json={"due_date": "2030-01-02"}, headers=HOST)
        client.post("/logout", headers=HOST)
        login(client, "bob", "bob-pw")
        done = client.put(f"/api/deck/{task['id']}", json={"completed": True}, headers=HOST).get_json()
        events = [(e["event"], e.get("by")) for e in done["history"]]
        assert events == [("created", "alice"), ("due", "alice"), ("done", "bob")]
        [spawn] = [t for t in client.get("/api/deck", headers=HOST).get_json() if t["spawned_from"] == task["id"]]
        assert spawn["history"][-1]["event"] == "repeat" and spawn["history"][-1]["by"] == "bob"
        # Undo puts a snapshot back: the authors stay.
        client.put(f"/api/deck/{task['id']}", json={"completed": False, "history": done["history"][:2]}, headers=HOST)
        [back] = [t for t in client.get("/api/deck", headers=HOST).get_json() if t["id"] == task["id"]]
        assert [e.get("by") for e in back["history"]][:2] == ["alice", "alice"]

    def test_without_users_comments_and_history_have_no_author(self, client, git_wiki_dir):
        task = client.post("/api/deck", json={"title": "Call Sam"}, headers=HOST).get_json()
        client.post(f"/api/deck/{task['id']}/comments", json={"text": "Left a message"}, headers=HOST)
        client.put(f"/api/deck/{task['id']}", json={"due_date": "2030-01-02"}, headers=HOST)
        assert '"by"' not in (git_wiki_dir / "_deck" / "tasks.json").read_text()
        assert "hist-by" not in client.get("/history/index", headers=HOST).data.decode()


def git(wiki, *args):
    return subprocess.run(["git", *args], cwd=wiki, capture_output=True, text=True, check=True,
                          env=settings.GIT_ENV).stdout


class TestDrafts:
    """Several people on one page: the text someone auto-saved is
    committed in their name before another's write to the page, so it is
    in git under its author and not in the other's commit."""

    PAGE = "# Plans\n\nThe start.\n\n- [ ] Item\n"
    BOBS = "# Plans\n\nThe start.\n\n- [ ] Item\n\nBob's line.\n"

    @pytest.fixture()
    def bobs_draft(self, client, git_wiki_dir, users):
        """plans, committed; bob's auto-save on it, not committed; alice signed in."""
        (git_wiki_dir / "plans.md").write_text(self.PAGE)
        git(git_wiki_dir, "add", "-A")
        git(git_wiki_dir, "commit", "-q", "-m", "Plans")
        login(client, "bob", "bob-pw")
        assert client.post("/api/save/plans", data={"content": self.BOBS, "file_hash": content_hash(self.PAGE)},
                           headers=HOST).status_code == 200
        client.post("/logout", headers=HOST)
        login(client, "alice", "alice-pw")
        return git_wiki_dir

    def test_another_users_save_commits_the_draft_first(self, client, bobs_draft):
        alices = self.BOBS + "\nAlice's line.\n"
        assert client.post("/api/save/plans", data={"content": alices, "file_hash": content_hash(self.BOBS)},
                           headers=HOST).status_code == 200
        assert authors(bobs_draft)[0] == ("bob", "Save plans")
        assert git(bobs_draft, "show", "HEAD:plans.md") == self.BOBS
        client.post("/edit/plans", data={"content": alices, "file_hash": content_hash(alices)}, headers=HOST)
        assert authors(bobs_draft)[:2] == [("alice", "Update plans"), ("bob", "Save plans")]
        diff = git(bobs_draft, "show", "HEAD", "--format=")
        assert "+Alice's line." in diff and "+Bob's line." not in diff

    def test_done_by_another_user_doesnt_take_the_draft(self, client, bobs_draft):
        client.post("/edit/plans", data={"content": self.BOBS, "file_hash": content_hash(self.BOBS)}, headers=HOST)
        assert authors(bobs_draft)[0] == ("bob", "Save plans")
        assert git(bobs_draft, "status", "--porcelain") == ""

    def test_ones_own_drafts_stay_uncommitted(self, client, bobs_draft):
        client.post("/logout", headers=HOST)
        login(client, "bob", "bob-pw")
        more = self.BOBS + "More.\n"
        client.post("/api/save/plans", data={"content": more, "file_hash": content_hash(self.BOBS)}, headers=HOST)
        assert "Save plans" not in [s for a, s in authors(bobs_draft)]   # no commit per pause in typing
        client.post("/edit/plans", data={"content": more, "file_hash": content_hash(more)}, headers=HOST)
        assert authors(bobs_draft)[0] == ("bob", "Update plans")

    @pytest.mark.parametrize("write", ["checkbox", "delete", "rename", "commit-all", "commit-file", "mcp"])
    def test_other_writes_commit_the_draft_first(self, client, bobs_draft, write):
        """Bob's line goes into git in a commit of his, never in one of alice's."""
        if write == "checkbox":
            client.post("/api/toggle-checkbox/plans", data={"idx": "0"}, headers=HOST)
        elif write == "delete":
            client.post("/delete/plans", headers=HOST)
        elif write == "rename":
            client.post("/rename/plans", data={"new_name": "plans-2"}, headers=HOST)
        elif write == "commit-all":
            client.post("/api/commit-all", headers=HOST)
        elif write == "commit-file":
            client.post("/api/commit-file", json={"filepath": "plans.md"}, headers=HOST)
        else:
            mcp(client, "alice-token", "edit_page", slug="plans", old_string="The start.", new_string="The begin.")
        with_line = [author for author, diff in self.commits(bobs_draft) if "+Bob's line." in diff]
        assert with_line == ["bob"]

    @staticmethod
    def commits(wiki):
        """Each commit's author and diff, newest first."""
        shas = git(wiki, "log", "--format=%H").split()
        return [(git(wiki, "show", "-s", "--format=%an", sha).strip(), git(wiki, "show", "--format=", sha))
                for sha in shas]

    @pytest.mark.parametrize("write", ["rename a page it links to", "append a note to it"])
    def test_writes_that_change_it_on_the_way_commit_the_draft_first(self, client, bobs_draft, write):
        """A rename re-points links on bob's page, an append adds to it:
        his draft goes into git in his name first, not in alice's commit."""
        (bobs_draft / "golf.md").write_text("# Golf\n")
        git(bobs_draft, "add", "golf.md")
        git(bobs_draft, "commit", "-q", "-m", "Golf")
        (bobs_draft / "plans.md").write_text(self.BOBS + "\nSee [[golf]].\n")   # still bob's draft
        if write.startswith("rename"):
            client.post("/rename/golf", data={"new_name": "golfing"}, headers=HOST)
        else:
            note = client.post("/api/scratch", data={"title": "Idea"}, headers=HOST).get_json()["slug"]
            client.post(f"/append/{note}", data={"page": "plans", "heading": "Idea"}, headers=HOST)
        with_line = [author for author, diff in self.commits(bobs_draft) if "+Bob's line." in diff]
        assert with_line == ["bob"]

    def test_a_merge_names_whose_change_it_met(self, client, git_wiki_dir, users):
        """alice's save from the page as it was meets bob's draft: merged,
        named, and his draft committed as his first."""
        (git_wiki_dir / "plans.md").write_text(self.PAGE)
        login(client, "alice", "alice-pw")
        client.get("/edit/plans", headers=HOST)
        client.post("/logout", headers=HOST)
        login(client, "bob", "bob-pw")
        client.post("/api/save/plans", data={"content": self.BOBS, "file_hash": content_hash(self.PAGE)}, headers=HOST)
        client.post("/logout", headers=HOST)
        login(client, "alice", "alice-pw")
        resp = client.post("/api/save/plans", data={"content": self.PAGE.replace("The start.", "The begin."),
                                                    "file_hash": content_hash(self.PAGE), "merge": "1"}, headers=HOST)
        assert resp.get_json()["by"] == "bob" and "Bob's line." in resp.get_json()["content"]
        assert authors(git_wiki_dir)[0] == ("bob", "Save plans")

    def test_without_users_nothing_changes(self, client, git_wiki_dir):
        (git_wiki_dir / "plans.md").write_text(self.PAGE)
        client.post("/api/save/plans", data={"content": self.BOBS, "file_hash": content_hash(self.PAGE)},
                    headers=HOST)
        client.post("/api/save/plans", data={"content": self.BOBS + "x\n", "file_hash": content_hash(self.BOBS)},
                    headers=HOST)
        assert "Save plans" not in [s for a, s in authors(git_wiki_dir)]
