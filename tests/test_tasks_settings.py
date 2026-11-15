"""The task app's settings dialog (docs/spec/tasks.md, Settings): where a
task app has its settings, and POST /api/tasks/settings, which rewrites a
page's [[tasks: …]], a list's own settings or those of All tasks."""

from tests.conftest import HOST, list_page, make_list, preview, rendered, write_page


def save(client, **body):
    return client.post("/api/tasks/settings", json=body, headers=HOST)


class TestListSettings:
    def test_a_page_view_has_the_settings_and_names_where_its_params_are(self, client, wiki_dir):
        make_list(client, "shop", headers=HOST)
        html = rendered(client, wiki_dir, "[[tasks: tag=a]]\n\n[[tasks: list=shop  view=open]]\n", slug="lists")
        assert 'data-settings-page="lists" data-settings-index="0" data-settings-raw="tag=a" data-settings-embed="1">' in html
        # An embed of a list: the page's own params, and the list whose settings are its own.
        assert ('data-settings-page="lists" data-settings-index="1" data-settings-raw="list=shop  view=open" '
                'data-settings-embed="1" data-settings-list="shop" data-settings-list-set="{}" data-settings-home="1">') in html   # home: it can be left out of My tasks
        assert 'id="task-settings-modal"' in html and 'id="task-settings-modal-1"' in html
        # The list's ⋯ menu opens it (List settings…, then Export, Import…); the toolbar has no gear.
        assert 'id="task-more-btn"' in html and 'id="task-more-btn-1"' in html and 'id="task-import-modal-1"' in html
        assert "task-settings-btn" not in html

    def test_a_preview_has_none(self, client, wiki_dir):
        html = preview(client, "[[tasks: tag=a]]\n")
        assert "task-settings" not in html and "data-settings" not in html
        assert "task-more-btn" not in html and "task-import" not in html

    def test_tasks_page_all_and_a_list(self, client, wiki_dir):
        list_page(client, wiki_dir, "shopping", "# Shopping\n\ntext\n\n[[tasks: tag=x]]\n\n[[tasks: list=shop keep=1 pin=1]]\n")
        html = client.get("/tasks?list=shop", headers=HOST).get_data(as_text=True)
        # A list: its own settings (no page's), and how the user pinned it.
        assert ('data-settings-page="" data-settings-index="-1" data-settings-raw="" data-settings-list="shop" '
                'data-settings-list-set="{&#34;keep&#34;:&#34;1&#34;}" data-settings-pin="1" data-settings-home="1">') in html
        html = client.get("/tasks", headers=HOST).get_data(as_text=True)
        assert 'data-settings-page="" data-settings-index="-1" data-settings-raw="">' in html
        # A name no list has: nothing to set.
        html = client.get("/tasks?list=other", headers=HOST).get_data(as_text=True)
        assert "task-settings" not in html

    def test_the_headless_app_has_none(self, client, wiki_dir):
        assert "task-settings" not in client.get("/api/tasks/headless", headers=HOST).get_data(as_text=True)


class TestPlanParam:
    def test_plan_reaches_the_app(self, client, wiki_dir):
        assert 'data-init-view="" data-init-group="none"' in preview(client, "[[tasks: plan=off]]\n")
        assert 'data-init-view="" data-init-group=""' in preview(client, "[[tasks: plan=maybe]]\n")
        # view=open has always been the one list, view=plan the board; plan= wins.
        assert 'data-init-view="open" data-init-group="none"' in preview(client, "[[tasks: view=open]]\n")
        assert 'data-init-view="plan" data-init-group="day"' in preview(client, "[[tasks: view=open plan=on]]\n")
        assert 'data-init-view="open" data-init-group="none"' in preview(client, "[[tasks: view=plan plan=off]]\n")
        assert 'data-init-view="focus" data-init-group="none"' in preview(client, "[[tasks: view=focus plan=off]]\n")

    def test_group_says_what_the_open_tab_shows(self, client, wiki_dir):
        """group= (none, day, person, list) took over from plan=, which is
        still read; with both, group= counts."""
        assert 'data-init-view="" data-init-group="person"' in preview(client, "[[tasks: group=person]]\n")
        assert 'data-init-view="" data-init-group=""' in preview(client, "[[tasks: group=week]]\n")
        assert 'data-init-view="plan" data-init-group="list"' in preview(client, "[[tasks: view=open group=list]]\n")
        assert 'data-init-view="open" data-init-group="none"' in preview(client, "[[tasks: view=plan group=none]]\n")
        assert 'data-init-view="plan" data-init-group="person"' in preview(client, "[[tasks: view=plan group=person plan=off]]\n")
        assert 'data-init-view="focus" data-init-group="day"' in preview(client, "[[tasks: view=focus group=day]]\n")
        assert 'data-init-view="plan" data-init-group=""' in preview(client, "[[tasks: view=plan]]\n")


