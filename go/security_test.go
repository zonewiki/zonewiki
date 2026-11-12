package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
	"zonewiki/internal/settingsfile"
)

// clock replaces now for a test.
func clock(t *testing.T, at time.Time) *time.Time {
	t.Helper()
	cur := at
	now = func() time.Time { return cur }
	t.Cleanup(func() { now = time.Now })
	return &cur
}

func TestWrongPasswordsSlowTheGuesserDown(t *testing.T) {
	at := clock(t, time.Unix(1_000_000, 0))
	th := newThrottle()
	for range 5 {
		if th.wait("x") != 0 {
			t.Fatal("waits before 5 failures")
		}
		th.failed("x")
	}
	if w := th.wait("x"); w != time.Minute || waitMessage(w) != "Too many wrong attempts. Try again in 1 minute." {
		t.Fatalf("wait %v", w)
	}
	*at = at.Add(61 * time.Second)
	if th.wait("x") != 0 {
		t.Fatal("still waits after a minute")
	}
	th.failed("x") // the 6th: 2 minutes now
	if w := th.wait("x"); w != 2*time.Minute || !strings.Contains(waitMessage(w), "2 minutes") {
		t.Fatalf("wait %v", w)
	}
	th.succeeded("x") // a success forgets the failures
	if th.wait("x") != 0 {
		t.Fatal("a success didn't clear")
	}
}

// Tries sent at once are checked one by one: after five failures the rest
// are refused unchecked, however many were on their way.
func TestTriesAtOnceAreCountedOneByOne(t *testing.T) {
	th := newThrottle()
	var checked atomic.Int32
	var wg sync.WaitGroup
	for range 50 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			th.try("x", func() bool {
				checked.Add(1)
				time.Sleep(time.Millisecond)
				return false
			})
		}()
	}
	wg.Wait()
	if n := checked.Load(); n != 5 {
		t.Fatalf("%d tries were checked, want 5", n)
	}
	if len(th.trying) != 0 {
		t.Fatalf("locks left: %d", len(th.trying))
	}
	// Another client isn't held up, and a right try clears its failures.
	th.failed("y")
	if ok, wait := th.try("y", func() bool { return true }); !ok || wait != 0 || th.fails["y"].count != 0 {
		t.Fatalf("y: %v %v %v", ok, wait, th.fails["y"])
	}
}

func TestNetwork(t *testing.T) {
	for in, want := range map[string]string{
		"203.0.113.9": "203.0.113.9", "2001:db8:1:2:3:4:5:6": "2001:db8:1:2::/64", "2001:db8:1:2::1": "2001:db8:1:2::/64",
		"::ffff:203.0.113.9": "::ffff:203.0.113.9", "fe80::1%eth0": "fe80::/64", "not an address": "not an address",
	} {
		if got := network(in); got != want {
			t.Errorf("%s: %q, want %q", in, got, want)
		}
	}
}

func TestLongestWaitIsAnHour(t *testing.T) {
	th := newThrottle()
	for range 30 {
		th.failed("x")
	}
	if w := th.wait("x"); w <= 3500*time.Second || w > time.Hour {
		t.Fatalf("wait %v", w)
	}
}

func TestQuietClientsAreForgotten(t *testing.T) {
	at := clock(t, time.Unix(1_000_000, 0))
	th := newThrottle()
	th.failed("old")
	*at = at.Add(25 * time.Hour)
	th.failed("new")
	if _, ok := th.fails["old"]; ok {
		t.Fatal("a client quiet for a day is kept")
	}
}

