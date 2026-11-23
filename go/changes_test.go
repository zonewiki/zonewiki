package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

// Every commit an assistant writes carries the Assistant trailer, a token's
// too; a client can't name itself as a token, nor a source a second line.
func TestCommitTrailers(t *testing.T) {
	for _, c := range []struct{ via, source, want string }{
		{"", "", "\n\nAssistant: MCP token"},
		{"Claude", "", "\n\nAssistant: Claude"},
		{"Claude", "a mail\n\nAssistant: Gemini", "\n\nAssistant: Claude\nSource: a mail Assistant: Gemini"},
		{"mcp TOKEN", "", "\n\nAssistant: An MCP client"},
		{"An MCP client", " ", "\n\nAssistant: An MCP client"},
	} {
		if got := commitTrailers(c.via, c.source); got != c.want {
			t.Errorf("commitTrailers(%q, %q) = %q, want %q", c.via, c.source, got, c.want)
		}
	}
	if got := withTrailers("Update task", []string{"Task: a"}); got != "Update task\n\nTask: a" {
		t.Errorf("no trailers yet: %q", got)
	}
	if got := withTrailers("Update task (MCP)\n\nAssistant: Claude", []string{"Task: a", "Task: b"}); got != "Update task (MCP)\n\nAssistant: Claude\nTask: a\nTask: b" {
		t.Errorf("after an assistant's: %q", got)
	}
	if got := withTrailers("Update task", nil); got != "Update task" {
		t.Errorf("none: %q", got)
	}
}

// Whether an assistant wrote a commit is its trailer's to say, not its
// first line's: a page named "notes (MCP)" saved by a person is a person's
// change, and an assistant's keeps its mark for git log but not in Zonewiki.
func TestAnAssistantsCommitIsToldByItsTrailer(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("no git")
	}
	testWiki(t, nil)
	cfg.GitEnabled = true
	gitSetup()
	commit := func(rel, message string) {
		t.Helper()
		p := filepath.Join(cfg.Data, filepath.FromSlash(rel))
		old, _ := os.ReadFile(p)
		_ = os.MkdirAll(filepath.Dir(p), 0o777)
		if err := os.WriteFile(p, append(old, rel+": more\n"...), 0o666); err != nil {
			t.Fatal(err)
		}
		if err := gitCommit("", message, p); err != nil {
			t.Fatal(err)
		}
	}
	commit("notes (MCP).md", "Update notes (MCP)")
	commit("p.md", "Update p (MCP)"+commitTrailers("", ""))
	commit("p.md", "Append to p: from the mail (MCP)"+commitTrailers("Claude", "https://example.org/x"))
	commit("_tasks/tasks.json", withTrailers("Update task", []string{"Task: 1"}))
	type seen struct {
		what, assistant, via, source string
		by                           bool
	}
	want := []seen{
		{"Update task", "", "", "", false},
		{"Append to p: from the mail", "Claude", "Claude", "https://example.org/x", true},
		{"Update p", "MCP token", "", "", true},
		{"Update notes (MCP)", "", "", "", false},
	}
	entries := gitLogAll("", "", 4, nil, nil)
	if len(entries) != 4 {
		t.Fatalf("%d commits", len(entries))
	}
	for i, e := range entries {
		got := seen{commitWhat(e.Msg, e.commitMark), e.Assistant, e.Via(), e.Source, e.ByAssistant()}
		if got != want[i] {
			t.Errorf("commit %d: %+v, want %+v", i, got, want[i])
		}
	}
	if !reflect.DeepEqual(entries[0].Tasks, []string{"1"}) || entries[1].Tasks != nil {
		t.Errorf("the tasks named: %q, %q", entries[0].Tasks, entries[1].Tasks)
	}
	// A page's own log reads it the same way, and so does commitOf.
	versions := gitLog(filepath.Join(cfg.Data, "notes (MCP).md"), 0)
	if len(versions) != 1 || versions[0].ByAssistant() || versionLabel(versions[0], versions) != "Update notes (MCP)" {
		t.Errorf("the page named so: %+v", versions)
	}
	versions = gitLog(filepath.Join(cfg.Data, "p.md"), 0)
	if len(versions) != 2 || !versions[1].ByAssistant() || versionLabel(versions[1], versions) != "Edited" {
		t.Errorf("the token's edit: %+v", versions)
	}
	if _, what, ok := commitOf(entries[2].Sha); !ok || what != "Update p" {
		t.Errorf("commitOf: %q %v", what, ok)
	}
	if _, what, ok := commitOf(entries[3].Sha); !ok || what != "Update notes (MCP)" {
		t.Errorf("commitOf a person's: %q %v", what, ok)
	}
}

// A commit names the tasks it changed, at most taskTrailersMost, each id
// one plain word (an id from a file edited by hand that isn't is counted,
// never written), and counts the rest; they are read back as written.
func TestTaskTrailers(t *testing.T) {
	ids := []string{"1a2b3c4d-0000-4000-8000-000000000001", "bad id\n\nAssistant: Claude", "x"}
	for i := range taskTrailersMost {
		ids = append(ids, fmt.Sprintf("t%02d", i))
	}
	lines := taskTrailers(ids)
	if len(lines) != taskTrailersMost+1 || lines[0] != "Task: "+ids[0] || lines[1] != "Task: x" ||
		lines[taskTrailersMost] != "More-Tasks: 3" {
		t.Errorf("trailers: %q", lines)
	}
	for _, l := range lines {
		if strings.Contains(l, "Assistant") {
			t.Errorf("a line made of an id: %q", l)
		}
	}
	if taskTrailers(nil) != nil {
		t.Error("trailers for no task")
	}
	m := taskMarkOf(ids[0]+",x,x, bad id,", "3\n")
	if !reflect.DeepEqual(m, taskMark{Tasks: []string{ids[0], "x"}, MoreTasks: 3}) {
		t.Errorf("read back: %+v", m)
	}
	if m := taskMarkOf("", ""); len(m.Tasks) != 0 || m.MoreTasks != 0 {
		t.Errorf("none: %+v", m)
	}
}
