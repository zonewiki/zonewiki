"""Zone's server under the tests: the `client` fixture talks HTTP to a Go
server on the test's wiki, the browser tests drive one.

GoClient takes the calls of Flask's test client, which the tests were
written for (get/post/put/delete with headers, data, json, query_string,
content_type, follow_redirects; set_cookie), and answers with werkzeug's
TestResponse. The settings a test
patches in tests/settings.py (a password, HOSTS …) go to the server
as its environment (and settings file, .zone/config): when they change, the
server restarts on the same wiki before the next request."""

import base64
import hashlib
import http.client
import json
import os
import shutil
import socket
import subprocess
import tempfile
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import quote

from werkzeug.datastructures import Headers
from werkzeug.test import EnvironBuilder, TestResponse

from tests import settings

REPO = Path(__file__).resolve().parent.parent
_binary: Path | None = None


def binary() -> Path:
    """The server, built once per test run: by the first process (pytest's,
    or with xdist its controller, before the workers start), which hands its
    path on in ZONE_TEST_BINARY. With ZONE_TEST_RACE it is built with Go's race
    detector (which needs cgo and a C compiler), and a race ends it."""
    global _binary
    if _binary is None:
        if os.environ.get("ZONE_TEST_BINARY"):
            _binary = Path(os.environ["ZONE_TEST_BINARY"])
        else:
            out = Path(tempfile.mkdtemp(prefix="zonewiki_go_")) / "zonewiki"
            go = shutil.which("go") or "go"
            race = bool(os.environ.get("ZONE_TEST_RACE"))
            subprocess.run([go, "build", *(["-race"] if race else []), "-o", str(out), "."], cwd=REPO / "go", check=True,
                           env={**os.environ, **({"CGO_ENABLED": "1"} if race else {})})
            if race:
                os.environ.setdefault("GORACE", "halt_on_error=1")
            os.environ["ZONE_TEST_BINARY"] = str(out)
            _binary = out
    return _binary


def _settings() -> dict[str, str]:
    """The environment that gives the server the deployment's settings now
    (the wiki's are in its settings file: config_text)."""
    env = {
        "ZONE_DATA": str(settings.ZONE_DATA),
        "ZONE_BUILD": settings.ZONE_BUILD,
        "ZONE_HOST_DATA": settings.ZONE_HOST_DATA,
        "ZONE_ALLOW_REMOTE": "1" if settings.ZONE_ALLOW_REMOTE else "",
        **settings.ENV,
    }
    if not settings.GIT_ENABLED:
        # No git on its PATH: the server finds none.
        env["PATH"] = os.pathsep.join(d for d in os.environ.get("PATH", "").split(os.pathsep)
                                      if d and not os.path.exists(os.path.join(d, "git")))
    return env


def config_text() -> str:
    """The settings file the server gets (.zone/config): the wiki's settings
    that differ from Zone's defaults, CONFIG_FILE, then the users; "" when
    there is nothing to say (the wiki then has no file)."""
    noshare = [f"#{t}" for t in sorted(settings.NOSHARE_TAGS)] + list(settings.NOSHARE_FOLDERS)
    values = {
        "password": password_hash(settings.PASSWORD) if settings.PASSWORD else "",
        "mcp-token": token_hash(settings.MCP_TOKEN) if settings.MCP_TOKEN else "",
        "base-url": settings.BASE_URL,
        "query-log": "yes" if settings.QUERY_LOG else "",
        "git-remote": settings.GIT_REMOTE,
        "hosts": ", ".join(sorted(settings.HOSTS)),
        "locale": settings.LOCALE,
        "date-format": settings.DATE_FORMAT,
    }
    text = "".join(f"{key} = {value}\n" for key, value in values.items() if value)
    if noshare != ["#noshare"]:
        text += f"noshare = {', '.join(noshare)}\n"
    if settings.NO_LOGIN and not settings.PASSWORD and not settings.USERS:
        text += "no-login = yes\n"
    text += settings.CONFIG_FILE
    for name, (password, token) in settings.USERS.items():
        text += f"\n[user {name}]\npassword = {password_hash(password)}\n"
        if token:
            text += f"mcp-token = {token_hash(token)}\n"
        for key, value in settings.USER_SETTINGS.get(name, {}).items():
            text += f"{key} = {value}\n"
    for group, includes in settings.GROUPS.items():
        text += f"\n[group {group}]\nincludes = {includes}\n"
    return text


