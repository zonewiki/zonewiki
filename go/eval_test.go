package main

import (
	"bytes"
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"zonewiki/internal/jsonobj"
)

// zonewiki eval (eval.go): ranks, targets, the report, and that it leaves the
// wiki alone.

func evalWiki(t *testing.T) {
	testWiki(t, map[string]string{
		"golf":       "# Golf\n\n## Werkstatt\n\nTermin beim Mechaniker, Bremsen quietschen.\n\n## Reifen\n\nWinterreifen im Keller.\n",
		"git-remote": "# Git remote\n\nSet ZONE_GIT_REMOTE, then push to copy every commit.\n",
	})
	old := semantic
	semantic = nil
	t.Cleanup(func() { semantic = old })
}

// A page target matches any of its sections, a section target only its own.
func TestEvalRank(t *testing.T) {
	evalWiki(t)
	byHeading := map[string]*SearchDoc{}
	for _, d := range docsOf(chunkIndex(nil)) {
		if d.Slug == "golf" {
			byHeading[d.heading()] = d
		}
	}
	results := []*SearchDoc{byHeading["Reifen"], byHeading["Werkstatt"]}
	if results[0] == nil || results[1] == nil || results[0].anchor() != "#reifen" {
		t.Fatalf("golf's sections: %v", byHeading)
	}
	for _, c := range []struct {
		expect []string
		want   int
	}{{[]string{"golf"}, 1}, {[]string{"golf#werkstatt"}, 2}, {[]string{"golf#bremsen", "other"}, 0}} {
		if got := rankOf(results, c.expect); got != c.want {
			t.Errorf("%q: rank %d, want %d", c.expect, got, c.want)
		}
	}
	task := &SearchDoc{Slug: "_tasks", Task: &Task{ID: "t1"}}
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
	if err := os.MkdirAll(filepath.Join(cfg.Data, "_tasks"), 0o777); err != nil {
		t.Fatal(err)
	}
	_ = os.WriteFile(taskFiles.OpenPath(""), []byte(`[{"id": "t1", "title": "Reifen wechseln", "tags": []}]`), 0o666)
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
	if err != nil || len(stubs) != 2 || stubs[1].Q != "Winter tyres" ||
		jsonobj.Text(stubs[0]) != `{"q":"git push","expect":[],"results":["r0","r1","r2","r3","r4"]}` {
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
	t.Setenv("ZONE_DATA", cfg.Data)
	t.Setenv("ZONE_SEMANTIC_SEARCH", "")
	f := filepath.Join(t.TempDir(), "q.json")
	_ = os.WriteFile(f, []byte(`[{"q": "Mechaniker", "expect": "golf"}, {"q": "push", "expect": "nope"}, {"q": "open"}]`), 0o666)
	var out, errOut bytes.Buffer
	if code := evalMain([]string{f, "--k", "3"}, &out, &errOut); code != 0 {
		t.Fatalf("exit %d: %s", code, errOut.String())
	}
	expectIn(t, out.String(), "           keyword\nhit@1          1/2\nhit@3          1/2\nMRR           0.50\n",
		"1 question(s) without targets skipped.")
	expectIn(t, errOut.String(), "Not in the wiki:\n  nope  (in: push)")
}

func TestEvalFloors(t *testing.T) {
	// Ranks of five questions: first, third, eighth, not found, first.
	byMode := map[string][]int{"keyword": {1, 3, 8, 0, 1}}
	got := evalNumbers(byMode["keyword"], 8)
	if got["hit@1"] != 2 || got["hit@3"] != 3 || got["hit@8"] != 4 || math.Abs(got["MRR"]-(1+1.0/3+1.0/8+1)/5) > 1e-9 {
		t.Errorf("numbers: %v", got)
	}
	if n := evalNumbers([]int{1, 2, 0}, 3); n["hit@1"] != 1 || n["hit@3"] != 2 {
		t.Errorf("with k 3: %v", n)
	}
	for _, c := range []struct {
		floors      evalFloors
		under, over string
	}{
		{evalFloors{"keyword": {"hit@1": 2, "hit@3": 3, "hit@8": 4, "MRR": 0.49}}, "", ""},
		{evalFloors{"keyword": {"hit@1": 3}}, "keyword hit@1: 2, the floor is 3", ""},
		{evalFloors{"keyword": {"hit@1": 1, "MRR": 0.60}}, "keyword MRR: 0.49, the floor is 0.60", "keyword hit@1: 2, the floor is 1"},
		{evalFloors{"keyword": {"hit@5": 1}}, "keyword hit@5: not a measure of this run (hit@1, hit@3, hit@8, MRR)", ""},
		{evalFloors{"hybrid": {"hit@1": 1}}, "hybrid didn't run (semantic search needs ZONE_SEMANTIC_SEARCH and its model)", ""},
	} {
		under, over := c.floors.check([]string{"keyword"}, byMode, 8)
		if strings.Join(under, "; ") != c.under || strings.Join(over, "; ") != c.over {
			t.Errorf("%v:\n under %q\n over  %q", c.floors, under, over)
		}
	}
}

func TestEvalCommandHoldsAFloor(t *testing.T) {
	evalWiki(t)
	t.Setenv("ZONE_DATA", cfg.Data)
	t.Setenv("ZONE_SEMANTIC_SEARCH", "")
	dir := t.TempDir()
	q, floor := filepath.Join(dir, "q.json"), filepath.Join(dir, "floors.json")
	_ = os.WriteFile(q, []byte(`[{"q": "Mechaniker", "expect": "golf"}, {"q": "push commits", "expect": "git-remote"}]`), 0o666)
	run := func(floors string) (int, string) {
		_ = os.WriteFile(floor, []byte(floors), 0o666)
		var out, errOut bytes.Buffer
		code := evalMain([]string{"--floor", floor, q}, &out, &errOut)
		return code, out.String() + errOut.String()
	}
	if code, out := run(`{"keyword": {"hit@1": 2, "MRR": 1}}`); code != 0 || !strings.Contains(out, "No mode is under its floor.") {
		t.Errorf("at the floor: exit %d\n%s", code, out)
	}
	if code, out := run(`{"keyword": {"hit@1": 1}}`); code != 0 || !strings.Contains(out, "Above the floor (raise it in floors.json, so it is kept):\n  keyword hit@1: 2, the floor is 1") {
		t.Errorf("above the floor: exit %d\n%s", code, out)
	}
	if code, out := run(`{"keyword": {"hit@1": 3}}`); code != 1 || !strings.Contains(out, "Under the floor (floors.json):\n  keyword hit@1: 2, the floor is 3") {
		t.Errorf("under the floor: exit %d\n%s", code, out)
	}
	if code, out := run(`{"keyword": {"hit@1": 2}, "hybrid": {"hit@1": 1}}`); code != 1 || !strings.Contains(out, "hybrid didn't run") {
		t.Errorf("a mode that didn't run: exit %d\n%s", code, out)
	}
	if code, out := run(`[]`); code != 1 || !strings.Contains(out, "expected {") {
		t.Errorf("no floors: exit %d\n%s", code, out)
	}
	// A target that names nothing would lower the numbers unseen: a failure.
	_ = os.WriteFile(q, []byte(`[{"q": "Mechaniker", "expect": "golf"}, {"q": "push commits", "expect": "nope"}]`), 0o666)
	if code, out := run(`{"keyword": {"hit@1": 1}}`); code != 1 || !strings.Contains(out, "1 target(s) name nothing in the wiki") {
		t.Errorf("an unknown target: exit %d\n%s", code, out)
	}
}

// The sample wiki's floor (tests/retrieval: a household's and a workshop's
// pages and tasks, questions asked in other words than the pages', by id
// and name, in German and English and across the two): search by keywords
// finds what it found when the floor was set. A change to chunking, the
// tokens or the ranking that loses an answer fails here, in ./run.sh test;
// the hybrid ranking's floor needs the model (./run.sh retrieval).
func TestRetrievalHoldsItsFloor(t *testing.T) {
	src := filepath.Join("..", "tests", "retrieval")
	wiki := filepath.Join(t.TempDir(), "wiki")
	if err := os.CopyFS(wiki, os.DirFS(filepath.Join(src, "wiki"))); err != nil {
		t.Fatal(err)
	}
	b, err := os.ReadFile(filepath.Join(src, "floors.json"))
	if err != nil {
		t.Fatal(err)
	}
	var floors evalFloors
	if err := json.Unmarshal(b, &floors); err != nil || floors["keyword"] == nil || floors["hybrid"] == nil {
		t.Fatalf("floors.json: %v, with keyword and hybrid", err)
	}
	keyword, _ := json.Marshal(evalFloors{"keyword": floors["keyword"]})
	floor := filepath.Join(t.TempDir(), "floors.json")
	_ = os.WriteFile(floor, keyword, 0o666)
	old, oldSettings := cfg, settings()
	t.Cleanup(func() { cfg = old; current.Store(oldSettings); restartSearch() })
	restartSearch()
	t.Setenv("ZONE_DATA", wiki)
	t.Setenv("ZONE_SEMANTIC_SEARCH", "")
	var out, errOut bytes.Buffer
	code := evalMain([]string{"--floor", floor, filepath.Join(src, "questions.json")}, &out, &errOut)
	if code != 0 {
		t.Errorf("exit %d\n%s%s", code, out.String(), errOut.String())
	}
	// The floor is what was measured: a ranking that got better raises it, so the gain is kept.
	if strings.Contains(out.String(), "Above the floor") {
		t.Errorf("search by keywords got better than its floor: raise it in tests/retrieval/floors.json\n%s", out.String())
	}
}
