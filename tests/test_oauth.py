"""OAuth for /mcp (docs/spec/mcp.md, Connecting with OAuth): the tests play
an MCP client over HTTP: discovery, registration, the user's Allow in the
browser, the code for tokens, /mcp with them, refresh, the end of a
connection, and everything Zonewiki refuses on the way."""

import base64
import hashlib
import json
import re
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from tests import settings
from tests.conftest import HOST

USERS = {"alice": ("alice-pw", "alice-token"), "bob": ("bob-pw", ""), "carol": ("carol-pw", "")}
BASE = "http://localhost:5000"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
VERIFIER = "v" * 43 + "-zone-test-verifier"


@pytest.fixture()
def users(monkeypatch):
    monkeypatch.setattr(settings, "USERS", dict(USERS))
    monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}, "carol": {"role": "viewer"}})


def login(client, name):
    client.cookies.clear()
    resp = client.post("/login", data={"username": name, "password": USERS[name][0]}, headers=HOST)
    assert resp.status_code == 302
    return client


@pytest.fixture()
def bob(client, users):
    return login(client, "bob")


def challenge(verifier=VERIFIER):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def register(client, redirects=(CALLBACK,), name="Claude"):
    resp = client.post("/oauth/register", headers=HOST, json={"client_name": name, "redirect_uris": list(redirects)})
    assert resp.status_code == 201, resp.data
    return resp.get_json()["client_id"]


def asking(client_id, redirect=CALLBACK, **more):
    """What a client asks /oauth/authorize."""
    return {"response_type": "code", "client_id": client_id, "redirect_uri": redirect, "state": "s-123",
            "code_challenge": challenge(), "code_challenge_method": "S256", "resource": BASE + "/mcp", **more}


def answer_of(resp):
    """Where the page sends the browser (its Refresh header), and that address's query."""
    wait, _, to = resp.headers["Refresh"].partition("; url=")
    assert wait == "0" and f'href="{to.replace("&", "&amp;")}"' in resp.data.decode()
    return to, {k: v[0] for k, v in parse_qs(urlsplit(to).query).items()}


def allow(client, params, password):
    """The user's Allow: the response, and the code the client gets (None: none)."""
    resp = client.post("/oauth/authorize", headers=HOST, data={**params, "answer": "allow", "password": password})
    if "Refresh" not in resp.headers:
        return resp, None
    return resp, answer_of(resp)[1].get("code")


def token(client, **form):
    return client.post("/oauth/token", headers=HOST, data=form)


def exchange(client, the_client, the_code, redirect=CALLBACK, **more):
    form = {"grant_type": "authorization_code", "client_id": the_client, "code": the_code, "redirect_uri": redirect,
            "code_verifier": VERIFIER, "resource": BASE + "/mcp", **more}
    return token(client, **form)


def connect(client, password, redirect=CALLBACK, name="Claude"):
    """The whole way: the client id and the tokens of a new connection of whoever is signed in."""
    client_id = register(client, [redirect], name)
    resp, code = allow(client, asking(client_id, redirect), password)
    assert resp.status_code == 200 and code, resp.data
    resp = exchange(client, client_id, code, redirect)
    assert resp.status_code == 200, resp.data
    return client_id, resp.get_json()


def mcp(client, access, tool=None, **args):
    """A call of /mcp with an access token: the status, and a tool's text."""
    message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    if tool:
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}
    resp = client.post("/mcp", headers={**HOST, "Authorization": f"Bearer {access}"}, json=message)
    text = ""
    if tool and resp.status_code == 200:
        text = "".join(c["text"] for c in resp.get_json()["result"]["content"])
    return resp.status_code, text


def grants(wiki):
    path = wiki / ".zone" / "grants"
    return json.loads(path.read_text()) if path.exists() else []


def edit_grants(wiki, **fields):
    """Every connection's fields set, as time passing would."""
    all = grants(wiki)
    for g in all:
        g.update(fields)
    (wiki / ".zone" / "grants").write_text(json.dumps(all))


