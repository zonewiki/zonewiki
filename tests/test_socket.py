"""Zone on a Unix socket (--addr unix:PATH, A Unix socket in
docs/spec/deployment.md): for a reverse proxy on the same machine, with no
port. zonewiki health asks over the socket, and a peer there counts as
localhost, so the proxy's X-Forwarded-For names the client for the throttle."""

import http.client
import shutil
import socket
import subprocess
import tempfile
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
