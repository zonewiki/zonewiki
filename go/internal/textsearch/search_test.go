package textsearch

import (
	"fmt"
	"maps"
	"math"
	"math/rand"
	"path/filepath"
	"reflect"
	"slices"
	"sort"
	"strconv"
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
	got := Tokenize("The Müller's running-dogs [link](http://x.org/page) ![img](a.png) 日本語 x_y")
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
		if got := CloseMatches(w, poss, 3, 0.6); !reflect.DeepEqual(got, ws) {
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
		toks := Tokenize(text)
		for i := 0; i < TitleWeight; i++ {
			toks = append(toks, Tokenize(title)...)
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

// ---- the cases of Zonewiki's search tests ----

type testDoc struct{ slug, title, text string }

// ranked: the slugs a search finds, best first (titles weigh as in Zonewiki).
func ranked(docs []testDoc, query string) []string {
	var toks [][]string
	for _, d := range docs {
		ts := Tokenize(d.text)
		for i := 0; i < TitleWeight; i++ {
			ts = append(ts, Tokenize(d.title)...)
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
	if got := Tokenize("Running Tests, quickly!"); !reflect.DeepEqual(got, []string{"run", "test", "quickly"}) {
		t.Errorf("Tokenize: %q", got)
	}
	// An inflection matches its base at about full weight.
	var a, b float64
	toks := [][]string{Tokenize("We called him"), Tokenize("call me maybe")}
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
		if got := StripLinkTargets(in); got != want {
			t.Errorf("StripLinkTargets(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestUnicodeWords(t *testing.T) {
	if got := Tokenize("Müller"); !reflect.DeepEqual(got, []string{"muller"}) {
		t.Errorf("Müller: %q", got)
	}
	if !reflect.DeepEqual(Tokenize("Café MÜLLER"), Tokenize("cafe muller")) || !reflect.DeepEqual(Tokenize("Straße"), Tokenize("strasse")) {
		t.Error("accents, case and ß fold")
	}
	if got := Tokenize("foo_bar"); !reflect.DeepEqual(got, []string{"foo", "bar"}) {
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
		docs = append(docs, testDoc{"common" + strconv.Itoa(i), "Doc " + strconv.Itoa(i), "the the the the the ordinary text"})
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
	if !q.Accepts("Héllo world, eggs", []string{"A"}, "") ||
		q.Accepts("hello there world", []string{"a"}, "") || // no phrase
		q.Accepts("hello world", nil, "") || // tag missing
		q.Accepts("hello world", []string{"a", "B"}, "") { // excluded tag
		t.Error("Accepts")
	}
	if q.HoldsExcluded(Tokenize("hello world eggs")) || !q.HoldsExcluded(Tokenize("hello world spams")) {
		t.Error("HoldsExcluded")
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
	ok := func(query, day string) bool { return ParseQuery(query).Accepts("", nil, day) }
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

func TestDocsWith(t *testing.T) {
	ix := NewIndex([][]string{{"apple"}, {"pear"}, {"apple", "apple"}}).With(map[int]bool{0: true}, [][]string{{"apple", "fig"}}, nil)
	if got := ix.DocsWith("apple"); !slices.Equal(got, []int{0, 2, 3}) {
		t.Errorf("apple: %v", got)
	}
	if got := ix.DocsWith("kiwi"); got != nil {
		t.Errorf("kiwi: %v", got)
	}
}

// HaveAll finds a query's words by stem and prefix, not by a typo's guess,
// leaves stopwords out, and answers only for the documents asked about.
func TestHaveAll(t *testing.T) {
	ix := NewIndex([][]string{{"seccomp", "profil"}, {"kernel"}, {"seccomp"}}).WithExtra([][]string{{"kernel", "seccomp"}})
	all := []int{0, 1, 2, 3}
	for _, c := range []struct {
		query string
		docs  []int
		want  []int
	}{
		{"seccomp", all, []int{0, 2, 3}},
		{"Seccomp seccomp", all, []int{0, 2, 3}},
		{"the seccomp", all, []int{0, 2, 3}},
		{"secc", all, []int{0, 2, 3}},
		{"secomp", all, nil},
		{"secomp kernel", all, nil},
		{"seccomp kernels", all, []int{3}},
		{"seccomp", []int{1, 2}, []int{2}},
		{"seccomp zebra", all, nil},
		{"seccomp", nil, nil},
		{"", all, nil},
	} {
		got := slices.Sorted(maps.Keys(ix.HaveAll(c.query, c.docs)))
		if !slices.Equal(got, c.want) {
			t.Errorf("%q among %v: %v, want %v", c.query, c.docs, got, c.want)
		}
	}
}

func TestWithExtraOnAnEmptyIndex(t *testing.T) {
	over := NewIndex(nil).WithExtra([][]string{{"quokkaflute", "song"}})
	if hits := over.Search("quokkaflute", 5); len(hits) != 1 || hits[0].Doc != 0 || math.IsNaN(hits[0].Score) || math.IsInf(hits[0].Score, 0) {
		t.Errorf("hits %v", hits)
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
		dead := map[int]bool{}
		for i := range base {
			if r.Intn(4) == 0 {
				dead[i] = true
			}
		}
		if round%10 == 0 { // every document holding a term dead
			for i := range base {
				dead[i] = true
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
		// The same as one layer and as several (a viewer's places), which
		// hold some of the same words.
		ix := NewIndex(base)
		var layers []LayerAt
		for from := 0; from < len(extra); {
			to := from + 1 + r.Intn(len(extra)-from)
			layers = append(layers, LayerAt{NewLayer(to-from, func(i int) []string { return extra[from+i] }), pos[nb+from : nb+to]})
			from = to
		}
		// And as layers that are indexes with documents of their own left
		// out (a place's index after changes), in memory or from a file:
		// those keep their numbers, and nothing else of them shows.
		var places []LayerAt
		placed := map[int]string{}
		for i := range base {
			placed[i] = labelOf[i]
		}
		first := nb
		for from := 0; from < len(extra); {
			to := from + 1 + r.Intn(len(extra)-from)
			var toks [][]string
			var lpos []int32
			gone := map[int]bool{}
			for j := from; j < to; j++ {
				for r.Intn(3) == 0 {
					gone[len(toks)] = true
					toks, lpos = append(toks, doc()), append(lpos, 0)
				}
				placed[first+len(toks)] = labelOf[nb+j]
				toks, lpos = append(toks, extra[j]), append(lpos, pos[nb+j])
			}
			lix := NewIndex(toks)
			if round%2 == 0 {
				path := filepath.Join(t.TempDir(), "place.idx")
				text := func(i int) string { return strings.Join(toks[i], " ") }
				if err := WriteIndex(path, len(toks), func(i int) IndexDoc { return IndexDoc{Tokens: toks[i], Text: text(i)} }); err != nil {
					t.Fatal(err)
				}
				var err error
				if lix, err = OpenIndex(path); err != nil {
					t.Fatal(err)
				}
				// A phrase is looked for in the layer's file, as in the index's.
				view := ix.View(nil, nil, append(slices.Clone(places), LayerAt{Layer: lix.Layer(nil)}))
				for i := range toks {
					if has, known := view.TextHas(first+i, text(i)); !has || !known {
						t.Fatalf("round %d: a layer's doc's text from its file: has %v, known %v", round, has, known)
					}
					if has, known := view.TextHas(first+i, "no such words"); has || !known {
						t.Fatalf("round %d: a layer's doc has a text it doesn't", round)
					}
				}
			} else if _, known := ix.View(nil, nil, []LayerAt{{Layer: lix.Layer(nil)}}).TextHas(nb, "apple"); known {
				t.Fatalf("round %d: a layer in memory has no text", round)
			}
			places = append(places, LayerAt{lix.Layer(lix.Without(gone)), lpos})
			first += len(toks)
			from = to
		}
		whole := NewIndex(freshTokens)
		for k, got := range []*Index{ix.With(dead, extra, pos), ix.View(ix.Without(dead), pos[:nb], layers), ix.View(ix.Without(dead), pos[:nb], places)} {
			labelOf := labelOf
			if k == 2 {
				labelOf = placed
			}
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
}

// A layer is only read by a view: two views of two viewers share it, each
// with its own other layers, and neither finds the other's documents.
func TestViewsShareALayer(t *testing.T) {
	ix := NewIndex([][]string{{"apple", "pear"}, {"plum"}})
	group := NewLayer(2, func(i int) []string { return [][]string{{"apple", "ocelot"}, {"ocelot", "harp"}}[i] })
	anna := NewLayer(1, func(int) []string { return []string{"ocelot", "annasword"} })
	bo := NewLayer(1, func(int) []string { return []string{"bosword", "apple"} })
	a := ix.View(nil, nil, []LayerAt{{Layer: group}, {Layer: anna}})
	b := ix.View(nil, nil, []LayerAt{{Layer: bo}, {Layer: group}})
	docs := func(hits []Hit) (out []int) {
		for _, h := range hits {
			out = append(out, h.Doc)
		}
		slices.Sort(out)
		return
	}
	if got := docs(a.Search("ocelot", 10)); !slices.Equal(got, []int{2, 3, 4}) {
		t.Errorf("anna: ocelot in %v", got)
	}
	if got := docs(b.Search("ocelot", 10)); !slices.Equal(got, []int{3, 4}) { // the group's are numbered after bo's one
		t.Errorf("bo: ocelot in %v", got)
	}
	if a.Search("bosword", 10) != nil || a.Search("boswor", 10) != nil || a.Search("bosworx", 10) != nil {
		t.Error("anna finds bo's word, by itself, its start or a typo")
	}
	if b.Search("annasword", 10) != nil || len(b.Search("bosworx", 10)) != 1 {
		t.Error("bo finds anna's word, or not their own by a typo")
	}
	// The same as an index over each one's documents.
	for name, pair := range map[string][2]*Index{
		"anna": {a, NewIndex([][]string{{"apple", "pear"}, {"plum"}, {"apple", "ocelot"}, {"ocelot", "harp"}, {"ocelot", "annasword"}})},
		"bo":   {b, NewIndex([][]string{{"apple", "pear"}, {"plum"}, {"bosword", "apple"}, {"apple", "ocelot"}, {"ocelot", "harp"}})},
	} {
		for _, q := range []string{"apple", "ocelot harp", "ocel", "plum apple", "harq"} {
			if got, want := pair[0].Search(q, 10), pair[1].Search(q, 10); !slices.Equal(got, want) {
				t.Errorf("%s %q: %v, an index over their documents %v", name, q, got, want)
			}
		}
	}
	if ix.Search("ocelot", 10) != nil {
		t.Error("the index itself learned of a layer")
	}
}

func expectIn(t *testing.T, html string, want ...string) {
	t.Helper()
	for _, w := range want {
		if !strings.Contains(html, w) {
			t.Errorf("missing %q in\n%s", w, html)
		}
	}
}

func expectNot(t *testing.T, html string, unwanted ...string) {
	t.Helper()
	for _, u := range unwanted {
		if strings.Contains(html, u) {
			t.Errorf("unexpected %q in\n%s", u, html)
		}
	}
}

// A query costs what a few words do: it is cut at MaxQueryChars (at a
// space), ranks by its distinct words, at most maxQueryWords, and only
// fuzzyWords of those that match nothing are looked for as typos, each
// among every term of the index.
func TestAQueryIsAFewWords(t *testing.T) {
	long := strings.Repeat("word ", 300) + "zebracorn"
	if q := ParseQuery(long); RuneLen(q.Text) > MaxQueryChars || strings.Contains(q.Text, "zebracorn") || strings.HasSuffix(q.Text, "wor") {
		t.Errorf("cut: %d characters, %q…", RuneLen(q.Text), q.Text[len(q.Text)-10:])
	}
	if q := ParseQuery(strings.Repeat("ä", 2*MaxQueryChars)); RuneLen(q.Text) != MaxQueryChars {
		t.Errorf("one long word: %d characters", RuneLen(q.Text))
	}
	if ws := queryWords("b a b a c"); !reflect.DeepEqual(ws, []string{"b", "c"}) && !reflect.DeepEqual(ws, []string{"b", "a", "c"}) {
		t.Errorf("each once: %q", ws)
	}
	var many []string
	for i := range 3 * maxQueryWords {
		many = append(many, fmt.Sprintf("w%d", i))
	}
	if ws := queryWords(strings.Join(many, " ")); len(ws) != maxQueryWords || ws[0] != "w0" {
		t.Errorf("%d words", len(ws))
	}
	// Twelve typos, each of a term of its own: the first fuzzyWords find theirs.
	var terms, typos []string
	for i := range 12 {
		w := "kangaroo" + string(rune('a'+i))
		terms, typos = append(terms, w), append(typos, "kangarxo"+string(rune('a'+i)))
	}
	ix := NewIndex([][]string{terms})
	var found []string
	for _, tw := range ix.expand(typos, fuzzyWords) {
		found = append(found, tw.term)
	}
	if len(found) == 0 || len(found) > 3*fuzzyWords {
		t.Errorf("typos found: %q", found)
	}
	for _, f := range found {
		if f[len(f)-1] >= byte('a'+fuzzyWords) {
			t.Errorf("a typo after the first %d was looked up: %q", fuzzyWords, f)
		}
	}
}
