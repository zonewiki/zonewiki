"""The task app's settings dialog (docs/spec/tasks.md, Settings): where an
embed has its gear, and POST /api/tasks/settings, which rewrites the page's
[[tasks: …]] or the settings of All tasks."""

import json

from tests.conftest import HOST, preview, rendered, write_page


def save(client, **body):
    return client.post("/api/tasks/settings", json=body, headers=HOST)


class TestGear:
    def test_a_page_view_has_the_gear_and_names_where_its_params_are(self, client, wiki_dir):
        html = rendered(client, wiki_dir, "[[tasks: tag=a]]\n\n[[tasks: list=shop  view=open]]\n", slug="lists")
        assert 'data-settings-page="lists" data-settings-index="0" data-settings-raw="tag=a"' in html
        assert 'data-settings-page="lists" data-settings-index="1" data-settings-raw="list=shop  view=open"' in html
        assert 'id="task-settings-btn"' in html and 'id="task-settings-btn-1"' in html
        assert 'id="task-settings-modal-1"' in html

    def test_a_preview_has_none(self, client, wiki_dir):
        html = preview(client, "[[tasks: tag=a]]\n")
        assert "task-settings" not in html and "data-settings" not in html

    def test_tasks_page_all_and_a_list(self, client, wiki_dir):
        write_page(wiki_dir, "shopping", "# Shopping\n\ntext\n\n[[tasks: tag=x]]\n\n[[tasks: list=shop keep=1]]\n")
        html = client.get("/tasks?list=shop", headers=HOST).get_data(as_text=True)
        assert 'data-settings-page="shopping" data-settings-index="1" data-settings-raw="list=shop keep=1"' in html
        html = client.get("/tasks", headers=HOST).get_data(as_text=True)
        assert 'data-settings-page="" data-settings-index="-1" data-settings-raw=""' in html
        # A tag no page declares has no params to change.
        html = client.get("/tasks?list=other", headers=HOST).get_data(as_text=True)
        assert "task-settings" not in html

    def test_the_headless_app_has_none(self, client, wiki_dir):
        assert "task-settings" not in client.get("/api/tasks/headless", headers=HOST).get_data(as_text=True)


class TestPlanParam:
    def test_plan_reaches_the_app(self, client, wiki_dir):
        assert 'data-init-view="" data-init-plan="off"' in preview(client, "[[tasks: plan=off]]\n")
        assert 'data-init-view="" data-init-plan=""' in preview(client, "[[tasks: plan=maybe]]\n")
        # view=open has always been the one list, view=plan the board; plan= wins.
        assert 'data-init-view="open" data-init-plan="off"' in preview(client, "[[tasks: view=open]]\n")
        assert 'data-init-view="plan" data-init-plan="on"' in preview(client, "[[tasks: view=open plan=on]]\n")
        assert 'data-init-view="open" data-init-plan="off"' in preview(client, "[[tasks: view=plan plan=off]]\n")
        assert 'data-init-view="focus" data-init-plan="off"' in preview(client, "[[tasks: view=focus plan=off]]\n")


