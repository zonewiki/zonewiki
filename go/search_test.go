package main

import (
	"fmt"
	"math"
	"math/rand"
	"reflect"
	"slices"
	"sort"
	"strings"
	"testing"
)

// Search ranking (search.go): stemming, folding, fuzzy matches, excerpts,
// the query syntax. The expected values of the first tests are Python's
// (search.py and difflib, which search.go ported).

func TestStem(t *testing.T) {
	for w, want := range map[string]string{
		"running": "run", "runs": "run", "called": "call", "passed": "pass", "added": "add", "speed": "speed",
		"agreed": "agreed", "string": "string", "spring": "spring", "databases": "database", "ponies": "pony",
		"caress": "caress", "hopping": "hop", "fizzed": "fizz", "bed": "bed", "cats": "cat", "gas": "gas", "plays": "play",
	} {
		if got := stem(w); got != want {
			t.Errorf("stem(%q) = %q, want %q", w, got, want)
		}
	}
}

func TestFold(t *testing.T) {
	for w, want := range map[string]string{
		"Müller": "muller", "MÜLLER": "muller", "Straße": "strasse", "naïve": "naive", "ﬁle": "file",
		"Ångström": "angstrom", "½": "1⁄2", "øre": "øre", "İstanbul": "istanbul",
	} {
		if got := fold(w); got != want {
			t.Errorf("fold(%q) = %q, want %q", w, got, want)
		}
	}
}

func TestTokenize(t *testing.T) {
	got := tokenize("The Müller's running-dogs [link](http://x.org/page) ![img](a.png) 日本語 x_y")
	want := []string{"the", "muller", "s", "run", "dog", "link", "http", "x", "org", "page", "img", "a", "png", "日本語", "x", "y"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("got %q", got)
	}
}

func TestCloseMatches(t *testing.T) {
	poss := []string{"apple", "apply", "ample", "maple", "applet", "appel"}
	want := map[string][]string{
		"appel": {"appel", "apply", "apple"}, "aple": {"maple", "apple", "ample"}, "applt": {"applet", "apply", "apple"},
	}
	for w, ws := range want {
		if got := closeMatches(w, poss, 3, 0.6); !reflect.DeepEqual(got, ws) {
			t.Errorf("%q: got %q, want %q", w, got, ws)
		}
	}
}

func TestParseQuery(t *testing.T) {
	q := ParseQuery(`"Exact Phrase" word -not -"bad one" #Tag -#gone edited:2026-09 -after:2026-01 BEFORE:2027`)
	want := &Query{Text: "Exact Phrase word", Phrases: []string{"exact phrase"}, ExcludePhrases: []string{"bad one"},
		ExcludeStems: []string{"not"}, Tags: []string{"tag"}, ExcludeTags: []string{"gone"},
		Dates: []dateFilter{{"edited", "2026-09"}, {"before", "2026-01"}, {"before", "2027"}}}
	if !reflect.DeepEqual(q, want) {
		t.Errorf("got %+v", q)
	}
}

func TestExcerptAndMatchedWords(t *testing.T) {
	text := "Intro line.\nWe were running late & the <dogs> ran. Running again; runners run. "
	for i := 0; i < 80; i++ {
		text += "x "
	}
	text += "end runs"
	xs := func(n int) string {
		s := ""
		for i := 0; i < n; i++ {
			s += "x "
		}
		return s
	}
	cases := map[string]string{
		"run":          "Intro line. We were <mark>running</mark> late &amp; the &lt;dogs&gt; ran. <mark>Running</mark> again; <mark>runners</mark> <mark>run</mark>. " + xs(19) + "…",
		"running late": "Intro line. We were <mark>running late</mark> &amp; the &lt;dogs&gt; ran. Running again; runners run. " + xs(21) + "x…",
		"runnign":      "Intro line. We were running late &amp; the &lt;dogs&gt; ran. Running again; runners run. " + xs(35) + "x…",
	}
	for q, want := range cases {
		if got := Excerpt(text, q); got != want {
			t.Errorf("%q:\n got %q\nwant %q", q, got, want)
		}
	}
	if got := Excerpt("nothing here", "zebra"); got != "nothing here" {
		t.Errorf("no match: %q", got)
	}
	if got := MatchedWords(text, "run", 10); !reflect.DeepEqual(got, []string{"running", "runners", "run", "runs"}) {
		t.Errorf("matched: %q", got)
	}
	if got := MatchedWords(text, "Running  late", 10); !reflect.DeepEqual(got, []string{"running late"}) {
		t.Errorf("phrase: %q", got)
	}
}