class TestDiscovery:

    def test_mcp_without_a_token_says_where_to_sign_in(self, client, users):
        resp = client.post("/mcp", headers=HOST, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert resp.status_code == 401
        m = re.fullmatch(r'Bearer resource_metadata="([^"]+)", scope="mcp"', resp.headers["WWW-Authenticate"])
        assert m and m.group(1) == BASE + "/.well-known/oauth-protected-resource/mcp"
        for path in ("/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-protected-resource"):
            meta = client.get(path, headers=HOST).get_json()
            assert meta["resource"] == BASE + "/mcp" and meta["authorization_servers"] == [BASE]
        server = client.get("/.well-known/oauth-authorization-server", headers=HOST).get_json()
        assert server["issuer"] == BASE
        assert server["authorization_endpoint"] == BASE + "/oauth/authorize"
        assert server["token_endpoint"] == BASE + "/oauth/token"
        assert server["registration_endpoint"] == BASE + "/oauth/register"
        assert server["code_challenge_methods_supported"] == ["S256"]
        assert server["token_endpoint_auth_methods_supported"] == ["none"]
        assert server["grant_types_supported"] == ["authorization_code", "refresh_token"]
        assert "client_id_metadata_document_supported" not in server   # not yet: clients register

    def test_a_wrong_token_is_told_too_and_no_token_is_no_guess(self, client, users):
        for _ in range(8):   # a client finding its way isn't throttled
            assert client.get("/mcp", headers=HOST).status_code == 401
        resp = client.post("/mcp", headers={**HOST, "Authorization": "Bearer wrong"}, json={})
        assert resp.status_code == 401 and 'error="invalid_token"' in resp.headers["WWW-Authenticate"]
        assert "resource_metadata=" in resp.headers["WWW-Authenticate"]
        assert mcp(client, "alice-token")[0] == 200   # the static token works as ever

    def test_the_addresses_are_the_base_urls(self, client, users, monkeypatch):
        monkeypatch.setattr(settings, "BASE_URL", "https://wiki.example.org")
        resp = client.get("/mcp", headers=HOST)
        assert 'resource_metadata="https://wiki.example.org/.well-known/oauth-protected-resource/mcp"' in resp.headers["WWW-Authenticate"]
        meta = client.get("/.well-known/oauth-protected-resource/mcp", headers=HOST).get_json()
        assert meta["resource"] == "https://wiki.example.org/mcp"
        assert meta["authorization_servers"] == ["https://wiki.example.org"]

    def test_without_a_login_there_is_none(self, client):
        resp = client.post("/mcp", headers=HOST, json={})
        assert resp.status_code == 403 and "MCP is off" in resp.get_json()["error"]
        for path in ("/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-protected-resource",
                     "/.well-known/oauth-authorization-server", "/oauth/authorize"):
            assert client.get(path, headers=HOST).status_code == 404
        assert client.post("/oauth/register", headers=HOST, json={"redirect_uris": [CALLBACK]}).status_code == 404
        assert token(client, grant_type="refresh_token").status_code == 404
        assert "Sign in from the assistant" not in client.get("/assistant", headers=HOST).data.decode()

    def test_the_settings_turn_it_off(self, client, monkeypatch):
        monkeypatch.setattr(settings, "USERS", {"bob": USERS["bob"]})
        monkeypatch.setattr(settings, "CONFIG_FILE", "mcp-oauth = no\n")
        resp = client.post("/mcp", headers=HOST, json={})
        assert resp.status_code == 403 and "./run.sh user token NAME" in resp.get_json()["error"]
        assert client.get("/.well-known/oauth-authorization-server", headers=HOST).status_code == 404
        assert client.post("/oauth/register", headers=HOST, json={"redirect_uris": [CALLBACK]}).status_code == 404
        login(client, "bob")
        assert client.get("/oauth/authorize", headers=HOST).status_code == 404
        assert "Sign in from the assistant" not in client.get("/assistant", headers=HOST).data.decode()

    def test_turned_off_the_connections_stop(self, bob, wiki_dir, monkeypatch):
        _, tokens = connect(bob, "bob-pw")
        assert mcp(bob, tokens["access_token"])[0] == 200
        monkeypatch.setattr(settings, "CONFIG_FILE", "mcp-oauth = no\n")
        assert mcp(bob, tokens["access_token"])[0] == 401   # alice has a static token: /mcp is on
        assert mcp(bob, "alice-token")[0] == 200


class TestRegister:

    def test_a_client_registers_and_zone_keeps_nothing(self, client, users, wiki_dir):
        client.get("/login", headers=HOST)
        before = sorted(p.name for p in (wiki_dir / ".zone").iterdir())
        resp = client.post("/oauth/register", headers={**HOST, "Origin": "https://claude.ai"}, json={
            "client_name": "  Claude\n<b>x</b> ", "redirect_uris": [CALLBACK, "http://127.0.0.1:3118/callback"],
            "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"]})
        assert resp.status_code == 201
        reg = resp.get_json()
        assert reg["client_name"] == "Claude <b>x</b>" and reg["token_endpoint_auth_method"] == "none"
        assert reg["redirect_uris"] == [CALLBACK, "http://127.0.0.1:3118/callback"]
        assert reg["client_id"].startswith("zc1.") and "client_secret" not in reg
        assert sorted(p.name for p in (wiki_dir / ".zone").iterdir()) == before

    @pytest.mark.parametrize("redirects", [
        [], ["http://evil.example/cb"], ["https://claude.ai/cb#frag"], ["javascript:alert(1)"], ["cursor://x/cb"],
        ["https://user:pw@claude.ai/cb"], [CALLBACK, "ftp://x/y"], ["https://a.example/" + str(i) for i in range(11)],
        ["https://cl\u0430ude.ai/cb"], ["https://cl%D0%B0ude.ai/cb"],     # a look-alike name (a Cyrillic а), also escaped
    ])
    def test_where_answers_may_go(self, client, users, redirects):
        resp = client.post("/oauth/register", headers=HOST, json={"client_name": "X", "redirect_uris": redirects})
        assert resp.status_code == 400 and resp.get_json()["error"] == "invalid_redirect_uri"

    def test_an_international_name_is_shown_as_it_is_spelled(self, bob):
        """A host that isn't ASCII is refused (above), so the question and
        Connect an assistant show every name as punycode: a look-alike can't
        pass for the name it copies."""
        redirect = "https://xn--clude-0ve.ai/cb"
        client_id = register(bob, [redirect])
        page = bob.get("/oauth/authorize?" + urlencode(asking(client_id, redirect)), headers=HOST).data.decode()
        assert '<strong class="oauth-host">xn--clude-0ve.ai</strong>' in page

    def test_a_body_that_is_no_metadata(self, client, users):
        resp = client.post("/oauth/register", headers=HOST, data="client_name=x")
        assert resp.status_code == 400 and resp.get_json()["error"] == "invalid_client_metadata"

    def test_a_client_id_zone_didnt_sign_is_no_client(self, bob):
        client_id = register(bob)
        payload = base64.urlsafe_b64encode(json.dumps({"n": "Claude", "r": ["https://evil.example/cb"]}).encode())
        forged = "zc1." + payload.rstrip(b"=").decode() + "." + client_id.rsplit(".", 1)[1]
        for bad in (forged, client_id[:-2], "zc1.x.y", ""):
            resp = bob.get("/oauth/authorize?" + urlencode(asking(bad, "https://evil.example/cb")), headers=HOST)
            body = resp.data.decode()
            assert resp.status_code == 400 and "names no client this wiki knows" in body
            assert "Refresh" not in resp.headers and "evil.example" not in body


