package main

import (
	"fmt"
	"math/rand"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"zonewiki/internal/textsearch"
)

// Search and retrieval (retrieval.go): what the API tests can't reach.

// A page whose file vanishes or can't be read between listing and reading is
// left out of the search, not a failed search.
func TestUnreadablePagesAreSkipped(t *testing.T) {
	testWiki(t, map[string]string{"fine": "# Fine\n\nzzqword here\n"})
	// A dangling link fails the stat, a link to a folder the read.
	if err := os.Symlink(filepath.Join(cfg.Data, "nowhere.md"), filepath.Join(cfg.Data, "gone.md")); err != nil {
		t.Fatal(err)
	}
	_ = os.Mkdir(filepath.Join(cfg.Data, "folder"), 0o777)
	if err := os.Symlink(filepath.Join(cfg.Data, "folder"), filepath.Join(cfg.Data, "unread.md")); err != nil {
		t.Fatal(err)
	}
	var slugs []string
	for _, d := range pageSearchDocs() {
		slugs = append(slugs, d.Slug)
	}
	if len(slugs) != 1 || slugs[0] != "fine" {
		t.Errorf("pages searched: %q", slugs)
	}
}

func TestChunkSections(t *testing.T) {
	text := "Intro [link](http://x) text\n\n# Top\n\nOne\n\n```\n# not a heading\n```\n\n## Sub ##\n\nTwo\n\n#### Deep\n\nThree\n\n## Empty\n"
	got := chunkSections(text, maxChunkChars)
	want := []chunkSection{
		{"", "Intro [link] text", 0, ""},
		{"Top", "One\n\n```\n# not a heading\n```", 1, "Top"},
		{"Sub", "Two", 2, "Top › Sub"},
		{"Deep", "Three", 4, "Top › Sub › Deep"},
	}
	if len(got) != len(want) {
		t.Fatalf("got %+v", got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("%d: got %+v, want %+v", i, got[i], want[i])
		}
	}
	if a := sectionAnchorOf("Deep", 4); a != "" {
		t.Errorf("an h4 has no anchor: %q", a)
	}
}

func TestSplitLong(t *testing.T) {
	long := ""
	for i := 0; i < 30; i++ {
		long += "wörd "
	}
	eight := "wörd wörd wörd wörd wörd wörd wörd wörd"
	got := splitLong("short\n\n  \n"+long, 40) // Python's: embed._split_long
	want := []string{"short", eight, eight, eight, "wörd wörd wörd wörd wörd wörd"}
	if len(got) != len(want) {
		t.Fatalf("got %q", got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("%d: got %q, want %q", i, got[i], want[i])
		}
	}
}