func TestSearchRanksTitleAndTypos(t *testing.T) {
	doc := func(text, title string) []string {
		toks := tokenize(text)
		for i := 0; i < titleWeight; i++ {
			toks = append(toks, tokenize(title)...)
		}
		return toks
	}
	ix := NewIndex([][]string{
		doc("a page about gardening and beds", "Garden"),
		doc("the garden is mentioned once", "Notes"),
		doc("nothing relevant", "Other"),
	})
	hits := ix.Search("garden", 10)
	if len(hits) != 2 || hits[0].Doc != 0 || hits[1].Doc != 1 {
		t.Errorf("garden: %+v", hits)
	}
	if hits := ix.Search("gardne", 10); len(hits) == 0 || hits[0].Doc != 0 { // a typo finds it
		t.Errorf("typo: %+v", hits)
	}
}

// ---- the cases of Zone's search tests ----

type testDoc struct{ slug, title, text string }

// ranked: the slugs a search finds, best first (titles weigh as in Zone).
func ranked(docs []testDoc, query string) []string {
	var toks [][]string
	for _, d := range docs {
		ts := tokenize(d.text)
		for i := 0; i < titleWeight; i++ {
			ts = append(ts, tokenize(d.title)...)
		}
		toks = append(toks, ts)
	}
	var out []string
	for _, h := range NewIndex(toks).Search(query, 15) {
		out = append(out, docs[h.Doc].slug)
	}
	return out
}

func expectRanked(t *testing.T, docs []testDoc, query string, want ...string) {
	t.Helper()
	if got := ranked(docs, query); !reflect.DeepEqual(got, want) {
		t.Errorf("%q found %q, want %q", query, got, want)
	}
}

func TestStemCollapsesForms(t *testing.T) {
	for _, forms := range [][]string{
		{"database", "databases"}, {"run", "running"}, {"stop", "stopped"}, {"study", "studies"},
		{"call", "called", "calling"}, {"pass", "passed", "passes"}, {"fill", "filled"}, {"buzz", "buzzing"},
		{"add", "added"}, {"string", "strings"}, {"speed", "speeds"},
	} {
		for _, f := range forms[1:] {
			if stem(f) != stem(forms[0]) {
				t.Errorf("stem(%q) = %q, stem(%q) = %q", f, stem(f), forms[0], stem(forms[0]))
			}
		}
	}
	// Short words, ss, a base without a vowel stay as they are.
	for _, w := range []string{"cat", "is", "class", "spring", "string", "call", "pass", "add", "speed", "run", "stop"} {
		if stem(w) != w {
			t.Errorf("stem(%q) = %q", w, stem(w))
		}
	}
	if got := tokenize("Running Tests, quickly!"); !reflect.DeepEqual(got, []string{"run", "test", "quickly"}) {
		t.Errorf("tokenize: %q", got)
	}
	// An inflection matches its base at about full weight.
	var a, b float64
	toks := [][]string{tokenize("We called him"), tokenize("call me maybe")}
	for _, h := range NewIndex(toks).Search("call", 10) {
		if h.Doc == 0 {
			a = h.Score
		} else {
			b = h.Score
		}
	}
	if a == 0 || b == 0 || a/b < 0.8 || a/b > 1.2 {
		t.Errorf("called %v, call %v", a, b)
	}
}

