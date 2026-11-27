package main

import (
	"bytes"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"
	"zonewiki/internal/settingsfile"
)

func TestLoadSettingsFromFile(t *testing.T) {
	hash := hashPassword("pw")
	tok := hashToken("tok")
	load := func(text string) (*Settings, error) {
		t.Helper()
		f, err := parseConfig(text)
		if err != nil {
			t.Fatal(err)
		}
		return loadSettings(f)
	}
	s, err := load("locale = de-AT\ndate-format = ISO\nhosts = Wiki.Example.org:443, nas.local\nbase-url = http://x/\n" +
		"noshare = #People, personal/\nquery-log = yes\ngit-remote = r\nsemantic-min-score = 0.3\n[user alice]\n" +
		"password = " + hash + "\nmcp-token = " + tok + "\n[user bob]\npassword = " + hash + "\n")
	if err != nil {
		t.Fatal(err)
	}
	if s.Locale != "de-AT" || s.DateFormat != "iso" || !s.Hosts["wiki.example.org"] || !s.Hosts["nas.local"] ||
		s.BaseURL != "http://x" || !s.NoshareTags["people"] || s.NoshareFolder[0] != "personal/" || !s.QueryLog ||
		s.GitRemote != "r" || *s.SemanticMinScore != 0.3 || len(s.Users) != 2 || s.Users["alice"].MCPToken != tok ||
		!s.authOn() {
		t.Errorf("%+v", s)
	}
	// Not set: the defaults.
	if s, err := load(""); err != nil || !s.NoshareTags["noshare"] || s.authOn() || s.SemanticMinScore != nil ||
		s.Users != nil {
		t.Errorf("defaults: %v %+v", err, s)
	}
	// noshare empty: nothing hidden.
	if s, err := load("noshare =\n"); err != nil || len(s.NoshareTags)+len(s.NoshareFolder) != 0 {
		t.Errorf("empty noshare: %v %+v", err, s)
	}
	// The password is a hash, checked at sign-in (checkLogin).
	if s, err := load("password = " + hash + "\n"); err != nil || s.PasswordHash != hash || !s.authOn() {
		t.Errorf("password: %v %+v", err, s)
	}
	for text, want := range map[string]string{
		"password = " + hash + "\n[user a]\npassword = " + hash: "line 1: password is the wiki's, but there are users",
		"mcp-token = " + tok + "\n[user a]\npassword = " + hash: "line 1: mcp-token is the wiki's, but there are users",
		"mcp-token = tok": ".zone/config line 1: mcp-token = … isn't a token's hash",
		"[user a]\npassword = " + hash + "\nmcp-token = tok": "line 3: a's mcp-token isn't a token's hash",
		"mcp-token = sha256:" + strings.Repeat("A", 64):      "isn't a token's hash",
		"password = pw":                ".zone/config line 1: password = … isn't a password hash",
		"locale = de_AT":               ".zone/config line 1: locale = de_AT isn't a locale",
		"\ndate-format = us":           ".zone/config line 2: date-format = us isn't known",
		"query-log = sometimes":        ".zone/config line 1: query-log = sometimes isn't yes or no",
		"semantic-min-score = lots":    "line 1: semantic-min-score = lots isn't a similarity",
		"[user a]\nmcp-token = " + tok: "line 1: [user a] has no password",
		"[user a]\npassword = x":       "line 2: a's password isn't a password hash",
		"[user a]\npassword = " + hash + "\nmcp-token = " + tok + "\n[user b]\npassword = " + hash + "\nmcp-token = " + tok: "line 6: b's mcp-token is a's too",
	} {
		if _, err := load(text); err == nil || !strings.Contains(err.Error(), want) {
			t.Errorf("%q: %v, want %q", text, err, want)
		}
	}
	if _, err := load("password = pw"); err == nil || strings.Contains(err.Error(), "= pw") {
		t.Error("the error shows the password")
	}
}

// configWiki: a test wiki with this settings file, Zonewiki started on it.
func configWiki(t *testing.T, text string) string {
	t.Helper()
	testWiki(t, nil)
	path := configPath()
	if text != "" {
		if err := settingsfile.Write(path, text); err != nil {
			t.Fatal(err)
		}
	}
	f, err := readConfigFile(path)
	if err != nil {
		t.Fatal(err)
	}
	s, err := loadSettings(f)
	if err != nil {
		t.Fatal(err)
	}
	oldEvery := reloadEvery
	reloadEvery = 0
	t.Cleanup(func() { reloadEvery = oldEvery; settingsErr.Store(nil) })
	startSettings(s)
	return path
}

// rewrite gives the file new text, and a new mtime and size (the reload
// looks at both).
func rewrite(t *testing.T, path, text string) {
	t.Helper()
	if err := settingsfile.Write(path, text); err != nil {
		t.Fatal(err)
	}
}

func TestReloadSettings(t *testing.T) {
	path := configWiki(t, "locale = de\n")
	if settings().Locale != "de" {
		t.Fatal("start")
	}
	hash := hashPassword("pw")
	rewrite(t, path, "locale = en-GB\n[user alice]\npassword = "+hash+"\n")
	reloadSettings()
	if s := settings(); s.Locale != "en-GB" || s.Users["alice"] == nil || settingsError() != "" {
		t.Fatalf("reload: %+v %q", s, settingsError())
	}
	// A broken file keeps the settings from before, and says why.
	before := settings()
	rewrite(t, path, "locale = en-GB\n[user alice]\npasword = "+hash+"\n")
	reloadSettings()
	if settings() != before || !strings.Contains(settingsError(), "line 3") ||
		!strings.Contains(settingsError(), "keeps its settings") {
		t.Fatalf("broken: %q", settingsError())
	}
	// Unchanged since: not read again, the error stays.
	reloadSettings()
	if settingsError() == "" {
		t.Fatal("error forgotten")
	}
	rewrite(t, path, "locale = fr\n[user alice]\npassword = "+hash+"\n")
	reloadSettings()
	if s := settings(); s.Locale != "fr" || s.Users["alice"] == nil || settingsError() != "" {
		t.Fatalf("fixed: %+v %q", s, settingsError())
	}
	// No users and no password: no login, which only a restart takes.
	before = settings()
	rewrite(t, path, "locale = fr\n")
	reloadSettings()
	if settings() != before || !strings.Contains(settingsError(), "without a login") {
		t.Fatalf("login off: %q", settingsError())
	}
	rewrite(t, path, "password = "+hash+"\n")
	reloadSettings()
	if s := settings(); s.Users != nil || s.PasswordHash != hash || settingsError() != "" {
		t.Fatalf("users to a password: %+v %q", s, settingsError())
	}
	// Removed: kept (taking the users away could open the wiki), until it is back.
	before = settings()
	_ = os.Remove(path)
	reloadSettings()
	if settings() != before || !strings.Contains(settingsError(), "was removed") {
		t.Fatalf("removed: %q", settingsError())
	}
	rewrite(t, path, "locale = it\npassword = "+hash+"\n")
	reloadSettings()
	if settings().Locale != "it" || settingsError() != "" {
		t.Fatal("back")
	}
	// At most once per reloadEvery.
	reloadEvery = 1 << 62
	rewrite(t, path, "locale = es\n")
	reloadSettings()
	if settings().Locale != "it" {
		t.Error("looked again too soon")
	}
}

