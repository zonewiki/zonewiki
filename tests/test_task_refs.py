"""Task references: [[task:<id>]] in a page renders the deck task as a live
chip (server._task_ref_html), and search, Linked tasks and MCP know them.

Run with:
    pytest tests/test_task_refs.py -v
"""

import re
from datetime import date, timedelta

from tests.test_routes import HOST, client, wiki_dir, write_page  # noqa: F401  (fixtures; sets sys.path)
import server


def task(client, **fields):  # noqa: F811
    resp = client.post("/api/deck", json=fields, headers=HOST)
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


def put(client, task_id, **fields):  # noqa: F811
    resp = client.put(f"/api/deck/{task_id}", json=fields, headers=HOST)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def view(client, slug="notes"):  # noqa: F811
    resp = client.get(f"/page/{slug}", headers=HOST)
    assert resp.status_code == 200
    return resp.data.decode()


def day(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


class TestRendering:
    def test_short_id_renders_a_chip_linking_to_the_task(self, client, wiki_dir):  # noqa: F811
        t = task(client, title="Call the plumber")
        write_page(wiki_dir, "notes", f"# Notes\n\nRemember [[task:{t['id'][:8]}]] today.\n")
        body = view(client)
        assert f'<span class="task-ref" data-task-id="{t["id"]}">' in body
        assert f'href="/deck?task={t["id"]}&amp;view=open"' in body
        assert ">Call the plumber</a>" in body
        assert 'aria-label="Done: Call the plumber">' in body   # not checked
        assert "[[task:" not in body

    def test_full_id_and_uppercase_work(self, client, wiki_dir):  # noqa: F811
        t = task(client, title="Full")
        write_page(wiki_dir, "notes", f"# N\n\n[[task:{t['id']}]] and [[task:{t['id'][:8].upper()}]]\n")
        assert view(client).count(f'data-task-id="{t["id"]}"') == 2

    def test_unknown_id_is_marked_missing(self, client, wiki_dir):  # noqa: F811
        write_page(wiki_dir, "notes", "# N\n\nGone: [[task:abcd1234]]\n")
        body = view(client)
        assert 'class="task-ref task-ref-missing"' in body and "task abcd1234?" in body

    def test_done_and_wont_do(self, client, wiki_dir):  # noqa: F811
        a, b = task(client, title="Finished"), task(client, title="Dropped")
        put(client, a["id"], completed=True)
        put(client, b["id"], wont_do=True)
        write_page(wiki_dir, "notes", f"# N\n\n[[task:{a['id'][:8]}]] [[task:{b['id'][:8]}]]\n")
        body = view(client)
        assert f'<span class="task-ref task-ref-done" data-task-id="{a["id"]}">' in body
        assert 'aria-label="Done: Finished" checked>' in body
        assert f'href="/deck?task={a["id"]}&amp;view=completed"' in body
        assert f'<span class="task-ref task-ref-wontdo" data-task-id="{b["id"]}">' in body
        assert 'aria-label="Done: Dropped" disabled>' in body
        assert "won&#x27;t do" in body or "won't do" in body

    def test_completed_recurring_task_shows_the_current_occurrence(self, client, wiki_dir):  # noqa: F811
        t = task(client, title="Water plants", due_date=day(0), rec_type="weekly", rec_interval=1)
        put(client, t["id"], completed=True)
        write_page(wiki_dir, "notes", f"# N\n\n- [[task:{t['id'][:8]}]]\n")
        body = view(client)
        m = re.search(r'class="task-ref" data-task-id="([^"]+)"', body)
        assert m and m.group(1) != t["id"], "should be the spawned next occurrence, open"
        assert f"⏰ {server._short_date(day(7), day(0))}" in body

    def test_badges(self, client, wiki_dir):  # noqa: F811
        late = task(client, title="Late", due_date=day(-2))
        soon = task(client, title="Soon", start_date=day(3), due_date=day(0))
        keep = task(client, title="Milk", keep=True, tags=["shop"])
        put(client, keep["id"], completed=True)          # keep: stays open, ✓ today
        listed = task(client, title="Eggs", list="groceries")
        write_page(wiki_dir, "notes", "# N\n\n" + " ".join(
            f"[[task:{t['id'][:8]}]]" for t in (late, soon, keep, listed)) + "\n")
        body = view(client)
        assert 'class="task-ref-badge task-ref-due overdue"' in body
        assert 'class="task-ref-badge task-ref-due due-today"' in body
        assert 'class="task-ref-badge task-ref-start start-later"' in body
        assert f"⟲ ✓ {server._short_date(day(0), day(0))}" in body
        assert f'href="/deck?task={listed["id"]}&amp;view=open&amp;list=groceries"' in body
        assert '<span class="task-ref-badge task-ref-list">groceries</span>' in body

    def test_alone_in_a_list_item_is_a_row(self, client, wiki_dir):  # noqa: F811
        t = task(client, title="Row")
        ref = f"[[task:{t['id'][:8]}]]"
        write_page(wiki_dir, "notes", f"# N\n\n- {ref}\n- text and {ref}\n")
        body = view(client)
        assert body.count('<li class="task-ref-item">') == 1
        assert "<li>text and <span" in body

    def test_title_is_plain_and_escaped(self, client, wiki_dir):  # noqa: F811
        t = task(client, title="Read [[Design Doc]] <b>now</b>")
        write_page(wiki_dir, "notes", f"# N\n\n[[task:{t['id'][:8]}]]\n")
        body = view(client)
        assert ">Read Design Doc &lt;b&gt;now&lt;/b&gt;</a>" in body

    def test_code_stays_literal(self, client, wiki_dir):  # noqa: F811
        t = task(client, title="Coded")
        ref = f"[[task:{t['id'][:8]}]]"
        write_page(wiki_dir, "notes", f"# N\n\n`{ref}`\n\n```\n{ref}\n```\n")
        body = view(client)
        assert "task-ref" not in body.split('<article', 1)[1].split('</article>')[0]
        assert body.count(ref) == 2

    def test_preview_renders_chips(self, client, wiki_dir):  # noqa: F811
        t = task(client, title="Previewed")
        resp = client.post("/api/preview", data=f"[[task:{t['id']}]]", headers=HOST)
        assert f'data-task-id="{t["id"]}"' in resp.data.decode()


class TestLinkedTasks:
    def test_inline_task_is_not_repeated_under_linked_tasks(self, client, wiki_dir):  # noqa: F811
        shown = task(client, title="Shown inline", description="see [[notes]]")
        other = task(client, title="Only linked", description="see [[notes]]")
        write_page(wiki_dir, "notes", f"# Notes\n\n[[task:{shown['id'][:8]}]]\n")
        linked = view(client).split('id="task-backlinks"', 1)[1]
        assert "Only linked" in linked and "Shown inline" not in linked
        assert other["id"] in linked


class TestSearch:
    def test_page_is_found_by_the_task_title_and_follows_renames(self, client, wiki_dir):  # noqa: F811
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
    def test_read_page_lists_the_referenced_tasks(self, client, wiki_dir):  # noqa: F811
        t = task(client, title="Book flights", due_date="2030-01-02")
        write_page(wiki_dir, "notes", f"# N\n\n[[task:{t['id'][:8]}]] and [[task:deadbeef]]\n")
        resp = client.post("/mcp", headers=HOST, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "read_page", "arguments": {"slug": "notes"}}})
        text = resp.get_json()["result"]["content"][0]["text"]
        assert f"[[task:{t['id'][:8]}]] and [[task:deadbeef]]" in text      # raw page kept
        assert f"- [[task:{t['id'][:8]}]]: Book flights (open, due 2030-01-02) " in text
        assert f"/deck?task={t['id']}&view=open" in text
        assert "- [[task:deadbeef]]: no such task (deleted)" in text