class TestAllowing:

    def test_signed_out_it_leads_through_the_login_and_back(self, client, users):
        client_id = register(client)
        path = "/oauth/authorize?" + urlencode(asking(client_id))
        resp = client.get(path, headers=HOST)
        assert resp.status_code == 302 and resp.headers["Location"].startswith("/login?next=%2Foauth%2Fauthorize%3F")
        form = client.get(resp.headers["Location"], headers=HOST).data.decode()
        action = re.search(r'<form class="login-form" method="post" action="([^"]+)"', form).group(1).replace("&amp;", "&")
        resp = client.post(action, data={"username": "bob", "password": "bob-pw"}, headers=HOST)
        assert resp.status_code == 302 and resp.headers["Location"] == path
        body = client.get(path, headers=HOST).data.decode()
        assert "<h2>Connect an assistant?</h2>" in body

    def test_the_page_says_who_asks_and_where_the_answer_goes(self, bob):
        client_id = register(bob, name="Claude")
        resp = bob.get("/oauth/authorize?" + urlencode(asking(client_id)), headers=HOST)
        body = resp.data.decode()
        assert resp.status_code == 200 and resp.headers["Cache-Control"] == "no-store"
        assert '<strong class="oauth-client">Claude</strong> asks to use this wiki as you, <strong>bob</strong>' in body
        assert '<strong class="oauth-host">claude.ai</strong>' in body and "a program on this computer" not in body
        assert 'type="password"' in body and ">Allow</button>" in body and ">Deny</button>" in body
        assert f'name="client_id" value="{client_id}"' in body and 'name="state" value="s-123"' in body
        assert "Refresh" not in resp.headers

    def test_allowing_takes_the_password(self, bob, wiki_dir):
        client_id = register(bob)
        for password in ("wrong", "", "alice-pw"):
            resp, code = allow(bob, asking(client_id), password)
            assert resp.status_code == 403 and code is None
            assert "Incorrect password: nothing was allowed." in resp.data.decode()
        assert grants(wiki_dir) == []

    def test_wrong_passwords_are_throttled_as_at_the_login(self, bob):
        client_id = register(bob)
        for _ in range(5):
            assert allow(bob, asking(client_id), "wrong")[0].status_code == 403
        resp, code = allow(bob, asking(client_id), "bob-pw")
        assert resp.status_code == 429 and code is None and "Try again in" in resp.data.decode()

    def test_allow_sends_the_code_back_by_a_page_not_a_redirect(self, bob):
        client_id = register(bob)
        resp, code = allow(bob, asking(client_id), "bob-pw")
        assert resp.status_code == 200 and re.fullmatch(r"[0-9a-f]{64}", code)
        to, query = answer_of(resp)
        assert to.startswith(CALLBACK + "?") and query == {"code": code, "state": "s-123", "iss": BASE}
        assert "<h2>Connected</h2>" in resp.data.decode() and "Location" not in resp.headers
        assert resp.headers["Cache-Control"] == "no-store" and resp.headers["Referrer-Policy"] == "same-origin"
        assert "form-action 'self'" in resp.headers["Content-Security-Policy"]   # the policy is as everywhere

    def test_deny_says_so_to_the_client(self, bob, wiki_dir):
        client_id = register(bob)
        resp = bob.post("/oauth/authorize", headers=HOST, data={**asking(client_id), "answer": "deny"})
        to, query = answer_of(resp)
        assert query == {"error": "access_denied", "state": "s-123", "iss": BASE} and to.startswith(CALLBACK)
        assert "<h2>Not connected</h2>" in resp.data.decode() and grants(wiki_dir) == []

    def test_an_answer_goes_only_where_the_client_registered(self, bob):
        client_id = register(bob)
        for redirect in ("https://evil.example/cb", CALLBACK + "/more", "https://claude.ai.evil.example/api/mcp/auth_callback", ""):
            for send in (bob.get, None):
                if send:
                    resp = bob.get("/oauth/authorize?" + urlencode(asking(client_id, redirect)), headers=HOST)
                else:
                    resp = bob.post("/oauth/authorize", headers=HOST,
                                    data={**asking(client_id, redirect), "answer": "allow", "password": "bob-pw"})
                body = resp.data.decode()
                assert resp.status_code == 400 and "an address its client didn&#39;t register" in body
                assert "Refresh" not in resp.headers and "evil.example" not in body

    @pytest.mark.parametrize("change,error", [
        ({"response_type": "token"}, "unsupported_response_type"),
        ({"code_challenge": ""}, "invalid_request"),
        ({"code_challenge_method": "plain"}, "invalid_request"),
        ({"resource": "https://other.example/mcp"}, "invalid_target"),
    ])
    def test_what_a_client_got_wrong_it_is_told(self, bob, change, error):
        client_id = register(bob)
        resp = bob.get("/oauth/authorize?" + urlencode(asking(client_id, **change)), headers=HOST)
        body = resp.data.decode()
        assert resp.status_code == 400 and "This can't be connected" in body and 'type="password"' not in body
        back = re.search(r'<a href="([^"]+)" rel="noreferrer">Back to claude.ai</a>', body).group(1).replace("&amp;", "&")
        assert parse_qs(urlsplit(back).query) == {"error": [error], "state": ["s-123"], "iss": [BASE]}
        # And allowing it all the same makes no code.
        resp, code = allow(bob, asking(client_id, **change), "bob-pw")
        assert resp.status_code == 400 and code is None

    def test_a_program_on_this_computer_listens_on_any_port(self, bob):
        client_id = register(bob, ["http://localhost:3118/callback"], "Claude Code")
        redirect = "http://localhost:51234/callback"
        body = bob.get("/oauth/authorize?" + urlencode(asking(client_id, redirect)), headers=HOST).data.decode()
        assert '<strong class="oauth-host">localhost:51234</strong>' in body and "a program on this computer" in body
        resp, code = allow(bob, asking(client_id, redirect), "bob-pw")
        assert answer_of(resp)[0].startswith(redirect + "?") and code
        assert exchange(bob, client_id, code, redirect).status_code == 200
        for other in ("http://localhost:51234/other", "http://127.0.0.1:51234/callback", "https://localhost:51234/callback"):
            assert bob.get("/oauth/authorize?" + urlencode(asking(client_id, other)), headers=HOST).status_code == 400

    def test_another_sites_post_is_refused(self, bob, wiki_dir):
        client_id = register(bob)
        resp = bob.post("/oauth/authorize", headers={**HOST, "Origin": "https://evil.example"},
                        data={**asking(client_id), "answer": "allow", "password": "bob-pw"})
        assert resp.status_code == 403 and "Refresh" not in resp.headers

    def test_signed_out_nothing_is_allowed(self, client, users, wiki_dir):
        client_id = register(client)
        resp = client.post("/oauth/authorize", headers=HOST,
                           data={**asking(client_id), "answer": "allow", "password": "bob-pw", "username": "bob"})
        assert resp.status_code == 302 and "Refresh" not in resp.headers