func TestClientKey(t *testing.T) {
	configWiki(t, "")
	for _, c := range []struct{ remote, forwarded, want string }{
		{"8.8.8.8:1234", "10.0.0.1", "8.8.8.8"},           // a client on the internet can't pick its own key
		{"10.0.0.2:1234", "1.1.1.1", "10.0.0.2"},          // nor one on the LAN: no proxy of the settings'
		{"127.0.0.1:1234", "1.1.1.1, 9.9.9.9", "9.9.9.9"}, // a proxy: the last entry is the one it added
		{"127.0.0.1:1234", "", "127.0.0.1"},
		{"[::1]:1234", "203.0.113.9", "203.0.113.9"},
	} {
		r := httptest.NewRequest("GET", "/", nil)
		r.RemoteAddr = c.remote
		if c.forwarded != "" {
			r.Header.Set("X-Forwarded-For", c.forwarded)
		}
		if got := clientKey(r); got != c.want {
			t.Errorf("%v: %q", c, got)
		}
	}
	// A proxy in a container network, and one in front of it.
	configWiki(t, "trusted-proxies = 172.16.0.0/12, 192.168.1.5\n")
	for _, c := range []struct{ remote, forwarded, want string }{
		{"172.17.0.1:1234", "203.0.113.9", "203.0.113.9"},
		{"172.17.0.1:1234", "6.6.6.6, 203.0.113.9, 192.168.1.5", "203.0.113.9"},
		{"127.0.0.1:1234", "203.0.113.9", "127.0.0.1"}, // loopback isn't trusted once the setting is
		{"10.0.0.2:1234", "1.1.1.1", "10.0.0.2"},
	} {
		r := httptest.NewRequest("GET", "/", nil)
		r.RemoteAddr = c.remote
		if c.forwarded != "" {
			r.Header.Set("X-Forwarded-For", c.forwarded)
		}
		if got := clientKey(r); got != c.want {
			t.Errorf("%v: %q", c, got)
		}
	}
}

func TestHostName(t *testing.T) {
	for in, want := range map[string]string{
		"Wiki.Example.org:443": "wiki.example.org", "[::1]:5000": "[::1]", "leaf.home.arpa": "leaf.home.arpa",
	} {
		if got := hostName(in); got != want {
			t.Errorf("%s: %s", in, got)
		}
	}
}

func TestSecretKeyIsMadeOnceAndKept(t *testing.T) {
	wiki := t.TempDir()
	key := secretKey(wiki)
	path := filepath.Join(wiki, ".zone", "secret_key")
	b, _ := os.ReadFile(path)
	st, _ := os.Stat(path)
	if len(key) != 64 || string(b) != string(key) || st.Mode().Perm() != 0o600 {
		t.Fatalf("key %q, file %q, mode %v", key, b, st.Mode())
	}
	if string(secretKey(wiki)) != string(key) {
		t.Fatal("a restart doesn't keep it")
	}
}

func TestSecretKeyWithoutAWritableWiki(t *testing.T) {
	wiki := t.TempDir()
	_ = os.WriteFile(filepath.Join(wiki, ".zone"), []byte("not a folder"), 0o666)
	first, second := secretKey(wiki), secretKey(wiki)
	if len(first) != 64 || len(second) != 64 || string(first) == string(second) {
		t.Fatal("works, not kept")
	}
}

func TestSession(t *testing.T) {
	testWiki(t, nil)
	sessionKey = []byte(strings.Repeat("k", 64))
	at := clock(t, time.Unix(1_000_000, 0))
	w := httptest.NewRecorder()
	setSession(w, httptest.NewRequest("GET", "/", nil), "")
	cookie := w.Result().Cookies()[0]
	if user, ok, renew := signedIn(withCookie(cookie.Value)); !ok || renew || user != "" {
		t.Fatal("a fresh session isn't valid")
	}
	if _, ok, _ := signedIn(withCookie(cookie.Value + "x")); ok {
		t.Fatal("a forged signature passes")
	}
	sessionKey = []byte(strings.Repeat("o", 64))
	if _, ok, _ := signedIn(withCookie(cookie.Value)); ok {
		t.Fatal("another key's session passes")
	}
	sessionKey = []byte(strings.Repeat("k", 64))
	*at = at.Add(2 * 24 * time.Hour)
	if _, ok, renew := signedIn(withCookie(cookie.Value)); !ok || !renew {
		t.Fatal("an older session isn't renewed")
	}
	*at = at.Add(30 * 24 * time.Hour)
	if _, ok, _ := signedIn(withCookie(cookie.Value)); ok {
		t.Fatal("an expired session passes")
	}
}

func withCookie(value string) *http.Request {
	r := httptest.NewRequest("GET", "/", nil)
	r.AddCookie(&http.Cookie{Name: sessionCookie, Value: value})
	return r
}