// A change takes effect between requests, so each sees one set of users
// from its session check to its answer: while one runs, the reload returns
// at once (nothing waits for it) and the change comes when the request
// ends, before the next one begins; a newer change replaces the one waiting; a request that doesn't
// end holds it up at most drainWait. The checks count every request.
func TestReloadBetweenRequests(t *testing.T) {
	path := configWiki(t, "locale = de\n")
	locale := func(want string) bool {
		t.Helper()
		for deadline := time.Now().Add(2 * time.Second); time.Now().Before(deadline); time.Sleep(time.Millisecond) {
			if settings().Locale == want {
				return true
			}
		}
		return false
	}
	beginRequest()
	rewrite(t, path, "locale = fr-CH\n")
	done := make(chan bool)
	go func() { reloadSettings(); close(done) }()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("the reload waited for the request")
	}
	time.Sleep(50 * time.Millisecond)
	if settings().Locale != "de" {
		t.Error("changed while a request ran")
	}
	rewrite(t, path, "locale = pt-BR\n")
	reloadSettings()
	endRequest()
	// At once, before the next request begins: else that one would hold
	// it up again (an admin's next page still saw them admin).
	beginRequest()
	if settings().Locale != "pt-BR" {
		t.Errorf("not taken when it ended: %s", settings().Locale)
	}
	endRequest()

	oldWait := drainWait
	drainWait = 50 * time.Millisecond
	t.Cleanup(func() { drainWait = oldWait })
	beginRequest()
	rewrite(t, path, "locale = it\n")
	reloadSettings()
	if !locale("it") {
		t.Error("a request that doesn't end held the reload up for good")
	}
	endRequest()

	running := -1
	h := checks(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		flightMu.Lock()
		running = inFlight
		flightMu.Unlock()
	}))
	req := httptest.NewRequest("GET", "/x", nil)
	req.Host = "localhost:5000"
	h.ServeHTTP(httptest.NewRecorder(), req)
	flightMu.Lock()
	after := inFlight
	flightMu.Unlock()
	if running != 1 || after != 0 {
		t.Errorf("counted %d while it ran, %d after", running, after)
	}
}

// A reload's commit waits for the other commits (commitMu), and the other
// requests don't wait for it: reloadMu is free while it runs.
func TestReloadDoesNotCommit(t *testing.T) {
	path := configWiki(t, "locale = de\n")
	cfg.GitEnabled = true
	commitMu.Lock() // a reload needs no git: the file isn't in it
	defer commitMu.Unlock()
	rewrite(t, path, "locale = fr\n")
	done := make(chan bool)
	go func() { reloadSettings(); close(done) }()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("the reload waited for git")
	}
	if settings().Locale != "fr" {
		t.Error("not taken")
	}
}

func TestReloadClearsNoshare(t *testing.T) {
	path := configWiki(t, "noshare = #secret\n")
	p := filepath.Join(cfg.Data, "a.md")
	_ = os.WriteFile(p, []byte("---\ntags: [diary]\n---\n# A\n"), 0o666)
	if isNoshare("a") {
		t.Fatal("hidden before")
	}
	rewrite(t, path, "noshare = #diary\n")
	reloadSettings()
	if !isNoshare("a") {
		t.Error("the noshare cache kept the old answer")
	}
}

func TestUserCommands(t *testing.T) {
	wiki := t.TempDir()
	env := map[string]string{"ZONE_DATA": wiki}
	getenv := func(k string) string { return env[k] }
	run := func(input string, args ...string) (int, string, string) {
		t.Helper()
		var out, errs bytes.Buffer
		code := userMain(args, getenv, strings.NewReader(input), &out, &errs)
		return code, out.String(), errs.String()
	}
	read := func() *settingsfile.File {
		t.Helper()
		f, err := readConfigFile(filepath.Join(wiki, ".zone", "config"))
		if err != nil || f == nil {
			t.Fatal(err)
		}
		return f
	}
	if code, out, _ := run("", "list"); code != 0 || !strings.Contains(out, "No users") {
		t.Errorf("list, none: %d %q", code, out)
	}
	code, out, errs := run("s3cret-pw\ns3cret-pw\n", "add", "alice")
	if code != 0 || !strings.Contains(out, "Added alice") || strings.Contains(out, "token") {
		t.Fatalf("add: %d %q %q", code, out, errs)
	}
	f := read()
	if !strings.HasPrefix(f.Lines[0], "# ") || f.Lines[1] != "" || f.Lines[2] != "[user alice]" || !checkPassword("s3cret-pw", f.User("alice").Values["password"].Value) {
		t.Fatalf("%q", f.Text())
	}
	if st, _ := os.Stat(filepath.Join(wiki, ".zone", "config")); st.Mode().Perm() != 0o600 {
		t.Errorf("mode %o", st.Mode().Perm())
	}
	code, out, _ = run("password\npassword\n", "add", "bob", "--mcp-token")
	token := f.User("bob")
	f = read()
	if token = f.User("bob"); code != 0 || token == nil ||
		token.Values["mcp-token"].Value != hashToken(lastWord(out)) || len(lastWord(out)) != 64 {
		t.Fatalf("add with a token: %d %q", code, out)
	}
	old := token.Values["mcp-token"].Value
	if code, out, _ = run("", "token", "bob"); code != 0 || read().User("bob").Values["mcp-token"].Value == old ||
		read().User("bob").Values["mcp-token"].Value != hashToken(lastWord(out)) {
		t.Errorf("token: %d %q", code, out)
	}
	if code, _, _ = run("new-password\nnew-password\n", "passwd", "alice"); code != 0 ||
		!checkPassword("new-password", read().User("alice").Values["password"].Value) {
		t.Error("passwd")
	}
	if code, out, _ = run("", "list"); code != 0 || out != "  alice (admin)\n  bob (MCP token)\n" {
		t.Errorf("list: %q", out)
	}
	_ = os.MkdirAll(filepath.Join(wiki, "private", "bob"), 0o777)
	if code, out, _ = run("", "remove", "bob"); code != 0 || read().User("bob") != nil ||
		!strings.Contains(out, "private/bob/") {
		t.Errorf("remove: %d %q", code, out)
	}
	// Noted removed: no stranger signs up by the name (userNameHasHistory).
	if b, err := os.ReadFile(filepath.Join(wiki, ".zone", "removed-users")); err != nil || string(b) != "[\n  \"bob\"\n]\n" {
		t.Errorf("removed-users: %q %v", b, err)
	}
	before := read().Text()
	for _, c := range []struct {
		input string
		args  []string
		want  string
	}{
		{"password\npassword\n", []string{"add", "alice"}, "alice is a user already"},
		{"", []string{"passwd", "carol"}, "there is no user carol"},
		{"", []string{"remove", "carol"}, "there is no user carol"},
		{"", []string{"add", "Bad/Name"}, "isn't a user name"},
		{"password\nwordpass\n", []string{"add", "carol"}, "The passwords differ"},
		{"", []string{"add"}, "Usage"},
		{"", []string{"passwd", "alice", "--mcp-token"}, "Usage"},
		{"", []string{"frobnicate", "x"}, "Usage"},
	} {
		if code, _, errs := run(c.input, c.args...); code == 0 || !strings.Contains(errs, c.want) {
			t.Errorf("%v: %d %q", c.args, code, errs)
		}
	}
	// A change Zonewiki wouldn't start with is refused, the file left as it was.
	path := filepath.Join(wiki, ".zone", "config")
	_ = settingsfile.Write(path, before+"[user dave]\n")
	if code, _, errs := run("password\npassword\n", "add", "carol"); code == 0 || !strings.Contains(errs, "not changed") {
		t.Errorf("with a broken section: %d %q", code, errs)
	}
	if got := readFile(t, path); got != before+"[user dave]\n" {
		t.Error("a refused command changed the file")
	}
	_ = settingsfile.Write(path, before)
	// The last user goes only with the wiki's password instead, or with no login.
	if code, _, errs := run("", "remove", "alice"); code == 0 || !strings.Contains(errs, "last user") ||
		read().User("alice") == nil {
		t.Errorf("remove the last: %d %q", code, errs)
	}
	if code, out, _ := run("wiki-password\nwiki-password\n", "remove", "alice", "--password"); code != 0 ||
		len(read().Users) != 0 || !checkPassword("wiki-password", read().Top["password"].Value) ||
		!strings.Contains(out, "password signs in") {
		t.Errorf("remove the last, with a password: %d %q", code, out)
	}
	// The first user replaces the wiki's password and token.
	f = read()
	f.Set("", "mcp-token", hashToken("t"))
	_ = settingsfile.Write(path, f.Text())
	if code, out, _ := run("password\npassword\n", "add", "carol"); code != 0 || read().Top["password"].Line != 0 ||
		read().Top["mcp-token"].Line != 0 || read().User("carol") == nil ||
		!strings.Contains(out, "password no longer works") || !strings.Contains(out, "MCP token no longer works") {
		t.Errorf("first user over the wiki's password: %d %q", code, out)
	}
	// One dash works too, as with the server's flags.
	if code, out, _ := run("", "remove", "carol", "-open"); code != 0 || len(read().Users) != 0 ||
		read().Top["password"].Line != 0 || !strings.Contains(out, "next start") {
		t.Errorf("remove the last, open: %d %q", code, out)
	}
	for _, args := range [][]string{{"remove", "x", "--password", "--open"}, {"add", "x", "--open"}} {
		if code, _, _ := run("", args...); code != 2 {
			t.Errorf("%v: %d", args, code)
		}
	}
}

