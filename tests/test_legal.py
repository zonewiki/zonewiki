"""The imprint and privacy settings (imprint = …, privacy = … in the
settings file, docs/spec/users.md): links under the page that say who runs
the wiki, for whoever isn't signed in."""

import re

from tests import settings
from tests.conftest import HOST

IMPRINT = "https://example.org/imprint.html"
PRIVACY = "https://example.org/privacy.html"
BOTH = f"imprint = {IMPRINT}\nprivacy = {PRIVACY}\n"
FOOTER = re.compile(r'<footer class="wiki-legal">(.*?)</footer>', re.S)


def links(client, path):
    resp = client.get(path, headers=HOST)
    assert resp.status_code == 200, path
    m = FOOTER.search(resp.data.decode())
    return re.findall(r'<a href="([^"]*)" rel="noopener">([^<]*)</a>', m.group(1)) if m else None


def test_without_them_no_page_has_the_footer(client, wiki_dir, monkeypatch):
    monkeypatch.setattr(settings, "USERS", {"ada": ("ada-pw", "")})
    assert links(client, "/login") is None


def test_the_login_page_shows_them_and_a_signed_in_user_sees_none(client, wiki_dir, monkeypatch):
    monkeypatch.setattr(settings, "USERS", {"ada": ("ada-pw", "")})
    monkeypatch.setattr(settings, "CONFIG_FILE", BOTH)
    assert links(client, "/login") == [(IMPRINT, "Imprint"), (PRIVACY, "Privacy")]
    client.post("/login", data={"username": "ada", "password": "ada-pw"}, headers=HOST)
    for path in ("/page/index", "/tasks", "/notes", "/edit/index", "/help"):
        assert links(client, path) is None, path


def test_one_alone_is_one_link(client, wiki_dir, monkeypatch):
    monkeypatch.setattr(settings, "PASSWORD", "pw")
    monkeypatch.setattr(settings, "CONFIG_FILE", "imprint = /page/imprint\n")
    assert links(client, "/login") == [("/page/imprint", "Imprint")]


def test_a_public_wikis_visitor_sees_them(client, wiki_dir, monkeypatch):
    monkeypatch.setattr(settings, "USERS", {"admin": ("admin-pw", "")})
    monkeypatch.setattr(settings, "USER_SETTINGS", {"admin": {"role": "admin"}})
    monkeypatch.setattr(settings, "ENV", {"ZONE_PUBLIC": "view"})
    monkeypatch.setattr(settings, "CONFIG_FILE", BOTH)
    assert links(client, "/page/index") == [(IMPRINT, "Imprint"), (PRIVACY, "Privacy")]
    client.post("/login", data={"username": "admin", "password": "admin-pw"}, headers=HOST)
    assert links(client, "/page/index") is None


def test_a_wiki_without_a_login_shows_them_on_its_pages_but_not_in_the_editor(client, wiki_dir, monkeypatch):
    monkeypatch.setattr(settings, "CONFIG_FILE", BOTH)
    assert links(client, "/page/index") == [(IMPRINT, "Imprint"), (PRIVACY, "Privacy")]
    assert links(client, "/edit/index") is None