// Adding users changes which documents are private without a document
// changing: the cache must not keep the split it made before.
func TestIndexCacheFollowsTheUsers(t *testing.T) {
	testWiki(t, nil)
	docs := []*SearchDoc{
		{Slug: "plans", Tokens: fixedTokens("plans", "quokkaflute")},
		{Slug: "private/alice/diary", Tokens: fixedTokens("diary", "secretword")},
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

// Edits, deletions and new pages and tasks, round after round: every
// viewer's index answers as one built afresh over what they may see, in the
// same order, whether the cache kept its base and corrected it or built it
// again. With the base in a file, Zone starts again now and then, and finds
// it there.
func TestIndexCacheStaysAsIfBuiltAfresh(t *testing.T) {
	for _, file := range []string{"", "test.idx"} {
		t.Run("file "+file, func(t *testing.T) { indexCacheStaysAsIfBuiltAfresh(t, file) })
	}
}

func indexCacheStaysAsIfBuiltAfresh(t *testing.T, file string) {
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
			return &SearchDoc{Slug: fmt.Sprintf("private/alice/a%d", serial), Tokens: fixedTokens(tokens()...)}
		case 1:
			return &SearchDoc{Slug: fmt.Sprintf("private/bob/b%d", serial), Tokens: fixedTokens(tokens()...)}
		case 2:
			owner := []string{"", "alice", "bob"}[r.Intn(3)]
			return &SearchDoc{Slug: tasksDirName, TaskID: fmt.Sprintf("t%d", serial), Task: &Task{ID: fmt.Sprintf("t%d", serial), Owner: owner}, Tokens: fixedTokens(tokens()...)}
		}
		return &SearchDoc{Slug: fmt.Sprintf("p%d", serial), Tokens: fixedTokens(tokens()...)}
	}
	label := func(d *SearchDoc) string { return d.Slug + "/" + d.TaskID }
	keyed := func(d *SearchDoc) *SearchDoc { d.key = testKey(d); return d }
	edited := func(d *SearchDoc) *SearchDoc { // the doc as a changed file would give it: new
		return keyed(&SearchDoc{Slug: d.Slug, TaskID: d.TaskID, Task: d.Task, Tokens: fixedTokens(tokens()...)})
	}
	var docs []*SearchDoc
	for i := 0; i < 240; i++ {
		docs = append(docs, newDoc())
		if i > 0 && r.Intn(6) == 0 { // the same words: equal scores, the order decides
			docs[i].Tokens = docs[i-1].Tokens
		}
		keyed(docs[i])
	}
	queries := []string{"apple", "banana cherry", "grap", "aple", "bananna", "pech", "zzzz", "kiwi lemon lime", "nectar orange papaya"}
	viewers := map[string]*Req{"alice": {user: "alice"}, "bob": {user: "bob"}, "leaf": nil}
	c := &indexCache{file: file}
	rebuilds, corrected, starts := 0, 0, 0
	var lastBase *docIndex
	for round := 0; round < 60; round++ {
		if file != "" && round%7 == 3 { // Zone starts again
			c, lastBase = &indexCache{file: file}, nil
			starts++
		}
		if round > 0 {
			for k := r.Intn(8); k >= 0; k-- { // a few edits, deletions, additions
				switch i := r.Intn(len(docs)); r.Intn(3) {
				case 0:
					docs[i] = edited(docs[i])
				case 1:
					docs = slices.Delete(docs, i, i+1)
				default:
					docs = slices.Insert(docs, i, keyed(newDoc()))
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
		if c.base != lastBase && lastBase != nil {
			rebuilds++
		} else if len(c.dead)+len(c.delta) > 0 {
			corrected++
		}
		lastBase = c.base
	}
	if rebuilds < 3 || corrected < 10 || file != "" && starts < 5 {
		t.Errorf("the test should use every way: %d rebuilds, %d rounds with a corrected base, %d starts", rebuilds, corrected, starts)
	}
}

// testKey: a test doc's key, from what it is and its tokens.
func testKey(d *SearchDoc) textsearch.DocKey {
	return docKey("test", d.Slug, d.TaskID, strings.Join(d.Tokens(), " "))
}

// A start finds the base in its file and doesn't build it: no doc's tokens
// are asked for, only those of a doc that changed meanwhile. A damaged file
// is built again.
func TestStartFindsTheIndexFile(t *testing.T) {
	testWiki(t, nil)
	asked := 0
	doc := func(slug string, words ...string) *SearchDoc {
		d := &SearchDoc{Slug: slug, Tokens: func() []string { asked++; return words }}
		d.key = docKey("test", slug, strings.Join(words, " "))
		return d
	}
	var docs []*SearchDoc
	for i := range 100 {
		docs = append(docs, doc(fmt.Sprintf("p%d", i), "apple", fmt.Sprintf("w%d", i%7)))
	}
	search := func(c *indexCache) []*SearchDoc { return c.get(nil, docs).search("apple w3", 1000) }
	want := search(&indexCache{file: "pages.idx"})
	if asked != len(docs) {
		t.Fatalf("the first build asked for %d docs' tokens", asked)
	}
	path := statePath("index", "pages.idx")
	if _, err := os.Stat(path); err != nil {
		t.Fatal(err)
	}

	asked = 0
	if got := search(&indexCache{file: "pages.idx"}); !slices.Equal(got, want) || asked != 0 {
		t.Errorf("a start: %d docs' tokens asked for, the same hits: %v", asked, slices.Equal(got, want))
	}

	docs[5] = doc("p5", "apple", "w3", "w3") // changed while Zone was down
	asked = 0
	c := &indexCache{file: "pages.idx"}
	got := search(c)
	if asked != 1 || len(c.dead) != 1 || len(c.delta) != 1 {
		t.Errorf("a start after an edit: %d docs' tokens asked for, %d dead, %d new", asked, len(c.dead), len(c.delta))
	}
	if fresh := newDocIndex(docs).search("apple w3", 1000); !slices.Equal(got, fresh) {
		t.Errorf("after an edit: not as if built afresh")
	}

	b, _ := os.ReadFile(path)
	b[len(b)-1] ^= 1
	if err := os.WriteFile(path, b, 0o666); err != nil {
		t.Fatal(err)
	}
	asked = 0
	if got := search(&indexCache{file: "pages.idx"}); !slices.Equal(got, newDocIndex(docs).search("apple w3", 1000)) || asked != 2*len(docs) {
		t.Errorf("a damaged file: %d docs' tokens asked for (built, and the check against a fresh one)", asked)
	}
}

// Users with nothing private of their own are searched with one and the same
// index, also while it has changes: how many users there are costs nothing.
func TestUsersWithNothingPrivateShareAnIndex(t *testing.T) {
	testWiki(t, nil)
	withUsers(t, "alice", "bob", "carol")
	docs := []*SearchDoc{
		{Slug: "p1", Tokens: fixedTokens("apple")}, {Slug: "p2", Tokens: fixedTokens("pear")},
		{Slug: "private/alice/a", Tokens: fixedTokens("fig")},
	}
	var c indexCache
	c.get(&Req{user: "bob"}, docs)
	docs[1] = &SearchDoc{Slug: "p2", Tokens: fixedTokens("pear", "plum")} // an edit: the base now has changes
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

// fixedTokens: a doc's tokens, as the doc asks for them.
func fixedTokens(w ...string) func() []string { return func() []string { return w } }

// A -word goes by the index, so it has to see what the tokens would have: the
// title and the slug, and a page edited since the index was built.
func TestExcludedWordsComeFromTheIndex(t *testing.T) {
	testWiki(t, nil)
	docs := []*SearchDoc{
		{Slug: "a", Tokens: fixedTokens("apple", "spam")},
		{Slug: "b", Tokens: fixedTokens("apple")},
		{Slug: "c", Tokens: fixedTokens("apple", "spam")},
	}
	var c indexCache
	c.get(nil, docs)
	docs[1] = &SearchDoc{Slug: "b", Tokens: fixedTokens("apple", "spam")} // an edit: now it holds it
	docs[2] = &SearchDoc{Slug: "c", Tokens: fixedTokens("apple")}         // and this one doesn't
	x := c.get(nil, docs)
	var got []string
	for _, d := range bm25(x, textsearch.ParseQuery("apple -spam"), 10, nil) {
		got = append(got, d.Slug)
	}
	if !slices.Equal(got, []string{"c"}) {
		t.Errorf("found %v, want only c", got)
	}
}

// Page and section docs keep no text: they read it from their file again,
// their tokens too, and a page gone since gives none.
func TestDocsReadTheirTextAgain(t *testing.T) {
	page := "# Gärten  ÜBER\n\nIntro with a [link](http://example.com/zzqtarget).\n\n## Roses\n\nRed zzqrose here.\n\n## Tulips\n\nYellow   Straße zzqtulip.\n"
	testWiki(t, map[string]string{"garden": page})
	docs := pageSearchDocs()
	if len(docs) != 1 {
		t.Fatalf("%d docs", len(docs))
	}
	d := docs[0]
	body, ok := d.body()
	if !ok || body != searchText("garden", page) || strings.Contains(body, "zzqtarget") {
		t.Errorf("the page's text: %v %q", ok, body)
	}
	if !slices.Contains(d.Tokens(), "zzqrose") || !d.hasPhrase("red zzqrose here") {
		t.Errorf("tokens %v", d.Tokens())
	}
	var tulips *SearchDoc
	for _, chunks := range perPage(&chunkDocCache, pageChunks, "", nil) {
		for _, c := range chunks {
			if c.Heading == "Tulips" {
				tulips = c
			}
		}
	}
	if tulips == nil {
		t.Fatal("no Tulips section")
	}
	if body, ok := tulips.body(); !ok || body != "Yellow   Straße zzqtulip." || !strings.HasSuffix(tulips.text(), "Tulips\nYellow   Straße zzqtulip.") {
		t.Errorf("the section's text: %v %q / %q", ok, body, tulips.text())
	}
	if toks := tulips.Tokens(); !slices.Contains(toks, "zzqtulip") || slices.Contains(toks, "zzqrose") {
		t.Errorf("the section's tokens: %v", toks)
	}
	// What goes into the index file: the same tokens, the text squashed.
	for _, doc := range []*SearchDoc{d, tulips} {
		toks, squashed := doc.indexed()
		if !slices.Equal(toks, doc.Tokens()) || squashed != textsearch.Squash(doc.text()) {
			t.Errorf("%s %q: indexed %q, want %q", doc.Slug, doc.Heading, squashed, textsearch.Squash(doc.text()))
		}
	}

	docTexts = textCache{} // as after the cache started over
	if err := os.Remove(filepath.Join(cfg.Data, "garden.md")); err != nil {
		t.Fatal(err)
	}
	if body, ok := d.body(); ok || body != "" {
		t.Errorf("a page gone: %v %q", ok, body)
	}
	if _, ok := tulips.body(); ok {
		t.Error("a page gone: its section still has text")
	}
}
