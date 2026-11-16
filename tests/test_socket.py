"""Zone on a Unix socket (--addr unix:PATH, A Unix socket in
docs/spec/deployment.md): for a reverse proxy on the same machine, with no
port. zonewiki health asks over the socket, and a peer there counts as
localhost, so the proxy's X-Forwarded-For names the client for the throttle.

And on a socket it was handed (--addr fd:N, as systemd's socket activation
does): with ZONE_IDLE_EXIT it exits when nobody asks, and connections that
come meanwhile wait in the socket for the next start."""

import http.client
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.parse

import pytest

from tests import settings
from tests.goserver import GoServer, _flags, _settings, binary


class SocketConnection(http.client.HTTPConnection):
    """HTTP to localhost, over the socket at path."""

    def __init__(self, path):
        super().__init__("localhost")
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX)
        if isinstance(self.timeout, (int, float)):
            self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


@pytest.fixture()
def zone(wiki_dir, tmp_path, monkeypatch):
    """Zone with a password, on a socket; returns the socket's path."""
    monkeypatch.setattr(settings, "PASSWORD", "pw")
    folder = tempfile.mkdtemp(prefix="zone")   # short: a socket's path holds 107 bytes
    server = GoServer(_settings(), tmp_path / "server.log", f"unix:{folder}/zone.sock", _flags())
    try:
        assert server.host == f"unix:{folder}/zone.sock"   # the line Zone prints names it as --addr does
        yield f"{folder}/zone.sock"
    finally:
        server.stop()
        shutil.rmtree(folder)


def login(path, password, forwarded):
    conn = SocketConnection(path)
    conn.request("POST", "/login", urllib.parse.urlencode({"password": password}),
                 {"Content-Type": "application/x-www-form-urlencoded", "X-Forwarded-For": forwarded})
    status = conn.getresponse().status
    conn.close()
    return status


def test_health_asks_over_the_socket(zone):
    assert subprocess.run([str(binary()), "health", f"unix:{zone}"]).returncode == 0
    assert subprocess.run([str(binary()), "health", f"unix:{zone}.gone"], stderr=subprocess.DEVNULL).returncode == 1


def test_the_proxy_names_the_client(zone):
    """Five wrong passwords from one client behind the proxy make that client
    wait, and no other: the socket's peer is trusted as localhost is."""
    for _ in range(5):
        assert login(zone, "nope", "1.2.3.4, 203.0.113.9") == 200
    assert login(zone, "pw", "203.0.113.9") == 429
    assert login(zone, "pw", "198.51.100.7") == 302


@pytest.fixture()
def held(wiki_dir, tmp_path, monkeypatch):
    """A listening socket this test holds, as systemd holds a socket unit's;
    returns (the socket, its path, a function that starts Zone on it)."""
    monkeypatch.setattr(settings, "PASSWORD", "pw")
    folder = tempfile.mkdtemp(prefix="zone")
    holder = socket.socket(socket.AF_UNIX)
    holder.bind(f"{folder}/zone.sock")
    holder.listen(16)
    servers = []

    def start(idle_exit="1s"):
        env = {**_settings(), "ZONE_IDLE_EXIT": idle_exit}
        server = GoServer(env, tmp_path / "server.log", f"fd:{holder.fileno()}", _flags(), pass_fds=(holder.fileno(),))
        servers.append(server)
        return server

    try:
        yield holder, f"{folder}/zone.sock", start
    finally:
        for server in servers:
            server.stop()
        holder.close()
        shutil.rmtree(folder)


def get(path, url, timeout=None):
    """The status of a GET over the socket; None when nothing answers within
    timeout seconds (no Zone is listening: the request waits in the socket)."""
    conn = SocketConnection(path)
    conn.timeout = timeout
    try:
        conn.request("GET", url)
        return conn.getresponse().status
    except TimeoutError:
        return None
    finally:
        conn.close()


def test_zone_exits_when_nobody_asks_and_the_next_request_waits(held):
    _, path, start = held
    server = start()
    assert server.host == f"unix:{path}"
    # Requests keep it up, well past the second it would wait.
    until = time.monotonic() + 2.5
    while time.monotonic() < until:
        assert get(path, "/login") == 200
        time.sleep(0.2)
    assert server.proc.poll() is None
    # The healthcheck alone doesn't: it exits, with 0, and leaves the socket.
    until = time.monotonic() + 10
    while server.proc.poll() is None and time.monotonic() < until:
        # One sent as Zone goes is answered by nobody (it waits in the socket,
        # as the request below does): it must not wait for ever.
        assert get(path, "/api/health", timeout=2) in (200, None)
        time.sleep(0.2)
    assert server.proc.wait(10) == 0
    # A request that comes now waits in the socket, and the next Zone answers it.
    conn = SocketConnection(path)
    conn.request("GET", "/login")
    start()
    assert conn.getresponse().status == 200
    conn.close()


def test_zone_stays_up_while_the_throttle_remembers_a_wrong_password(held):
    """An exit would forget who guessed wrong, and give five new tries."""
    _, path, start = held
    server = start()
    assert login(path, "nope", "203.0.113.9") == 200
    time.sleep(3)
    assert server.proc.poll() is None
    assert login(path, "pw", "203.0.113.9") == 302   # right: forgotten
    assert server.proc.wait(10) == 0


def test_idle_exit_is_ignored_on_a_socket_zone_opened_itself(wiki_dir, tmp_path):
    """Nothing would start it again."""
    folder = tempfile.mkdtemp(prefix="zone")
    server = GoServer({**_settings(), "ZONE_IDLE_EXIT": "1s"}, tmp_path / "server.log", f"unix:{folder}/zone.sock", _flags())
    try:
        time.sleep(2.5)
        assert server.proc.poll() is None
        assert "nothing would start it again" in (tmp_path / "server.log").read_text()
    finally:
        server.stop()
        shutil.rmtree(folder)