def token_hash(token: str) -> str:
    """An MCP token as the settings file keeps it (go/configfile.go)."""
    return "sha256:" + hashlib.sha256(token.encode()).hexdigest()


# The settings file each wiki got last (path → text): a file the tests
# wrote is removed when the settings no longer give one.
_written: dict[Path, str] = {}


def write_config(text: str) -> None:
    """Gives the wiki the settings file text (none for "", unless a test
    wrote one there itself)."""
    path = Path(settings.ZONE_DATA) / ".zone" / "config"
    if text:
        path.parent.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8")
        path.chmod(0o600)
        _written[path] = text
    elif path in _written:
        if path.exists() and path.read_text(encoding="utf-8") == _written[path]:
            path.unlink()
        del _written[path]


def password_hash(password: str, iterations: int = 1000) -> str:
    """A password's hash as `zonewiki config passwd` makes it (go/users.go), with a
    fixed salt so the settings, and so the server, stay the same between
    requests, and few iterations, so a login is quick."""
    salt = b"zone-tests-salt!"
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations, 32)
    b64 = lambda b: base64.b64encode(b).decode().rstrip("=")
    return f"pbkdf2-sha256:{iterations}:{b64(salt)}:{b64(key)}"


def _flags() -> list[str]:
    """The flags for what a test sets that isn't a setting: Zone's docs
    folder (for zone_help), and the semantic index with the tests' fake
    embedder (tests/semantic.py)."""
    flags = ["--docs", str(settings.DOCS or "/nonexistent")]
    if settings.SEMANTIC is not None:
        flags += ["--semantic-test", json.dumps(settings.SEMANTIC.flag())]
    return flags


class GoServer:
    """One Go server process, on addr (a free port: 127.0.0.1:0; unix:PATH: a socket)."""

    def __init__(self, env: dict[str, str], log: Path, addr: str = "127.0.0.1:0", flags: list[str] = (),
                 config: str | None = None, pass_fds: tuple[int, ...] = ()):
        self.env, self.flags = env, list(flags)
        self.config = config_text() if config is None else config
        write_config(self.config)
        base = {k: v for k, v in os.environ.items() if not k.startswith("ZONE_")}
        self.binary = binary()   # built before PATH might lose go
        self.log = open(log, "ab")
        self.proc = subprocess.Popen(
            [str(self.binary), "--addr", addr, "--public", str(REPO / "public"), *self.flags],
            env={**base, **env}, stdout=subprocess.PIPE, stderr=self.log, pass_fds=pass_fds)
        line = self.proc.stdout.readline().decode()
        if " listening on " not in line:
            self.stop()
            raise RuntimeError(f"Go server didn't start: {line!r} (log: {log})")
        where = line.rsplit(" ", 1)[1].strip()
        if where.startswith("unix:"):   # a socket: no port
            self.host, self.port = where, 0
        else:
            host, port = where.rsplit(":", 1)
            self.host, self.port = host, int(port)

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc.stdout.close()
        self.log.close()


class LiveServer:
    """The test's Go server, started on first use and restarted (on the same
    port, when given one) when the settings (or _flags) change."""

    def __init__(self, log: Path, port: int = 0):
        self.log, self.port = log, port
        self.server: GoServer | None = None

    def ensure(self) -> GoServer:
        env, flags, config = _settings(), _flags(), config_text()
        if self.server and (self.server.env, self.server.flags, self.server.config) != (env, flags, config):
            self.stop()
        if not self.server:
            self.server = GoServer(env, self.log, f"127.0.0.1:{self.port}", flags, config)
        return self.server

    def stop(self) -> None:
        if self.server:
            self.server.stop()
            self.server = None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Cookie:
    def __init__(self, key, value):
        self.key, self.value = key, value