class TestTokens:

    def test_the_code_becomes_tokens_and_the_assistant_acts_as_its_user(self, bob, wiki_dir):
        client_id, tokens = connect(bob, "bob-pw")
        assert tokens["token_type"] == "Bearer" and tokens["expires_in"] == 3600 and tokens["scope"] == "mcp"
        access, refresh = tokens["access_token"], tokens["refresh_token"]
        bob.cookies.clear()   # the assistant has no session, only its token
        status, text = mcp(bob, access, "write_page", note=True, slug="From Claude", content="Hello\n")
        assert status == 200 and list((wiki_dir / "private/bob").glob("*from-claude*")), text
        # Kept as hashes, owner only, beside the other state.
        kept = (wiki_dir / ".zone" / "grants").read_text()
        assert access not in kept and refresh not in kept and client_id not in kept
        assert (wiki_dir / ".zone" / "grants").stat().st_mode & 0o777 == 0o600
        [g] = grants(wiki_dir)
        assert g["user"] == "bob" and g["client"] == "Claude" and g["redirect"] == "claude.ai"
        assert g["access"] == "sha256:" + hashlib.sha256(access.encode()).hexdigest()
        assert "grants" not in (wiki_dir / ".zone" / "config").read_text()

    def test_tags_on_several_tasks_at_once(self, bob, wiki_dir):
        """update_task with ids takes only the tag arguments, and the
        connection's name Zonewiki adds to every call through one (for the
        commit's message) is none of the client's."""
        _, tokens = connect(bob, "bob-pw")
        bob.cookies.clear()
        access = tokens["access_token"]
        ids = []
        for title in ("One", "Two"):
            status, text = mcp(bob, access, "create_task", title=title)
            assert status == 200 and "Created" in text, text
            ids.append(re.search(r"id: ([0-9a-f]{8})", text).group(1))
        status, text = mcp(bob, access, "update_task", ids=ids, add_tags=["batch"])
        assert status == 200 and "With ids only" not in text, text
        status, text = mcp(bob, access, "search_tasks", tag="batch")
        assert "One" in text and "Two" in text, text
        # Anything else the client sends with ids is still refused.
        status, text = mcp(bob, access, "update_task", ids=ids, add_tags=["x"], title="No")
        assert "With ids only add_tags, remove_tags and a position (position, after, before) work, not title" in text, text

    def test_the_token_call_needs_no_session_and_no_origin(self, bob):
        client_id = register(bob)
        _, code = allow(bob, asking(client_id), "bob-pw")
        bob.cookies.clear()
        resp = bob.post("/oauth/token", headers={**HOST, "Origin": "https://claude.ai"}, data={
            "grant_type": "authorization_code", "client_id": client_id, "code": code, "redirect_uri": CALLBACK,
            "code_verifier": VERIFIER})   # no resource: it can only be this wiki's
        assert resp.status_code == 200 and resp.headers["Cache-Control"] == "no-store"

    @pytest.mark.parametrize("change,error", [
        ({"code_verifier": "w" * 50}, "invalid_grant"),
        ({"code_verifier": ""}, "invalid_grant"),
        ({"redirect_uri": "https://claude.ai/other"}, "invalid_grant"),
        ({"code": "0" * 64}, "invalid_grant"),
        ({"resource": "https://other.example/mcp"}, "invalid_target"),
        ({"client_id": "zc1.x.y"}, "invalid_client"),
        ({"grant_type": "password"}, "unsupported_grant_type"),
    ])
    def test_what_gets_no_tokens(self, bob, wiki_dir, change, error):
        client_id = register(bob)
        _, code = allow(bob, asking(client_id), "bob-pw")
        resp = exchange(bob, client_id, code, **change)
        assert resp.status_code == 400 and resp.get_json()["error"] == error
        assert grants(wiki_dir) == []

    def test_a_code_is_another_clients_no_use(self, bob, wiki_dir):
        client_id, other = register(bob), register(bob, name="Another")
        _, code = allow(bob, asking(client_id), "bob-pw")
        assert exchange(bob, other, code).get_json()["error"] == "invalid_grant"
        assert exchange(bob, client_id, code).status_code == 400   # and it is used up

    def test_a_code_works_once(self, bob, wiki_dir):
        """A code used a second time was copied (RFC 6749, 4.1.2): the
        connection made with it is ended."""
        client_id = register(bob)
        _, code = allow(bob, asking(client_id), "bob-pw")
        first = exchange(bob, client_id, code)
        assert first.status_code == 200 and len(grants(wiki_dir)) == 1
        assert mcp(bob, first.get_json()["access_token"])[0] == 200
        resp = exchange(bob, client_id, code)
        assert resp.status_code == 400 and resp.get_json()["error"] == "invalid_grant"
        assert grants(wiki_dir) == [] and mcp(bob, first.get_json()["access_token"])[0] == 401
        resp = token(bob, grant_type="refresh_token", client_id=client_id, refresh_token=first.get_json()["refresh_token"])
        assert resp.get_json()["error"] == "invalid_grant"

    def test_failures_are_throttled(self, bob):
        client_id = register(bob)
        for _ in range(5):
            assert exchange(bob, client_id, "0" * 64).status_code == 400
        resp = exchange(bob, client_id, "0" * 64)
        assert resp.status_code == 429 and "Retry-After" in resp.headers

    def test_a_refresh_gives_new_tokens_and_ends_the_old(self, bob, wiki_dir):
        client_id, first = connect(bob, "bob-pw")
        resp = token(bob, grant_type="refresh_token", client_id=client_id, refresh_token=first["refresh_token"])
        assert resp.status_code == 200
        second = resp.get_json()
        assert second["access_token"] != first["access_token"] and second["refresh_token"] != first["refresh_token"]
        assert mcp(bob, first["access_token"])[0] == 401 and mcp(bob, second["access_token"])[0] == 200
        assert len(grants(wiki_dir)) == 1

    def test_a_refresh_token_used_twice_ends_the_connection(self, bob, wiki_dir):
        client_id, first = connect(bob, "bob-pw")
        second = token(bob, grant_type="refresh_token", client_id=client_id, refresh_token=first["refresh_token"]).get_json()
        resp = token(bob, grant_type="refresh_token", client_id=client_id, refresh_token=first["refresh_token"])
        assert resp.status_code == 400 and resp.get_json()["error"] == "invalid_grant"
        assert grants(wiki_dir) == [] and mcp(bob, second["access_token"])[0] == 401
        resp = token(bob, grant_type="refresh_token", client_id=client_id, refresh_token=second["refresh_token"])
        assert resp.get_json()["error"] == "invalid_grant"

    def test_a_refresh_token_is_its_clients(self, bob, wiki_dir):
        client_id, tokens = connect(bob, "bob-pw")
        other = register(bob, name="Another")
        resp = token(bob, grant_type="refresh_token", client_id=other, refresh_token=tokens["refresh_token"])
        assert resp.get_json()["error"] == "invalid_grant" and len(grants(wiki_dir)) == 1
        assert token(bob, grant_type="refresh_token", client_id=client_id,
                     refresh_token=tokens["refresh_token"]).status_code == 200

    def test_tokens_expire(self, bob, wiki_dir):
        client_id, tokens = connect(bob, "bob-pw")
        edit_grants(wiki_dir, access_expires="2020-01-01T00:00:00Z")
        assert mcp(bob, tokens["access_token"])[0] == 401
        resp = token(bob, grant_type="refresh_token", client_id=client_id, refresh_token=tokens["refresh_token"])
        assert resp.status_code == 200 and mcp(bob, resp.get_json()["access_token"])[0] == 200
        edit_grants(wiki_dir, refresh_expires="2020-01-01T00:00:00Z")
        resp = token(bob, grant_type="refresh_token", client_id=client_id, refresh_token=resp.get_json()["refresh_token"])
        assert resp.get_json()["error"] == "invalid_grant"
        assert "No assistant is connected" in bob.get("/assistant", headers=HOST).data.decode()

    def test_the_day_it_was_last_used_is_kept(self, bob, wiki_dir):
        _, tokens = connect(bob, "bob-pw")
        edit_grants(wiki_dir, used="2020-01-01")
        assert mcp(bob, tokens["access_token"])[0] == 200
        assert grants(wiki_dir)[0]["used"] != "2020-01-01"


