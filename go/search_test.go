package main

import (
	"reflect"
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

// ---- the cases of Leaf's search tests ----

type testDoc struct{ slug, title, text string }

// ranked: the slugs a search finds, best first (titles weigh as in Leaf).
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