class GoClient:
    """Flask's test client, over HTTP to a Go server."""

    def __init__(self, log: Path):
        self.live = LiveServer(log)
        self.cookies: dict[str, str] = {}

    def close(self) -> None:
        self.live.stop()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # Cookies, as the test client's (one domain, no expiry but deletion).
    def set_cookie(self, key, value="", **kwargs):
        self.cookies[key] = value

    def delete_cookie(self, key, **kwargs):
        self.cookies.pop(key, None)

    def get_cookie(self, key, **kwargs):
        return _Cookie(key, self.cookies[key]) if key in self.cookies else None

    def _keep_cookies(self, headers: Headers) -> None:
        for raw in headers.getlist("Set-Cookie"):
            jar = SimpleCookie()
            jar.load(raw)
            for key, morsel in jar.items():
                if morsel["max-age"] in ("0", "-1") or not morsel.value and morsel["expires"]:
                    self.cookies.pop(key, None)
                else:
                    self.cookies[key] = morsel.value

    def open(self, path="/", method="GET", follow_redirects=False, **kwargs) -> TestResponse:
        builder = EnvironBuilder(path=path, method=method, **kwargs)
        try:
            environ = builder.get_environ()
            request = builder.get_request()
        finally:
            builder.close()
        body = environ["wsgi.input"].read()
        # PATH_INFO is WSGI's: the path's bytes as latin-1.
        target = quote(environ["PATH_INFO"].encode("latin-1") or b"/", safe="/%:@!$&'()*+,;=-._~")
        if environ.get("QUERY_STRING"):
            # As Flask's client takes it: "?q=a b" has its space encoded.
            target += "?" + quote(environ["QUERY_STRING"], safe="/%:@!$&'()*+,;=-._~?")
        headers = [(k, v) for k, v in builder.headers.items()
                   if k.lower() not in ("content-length", "content-type", "host")]
        if environ.get("CONTENT_TYPE"):
            headers.append(("Content-Type", environ["CONTENT_TYPE"]))
        host = builder.headers.get("Host") or "localhost"
        if self.cookies:
            headers.append(("Cookie", "; ".join(f"{k}={v}" for k, v in self.cookies.items())))
        server = self.live.ensure()
        conn = http.client.HTTPConnection(server.host, server.port, timeout=120)
        try:
            conn.putrequest(method, target, skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", host)
            for k, v in headers:
                conn.putheader(k, v)
            if body or method in ("POST", "PUT", "PATCH"):
                conn.putheader("Content-Length", str(len(body)))
            conn.endheaders(body or None)
            resp = conn.getresponse()
            data = resp.read()
            reply = Headers(resp.getheaders())
            status = f"{resp.status} {resp.reason}"
        finally:
            conn.close()
        self._keep_cookies(reply)
        response = TestResponse([data], status, reply, request=request)
        if follow_redirects and response.status_code in (301, 302, 303, 307, 308):
            location = response.headers["Location"]
            keep = {k: v for k, v in kwargs.items() if k in ("headers",)}
            again = "GET" if response.status_code in (301, 302, 303) else method
            return self.open(location, again, follow_redirects=True, **keep)
        return response

    def get(self, *args, **kw):
        return self.open(*args, method="GET", **kw)

    def post(self, *args, **kw):
        return self.open(*args, method="POST", **kw)

    def put(self, *args, **kw):
        return self.open(*args, method="PUT", **kw)

    def patch(self, *args, **kw):
        return self.open(*args, method="PATCH", **kw)

    def delete(self, *args, **kw):
        return self.open(*args, method="DELETE", **kw)

    def head(self, *args, **kw):
        return self.open(*args, method="HEAD", **kw)
