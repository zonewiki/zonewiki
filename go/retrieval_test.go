package main

import (
	"fmt"
	"math/rand"
	"net"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"
	"unsafe"
	"zonewiki/internal/taskfiles"
	"zonewiki/internal/textsearch"
)

// Search and retrieval (retrieval.go): what the API tests can't reach.

// A page whose file vanishes or can't be read between listing and reading is
// left out of the search, not a failed search.
func TestUnreadablePagesAreSkipped(t *testing.T) {
	testWiki(t, map[string]string{"fine": "# Fine\n\nzzqword here\n"})
	// A socket by a page's name can't be read (a link is no page at all:
	// iterPages skips it).
	l, err := net.ListenUnix("unix", &net.UnixAddr{Name: filepath.Join(cfg.Data, "unread.md"), Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	l.SetUnlinkOnClose(false)
	l.Close()
	if err := os.Symlink(filepath.Join(cfg.Data, "nowhere.md"), filepath.Join(cfg.Data, "gone.md")); err != nil {
		t.Fatal(err)
	}
	var slugs []string
	for _, d := range pageSearchDocs(nil) {
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
	bob := &Req{user: asUser("bob")}
	if got := c.get(bob, docs); got.count() != 2 {
		t.Fatalf("without users everything is shared: %d docs", got.count())
	}
	withUsers(t, "alice", "bob")
	if got := c.get(bob, docs); got.count() != 1 || docsOf(got)[0].Slug != "plans" || got.ix.Search("secretword", 5) != nil {
		t.Errorf("after users were added bob's index holds %d docs, and finds secretword: %v", got.count(), got.ix.Search("secretword", 5))
	}
	if got := c.get(&Req{user: asUser("alice")}, docs); got.count() != 2 || len(got.ix.Search("secretword", 5)) != 1 {
		t.Errorf("alice's own page is missing from her index")
	}
	settings().Users = nil
	if got := c.get(bob, docs); got.count() != 2 {
		t.Errorf("after the users were removed everything is shared again: %d docs", got.count())
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
	withGroups(t, map[string][]string{"all": {"dev"}}, "alice:dev", "bob", "carol:dev")
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
		switch r.Intn(8) {
		case 6: // a group's page, a group's that includes it, a group's nobody is in, no one's
			folder := []string{"groups/dev", "groups/all", "groups/empty", "private/nobody"}[r.Intn(4)]
			return &SearchDoc{Slug: fmt.Sprintf("%s/g%d", folder, serial), Tokens: fixedTokens(tokens()...)}
		case 0:
			return &SearchDoc{Slug: fmt.Sprintf("private/alice/a%d", serial), Tokens: fixedTokens(tokens()...)}
		case 1:
			return &SearchDoc{Slug: fmt.Sprintf("private/bob/b%d", serial), Tokens: fixedTokens(tokens()...)}
		case 2:
			owner := []string{"", "alice", "bob"}[r.Intn(3)]
			return &SearchDoc{Slug: taskfiles.DirName, Task: &Task{ID: fmt.Sprintf("t%d", serial), Owner: owner}, Tokens: fixedTokens(tokens()...)}
		}
		return &SearchDoc{Slug: fmt.Sprintf("p%d", serial), Tokens: fixedTokens(tokens()...)}
	}
	label := func(d *SearchDoc) string { return d.Slug + "/" + d.taskID() }
	keyed := func(d *SearchDoc) *SearchDoc { d.key = testKey(d); return d }
	edited := func(d *SearchDoc) *SearchDoc { // the doc as a changed file would give it: new
		return keyed(&SearchDoc{Slug: d.Slug, Task: d.Task, Tokens: fixedTokens(tokens()...)})
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
	viewers := map[string]*Req{"alice": {user: asUser("alice")}, "bob": {user: asUser("bob")}, "carol": {user: asUser("carol")}, "leaf": nil}
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
			if !slices.Equal(docsOf(got), visible) {
				t.Fatalf("round %d %s: the docs differ from what the viewer may see", round, name)
			}
			if got.count() != len(visible) {
				t.Fatalf("round %d %s: counts %d docs, has %d", round, name, got.count(), len(visible))
			}
			for id, d := range got.all() {
				if got.doc(id) != d {
					t.Fatalf("round %d %s: %s has another's number in the index", round, name, label(d))
				}
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
	return docKey("test", d.Slug, d.taskID(), strings.Join(d.tokens(), " "))
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

	// Docs in another order than the file's, nothing else changed (a task
	// moved in its list): the file is used still, and equal scores go by the
	// order now.
	inOrder := slices.Clone(docs)
	p3 := docs[3] // has "w3": tied with p10, p17 …
	docs = append(slices.Delete(docs, 3, 4), p3)
	asked = 0
	rc := &indexCache{file: "pages.idx"}
	got := search(rc)
	if !rc.reordered || asked != 0 {
		t.Errorf("a start, the docs reordered: reordered %v, %d docs' tokens asked for", rc.reordered, asked)
	}
	if fresh := newDocIndex(docs).search("apple w3", 1000); !slices.Equal(got, fresh) || slices.Equal(got, want) {
		t.Errorf("reordered: not as if built afresh")
	}
	docs = inOrder

	docs[5] = doc("p5", "apple", "w3", "w3") // changed while Zone was down
	asked = 0
	c := &indexCache{file: "pages.idx"}
	got = search(c)
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
	c.get(&Req{user: asUser("bob")}, docs)
	docs[1] = &SearchDoc{Slug: "p2", Tokens: fixedTokens("pear", "plum")} // an edit: the base now has changes
	bob, carol, alice := c.get(&Req{user: asUser("bob")}, docs), c.get(&Req{user: asUser("carol")}, docs), c.get(&Req{user: asUser("alice")}, docs)
	if len(c.dead)+len(c.delta) == 0 {
		t.Fatal("the base should have changes")
	}
	if !slices.Equal(docsOf(bob), docsOf(carol)) || len(c.places) != 1 {
		t.Errorf("bob and carol have nothing private: the same docs, and one place beside them (%d)", len(c.places))
	}
	if alice.count() != 3 || bob.count() != 2 {
		t.Errorf("alice has a private page: her index has %d docs, bob's %d", alice.count(), bob.count())
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
	docs := pageSearchDocs(nil)
	if len(docs) != 1 {
		t.Fatalf("%d docs", len(docs))
	}
	d := docs[0]
	body, ok := d.body()
	if !ok || body != searchText("garden", page) || strings.Contains(body, "zzqtarget") {
		t.Errorf("the page's text: %v %q", ok, body)
	}
	if !slices.Contains(d.tokens(), "zzqrose") || !d.hasPhrase("red zzqrose here") {
		t.Errorf("tokens %v", d.tokens())
	}
	var tulips *SearchDoc
	for _, chunks := range perPage(&chunkDocCache, (*Req)(nil).pageStats(), pageChunks, "", nil) {
		for _, c := range chunks {
			if c.heading() == "Tulips" {
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
	if toks := tulips.tokens(); !slices.Contains(toks, "zzqtulip") || slices.Contains(toks, "zzqrose") {
		t.Errorf("the section's tokens: %v", toks)
	}
	// What goes into the index file: the same tokens, the text squashed.
	for _, doc := range []*SearchDoc{d, tulips} {
		toks, squashed, same := doc.indexed()
		if !slices.Equal(toks, doc.tokens()) || squashed != textsearch.Squash(doc.text()) || !same {
			t.Errorf("%s %q: indexed %q (same file %v), want %q", doc.Slug, doc.heading(), squashed, same, textsearch.Squash(doc.text()))
		}
	}

	// The file changed since the docs were made: what is read is no longer
	// what their keys say (build stores them under no key).
	docTexts = textCache{}
	later := d.Mtime.Add(time.Hour)
	if err := os.Chtimes(filepath.Join(cfg.Data, "garden.md"), later, later); err != nil {
		t.Fatal(err)
	}
	for _, doc := range []*SearchDoc{d, tulips} {
		if _, _, same := doc.indexed(); same {
			t.Errorf("%s %q: a changed file read as the same", doc.Slug, doc.heading())
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

// Phrases (phraseFirst, a quoted query's filters) checked in the index file
// (TextHas) find what the docs' own texts find in an index built in memory,
// also for a page changed since (not in the file) and after a start.
func TestPhrasesInTheFileAsInMemory(t *testing.T) {
	testWiki(t, map[string]string{
		"alpha": "# Alpha\n\nThe quick brown fox jumps.\n",
		"beta":  "# Beta\n\nA quick fox, brown and lazy.\n",
		"gamma": "# Gamma\n\nbrown quick fox\n\n## Fox notes\n\nThe   QUICK Brown fox again.\n",
		"delta": "# Delta\n\nNothing about foxes here, quick or brown.\n",
	})
	queries := []string{`"quick brown fox"`, `fox "brown and lazy"`, `fox -"quick brown"`, `quick brown`, `"the quick" fox`, `-"fox" quick`}
	same := func(label string, c *indexCache) {
		t.Helper()
		docs := pageSearchDocs(nil)
		got, want := c.get(nil, docs), newDocIndex(docs)
		if got.ix.Keys() == nil {
			t.Fatalf("%s: the index isn't from a file", label)
		}
		for _, raw := range queries {
			q := textsearch.ParseQuery(raw)
			a, b := rankedDocs(q, got, nil, 100), rankedDocs(q, want, nil, 100)
			if !slices.Equal(a, b) {
				t.Errorf("%s %s: %d hits, want %d", label, raw, len(a), len(b))
			}
		}
	}
	c := &indexCache{file: "pages.idx"}
	same("built", c)

	path := filepath.Join(cfg.Data, "delta.md")
	if err := os.WriteFile(path, []byte("# Delta\n\nNow the quick brown fox, and brown and lazy too.\n"), 0o666); err != nil {
		t.Fatal(err)
	}
	later := time.Now().Add(time.Hour)
	if err := os.Chtimes(path, later, later); err != nil {
		t.Fatal(err)
	}
	same("a page changed", c)
	same("a start", &indexCache{file: "pages.idx"})
}

// A section doc keeps only what its page's sections share and which one it
// is: its heading, path, anchor and semantic key come from its page split
// again, and are what the split gives, a long section's parts too.
func TestSectionDocsReadWhatTheyShow(t *testing.T) {
	long := strings.Repeat("Many words in a long section. ", maxChunkChars/20)
	page := "# Garden\n\nIntro.\n\n## Roses\n\nRed.\n\n### Pruning\n\nIn March.\n\n#### Tools\n\nShears.\n\n## Long\n\n" + long + "\n"
	testWiki(t, map[string]string{"garden": page})
	title, body := semanticText(page, "garden")
	want := chunkSections(body, maxChunkChars)
	var docs []*SearchDoc
	for _, d := range chunkIndex(nil).all() {
		if d.Slug == "garden" {
			docs = append(docs, d)
		}
	}
	if len(docs) != len(want) || len(want) < 6 {
		t.Fatalf("%d section docs, the split has %d", len(docs), len(want))
	}
	for i, s := range want {
		d := docs[i]
		if d.heading() != s.Heading || d.path() != s.Path || d.anchor() != sectionAnchorOf(s.Heading, s.Level) ||
			d.semKey() != chunkKey(title, chunkText(s.Path, s.Body)) {
			t.Errorf("section %d: %q %q %q, want %+v", i, d.heading(), d.path(), d.anchor(), s)
		}
	}
	if d := pageSearchDocs(nil)[0]; d.heading() != "" || d.anchor() != "" || d.path() != "" {
		t.Errorf("a page doc has a section's: %q %q %q", d.heading(), d.anchor(), d.path())
	}
}

// What every page and section costs while it is indexed: a doc of a few
// words (8 bytes each), not its text, headings or keys. A field added to
// SearchDoc is one more for each of hundreds of thousands of sections.
func TestSearchDocStaysSmall(t *testing.T) {
	if n := unsafe.Sizeof(SearchDoc{}); n > 144 {
		t.Errorf("a SearchDoc is %d bytes", n)
	}
}

// search_wiki (retrieve) fuses keywords with meaning: a section found only
// by meaning is matched to its doc by its key, worked out only for the
// pages hit.
func TestRetrieveFindsSectionsByMeaning(t *testing.T) {
	testWiki(t, semanticPages)
	x := testIndex(newRecorder(), nil)
	x.sync()
	old := semantic
	semantic = x
	t.Cleanup(func() { semantic = old })
	var got []string
	for _, d := range retrieve(&Req{}, "car repair", 8, "", false) {
		got = append(got, resultTarget(d))
	}
	if len(got) == 0 || got[0] != "golf#werkstatt" {
		t.Errorf("car repair: %v, want golf#werkstatt first", got)
	}
}

// restartSearch: search's caches as a start finds them, empty, but for the
// index files in .zone/index.
func restartSearch() {
	searchDocCache = pageCache[*SearchDoc]{}
	chunkDocCache = pageCache[[]*SearchDoc]{}
	searchIndexCache = indexCache{file: "pages.idx"}
	chunkIndexCache = indexCache{file: "sections.idx"}
	docTexts = textCache{}
}

// A start makes the docs from the index files' metas, not from the pages:
// its first search reads no page that hasn't changed since (a page with
// [[task:…]] is read, its doc depends on the tasks), and answers as an index
// built anew. A page changed while Zone was down is read, and only it.
func TestStartReadsNoPages(t *testing.T) {
	pages := map[string]string{
		"tasky": "# Tasky\n\nSee [[task:a1b2c3]] about apples.\n",
	}
	for i := range 30 {
		pages[fmt.Sprintf("notes/p%02d", i)] = fmt.Sprintf("# Page %d\n\ntags: [fruit]\n\nApples %d.\n\n## Pears\n\nPears and apples, %d.\n\n### Über\n\nPlums.\n", i, i%5, i%3)
	}
	testWiki(t, pages)
	restartSearch()
	t.Cleanup(restartSearch)
	targets := func(docs []*SearchDoc) []string {
		var out []string
		for _, d := range docs {
			out = append(out, resultTarget(d))
		}
		return out
	}
	queries := []string{"apples", "pears plums", "uber", "apples 3", "tasky"}
	same := func(label string) {
		t.Helper()
		pages, chunks := searchIndex(nil), chunkIndex(nil)
		for _, q := range queries {
			for _, x := range []*docIndex{pages, chunks} {
				fresh := newDocIndex(docsOf(x))
				if a, b := targets(x.search(q, 100)), targets(fresh.search(q, 100)); !slices.Equal(a, b) {
					t.Errorf("%s %q: %v, want %v", label, q, a, b)
				}
			}
		}
	}
	same("built")
	for _, f := range []string{"pages.idx", "sections.idx"} {
		if _, err := os.Stat(statePath("index", f)); err != nil {
			t.Fatal(err)
		}
	}

	restartSearch()
	before := pageReads.Load()
	pagesIx, chunksIx := searchIndex(nil), chunkIndex(nil)
	if n := pageReads.Load() - before; n != 1 {
		t.Errorf("a start read %d pages, want 1 (the one with [[task:…]])", n)
	}
	if pagesIx.count() != len(pages) || chunksIx.count() < 3*30 {
		t.Fatalf("%d page docs, %d section docs", pagesIx.count(), chunksIx.count())
	}
	for _, d := range chunksIx.all() {
		if d.Slug == "notes/p07" && d.heading() == "Über" {
			if body, ok := d.body(); !ok || body != "Plums." || d.anchor() != "#über" || d.path() != "Page 7 › Pears › Über" && d.path() != "Pears › Über" {
				t.Errorf("a section from the file: %q %v %q %q", body, ok, d.anchor(), d.path())
			}
		}
	}
	same("a start")

	path := filepath.Join(cfg.Data, "notes", "p03.md")
	if err := os.WriteFile(path, []byte("# Page 3\n\nNow about quinces.\n"), 0o666); err != nil {
		t.Fatal(err)
	}
	later := time.Now().Add(time.Hour)
	if err := os.Chtimes(path, later, later); err != nil {
		t.Fatal(err)
	}
	restartSearch()
	before = pageReads.Load()
	searchIndex(nil)
	chunkIndex(nil)
	// tasky; p03 for its page doc and its sections; p03's tokens for each
	// index's changes.
	if n := pageReads.Load() - before; n > 5 {
		t.Errorf("a start after an edit read %d pages", n)
	}
	queries = append(queries, "quinces")
	same("a start after an edit")
}

// A doc's meta in the index file makes the doc again; one that isn't a
// doc's (a task's, cut short, another version) makes none.
func TestDocMeta(t *testing.T) {
	testWiki(t, nil)
	mtime := time.Date(2026, 11, 8, 15, 4, 5, 123456789, time.UTC)
	key := textsearch.DocKey{1, 2, 3}
	for _, d := range []*SearchDoc{
		{Slug: "notes/über", Title: "Über alles", Mtime: mtime, Tags: []string{"a", "b c"}, TaskRefs: true, file: &pageFile{}},
		{Slug: "x", Title: "", Mtime: mtime, Section: true, nth: 300, file: &pageFile{}},
	} {
		got, ok := docFromMeta(docMeta(d), key)
		if !ok || got.Slug != d.Slug || got.Title != d.Title || !got.Mtime.Equal(d.Mtime) || !slices.Equal(got.Tags, d.Tags) ||
			got.Section != d.Section || got.TaskRefs != d.TaskRefs || got.nth != d.nth || got.key != key ||
			got.file == nil || got.file.path != filepath.Join(cfg.Data, filepath.FromSlash(d.Slug)+".md") {
			t.Errorf("%q: %+v", d.Slug, got)
		}
		m := docMeta(d)
		for cut := range len(m) {
			if _, ok := docFromMeta(m[:cut], key); ok {
				t.Errorf("%q cut at %d: a doc", d.Slug, cut)
			}
		}
	}
	if docMeta(&SearchDoc{Task: &Task{ID: "t"}}) != nil {
		t.Error("a task has a meta")
	}
	if _, ok := docFromMeta([]byte{metaVersion + 1, 0, 0, 0, 0}, key); ok {
		t.Error("another version's meta made a doc")
	}
}

// A rebuild with its base in a file merges from it: only the docs changed
// since are asked for their tokens, and it answers as one built anew.
func TestRebuildReadsOnlyChanges(t *testing.T) {
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
	c := &indexCache{file: "pages.idx"}
	c.get(nil, docs)
	first := c.base
	for i := 0; i < 40; i++ { // past maxChanges: built again
		docs[i*2] = doc(fmt.Sprintf("p%d", i*2), "apple", "pear", fmt.Sprintf("w%d", i%5))
	}
	asked = 0
	got := c.get(nil, docs).search("apple pear w3", 1000)
	if c.base == first {
		t.Fatal("not built again")
	}
	if asked != 40 {
		t.Errorf("the rebuild asked %d docs for their tokens, want the 40 changed", asked)
	}
	if fresh := newDocIndex(docs).search("apple pear w3", 1000); !slices.Equal(got, fresh) {
		t.Error("not as if built afresh")
	}
}

// A group's pages are in no index but its members' (docs/spec/search.md,
// Who searches what): to anyone else the search is, to the last number, that of a
// wiki without the folder, so neither a ranking nor a typo's suggestion
// tells that a word is in there.
func TestAGroupsPagesAreInItsMembersIndexAlone(t *testing.T) {
	testWiki(t, nil)
	withGroups(t, map[string][]string{"product": {"dev"}}, "alice:dev", "bob", "carol:dev")
	shared := []*SearchDoc{
		{Slug: "p1", Tokens: fixedTokens("apple", "pear", "roadmap")}, {Slug: "p2", Tokens: fixedTokens("pear", "pear", "plum")},
		{Slug: "p3", Tokens: fixedTokens("apple", "ocelotharq")},
	}
	groups := []*SearchDoc{
		{Slug: "groups/dev/a", Tokens: fixedTokens("ocelotharp", "apple", "apple", "apple")},
		{Slug: "groups/dev/b", Tokens: fixedTokens("ocelotharp", "pear", "pear", "pear", "pear", "pear", "pear")},
		{Slug: "groups/product/c", Tokens: fixedTokens("ocelotharp", "plum")},
		{Slug: "groups/nobody/d", Tokens: fixedTokens("ocelotharp")},
	}
	docs := slices.Concat(shared[:2], groups, shared[2:])
	var c indexCache
	bob, alice, carol := c.get(&Req{user: asUser("bob")}, docs), c.get(&Req{user: asUser("alice")}, docs), c.get(&Req{user: asUser("carol")}, docs)
	if !slices.Equal(docsOf(bob), shared) {
		t.Fatalf("bob's index has %d docs, want the %d shared ones", bob.count(), len(shared))
	}
	if alice.count() != 6 || !slices.Equal(docsOf(alice), docsOf(carol)) {
		t.Errorf("alice's index has %d docs, carol's %d: the shared ones and their groups' three", alice.count(), carol.count())
	}
	// A group's pages are indexed once, for whoever is in it: two layers
	// made (dev's and product's), none for the folder nobody is in.
	made := 0
	for _, l := range c.places {
		if l.layer != nil {
			made++
		}
	}
	if dev := c.places[docPlace{place: place{ofGroup, "dev"}}]; made != 2 || len(c.places) != 3 || dev == nil || dev.layer == nil {
		t.Errorf("%d layers made of %d places, want dev's and product's of three", made, len(c.places))
	}
	without := newDocIndex(shared)
	for _, q := range []string{"apple", "pear", "plum", "ocelotharp", "ocelothar", "ocelotharq", "apple pear", "roadmap"} {
		got, want := bob.search(q, 10), without.search(q, 10)
		if !slices.Equal(got, want) {
			t.Errorf("%q for bob: %d hits, in a wiki without the folder %d", q, len(got), len(want))
		}
	}
	// His typo of the group's word still finds his own page: the word is in no vocabulary of his.
	if hits := bob.search("ocelotharp", 10); len(hits) != 1 || hits[0].Slug != "p3" {
		t.Errorf("bob's search for the group's word: %d hits", len(hits))
	}
	if hits := alice.search("ocelotharp", 10); len(hits) != 3 {
		t.Errorf("alice's search for the group's word: %d hits, want her groups' three", len(hits))
	}
	// Zone itself has every doc.
	if all := c.get(nil, docs); all.count() != len(docs) {
		t.Errorf("Zone's own index has %d docs", all.count())
	}
}

// A list for some is a place too: its tasks are in its members' index alone,
// and a list narrowed or widened splits the docs again though no task
// changed.
func TestAListForSomesTasksAreInItsMembersIndexAlone(t *testing.T) {
	testWiki(t, nil)
	withGroups(t, nil, "alice:hiring", "bob", "carol:hiring")
	dir := filepath.Join(cfg.Data, "_tasks", "lists", "hiring")
	entry := filepath.Join(dir, "list.json")
	if err := os.MkdirAll(dir, 0o777); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(entry, []byte(`{"name": "hiring", "for": ["@hiring"]}`), 0o666); err != nil {
		t.Fatal(err)
	}
	task := func(id, list string, words ...string) *SearchDoc {
		return &SearchDoc{Slug: "task:" + id, Task: &Task{ID: id, List: list}, Tokens: fixedTokens(words...)}
	}
	shared := []*SearchDoc{
		{Slug: "p1", Tokens: fixedTokens("apple", "pear")}, task("t1", "team", "pear", "plum"),
		{Slug: "p3", Tokens: fixedTokens("apple", "ocelotharq")},
	}
	theirs := []*SearchDoc{
		task("h1", "hiring", "ocelotharp", "apple", "apple"), task("h2", "hiring", "ocelotharp", "pear", "pear", "pear"),
	}
	docs := slices.Concat(shared[:2], theirs, shared[2:])
	var c indexCache
	bob, alice := c.get(&Req{user: asUser("bob")}, docs), c.get(&Req{user: asUser("alice")}, docs)
	if !slices.Equal(docsOf(bob), shared) {
		t.Fatalf("bob's index has %d docs, want the %d shared ones", bob.count(), len(shared))
	}
	if alice.count() != 5 || !slices.Equal(docsOf(alice), docsOf(c.get(&Req{user: asUser("carol")}, docs))) {
		t.Errorf("alice's index has %d docs, want the shared ones and the list's two", alice.count())
	}
	without := newDocIndex(shared)
	for _, q := range []string{"apple", "pear", "plum", "ocelotharp", "ocelothar", "ocelotharq", "apple pear"} {
		if got, want := bob.search(q, 10), without.search(q, 10); !slices.Equal(got, want) {
			t.Errorf("%q for bob: %d hits, in a wiki without the list %d", q, len(got), len(want))
		}
	}
	if hits := bob.search("ocelotharp", 10); len(hits) != 1 || hits[0].Slug != "p3" {
		t.Errorf("bob's search for the list's word: %d hits, want his own page by the typo", len(hits))
	}
	if hits := alice.search("ocelotharp", 10); len(hits) != 2 {
		t.Errorf("alice's search for the list's word: %d hits, want the list's two", len(hits))
	}
	// Widened, with no task changed: everyone's.
	if err := os.Remove(entry); err != nil {
		t.Fatal(err)
	}
	if bob := c.get(&Req{user: asUser("bob")}, docs); bob.count() != 5 || len(bob.search("ocelotharp", 10)) != 2 {
		t.Errorf("the list widened: bob's index has %d docs", bob.count())
	}
	// And narrowed again.
	if err := os.WriteFile(entry, []byte(`{"name": "hiring", "for": ["alice"]}`), 0o666); err != nil {
		t.Fatal(err)
	}
	if bob, carol := c.get(&Req{user: asUser("bob")}, docs), c.get(&Req{user: asUser("carol")}, docs); bob.count() != 3 || carol.count() != 3 {
		t.Errorf("the list narrowed to alice: bob's index has %d docs, carol's %d", bob.count(), carol.count())
	}
}

// A search asks about each list once (docHider) and leaves out what
// hiddenDoc does, for each user: a list for some's tasks to who is none of
// them, another's private task and pages; and the next search sees a list
// narrowed in the meantime.
func TestASearchHidesWhatHiddenDocDoes(t *testing.T) {
	testWiki(t, nil)
	withGroups(t, nil, "alice:hiring", "bob", "carol:hiring")
	dir := filepath.Join(cfg.Data, "_tasks", "lists", "hiring")
	if err := os.MkdirAll(dir, 0o777); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "list.json"), []byte(`{"name": "hiring", "for": ["@hiring"]}`), 0o666); err != nil {
		t.Fatal(err)
	}
	task := func(id, owner, list string) *SearchDoc {
		return &SearchDoc{Slug: "task:" + id, Task: &Task{ID: id, Owner: owner, List: list}}
	}
	docs := []*SearchDoc{
		task("t1", "", ""), task("t2", "", "team"), task("h1", "", "hiring"), task("t3", "", "team"),
		task("h2", "", "hiring"), task("a1", "alice", ""), task("b1", "bob", "mine"),
		{Slug: "p1"}, {Slug: "private/alice/notes"}, {Slug: "groups/hiring/plan"},
	}
	for _, rq := range []*Req{nil, {user: asUser("alice")}, {user: asUser("bob")}, {user: asUser("carol")}, {user: asUser(visitor)}} {
		hidden := docHider(rq)
		for _, d := range docs {
			if got, want := hidden(d), hiddenDoc(rq, d); got != want {
				t.Errorf("%v: %s hidden %v, hiddenDoc says %v", rq, d.Slug, got, want)
			}
		}
	}
	if hidden := docHider(&Req{user: asUser("bob")}); !hidden(docs[2]) || hidden(docs[1]) {
		t.Errorf("bob sees the list for @hiring's task, or not the team's")
	}
	if err := os.WriteFile(filepath.Join(dir, "list.json"), []byte(`{"name": "hiring", "for": ["alice"]}`), 0o666); err != nil {
		t.Fatal(err)
	}
	if hidden := docHider(&Req{user: asUser("carol")}); !hidden(docs[2]) {
		t.Errorf("the list narrowed to alice: carol's next search still has its task")
	}
}

// How long a member's index takes to put together when their group's folder
// is large (measured when group folders were built: 2,000 pages in a group of
// twenty); it says the time, and fails only where it is far off.
func TestAMembersIndexOfALargeGroup(t *testing.T) {
	if testing.Short() {
		t.Skip("a measurement")
	}
	testWiki(t, nil)
	users := []string{"outsider"}
	for i := 0; i < 20; i++ {
		users = append(users, fmt.Sprintf("m%d:dev", i))
	}
	withGroups(t, nil, users...)
	r := rand.New(rand.NewSource(7))
	text := func() []string {
		out := make([]string, 300)
		for i := range out {
			out[i] = fmt.Sprintf("w%d", r.Intn(5000))
		}
		return out
	}
	var docs []*SearchDoc
	for i := 0; i < 4000; i++ {
		slug := fmt.Sprintf("p%d", i)
		if i%2 == 0 {
			slug = "groups/dev/" + slug
		}
		docs = append(docs, &SearchDoc{Slug: slug, Tokens: fixedTokens(text()...)})
	}
	var c indexCache
	start := time.Now()
	c.get(&Req{user: asUser("outsider")}, docs)
	base := time.Since(start)
	start = time.Now()
	first := c.get(&Req{user: asUser("m0")}, docs)
	view := time.Since(start)
	start = time.Now()
	for i := 1; i < 20; i++ {
		c.get(&Req{user: asUser(fmt.Sprintf("m%d", i))}, docs)
	}
	others := time.Since(start) / 19
	start = time.Now()
	hits := first.search("w17 w4242", 15)
	t.Logf("2,000 shared pages and 2,000 of a group, 300 words each: the shared index %v, a member's view %v, each further member's %v, a search in it %v (%d hits)",
		base, view, others, time.Since(start), len(hits))
	if first.count() != 4000 || view > 5*time.Second {
		t.Errorf("a member's view: %d docs in %v", first.count(), view)
	}
}

// docsOf: the docs an index is over, in order.
func docsOf(x *docIndex) []*SearchDoc {
	var docs []*SearchDoc
	for _, d := range x.all() {
		docs = append(docs, d)
	}
	return docs
}
