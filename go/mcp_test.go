package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"
	"time"
	"zonewiki/internal/jsonobj"
)

// The MCP server (mcp.go, mcptools.go, mcpwrite.go): the protocol's
// handling and the limits long answers are cut at, which the API tests
// (tests/test_mcp.py) can't reach.

// set changes a variable for one test.
func set[T any](t *testing.T, v *T, to T) {
	old := *v
	*v = to
	t.Cleanup(func() { *v = old })
}

// replyText: the reply to msg as JSON text ("null" for none).
func replyText(t *testing.T, msg string, call func(string, jsonObj) ([]toolContent, error)) string {
	t.Helper()
	reply, err := handleMCP(json.RawMessage(msg), asker{}, call)
	if err != nil {
		t.Fatal(err)
	}
	return jsonobj.Text(reply)
}

// handle: the reply to msg, as JSON reads back (nil for none).
func handle(t *testing.T, msg string, call func(string, jsonObj) ([]toolContent, error)) map[string]any {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal([]byte(replyText(t, msg, call)), &m); err != nil {
		t.Fatal(err)
	}
	return m
}

// field: a member of nested objects, by path.
func field(v any, path ...string) any {
	for _, k := range path {
		m, _ := v.(map[string]any)
		v = m[k]
	}
	return v
}

func noTools(string, jsonObj) ([]toolContent, error) { panic("not called") }

func TestMCPProtocol(t *testing.T) {
	testWiki(t, nil)
	r := handle(t, `{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}`, noTools)
	instructions, _ := field(r, "result", "instructions").(string)
	if field(r, "result", "protocolVersion") != "2025-06-18" || field(r, "result", "serverInfo", "version") != version ||
		!strings.Contains(instructions, "search_wiki") {
		t.Errorf("initialize: %s", jsonobj.Text(r))
	}
	r = handle(t, `{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}}`, noTools)
	if field(r, "result", "protocolVersion") != protocolVersions[0] {
		t.Error("an unknown version doesn't get the newest")
	}
	for _, msg := range []string{`{"jsonrpc": "2.0", "method": "notifications/initialized"}`, `{"jsonrpc": "2.0", "id": 5, "result": {}}`} {
		if handle(t, msg, noTools) != nil {
			t.Errorf("answered %s", msg)
		}
	}
	if got := replyText(t, `{"jsonrpc": "2.0", "id": 2, "method": "ping"}`, noTools); got != `{"jsonrpc":"2.0","id":2,"result":{}}` {
		t.Errorf("ping: %s", got)
	}
	code := func(msg string) any { return field(handle(t, msg, noTools), "error", "code") }
	for msg, want := range map[string]float64{
		`{"jsonrpc": "2.0", "id": 1, "method": "resources/list"}`: -32601,
		`{"id": 1, "method": "ping"}`:                             -32600,
		`[1]`:                                                     -32600,
		`{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "delete_everything"}}`:           -32602,
		`{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "read_page", "arguments": [1]}}`: -32602,
	} {
		if c := code(msg); c != want {
			t.Errorf("%s: code %v", msg, c)
		}
	}
	fail := func(string, jsonObj) ([]toolContent, error) { return nil, toolErr("No page 'x'") }
	got := replyText(t, `{"jsonrpc": "2.0", "id": "a", "method": "tools/call", "params": {"name": "read_page", "arguments": {"slug": "x"}}}`, fail)
	if got != `{"jsonrpc":"2.0","id":"a","result":{"content":[{"type":"text","text":"No page 'x'"}],"isError":true}}` {
		t.Errorf("a tool error: %s", got)
	}
}

func TestMCPToolDefinitions(t *testing.T) {
	var names []string
	for _, tool := range mcpTools {
		name, _ := tool["name"].(string)
		names = append(names, name)
		row, ok := mcpToolTable[name]
		if !ok || row.run == nil && row.content == nil {
			t.Errorf("%s has no function", name)
		}
		// What the definition tells the client and what Zone does with the
		// tool agree: a tool that isn't read-only says what it writes (so it
		// takes pagesMu for pages, and a viewer doesn't get it).
		ann, _ := tool["annotations"].(map[string]any)
		readOnly, declared := ann["readOnlyHint"].(bool)
		if !declared {
			t.Errorf("%s: no readOnlyHint", name)
		}
		if readOnly != (row.writes == toolWritesNothing) {
			t.Errorf("%s: readOnlyHint %v, but mcpToolTable says it writes %d", name, readOnly, row.writes)
		}
	}
	if len(names) != len(mcpToolTable) || names[0] != "search_wiki" || names[len(names)-1] != "update_task" {
		t.Errorf("tools: %q", names)
	}
	// Raised from 34000 for rename_page's narrow (2026-11): a move that takes
	// a page from readers wants its own yes; from 34200 for undo_changes
	// (2026-11): what the browser's Changes undoes an assistant undoes too.
	// From 35100 for source on the tools that write text (2026-11): where an
	// assistant's words are from, when not from its user. From 35800 for
	// a task's place in the manual order (2026-11): what a drop in a list
	// does, an assistant does too. From 36600 for history's assistant and
	// outside (2026-11): Changes' filters by connection and by text from
	// elsewhere, at both doors.
	// tests/test_mcp.py caps what is served (the same list, compact).
	if len(mcpToolsJSON) > 36900 {
		t.Errorf("the definitions grew to %d bytes: every session loads them", len(mcpToolsJSON))
	}
}

