package main

import (
	"reflect"
	"testing"
)

// src/search.py in Go (search.go). The expected values are Python's
// (src/search.py, difflib), printed by it.

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
