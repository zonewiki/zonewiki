package main

import (
	"bytes"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
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

// configWiki: a test wiki with this settings file, Zone started on it.
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
// ends; a newer change replaces the one waiting; a request that doesn't
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
	if !locale("pt-BR") {
		t.Errorf("not taken after it: %s", settings().Locale)
	}

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
func TestReloadCommitsOutsideItsLock(t *testing.T) {
	path := configWiki(t, "locale = de\n")
	cfg.GitEnabled = true
	commitMu.Lock()
	rewrite(t, path, "locale = fr\n")
	done := make(chan bool)
	go func() { reloadSettings(); close(done) }()
	deadline := time.Now().Add(5 * time.Second)
	for settings().Locale != "fr" && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if !reloadMu.TryLock() {
		t.Error("reloadMu held while the reload commits")
	} else {
		reloadMu.Unlock()
	}
	select {
	case <-done:
		t.Error("committed past commitMu")
	case <-time.After(50 * time.Millisecond):
	}
	commitMu.Unlock()
	<-done
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
	code, out, errs := run("s3cret\ns3cret\n", "add", "alice")
	if code != 0 || !strings.Contains(out, "Added alice") || strings.Contains(out, "token") {
		t.Fatalf("add: %d %q %q", code, out, errs)
	}
	f := read()
	if !strings.HasPrefix(f.Lines[0], "# ") || f.Lines[1] != "" || f.Lines[2] != "[user alice]" || !checkPassword("s3cret", f.User("alice").Values["password"].Value) {
		t.Fatalf("%q", f.Text())
	}
	if st, _ := os.Stat(filepath.Join(wiki, ".zone", "config")); st.Mode().Perm() != 0o600 {
		t.Errorf("mode %o", st.Mode().Perm())
	}
	code, out, _ = run("pw\npw\n", "add", "bob", "--mcp-token")
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
	if code, _, _ = run("new\nnew\n", "passwd", "alice"); code != 0 ||
		!checkPassword("new", read().User("alice").Values["password"].Value) {
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
	before := read().Text()
	for _, c := range []struct {
		input string
		args  []string
		want  string
	}{
		{"pw\npw\n", []string{"add", "alice"}, "alice is a user already"},
		{"", []string{"passwd", "carol"}, "there is no user carol"},
		{"", []string{"remove", "carol"}, "there is no user carol"},
		{"", []string{"add", "Bad/Name"}, "isn't a user name"},
		{"pw\nwp\n", []string{"add", "carol"}, "The passwords differ"},
		{"", []string{"add"}, "Usage"},
		{"", []string{"passwd", "alice", "--mcp-token"}, "Usage"},
		{"", []string{"frobnicate", "x"}, "Usage"},
	} {
		if code, _, errs := run(c.input, c.args...); code == 0 || !strings.Contains(errs, c.want) {
			t.Errorf("%v: %d %q", c.args, code, errs)
		}
	}
	// A change Zone wouldn't start with is refused, the file left as it was.
	path := filepath.Join(wiki, ".zone", "config")
	_ = settingsfile.Write(path, before+"[user dave]\n")
	if code, _, errs := run("pw\npw\n", "add", "carol"); code == 0 || !strings.Contains(errs, "not changed") {
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
	if code, out, _ := run("wiki\nwiki\n", "remove", "alice", "--password"); code != 0 ||
		len(read().Users) != 0 || !checkPassword("wiki", read().Top["password"].Value) ||
		!strings.Contains(out, "password signs in") {
		t.Errorf("remove the last, with a password: %d %q", code, out)
	}
	// The first user replaces the wiki's password and token.
	f = read()
	f.Set("", "mcp-token", hashToken("t"))
	_ = settingsfile.Write(path, f.Text())
	if code, out, _ := run("pw\npw\n", "add", "carol"); code != 0 || read().Top["password"].Line != 0 ||
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
	if code, out, _ := run("pw\npw\n", "add", "alice"); code != 0 || !strings.Contains(out, "as an admin") ||
		users()["alice"].Role != roleAdmin || !strings.Contains(readFile(t, path), "role = admin") {
		t.Fatalf("first: %d %q", code, out)
	}
	if code, _, _ := run("pw\npw\n", "add", "bob"); code != 0 || users()["bob"].Role != roleEditor ||
		strings.Count(readFile(t, path), "role =") != 1 {
		t.Fatalf("second: %d", code)
	}
	if code, _, errs := run("pw\npw\n", "add", "carol", "--role", "viewer"); code != 0 || users()["carol"].Role != roleViewer {
		t.Fatalf("--role: %d %q", code, errs)
	}
	if code, _, errs := run("pw\npw\n", "add", "dave", "--role", "boss"); code == 0 || !strings.Contains(errs, "isn't a role") {
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
		"[user a]\npassword = " + hash + "\nrole = boss\n":      "line 3: a's role boss isn't one",
		"[user a]\npassword = " + hash + "\ndisabled = maybe\n": "line 3: a's disabled = maybe isn't yes or no",
		"role = admin\n": `"role" isn't a setting Zone knows`,
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
}

func mustParse(t *testing.T, text string) *settingsfile.File {
	t.Helper()
	f, err := parseConfig(text)
	if err != nil {
		t.Fatal(err)
	}
	return f
}

func TestConfigCommands(t *testing.T) {
	wiki := t.TempDir()
	hash := hashPassword("pw")
	// The settings as the environment held them before 2026-10.
	env := map[string]string{"ZONE_DATA": wiki, "LEAF_LOCALE": "de-AT", "LEAF_HOSTS": "nas.local",
		"LEAF_USERS": "alice,mary-ann", "LEAF_USER_ALICE_PASSWORD": hash, "LEAF_USER_ALICE_MCP_TOKEN": "s3cret-tok",
		"LEAF_USER_MARY_ANN_PASSWORD": hash, "ZONE_ALLOW_REMOTE": "1", "LEAF_SECRET_KEY": "k"}
	getenv := func(k string) string { return env[k] }
	run := func(input string, args ...string) (int, string, string) {
		var out, errs bytes.Buffer
		code := configMain(args, getenv, strings.NewReader(input), &out, &errs)
		return code, out.String(), errs.String()
	}
	if code, _, errs := run("", "check"); code != 1 || !strings.Contains(errs, "LEAF_HOSTS, LEAF_LOCALE, LEAF_USERS are set") {
		t.Errorf("check before the import: %d %q", code, errs)
	}
	code, out, errs := run("", "import")
	if code != 0 || !strings.Contains(out, "  LEAF_USER_ALICE_MCP_TOKEN\n") || !strings.Contains(out, "  LEAF_LOCALE\n") ||
		!strings.Contains(out, "  LEAF_SECRET_KEY (no longer used") || strings.Contains(out, "ZONE_ALLOW_REMOTE") {
		t.Fatalf("import: %d %q %q", code, out, errs)
	}
	f, _ := readConfigFile(filepath.Join(wiki, ".zone", "config"))
	if f.Top["locale"].Value != "de-AT" || f.Top["hosts"].Value != "nas.local" ||
		f.User("alice").Values["mcp-token"].Value != hashToken("s3cret-tok") ||
		f.User("mary-ann").Values["password"].Value != hash {
		t.Fatalf("%s", f.Text())
	}
	env = map[string]string{"ZONE_DATA": wiki, "ZONE_ALLOW_REMOTE": "1"}
	code, out, _ = run("", "check")
	if code != 0 || !strings.Contains(out, "locale             de-AT  [line ") ||
		!strings.Contains(out, "users              alice (MCP token), mary-ann") ||
		!strings.Contains(out, "noshare            #noshare  [default]") || strings.Contains(out, "sha256") {
		t.Errorf("check:\n%s", out)
	}
	if code, out, _ := run("", "import"); code != 0 || !strings.Contains(out, "Nothing to import") {
		t.Errorf("import, nothing: %q", out)
	}
	if code, _, errs := run("pw\npw\n", "passwd"); code == 0 || !strings.Contains(errs, "zonewiki user passwd NAME") {
		t.Errorf("passwd with users: %q", errs)
	}
	// Without users: the wiki's password and token; a plain LEAF_PASSWORD goes in as its hash.
	wiki2 := t.TempDir()
	env = map[string]string{"ZONE_DATA": wiki2, "LEAF_PASSWORD": "secret", "LEAF_MCP_TOKEN": "tok"}
	if code, _, errs := run("", "import"); code != 0 {
		t.Fatal(errs)
	}
	f, _ = readConfigFile(filepath.Join(wiki2, ".zone", "config"))
	if !checkPassword("secret", f.Top["password"].Value) || f.Top["mcp-token"].Value != hashToken("tok") {
		t.Errorf("%s", f.Text())
	}
	env = map[string]string{"ZONE_DATA": wiki2}
	if code, out, errs := run("new\nnew\n", "passwd"); code != 0 || !strings.Contains(out, "Changed the wiki's password") {
		t.Fatal(errs)
	}
	code, out, _ = run("", "token")
	f, _ = readConfigFile(filepath.Join(wiki2, ".zone", "config"))
	if code != 0 || !checkPassword("new", f.Top["password"].Value) || f.Top["mcp-token"].Value != hashToken(lastWord(out)) {
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
	code, out, errs = run("", "unset", "password")
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
	if code := health(srv.URL + "/api/health"); code != 0 {
		t.Fatal("healthy")
	}
	rewrite(t, path, "locale = de_AT\n")
	if code := health(srv.URL + "/api/health"); code != 1 || !strings.Contains(settingsError(), "line 1") {
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