// zonewiki user email sets, changes and takes out a user's address,
// refusing what isn't one and one another user has, the file unchanged.
func TestUserEmail(t *testing.T) {
	wiki := t.TempDir()
	env := map[string]string{"ZONE_DATA": wiki}
	getenv := func(k string) string { return env[k] }
	run := func(input string, args ...string) (int, string, string) {
		t.Helper()
		var out, errs bytes.Buffer
		code := userMain(args, getenv, strings.NewReader(input), &out, &errs)
		return code, out.String(), errs.String()
	}
	path := filepath.Join(wiki, ".zone", "config")
	email := func(name string) string {
		t.Helper()
		f, err := readConfigFile(path)
		if err != nil || f == nil || f.User(name) == nil {
			t.Fatal(err)
		}
		return f.User(name).Values["email"].Value
	}
	for _, name := range []string{"alice", "bob"} {
		if code, _, errs := run("password\npassword\n", "add", name); code != 0 {
			t.Fatalf("add %s: %q", name, errs)
		}
	}
	if code, out, _ := run("", "email", "bob"); code != 0 || !strings.Contains(out, "bob has no mail address") {
		t.Errorf("alone, none: %d %q", code, out)
	}
	if code, out, errs := run("", "email", "bob", "bob@example.org"); code != 0 || email("bob") != "bob@example.org" ||
		!strings.Contains(out, "bob's mail address is bob@example.org") {
		t.Fatalf("set: %d %q %q", code, out, errs)
	}
	if code, out, _ := run("", "email", "bob"); code != 0 || out != "bob@example.org\n" {
		t.Errorf("alone: %d %q", code, out)
	}
	if code, out, _ := run("", "list"); code != 0 || !strings.Contains(out, "bob@example.org") {
		t.Errorf("list: %d %q", code, out)
	}
	if code, _, errs := run("", "email", "bob", "Bob@Example.net"); code != 0 || email("bob") != "Bob@Example.net" {
		t.Errorf("change: %d %q", code, errs)
	}
	// His own address again is no clash.
	if code, _, errs := run("", "email", "bob", "bob@example.net"); code != 0 || email("bob") != "bob@example.net" {
		t.Errorf("own again: %d %q", code, errs)
	}
	before := readFile(t, path)
	for _, c := range []struct {
		args []string
		want string
	}{
		{[]string{"email", "alice", "BOB@example.net"}, "bob has the address BOB@example.net already"},
		{[]string{"email", "alice", "not an address"}, "isn't a mail address"},
		{[]string{"email", "alice", "Alice <alice@example.org>"}, "isn't a mail address"},
		{[]string{"email", "alice", "alice@localhost"}, "isn't a mail address"},
		{[]string{"email", "carol", "carol@example.org"}, "there is no user carol"},
		{[]string{"email", "carol", "--remove"}, "there is no user carol"},
		{[]string{"email"}, "Usage"},
		{[]string{"email", "alice", "a@example.org", "--remove"}, "Usage"},
		{[]string{"email", "alice", "a@example.org", "b@example.org"}, "Usage"},
		{[]string{"passwd", "alice", "--remove"}, "Usage"},
	} {
		if code, _, errs := run("", c.args...); code == 0 || !strings.Contains(errs, c.want) {
			t.Errorf("%v: %d %q", c.args, code, errs)
		}
	}
	if readFile(t, path) != before {
		t.Error("a refused command changed the file")
	}
	if code, out, _ := run("", "email", "bob", "--remove"); code != 0 || email("bob") != "" ||
		!strings.Contains(out, "Took out bob's mail address") || strings.Contains(readFile(t, path), "email") {
		t.Errorf("remove: %d %q", code, out)
	}
	if code, out, _ := run("", "email", "bob", "-remove"); code != 0 || !strings.Contains(out, "bob has no mail address") {
		t.Errorf("remove, none: %d %q", code, out)
	}
	// Free again, the address can go to another.
	if code, _, errs := run("", "email", "alice", "bob@example.net"); code != 0 || email("alice") != "bob@example.net" {
		t.Errorf("after remove: %d %q", code, errs)
	}
}