class TestConnections:

    def test_the_page_lists_them_and_ends_each(self, bob, wiki_dir):
        body = bob.get("/assistant", headers=HOST).data.decode()
        assert "Sign in from the assistant" in body and "No assistant is connected as you this way" in body
        assert f"claude mcp add --transport http zone {BASE}/mcp</code>" in body
        _, one = connect(bob, "bob-pw", name="Claude")
        _, two = connect(bob, "bob-pw", "http://localhost:3118/callback", "Claude Code")
        body = bob.get("/assistant", headers=HOST).data.decode()
        rows = re.findall(r"<tr>\s*<td>([^<]*)</td><td>([^<]*)</td>", body)
        assert rows == [("Claude Code", "localhost:3118"), ("Claude", "claude.ai")]   # newest first
        ids = re.findall(r'action="/assistant/connections/([0-9a-f]{16})/revoke"', body)
        assert len(ids) == 2
        resp = bob.post(f"/assistant/connections/{ids[0]}/revoke", headers=HOST)
        assert resp.status_code == 303 and resp.headers["Location"] == "/assistant?done=disconnect"
        assert "Ended the connection" in bob.get(resp.headers["Location"], headers=HOST).data.decode()
        assert mcp(bob, two["access_token"])[0] == 401 and mcp(bob, one["access_token"])[0] == 200
        assert [g["client"] for g in grants(wiki_dir)] == ["Claude"]
        assert bob.post(f"/assistant/connections/{ids[0]}/revoke", headers=HOST).status_code == 404

    def test_they_are_only_ones_own(self, client, users, wiki_dir):
        _, tokens = connect(login(client, "bob"), "bob-pw")
        gid = grants(wiki_dir)[0]["id"]
        login(client, "alice")
        assert gid not in client.get("/assistant", headers=HOST).data.decode()
        assert client.post(f"/assistant/connections/{gid}/revoke", headers=HOST).status_code == 404
        assert mcp(client, tokens["access_token"])[0] == 200

    def test_a_viewers_assistant_only_reads(self, client, users, wiki_dir, monkeypatch):
        login(client, "carol")
        client_id = register(client)
        assert "You are a viewer: it reads what you see, and changes nothing." in client.get(
            "/oauth/authorize?" + urlencode(asking(client_id)), headers=HOST).data.decode()
        resp, code = allow(client, asking(client_id), "carol-pw")
        assert "may now read this wiki" in resp.data.decode() and "it changes nothing" in resp.data.decode()
        tokens = exchange(client, client_id, code).get_json()
        assert mcp(client, tokens["access_token"], "read_page", slug="index")[0] == 200
        _, text = mcp(client, tokens["access_token"], "write_page", slug="carols", content="Hers\n")
        assert "you are a viewer" in text and not (wiki_dir / "carols.md").exists()
        # What the question said holds: made an editor, she has an assistant that reads.
        assert grants(wiki_dir)[0]["read_only"] is True
        monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}})
        _, text = mcp(client, tokens["access_token"], "write_page", slug="carols", content="Hers\n")
        assert "This connection only reads" in text and not (wiki_dir / "carols.md").exists()
        login(client, "carol")
        gid = grants(wiki_dir)[0]["id"]
        assert client.post(f"/assistant/connections/{gid}/revoke", headers=HOST).status_code == 303

    def test_a_disabled_users_are_refused(self, bob, wiki_dir, monkeypatch):
        client_id, tokens = connect(bob, "bob-pw")
        monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}, "bob": {"disabled": "yes"}})
        assert mcp(bob, tokens["access_token"])[0] == 401
        resp = token(bob, grant_type="refresh_token", client_id=client_id, refresh_token=tokens["refresh_token"])
        assert resp.get_json()["error"] == "invalid_grant"
        # Enabled again, the connection is as it was.
        monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}})
        assert mcp(bob, tokens["access_token"])[0] == 200
        assert token(bob, grant_type="refresh_token", client_id=client_id,
                     refresh_token=tokens["refresh_token"]).status_code == 200

    def test_removing_a_user_ends_theirs(self, client, users, wiki_dir):
        _, bobs = connect(login(client, "bob"), "bob-pw")
        _, alices = connect(login(client, "alice"), "alice-pw")
        assert client.post("/admin/users/bob/remove", headers=HOST).status_code == 303
        assert [g["user"] for g in grants(wiki_dir)] == ["alice"]
        assert mcp(client, bobs["access_token"])[0] == 401 and mcp(client, alices["access_token"])[0] == 200

    def test_a_reset_link_ends_them_a_new_password_doesnt(self, client, users, wiki_dir):
        _, tokens = connect(login(client, "bob"), "bob-pw")
        resp = client.post("/password", headers=HOST,
                           data={"current": "bob-pw", "password": "bobs new one", "again": "bobs new one"})
        assert resp.status_code == 303 and mcp(client, tokens["access_token"])[0] == 200
        login(client, "alice")
        page = client.post("/admin/users/bob/reset", headers=HOST).data.decode()
        link = re.search(r'<code class="users-link">https?://[^/]+(/welcome/[0-9a-f]{64})</code>', page).group(1)
        client.cookies.clear()
        assert "the connections of assistants you allowed" in client.get(link, headers=HOST).data.decode()
        resp = client.post(link, headers=HOST, data={"password": "after the reset", "again": "after the reset"})
        assert resp.status_code == 303
        assert grants(wiki_dir) == [] and mcp(client, tokens["access_token"])[0] == 401


