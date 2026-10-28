package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"
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
func replyText(t *testing.T, msg string, call func(string, jsonObj) (string, error)) string {
	t.Helper()
	reply, err := handleMCP(json.RawMessage(msg), "", call)
	if err != nil {
		t.Fatal(err)
	}
	return jsonText(reply)
}

// handle: the reply to msg, as JSON reads back (nil for none).
func handle(t *testing.T, msg string, call func(string, jsonObj) (string, error)) map[string]any {
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

func noTools(string, jsonObj) (string, error) { panic("not called") }

func TestMCPProtocol(t *testing.T) {
	testWiki(t, nil)
	r := handle(t, `{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}`, noTools)
	instructions, _ := field(r, "result", "instructions").(string)
	if field(r, "result", "protocolVersion") != "2025-06-18" || field(r, "result", "serverInfo", "version") != version ||
		!strings.Contains(instructions, "search_wiki") {
		t.Errorf("initialize: %s", jsonText(r))
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
	fail := func(string, jsonObj) (string, error) { return "", toolErr("No page 'x'") }
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
		if _, ok := mcpToolFuncs[name]; !ok {
			t.Errorf("%s has no function", name)
		}
	}
	if len(names) != len(mcpToolFuncs) || names[0] != "search_wiki" || names[len(names)-1] != "update_task" {
		t.Errorf("tools: %q", names)
	}
	if len(mcpToolsJSON) > 30000 {
		t.Errorf("the definitions grew to %d bytes: every session loads them", len(mcpToolsJSON))
	}
}

func TestLongGuidesAreCut(t *testing.T) {
	testWiki(t, map[string]string{"wiki-guide": "# Wiki guide\n\n" + strings.Repeat("x", 100),
		"tickets/CLAUDE": "# Tickets\n\nOne folder per ticket.\n", "tickets/bug-1": "# Bug\n"})
	set(t, &guideChars, 20)
	if got := instructionsWithGuide(); !strings.HasSuffix(got, "\n\n# Wiki guide\n\nxxxxxx\n\n[… cut here: read_page \"wiki-guide\" for the rest]") {
		t.Errorf("instructions: %q", got[len(got)-100:])
	}
	set(t, &folderGuideChars, 10)
	err := checkGuides(jsonObj{}, []string{"tickets/bug-1"}, "")
	if err == nil || !strings.Contains(err.Error(), "# Tickets\n\n\n[… cut here: read_page \"tickets/CLAUDE\" for the rest]") {
		t.Errorf("refusal: %v", err)
	}
}

func TestToolListNamesGuidedFolders(t *testing.T) {
	testWiki(t, nil)
	desc := func() string {
		for _, tool := range toolList("") {
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
	if got := mcpPageLinks("golf", "# Golf\n", ""); !strings.HasSuffix(got, "Linked from (3): c, d, … and 1 more") {
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

// A pattern that backtracks for ages in Python (Python ran it in a process
// of its own, killed after a timeout) is linear in RE2.
func TestCatastrophicRegexIsFast(t *testing.T) {
	testWiki(t, map[string]string{"slow": "# Slow\n\n" + strings.Repeat("a", 5000) + "!\n"})
	start := time.Now()
	args, _ := parseObj([]byte(`{"find": "(a+)+$", "replace": "x", "regex": true, "dry_run": false}`))
	out, err := replaceInPages(args, "", "")
	if err != nil || out != `Nothing matches "(a+)+$" outside code.` || time.Since(start) > 2*time.Second {
		t.Errorf("%q %v in %v", out, err, time.Since(start))
	}
}

func TestGrepTimeout(t *testing.T) {
	testWiki(t, map[string]string{"a": "# A\n"})
	cfg.GitEnabled = true
	set(t, &grepTimeout, time.Nanosecond)
	if _, err := grepPages("(a+)+$", false, nil); err == nil || !strings.Contains(err.Error(), "took longer than") {
		t.Errorf("err %v", err)
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