func TestUserRoles(t *testing.T) {
	wiki := t.TempDir()
	env := map[string]string{"ZONE_DATA": wiki}
	getenv := func(k string) string { return env[k] }
	run := func(input string, args ...string) (int, string, string) {
		t.Helper()
		var out, errs bytes.Buffer
		code := userMain(args, getenv, strings.NewReader(input), &out, &errs)
		return code, out.String(), errs.String()
	}
	path := filepath.Join(wiki, ".zone", "config")
	users := func() map[string]*User {
		t.Helper()
		return settingsOf(readFile(t, path)).Users
	}
	// The first user is an admin; the others editors unless told.
	if code, out, _ := run("password\npassword\n", "add", "alice"); code != 0 || !strings.Contains(out, "as an admin") ||
		users()["alice"].Role != roleAdmin || !strings.Contains(readFile(t, path), "role = admin") {
		t.Fatalf("first: %d %q", code, out)
	}
	if code, _, _ := run("password\npassword\n", "add", "bob"); code != 0 || users()["bob"].Role != roleEditor ||
		strings.Count(readFile(t, path), "role =") != 1 {
		t.Fatalf("second: %d", code)
	}
	if code, _, errs := run("password\npassword\n", "add", "carol", "--role", "viewer"); code != 0 || users()["carol"].Role != roleViewer {
		t.Fatalf("--role: %d %q", code, errs)
	}
	if code, _, errs := run("password\npassword\n", "add", "dave", "--role", "boss"); code == 0 || !strings.Contains(errs, "isn't a role") {
		t.Errorf("a bad role: %d %q", code, errs)
	}
	// role: editor is the default, so its line goes.
	if code, out, _ := run("", "role", "carol", "editor"); code != 0 || users()["carol"].Role != roleEditor ||
		!strings.Contains(out, "carol is an editor now") {
		t.Errorf("role editor: %d %q", code, out)
	}
	if code, _, _ := run("", "role", "carol", "viewer"); code != 0 || users()["carol"].Role != roleViewer {
		t.Error("role viewer")
	}
	// disable and enable.
	if code, out, _ := run("", "disable", "bob"); code != 0 || !users()["bob"].Disabled || !strings.Contains(out, "enable bob") {
		t.Errorf("disable: %d %q", code, out)
	}
	if code, out, _ := run("", "list"); code != 0 || out != "  alice (admin)\n  bob (disabled)\n  carol (viewer)\n" {
		t.Errorf("list: %q", out)
	}
	if code, _, _ := run("", "enable", "bob"); code != 0 || users()["bob"].Disabled || strings.Contains(readFile(t, path), "disabled") {
		t.Error("enable")
	}
	// shared: an account several people sign in to; no admin is one.
	if code, out, _ := run("", "shared", "bob"); code != 0 || !users()["bob"].Shared || !strings.Contains(out, "shared bob --remove") ||
		!strings.Contains(readFile(t, path), "shared = yes") {
		t.Errorf("shared: %d %q", code, out)
	}
	if code, out, _ := run("", "list"); code != 0 || out != "  alice (admin)\n  bob (shared)\n  carol (viewer)\n" {
		t.Errorf("list, shared: %q", out)
	}
	if code, _, errs := run("", "role", "bob", "admin"); code == 0 || !strings.Contains(errs, "bob is shared and an admin") ||
		users()["bob"].Role != roleEditor {
		t.Errorf("a shared admin: %d %q", code, errs)
	}
	if code, _, errs := run("", "shared", "alice"); code == 0 || !strings.Contains(errs, "alice is shared and an admin") ||
		users()["alice"].Shared {
		t.Errorf("an admin shared: %d %q", code, errs)
	}
	if code, out, _ := run("", "shared", "bob", "--remove"); code != 0 || users()["bob"].Shared ||
		strings.Contains(readFile(t, path), "shared") || !strings.Contains(out, "one person's account again") {
		t.Errorf("shared --remove: %d %q", code, out)
	}
	// Groups: who belongs together, said on the users.
	if code, out, _ := run("", "group", "bob", "+Hiring", "+dev"); code != 0 || out != "bob is in: dev, hiring.\n" ||
		!slices.Equal(users()["bob"].Groups, []string{"dev", "hiring"}) || !strings.Contains(readFile(t, path), "groups = dev, hiring") {
		t.Errorf("group +: %d %q", code, out)
	}
	if code, out, _ := run("", "group", "bob"); code != 0 || out != "bob is in: dev, hiring.\n" {
		t.Errorf("group alone: %q", out)
	}
	if code, out, _ := run("", "list"); code != 0 || !strings.Contains(out, "  bob (in dev, hiring)\n") {
		t.Errorf("list with groups: %q", out)
	}
	if s := settingsOf(readFile(t, path)); !s.memberOf("bob", "dev") || s.memberOf("carol", "dev") || s.memberOf("bob", "ops") ||
		!slices.Equal(s.groups(), []string{"dev", "hiring"}) {
		t.Error("memberOf")
	}
	for _, bad := range [][]string{{"group", "bob", "dev"}, {"group", "bob", "+Dev Ops"}, {"group", "bob", "+"}, {"group", "nobody", "+dev"}, {"group"}} {
		if code, _, _ := run("", bad...); code == 0 {
			t.Errorf("%v went through", bad)
		}
	}
	if code, out, _ := run("", "group", "bob", "-dev", "-hiring"); code != 0 || out != "bob is in no group.\n" ||
		strings.Contains(readFile(t, path), "groups") {
		t.Errorf("group -: %d %q", code, out)
	}
	// A disabled user is in no group for what a group is for, and a name that is none is an error of the file.
	if s := settingsOf("[user x]\npassword = " + hashPassword("p") + "\ngroups = @dev ops\ndisabled = yes\n"); s == nil ||
		!slices.Equal(s.Users["x"].Groups, []string{"dev", "ops"}) || s.memberOf("x", "dev") {
		t.Error("groups of a disabled user")
	}
	badGroups, _ := parseConfig("[user x]\npassword = " + hashPassword("p") + "\ngroups = dev, Not_A_Group\n")
	if _, err := loadSettings(badGroups); err == nil || !strings.Contains(err.Error(), "Not_A_Group") {
		t.Errorf("a bad group's name: %v", err)
	}
	// Never back to no admin: the last one can't be demoted, disabled or removed.
	before := readFile(t, path)
	for _, args := range [][]string{{"role", "alice", "editor"}, {"disable", "alice"}, {"remove", "alice"}} {
		if code, _, errs := run("", args...); code == 0 || !strings.Contains(errs, "without an admin") {
			t.Errorf("%v: %d %q", args, code, errs)
		}
	}
	if readFile(t, path) != before {
		t.Error("a refused change changed the file")
	}
	// With another admin, it can.
	if code, _, _ := run("", "role", "bob", "admin"); code != 0 {
		t.Fatal("bob admin")
	}
	if code, _, errs := run("", "role", "alice", "viewer"); code != 0 {
		t.Errorf("demote with another admin: %q", errs)
	}
	// A wiki from before roles has no admin, and changes go through.
	_ = settingsfile.Write(path, "[user x]\npassword = "+hashPassword("p")+"\n\n[user y]\npassword = "+hashPassword("p")+"\n")
	if code, _, errs := run("", "remove", "y"); code != 0 {
		t.Errorf("no admin before: %q", errs)
	}
	// Usage.
	for _, args := range [][]string{{"role", "x"}, {"add", "z", "--role"}, {"passwd", "x", "--role", "admin"}, {"disable"}} {
		if code, _, _ := run("", args...); code != 2 {
			t.Errorf("%v: %d", args, code)
		}
	}
}

// A group made of groups (docs/spec/users.md, Groups): [group product]
// includes = dev, design; who is in dev or in design is in product, and so
// on through what those include.
func TestGroupsOfGroups(t *testing.T) {
	user := func(name, more string) string {
		return "[user " + name + "]\npassword = " + hashPassword("p") + "\n" + more
	}
	text := user("alice", "groups = dev\nrole = admin\n") + user("bob", "groups = design\n") + user("carol", "groups = sales, product\n") +
		user("dan", "groups = dev\ndisabled = yes\n") + user("eve", "") +
		"[group product]\nincludes = Dev, @design\n[group company]\nincludes = product, nobody-yet\n"
	f, err := parseConfig(text)
	if err != nil {
		t.Fatal(err)
	}
	s, err := loadSettings(f)
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(s.Includes["product"], []string{"design", "dev"}) || !slices.Equal(s.Users["alice"].Groups, []string{"dev"}) ||
		!slices.Equal(s.Users["alice"].In, []string{"company", "dev", "product"}) ||
		!slices.Equal(s.Users["carol"].In, []string{"company", "product", "sales"}) || len(s.Users["eve"].In) != 0 {
		t.Errorf("includes %v, alice in %v, carol in %v", s.Includes, s.Users["alice"].In, s.Users["carol"].In)
	}
	for _, c := range []struct {
		user, group string
		want        bool
	}{
		{"alice", "dev", true}, {"alice", "product", true}, {"alice", "company", true}, {"alice", "design", false},
		{"bob", "product", true}, {"bob", "company", true}, {"bob", "dev", false},
		{"carol", "product", true}, {"carol", "dev", false}, // named on her: not in what it is made of
		{"dan", "dev", false}, {"dan", "product", false}, // disabled: in none
		{"eve", "product", false}, {"eve", "company", false},
		{"alice", "nobody-yet", false}, // an admin is in no group by being admin, and nobody is in this one
	} {
		if got := s.memberOf(c.user, c.group); got != c.want {
			t.Errorf("memberOf(%s, %s) = %v", c.user, c.group, got)
		}
	}
	if got := s.groups(); !slices.Equal(got, []string{"company", "design", "dev", "product", "sales"}) {
		t.Errorf("groups %v", got)
	}
	if got := s.groupMembers("product"); !slices.Equal(got, []string{"alice", "bob", "carol"}) {
		t.Errorf("members of product %v", got)
	}
	if got := s.groupMembers("nobody-yet"); got != nil {
		t.Errorf("members of an empty group %v", got)
	}
	// The log says what changed.
	old, _ := loadSettings(mustParse(t, user("alice", "groups = dev\n")+"[group product]\nincludes = dev\n[group old]\nincludes = dev\n"))
	if got := settingsChange(old, s); !strings.Contains(got, "company includes nobody-yet, product") ||
		!strings.Contains(got, "product includes design, dev") || !strings.Contains(got, "old includes no group") {
		t.Errorf("the change's line: %s", got)
	}
	// What the file can't say: a group that includes itself, by whatever way
	// round; a name that is no group's; a key a group doesn't have.
	for more, want := range map[string]string{
		"[group a]\nincludes = a\n":                                                      "line 4: [group a] includes itself.",
		"[group a]\nincludes = b\n[group b]\nincludes = a\n":                             "line 4: [group a] includes itself, by way of b",
		"[group a]\nincludes = x, b\n[group b]\nincludes = c\n[group c]\nincludes = a\n": "line 4: [group a] includes itself, by way of b, c",
		"[group a]\nincludes = dev, Not_One\n":                                           "line 4: [group a] includes Not_One, which isn't a group's name",
		"[group a]\nmembers = alice\n":                                                   "line 4: \"members\" isn't a group's setting",
		"[group A B]\n":                                                                  "line 3: \"A B\" isn't a group's name",
	} {
		f, err := parseConfig(user("alice", "") + more)
		if err == nil {
			_, err = loadSettings(f)
		}
		if err == nil || !strings.Contains(err.Error(), want) {
			t.Errorf("%q: %v, want %q", more, err, want)
		}
	}
	// A group with a section and nothing in it is a group nobody is in.
	if s := settingsOf(user("alice", "") + "[group a]\n"); s.Users == nil || s.memberOf("alice", "a") || !slices.Equal(s.groups(), []string{"a"}) {
		t.Error("an empty section")
	}
}

