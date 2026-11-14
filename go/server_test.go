package main

import (
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// zonewiki health asks localhost at the port Zone listens on.
func TestHealthURL(t *testing.T) {
	for args, want := range map[string]string{
		"":                 "http://localhost:5000/api/health",
		":8080":            "http://localhost:8080/api/health",
		"127.0.0.1:9000":   "http://localhost:9000/api/health",
		"nonsense":         "http://localhost:5000/api/health",
		"unix:/run/z.sock": "http://localhost/api/health",
	} {
		var a []string
		if args != "" {
			a = []string{args}
		}
		if got := healthURL(a); got != want {
			t.Errorf("%q: %s", args, got)
		}
	}
}

// --addr unix:PATH: Zone answers on a socket anyone who reaches the path may
// connect to, zonewiki health asks over it, and a socket file a killed Zone
// left is replaced, but not one still listened on, nor another file.
func TestListenOnSocket(t *testing.T) {
	configWiki(t, "")
	t.Cleanup(func() { onSocket = false })
	path := filepath.Join(t.TempDir(), "zone.sock")
	addr := socketPrefix + path

	// What a killed Zone leaves: the file, and nobody listening.
	dead, err := net.Listen("unix", path)
	if err != nil {
		t.Fatal(err)
	}
	dead.(*net.UnixListener).SetUnlinkOnClose(false)
	dead.Close()

	ln, err := listen(addr)
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	if st, err := os.Stat(path); err != nil || st.Mode().Perm() != 0o666 {
		t.Errorf("the socket's mode: %v, %v", st.Mode(), err)
	}
	if got := listenName(ln); got != addr {
		t.Errorf("listenName: %q", got)
	}
	go http.Serve(ln, newHandler())
	if code := health(healthClient([]string{addr}), healthURL([]string{addr})); code != 0 {
		t.Errorf("health over the socket: %d", code)
	}
	if _, err := listen(addr); err == nil || !strings.Contains(err.Error(), "listens") {
		t.Errorf("a second Zone on the socket: %v", err)
	}

	file := filepath.Join(t.TempDir(), "notes.md")
	os.WriteFile(file, []byte("text"), 0o600)
	if _, err := listen(socketPrefix + file); err == nil || !strings.Contains(err.Error(), "no socket") {
		t.Errorf("a file in the socket's place: %v", err)
	}
	if text, _ := os.ReadFile(file); string(text) != "text" {
		t.Error("the file in the socket's place was touched")
	}
	if _, err := listen(socketPrefix); err == nil {
		t.Error("unix: without a path")
	}
}

// A socket's peer has no address: it counts as localhost, so a reverse
// proxy's X-Forwarded-For names the client unless trusted-proxies says otherwise.
func TestClientKeyOnSocket(t *testing.T) {
	configWiki(t, "")
	onSocket = true
	t.Cleanup(func() { onSocket = false })
	for _, c := range []struct{ forwarded, want string }{
		{"203.0.113.9", "203.0.113.9"},
		{"6.6.6.6, 203.0.113.9", "203.0.113.9"},
		{"", "127.0.0.1"},
	} {
		r := httptest.NewRequest("GET", "/", nil)
		r.RemoteAddr = "@"
		if c.forwarded != "" {
			r.Header.Set("X-Forwarded-For", c.forwarded)
		}
		if got := clientKey(r); got != c.want {
			t.Errorf("%v: %q", c, got)
		}
	}
	configWiki(t, "trusted-proxies = 172.17.0.1\n")
	r := httptest.NewRequest("GET", "/", nil)
	r.RemoteAddr = "@"
	r.Header.Set("X-Forwarded-For", "203.0.113.9")
	if got := clientKey(r); got != "127.0.0.1" {
		t.Errorf("trusted-proxies without localhost: %q", got)
	}
}
