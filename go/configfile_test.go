package main

import (
	"bytes"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestParseConfig(t *testing.T) {
	f, err := parseConfig("\ufeff# Zone\r\nlocale = de-AT\n  # indented comment\nnoshare = #noshare, people/\n\n" +
		"[user alice]\npassword = h=1\nmcp-token=  tok  \n[ user   mary-ann ]\n")
	if err != nil {
		t.Fatal(err)
	}
	if f.top["locale"] != (configValue{"de-AT", 2}) || f.top["noshare"].value != "#noshare, people/" {
		t.Errorf("top %v", f.top)
	}
	if len(f.users) != 2 || f.users[0].name != "alice" || f.users[0].values["password"].value != "h=1" ||
		f.users[0].values["mcp-token"].value != "tok" || f.users[1].name != "mary-ann" || f.users[1].line != 9 {
		t.Errorf("users %+v", f.users)
	}
	for text, want := range map[string]string{
		"locale = de\npasword = x":           "line 2: \"pasword\" isn't a setting",
		"[group admins]":                     "line 1: [group admins] isn't a section",
		"[user Alice]":                       "line 1: \"Alice\" isn't a user name",
		"[user]":                             "line 1: \"\" isn't a user name",
		"[user a]\n[user a]":                 "line 2: [user a] is there twice (line 1)",
		"locale = de\nlocale = en":           "line 2: locale is set twice (line 1)",
		"just words":                         "line 1: not a setting",
		"= x":                                "line 1: not a setting",
		"[user a]\nlocale = de":              "line 2: \"locale\" isn't a user's setting",
		"[user a]\npassword = x\npassword=y": "line 3: password is set twice (line 2)",
	} {
		if _, err := parseConfig(text); err == nil || !strings.Contains(err.Error(), want) ||
			!strings.HasPrefix(err.Error(), configName()+" ") {
			t.Errorf("%q: %v, want %q", text, err, want)
		}
	}
	if f, err := parseConfig(""); err != nil || len(f.lines) != 0 || len(f.users) != 0 {
		t.Errorf("empty: %v %+v", err, f)
	}
}

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
		if err := writeConfig(path, text); err != nil {
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
	if err := writeConfig(path, text); err != nil {
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

func TestConfigFileEdits(t *testing.T) {
	f, _ := parseConfig("# Zone's settings\n# more\n\nlocale = de\n\n# Alice, admin\n[user alice]\npassword = a\n\n" +
		"# Bob\n[user bob]\npassword = b\n")
	f.set("", "hosts", "x")
	f.set("", "locale", "en")
	f.set("alice", "mcp-token", "t")
	f.set("bob", "password", "b2")
	f.set("carol", "password", "c")
	want := "# Zone's settings\n# more\n\nlocale = en\nhosts = x\n\n# Alice, admin\n[user alice]\npassword = a\n" +
		"mcp-token = t\n\n# Bob\n[user bob]\npassword = b2\n\n[user carol]\npassword = c\n"
	if f.text() != want {
		t.Fatalf("set:\n%s\nwant:\n%s", f.text(), want)
	}
	f.removeUser("alice")
	f.removeUser("carol")
	f.removeUser("nobody")
	want = "# Zone's settings\n# more\n\nlocale = en\nhosts = x\n\n# Bob\n[user bob]\npassword = b2\n"
	if f.text() != want {
		t.Fatalf("remove:\n%s\nwant:\n%s", f.text(), want)
	}
	f, _ = parseConfig("# Zone\n[user a]\npassword = x\n")
	if f.removeUser("a"); f.text() != "# Zone\n" {
		t.Errorf("the file's own comment went: %q", f.text())
	}
	f, _ = parseConfig("# Zone\n\n[user a]\npassword = x\n")
	if f.set("", "locale", "de"); f.text() != "# Zone\nlocale = de\n\n[user a]\npassword = x\n" {
		t.Errorf("before the users: %q", f.text())
	}
	f, _ = parseConfig("# Zone\n[user a]\npassword = x\n")
	if f.set("", "locale", "de"); f.text() != "# Zone\nlocale = de\n\n[user a]\npassword = x\n" {
		t.Errorf("before the users: %q", f.text())
	}
	// Into a file of comments only: below them.
	f, _ = parseConfig("# Zone\n\n# end\n")
	f.set("", "locale", "de")
	if f.text() != "# Zone\nlocale = de\n\n# end\n" {
		t.Errorf("%q", f.text())
	}
}

func TestWriteConfig(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, ".zone", "config")
	if err := writeConfig(path, "locale = de\n"); err != nil {
		t.Fatal(err)
	}
	_ = os.Chmod(path, 0o644)
	if err := writeConfig(path, "locale = en\n"); err != nil {
		t.Fatal(err)
	}
	st, _ := os.Stat(path)
	b, _ := os.ReadFile(path)
	entries, _ := os.ReadDir(filepath.Dir(path))
	if st.Mode().Perm() != 0o600 || string(b) != "locale = en\n" || len(entries) != 1 {
		t.Errorf("%o %q %d files", st.Mode().Perm(), b, len(entries))
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
	read := func() *configFile {
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
	if !strings.HasPrefix(f.lines[0], "# ") || f.lines[1] != "" || f.lines[2] != "[user alice]" || !checkPassword("s3cret", f.user("alice").values["password"].value) {
		t.Fatalf("%q", f.text())
	}
	if st, _ := os.Stat(filepath.Join(wiki, ".zone", "config")); st.Mode().Perm() != 0o600 {
		t.Errorf("mode %o", st.Mode().Perm())
	}
	code, out, _ = run("pw\npw\n", "add", "bob", "--mcp-token")
	token := f.user("bob")
	f = read()
	if token = f.user("bob"); code != 0 || token == nil ||
		token.values["mcp-token"].value != hashToken(lastWord(out)) || len(lastWord(out)) != 64 {
		t.Fatalf("add with a token: %d %q", code, out)
	}
	old := token.values["mcp-token"].value
	if code, out, _ = run("", "token", "bob"); code != 0 || read().user("bob").values["mcp-token"].value == old ||
		read().user("bob").values["mcp-token"].value != hashToken(lastWord(out)) {
		t.Errorf("token: %d %q", code, out)
	}
	if code, _, _ = run("new\nnew\n", "passwd", "alice"); code != 0 ||
		!checkPassword("new", read().user("alice").values["password"].value) {
		t.Error("passwd")
	}
	if code, out, _ = run("", "list"); code != 0 || out != "  alice\n  bob (MCP token)\n" {
		t.Errorf("list: %q", out)
	}
	_ = os.MkdirAll(filepath.Join(wiki, "private", "bob"), 0o777)
	if code, out, _ = run("", "remove", "bob"); code != 0 || read().user("bob") != nil ||
		!strings.Contains(out, "private/bob/") {
		t.Errorf("remove: %d %q", code, out)
	}
	before := read().text()
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
	_ = writeConfig(path, before+"[user dave]\n")
	if code, _, errs := run("pw\npw\n", "add", "carol"); code == 0 || !strings.Contains(errs, "not changed") {
		t.Errorf("with a broken section: %d %q", code, errs)
	}
	if got := readFile(t, path); got != before+"[user dave]\n" {
		t.Error("a refused command changed the file")
	}
	_ = writeConfig(path, before)
	// The last user goes only with the wiki's password instead, or with no login.
	if code, _, errs := run("", "remove", "alice"); code == 0 || !strings.Contains(errs, "last user") ||
		read().user("alice") == nil {
		t.Errorf("remove the last: %d %q", code, errs)
	}
	if code, out, _ := run("wiki\nwiki\n", "remove", "alice", "--password"); code != 0 ||
		len(read().users) != 0 || !checkPassword("wiki", read().top["password"].value) ||
		!strings.Contains(out, "password signs in") {
		t.Errorf("remove the last, with a password: %d %q", code, out)
	}
	// The first user replaces the wiki's password and token.
	f = read()
	f.set("", "mcp-token", hashToken("t"))
	_ = writeConfig(path, f.text())
	if code, out, _ := run("pw\npw\n", "add", "carol"); code != 0 || read().top["password"].line != 0 ||
		read().top["mcp-token"].line != 0 || read().user("carol") == nil ||
		!strings.Contains(out, "password no longer works") || !strings.Contains(out, "MCP token no longer works") {
		t.Errorf("first user over the wiki's password: %d %q", code, out)
	}
	if code, out, _ := run("", "remove", "carol", "--open"); code != 0 || len(read().users) != 0 ||
		read().top["password"].line != 0 || !strings.Contains(out, "next start") {
		t.Errorf("remove the last, open: %d %q", code, out)
	}
	for _, args := range [][]string{{"remove", "x", "--password", "--open"}, {"add", "x", "--open"}} {
		if code, _, _ := run("", args...); code != 2 {
			t.Errorf("%v: %d", args, code)
		}
	}
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
	if f.top["locale"].value != "de-AT" || f.top["hosts"].value != "nas.local" ||
		f.user("alice").values["mcp-token"].value != hashToken("s3cret-tok") ||
		f.user("mary-ann").values["password"].value != hash {
		t.Fatalf("%s", f.text())
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
	if !checkPassword("secret", f.top["password"].value) || f.top["mcp-token"].value != hashToken("tok") {
		t.Errorf("%s", f.text())
	}
	env = map[string]string{"ZONE_DATA": wiki2}
	if code, out, errs := run("new\nnew\n", "passwd"); code != 0 || !strings.Contains(out, "Changed the wiki's password") {
		t.Fatal(errs)
	}
	code, out, _ = run("", "token")
	f, _ = readConfigFile(filepath.Join(wiki2, ".zone", "config"))
	if code != 0 || !checkPassword("new", f.top["password"].value) || f.top["mcp-token"].value != hashToken(lastWord(out)) {
		t.Errorf("passwd, token: %q %s", out, f.text())
	}
	if code, _, errs := run("", "set", "hosts", "nas.local,", "wiki.example.org"); code != 0 {
		t.Fatal(errs)
	}
	if code, _, errs := run("", "set", "locale", "de-AT"); code != 0 {
		t.Fatal(errs)
	}
	f, _ = readConfigFile(filepath.Join(wiki2, ".zone", "config"))
	if f.top["hosts"].value != "nas.local, wiki.example.org" || f.top["locale"].value != "de-AT" {
		t.Errorf("set: %s", f.text())
	}
	if code, _, errs := run("", "unset", "locale"); code != 0 {
		t.Fatal(errs)
	}
	f, _ = readConfigFile(filepath.Join(wiki2, ".zone", "config"))
	if _, ok := f.top["locale"]; ok || f.top["hosts"].value == "" {
		t.Errorf("unset: %s", f.text())
	}
	for _, c := range [][]string{{"set", "locale", "de_AT"}, {"set", "pasword", "x"}, {"set", "password", "x"},
		{"set", "mcp-token", "x"}, {"set", "base-url", "http://x\n[user eve]\npassword = x"}} {
		if code, _, errs := run("", c...); code != 1 || errs == "" {
			t.Errorf("%v: %d", c, code)
		}
	}
	// The password and token are unset like the others (no login, /mcp off).
	f.set("", "password", hashPassword("pw"))
	f.set("", "mcp-token", hashToken("t"))
	_ = writeConfig(filepath.Join(wiki2, ".zone", "config"), f.text())
	code, out, errs = run("", "unset", "password")
	if code2, _, _ := run("", "unset", "mcp-token"); code != 0 || code2 != 0 || !strings.Contains(out, "no login") {
		t.Errorf("unset password, token: %d %d %q", code, code2, errs)
	}
	if f, _ = readConfigFile(filepath.Join(wiki2, ".zone", "config")); f.top["password"].line != 0 ||
		f.top["mcp-token"].line != 0 {
		t.Errorf("unset password, token: %s", f.text())
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
	f.set("", "locale", "fr")
	rewrite(t, filepath.Join(wiki2, ".zone", "config"), "locale = it\n")
	if err := saveConfig(f); err == nil || !strings.Contains(err.Error(), "changed while this command ran") {
		t.Errorf("changed meanwhile: %v", err)
	}
	if f, _ = readConfigFile(filepath.Join(wiki2, ".zone", "config")); f.top["locale"].value != "it" {
		t.Errorf("overwritten: %s", f.text())
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