func mustParse(t *testing.T, text string) *settingsfile.File {
	t.Helper()
	f, err := parseConfig(text)
	if err != nil {
		t.Fatal(err)
	}
	return f
}

// zonewiki group includes NAME +dev -design, zonewiki group list.
func TestGroupCommand(t *testing.T) {
	wiki := t.TempDir()
	env := map[string]string{"ZONE_DATA": wiki}
	getenv := func(k string) string { return env[k] }
	path := filepath.Join(wiki, ".zone", "config")
	run := func(args ...string) (int, string, string) {
		t.Helper()
		var out, errs bytes.Buffer
		code := groupMain(args, getenv, &out, &errs)
		return code, out.String(), errs.String()
	}
	user := func(input string, args ...string) {
		t.Helper()
		var out, errs bytes.Buffer
		if code := userMain(args, getenv, strings.NewReader(input), &out, &errs); code != 0 {
			t.Fatalf("user %v: %s", args, errs.String())
		}
	}
	if code, out, _ := run("list"); code != 0 || !strings.Contains(out, "No groups") {
		t.Errorf("list without groups: %d %q", code, out)
	}
	user("password\npassword\n", "add", "alice")
	user("password\npassword\n", "add", "bob")
	user("password\npassword\n", "add", "carol")
	user("", "group", "alice", "+dev")
	user("", "group", "bob", "+design")
	if code, out, errs := run("includes", "Product", "+dev", "+@design"); code != 0 || out != "product includes design, dev: alice, bob.\n" ||
		!strings.Contains(readFile(t, path), "\n[group product]\nincludes = design, dev\n") {
		t.Errorf("includes +: %d %q %q\n%s", code, out, errs, readFile(t, path))
	}
	if st, err := os.Stat(path); err != nil || st.Mode().Perm() != 0o600 {
		t.Errorf("the file's mode: %v %v", st, err)
	}
	if code, out, _ := run("includes", "product"); code != 0 || out != "product includes design, dev: alice, bob.\n" {
		t.Errorf("includes alone: %q", out)
	}
	// Owners: who says who is in it, in the browser.
	if code, out, errs := run("owners", "dev", "+alice", "+bob", "-bob"); code != 0 || !strings.HasPrefix(out, "dev's owners: alice.") ||
		!strings.Contains(readFile(t, path), "\n[group dev]\nowners = alice\n") {
		t.Errorf("owners +: %d %q %q\n%s", code, out, errs, readFile(t, path))
	}
	if code, out, _ := run("list"); code != 0 || !strings.Contains(out, "  dev (owners: alice): alice\n") {
		t.Errorf("list with owners: %q", out)
	}
	if code, _, errs := run("owners", "dev", "+nobody"); code != 1 || !strings.Contains(errs, "no user") {
		t.Errorf("an owner who is no user: %d %q", code, errs)
	}
	if code, out, _ := run("owners", "dev", "-alice"); code != 0 || !strings.Contains(out, "no owner") || strings.Contains(readFile(t, path), "[group dev]") {
		t.Errorf("owners -: %q\n%s", out, readFile(t, path))
	}
	if code, out, _ := run("includes", "dev"); code != 0 || out != "dev: alice.\n" {
		t.Errorf("a plain group: %q", out)
	}
	if code, out, _ := run("includes", "nothing"); code != 0 || out != "nothing: nobody is in it.\n" {
		t.Errorf("no group: %q", out)
	}
	if code, out, _ := run("list"); code != 0 || out != "  design: bob\n  dev: alice\n  product includes design, dev: alice, bob\n" {
		t.Errorf("list: %q", out)
	}
	var out, errs bytes.Buffer
	if userMain([]string{"list"}, getenv, strings.NewReader(""), &out, &errs); !strings.Contains(out.String(), "  bob (in design, product)\n") {
		t.Errorf("user list: %q", out.String())
	}
	// A group that would include itself is refused, and the file stays.
	before := readFile(t, path)
	if code, _, errs := run("includes", "dev", "+product"); code != 1 || !strings.Contains(errs, "not changed") ||
		!strings.Contains(errs, "includes itself") || readFile(t, path) != before {
		t.Errorf("a round: %d %q", code, errs)
	}
	for _, bad := range [][]string{{"includes"}, {"includes", "+dev"}, {"includes", "product", "dev"}, {"includes", "product", "+Dev Ops"},
		{"includes", "Pro duct", "+dev"}, {"list", "x"}, {"rename", "a", "b"}, {}} {
		if code, _, _ := run(bad...); code == 0 || readFile(t, path) != before {
			t.Errorf("%v went through", bad)
		}
	}
	if code, out, _ := run("--help"); code != 0 || !strings.Contains(out, "includes NAME") {
		t.Errorf("help: %d %q", code, out)
	}
	// Taking a group out says who is no longer in the larger one; the last
	// one out takes the section with it.
	if code, out, _ := run("includes", "product", "-design"); code != 0 ||
		out != "product includes dev: alice.\nNo longer in product: bob. What is for @product they no longer see.\n" {
		t.Errorf("includes -: %q", out)
	}
	if code, out, _ := run("includes", "product", "-dev"); code != 0 || !strings.HasPrefix(out, "product: nobody is in it.\nNo longer in product: alice.") ||
		strings.Contains(readFile(t, path), "group") && strings.Contains(readFile(t, path), "[group") {
		t.Errorf("the last one out: %q\n%s", out, readFile(t, path))
	}
}

