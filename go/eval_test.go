package main

import (
	"bytes"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

// leaf eval (eval.go): ranks, targets, the report, and that it leaves the
// wiki alone.

func evalWiki(t *testing.T) {
	testWiki(t, map[string]string{
		"golf":       "# Golf\n\n## Werkstatt\n\nTermin beim Mechaniker, Bremsen quietschen.\n\n## Reifen\n\nWinterreifen im Keller.\n",
		"git-remote": "# Git remote\n\nSet LEAF_GIT_REMOTE, then push to copy every commit.\n",
	})
	old := semantic
	semantic = nil
	t.Cleanup(func() { semantic = old })
}

// A page target matches any of its sections, a section target only its own.
func TestEvalRank(t *testing.T) {
	results := []*SearchDoc{{Slug: "golf", Anchor: "#reifen"}, {Slug: "golf", Anchor: "#werkstatt"}}
	for _, c := range []struct {
		expect []string
		want   int
	}{{[]string{"golf"}, 1}, {[]string{"golf#werkstatt"}, 2}, {[]string{"golf#bremsen", "other"}, 0}} {
		if got := rankOf(results, c.expect); got != c.want {
			t.Errorf("%q: rank %d, want %d", c.expect, got, c.want)
		}
	}
	task := &SearchDoc{Slug: "_deck", Task: &Task{ID: "t1"}, TaskID: "t1"}
	if rankOf([]*SearchDoc{task}, []string{"task:t1"}) != 1 {
		t.Error("task target")
	}
}

func TestEvalRunRanksByKeywords(t *testing.T) {
	evalWiki(t)
	got := evalRun([]question{{"Mechaniker Termin", []string{"golf#werkstatt"}},
		{"Winterreifen", []string{"golf#werkstatt"}}, {"push commits", []string{"git-remote"}}}, 8)
	if !reflect.DeepEqual(got, []int{1, 0, 1}) {
		t.Errorf("ranks %v", got)
	}
}

func TestEvalUnknownTargets(t *testing.T) {
	evalWiki(t)
	if err := os.MkdirAll(filepath.Join(cfg.Data, "_deck"), 0o777); err != nil {
		t.Fatal(err)
	}
	_ = os.WriteFile(deckFile(), []byte(`[{"id": "t1", "title": "Reifen wechseln", "tags": []}]`), 0o666)
	bad := unknownTargets([]question{{"a", []string{"golf", "golf#werkstatt", "task:t1"}},
		{"b", []string{"golfe", "golf#bremsen", "task:nope"}}})
	var first []string
	for _, b := range bad {
		first = append(first, strings.Fields(b)[0])
	}
	if !reflect.DeepEqual(first, []string{"golfe", "golf#bremsen", "task:nope"}) {
		t.Errorf("unknown %q", bad)
	}
}

func TestEvalReport(t *testing.T) {
	out := evalReport([]question{{Q: "one"}, {Q: "two"}, {Q: "three"}}, []string{"keyword", "hybrid"},
		map[string][]int{"keyword": {1, 3, 0}, "hybrid": {1, 1, 2}}, 8)
	expectIn(t, out, "hit@1          1/3       2/3", "MRR           0.44      0.83", "       3       1  two", "       -       2  three")
	if strings.Contains(strings.Split(out, "Not first")[1], "one") {
		t.Errorf("a question first in every mode listed:\n%s", out)
	}
}

func TestEvalLoadQuestions(t *testing.T) {
	f := filepath.Join(t.TempDir(), "q.json")
	_ = os.WriteFile(f, []byte(`[{"q": " Bremsen ", "expect": "golf", "note": "x"},
		{"q": "Reifen", "expect": [], "results": ["golf#reifen"]}, {"q": "Öl"}]`), 0o666)
	qs, open, err := loadQuestions(f)
	if err != nil || open != 2 || !reflect.DeepEqual(qs, []question{{"Bremsen", []string{"golf"}}}) {
		t.Errorf("%v %d %v", qs, open, err)
	}
	_ = os.WriteFile(f, []byte(`[{"expect": ["golf"]}]`), 0o666)
	if _, _, err := loadQuestions(f); err == nil || !strings.Contains(err.Error(), "question 1") {
		t.Errorf("no q: %v", err)
	}
}

// Logged queries not in the file yet, newest first, each once.
func TestEvalNewQueries(t *testing.T) {
	dir := t.TempDir()
	f := filepath.Join(dir, "q.json")
	_ = os.WriteFile(f, []byte(`[{"q": "Golf  Bremsen", "expect": ["golf"]}]`), 0o666)
	old, log := filepath.Join(dir, "queries.1.jsonl"), filepath.Join(dir, "queries.jsonl")
	_ = os.WriteFile(old, []byte(`{"at": "2026-10-01T09:00:00", "q": "winter tyres", "results": ["golf#reifen"]}`+"\n"), 0o666)
	_ = os.WriteFile(log, []byte(`{"at": "2026-10-02T09:00:00", "q": "golf bremsen", "results": []}
{"at": "2026-10-03T09:00:00", "q": "Winter tyres", "results": []}
{"at": "2026-10-04T09:00:00", "q": "git push", "results": ["r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8"]}
not json
`), 0o666)
	stubs, err := newQueries(f, []string{old, log, filepath.Join(dir, "missing.jsonl")})
	if err != nil || len(stubs) != 2 || stubs[1].Str("q") != "Winter tyres" ||
		jsonText(stubs[0]) != `{"q":"git push","expect":[],"results":["r0","r1","r2","r3","r4"]}` {
		t.Errorf("%v %v", stubs, err)
	}
}

// The semantic index is read from the wiki's cache, written elsewhere.
func TestEvalNeverWritesTheWikisCache(t *testing.T) {
	evalWiki(t)
	x := testIndex(newRecorder(), nil)
	live := x.cachePath
	dir := t.TempDir()
	x.cachePath = filepath.Join(dir, "embeddings.json")
	x.sync()
	if exists(live) || !exists(x.cachePath) {
		t.Error("the wiki's cache was written")
	}
}

func TestEvalCommand(t *testing.T) {
	evalWiki(t)
	t.Setenv("LEAF_DATA", cfg.Data)
	t.Setenv("LEAF_SEMANTIC_SEARCH", "")
	f := filepath.Join(t.TempDir(), "q.json")
	_ = os.WriteFile(f, []byte(`[{"q": "Mechaniker", "expect": "golf"}, {"q": "push", "expect": "nope"}, {"q": "open"}]`), 0o666)
	var out, errOut bytes.Buffer
	if code := evalMain([]string{f, "-k", "3"}, &out, &errOut); code != 0 {
		t.Fatalf("exit %d: %s", code, errOut.String())
	}
	expectIn(t, out.String(), "           keyword\nhit@1          1/2\nhit@3          1/2\nMRR           0.50\n",
		"1 question(s) without targets skipped.")
	expectIn(t, errOut.String(), "Not in the wiki:\n  nope  (in: push)")
}
