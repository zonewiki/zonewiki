package main

import (
	"encoding/base64"
	"encoding/json"
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
	t.Cleanup(func() { now = wikiNow })
	return &cur
}

func TestWrongPasswordsSlowTheGuesserDown(t *testing.T) {
	at := clock(t, time.Unix(1_000_000, 0))
	th := newThrottle()
	for range 5 {
		if th.wait("x", "alice") != 0 {
			t.Fatal("waits before 5 failures")
		}
		th.failed("x", "alice")
	}
	if w := th.wait("x", "alice"); w != time.Minute || waitMessage(w) != "Too many wrong attempts. Try again in 1 minute." {
		t.Fatalf("wait %v", w)
	}
	*at = at.Add(61 * time.Second)
	if th.wait("x", "alice") != 0 {
		t.Fatal("still waits after a minute")
	}
	th.failed("x", "alice") // the 6th: 2 minutes now
	if w := th.wait("x", "alice"); w != 2*time.Minute || !strings.Contains(waitMessage(w), "2 minutes") {
		t.Fatalf("wait %v", w)
	}
	th.succeeded("x", "alice") // a success forgets the failures
	if th.wait("x", "alice") != 0 {
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
			th.try("x", "alice", func() bool {
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
	th.failed("y", "bob")
	if ok, wait := th.try("y", "bob", func() bool { return true }); !ok || wait != 0 || th.fails[pairKey("y", "bob")].count != 0 {
		t.Fatalf("y: %v %v %v", ok, wait, th.fails[pairKey("y", "bob")])
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
		th.failed("x", "alice")
	}
	if w := th.wait("x", "alice"); w <= 3500*time.Second || w > time.Hour {
		t.Fatalf("wait %v", w)
	}
}

func TestQuietClientsAreForgotten(t *testing.T) {
	at := clock(t, time.Unix(1_000_000, 0))
	th := newThrottle()
	th.failed("old", "alice")
	*at = at.Add(25 * time.Hour)
	th.failed("new", "alice")
	if _, ok := th.fails[pairKey("old", "alice")]; ok {
		t.Fatal("a client quiet for a day is kept")
	}
	if _, ok := th.totals["old"]; ok {
		t.Fatal("a client's total is kept after it dropped to none")
	}
}

// A right try clears its own account's count and nothing else: signing in
// as oneself between guesses at someone else's password forgets none of
// them (it used to clear the client: unlimited guesses for any user).
func TestARightTryClearsOnlyItsAccount(t *testing.T) {
	clock(t, time.Unix(1_000_000, 0))
	th := newThrottle()
	guesses := 0
	for range 10 {
		for range 4 {
			if ok, wait := th.try("x", "alice", func() bool { return false }); !ok && wait == 0 {
				guesses++
			}
		}
		if ok, _ := th.try("x", "mallory", func() bool { return true }); !ok {
			t.Fatal("mallory's own password refused")
		}
	}
	if guesses != 5 {
		t.Fatalf("%d guesses at alice's password checked, want 5", guesses)
	}
	// alice herself, elsewhere, isn't held up by it.
	if wait := th.wait("y", "alice"); wait != 0 {
		t.Fatalf("another client waits %v for alice", wait)
	}
}

// Guesses sprayed over many names count together: past the client's total
// every try of it waits, whatever name it is for, and a right one doesn't
// clear the total.
func TestNamesSprayedCountTogether(t *testing.T) {
	at := clock(t, time.Unix(1_000_000, 0))
	th := newThrottle()
	for i := range 20 {
		if ok, wait := th.try("x", "user"+itoa(i), func() bool { return false }); ok || wait != 0 {
			t.Fatalf("try %d: %v %v", i, ok, wait)
		}
	}
	if ok, _ := th.try("x", "mallory", func() bool { return true }); ok {
		t.Fatal("a try past the client's total was checked")
	}
	if wait := th.wait("x", "someone-new"); wait != time.Minute {
		t.Fatalf("a new name waits %v, want a minute", wait)
	}
	if wait := th.wait("y", "someone-new"); wait != 0 {
		t.Fatalf("another client waits %v", wait)
	}
	*at = at.Add(time.Minute)
	if ok, _ := th.try("x", "mallory", func() bool { return true }); !ok {
		t.Fatal("refused after the wait")
	}
	if th.total("x", now()) < 19.9 {
		t.Fatalf("a right try lowered the total to %v", th.total("x", now()))
	}
	// The total drops by one an hour: a day later the client is free again.
	*at = at.Add(21 * time.Hour)
	if wait := th.wait("x", "someone-new"); wait != 0 {
		t.Fatalf("waits %v a day later", wait)
	}
}

// What docs/spec/security.md says a guesser gets in its first day, trying
// whenever it may: for one account, and over ever new names (or signing in
// as itself between guesses, which the account's count ignores).
func TestGuessesInADay(t *testing.T) {
	count := func(next func(i int) string) int {
		at := clock(t, time.Unix(1_000_000, 0))
		th := newThrottle()
		end, n := at.Add(24*time.Hour), 0
		for at.Before(end) {
			account := next(n)
			if wait := th.wait("x", account); wait > 0 {
				*at = at.Add(wait)
				continue
			}
			th.failed("x", account)
			n++
		}
		return n
	}
	one := count(func(int) string { return "alice" })
	many := count(func(i int) string { return "user" + itoa(i) })
	t.Logf("guesses in a day: %d for one account, %d over many", one, many)
	if one != 33 || many != 49 {
		t.Fatalf("one account %d (want 33), many names %d (want 49): update docs/spec/security.md, Throttling", one, many)
	}
}

// A redirect goes only to a path of Zonewiki's own (the login's next,
// Delete's): not to one a browser reads as another site's, after it drops
// tabs and line breaks from the address and reads a backslash as a slash.
func TestIsLocalPath(t *testing.T) {
	for _, u := range []string{"/", "/page/foo", "/page/a-b?x=1#h", "/oauth/authorize?client_id=x&state=%20"} {
		if !isLocalPath(u) {
			t.Errorf("%q isn't local", u)
		}
	}
	for _, u := range []string{"", "page/foo", "//evil.example", "/\\evil.example", "https://evil.example",
		"/\t/evil.example", "/\n/evil.example", "/\r/evil.example", "/ /evil.example", "/\u00a0/evil.example",
		"/x\\..\\evil", "/\x7f/evil.example", "/\x00"} {
		if isLocalPath(u) {
			t.Errorf("%q is local", u)
		}
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
		// An entry with a port is its address: the port would make each
		// connection a client of its own.
		{"127.0.0.1:1234", "203.0.113.9:4711", "203.0.113.9"},
		{"127.0.0.1:1234", "[2001:db8:1:2::9]:4711", "2001:db8:1:2::/64"},
		{"127.0.0.1:1234", "[2001:db8:1:2::9]", "2001:db8:1:2::/64"},
		{"127.0.0.1:1234", "::ffff:203.0.113.9", "203.0.113.9"},
		// One that is no address is the hop's that appended it, not the
		// next one to the left, which the client wrote.
		{"127.0.0.1:1234", "203.0.113.9, unknown", "127.0.0.1"},
		{"127.0.0.1:1234", "made-up-" + itoa(int(time.Now().UnixNano())), "127.0.0.1"},
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
		{"172.17.0.1:1234", "6.6.6.6, unknown, 192.168.1.5", "192.168.1.5"},
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

func TestSigningOut(t *testing.T) {
	testWiki(t, nil)
	sessionKey = []byte(strings.Repeat("k", 64))
	at := clock(t, time.Unix(1_000_000, 0))
	cookieOf := func(w *httptest.ResponseRecorder) string { return w.Result().Cookies()[0].Value }
	w := httptest.NewRecorder()
	setSession(w, httptest.NewRequest("GET", "/", nil), "")
	first := cookieOf(w)
	w = httptest.NewRecorder()
	setSession(w, httptest.NewRequest("GET", "/", nil), "")
	other := cookieOf(w)

	// Renewed, it is still the same session: signing out the renewed
	// cookie ends the old copy too, and the other session goes on.
	*at = at.Add(2 * 24 * time.Hour)
	w = httptest.NewRecorder()
	renewSession(w, withCookie(first))
	renewed := cookieOf(w)
	if renewed == first {
		t.Fatal("not renewed")
	}
	logout(httptest.NewRecorder(), withCookie(renewed))
	for _, c := range []string{first, renewed} {
		if _, ok, _ := signedIn(withCookie(c)); ok {
			t.Fatal("a signed-out session passes")
		}
	}
	if _, ok, _ := signedIn(withCookie(other)); !ok {
		t.Fatal("another session was signed out with it")
	}

	// Read again from the file (a restart).
	signedOut.until = nil
	if _, ok, _ := signedIn(withCookie(first)); ok {
		t.Fatal("forgotten with a restart")
	}

	// Kept until no cookie of the session can be valid, then dropped.
	*at = at.Add(sessionLifetime + time.Hour)
	logout(httptest.NewRecorder(), withCookie(other))
	if len(signedOut.until) != 1 {
		t.Fatalf("kept %d, want only the one just signed out", len(signedOut.until))
	}

	// A session without an id is none: it couldn't be signed out.
	raw, _ := json.Marshal(session{Auth: true, Issued: now().Unix(), Pw: passwordStamp("")})
	payload := base64.RawURLEncoding.EncodeToString(raw)
	if _, ok, _ := signedIn(withCookie(payload + "." + sign(payload))); ok {
		t.Fatal("a session without an id passes")
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
	if isDir(filepath.Join(wiki, "_assets")) {
		t.Fatal("loading the settings made _assets/: the start makes it, once the folder is checked")
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
	if _, _, err := loadConfig(env("ZONE_DATA", filepath.Join(wiki, "missing"))); err == nil || isDir(filepath.Join(wiki, "missing")) {
		t.Errorf("a missing ZONE_DATA: %v", err)
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

func TestVersionIsSemver(t *testing.T) {
	if !regexp.MustCompile(`^\d+\.\d+\.\d+(-dev)?$`).MatchString(version) {
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
	client := healthClient(nil)
	if health(client, srv.URL+"/ok") != 0 || health(client, srv.URL+"/missing") != 1 || health(client, "http://127.0.0.1:1/") != 1 {
		t.Error("health")
	}
}