// A session names its user; it is valid only while that user is in
// the settings, and a user's session is no good without users (nor the other
// way round).
func TestUserSession(t *testing.T) {
	testWiki(t, nil)
	sessionKey = []byte(strings.Repeat("k", 64))
	session := func(user string) string {
		w := httptest.NewRecorder()
		setSession(w, httptest.NewRequest("GET", "/", nil), user)
		return w.Result().Cookies()[0].Value
	}
	alice, anyone := session("alice"), session("")
	if _, ok, _ := signedIn(withCookie(alice)); ok {
		t.Error("a user's session passes without users")
	}
	settings().Users = map[string]*User{"alice": {Name: "alice"}, "bob": {Name: "bob"}}
	if user, ok, _ := signedIn(withCookie(alice)); !ok || user != "alice" {
		t.Errorf("alice's session: %q %v", user, ok)
	}
	if _, ok, _ := signedIn(withCookie(anyone)); ok {
		t.Error("a password session passes with users")
	}
	// A new password ends the sessions of the old one, the same password
	// set again too (a new salt); the other users' stay.
	bob := session("bob")
	settings().Users["alice"].PasswordHash = hashPassword("pw")
	alice = session("alice")
	settings().Users["alice"] = &User{Name: "alice", PasswordHash: hashPassword("pw")}
	if _, ok, _ := signedIn(withCookie(alice)); ok {
		t.Error("a session of the old password passes")
	}
	if _, ok, _ := signedIn(withCookie(bob)); !ok {
		t.Error("another user's session ended")
	}
	alice = session("alice")
	delete(settings().Users, "alice")
	if _, ok, _ := signedIn(withCookie(alice)); ok {
		t.Error("a removed user's session passes")
	}
	settings().Users["alice"] = &User{Name: "alice"}
	if _, ok, _ := signedIn(withCookie(alice)); ok {
		t.Error("a user removed and added again has their old sessions")
	}
	// The wiki's password likewise.
	settings().Users, settings().PasswordHash = nil, hashPassword("pw")
	anyone = session("")
	settings().PasswordHash = hashPassword("new")
	if _, ok, _ := signedIn(withCookie(anyone)); ok {
		t.Error("a session of the wiki's old password passes")
	}
}

func TestLoadConfig(t *testing.T) {
	wiki := t.TempDir()
	env := func(kv ...string) func(string) string {
		m := map[string]string{"ZONE_DATA": wiki}
		for i := 0; i < len(kv); i += 2 {
			m[kv[i]] = kv[i+1]
		}
		return func(k string) string { return m[k] }
	}
	file := func(text string) {
		if err := settingsfile.Write(filepath.Join(wiki, ".zone", "config"), text); err != nil {
			t.Fatal(err)
		}
	}
	file("hosts = Wiki.Example.org:443, ,leaf.home.arpa\nnoshare = #People,private, Personal/ \n" +
		"base-url = http://x:5000/\n")
	c, s, err := loadConfig(env("ZONE_ALLOW_REMOTE", "yes"))
	if err != nil {
		t.Fatal(err)
	}
	if len(s.Hosts) != 2 || !s.Hosts["wiki.example.org"] || !c.AllowRemote || s.BaseURL != "http://x:5000" {
		t.Fatalf("%+v %+v", c, s)
	}
	if !s.NoshareTags["people"] || !s.NoshareTags["private"] || len(s.NoshareFolder) != 1 || s.NoshareFolder[0] != "personal/" {
		t.Fatalf("noshare %v %v", s.NoshareTags, s.NoshareFolder)
	}
	if !isDir(filepath.Join(wiki, "_assets")) {
		t.Fatal("no _assets/")
	}
	file("")
	if _, s, _ := loadConfig(env()); !s.NoshareTags["noshare"] {
		t.Fatal("noshare's default")
	}
	for _, bad := range []string{"locale = de_AT", "locale = german", "locale = de-AT;x", `locale = "><script>`,
		"date-format = us", "date-format = yyyy-mm-dd", "semantic-min-score = high", "semantic-min-score = 2"} {
		file(bad + "\n")
		if _, _, err := loadConfig(env()); err == nil {
			t.Errorf("%v: no error", bad)
		}
	}
	file("")
	for _, bad := range [][]string{{"ZONE_DATA", ""}, {"ZONE_DATA", filepath.Join(wiki, "missing")}} {
		if _, _, err := loadConfig(env(bad...)); err == nil {
			t.Errorf("%v: no error", bad)
		}
	}
	file("locale =  de-AT \ndate-format = ISO\nsemantic-min-score = 0.42\n")
	if _, s, err := loadConfig(env()); err != nil || s.Locale != "de-AT" || s.DateFormat != "iso" ||
		*s.SemanticMinScore != 0.42 {
		t.Fatalf("%v %+v", err, s)
	}
	for _, tag := range []string{"en", "zh-Hant-TW", ""} {
		file("locale = " + tag + "\n")
		if _, s, err := loadConfig(env()); err != nil || s.Locale != tag {
			t.Errorf("locale = %s: %v", tag, err)
		}
	}
}