class TestWithoutUsers:

    def test_with_the_wikis_password_the_connection_is_the_wikis(self, client, wiki_dir, monkeypatch):
        monkeypatch.setattr(settings, "PASSWORD", "one password")
        assert client.post("/mcp", headers=HOST, json={}).status_code == 401   # no token set, but a way to sign in
        assert client.post("/login", data={"password": "one password"}, headers=HOST).status_code == 302
        client_id = register(client)
        body = client.get("/oauth/authorize?" + urlencode(asking(client_id)), headers=HOST).data.decode()
        assert "asks to use this wiki." in body and "it searches, reads and writes the pages and tasks" in body
        assert allow(client, asking(client_id), "wrong")[0].status_code == 403
        _, tokens = connect(client, "one password")
        status, text = mcp(client, tokens["access_token"], "write_page", slug="from-claude", content="Hello\n")
        assert status == 200 and (wiki_dir / "from-claude.md").exists(), text
        assert grants(wiki_dir)[0]["user"] == ""
        body = client.get("/assistant", headers=HOST).data.decode()
        assert "<td>Claude</td><td>claude.ai</td>" in body
        gid = grants(wiki_dir)[0]["id"]
        assert client.post(f"/assistant/connections/{gid}/revoke", headers=HOST).status_code == 303
        assert mcp(client, tokens["access_token"])[0] == 401


def connect_to_read(client, password, name="Claude"):
    """A new connection of whoever is signed in that only reads: its tokens."""
    client_id = register(client, [CALLBACK], name)
    resp, code = allow(client, {**asking(client_id), "may": "read"}, password)
    assert resp.status_code == 200 and code, resp.data
    assert "may now read this wiki" in resp.data.decode() and "it changes nothing" in resp.data.decode()
    return client_id, exchange(client, client_id, code).get_json()