func TestLongGuidesAreCut(t *testing.T) {
	testWiki(t, map[string]string{"wiki-guide": "# Wiki guide\n\n" + strings.Repeat("x", 100),
		"tickets/CLAUDE": "# Tickets\n\nOne folder per ticket.\n", "tickets/bug-1": "# Bug\n"})
	set(t, &guideChars, 20)
	if got := instructionsWithGuide(asker{}); !strings.HasSuffix(got, "\n\n# Wiki guide\n\nxxxxxx\n\n[… cut here: read_page \"wiki-guide\" for the rest]") {
		t.Errorf("instructions: %q", got[len(got)-100:])
	}
	set(t, &folderGuideChars, 10)
	err := checkGuides(jsonObj{}, []string{"tickets/bug-1"}, asker{})
	if err == nil || !strings.Contains(err.Error(), "# Tickets\n\n\n[… cut here: read_page \"tickets/CLAUDE\" for the rest]") {
		t.Errorf("refusal: %v", err)
	}
}

func TestToolListNamesGuidedFolders(t *testing.T) {
	testWiki(t, nil)
	desc := func() string {
		for _, tool := range toolList(asker{}) {
			if tool["name"] == "read_guides" {
				return tool["description"].(string)
			}
		}
		return ""
	}
	if strings.Contains(desc(), "Guides in this wiki") {
		t.Error("guides named without any")
	}
	testWiki(t, map[string]string{"a/CLAUDE": "# A\n", "b/CLAUDE": "# B\n"})
	set(t, &guidedShown, 1)
	if d := desc(); !strings.HasSuffix(d, " Guides in this wiki:\n- a/: A\n- … and 1 more folders") {
		t.Errorf("description: %q", d)
	}
}

func TestLinkedFromIsCut(t *testing.T) {
	testWiki(t, map[string]string{"golf": "# Golf\n", "c": "[[golf]]\n", "d": "[[golf]]\n", "e": "[[golf]]\n"})
	set(t, &backlinksShown, 2)
	if got := mcpPageLinks("golf", "# Golf\n", asker{}); !strings.HasSuffix(got, "Linked from (3): c, d, … and 1 more") {
		t.Errorf("got %q", got)
	}
}

func TestQueryLogMovesAsideWhenFull(t *testing.T) {
	testWiki(t, map[string]string{"golf": "# Golf\n\nMechaniker, Bremsen.\n"})
	settings().QueryLog = true
	set(t, &queryLogMax, 10)
	for _, q := range []string{"git push", "Mechaniker", "Bremsen"} {
		logQuery(q, "", nil)
	}
	log, _ := os.ReadFile(queryLogPath())
	old, _ := os.ReadFile(filepath.Join(filepath.Dir(queryLogPath()), "queries.1.jsonl"))
	if strings.Count(string(log), "\n") != 1 || !strings.Contains(string(log), `"Bremsen"`) || !strings.Contains(string(old), `"Mechaniker"`) {
		t.Errorf("log %q, moved aside %q", log, old)
	}
}

