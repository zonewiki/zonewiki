// zonewiki eval: how well retrieval (search_wiki) finds the right section for a
// question, measured on a wiki and a list of questions with their answers.
//
//	./run.sh eval [FILE]               in the app image, with the semantic model
//	ZONE_DATA=~/zonewiki zonewiki eval FILE    keywords only, unless ZONE_SEMANTIC_SEARCH is on
//
// FILE is JSON, a list of {"q": "question", "expect": [target, …]}. A target
// is a page ("people/alex"), a section ("people/alex#current-projects", the
// anchor in the section's URL) or a task ("task:<id>"); "note" is free text.
// A question is found at the rank of the first result matching one of its
// targets, a page matching any of its sections. A question with no targets
// yet is skipped (counted in the report): paste --new's stubs, fill them in
// over time. The report gives hit@1, hit@3, hit@k and MRR, by keywords
// alone and, with ZONE_SEMANTIC_SEARCH, hybrid, then the questions that
// weren't found first in every mode.
//
// --new lists the queries search_wiki was asked (the query log, which
// query-log in the settings file turns on) that FILE doesn't have yet, newest first, as
// stubs to paste into FILE: "results" is what the search found then, a hint
// for "expect", not the answer.
//
// Read-only: the semantic cache is read from the wiki; sections changed since
// are embedded into a temporary copy, never written back.
package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"
	"sort"
	"strings"
	"zonewiki/internal/jsonobj"
)

type question struct {
	Q      string
	Expect []string
}

// evalMain runs `zonewiki eval`; its exit code.
func evalMain(args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("zonewiki eval", flag.ContinueOnError)
	fs.SetOutput(stderr)
	k := fs.Int("k", 8, "`N` results per question, as search_wiki gives")
	newOnly := fs.Bool("new", false, "list logged search_wiki queries not in the file yet, as stubs to paste")
	floorFile := fs.String("floor", "", "`FILE` of the least each mode must reach (JSON); exit 1 when one falls under it")
	// The wiki stays as it is (Read-only, docs/spec/search.md).
	defer func(was bool) { writeIndexFiles = was }(writeIndexFiles)
	writeIndexFiles = false
	fs.Usage = func() {
		fmt.Fprintln(stderr, "Usage: zonewiki eval [--k N] [--new] [--floor FLOORS.json] QUESTIONS.json")
		printFlags(fs)
	}
	// Flags before or after the file.
	var files []string
	for {
		if err := fs.Parse(args); errors.Is(err, flag.ErrHelp) {
			return 0
		} else if err != nil {
			return 2
		}
		if fs.NArg() == 0 {
			break
		}
		files, args = append(files, fs.Arg(0)), fs.Args()[1:]
	}
	if len(files) != 1 {
		fs.Usage()
		return 2
	}
	fail := func(err error) int { fmt.Fprintln(stderr, err); return 1 }
	c, s, err := loadConfig(environ)
	if err != nil {
		return fail(err)
	}
	cfg = c
	current.Store(s)
	path := files[0]
	if *newOnly {
		log := queryLogPath()
		stubs, err := newQueries(path, []string{strings.TrimSuffix(log, ".jsonl") + ".1.jsonl", log})
		if err != nil {
			return fail(err)
		}
		if len(stubs) == 0 {
			fmt.Fprintln(stderr, "No new queries in the log (query-log = yes in .zone/config turns it on).")
			return 0
		}
		lines := make([]string, len(stubs))
		for i, s := range stubs {
			lines[i] = jsonobj.Text(s)
		}
		fmt.Fprintln(stdout, strings.Join(lines, ",\n"))
		return 0
	}
	questions, open, err := loadQuestions(path)
	if err != nil {
		return fail(err)
	}
	var floors evalFloors
	if *floorFile != "" {
		if floors, err = loadFloors(*floorFile); err != nil {
			return fail(err)
		}
	}
	bad := unknownTargets(questions)
	if len(bad) > 0 {
		fmt.Fprintln(stderr, "Not in the wiki:\n  "+strings.Join(bad, "\n  ")+"\n")
	}
	modes := []string{"keyword"}
	byMode := map[string][]int{}
	semantic = nil
	byMode["keyword"] = evalRun(questions, *k)
	if x := evalSemanticIndex(); x != nil {
		semantic = x
		modes = append(modes, "hybrid")
		byMode["hybrid"] = evalRun(questions, *k)
	}
	fmt.Fprintln(stdout, evalReport(questions, modes, byMode, *k))
	if open > 0 {
		fmt.Fprintf(stdout, "\n%d question(s) without targets skipped.\n", open)
	}
	if floors == nil {
		return 0
	}
	// Against a floor a target that names nothing is a failure: its
	// question would count as not found, and lower the numbers unseen.
	under, over := floors.check(modes, byMode, *k)
	if len(bad) > 0 {
		under = append(under, fmt.Sprintf("%d target(s) name nothing in the wiki", len(bad)))
	}
	if len(over) > 0 {
		fmt.Fprintln(stdout, "\nAbove the floor (raise it in "+filepath.Base(*floorFile)+", so it is kept):\n  "+strings.Join(over, "\n  "))
	}
	if len(under) > 0 {
		fmt.Fprintln(stdout, "\nUnder the floor ("+filepath.Base(*floorFile)+"):\n  "+strings.Join(under, "\n  "))
		return 1
	}
	fmt.Fprintln(stdout, "\nNo mode is under its floor.")
	return 0
}