func TestStripLinkTargets(t *testing.T) {
	for in, want := range map[string]string{
		"See [Foo](/page/foo).":                            "See [Foo].",
		`![pic](/_assets/a.png "A")`:                       "![pic]",
		"[W](https://en.wikipedia.org/wiki/A_(b)) after":   "[W] after",
		`[ref]: https://x.org "T"`:                         "[ref]:",
		"go to https://example.com or [[some page|Label]]": "go to https://example.com or [[some page|Label]]",
	} {
		if got := stripLinkTargets(in); got != want {
			t.Errorf("stripLinkTargets(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestUnicodeWords(t *testing.T) {
	if got := tokenize("Müller"); !reflect.DeepEqual(got, []string{"muller"}) {
		t.Errorf("Müller: %q", got)
	}
	if !reflect.DeepEqual(tokenize("Café MÜLLER"), tokenize("cafe muller")) || !reflect.DeepEqual(tokenize("Straße"), tokenize("strasse")) {
		t.Error("accents, case and ß fold")
	}
	if got := tokenize("foo_bar"); !reflect.DeepEqual(got, []string{"foo", "bar"}) {
		t.Errorf("underscore: %q", got)
	}
	docs := []testDoc{{"a", "A", "Notizen zur Größe"}, {"b", "B", "other text"}}
	expectRanked(t, docs, "grö", "a")
	expectRanked(t, docs, "gro", "a")
	expectRanked(t, []testDoc{{"a", "A", "Die Straße"}, {"b", "B", "other text"}}, "e")
	expectRanked(t, []testDoc{{"a", "A", "Ein Besuch im Café"}, {"b", "B", "other text"}}, "cafe", "a")
}

func TestRanking(t *testing.T) {
	if got := ranked([]testDoc{{"body", "Other", "the token appears once here"}, {"title", "token", "unrelated body content"}}, "token"); got[0] != "title" {
		t.Errorf("title match ranks first: %q", got)
	}
	// Words of the query anywhere, not as a phrase.
	expectRanked(t, []testDoc{{"a", "Alpha", "the vector lives here and the database elsewhere"}, {"b", "Beta", "nothing relevant at all"}},
		"vector database", "a")
	var docs []testDoc
	for i := 0; i < 5; i++ {
		docs = append(docs, testDoc{"common" + itoa(i), "Doc " + itoa(i), "the the the the the ordinary text"})
	}
	docs = append(docs, testDoc{"rare", "Doc rare", "the quasar text"})
	if got := ranked(docs, "the quasar"); got[0] != "rare" {
		t.Errorf("the rarer word wins: %q", got)
	}
	one := []testDoc{{"a", "Alpha", "some content"}}
	expectRanked(t, one, "zzzznomatch")
	expectRanked(t, one, "   ")
	expectRanked(t, []testDoc{{"a", "A", "apple pie"}, {"b", "B", "banana"}, {"c", "C", "apple tart"}}, "apple", "a", "c")
	expectRanked(t, []testDoc{{"x", "T", "same words here"}, {"y", "T", "same words here"}, {"z", "T", "same words here"}}, "words", "x", "y", "z")
}

func TestFuzzyAndPrefixes(t *testing.T) {
	expectRanked(t, []testDoc{{"db", "Database notes", "everything about the database engine"}, {"x", "Other", "unrelated"}}, "databse", "db")
	expectRanked(t, []testDoc{{"db", "Notes", "the database engine internals"}, {"x", "Other", "unrelated content"}}, "datab", "db")
	got := ranked([]testDoc{{"a", "A", "databank"}, {"b", "B", "datum dat"}, {"c", "C", "database"}, {"d", "D", "update"}}, "data")
	sort.Strings(got)
	if !reflect.DeepEqual(got, []string{"a", "c"}) {
		t.Errorf("prefix data: %q", got)
	}
	expectRanked(t, []testDoc{{"r", "Notes", "I was running in the park"}, {"x", "Other", "unrelated content"}}, "run", "r")
}

func TestExcerptCases(t *testing.T) {
	in := func(text, query string, want ...string) {
		t.Helper()
		expectIn(t, Excerpt(text, query), want...)
	}
	in("see the findme_content marker here", "findme_content", "<mark>findme_content</mark>")
	in("alpha beta gamma delta", "gamma epsilon", "<mark>gamma</mark>")
	in("a <script> tag near findme here", "findme", "&lt;script&gt;")
	expectNot(t, Excerpt("a <script> tag near findme here", "findme"), "<script>")
	if got := Excerpt("some leading content", "nomatchword"); got != "some leading content" {
		t.Errorf("no match: %q", got)
	}
	in("He runs every morning", "running", "<mark>runs</mark>")
	if got := Excerpt("restart the art show", "art"); got != "restart the <mark>art</mark> show" {
		t.Errorf("whole words: %q", got)
	}
	in("set up the database", "data", "<mark>database</mark>")
	in("notes on the kubernetes cluster", "kubernets", "<mark>kubernetes</mark>")
	in("Termin mit Herrn Müller", "muller", "<mark>Müller</mark>")
	in("one two. Alpha Beta here", "alpha beta", "<mark>Alpha Beta</mark>")
	if n := strings.Count(Excerpt("a cat and another cat", "cat"), "<mark>cat</mark>"); n != 2 {
		t.Errorf("%d cats marked", n)
	}
	long := Excerpt(strings.Repeat("x ", 200)+"needle"+strings.Repeat(" y", 200), "needle")
	if !strings.HasPrefix(long, "…") || !strings.HasSuffix(long, "…") || !strings.Contains(long, "<mark>needle</mark>") {
		t.Errorf("window: %q", long)
	}
	in("Notes on the database migration.", "databse", "<mark>database</mark>")
	if got := MatchedWords("Two runs, one ran, many reruns.", "running", 10); !reflect.DeepEqual(got, []string{"runs"}) {
		t.Errorf("matched %q", got)
	}
}

func TestStopwords(t *testing.T) {
	for q, want := range map[string][]string{
		"When is the car going to the Werkstatt?": {"car", "going", "werkstatt"},
		"Termin für das Auto":                     {"termin", "auto"},
		"what is this":                            {"what", "is", "this"}, // all stopwords: kept
	} {
		if got := queryWords(q); !reflect.DeepEqual(got, want) {
			t.Errorf("queryWords(%q) = %q, want %q", q, got, want)
		}
	}
	expectRanked(t, []testDoc{{"filler", "Notes", "the the is to the in"}, {"car", "Car", "car repair"}}, "the car", "car")
	out := Excerpt("Holiday plans in Italy, in August.", "holiday in august")
	expectIn(t, out, "<mark>August</mark>")
	expectNot(t, out, "<mark>in</mark>")
	expectIn(t, Excerpt("Two weeks of holiday in Italy.", "holiday in italy"), "<mark>holiday in Italy</mark>")
}

func TestQuerySyntax(t *testing.T) {
	if q := ParseQuery("git push"); q.Text != "git push" || q.HasFilters() {
		t.Errorf("plain: %+v", q)
	}
	q := ParseQuery(`"holiday in italy" -rome #Travel -#old -"bad idea" cheap`)
	if q.Text != "holiday in italy cheap" || !reflect.DeepEqual(q.Phrases, []string{"holiday in italy"}) ||
		!reflect.DeepEqual(q.ExcludePhrases, []string{"bad idea"}) || !reflect.DeepEqual(q.ExcludeStems, []string{"rome"}) ||
		!reflect.DeepEqual(q.Tags, []string{"travel"}) || !reflect.DeepEqual(q.ExcludeTags, []string{"old"}) {
		t.Errorf("operators: %+v", q)
	}
	if q := ParseQuery("#leaf"); q.Text != "" || q.RankingText() != "leaf" {
		t.Errorf("only tags: %+v", q)
	}
	if ParseQuery("-foo").RankingText() != "" {
		t.Error("only exclusions rank nothing")
	}
	if q := ParseQuery(`say "hello`); q.HasFilters() || q.Text != `say "hello` {
		t.Errorf("unclosed quote: %+v", q)
	}
	if ParseQuery("a - b #").HasFilters() {
		t.Error("a lone - or # is text")
	}
	q = ParseQuery(`"Hello  World" -spam #a -#b`)
	toks := tokenize("hello world eggs")
	if !q.Accepts("Héllo world, eggs", toks, []string{"A"}, "") ||
		q.Accepts("hello there world", toks, []string{"a"}, "") || // no phrase
		q.Accepts("hello world", tokenize("hello world spams"), []string{"a"}, "") || // excluded stem
		q.Accepts("hello world", toks, nil, "") || // tag missing
		q.Accepts("hello world", toks, []string{"a", "B"}, "") { // excluded tag
		t.Error("Accepts")
	}
}

func TestDateFilters(t *testing.T) {
	q := ParseQuery("alex edited:2026-09 AFTER:2026-09-10 before:2027")
	if q.Text != "alex" || !q.HasFilters() || !reflect.DeepEqual(q.Dates, []dateFilter{{"edited", "2026-09"}, {"after", "2026-09-10"}, {"before", "2027"}}) {
		t.Errorf("parsed: %+v", q)
	}
	q = ParseQuery("-edited:2026-09 -after:2026-10 -before:2026-01")
	if len(q.ExcludeStems) != 0 || !reflect.DeepEqual(q.Dates, []dateFilter{{"not-edited", "2026-09"}, {"before", "2026-10"}, {"after", "2026-01"}}) {
		t.Errorf("negated: %+v", q)
	}
	if q := ParseQuery("edited:soon after:26-9"); q.Dates != nil || q.Text != "edited:soon after:26-9" {
		t.Errorf("invalid dates: %+v", q)
	}
	ok := func(query, day string) bool { return ParseQuery(query).Accepts("", nil, nil, day) }
	for _, c := range []struct {
		query, day string
		want       bool
	}{
		{"edited:2026-09", "2026-09-30", true}, {"edited:2026-09", "2026-10-01", false},
		{"after:2026-07", "2026-07-01", true}, {"after:2026-07", "2026-06-30", false},
		{"before:2026-08", "2026-07-31", true}, {"before:2026-08", "2026-08-01", false},
		{"before:2026-08-15", "2026-08-14", true}, {"before:2026-08-15", "2026-08-15", false},
		{"edited:2026", "", false}, // undated documents don't pass a date filter
		{"anything", "", true},     // nor need a date without one
	} {
		if ok(c.query, c.day) != c.want {
			t.Errorf("%q on %q: want %v", c.query, c.day, c.want)
		}
	}
}

func sameHits(t *testing.T, what string, got, want []Hit) {
	t.Helper()
	if len(got) != len(want) {
		t.Errorf("%s: %v, want %v", what, got, want)
		return
	}
	for i := range want {
		if got[i].Doc != want[i].Doc || math.Abs(got[i].Score-want[i].Score) > 1e-12 {
			t.Errorf("%s: hit %d is %v, want %v", what, i, got[i], want[i])
		}
	}
}

// An index with extra documents searches exactly as one built over all of
// them: the extra documents count in the idf and the average length.
func TestWithExtraIsAsIfBuiltOverAll(t *testing.T) {
	shared := [][]string{{"apple", "pie"}, {"apple", "tart", "cream"}, {"berry", "pie", "pie"}, {"plain"}}
	extra := [][]string{{"apple", "apple", "quokkaflute"}, {"pie"}, {"berry", "berry", "tart", "tart", "pie", "pie", "x"}}
	base := NewIndex(shared)
	before := base.Search("apple pie", 10)
	over := base.WithExtra(extra)
	whole := NewIndex(append(append([][]string{}, shared...), extra...))

	for _, q := range []string{"apple pie", "pie", "berry tart", "quokkaflute", "quokkaf", "quokkaflutte", "appl", "nothing", "plain"} {
		sameHits(t, q, over.Search(q, 20), whole.Search(q, 20))
	}
	// The index the others search never learned of them.
	sameHits(t, "base after", base.Search("apple pie", 10), before)
	if hits := base.Search("quokkaflute", 10); hits != nil {
		t.Errorf("the base index found %v", hits)
	}
}

func TestWithExtraOnAnEmptyIndex(t *testing.T) {
	over := NewIndex(nil).WithExtra([][]string{{"quokkaflute", "song"}})
	if hits := over.Search("quokkaflute", 5); len(hits) != 1 || hits[0].Doc != 0 || math.IsNaN(hits[0].Score) || math.IsInf(hits[0].Score, 0) {
		t.Errorf("hits %v", hits)
	}
}

// Adding users changes which documents are private without a document
// changing: the cache must not keep the split it made before.
func TestIndexCacheFollowsTheUsers(t *testing.T) {
	testWiki(t, nil)
	docs := []*SearchDoc{
		{Slug: "plans", Tokens: []string{"plans", "quokkaflute"}},
		{Slug: "private/alice/diary", Tokens: []string{"diary", "secretword"}},
	}
	var c indexCache
	bob := &Req{user: "bob"}
	if got := c.get(bob, docs); len(got.docs) != 2 {
		t.Fatalf("without users everything is shared: %d docs", len(got.docs))
	}
	withUsers(t, "alice", "bob")
	if got := c.get(bob, docs); len(got.docs) != 1 || got.docs[0].Slug != "plans" || got.ix.Search("secretword", 5) != nil {
		t.Errorf("after users were added bob's index holds %d docs, and finds secretword: %v", len(got.docs), got.ix.Search("secretword", 5))
	}
	if got := c.get(&Req{user: "alice"}, docs); len(got.docs) != 2 || len(got.ix.Search("secretword", 5)) != 1 {
		t.Errorf("alice's own page is missing from her index")
	}
	settings().Users = nil
	if got := c.get(bob, docs); len(got.docs) != 2 {
		t.Errorf("after the users were removed everything is shared again: %d docs", len(got.docs))
	}
}

// SearchWhere is Search with the refused documents taken out of the list:
// the same hits, scores and order. And a short limit gives the head of the
// full ordering.
func TestSearchWhereIsSearchFiltered(t *testing.T) {
	r := rand.New(rand.NewSource(3))
	toks := make([][]string, 300)
	for i := range toks {
		for j := 0; j < 5+r.Intn(40); j++ {
			toks[i] = append(toks[i], fmt.Sprintf("w%d", r.Intn(25)))
		}
	}
	ix := NewIndex(toks[:200]).WithExtra(toks[200:])
	even := func(d int) bool { return d%2 == 0 }
	for _, q := range []string{"w1", "w1 w2 w3", "w9 w24", "w1x", "w"} {
		var want []Hit
		for _, h := range ix.Search(q, 1000) {
			if even(h.Doc) {
				want = append(want, h)
			}
		}
		sameHits(t, q+" filtered", ix.SearchWhere(q, 1000, even), want)
		all := ix.Search(q, 1000)
		for _, limit := range []int{1, 7, 30, 64, 65, 1000} {
			sameHits(t, fmt.Sprintf("%s top %d", q, limit), ix.Search(q, limit), all[:min(limit, len(all))])
			sameHits(t, fmt.Sprintf("%s filtered top %d", q, limit), ix.SearchWhere(q, limit, even), want[:min(limit, len(want))])
		}
	}
}

type labelled struct {
	label string
	hit   Hit
}

// An index with documents left out and others added, ordered as given,
// searches exactly as one built over the documents left in that order: the
// same hits, scores and order (ties too), for words, prefixes and typos.
func TestWithChangesIsAsIfBuiltOverWhatIsLeft(t *testing.T) {
	r := rand.New(rand.NewSource(11))
	words := strings.Fields("apple apples applet banana bananas cherry cherries date fig grape grapes kiwi lemon lime mango melon nectar orange papaya peach pear plum quince raisin")
	doc := func() []string {
		out := make([]string, 3+r.Intn(25))
		for i := range out {
			out[i] = words[r.Intn(len(words))]
		}
		return out
	}
	queries := []string{"apple", "banana cherry", "grap", "app", "aple", "bananna", "chery", "pech", "zzzz", "kiwi lemon lime", "fig", "quinc raisn", "nectar orange papaya"}
	for round := 0; round < 80; round++ {
		nb := 10 + r.Intn(60)
		base := make([][]string, nb)
		for i := range base {
			base[i] = doc()
			if i > 0 && r.Intn(5) == 0 {
				base[i] = base[i-1] // equal scores: the order has to decide
			}
		}
		dead := map[int][]string{}
		for i := range base {
			if r.Intn(4) == 0 {
				dead[i] = base[i]
			}
		}
		if round%10 == 0 { // every document holding a term dead
			for i := range base {
				dead[i] = base[i]
			}
		}
		extra := make([][]string, r.Intn(15))
		for j := range extra {
			extra[j] = doc()
		}
		type ent struct {
			id     int
			tokens []string
			label  string
		}
		var live []ent
		for i := range base {
			if _, d := dead[i]; !d {
				live = append(live, ent{i, base[i], fmt.Sprintf("b%d", i)})
			}
		}
		for j := range extra {
			live = append(live, ent{nb + j, extra[j], fmt.Sprintf("e%d", j)})
		}
		r.Shuffle(len(live), func(a, b int) { live[a], live[b] = live[b], live[a] })
		pos := make([]int32, nb+len(extra))
		labelOf := map[int]string{}
		freshTokens := make([][]string, len(live))
		for k, e := range live {
			pos[e.id] = int32(k)
			labelOf[e.id] = e.label
			freshTokens[k] = e.tokens
		}
		got, whole := NewIndex(base).With(dead, extra, pos), NewIndex(freshTokens)
		for _, q := range queries {
			for _, limit := range []int{3, 1000} {
				var a, b []labelled
				for _, h := range got.Search(q, limit) {
					a = append(a, labelled{labelOf[h.Doc], h})
				}
				for _, h := range whole.Search(q, limit) {
					b = append(b, labelled{live[h.Doc].label, h})
				}
				if len(a) != len(b) {
					t.Fatalf("round %d %q top %d: %d hits, want %d", round, q, limit, len(a), len(b))
				}
				for i := range a {
					if a[i].label != b[i].label || a[i].hit.Score != b[i].hit.Score {
						t.Fatalf("round %d %q top %d, hit %d: %s %v, want %s %v", round, q, limit, i, a[i].label, a[i].hit.Score, b[i].label, b[i].hit.Score)
					}
				}
			}
		}
	}
}

// Edits, deletions and new pages and tasks, round after round: every
// viewer's index answers as one built afresh over what they may see, in the
// same order, whether the cache kept its base and corrected it or built it
// again.
func TestIndexCacheStaysAsIfBuiltAfresh(t *testing.T) {
	testWiki(t, nil)
	withUsers(t, "alice", "bob")
	r := rand.New(rand.NewSource(5))
	words := strings.Fields("apple apples applet banana bananas cherry cherries date fig grape grapes kiwi lemon lime mango melon nectar orange papaya peach pear plum quince raisin")
	tokens := func() []string {
		out := make([]string, 3+r.Intn(25))
		for i := range out {
			out[i] = words[r.Intn(len(words))]
		}
		return out
	}
	serial := 0
	newDoc := func() *SearchDoc {
		serial++
		switch r.Intn(6) {
		case 0:
			return &SearchDoc{Slug: fmt.Sprintf("private/alice/a%d", serial), Tokens: tokens()}
		case 1:
			return &SearchDoc{Slug: fmt.Sprintf("private/bob/b%d", serial), Tokens: tokens()}
		case 2:
			owner := []string{"", "alice", "bob"}[r.Intn(3)]
			return &SearchDoc{Slug: tasksDirName, TaskID: fmt.Sprintf("t%d", serial), Task: &Task{ID: fmt.Sprintf("t%d", serial), Owner: owner}, Tokens: tokens()}
		}
		return &SearchDoc{Slug: fmt.Sprintf("p%d", serial), Tokens: tokens()}
	}
	label := func(d *SearchDoc) string { return d.Slug + "/" + d.TaskID }
	edited := func(d *SearchDoc) *SearchDoc { // the doc as a changed file would give it: new
		return &SearchDoc{Slug: d.Slug, TaskID: d.TaskID, Task: d.Task, Tokens: tokens()}
	}
	var docs []*SearchDoc
	for i := 0; i < 240; i++ {
		docs = append(docs, newDoc())
		if i > 0 && r.Intn(6) == 0 { // the same words: equal scores, the order decides
			docs[i].Tokens = docs[i-1].Tokens
		}
	}
	queries := []string{"apple", "banana cherry", "grap", "aple", "bananna", "pech", "zzzz", "kiwi lemon lime", "nectar orange papaya"}
	viewers := map[string]*Req{"alice": {user: "alice"}, "bob": {user: "bob"}, "leaf": nil}
	var c indexCache
	rebuilds, corrected := 0, 0
	var lastBase *docIndex
	for round := 0; round < 60; round++ {
		if round > 0 {
			for k := r.Intn(8); k >= 0; k-- { // a few edits, deletions, additions
				switch i := r.Intn(len(docs)); r.Intn(3) {
				case 0:
					docs[i] = edited(docs[i])
				case 1:
					docs = slices.Delete(docs, i, i+1)
				default:
					docs = slices.Insert(docs, i, newDoc())
				}
			}
			if round%20 == 0 { // many at once: more than the base is kept for
				for i := range docs[:len(docs)/3] {
					docs[i] = edited(docs[i])
				}
			}
		}
		for name, rq := range viewers {
			got := c.get(rq, docs)
			var visible []*SearchDoc
			for _, d := range docs {
				if rq == nil || docVisible(rq.user, d) {
					visible = append(visible, d)
				}
			}
			want := newDocIndex(visible)
			for _, q := range queries {
				for _, limit := range []int{4, 1000} {
					a, b := got.search(q, limit), want.search(q, limit)
					if len(a) != len(b) {
						t.Fatalf("round %d %s %q top %d: %d hits, want %d", round, name, q, limit, len(a), len(b))
					}
					for i := range a {
						if a[i] != b[i] {
							t.Fatalf("round %d %s %q top %d, hit %d: %s, want %s", round, name, q, limit, i, label(a[i]), label(b[i]))
						}
					}
				}
			}
			if !slices.Equal(got.docs, visible) {
				t.Fatalf("round %d %s: the docs differ from what the viewer may see", round, name)
			}
		}
		if c.base != lastBase {
			rebuilds++
		} else if len(c.dead)+len(c.delta) > 0 {
			corrected++
		}
		lastBase = c.base
	}
	if rebuilds < 3 || corrected < 10 {
		t.Errorf("the test should use both ways: %d rebuilds, %d rounds with a corrected base", rebuilds, corrected)
	}
}

// Users with nothing private of their own are searched with one and the same
// index, also while it has changes: how many users there are costs nothing.
func TestUsersWithNothingPrivateShareAnIndex(t *testing.T) {
	testWiki(t, nil)
	withUsers(t, "alice", "bob", "carol")
	docs := []*SearchDoc{
		{Slug: "p1", Tokens: []string{"apple"}}, {Slug: "p2", Tokens: []string{"pear"}},
		{Slug: "private/alice/a", Tokens: []string{"fig"}},
	}
	var c indexCache
	c.get(&Req{user: "bob"}, docs)
	docs[1] = &SearchDoc{Slug: "p2", Tokens: []string{"pear", "plum"}} // an edit: the base now has changes
	bob, carol, alice := c.get(&Req{user: "bob"}, docs), c.get(&Req{user: "carol"}, docs), c.get(&Req{user: "alice"}, docs)
	if len(c.dead)+len(c.delta) == 0 {
		t.Fatal("the base should have changes")
	}
	if bob != carol {
		t.Error("bob and carol have nothing private and should share an index")
	}
	if alice == bob || len(alice.docs) != 3 || len(bob.docs) != 2 {
		t.Errorf("alice has a private page: her index is her own (%d docs), bob's has %d", len(alice.docs), len(bob.docs))
	}
}
