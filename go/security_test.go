package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"
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
	for _, c := range []struct{ remote, forwarded, want string }{
		{"8.8.8.8:1234", "10.0.0.1", "8.8.8.8"},          // a client on the internet can't pick its own key
		{"10.0.0.2:1234", "1.1.1.1, 9.9.9.9", "9.9.9.9"}, // a proxy: the last entry is the one it added
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
	t.Setenv("LEAF_SECRET_KEY", "")
	wiki := t.TempDir()
	key := secretKey(wiki)
	path := filepath.Join(wiki, ".leaf", "secret_key")
	b, _ := os.ReadFile(path)
	st, _ := os.Stat(path)
	if len(key) != 64 || string(b) != string(key) || st.Mode().Perm() != 0o600 {
		t.Fatalf("key %q, file %q, mode %v", key, b, st.Mode())
	}
	if string(secretKey(wiki)) != string(key) {
		t.Fatal("a restart doesn't keep it")
	}
	t.Setenv("LEAF_SECRET_KEY", "from-env")
	if string(secretKey(wiki)) != "from-env" {
		t.Fatal("LEAF_SECRET_KEY not used")
	}
}

func TestSecretKeyWithoutAWritableWiki(t *testing.T) {
	t.Setenv("LEAF_SECRET_KEY", "")
	wiki := t.TempDir()
	_ = os.WriteFile(filepath.Join(wiki, ".leaf"), []byte("not a folder"), 0o666)
	first, second := secretKey(wiki), secretKey(wiki)
	if len(first) != 64 || len(second) != 64 || string(first) == string(second) {
		t.Fatal("works, not kept")
	}
}

func TestSession(t *testing.T) {
	sessionKey = []byte(strings.Repeat("k", 64))
	at := clock(t, time.Unix(1_000_000, 0))
	w := httptest.NewRecorder()
	setSession(w, httptest.NewRequest("GET", "/", nil))
	cookie := w.Result().Cookies()[0]
	request := func(value string) *http.Request {
		r := httptest.NewRequest("GET", "/", nil)
		r.AddCookie(&http.Cookie{Name: sessionCookie, Value: value})
		return r
	}
	if ok, renew := signedIn(request(cookie.Value)); !ok || renew {
		t.Fatal("a fresh session isn't valid")
	}
	if ok, _ := signedIn(request(cookie.Value + "x")); ok {
		t.Fatal("a forged signature passes")
	}
	sessionKey = []byte(strings.Repeat("o", 64))
	if ok, _ := signedIn(request(cookie.Value)); ok {
		t.Fatal("another key's session passes")
	}
	sessionKey = []byte(strings.Repeat("k", 64))
	*at = at.Add(2 * 24 * time.Hour)
	if ok, renew := signedIn(request(cookie.Value)); !ok || !renew {
		t.Fatal("an older session isn't renewed")
	}
	*at = at.Add(30 * 24 * time.Hour)
	if ok, _ := signedIn(request(cookie.Value)); ok {
		t.Fatal("an expired session passes")
	}
}

func TestLoadConfig(t *testing.T) {
	wiki := t.TempDir()
	env := func(kv ...string) func(string) string {
		m := map[string]string{"LEAF_DATA": wiki}
		for i := 0; i < len(kv); i += 2 {
			m[kv[i]] = kv[i+1]
		}
		return func(k string) string { return m[k] }
	}
	c, err := loadConfig(env("LEAF_HOSTS", "Wiki.Example.org:443, ,leaf.home.arpa", "LEAF_ALLOW_REMOTE", "yes",
		"LEAF_NOSHARE", "#People,private, Personal/ ", "LEAF_BASE_URL", " http://x:5000/ "))
	if err != nil {
		t.Fatal(err)
	}
	if len(c.Hosts) != 2 || !c.Hosts["wiki.example.org"] || !c.AllowRemote || c.BaseURL != "http://x:5000" {
		t.Fatalf("%+v", c)
	}
	if !c.NoshareTags["people"] || !c.NoshareTags["private"] || len(c.NoshareFolder) != 1 || c.NoshareFolder[0] != "personal/" {
		t.Fatalf("noshare %v %v", c.NoshareTags, c.NoshareFolder)
	}
	if !isDir(filepath.Join(wiki, "_assets")) {
		t.Fatal("no _assets/")
	}
	if c, _ := loadConfig(env()); !c.NoshareTags["noshare"] {
		t.Fatal("LEAF_NOSHARE's default")
	}
	for _, bad := range [][]string{{"LEAF_LOCALE", "de_AT"}, {"LEAF_LOCALE", "german"}, {"LEAF_LOCALE", "de-AT;x"},
		{"LEAF_LOCALE", `"><script>`}, {"LEAF_DATE_FORMAT", "us"}, {"LEAF_DATE_FORMAT", "yyyy-mm-dd"},
		{"LEAF_DATA", ""}, {"LEAF_DATA", filepath.Join(wiki, "missing")}} {
		if _, err := loadConfig(env(bad...)); err == nil {
			t.Errorf("%v: no error", bad)
		}
	}
	if c, err := loadConfig(env("LEAF_LOCALE", " de-AT ", "LEAF_DATE_FORMAT", "ISO")); err != nil || c.Locale != "de-AT" || c.DateFormat != "iso" {
		t.Fatalf("%v %+v", err, c)
	}
	for _, tag := range []string{"en", "zh-Hant-TW", ""} {
		if c, err := loadConfig(env("LEAF_LOCALE", tag)); err != nil || c.Locale != tag {
			t.Errorf("LEAF_LOCALE=%s: %v", tag, err)
		}
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
	if uploadExt("image/jpeg") != ".jpg" || uploadExt("image/png") != ".png" || uploadExt("x/unknown") != "" {
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
