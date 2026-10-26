"""The Go port under the tests (LEAF_TEST_GO=1): the `client` fixture talks
HTTP to a Go server on the test's wiki instead of to Flask in-process.

GoClient takes the calls of Flask's test client (get/post/put/delete with
headers, data, json, query_string, content_type, follow_redirects;
set_cookie) and answers with werkzeug's TestResponse, so a test runs
unchanged against either. The settings a test patches in config (a
password, LEAF_HOSTS …) go to the server as its environment: when they
change, the server restarts on the same wiki before the next request."""

import http.client
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

import config

REPO = Path(__file__).resolve().parent.parent
_binary: Path | None = None


def go_enabled() -> bool:
    return os.environ.get("LEAF_TEST_GO", "").lower() in ("1", "true", "yes")


def binary() -> Path:
    """The Go server, built once per test process."""
    global _binary
    if _binary is None:
        out = Path(tempfile.mkdtemp(prefix="leaf_go_")) / "leaf"
        go = shutil.which("go") or "go"
        subprocess.run([go, "build", "-o", str(out), "."], cwd=REPO / "go", check=True)
        _binary = out
    return _binary


def _settings() -> dict[str, str]:
    """The environment that gives the Go server config's settings now."""
    noshare = [f"#{t}" for t in sorted(config.NOSHARE_TAGS)] + list(config.NOSHARE_FOLDERS)
    env = {
        "LEAF_DATA": str(config.LEAF_DATA),
        "LEAF_BUILD": config.LEAF_BUILD,
        "LEAF_PASSWORD": config.LEAF_PASSWORD,
        "LEAF_MCP_TOKEN": config.LEAF_MCP_TOKEN,
        "LEAF_BASE_URL": config.LEAF_BASE_URL,
        "LEAF_HOST_DATA": config.LEAF_HOST_DATA,
        "LEAF_QUERY_LOG": "1" if config.LEAF_QUERY_LOG else "",
        "LEAF_GIT_REMOTE": config.LEAF_GIT_REMOTE,
        "LEAF_ALLOW_REMOTE": "1" if config.LEAF_ALLOW_REMOTE else "",
        "LEAF_HOSTS": ",".join(sorted(config.LEAF_HOSTS)),
        "LEAF_NOSHARE": ",".join(noshare) or ",",
        "LEAF_LOCALE": config.LEAF_LOCALE,
        "LEAF_DATE_FORMAT": config.LEAF_DATE_FORMAT,
    }
    if os.environ.get("LEAF_SECRET_KEY"):
        env["LEAF_SECRET_KEY"] = os.environ["LEAF_SECRET_KEY"]
    if not config.GIT_ENABLED:
        # No git on its PATH: the server finds none, as config.GIT_ENABLED says.
        env["PATH"] = os.pathsep.join(d for d in os.environ.get("PATH", "").split(os.pathsep)
                                      if d and not os.path.exists(os.path.join(d, "git")))
    return env


def _flags() -> list[str]:
    """The flags for what a test patches that isn't a setting: Leaf's docs
    folder (manual.docs_root, for leaf_help)."""
    import manual
    return ["-docs", str(manual.docs_root() or "/nonexistent")]


class GoServer:
    """One Go server process, on addr (a free port: 127.0.0.1:0)."""

    def __init__(self, env: dict[str, str], log: Path, addr: str = "127.0.0.1:0", flags: list[str] = ()):
        self.env, self.flags = env, list(flags)
        base = {k: v for k, v in os.environ.items() if not k.startswith("LEAF_")}
        self.binary = binary()   # built before PATH might lose go
        self.log = open(log, "ab")
        self.proc = subprocess.Popen(
            [str(self.binary), "-addr", addr, "-public", str(REPO / "src" / "public"), *self.flags],
            env={**base, **env}, stdout=subprocess.PIPE, stderr=self.log)
        line = self.proc.stdout.readline().decode()
        if " listening on " not in line:
            self.stop()
            raise RuntimeError(f"Go server didn't start: {line!r} (log: {log})")
        host, port = line.rsplit(" ", 1)[1].strip().rsplit(":", 1)
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
    port, when given one) when the settings in config (or _flags) change."""

    def __init__(self, log: Path, port: int = 0):
        self.log, self.port = log, port
        self.server: GoServer | None = None

    def ensure(self) -> GoServer:
        env, flags = _settings(), _flags()
        if self.server and (self.server.env, self.server.flags) != (env, flags):
            self.stop()
        if not self.server:
            self.server = GoServer(env, self.log, f"127.0.0.1:{self.port}", flags)
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
