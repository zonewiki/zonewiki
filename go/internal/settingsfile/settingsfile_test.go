package settingsfile

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

var testKeys = Keys{Top: []string{"locale", "noshare", "hosts"}, User: []string{"password", "mcp-token"},
	UserName: regexp.MustCompile(`^[a-z0-9]+(-[a-z0-9]+)*$`),
	Group:    []string{"includes", "owners"}, GroupName: regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,39}$`)}

func parse(text string) (*File, error) { return Parse("config", text, testKeys) }

func TestParseConfig(t *testing.T) {
	f, err := parse("\ufeff# Zonewiki\r\nlocale = de-AT\n  # indented comment\nnoshare = #noshare, people/\n\n" +
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
		"[team admins]":                      "line 1: [team admins] isn't a section",
		"[group Admins]":                     "line 1: \"Admins\" isn't a group's name",
		"[group a]\n[group a]":               "line 2: [group a] is there twice (line 1)",
		"[group a]\nmembers = x":             "line 2: \"members\" isn't a group's setting. A group has includes",
		"[user a]\n[group a]\npassword = x":  "line 3: \"password\" isn't a group's setting",
		"[group a]\n[user a]\nincludes = x":  "line 3: \"includes\" isn't a user's setting",
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
	f, _ := parse("# Zonewiki's settings\n# more\n\nlocale = de\n\n# Alice, admin\n[user alice]\npassword = a\n\n" +
		"# Bob\n[user bob]\npassword = b\n")
	f.Set("", "hosts", "x")
	f.Set("", "locale", "en")
	f.Set("alice", "mcp-token", "t")
	f.Set("bob", "password", "b2")
	f.Set("carol", "password", "c")
	want := "# Zonewiki's settings\n# more\n\nlocale = en\nhosts = x\n\n# Alice, admin\n[user alice]\npassword = a\n" +
		"mcp-token = t\n\n# Bob\n[user bob]\npassword = b2\n\n[user carol]\npassword = c\n"
	if f.Text() != want {
		t.Fatalf("set:\n%s\nwant:\n%s", f.Text(), want)
	}
	f.RemoveUser("alice")
	f.RemoveUser("carol")
	f.RemoveUser("nobody")
	want = "# Zonewiki's settings\n# more\n\nlocale = en\nhosts = x\n\n# Bob\n[user bob]\npassword = b2\n"
	if f.Text() != want {
		t.Fatalf("remove:\n%s\nwant:\n%s", f.Text(), want)
	}
	f, _ = parse("# Zonewiki\n[user a]\npassword = x\n")
	if f.RemoveUser("a"); f.Text() != "# Zonewiki\n" {
		t.Errorf("the file's own comment went: %q", f.Text())
	}
	f, _ = parse("# Zonewiki\n\n[user a]\npassword = x\n")
	if f.Set("", "locale", "de"); f.Text() != "# Zonewiki\nlocale = de\n\n[user a]\npassword = x\n" {
		t.Errorf("before the users: %q", f.Text())
	}
	f, _ = parse("# Zonewiki\n[user a]\npassword = x\n")
	if f.Set("", "locale", "de"); f.Text() != "# Zonewiki\nlocale = de\n\n[user a]\npassword = x\n" {
		t.Errorf("before the users: %q", f.Text())
	}
	// Into a file of comments only: below them.
	f, _ = parse("# Zonewiki\n\n# end\n")
	f.Set("", "locale", "de")
	if f.Text() != "# Zonewiki\nlocale = de\n\n# end\n" {
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

// A [group NAME] section: read beside the users, and edited as a user's is,
// keeping the comments and the lines an edit doesn't change.
func TestGroupSections(t *testing.T) {
	f, err := parse("locale = de\n\n[user alice]\npassword = h\n\n# Two teams\n[group product]\nincludes = dev, design\n\n[user bob]\npassword = h\n")
	if err != nil {
		t.Fatal(err)
	}
	if len(f.Groups) != 1 || f.Group("product") == nil || f.Group("product").Values["includes"] != (Value{"dev, design", 8}) ||
		f.Group("dev") != nil || len(f.Users) != 2 {
		t.Fatalf("groups %+v users %+v", f.Groups, f.Users)
	}
	// A group and a user of one name are two sections.
	f.Set("product", "password", "p")
	f.SetGroup("product", "includes", "dev")
	f.SetGroup("all", "includes", "product")
	want := "locale = de\n\n[user alice]\npassword = h\n\n# Two teams\n[group product]\nincludes = dev\n\n[user bob]\npassword = h\n\n" +
		"[user product]\npassword = p\n\n[group all]\nincludes = product\n"
	if f.Text() != want {
		t.Errorf("set:\n%s", f.Text())
	}
	if _, err := parse(f.Text()); err != nil {
		t.Errorf("what the edits wrote: %v", err)
	}
	f.RemoveGroup("product") // with the comment about it; the user of that name stays
	f.RemoveGroup("none")
	want = "locale = de\n\n[user alice]\npassword = h\n\n[user bob]\npassword = h\n\n[user product]\npassword = p\n\n[group all]\nincludes = product\n"
	if f.Text() != want {
		t.Errorf("remove:\n%s", f.Text())
	}
}

func TestGroupEdits(t *testing.T) {
	f, err := parse("[user alice]\npassword = h\n\n# The project\n[group apollo]\nincludes = dev\n# who says who is in it\nowners = alice\n\n[group all]\nincludes = apollo\n")
	if err != nil {
		t.Fatal(err)
	}
	f.UnsetGroup("apollo", "includes")
	f.UnsetGroup("apollo", "nothing")
	f.UnsetGroup("none", "includes")
	f.RenameGroup("apollo", "artemis") // the header's line alone
	f.RenameGroup("none", "other")
	want := "[user alice]\npassword = h\n\n# The project\n[group artemis]\n# who says who is in it\nowners = alice\n\n[group all]\nincludes = apollo\n"
	if f.Text() != want {
		t.Errorf("edits:\n%s", f.Text())
	}
	if again, err := parse(f.Text()); err != nil || again.Group("artemis") == nil || again.Group("apollo") != nil {
		t.Errorf("what the edits wrote: %v", err)
	}
}