// evalFloors: the least each mode must reach, by measure ("hit@1", "hit@3",
// "hit@8" as counts of questions, "MRR"), as a floor file has it:
// {"keyword": {"hit@1": 44, …}, "hybrid": {…}}. A change to chunking,
// ranking or the model that takes a mode under its floor is one that lost
// answers.
type evalFloors map[string]map[string]float64

func loadFloors(path string) (evalFloors, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("Can't read %s: %v", path, err)
	}
	var floors evalFloors
	if err := json.Unmarshal(b, &floors); err != nil || len(floors) == 0 {
		return nil, fmt.Errorf("%s: expected {\"keyword\": {\"hit@1\": N, …}, …}", path)
	}
	return floors, nil
}

// evalNumbers: evalSummary's measures as numbers: the hits as counts.
func evalNumbers(ranks []int, k int) map[string]float64 {
	out := map[string]float64{"hit@1": 0, "hit@3": 0, fmt.Sprintf("hit@%d", k): 0, "MRR": 0}
	for _, r := range ranks {
		if r <= 0 {
			continue
		}
		for _, m := range []int{1, 3, k} {
			if r <= m {
				out[fmt.Sprintf("hit@%d", m)]++
			}
		}
		out["MRR"] += 1 / float64(r)
	}
	if k == 1 || k == 3 { // counted twice above, as the same measure
		out[fmt.Sprintf("hit@%d", k)] /= 2
	}
	if len(ranks) > 0 {
		out["MRR"] /= float64(len(ranks))
	}
	return out
}

// check: what is under its floor, and what is above it, each a line. A
// mode with a floor that didn't run (hybrid without its model) is under it:
// a floor nobody measured holds nothing.
func (f evalFloors) check(modes []string, byMode map[string][]int, k int) (under, over []string) {
	for _, mode := range sortedKeys(f) {
		if !slices.Contains(modes, mode) {
			under = append(under, mode+" didn't run (semantic search needs ZONE_SEMANTIC_SEARCH and its model)")
			continue
		}
		got := evalNumbers(byMode[mode], k)
		for _, measure := range sortedKeys(f[mode]) {
			floor, have, known := f[mode][measure], got[measure], false
			if _, known = got[measure]; !known {
				under = append(under, fmt.Sprintf("%s %s: not a measure of this run (hit@1, hit@3, hit@%d, MRR)", mode, measure, k))
				continue
			}
			show := func(x float64) string {
				if measure == "MRR" {
					return fmt.Sprintf("%.2f", x)
				}
				return fmt.Sprintf("%.0f", x)
			}
			switch {
			case have < floor-1e-9:
				under = append(under, fmt.Sprintf("%s %s: %s, the floor is %s", mode, measure, show(have), show(floor)))
			case measure == "MRR" && have >= floor+0.005 || measure != "MRR" && have > floor:
				over = append(over, fmt.Sprintf("%s %s: %s, the floor is %s", mode, measure, show(have), show(floor)))
			}
		}
	}
	return under, over
}