func TestUserLinks(t *testing.T) {
	wiki := t.TempDir()
	env := map[string]string{"ZONE_DATA": wiki}
	getenv := func(k string) string { return env[k] }
	run := func(input string, args ...string) (int, string, string) {
		t.Helper()
		var out, errs bytes.Buffer
		code := userMain(args, getenv, strings.NewReader(input), &out, &errs)
		return code, out.String(), errs.String()
	}
	if code, _, errs := run("", "invite", "dave"); code == 0 || !strings.Contains(errs, "no users") {
		t.Errorf("invite without users: %d %q", code, errs)
	}
	run("password\npassword\n", "add", "alice")
	code, out, errs := run("", "invite", "dave", "--role", "viewer")
	if code != 0 || !strings.Contains(out, "as a viewer") || !strings.Contains(out, "/welcome/") ||
		!strings.Contains(out, "base-url") {
		t.Fatalf("invite: %d %q %q", code, out, errs)
	}
	token := strings.TrimPrefix(strings.Fields(strings.Split(out, "\n")[1])[0], "/welcome/")
	links, err := readLinks()
	if err != nil || len(links) != 1 || links[0].User != "dave" || links[0].Role != roleViewer ||
		links[0].Hash != hashToken(token) || links[0].Kind != "invite" {
		t.Fatalf("links: %+v %v", links, err)
	}
	if st, _ := os.Stat(filepath.Join(wiki, ".zone", "links")); st.Mode().Perm() != 0o600 {
		t.Errorf("mode %o", st.Mode().Perm())
	}
	// The name is taken while the invite is open.
	if code, _, errs := run("password\npassword\n", "add", "dave"); code == 0 || !strings.Contains(errs, "open invite") {
		t.Errorf("add over an invite: %d %q", code, errs)
	}
	for _, c := range []struct {
		args []string
		want string
	}{
		{[]string{"invite", "alice"}, "is a user already"},
		{[]string{"reset", "zed"}, "there is no user zed"},
		{[]string{"invite", "x", "--role", "boss"}, "isn't a role"},
	} {
		if code, _, errs := run("", c.args...); code == 0 || !strings.Contains(errs, c.want) {
			t.Errorf("%v: %d %q", c.args, code, errs)
		}
	}
	// A reset, with base-url: the whole address; it replaces nothing of dave's.
	f, _ := readConfigFile(filepath.Join(wiki, ".zone", "config"))
	f.Set("", "base-url", "https://wiki.example.org/")
	_ = settingsfile.Write(filepath.Join(wiki, ".zone", "config"), f.Text())
	if code, out, _ := run("", "reset", "alice"); code != 0 || !strings.Contains(out, "https://wiki.example.org/welcome/") ||
		strings.Contains(out, "base-url") {
		t.Errorf("reset: %d %q", code, out)
	}
	if links, _ := readLinks(); len(links) != 2 {
		t.Errorf("two links: %+v", links)
	}
	// A removed user's open link goes with them: whoever is added by that
	// name later doesn't get in by it.
	if code, _, errs := run("password\npassword\n", "add", "bob"); code != 0 {
		t.Fatalf("add bob: %q", errs)
	}
	if code, _, errs := run("", "reset", "bob"); code != 0 {
		t.Fatalf("reset bob: %q", errs)
	}
	if code, _, errs := run("", "remove", "bob"); code != 0 {
		t.Fatalf("remove bob: %q", errs)
	}
	left, _ := readLinks()
	for _, l := range left {
		if l.User == "bob" {
			t.Errorf("bob's link is still there after he was removed: %+v", left)
		}
	}
	if len(left) != 2 {
		t.Errorf("the others' links stay: %+v", left)
	}
}

// notice: a line every page shows; one line, trimmed, at most noticeMax
// characters (not bytes), and a change of it named so.
func TestTheNotice(t *testing.T) {
	s, err := loadSettings(mustParse(t, "notice =   Put back at every full hour  \n"))
	if err != nil || s.Notice != "Put back at every full hour" {
		t.Fatalf("%q, %v", s.Notice, err)
	}
	none, _ := loadSettings(mustParse(t, ""))
	if none.Notice != "" {
		t.Errorf("none: %q", none.Notice)
	}
	if got := settingsChange(none, s); got != "Change settings: notice" {
		t.Errorf("change: %q", got)
	}
	long := strings.Repeat("ä", noticeMax)
	if s, err := loadSettings(mustParse(t, "notice = "+long+"\n")); err != nil || s.Notice != long {
		t.Errorf("%d characters, more bytes: %v", noticeMax, err)
	}
	_, err = loadSettings(mustParse(t, "\nnotice = "+long+"x\n"))
	if err == nil || !strings.Contains(err.Error(), "line 2: notice = … is 201 characters: a notice is one line of at most 200") {
		t.Errorf("too long: %v", err)
	}
}

// imprint, privacy: an http(s) address or a path in the wiki, trimmed;
// anything else is an error that names the line, and a change is named.
func TestTheLegalLinks(t *testing.T) {
	s, err := loadSettings(mustParse(t, "imprint =  https://example.org/imprint.html \nprivacy = /page/privacy\n"))
	if err != nil || s.Imprint != "https://example.org/imprint.html" || s.Privacy != "/page/privacy" {
		t.Fatalf("%q, %q, %v", s.Imprint, s.Privacy, err)
	}
	none, _ := loadSettings(mustParse(t, ""))
	if none.Imprint != "" || none.Privacy != "" {
		t.Errorf("none: %q, %q", none.Imprint, none.Privacy)
	}
	if got := settingsChange(none, s); got != "Change settings: imprint, privacy" {
		t.Errorf("change: %q", got)
	}
	for _, bad := range []string{"javascript:alert(1)", "//example.org/imprint", "example.org/imprint",
		"mailto:a@example.org", "https://", "https://user:pw@example.org/", "https://example.org/a b",
		"/page/a\\b", "https://example.org/" + strings.Repeat("a", 500)} {
		for _, key := range []string{"imprint", "privacy"} {
			_, err := loadSettings(mustParse(t, "\n"+key+" = "+bad+"\n"))
			if err == nil || !strings.Contains(err.Error(), "line 2: "+key+" = ") || !strings.Contains(err.Error(), "isn't a link") {
				t.Errorf("%s = %s: %v", key, bad, err)
			}
		}
	}
	for _, key := range []string{"imprint", "privacy"} {
		if !slices.Contains(hostKeys, key) {
			t.Errorf("%s is the host's: an import leaves it out", key)
		}
	}
}

func TestRolesInTheFile(t *testing.T) {
	hash := hashPassword("p")
	f, err := parseConfig("[user a]\npassword = " + hash + "\nrole = Admin\n\n[user b]\npassword = " + hash +
		"\ndisabled = yes\n\n[user c]\npassword = " + hash + "\nrole = viewer\n")
	if err != nil {
		t.Fatal(err)
	}
	s, err := loadSettings(f)
	if err != nil {
		t.Fatal(err)
	}
	if !s.isAdmin("a") || s.isAdmin("b") || s.Users["b"].Role != roleEditor || !s.Users["b"].Disabled ||
		!s.isViewer("c") || s.isViewer("a") || !s.hasAdmin() {
		t.Errorf("%+v %+v %+v", s.Users["a"], s.Users["b"], s.Users["c"])
	}
	for text, want := range map[string]string{
		"[user a]\npassword = " + hash + "\nrole = boss\n":                                                                   "line 3: a's role boss isn't one",
		"[user a]\npassword = " + hash + "\ndisabled = maybe\n":                                                              "line 3: a's disabled = maybe isn't yes or no",
		"[user a]\npassword = " + hash + "\nshared = maybe\n":                                                                "line 3: a's shared = maybe isn't yes or no",
		"[user a]\npassword = " + hash + "\nrole = admin\n\n[user d]\npassword = " + hash + "\nrole = admin\nshared = yes\n": "line 8: d is shared and an admin",
		"role = admin\n": `"role" isn't a setting Zonewiki knows`,
	} {
		f, err := parseConfig(text)
		if err == nil {
			_, err = loadSettings(f)
		}
		if err == nil || !strings.Contains(err.Error(), want) {
			t.Errorf("%q: %v", text, err)
		}
	}
	// A disabled admin is no admin: keepsAdmin counts the enabled ones.
	old, _ := loadSettings(mustParse(t, "[user a]\npassword = "+hash+"\nrole = admin\n"))
	now, _ := loadSettings(mustParse(t, "[user a]\npassword = "+hash+"\nrole = admin\ndisabled = yes\n"))
	if keepsAdmin(old, now) == nil || keepsAdmin(old, &Settings{}) != nil {
		t.Error("keepsAdmin")
	}
	if got := settingsChange(old, now); got != "Change settings: disable a" {
		t.Errorf("change: %q", got)
	}
	// A shared account: said in the file, and in what a change is called.
	one, _ := loadSettings(mustParse(t, "[user a]\npassword = "+hash+"\nrole = admin\n\n[user d]\npassword = "+hash+"\n"))
	many, _ := loadSettings(mustParse(t, "[user a]\npassword = "+hash+"\nrole = admin\n\n[user d]\npassword = "+hash+"\nshared = Yes\n"))
	if one.sharedAccount("d") || !many.sharedAccount("d") || many.sharedAccount("a") || many.sharedAccount("nobody") {
		t.Error("sharedAccount")
	}
	if got := settingsChange(one, many); got != "Change settings: d is shared" {
		t.Errorf("change to shared: %q", got)
	}
	if got := settingsChange(many, one); got != "Change settings: d is no longer shared" {
		t.Errorf("change from shared: %q", got)
	}
}

