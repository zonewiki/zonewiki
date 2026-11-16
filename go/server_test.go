package main

import (
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
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

// A socket's peer has no address: it is the reverse proxy, so its
// X-Forwarded-For names the client, whatever trusted-proxies holds (a wiki
// moved from a port to a socket keeps the proxy's old address there).
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
	if got := clientKey(r); got != "203.0.113.9" {
		t.Errorf("trusted-proxies without localhost: %q", got)
	}
	// The setting still says which entries before the last are proxies too.
	r.Header.Set("X-Forwarded-For", "6.6.6.6, 203.0.113.9, 172.17.0.1")
	if got := clientKey(r); got != "203.0.113.9" {
		t.Errorf("a proxy in front of the proxy: %q", got)
	}
}

// --addr fd:N: Zone serves on a listening socket it was started with, as
// systemd hands one to a service of a socket unit. A Unix socket's file
// stays when Zone closes its listener: it is the holder's.
func TestListenOnAHandedSocket(t *testing.T) {
	configWiki(t, "")
	t.Cleanup(func() { onSocket, handedSocket = false, false })
	path := filepath.Join(t.TempDir(), "zone.sock")
	holder, err := net.Listen("unix", path)
	if err != nil {
		t.Fatal(err)
	}
	defer holder.Close()
	f, err := holder.(*net.UnixListener).File()
	if err != nil {
		t.Fatal(err)
	}
	ln, err := listen("fd:" + strconv.Itoa(int(f.Fd())))
	if err != nil {
		t.Fatal(err)
	}
	if !handedSocket || !onSocket {
		t.Errorf("handedSocket %v, onSocket %v", handedSocket, onSocket)
	}
	if got := listenName(ln); got != socketPrefix+path {
		t.Errorf("listenName: %q", got)
	}
	go http.Serve(ln, newHandler())
	addr := socketPrefix + path
	if code := health(healthClient([]string{addr}), healthURL([]string{addr})); code != 0 {
		t.Errorf("health over the handed socket: %d", code)
	}
	ln.Close()
	if _, err := os.Stat(path); err != nil {
		t.Errorf("the holder's socket file went with Zone's listener: %v", err)
	}
	for _, bad := range []string{"fd:", "fd:x", "fd:2", "fd:987"} {
		if _, err := listen(bad); err == nil {
			t.Errorf("%s was taken", bad)
		}
	}
	file, err := os.Open(os.DevNull)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	if _, err := listen("fd:" + strconv.Itoa(int(file.Fd()))); err == nil || !strings.Contains(err.Error(), "listening socket") {
		t.Errorf("a file that is no socket: %v", err)
	}
}

// Zone is idle long enough to exit when nothing was asked for that long,
// nothing runs, and no throttle remembers a wrong try; the healthcheck is
// no request.
func TestIdleLongEnough(t *testing.T) {
	configWiki(t, "")
	at := clock(t, time.Unix(2_000_000, 0))
	loginThrottle, mcpThrottle = newThrottle(), newThrottle()
	t.Cleanup(func() { loginThrottle, mcpThrottle = newThrottle(), newThrottle() })
	h := countRequests(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if idle.busy.Load() != 1 && r.URL.Path != "/api/health" {
			t.Errorf("%s: %d requests counted while it runs", r.URL.Path, idle.busy.Load())
		}
	}))
	ask := func(path string) { h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("GET", path, nil)) }
	const after = 15 * time.Minute
	ask("/page/index")
	if idleLongEnough(after) {
		t.Error("idle right after a request")
	}
	*at = at.Add(14 * time.Minute)
	ask("/api/health")
	*at = at.Add(time.Minute)
	if !idleLongEnough(after) {
		t.Error("not idle 15 minutes after the last request, with a healthcheck between")
	}
	idle.busy.Add(1)
	if idleLongEnough(after) {
		t.Error("idle while a request runs")
	}
	idle.busy.Add(-1)
	loginThrottle.failed("203.0.113.9")
	*at = at.Add(23 * time.Hour)
	if idleLongEnough(after) {
		t.Error("idle while the throttle remembers a wrong password")
	}
	*at = at.Add(2 * time.Hour)
	if !idleLongEnough(after) {
		t.Error("not idle a day after the wrong password")
	}
	loginThrottle.failed("203.0.113.9")
	loginThrottle.succeeded("203.0.113.9")
	*at = at.Add(after)
	if !idleLongEnough(after) {
		t.Error("not idle after the right password cleared the client")
	}
	// Nor while the repository is packed: a join stopped halfway starts anew.
	setPacking := func(on bool) {
		packing.Lock()
		packing.running = on
		packing.Unlock()
	}
	setPacking(true)
	t.Cleanup(func() { setPacking(false) })
	if idleLongEnough(after) {
		t.Error("idle while the repository is packed")
	}
	setPacking(false)
	if !idleLongEnough(after) {
		t.Error("not idle after the pack ended")
	}
}