// The wiki's settings left in the environment stop Zone: they would be
// ignored (a forgotten LEAF_PASSWORD: an open wiki).
func TestStaleEnv(t *testing.T) {
	wiki := t.TempDir()
	for _, c := range []struct{ env, want string }{
		{"LEAF_PASSWORD", "LEAF_PASSWORD is set in the environment (.env), but Zone reads the wiki's settings only from .zone/config now. Move them there with ./run.sh config import"},
		{"LEAF_USERS", "LEAF_USERS is set"}, {"LEAF_LOCALE", "LEAF_LOCALE is set"},
		{"LEAF_SEMANTIC_MIN_SCORE", "LEAF_SEMANTIC_MIN_SCORE is set"},
		{"LEAF_SECRET_KEY", "LEAF_SECRET_KEY is set in the environment (.env), but Zone no longer reads it"},
	} {
		getenv := func(k string) string { return map[string]string{"ZONE_DATA": wiki, c.env: "x"}[k] }
		if _, _, err := loadConfig(getenv); err == nil || !strings.HasPrefix(err.Error(), c.want) {
			t.Errorf("%s: %v", c.env, err)
		}
	}
	getenv := func(k string) string {
		return map[string]string{"ZONE_DATA": wiki, "LEAF_HOSTS": "x", "LEAF_LOCALE": "de"}[k]
	}
	if _, _, err := loadConfig(getenv); err == nil || !strings.HasPrefix(err.Error(), "LEAF_HOSTS, LEAF_LOCALE are set") {
		t.Errorf("two: %v", err)
	}
	empty := func(k string) string { return map[string]string{"ZONE_DATA": wiki, "LEAF_LOCALE": ""}[k] }
	if _, _, err := loadConfig(empty); err != nil {
		t.Errorf("empty is unset: %v", err)
	}
}

func TestVersionIsSemver(t *testing.T) {
	if !regexp.MustCompile(`^\d+\.\d+\.\d+$`).MatchString(version) {
		t.Errorf("version %q", version)
	}
}

func TestUploadNames(t *testing.T) {
	for in, want := range map[string]string{"a/b.png": "b.png", "..": "..", "x/": "x", ".": "", "": ""} {
		if got := baseName(in); got != want {
			t.Errorf("baseName(%q) = %q", in, got)
		}
	}
	for in, want := range map[string]string{"a.PNG": ".PNG", ".bashrc": "", "a.": ".", "a.tar.gz": ".gz", "a": ""} {
		if got := suffix(in); got != want {
			t.Errorf("suffix(%q) = %q", in, got)
		}
	}
	if uploadExt("image/jpeg") != ".jpg" || uploadExt("image/png") != ".png" || uploadExt("x/unknown") != ".bin" {
		t.Error("uploadExt")
	}
}

func TestHealth(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/ok" {
			http.NotFound(w, r)
		}
	}))
	defer srv.Close()
	if health(srv.URL+"/ok") != 0 || health(srv.URL+"/missing") != 1 || health("http://127.0.0.1:1/") != 1 {
		t.Error("health")
	}
}