func TestConfigCommands(t *testing.T) {
	wiki := t.TempDir()
	hash := hashPassword("pw")
	env := map[string]string{"ZONE_DATA": wiki, "ZONE_ALLOW_REMOTE": "1"}
	getenv := func(k string) string { return env[k] }
	run := func(input string, args ...string) (int, string, string) {
		var out, errs bytes.Buffer
		code := configMain(args, getenv, strings.NewReader(input), &out, &errs)
		return code, out.String(), errs.String()
	}
	_ = os.Mkdir(filepath.Join(wiki, ".zone"), 0o777)
	_ = settingsfile.Write(filepath.Join(wiki, ".zone", "config"), "locale = de-AT\nhosts = nas.local\n\n[user alice]\npassword = "+hash+
		"\nmcp-token = "+hashToken("s3cret-tok")+"\n\n[user mary-ann]\npassword = "+hash+"\n")
	code, out, _ := run("", "check")
	if code != 0 || !strings.Contains(out, "locale             de-AT  [line ") ||
		!strings.Contains(out, "users              alice (MCP token), mary-ann") ||
		!strings.Contains(out, "noshare            #noshare  [default]") || strings.Contains(out, "sha256") {
		t.Errorf("check:\n%s", out)
	}
	if code, _, errs := run("password\npassword\n", "passwd"); code == 0 || !strings.Contains(errs, "zonewiki user passwd NAME") {
		t.Errorf("passwd with users: %q", errs)
	}
	// Without users: the wiki's password and token.
	wiki2 := t.TempDir()
	env = map[string]string{"ZONE_DATA": wiki2}
	if code, out, errs := run("new-password\nnew-password\n", "passwd"); code != 0 || !strings.Contains(out, "Changed the wiki's password") {
		t.Fatal(errs)
	}
	code, out, _ = run("", "token")
	f, _ := readConfigFile(filepath.Join(wiki2, ".zone", "config"))
	if code != 0 || !checkPassword("new-password", f.Top["password"].Value) || f.Top["mcp-token"].Value != hashToken(lastWord(out)) {
		t.Errorf("passwd, token: %q %s", out, f.Text())
	}
	if code, _, errs := run("", "set", "hosts", "nas.local,", "wiki.example.org"); code != 0 {
		t.Fatal(errs)
	}
	if code, _, errs := run("", "set", "locale", "de-AT"); code != 0 {
		t.Fatal(errs)
	}
	f, _ = readConfigFile(filepath.Join(wiki2, ".zone", "config"))
	if f.Top["hosts"].Value != "nas.local, wiki.example.org" || f.Top["locale"].Value != "de-AT" {
		t.Errorf("set: %s", f.Text())
	}
	if code, _, errs := run("", "unset", "locale"); code != 0 {
		t.Fatal(errs)
	}
	f, _ = readConfigFile(filepath.Join(wiki2, ".zone", "config"))
	if _, ok := f.Top["locale"]; ok || f.Top["hosts"].Value == "" {
		t.Errorf("unset: %s", f.Text())
	}
	for _, c := range [][]string{{"set", "locale", "de_AT"}, {"set", "pasword", "x"}, {"set", "password", "x"},
		{"set", "mcp-token", "x"}, {"set", "base-url", "http://x\n[user eve]\npassword = x"}} {
		if code, _, errs := run("", c...); code != 1 || errs == "" {
			t.Errorf("%v: %d", c, code)
		}
	}
	// The password and token are unset like the others (no login, /mcp off).
	f.Set("", "password", hashPassword("pw"))
	f.Set("", "mcp-token", hashToken("t"))
	_ = settingsfile.Write(filepath.Join(wiki2, ".zone", "config"), f.Text())
	code, out, errs := run("", "unset", "password")
	if code2, _, _ := run("", "unset", "mcp-token"); code != 0 || code2 != 0 || !strings.Contains(out, "no login") {
		t.Errorf("unset password, token: %d %d %q", code, code2, errs)
	}
	if f, _ = readConfigFile(filepath.Join(wiki2, ".zone", "config")); f.Top["password"].Line != 0 ||
		f.Top["mcp-token"].Line != 0 {
		t.Errorf("unset password, token: %s", f.Text())
	}
	for _, c := range [][]string{{"bogus"}, {}, {"set", "locale"}, {"unset"}, {"check", "x"}} {
		if code, _, errs := run("", c...); code != 2 || !strings.Contains(errs, "Usage") {
			t.Errorf("usage %v", c)
		}
	}
	// A command writes only onto the file it read.
	env["ZONE_DATA"] = wiki2
	f, err := openConfig(getenv)
	if err != nil {
		t.Fatal(err)
	}
	f.Set("", "locale", "fr")
	rewrite(t, filepath.Join(wiki2, ".zone", "config"), "locale = it\n")
	if err := saveConfig(f); err == nil || !strings.Contains(err.Error(), "changed while this command ran") {
		t.Errorf("changed meanwhile: %v", err)
	}
	if f, _ = readConfigFile(filepath.Join(wiki2, ".zone", "config")); f.Top["locale"].Value != "it" {
		t.Errorf("overwritten: %s", f.Text())
	}
	_ = os.WriteFile(filepath.Join(wiki2, ".zone", "config"), []byte("x\n"), 0o600)
	if code, _, errs := run("", "check"); code != 1 || !strings.Contains(errs, "line 1") {
		t.Errorf("broken: %q", errs)
	}
}

// zonewiki health fails while the settings file's last change wasn't taken.
func TestHealthSettingsError(t *testing.T) {
	path := configWiki(t, "locale = de\n")
	cfg.AllowRemote = true // the test server's Host
	srv := httptest.NewServer(newHandler())
	defer srv.Close()
	if code := health(healthClient(nil), srv.URL+"/api/health"); code != 0 {
		t.Fatal("healthy")
	}
	rewrite(t, path, "locale = de_AT\n")
	if code := health(healthClient(nil), srv.URL+"/api/health"); code != 1 || !strings.Contains(settingsError(), "line 1") {
		t.Errorf("broken: %d", code)
	}
}

// lastWord: the last word a command printed (a token).
func lastWord(out string) string {
	f := strings.Fields(out)
	if len(f) == 0 {
		return ""
	}
	return f[len(f)-1]
}

func TestTokenHashes(t *testing.T) {
	kept := hashToken("secret")
	if !isTokenHash(kept) || isTokenHash("secret") || isTokenHash("sha256:abc") {
		t.Error("isTokenHash")
	}
	if !tokenMatches("secret", kept) || tokenMatches("Secret", kept) || tokenMatches(kept, kept) ||
		!tokenMatches("plain", "plain") || tokenMatches("", "") || tokenMatches("x", "") {
		t.Error("tokenMatches")
	}
}