// loadQuestions: the questions with targets, and how many have none yet.
func loadQuestions(path string) ([]question, int, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, 0, fmt.Errorf("Can't read %s: %v", path, err)
	}
	var data []any
	if err := json.Unmarshal(b, &data); err != nil {
		var raw any
		if json.Unmarshal(b, &raw) == nil {
			return nil, 0, fmt.Errorf("%s: expected a list of questions", path)
		}
		return nil, 0, fmt.Errorf("Can't read %s: %v", path, err)
	}
	var questions []question
	open := 0
	for i, item := range data {
		m, _ := item.(map[string]any)
		q, _ := m["q"].(string)
		if m == nil || strings.TrimSpace(q) == "" {
			return nil, 0, fmt.Errorf("%s: question %d needs a \"q\"", path, i+1)
		}
		var expect []string
		switch e := m["expect"].(type) {
		case nil:
		case string:
			if e != "" {
				expect = []string{e}
			}
		case []any:
			for _, t := range e {
				s, ok := t.(string)
				if !ok {
					return nil, 0, fmt.Errorf("%s: question %d: \"expect\" is a list of targets", path, i+1)
				}
				expect = append(expect, s)
			}
		default:
			return nil, 0, fmt.Errorf("%s: question %d: \"expect\" is a list of targets", path, i+1)
		}
		if len(expect) > 0 {
			questions = append(questions, question{strings.TrimSpace(q), expect})
		} else {
			open++
		}
	}
	return questions, open, nil
}

func normQuery(q string) string { return strings.Join(strings.Fields(strings.ToLower(q)), " ") }

// queryStub: a logged query as a question to fill in (its expect).
type queryStub struct {
	Q       string            `json:"q"`
	Expect  []string          `json:"expect"`
	Results []json.RawMessage `json:"results"`
}