class TestSave:
    def test_rewrites_the_embed_and_nothing_else(self, client, wiki_dir):
        text = "# Lists\n\n```\n[[tasks: tag=code]]\n```\n\n[[tasks: tag=a]]\n\nBetween.\n\n[[tasks: tag=shop keep=1]]\n"
        path = write_page(wiki_dir, "lists", text)
        resp = save(client, page="lists", index=1, was="tag=shop keep=1",
                    set={"view": "open", "limit": "5", "labels": "To_buy,Cart", "new": "weekly"})
        assert resp.status_code == 200 and resp.get_json() == {"ok": True}
        assert path.read_text() == text.replace(
            "[[tasks: tag=shop keep=1]]", "[[tasks: tag=shop keep=1 new=weekly view=open limit=5 labels=To_buy,Cart]]")
        html = client.get("/page/lists", headers=HOST).get_data(as_text=True)
        assert ">To buy</button>" in html and ">Cart</button>" in html and 'data-init-limit="5"' in html

    def test_an_empty_value_takes_the_param_out(self, client, wiki_dir):
        path = write_page(wiki_dir, "lists", "[[tasks: tag=a view=open limit=3]]\n")
        assert save(client, page="lists", index=0, was="tag=a view=open limit=3", set={"view": "", "limit": ""}).status_code == 200
        assert path.read_text() == "[[tasks: tag=a]]\n"

    def test_a_changed_page_is_refused(self, client, wiki_dir):
        path = write_page(wiki_dir, "lists", "[[tasks: tag=a]]\n")
        resp = save(client, page="lists", index=0, was="tag=b", set={"view": "open"})
        assert resp.status_code == 409 and "Reload" in resp.get_json()["error"]
        assert save(client, page="lists", index=3, was="", set={"view": "open"}).status_code == 409
        assert path.read_text() == "[[tasks: tag=a]]\n"

    def test_bad_values_and_pages(self, client, wiki_dir):
        path = write_page(wiki_dir, "lists", "[[tasks:]]\n")
        # (pin is no param: a pin is the user's, of a list.)
        for bad in ({"view": "week"}, {"limit": "0"}, {"tag": "a]] x"}, {"keep": "1"}, {"list": "other"}, {"plan": "yes"}, {"group": "week"}, {"pin": "1"}):
            resp = save(client, page="lists", index=0, was="", set=bad)
            assert resp.status_code == 400 and resp.get_json()["error"], bad
        assert save(client, page="nope", index=0, was="", set={"view": "open"}).status_code == 404
        assert save(client, page="../x", index=0, was="", set={"view": "open"}).status_code == 404
        assert path.read_text() == "[[tasks:]]\n"

    def test_a_list_keeps_its_settings_in_the_registry(self, client, wiki_dir):
        make_list(client, "shop", headers=HOST)
        text = "# Lists\n\n[[tasks: list=shop view=open]]\n"
        path = write_page(wiki_dir, "lists", text)
        resp = save(client, list="Shop", set={"limit": "5", "labels": "To_buy,Cart", "keep": "yes", "new": "#weekly", "plan": "off"})
        assert resp.status_code == 200 and resp.get_json() == {"ok": True}
        registry = wiki_dir / "_tasks" / "lists.json"
        assert '    {"name": "shop", "limit": "5", "plan": "off", "keep": "1", "labels": "To_buy,Cart", "new": "weekly"}\n' in registry.read_text()
        assert path.read_text() == text             # no page is written
        # Wherever the list shows: its page, with the page's own tab, and /tasks?list=.
        for url in ("/page/lists", "/tasks?list=shop"):
            html = client.get(url, headers=HOST).get_data(as_text=True)
            assert ">To buy</button>" in html and ">Cart</button>" in html, url
            assert 'data-init-limit="5" data-init-keep="1"' in html and 'data-new-tags="weekly"' in html, url
        assert 'data-init-view="open" data-init-group="none"' in client.get("/page/lists", headers=HOST).get_data(as_text=True)
        # "" takes a setting out; hiding in sharing mode is one too.
        assert save(client, list="shop", set={"limit": "", "noshare": "1"}).status_code == 200
        assert '{"name": "shop", "plan": "off", "keep": "1", "labels": "To_buy,Cart", "new": "weekly", "noshare": "1"}' in registry.read_text()
        # What is no setting of a list, and a list that isn't there.
        before = registry.read_text()
        for bad in ({"tag": "x"}, {"assignee": "me"}, {"pin": "1"}, {"limit": "0"}, {"list": "other"}, {"keep": "maybe"}):
            resp = save(client, list="shop", set=bad)
            assert resp.status_code == 400 and resp.get_json()["error"], bad
        assert save(client, list="nosuch", set={"limit": "3"}).status_code == 404
        assert registry.read_text() == before

    def test_all_tasks_keeps_its_settings_in_the_registry(self, client, wiki_dir):
        resp = save(client, all=True, was="", set={"view": "open", "limit": "4", "labels": "Backlog,Now"})
        assert resp.status_code == 200
        registry = wiki_dir / "_tasks" / "lists.json"
        assert '  "all": {"view": "open", "limit": "4", "labels": "Backlog,Now"}\n' in registry.read_text()
        html = client.get("/tasks", headers=HOST).get_data(as_text=True)
        assert 'data-settings-raw="view=open limit=4 labels=Backlog,Now"' in html
        assert 'data-init-view="open" data-init-group="none" data-init-limit="4"' in html and ">Backlog</button>" in html
        # Saved from what it was; a filter isn't All tasks'.
        assert save(client, all=True, was="", set={"view": "focus"}).status_code == 409
        assert save(client, all=True, was="view=open limit=4 labels=Backlog,Now", set={"tag": "a"}).status_code == 400
        assert save(client, all=True, was="view=open limit=4 labels=Backlog,Now", set={"view": "", "limit": ""}).status_code == 200
        assert '  "all": {"labels": "Backlog,Now"}\n' in registry.read_text()
        assert not (wiki_dir / "_tasks" / "settings.json").exists()
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
    assert " ".join(log()).startswith("Change the settings of All tasks _tasks/lists.json")
    make_list(client, "shop", headers=HOST)
    assert save(client, list="shop", set={"limit": "3"}).status_code == 200
    assert " ".join(log()).startswith("Change the settings of list #shop _tasks/lists.json Create list #shop _tasks/lists.json")
    # Nothing changed: nothing written, no commit.
    head = log()
    assert save(client, page="lists", index=0, was="tag=a plan=off", set={"plan": "off"}).status_code == 200
    assert log() == head
