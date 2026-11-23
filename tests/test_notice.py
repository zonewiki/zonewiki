"""The notice setting (notice = … in the settings file, docs/spec/users.md):
one line across the top of every page, for whoever looks."""

import re

from tests import settings
from tests.conftest import HOST

NOTICE = "This demo is put back at every full hour"
BANNER = re.compile(r'<div class="wiki-notice" role="note" title="([^"]*)">([^<]*)</div>')


def banner(client, path):
    resp = client.get(path, headers=HOST)
    m = BANNER.search(resp.data.decode())
    return resp, m.group(2) if m else None


def test_without_one_no_page_has_it(client, wiki_dir):
    resp, text = banner(client, "/page/index")
    assert resp.status_code == 200 and text is None
    assert "has-notice" not in resp.data.decode()


def test_every_view_shows_it(client, wiki_dir, monkeypatch):
    monkeypatch.setattr(settings, "CONFIG_FILE", f"notice = {NOTICE}\n")
    (wiki_dir / "garden.md").write_text("# Garden\n")
    for path in ("/page/index", "/page/garden", "/edit/garden", "/tasks", "/notes", "/changes", "/search?q=garden",
                 "/help", "/assistant"):
        resp, text = banner(client, path)
        assert text == NOTICE, path
        body = resp.data.decode()
        assert re.search(r'<body class="[^"]*has-notice', body), path
    # The editor's page keeps its own class beside it.
    assert '<body class="editor-page has-notice"' in client.get("/edit/garden", headers=HOST).data.decode()


def test_the_login_page_shows_it_before_anyone_signs_in(client, wiki_dir, monkeypatch):
    monkeypatch.setattr(settings, "USERS", {"demo": ("demo-pw", "")})
    monkeypatch.setattr(settings, "CONFIG_FILE", f"notice = {NOTICE}\n")
    resp, text = banner(client, "/login")
    assert resp.status_code == 200 and text == NOTICE
    # And a page, once signed in.
    client.post("/login", data={"username": "demo", "password": "demo-pw"}, headers=HOST)
    assert banner(client, "/page/index")[1] == NOTICE


def test_a_public_wikis_visitor_sees_it(client, wiki_dir, monkeypatch):
    monkeypatch.setattr(settings, "USERS", {"admin": ("admin-pw", "")})
    monkeypatch.setattr(settings, "USER_SETTINGS", {"admin": {"role": "admin"}})
    monkeypatch.setattr(settings, "ENV", {"ZONE_PUBLIC": "view"})
    monkeypatch.setattr(settings, "CONFIG_FILE", f"notice = {NOTICE}\n")
    resp, text = banner(client, "/page/index")
    assert resp.status_code == 200 and text == NOTICE


def test_it_is_text_not_html(client, wiki_dir, monkeypatch):
    monkeypatch.setattr(settings, "CONFIG_FILE", 'notice = Back at <b>five</b> & "soon" <script>alert(1)</script>\n')
    body = client.get("/page/index", headers=HOST).data.decode()
    assert "<b>five</b>" not in body and "<script>alert(1)" not in body
    assert ('<div class="wiki-notice" role="note" title="Back at &lt;b&gt;five&lt;/b&gt; &amp; &#34;soon&#34; '
            '&lt;script&gt;alert(1)&lt;/script&gt;">Back at &lt;b&gt;five&lt;/b&gt; &amp; &#34;soon&#34; '
            '&lt;script&gt;alert(1)&lt;/script&gt;</div>') in body


def test_a_change_to_the_file_shows_from_the_next_request(client, wiki_dir, monkeypatch):
    monkeypatch.setattr(settings, "CONFIG_FILE", f"notice = {NOTICE}\n")
    assert banner(client, "/page/index")[1] == NOTICE
    path = wiki_dir / ".zone" / "config"
    path.write_text(path.read_text().replace(NOTICE, "Down for maintenance at 18:00"))
    for _ in range(40):   # Zonewiki looks at the file at most once a second
        if banner(client, "/page/index")[1] == "Down for maintenance at 18:00":
            break
        import time
        time.sleep(0.1)
    assert banner(client, "/page/index")[1] == "Down for maintenance at 18:00"
    path.write_text(path.read_text().replace("notice = Down for maintenance at 18:00\n", ""))
    for _ in range(40):
        if banner(client, "/page/index")[1] is None:
            break
        import time
        time.sleep(0.1)
    assert banner(client, "/page/index")[1] is None