// newQueries: logged queries the questions file doesn't have, newest first,
// each once, as stubs.
func newQueries(path string, logs []string) ([]queryStub, error) {
	known := map[string]bool{}
	if b, err := os.ReadFile(path); err == nil {
		var data []any
		if err := json.Unmarshal(b, &data); err != nil {
			return nil, fmt.Errorf("Can't read %s: %v", path, err)
		}
		for _, item := range data {
			if m, ok := item.(map[string]any); ok {
				if q, ok := m["q"].(string); ok {
					known[normQuery(q)] = true
				}
			}
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return nil, fmt.Errorf("Can't read %s: %v", path, err)
	}
	type entry struct {
		At      string            `json:"at"`
		Q       *string           `json:"q"`
		Results []json.RawMessage `json:"results"`
	}
	var entries []entry
	for _, log := range logs {
		b, err := os.ReadFile(log)
		if err != nil {
			continue
		}
		for _, line := range strings.Split(string(b), "\n") {
			var e entry
			if json.Unmarshal([]byte(line), &e) == nil && e.Q != nil {
				entries = append(entries, e)
			}
		}
	}
	sort.SliceStable(entries, func(i, j int) bool { return entries[i].At > entries[j].At })
	var stubs []queryStub
	for _, e := range entries {
		q := *e.Q
		if known[normQuery(q)] {
			continue
		}
		known[normQuery(q)] = true
		stubs = append(stubs, queryStub{Q: q, Expect: []string{},
			Results: append([]json.RawMessage{}, e.Results[:min(5, len(e.Results))]...)})
	}
	return stubs, nil
}

// matchesTarget: a result is the target: the task, the section, or a
// section of the page.
func matchesTarget(d *SearchDoc, target string) bool {
	if id, ok := strings.CutPrefix(target, "task:"); ok {
		return d.isTask() && d.taskID() == id
	}
	slug, anchor, _ := strings.Cut(target, "#")
	return !d.isTask() && d.Slug == slug && (anchor == "" || d.anchor() == "#"+anchor)
}

// rankOf: the 1-based rank of the first result matching a target, 0 if none does.
func rankOf(results []*SearchDoc, expect []string) int {
	for i, d := range results {
		for _, t := range expect {
			if matchesTarget(d, t) {
				return i + 1
			}
		}
	}
	return 0
}

// unknownTargets: targets naming no page, section or task of the wiki
// (typos, renames): such a question could never be found.
func unknownTargets(questions []question) []string {
	sections, slugs, tasks := map[string]bool{}, map[string]bool{}, map[string]bool{}
	for _, d := range chunkIndex(nil).all() {
		if d.isTask() {
			tasks[d.taskID()] = true
		} else {
			sections[d.Slug+"#"+strings.TrimPrefix(d.anchor(), "#")] = true
			slugs[d.Slug] = true
		}
	}
	var bad []string
	for _, q := range questions {
		for _, t := range q.Expect {
			var ok bool
			if id, isTask := strings.CutPrefix(t, "task:"); isTask {
				ok = tasks[id]
			} else if strings.Contains(t, "#") {
				ok = sections[t]
			} else {
				ok = slugs[t]
			}
			if !ok {
				bad = append(bad, t+"  (in: "+q.Q+")")
			}
		}
	}
	return bad
}

func evalRun(questions []question, k int) []int {
	ranks := make([]int, len(questions))
	for i, q := range questions {
		ranks[i] = rankOf(retrieve(nil, q.Q, k, "", false), q.Expect)
	}
	return ranks
}

// evalSummary: hit@1, hit@3, hit@k (unless k is 1 or 3) and MRR.
func evalSummary(ranks []int, k int) [][2]string {
	n := len(ranks)
	hits := func(m int) string {
		c := 0
		for _, r := range ranks {
			if r > 0 && r <= m {
				c++
			}
		}
		return fmt.Sprintf("%d/%d", c, n)
	}
	mrr := 0.0
	for _, r := range ranks {
		if r > 0 {
			mrr += 1 / float64(r)
		}
	}
	if n > 0 {
		mrr /= float64(n)
	}
	out := [][2]string{{"hit@1", hits(1)}, {"hit@3", hits(3)}}
	if k != 1 && k != 3 {
		out = append(out, [2]string{fmt.Sprintf("hit@%d", k), hits(k)})
	}
	return append(out, [2]string{"MRR", fmt.Sprintf("%.2f", mrr)})
}

// evalSemanticIndex: the wiki's semantic index, brought up to date in a
// temporary cache; nil without ZONE_SEMANTIC_SEARCH or its model (the log
// says why).
func evalSemanticIndex() *semanticIndex {
	x := semanticFromEnv(environ)
	if x == nil {
		return nil
	}
	dir, err := os.MkdirTemp("", "zonewiki-eval-")
	if err != nil {
		semLog("%v", err)
		return nil
	}
	x.cachePath = filepath.Join(dir, "embeddings.json")
	x.sync()
	return x
}

func evalReport(questions []question, modes []string, byMode map[string][]int, k int) string {
	stats := map[string][][2]string{}
	for _, m := range modes {
		stats[m] = evalSummary(byMode[m], k)
	}
	head := fmt.Sprintf("%8s", "")
	for _, m := range modes {
		head += fmt.Sprintf("%10s", m)
	}
	lines := []string{head}
	for i, kv := range stats[modes[0]] {
		line := fmt.Sprintf("%-8s", kv[0])
		for _, m := range modes {
			line += fmt.Sprintf("%10s", stats[m][i][1])
		}
		lines = append(lines, line)
	}
	var rows []int
	for i := range questions {
		for _, m := range modes {
			if byMode[m][i] != 1 {
				rows = append(rows, i)
				break
			}
		}
	}
	if len(rows) > 0 {
		head := ""
		for _, m := range modes {
			head += fmt.Sprintf("%8s", m)
		}
		lines = append(lines, "", fmt.Sprintf("Not first (rank, - = not in the top %d):", k), head+"  question")
		for _, i := range rows {
			line := ""
			for _, m := range modes {
				r := "-"
				if byMode[m][i] > 0 {
					r = itoa(byMode[m][i])
				}
				line += fmt.Sprintf("%8s", r)
			}
			lines = append(lines, line+"  "+questions[i].Q)
		}
	}
	return strings.Join(lines, "\n")
}