func TestSettingsChange(t *testing.T) {
	u := func(name, pw, tok string) *User { return &User{Name: name, PasswordHash: pw, MCPToken: tok} }
	old := &Settings{Locale: "de", Users: map[string]*User{"alice": u("alice", "a", ""), "bob": u("bob", "b", "t")}}
	now := &Settings{Locale: "en", Hosts: map[string]bool{"x": true},
		Users: map[string]*User{"alice": u("alice", "a2", ""), "bob": u("bob", "b", "t2"), "carol": u("carol", "c", "")}}
	want := "Change settings: alice's password, bob's MCP token, add user carol, hosts, locale"
	if got := settingsChange(old, now); got != want {
		t.Errorf("%q", got)
	}
	if got := settingsChange(now, &Settings{Locale: "en", Hosts: map[string]bool{"x": true}}); got !=
		"Change settings: remove user alice, remove user bob, remove user carol" {
		t.Errorf("%q", got)
	}
	if got := settingsChange(old, old); got != "Change settings" {
		t.Errorf("%q", got)
	}
}

func TestTrustedProxies(t *testing.T) {
	configWiki(t, "")
	if got := fmt.Sprint(settings().TrustedProxies); got != "[127.0.0.0/8 ::1/128]" {
		t.Errorf("default: %s", got)
	}
	configWiki(t, "trusted-proxies = 172.17.0.1, 10.1.2.3/8\n")
	if got := fmt.Sprint(settings().TrustedProxies); got != "[172.17.0.1/32 10.0.0.0/8]" {
		t.Errorf("set: %s", got)
	}
	configWiki(t, "trusted-proxies =\n")
	if len(settings().TrustedProxies) != 0 {
		t.Errorf("empty: none trusted, not %v", settings().TrustedProxies)
	}
	f := &settingsfile.File{Top: map[string]settingsfile.Value{"trusted-proxies": {Value: "my-proxy", Line: 3}}}
	if _, err := loadSettings(f); err == nil || !strings.Contains(err.Error(), "line 3: trusted-proxies = my-proxy isn't an IP address") {
		t.Errorf("a name: %v", err)
	}
}

func TestGroupOwners(t *testing.T) {
	user := func(name, more string) string {
		return "[user " + name + "]\npassword = " + hashPassword("p") + "\n" + more
	}
	text := user("alice", "groups = apollo\n") + user("bob", "groups = apollo, dev\n") + user("carol", "role = viewer\n") +
		user("dan", "disabled = yes\n") + user("eve", "role = admin\n") +
		"[group apollo]\nowners = Alice, @carol, dan, gone\n[group all]\nincludes = apollo\nowners = bob\n"
	f, err := parseConfig(text)
	if err != nil {
		t.Fatal(err)
	}
	s, err := loadSettings(f)
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(s.Owners["apollo"], []string{"alice", "carol", "dan", "gone"}) || !slices.Equal(s.Owners["all"], []string{"bob"}) ||
		!slices.Equal(s.groups(), []string{"all", "apollo", "dev"}) || !slices.Equal(s.directMembers("apollo"), []string{"alice", "bob"}) ||
		len(s.directMembers("all")) != 0 {
		t.Errorf("owners %v groups %v", s.Owners, s.groups())
	}
	for _, c := range []struct {
		user, group    string
		owner, manages bool
	}{
		{"alice", "apollo", true, true},
		{"bob", "apollo", false, false},  // in it, not its owner
		{"carol", "apollo", true, false}, // a viewer changes nothing
		{"dan", "apollo", false, false},  // disabled
		{"gone", "apollo", false, false}, // no user (removed): no error, and no owner
		{"eve", "apollo", false, true},   // an admin says it of any group
		{"eve", "dev", false, true},
		{"bob", "all", true, true},
		{"alice", "all", false, false},
		{"alice", "dev", false, false}, // a group without owners is an admin's
	} {
		if s.ownerOf(c.user, c.group) != c.owner || s.managesGroup(c.user, c.group) != c.manages {
			t.Errorf("%s and %s: owner %v, manages %v", c.user, c.group, s.ownerOf(c.user, c.group), s.managesGroup(c.user, c.group))
		}
	}
	if _, err := parseConfig(user("alice", "") + "[group apollo]\nmembers = alice\n"); err == nil {
		t.Error("a key a group's section doesn't have is an error")
	}
	if bad, _ := parseConfig(user("alice", "") + "[group apollo]\nowners = alice, no_name\n"); bad != nil {
		if _, err := loadSettings(bad); err == nil || !strings.Contains(err.Error(), "no_name") {
			t.Errorf("an owner that is no user's name: %v", err)
		}
	}
	// The line edits: a key that goes leaves the section while it says something else.
	setIncludes(f, "all", nil)
	setOwners(f, "apollo", []string{"alice"})
	dropOwner(f, "bob")
	if strings.Contains(f.Text(), "[group all]") || !strings.Contains(f.Text(), "[group apollo]\nowners = alice\n") {
		t.Errorf("after the edits:\n%s", f.Text())
	}
	// A rename: on the users, in what includes it, its section.
	f, _ = parseConfig(text)
	renameGroupInFile(f, settingsOf(f.Text()), "apollo", "zeus")
	now := settingsOf(f.Text())
	if !slices.Equal(now.Users["bob"].Groups, []string{"dev", "zeus"}) || !slices.Equal(now.Includes["all"], []string{"zeus"}) ||
		!slices.Equal(now.Owners["zeus"], []string{"alice", "carol", "dan", "gone"}) || slices.Contains(now.groups(), "apollo") {
		t.Errorf("after the rename:\n%s", f.Text())
	}
}

// TestCommandsAskForEightCharacters: a password set from the command line
// (user add, user passwd, user remove --password, config passwd) has at least
// passwordMin characters, as on the pages that set one; characters, not bytes.
func TestCommandsAskForEightCharacters(t *testing.T) {
	wiki := t.TempDir()
	getenv := func(k string) string {
		if k == "ZONE_DATA" {
			return wiki
		}
		return ""
	}
	user := func(input string, args ...string) (int, string) {
		var out, errs bytes.Buffer
		return userMain(args, getenv, strings.NewReader(input), &out, &errs), errs.String()
	}
	config := func(input string, args ...string) (int, string) {
		var out, errs bytes.Buffer
		return configMain(args, getenv, strings.NewReader(input), &out, &errs), errs.String()
	}
	const short = "Choose a password of at least 8 characters."
	if code, errs := config("seven-c\nseven-c\n", "passwd"); code == 0 || !strings.Contains(errs, short) {
		t.Errorf("config passwd, 7 characters: %d %q", code, errs)
	}
	if f, _ := readConfigFile(filepath.Join(wiki, ".zone", "config")); f != nil && f.Top["password"].Value != "" {
		t.Errorf("a short password was set: %q", f.Text())
	}
	if code, errs := config("äöüäöüäö\näöüäöüäö\n", "passwd"); code != 0 { // 8 characters, 16 bytes
		t.Errorf("config passwd, 8 characters: %d %q", code, errs)
	}
	if code, errs := user("seven-c\nseven-c\n", "add", "alice"); code == 0 || !strings.Contains(errs, short) {
		t.Errorf("user add, 7 characters: %d %q", code, errs)
	}
	if code, errs := user("eight-ch\neight-ch\n", "add", "alice"); code != 0 {
		t.Fatalf("user add, 8 characters: %d %q", code, errs)
	}
	if code, errs := user("seven-c\nseven-c\n", "passwd", "alice"); code == 0 || !strings.Contains(errs, short) {
		t.Errorf("user passwd, 7 characters: %d %q", code, errs)
	}
	f, _ := readConfigFile(filepath.Join(wiki, ".zone", "config"))
	if f == nil || !checkPassword("eight-ch", f.User("alice").Values["password"].Value) {
		t.Error("alice's password changed")
	}
	if code, errs := user("seven-c\nseven-c\n", "remove", "alice", "--password"); code == 0 || !strings.Contains(errs, short) {
		t.Errorf("user remove --password, 7 characters: %d %q", code, errs)
	}
	if f, _ := readConfigFile(filepath.Join(wiki, ".zone", "config")); f == nil || f.User("alice") == nil {
		t.Error("alice was removed")
	}
}
