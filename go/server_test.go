package main

import (
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

// zonewiki health asks localhost at the port Zonewiki listens on.
func TestHealthURL(t *testing.T) {
	for args, want := range map[string]string{
		"":                 "http://localhost:5080/api/health",
		":8080":            "http://localhost:8080/api/health",
		"127.0.0.1:9000":   "http://localhost:9000/api/health",
		"nonsense":         "http://localhost:5080/api/health",
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

// --addr: a port alone is localhost's unless ZONE_ALLOW_REMOTE, so a Zonewiki
// started by hand with no login isn't on the network; what is beyond
// loopback counts as reachable (a login first).
func TestListenAddr(t *testing.T) {
	for _, c := range []struct {
		addr   string
		remote bool
		want   string
		beyond bool
	}{
		{":5000", false, "127.0.0.1:5000", false},
		{":5000", true, ":5000", true},
		{":0", false, "127.0.0.1:0", false},
		{"0.0.0.0:5000", false, "0.0.0.0:5000", true},
		{"[::]:5000", false, "[::]:5000", true},
		{"192.168.1.5:5000", false, "192.168.1.5:5000", true},
		{"wiki.local:5000", false, "wiki.local:5000", true},
		{"127.0.0.1:5001", false, "127.0.0.1:5001", false},
		{"127.0.0.1:5001", true, "127.0.0.1:5001", false},
		{"[::1]:5000", false, "[::1]:5000", false},
		{"localhost:5000", false, "localhost:5000", false},
		{"unix:/run/zone.sock", false, "unix:/run/zone.sock", false},
		{"fd:3", false, "fd:3", false},
		{"nonsense", false, "nonsense", false},
	} {
		got := listenAddr(c.addr, c.remote)
		if got != c.want || beyondLoopback(got) != c.beyond {
			t.Errorf("%q remote=%v: %q, beyond loopback %v", c.addr, c.remote, got, beyondLoopback(got))
		}
	}
}

// --addr unix:PATH: Zonewiki answers on a socket anyone who reaches the path may
// connect to, zonewiki health asks over it, and a socket file a killed Zonewiki
// left is replaced, but not one still listened on, nor another file.
func TestListenOnSocket(t *testing.T) {
	configWiki(t, "")
	t.Cleanup(func() { onSocket = false })
	path := filepath.Join(t.TempDir(), "zone.sock")
	addr := socketPrefix + path

	// What a killed Zonewiki leaves: the file, and nobody listening.
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
		t.Errorf("a second Zonewiki on the socket: %v", err)
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

// A TCP socket Zonewiki was handed that listens beyond this machine counts as
// open to the network (a wiki without a login then asks for one first):
// "fd:3" doesn't say where it listens, the socket does. One on loopback,
// and a socket Zonewiki opened itself (--addr says where), don't.
func TestAHandedTCPSocketBeyondLoopback(t *testing.T) {
	configWiki(t, "")
	t.Cleanup(func() { onSocket, handedSocket = false, false })
	for _, c := range []struct {
		addr string
		want bool
	}{{"0.0.0.0:0", true}, {"127.0.0.1:0", false}} {
		holder, err := net.Listen("tcp4", c.addr)
		if err != nil {
			t.Fatal(err)
		}
		f, err := holder.(*net.TCPListener).File()
		if err != nil {
			t.Fatal(err)
		}
		handedSocket = false
		if handedBeyondLoopback(holder) {
			t.Errorf("%s, opened by Zonewiki: counted as handed", c.addr)
		}
		ln, err := listen("fd:" + strconv.Itoa(int(f.Fd())))
		if err != nil {
			t.Fatal(err)
		}
		if got := handedBeyondLoopback(ln); got != c.want || onSocket {
			t.Errorf("%s handed: beyond loopback %v, onSocket %v", c.addr, got, onSocket)
		}
		ln.Close()
		f.Close()
		holder.Close()
	}
}

// --addr fd:N: Zonewiki serves on a listening socket it was started with, as
// systemd hands one to a service of a socket unit. A Unix socket's file
// stays when Zonewiki closes its listener: it is the holder's.
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
	if !handedSocket || !onSocket || handedBeyondLoopback(ln) {
		t.Errorf("handedSocket %v, onSocket %v, beyond loopback %v", handedSocket, onSocket, handedBeyondLoopback(ln))
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
		t.Errorf("the holder's socket file went with Zonewiki's listener: %v", err)
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

// A Zonewiki that no request reached since its start (one started for the
// morning's mail, or by a health check) waits a minute to sleep again, not
// ZONE_IDLE_EXIT; a health check isn't a request, and the minute counts
// anew from when the morning's mails are out.
func TestAZonewikiNobodyAskedSleepsSoon(t *testing.T) {
	configWiki(t, "")
	at := clock(t, time.Unix(3_000_000, 0))
	loginThrottle, mcpThrottle = newThrottle(), newThrottle()
	idle.asked.Store(false)
	idle.last.Store(at.UnixNano()) // the start
	t.Cleanup(func() { idle.asked.Store(false) })
	h := countRequests(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	ask := func(path string) { h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("GET", path, nil)) }
	const setting = 15 * time.Minute
	if idleAfter(setting) != time.Minute || idleAfter(2*time.Second) != 2*time.Second {
		t.Fatalf("nobody asked: %s, %s", idleAfter(setting), idleAfter(2*time.Second))
	}
	ask("/api/health")
	*at = at.Add(59 * time.Second)
	if idleLongEnough(idleAfter(setting)) {
		t.Error("idle before its minute is over")
	}
	*at = at.Add(time.Second)
	if !idleLongEnough(idleAfter(setting)) {
		t.Error("not idle a minute after a start nobody asked for")
	}
	// While the morning's mails go out it stays, and its minute counts from
	// their end.
	digestsRunning.Store(true)
	if idleLongEnough(idleAfter(setting)) {
		t.Error("idle while the morning's mails go out")
	}
	digestsRunning.Store(false)
	idle.last.Store(at.UnixNano())
	if idleLongEnough(idleAfter(setting)) {
		t.Error("idle right after the morning's mails")
	}
	// Someone came: the setting's time from then on.
	ask("/page/index")
	*at = at.Add(14 * time.Minute)
	if idleAfter(setting) != setting || idleLongEnough(idleAfter(setting)) {
		t.Errorf("after a request: %s", idleAfter(setting))
	}
	*at = at.Add(time.Minute)
	if !idleLongEnough(idleAfter(setting)) {
		t.Error("not idle 15 minutes after the request")
	}
}

// Zonewiki is idle long enough to exit when nothing was asked for that long,
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
	loginThrottle.failed("203.0.113.9", "alice")
	*at = at.Add(23 * time.Hour)
	if idleLongEnough(after) {
		t.Error("idle while the throttle remembers a wrong password")
	}
	*at = at.Add(2 * time.Hour)
	if !idleLongEnough(after) {
		t.Error("not idle a day after the wrong password")
	}
	loginThrottle.failed("203.0.113.9", "alice")
	loginThrottle.succeeded("203.0.113.9", "alice")
	*at = at.Add(after)
	if !idleLongEnough(after) {
		t.Error("not idle after the right password cleared the account")
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

// ZONE_DATA unset: the wiki is ~/zonewiki, made at the first start (and
// said so), taken as it is after; one that is set must exist, and nothing
// is made for it.
func TestTheWikiByDefault(t *testing.T) {
	home := t.TempDir()
	env := func(kv ...string) func(string) string {
		m := map[string]string{"HOME": home}
		for i := 0; i+1 < len(kv); i += 2 {
			m[kv[i]] = kv[i+1]
		}
		return func(k string) string { return m[k] }
	}
	c, err := loadWiki(env())
	if err != nil {
		t.Fatal(err)
	}
	if want, _ := filepath.EvalSymlinks(home); c.Data != filepath.Join(want, "zonewiki") || !isDir(c.Data) {
		t.Fatalf("the wiki is %s", c.Data)
	}
	os.WriteFile(filepath.Join(c.Data, "index.md"), []byte("# Mine\n"), 0o666)
	if c2, err := loadWiki(env("ZONE_DATA", "")); err != nil || c2.Data != c.Data {
		t.Fatalf("again: %v %v", c2, err)
	}
	if b, _ := os.ReadFile(filepath.Join(c.Data, "index.md")); string(b) != "# Mine\n" {
		t.Errorf("the wiki changed: %q", b)
	}
	// A ZONE_DATA that is set and missing: refused, not made.
	missing := filepath.Join(home, "wkii")
	if _, err := loadWiki(env("ZONE_DATA", missing)); err == nil || !strings.Contains(err.Error(), "no wiki at "+missing) || exists(missing) {
		t.Errorf("a mistyped ZONE_DATA: %v", err)
	}
	// No home folder to put it in, or a file where it would go: refused.
	if _, err := loadWiki(func(string) string { return "" }); err == nil || !strings.Contains(err.Error(), "ZONE_DATA isn't set") {
		t.Errorf("no HOME: %v", err)
	}
	other := t.TempDir()
	os.WriteFile(filepath.Join(other, "zonewiki"), nil, 0o666)
	if _, err := loadWiki(func(k string) string { return map[string]string{"HOME": other}[k] }); err == nil ||
		!strings.Contains(err.Error(), "is a file") {
		t.Errorf("a file at ~/zonewiki: %v", err)
	}
	if _, err := loadWiki(func(k string) string { return map[string]string{"HOME": filepath.Join(other, "none")}[k] }); err == nil {
		t.Error("a home folder that isn't there: made")
	}
}

// The line at start says where to open Zonewiki: base-url, else localhost at
// its port; nothing on a socket or behind compose's port mapping.
func TestOpenURL(t *testing.T) {
	testWiki(t, nil)
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	port := strconv.Itoa(ln.Addr().(*net.TCPAddr).Port)
	if got := openURL(ln, ""); got != "http://localhost:"+port {
		t.Errorf("%q", got)
	}
	if got := openURL(ln, "https://wiki.example.org"); got != "https://wiki.example.org" {
		t.Errorf("base-url: %q", got)
	}
	cfg.PortMapped = true
	if got := openURL(ln, ""); got != "" {
		t.Errorf("behind compose: %q", got)
	}
	cfg.PortMapped = false
	sock, err := net.Listen("unix", filepath.Join(t.TempDir(), "z.sock"))
	if err != nil {
		t.Fatal(err)
	}
	defer sock.Close()
	if got := openURL(sock, ""); got != "" {
		t.Errorf("a socket: %q", got)
	}
	for bind, mapped := range map[string]bool{"": false, "127.0.0.1": true, "0.0.0.0": true} {
		c, err := loadWiki(func(k string) string { return map[string]string{"ZONE_DATA": cfg.Data, "ZONE_BIND": bind}[k] })
		if err != nil || c.PortMapped != mapped {
			t.Errorf("ZONE_BIND=%s: %v %v", bind, c, err)
		}
	}
}

// A port another program has: the error says to start Zonewiki on another one.
func TestPortTaken(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	_, err = listen(ln.Addr().String())
	if err == nil || !strings.Contains(portTaken(err).Error(), "zonewiki --addr :5081") {
		t.Errorf("%v", portTaken(err))
	}
	// AirPlay Receiver is named for 5000 alone, which it has on a Mac.
	if strings.Contains(portTaken(err).Error(), "AirPlay") {
		t.Errorf("AirPlay named for port %s: %v", ln.Addr(), portTaken(err))
	}
	at5000 := &net.OpError{Op: "listen", Net: "tcp", Addr: &net.TCPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 5000},
		Err: os.NewSyscallError("bind", syscall.EADDRINUSE)}
	if !strings.Contains(portTaken(at5000).Error(), "AirPlay Receiver has 5000") {
		t.Errorf("port 5000: %v", portTaken(at5000))
	}
	if other := os.ErrNotExist; portTaken(other) != other {
		t.Error("another error changed")
	}
}