class TestSave:
    def test_rewrites_the_embed_and_nothing_else(self, client, wiki_dir):
        text = "# Lists\n\n```\n[[tasks: tag=code]]\n```\n\n[[tasks: tag=a]]\n\nBetween.\n\n[[tasks: list=shop keep=1]]\n"
        path = write_page(wiki_dir, "lists", text)
        resp = save(client, page="lists", index=1, was="list=shop keep=1",
                    set={"view": "open", "limit": "5", "labels": "To_buy,Cart", "new": "weekly"})
        assert resp.status_code == 200 and resp.get_json() == {"ok": True}
        assert path.read_text() == text.replace(
            "[[tasks: list=shop keep=1]]", "[[tasks: list=shop keep=1 new=weekly view=open limit=5 labels=To_buy,Cart]]")
        html = client.get("/page/lists", headers=HOST).get_data(as_text=True)
        assert ">To buy</button>" in html and ">Cart</button>" in html and 'data-init-limit="5"' in html

    def test_an_empty_value_takes_the_param_out(self, client, wiki_dir):
        path = write_page(wiki_dir, "lists", "[[tasks: tag=a view=open pin=1]]\n")
        assert save(client, page="lists", index=0, was="tag=a view=open pin=1", set={"view": "", "pin": ""}).status_code == 200
        assert path.read_text() == "[[tasks: tag=a]]\n"

    def test_a_changed_page_is_refused(self, client, wiki_dir):
        path = write_page(wiki_dir, "lists", "[[tasks: tag=a]]\n")
        resp = save(client, page="lists", index=0, was="tag=b", set={"view": "open"})
        assert resp.status_code == 409 and "Reload" in resp.get_json()["error"]
        assert save(client, page="lists", index=3, was="", set={"view": "open"}).status_code == 409
        assert path.read_text() == "[[tasks: tag=a]]\n"

    def test_bad_values_and_pages(self, client, wiki_dir):
        path = write_page(wiki_dir, "lists", "[[tasks:]]\n")
        for bad in ({"view": "week"}, {"limit": "0"}, {"tag": "a]] x"}, {"keep": "1"}, {"list": "other"}, {"plan": "yes"}):
            resp = save(client, page="lists", index=0, was="", set=bad)
            assert resp.status_code == 400 and resp.get_json()["error"], bad
        assert save(client, page="nope", index=0, was="", set={"view": "open"}).status_code == 404
        assert save(client, page="../x", index=0, was="", set={"view": "open"}).status_code == 404
        assert path.read_text() == "[[tasks:]]\n"

    def test_all_tasks_keeps_its_settings_in_a_file(self, client, wiki_dir):
        resp = save(client, all=True, was="", set={"view": "open", "limit": "4", "labels": "Backlog,Now"})
        assert resp.status_code == 200
        stored = json.loads((wiki_dir / "_tasks" / "settings.json").read_text())
        assert stored == {"all": {"view": "open", "limit": "4", "labels": "Backlog,Now"}}
        html = client.get("/tasks", headers=HOST).get_data(as_text=True)
        assert 'data-settings-raw="view=open limit=4 labels=Backlog,Now"' in html
        assert 'data-init-view="open" data-init-plan="off" data-init-limit="4"' in html and ">Backlog</button>" in html
        # Saved from what it was; a filter isn't All tasks'.
        assert save(client, all=True, was="", set={"view": "focus"}).status_code == 409
        assert save(client, all=True, was="view=open limit=4 labels=Backlog,Now", set={"tag": "a"}).status_code == 400
        assert save(client, all=True, was="view=open limit=4 labels=Backlog,Now", set={"view": "", "limit": ""}).status_code == 200
        assert json.loads((wiki_dir / "_tasks" / "settings.json").read_text()) == {"all": {"labels": "Backlog,Now"}}
        # The tasks are as they were.
        assert client.get("/api/tasks", headers=HOST).get_json() == []


def test_a_save_is_one_commit(client, git_wiki_dir):
    import subprocess

    from tests import settings

    def log():
        return subprocess.run(["git", "log", "--format=%s", "--name-only"], cwd=git_wiki_dir, capture_output=True,
                              text=True, check=True, env=settings.GIT_ENV).stdout.split()

    write_page(git_wiki_dir, "lists", "[[tasks: tag=a]]\n")
    assert save(client, page="lists", index=0, was="tag=a", set={"plan": "off"}).status_code == 200
    assert " ".join(log()).startswith("Change the task list's settings on lists lists.md")
    assert save(client, all=True, was="", set={"plan": "off"}).status_code == 200
    assert " ".join(log()).startswith("Change the settings of All tasks _tasks/settings.json")
    # Nothing changed: nothing written, no commit.
    head = log()
    assert save(client, page="lists", index=0, was="tag=a plan=off", set={"plan": "off"}).status_code == 200
    assert log() == head
