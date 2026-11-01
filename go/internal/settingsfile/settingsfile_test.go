package settingsfile

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

var testKeys = Keys{Top: []string{"locale", "noshare", "hosts"}, User: []string{"password", "mcp-token"},
	UserName: regexp.MustCompile(`^[a-z0-9]+(-[a-z0-9]+)*$`)}

func parse(text string) (*File, error) { return Parse("config", text, testKeys) }

func TestParseConfig(t *testing.T) {
	f, err := parse("\ufeff# Zone\r\nlocale = de-AT\n  # indented comment\nnoshare = #noshare, people/\n\n" +
		"[user alice]\npassword = h=1\nmcp-token=  tok  \n[ user   mary-ann ]\n")
	if err != nil {
		t.Fatal(err)
	}
	if f.Top["locale"] != (Value{"de-AT", 2}) || f.Top["noshare"].Value != "#noshare, people/" {
		t.Errorf("top %v", f.Top)
	}
	if len(f.Users) != 2 || f.Users[0].Name != "alice" || f.Users[0].Values["password"].Value != "h=1" ||
		f.Users[0].Values["mcp-token"].Value != "tok" || f.Users[1].Name != "mary-ann" || f.Users[1].Line != 9 {
		t.Errorf("users %+v", f.Users)
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
		if _, err := parse(text); err == nil || !strings.Contains(err.Error(), want) ||
			!strings.HasPrefix(err.Error(), "config ") {
			t.Errorf("%q: %v, want %q", text, err, want)
		}
	}
	if f, err := parse(""); err != nil || len(f.Lines) != 0 || len(f.Users) != 0 {
		t.Errorf("empty: %v %+v", err, f)
	}
}

func TestConfigFileEdits(t *testing.T) {
	f, _ := parse("# Zone's settings\n# more\n\nlocale = de\n\n# Alice, admin\n[user alice]\npassword = a\n\n" +
		"# Bob\n[user bob]\npassword = b\n")
	f.Set("", "hosts", "x")
	f.Set("", "locale", "en")
	f.Set("alice", "mcp-token", "t")
	f.Set("bob", "password", "b2")
	f.Set("carol", "password", "c")
	want := "# Zone's settings\n# more\n\nlocale = en\nhosts = x\n\n# Alice, admin\n[user alice]\npassword = a\n" +
		"mcp-token = t\n\n# Bob\n[user bob]\npassword = b2\n\n[user carol]\npassword = c\n"
	if f.Text() != want {
		t.Fatalf("set:\n%s\nwant:\n%s", f.Text(), want)
	}
	f.RemoveUser("alice")
	f.RemoveUser("carol")
	f.RemoveUser("nobody")
	want = "# Zone's settings\n# more\n\nlocale = en\nhosts = x\n\n# Bob\n[user bob]\npassword = b2\n"
	if f.Text() != want {
		t.Fatalf("remove:\n%s\nwant:\n%s", f.Text(), want)
	}
	f, _ = parse("# Zone\n[user a]\npassword = x\n")
	if f.RemoveUser("a"); f.Text() != "# Zone\n" {
		t.Errorf("the file's own comment went: %q", f.Text())
	}
	f, _ = parse("# Zone\n\n[user a]\npassword = x\n")
	if f.Set("", "locale", "de"); f.Text() != "# Zone\nlocale = de\n\n[user a]\npassword = x\n" {
		t.Errorf("before the users: %q", f.Text())
	}
	f, _ = parse("# Zone\n[user a]\npassword = x\n")
	if f.Set("", "locale", "de"); f.Text() != "# Zone\nlocale = de\n\n[user a]\npassword = x\n" {
		t.Errorf("before the users: %q", f.Text())
	}
	// Into a file of comments only: below them.
	f, _ = parse("# Zone\n\n# end\n")
	f.Set("", "locale", "de")
	if f.Text() != "# Zone\nlocale = de\n\n# end\n" {
		t.Errorf("%q", f.Text())
	}
}

func TestWriteConfig(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, ".zone", "config")
	if err := Write(path, "locale = de\n"); err != nil {
		t.Fatal(err)
	}
	_ = os.Chmod(path, 0o644)
	if err := Write(path, "locale = en\n"); err != nil {
		t.Fatal(err)
	}
	st, _ := os.Stat(path)
	b, _ := os.ReadFile(path)
	entries, _ := os.ReadDir(filepath.Dir(path))
	if st.Mode().Perm() != 0o600 || string(b) != "locale = en\n" || len(entries) != 1 {
		t.Errorf("%o %q %d files", st.Mode().Perm(), b, len(entries))
	}
}