def mcp_error(client, access, tool, **args):
    """A tool's text, and whether it is an error."""
    resp = client.post("/mcp", headers={**HOST, "Authorization": f"Bearer {access}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
    result = resp.get_json()["result"]
    return result["content"][0]["text"], result["isError"]


def tool_names(client, access):
    resp = client.post("/mcp", headers={**HOST, "Authorization": f"Bearer {access}"},
                       json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    return [t["name"] for t in resp.get_json()["result"]["tools"]]


def wiki_state(wiki):
    """Every file of the wiki but its settings and state, and git's head."""
    files = {str(p.relative_to(wiki)): p.read_bytes() for p in sorted(wiki.rglob("*"))
             if p.is_file() and ".git" not in p.parts and ".zone" not in p.parts}
    head = (wiki / ".git/HEAD").read_text() + "".join(p.read_text() for p in sorted((wiki / ".git/refs").rglob("*")) if p.is_file())
    return files, head


class TestReadOnly:
    """A connection given less than its user: it reads, and changes nothing."""

    def test_the_question_offers_it(self, bob):
        client_id = register(bob)
        body = bob.get("/oauth/authorize?" + urlencode(asking(client_id)), headers=HOST).data.decode()
        assert 'name="may" value="write" checked' in body and 'name="may" value="read">' in body
        assert "it sees and changes what you do, no more" in body and "and changes nothing" in body
        # A wrong password keeps what was chosen.
        resp, code = allow(bob, {**asking(client_id), "may": "read"}, "wrong")
        assert code is None and 'name="may" value="read" checked' in resp.data.decode()

    def test_it_reads_and_every_writing_tool_is_refused(self, bob, wiki_dir, git_wiki_dir):
        (wiki_dir / "garden.md").write_text("# Garden\n\nBeds.\n")
        _, full = connect(bob, "bob-pw", name="Full")
        _, tokens = connect_to_read(bob, "bob-pw")
        access = tokens["access_token"]
        assert grants(wiki_dir)[-1]["read_only"] is True and grants(wiki_dir)[0]["read_only"] is False
        text, refused = mcp_error(bob, access, "read_page", slug="garden")
        assert not refused and "Beds." in text
        assert not mcp_error(bob, access, "search_wiki", query="beds")[1]
        # It is shown the tools that read, and those alone.
        every, shown = tool_names(bob, full["access_token"]), tool_names(bob, access)
        writing = [t for t in every if t not in shown]
        assert "write_page" in writing and "undo_changes" in writing and "upload_file" in writing and len(writing) >= 14
        assert {"search_wiki", "read_page", "history", "read_guides", "read_file"} <= set(shown)
        # Called all the same, each is refused before it looks at anything, and nothing changes.
        before = wiki_state(wiki_dir)
        for name in writing:
            text, refused = mcp_error(bob, access, name, slug="garden", content="x", title="x", versions="abcdef1",
                                      new_slug="yard", find="Beds", replace="x", dry_run=False, tag="a", new_tag="b",
                                      name="x", list="x", new_name="y", id="x", version="abcdef1", filename="x.txt",
                                      content_base64="eA==", old_string="Beds", new_string="x")
            assert refused and text.startswith("This connection only reads"), (name, text)
        assert wiki_state(wiki_dir) == before
        # The same user's other connection writes.
        assert not mcp_error(bob, full["access_token"], "append_to_page", slug="garden", content="Paths.")[1]

    def test_no_upload_with_its_token(self, bob, wiki_dir):
        import io
        _, tokens = connect_to_read(bob, "bob-pw")
        bob.cookies.clear()                                        # the assistant has no session
        resp = bob.post("/api/upload", headers={**HOST, "Authorization": f"Bearer {tokens['access_token']}"},
                        data={"file": (io.BytesIO(b"x"), "x.png")})
        assert resp.status_code == 403 and b"only reads" in resp.data
        assert not (wiki_dir / "_assets/x.png").exists()

    def test_it_stays_read_only_through_a_refresh(self, bob, wiki_dir):
        client_id, tokens = connect_to_read(bob, "bob-pw")
        fresh = token(bob, grant_type="refresh_token", refresh_token=tokens["refresh_token"], client_id=client_id).get_json()
        assert mcp_error(bob, fresh["access_token"], "write_page", slug="x", content="x")[1]
        assert "write_page" not in tool_names(bob, fresh["access_token"])

    def test_the_page_says_what_each_may_do(self, bob, wiki_dir):
        connect(bob, "bob-pw", name="Writer")
        connect_to_read(bob, "bob-pw", name="Reader")
        body = bob.get("/assistant", headers=HOST).data.decode()
        rows = re.findall(r'<td>([^<]*)</td><td>[^<]*</td><td class="assistant-may">([^<]*)</td>', body)
        assert rows == [("Reader", "Only read"), ("Writer", "Read and change")]

    def test_a_connection_from_before_writes(self, bob, wiki_dir):
        """A connection kept without the field (made before there was one) is as it was."""
        _, tokens = connect(bob, "bob-pw")
        all = grants(wiki_dir)
        for g in all:
            del g["read_only"]
        (wiki_dir / ".zone" / "grants").write_text(json.dumps(all))
        assert not mcp_error(bob, tokens["access_token"], "write_page", slug="x", content="x")[1]


def change(client, gid, may="write", see=None, password=None):
    """Change… of the connection gid: what it may do (may) and see (see:
    "private", "group:dev"; None sends no choice of places), and the
    password when given."""
    form = {"may": may}
    if see is not None:
        form.update(choose="1", see=see)
    if password is not None:
        form["password"] = password
    return client.post(f"/assistant/connections/{gid}/change", headers=HOST, data=form)


def reach(wiki, gid):
    """What the connection gid has: (read_only, no_private, groups)."""
    g = next(g for g in grants(wiki) if g["id"] == gid)
    return g.get("read_only", False), g.get("no_private", False), g.get("groups")


class TestChange:
    """Change… on Connect an assistant: what a connection may do and see,
    the choices of Allow, taken away without the password and given back
    only with it, through the tokens it has."""

    def test_the_page_offers_allows_choices_as_they_are(self, bob, wiki_dir):
        connect(bob, "bob-pw", name="Writer")
        connect_to_read(bob, "bob-pw", name="Reader")
        body = bob.get("/assistant", headers=HOST).data.decode()
        forms = re.findall(r'<form method="post" action="/assistant/connections/([0-9a-f]{16})/change"(.*?)</form>', body, re.S)
        assert [gid for gid, _ in forms] == [g["id"] for g in reversed(grants(wiki_dir))]   # newest first
        reader, writer = forms[0][1], forms[1][1]
        assert 'data-client="Reader"' in reader and 'data-client="Writer"' in writer
        for form in (reader, writer):
            assert "What may it do?" in form and "What may it see?" in form and 'name="choose" value="1"' in form
            assert 'name="see" value="private" checked data-had' in form and 'name="password"' in form
        # What it has now is ticked, and marked as had: the page asks for the password only to give more.
        assert 'name="may" value="read" checked' in reader and 'name="may" value="write">' in reader
        assert 'name="may" value="write" checked data-had' in writer
        assert "/read-only" not in body and "Make read-only" not in body

    def test_taking_away_needs_no_password_and_holds_for_its_tokens(self, bob, wiki_dir):
        (wiki_dir / "private/bob").mkdir(parents=True)
        (wiki_dir / "private/bob/diary.md").write_text("# Diary\n\nquillwort\n")
        client_id, tokens = connect(bob, "bob-pw")
        gid = grants(wiki_dir)[0]["id"]
        access = tokens["access_token"]
        assert "quillwort" in mcp(bob, access, "read_page", slug="private/bob/diary")[1]
        resp = change(bob, gid, may="read", see=[])
        assert resp.status_code == 303 and resp.headers["Location"] == "/assistant?done=change"
        assert "Changed what that assistant may do and see" in bob.get(resp.headers["Location"], headers=HOST).data.decode()
        assert reach(wiki_dir, gid) == (True, True, None)
        # The token it has is asked with what was chosen, from its next call.
        text, refused = mcp_error(bob, access, "write_page", slug="x", content="x")
        assert refused and "only reads" in text and not (wiki_dir / "x.md").exists()
        assert "quillwort" not in mcp(bob, access, "read_page", slug="private/bob/diary")[1]
        assert "write_page" not in tool_names(bob, access)
        # And so is every refresh.
        fresh = token(bob, grant_type="refresh_token", refresh_token=tokens["refresh_token"], client_id=client_id).get_json()
        assert mcp_error(bob, fresh["access_token"], "write_page", slug="x", content="x")[1]
        assert grants(wiki_dir)[0]["id"] == gid and len(grants(wiki_dir)) == 1

    def test_giving_more_takes_the_password(self, bob, wiki_dir):
        _, tokens = connect_to_read(bob, "bob-pw")
        gid = grants(wiki_dir)[0]["id"]
        for password, says in ((None, "enter your password"), ("", "enter your password"),
                               ("wrong", "Incorrect password: nothing was changed."),
                               ("alice-pw", "Incorrect password: nothing was changed.")):
            resp = change(bob, gid, may="write", see=["private"], password=password)
            body = resp.data.decode()
            assert resp.status_code == 403 and says in body, (password, body[:300])
            # The form comes back open, as chosen, with why.
            assert '<details class="assistant-change" open>' in body and 'name="may" value="write" checked' in body
            assert reach(wiki_dir, gid) == (True, False, None)
        assert mcp_error(bob, tokens["access_token"], "write_page", slug="x", content="x")[1]
        resp = change(bob, gid, may="write", see=["private"], password="bob-pw")
        assert resp.status_code == 303 and reach(wiki_dir, gid) == (False, False, None)
        text, refused = mcp_error(bob, tokens["access_token"], "write_page", slug="x", content="# X\n")
        assert not refused and (wiki_dir / "x.md").exists(), text

    def test_a_mixed_change_counts_as_giving_more(self, bob, wiki_dir):
        client_id = register(bob)
        resp, code = allow(bob, {**asking(client_id), "choose": "1", "see": []}, "bob-pw")
        exchange(bob, client_id, code)
        gid = grants(wiki_dir)[0]["id"]
        assert reach(wiki_dir, gid) == (False, True, None)
        # Only read (less), with the private place (more): the password.
        assert change(bob, gid, may="read", see=["private"]).status_code == 403
        assert reach(wiki_dir, gid) == (False, True, None)
        assert change(bob, gid, may="read", see=["private"], password="bob-pw").status_code == 303
        assert reach(wiki_dir, gid) == (True, False, None)

    def test_wrong_passwords_are_throttled_as_at_the_login(self, bob, wiki_dir):
        connect_to_read(bob, "bob-pw")
        gid = grants(wiki_dir)[0]["id"]
        for _ in range(5):
            assert change(bob, gid, see=["private"], password="wrong").status_code == 403
        resp = change(bob, gid, see=["private"], password="bob-pw")
        assert resp.status_code == 429 and "Try again in" in resp.data.decode()
        assert reach(wiki_dir, gid)[0] is True
        # Taking away asks no password, so nothing to wait for.
        assert change(bob, gid, may="read", see=[]).status_code == 303

    def test_only_ones_own(self, client, users, wiki_dir):
        _, tokens = connect(login(client, "bob"), "bob-pw")
        gid = grants(wiki_dir)[0]["id"]
        login(client, "alice")
        assert change(client, gid, may="read").status_code == 404
        assert change(client, gid, may="write", password="alice-pw").status_code == 404
        assert change(client, "nope", may="read").status_code == 404
        assert reach(wiki_dir, gid) == (False, False, None)
        assert not mcp_error(client, tokens["access_token"], "write_page", slug="bobs", content="x")[1]

    def test_a_viewers_stays_read_only(self, client, users, wiki_dir, monkeypatch):
        login(client, "carol")
        client_id = register(client)
        body = client.get("/oauth/authorize?" + urlencode(asking(client_id)), headers=HOST).data.decode()
        assert "What may it do?" not in body and "What may it see?" in body     # what it sees is hers to choose
        _, code = allow(client, {**asking(client_id), "choose": "1", "see": []}, "carol-pw")
        tokens = exchange(client, client_id, code).get_json()
        gid = grants(wiki_dir)[0]["id"]
        assert reach(wiki_dir, gid) == (True, True, None)
        page = client.get("/assistant", headers=HOST).data.decode()
        assert "What may it see?" in page and "What may it do?" not in page
        # Read and change, with her password: still only reads; the private place she may give back.
        assert change(client, gid, may="write", see=["private"], password="carol-pw").status_code == 303
        assert reach(wiki_dir, gid) == (True, False, None)
        assert mcp_error(client, tokens["access_token"], "write_page", slug="carols", content="x")[1]
        # Made an editor, she may give it more, with her password.
        monkeypatch.setattr(settings, "USER_SETTINGS", {"alice": {"role": "admin"}})
        login(client, "carol")
        assert change(client, gid, may="write", see=["private"]).status_code == 403
        assert change(client, gid, may="write", see=["private"], password="carol-pw").status_code == 303
        assert reach(wiki_dir, gid) == (False, False, None)

    def test_it_is_judged_by_what_it_has_when_posted(self, bob, wiki_dir):
        """Two tabs: one makes it read-only; the other, still showing Read
        and change, takes the private place away and leaves Read and change
        ticked: that gives it more than it has now, so it takes the password."""
        connect(bob, "bob-pw")
        gid = grants(wiki_dir)[0]["id"]
        assert change(bob, gid, may="read", see=["private"]).status_code == 303
        assert change(bob, gid, may="write", see=[]).status_code == 403
        assert reach(wiki_dir, gid) == (True, False, None)

    def test_without_users_only_what_it_does(self, client, wiki_dir, monkeypatch):
        monkeypatch.setattr(settings, "PASSWORD", "one password")
        client.post("/login", data={"password": "one password"}, headers=HOST)
        _, tokens = connect(client, "one password")
        gid = grants(wiki_dir)[0]["id"]
        page = client.get("/assistant", headers=HOST).data.decode()
        assert "What may it do?" in page and "What may it see?" not in page
        assert change(client, gid, may="read").status_code == 303
        assert mcp_error(client, tokens["access_token"], "write_page", slug="x", content="x")[1]
        assert change(client, gid, may="write", password="wrong").status_code == 403
        assert change(client, gid, may="write", password="one password").status_code == 303
        assert not mcp_error(client, tokens["access_token"], "write_page", slug="x", content="# X\n")[1]