// Zone's own state that holds users' words is its owner's alone to read on
// the host, as the settings file is: the query log (every user's queries,
// and the private pages they found), a task archive's records (private
// tasks' titles), what a user has seen, the semantic cache's pages, who
// pasted a file for a task not made yet.
func TestStateWithUsersWordsIsOwnerOnly(t *testing.T) {
	testWiki(t, map[string]string{"golf": "# Golf\n"})
	settings().QueryLog = true
	old := filepath.Join(filepath.Dir(queryLogPath()), "queries.jsonl")
	if err := os.MkdirAll(filepath.Dir(old), 0o777); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(old, []byte("{}\n"), 0o644); err != nil { // a log from before
		t.Fatal(err)
	}
	logQuery("golf", "", nil)
	archive := filepath.Join(cfg.Data, "private/alice/_tasks/closed-2001-01.json")
	saveArchiveDocs(archive, "stamp", nil)
	seen := seenPath("alice")
	if err := os.MkdirAll(filepath.Dir(seen), 0o777); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(seen, []byte("{}\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	markSeen("alice", &Task{ID: "t1"})
	withUsers(t, "alice")
	if !claimUnsaved(asUser("alice"), "0a1b2c3d-0000-4000-8000-00000000abcd") {
		t.Fatal("no claim")
	}
	for _, path := range []string{queryLogPath(), archiveDocsPath(archive), seen, statePath("unsaved-files.json")} {
		if st, err := os.Stat(path); err != nil || st.Mode().Perm()&0o077 != 0 {
			t.Errorf("%s: %v %v", path, st.Mode(), err)
		}
	}
}

// A pattern that backtracks for ages in Python (Python ran it in a process
// of its own, killed after a timeout) is linear in RE2.
func TestCatastrophicRegexIsFast(t *testing.T) {
	testWiki(t, map[string]string{"slow": "# Slow\n\n" + strings.Repeat("a", 5000) + "!\n"})
	start := time.Now()
	args, _ := jsonobj.Parse([]byte(`{"find": "(a+)+$", "replace": "x", "regex": true, "dry_run": false}`))
	out, err := replaceInPages(args, "", asker{})
	if err != nil || out != `Nothing matches "(a+)+$" outside code.` || time.Since(start) > 2*time.Second {
		t.Errorf("%q %v in %v", out, err, time.Since(start))
	}
}

func TestGrepTimeout(t *testing.T) {
	testWiki(t, map[string]string{"a": "# A\n"})
	cfg.GitEnabled = true
	set(t, &grepTimeout, time.Nanosecond)
	if _, err := grepPages(asker{}, "(a+)+$", false); err == nil || !strings.Contains(err.Error(), "took longer than") {
		t.Errorf("err %v", err)
	}
}

// git grep is told folders as patterns: one whose name holds a wildcard is
// itself, and what is below Zone's own folders (_…, .…) isn't searched.
func TestGrepFoldersAreThemselves(t *testing.T) {
	testWiki(t, map[string]string{"top": "needle", "a[1]*?/x": "needle", "a1/x": "hay", "notes/_drafts/y": "needle",
		"notes/.old/z": "needle", "notes/n": "needle"})
	cfg.GitEnabled = true
	hits, err := grepPages(asker{}, "needle", false)
	var slugs []string
	for _, h := range hits {
		slugs = append(slugs, h.Slug)
	}
	if sort.Strings(slugs); err != nil || strings.Join(slugs, " ") != "a[1]*?/x notes/n top" {
		t.Errorf("%v %v", slugs, err)
	}
}

func TestReplacementTemplates(t *testing.T) {
	re := regexp.MustCompile(`BUG-(\d+)(?P<rest>x)?`)
	m := re.FindStringSubmatchIndex("BUG-12")
	for tmpl, want := range map[string]string{
		`[[bug-\1]]`:    "[[bug-12]]",
		`\g<1>\g<0>`:    "12BUG-12",
		`<\g<rest>>`:    "<>", // a group that didn't take part
		`a\nb\\c\-d`:    "a\nb\\c\\-d",
		`\101`:          "A", // octal
		`\10`:           "",  // group 10: an error below
		`plain $1 text`: "plain $1 text",
	} {
		parts, err := parseTemplate(tmpl, re)
		if tmpl == `\10` {
			if err == nil {
				t.Error(`\10 isn't a group`)
			}
			continue
		}
		if err != nil {
			t.Errorf("%s: %v", tmpl, err)
			continue
		}
		if got := expand(parts, "BUG-12", m); got != want {
			t.Errorf("%s: got %q, want %q", tmpl, got, want)
		}
	}
	for _, bad := range []string{`\2`, `\q`, `\g<nope>`, `\g<1`, `\`} {
		if _, err := parseTemplate(bad, regexp.MustCompile(`(x)`)); err == nil {
			t.Errorf("%s: no error", bad)
		}
	}
}

func TestReplaceLinesLeavesCodeAlone(t *testing.T) {
	re := regexp.MustCompile(regexp.QuoteMeta("BUG"))
	lit := func(string, []int) (string, error) { return "ONE", nil }
	text := "BUG and `BUG`\n```\nBUG\n```\nlast BUG"
	got, changed, _ := replaceLines(text, re, lit, false)
	if got != "ONE and `BUG`\n```\nBUG\n```\nlast ONE" || len(changed) != 2 || changed[1].number != 5 {
		t.Errorf("got %q, %+v", got, changed)
	}
	if got, _, _ := replaceLines(text, re, lit, true); strings.Contains(got, "BUG") {
		t.Errorf("include code: %q", got)
	}
	if got := pyPattern(`a\Z\d`); got != `a\z\d` {
		t.Errorf("pyPattern: %q", got)
	}
}
