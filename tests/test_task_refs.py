"""Task references: [[task:<id>]] in a page renders the task as a live
chip, and search, Linked tasks and MCP know them.
"""

import json
import re
from datetime import date, timedelta

from tests import settings
from tests.conftest import HOST, write_page


def task(client, **fields):
    resp = client.post("/api/tasks", json=fields, headers=HOST)
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


def put(client, task_id, **fields):
    resp = client.put(f"/api/tasks/{task_id}", json=fields, headers=HOST)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def view(client, slug="notes"):
    resp = client.get(f"/page/{slug}", headers=HOST)
    assert resp.status_code == 200
    return resp.data.decode()


def day(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


class TestRendering:
    def test_short_id_renders_a_chip_linking_to_the_task(self, client, wiki_dir):
        t = task(client, title="Call the plumber")
        write_page(wiki_dir, "notes", f"# Notes\n\nRemember [[task:{t['id'][:8]}]] today.\n")
        body = view(client)
        assert f'<span class="task-ref" data-task-id="{t["id"]}">' in body
        assert f'href="/tasks?task={t["id"]}&amp;view=open"' in body
        assert ">Call the plumber</a>" in body
        assert 'aria-label="Done: Call the plumber">' in body   # not checked
        assert "[[task:" not in body

    def test_full_id_and_uppercase_work(self, client, wiki_dir):
        t = task(client, title="Full")
        write_page(wiki_dir, "notes", f"# N\n\n[[task:{t['id']}]] and [[task:{t['id'][:8].upper()}]]\n")
        assert view(client).count(f'data-task-id="{t["id"]}"') == 2

    def test_unknown_id_is_marked_missing(self, client, wiki_dir):
        write_page(wiki_dir, "notes", "# N\n\nGone: [[task:abcd1234]]\n")
        body = view(client)
        assert 'class="task-ref task-ref-missing"' in body and "task abcd1234?" in body

    def test_done_and_wont_do(self, client, wiki_dir):
        a, b = task(client, title="Finished"), task(client, title="Dropped")
        put(client, a["id"], completed=True)
        put(client, b["id"], wont_do=True)
        write_page(wiki_dir, "notes", f"# N\n\n[[task:{a['id'][:8]}]] [[task:{b['id'][:8]}]]\n")
        body = view(client)
        assert f'<span class="task-ref task-ref-done" data-task-id="{a["id"]}">' in body
        assert 'aria-label="Done: Finished" checked>' in body
        assert f'href="/tasks?task={a["id"]}&amp;view=completed"' in body
        assert f'<span class="task-ref task-ref-wontdo" data-task-id="{b["id"]}">' in body
        assert 'aria-label="Done: Dropped" disabled>' in body
        assert "won&#x27;t do" in body or "won't do" in body

    def test_completed_recurring_task_shows_the_current_occurrence(self, client, wiki_dir):
        t = task(client, title="Water plants", due_date=day(0), rec_type="weekly", rec_interval=1)
        put(client, t["id"], completed=True)
        write_page(wiki_dir, "notes", f"# N\n\n- [[task:{t['id'][:8]}]]\n")
        body = view(client)
        m = re.search(r'class="task-ref" data-task-id="([^"]+)"', body)
        assert m and m.group(1) != t["id"], "should be the spawned next occurrence, open"
        assert f'⏰ <time class="local-date" datetime="{day(7)}">' in body

    def test_dates_from_the_file_are_escaped(self, client, wiki_dir):
        # tasks.json is read leniently: a hand edit or a pull can put
        # anything in a date, which the chip's title must not turn into markup.
        t = task(client, title="Hand edited", due_date=day(1), start_date=day(2))
        path = wiki_dir / "_tasks" / "tasks.json"
        bad = '"><form action="/delete/index"><button>x'
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else data["tasks"]
        for item in items:
            if item["id"] == t["id"]:
                item["due_date"] = day(1) + bad
                item["start_date"] = day(2) + bad
        path.write_text(json.dumps(data), encoding="utf-8")
        write_page(wiki_dir, "notes", f"# N\n\n[[task:{t['id'][:8]}]]\n")
        body = view(client)
        assert "Hand edited" in body
        assert '<form action="/delete/index">' not in body and "<button>x" not in body
        assert 'title="Due ' + day(1) + '&#34;&gt;&lt;form' in body

    def test_badges(self, client, wiki_dir):
        late = task(client, title="Late", due_date=day(-2))
        soon = task(client, title="Soon", start_date=day(3), due_date=day(0))
        keep = task(client, title="Milk", keep=True, tags=["shop"])
        put(client, keep["id"], completed=True)          # keep: stays open, ✓ today
        write_page(wiki_dir, "shopping", "# Shopping\n\n[[tasks: list=groceries]]\n")
        listed = task(client, title="Eggs", tags=["groceries"])
        write_page(wiki_dir, "notes", "# N\n\n" + " ".join(
            f"[[task:{t['id'][:8]}]]" for t in (late, soon, keep, listed)) + "\n")
        body = view(client)
        assert 'class="task-ref-badge task-ref-due overdue"' in body
        assert 'class="task-ref-badge task-ref-due due-today"' in body
        assert 'class="task-ref-badge task-ref-start start-later"' in body
        assert f'⟲ ✓ <time class="local-date" datetime="{day(0)}">' in body
        assert f'href="/tasks?task={listed["id"]}&amp;view=open&amp;list=groceries"' in body
        assert '<span class="task-ref-badge task-ref-list">groceries</span>' in body

    def test_alone_in_a_list_item_is_a_row(self, client, wiki_dir):
        t = task(client, title="Row")
        ref = f"[[task:{t['id'][:8]}]]"
        write_page(wiki_dir, "notes", f"# N\n\n- {ref}\n- text and {ref}\n")
        body = view(client)
        assert body.count('<li class="task-ref-item">') == 1
        assert "<li>text and <span" in body

    def test_title_is_plain_and_escaped(self, client, wiki_dir):
        t = task(client, title="Read [[Design Doc]] <b>now</b>")
        write_page(wiki_dir, "notes", f"# N\n\n[[task:{t['id'][:8]}]]\n")
        body = view(client)
        assert ">Read Design Doc &lt;b&gt;now&lt;/b&gt;</a>" in body

    def test_code_stays_literal(self, client, wiki_dir):
        t = task(client, title="Coded")
        ref = f"[[task:{t['id'][:8]}]]"
        write_page(wiki_dir, "notes", f"# N\n\n`{ref}`\n\n```\n{ref}\n```\n")
        body = view(client)
        assert "task-ref" not in body.split('<article', 1)[1].split('</article>')[0]
        assert body.count(ref) == 2

    def test_preview_renders_chips(self, client, wiki_dir):
        t = task(client, title="Previewed")
        resp = client.post("/api/preview", data=f"[[task:{t['id']}]]", headers=HOST)
        assert f'data-task-id="{t["id"]}"' in resp.data.decode()


class TestLinkedTasks:
    def test_inline_task_is_not_repeated_under_linked_tasks(self, client, wiki_dir):
        shown = task(client, title="Shown inline", description="see [[notes]]")
        other = task(client, title="Only linked", description="see [[notes]]")
        write_page(wiki_dir, "notes", f"# Notes\n\n[[task:{shown['id'][:8]}]]\n")
        linked = view(client).split('id="task-backlinks"', 1)[1]
        assert "Only linked" in linked and "Shown inline" not in linked
        assert other["id"] in linked


class TestTwoLists:
    """A task in two lists links, from a page declaring one of them, to that
    list rather than to its first list A–Z."""

    def _setup(self, client, wiki_dir):
        write_page(wiki_dir, "ideas", "# Ideas\n\n[[tasks: list=presentideas]]\n")
        t = task(client, title="Lego for Sam", tags=["presentideas", "task"],
                 description="see [[todo]]")
        write_page(wiki_dir, "todo", f"# Todo\n\n[[tasks: list=task]]\n\n[[task:{t['id'][:8]}]]\n")
        return t

    def test_chip_links_to_the_list_the_page_declares(self, client, wiki_dir):
        t = self._setup(client, wiki_dir)
        body = view(client, "todo")
        assert f'href="/tasks?task={t["id"]}&amp;view=open&amp;list=task"' in body
        assert '<span class="task-ref-badge task-ref-list">task</span>' in body

    def test_elsewhere_the_first_list_wins(self, client, wiki_dir):
        t = self._setup(client, wiki_dir)
        write_page(wiki_dir, "notes", f"# N\n\n[[task:{t['id'][:8]}]]\n")
        assert f'href="/tasks?task={t["id"]}&amp;view=open&amp;list=presentideas"' in view(client)

    def test_page_declaring_a_list_the_task_is_not_in(self, client, wiki_dir):
        t = self._setup(client, wiki_dir)
        write_page(wiki_dir, "notes", f"# N\n\n[[tasks: list=groceries]]\n\n[[task:{t['id'][:8]}]]\n")
        assert f'href="/tasks?task={t["id"]}&amp;view=open&amp;list=presentideas"' in view(client)

    def test_linked_tasks_link_to_the_pages_list(self, client, wiki_dir):
        t = self._setup(client, wiki_dir)
        write_page(wiki_dir, "todo", "# Todo\n\n[[tasks: list=task]]\n")   # no chip: Linked tasks
        linked = view(client, "todo").split('id="task-backlinks"', 1)[1]
        assert f'href="/tasks?task={t["id"]}&amp;view=open&amp;list=task"' in linked

    def test_preview_with_lists_param(self, client, wiki_dir):
        t = self._setup(client, wiki_dir)
        ref = f"[[task:{t['id']}]]"
        plain = client.post("/api/preview", data=ref, headers=HOST).data.decode()
        assert "list=presentideas" in plain
        kept = client.post("/api/preview?lists=Task", data=ref, headers=HOST).data.decode()
        assert "list=task" in kept and "list=presentideas" not in kept


class TestSearch:
    def test_page_is_found_by_the_task_title_and_follows_renames(self, client, wiki_dir):
        t = task(client, title="Renew passport")
        write_page(wiki_dir, "notes", f"# Notes\n\nTodo: [[task:{t['id'][:8]}]]\n")

        def found(q):
            res = client.get(f"/api/search?q={q}", headers=HOST).get_json()
            return [r["slug"] for r in res if r.get("type") != "task"]
        assert "notes" in found("passport")
        put(client, t["id"], title="Renew driving licence")
        assert "notes" in found("licence")
        assert "notes" not in found("passport")


class TestMcp:
    def test_read_page_lists_the_referenced_tasks(self, client, wiki_dir, monkeypatch):
        monkeypatch.setattr(settings, "LEAF_MCP_TOKEN", "t")
        t = task(client, title="Book flights", due_date="2030-01-02")
        write_page(wiki_dir, "notes", f"# N\n\n[[task:{t['id'][:8]}]] and [[task:deadbeef]]\n")
        resp = client.post("/mcp", headers={**HOST, "Authorization": "Bearer t"}, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "read_page", "arguments": {"slug": "notes"}}})
        text = resp.get_json()["result"]["content"][0]["text"]
        assert f"[[task:{t['id'][:8]}]] and [[task:deadbeef]]" in text      # raw page kept
        assert f"- [[task:{t['id'][:8]}]]: Book flights (open, due 2030-01-02) " in text
        assert f"/tasks?task={t['id']}&view=open" in text
        assert "- [[task:deadbeef]]: no such task (deleted)" in text


class TestMakeTask:
    def test_editor_has_the_button_and_the_quick_add_parser(self, client, wiki_dir):
        write_page(wiki_dir, "notes", "# N\n")
        body = client.get("/edit/notes", headers=HOST).data.decode()
        assert 'data-fmt="maketask"' in body and 'data-fmt="taskref"' in body
        # A hidden task app: its edit modal for one item, and tasks-core.js
        # (imported by the editor) parses `tomorrow #work due:fri` for
        # several lines.
        assert 'data-headless="1" style="display:none"' in body
        assert 'id="task-edit-modal"' in body
