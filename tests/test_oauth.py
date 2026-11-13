"""OAuth for /mcp (docs/spec/mcp.md, Connecting with OAuth): the tests play
an MCP client over HTTP: discovery, registration, the user's Allow in the
browser, the code for tokens, /mcp with them, refresh, the end of a
connection, and everything Zone refuses on the way."""

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
    ])
    def test_where_answers_may_go(self, client, users, redirects):
        resp = client.post("/oauth/register", headers=HOST, json={"client_name": "X", "redirect_uris": redirects})
        assert resp.status_code == 400 and resp.get_json()["error"] == "invalid_redirect_uri"

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
        client_id = register(bob)
        _, code = allow(bob, asking(client_id), "bob-pw")
        assert exchange(bob, client_id, code).status_code == 200
        resp = exchange(bob, client_id, code)
        assert resp.status_code == 400 and resp.get_json()["error"] == "invalid_grant"
        assert len(grants(wiki_dir)) == 1

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

    def test_a_viewers_assistant_only_reads(self, client, users, wiki_dir):
        login(client, "carol")
        client_id = register(client)
        assert "You are a viewer: it reads, and changes nothing." in client.get(
            "/oauth/authorize?" + urlencode(asking(client_id)), headers=HOST).data.decode()
        _, tokens = connect(client, "carol-pw")
        assert mcp(client, tokens["access_token"], "read_page", slug="index")[0] == 200
        _, text = mcp(client, tokens["access_token"], "write_page", slug="carols", content="Hers\n")
        assert "you are a viewer" in text and not (wiki_dir / "carols.md").exists()
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
        assert "asks to use this wiki: it will search, read and write its pages and tasks" in body
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
