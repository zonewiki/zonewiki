// Search and retrieval: BM25 over pages and deck tasks
// (search.go), fused with the optional semantic index (embed.go), for the
// browser (doSearch) and for LLMs (retrieve: sections rather than pages).
package main

import (
	"fmt"
	"maps"
	"net/url"
	"os"
	"path/filepath"
	"slices"
	"sort"
	"strings"
	"sync"
	"time"
)

// SearchDoc is a page, a page's section or a deck task as BM25 indexes it.
type SearchDoc struct {
	Slug, Title string
	Mtime       time.Time // a page's or section's file
	Tags        []string  // for #tag queries
	Tokens      []string
	ExcerptText string // a page's or task's searchable text
	TaskRefs    bool   // a page with [[task:…]], indexed with the tasks' titles

	// A section (the section index): the key is the one its vector has in
	// the semantic index.
	Section                          bool
	Heading, Path, Text, Anchor, Key string

	// A task: the whole task (the MCP tools' dates and tags), its status
	// ("open", "done"), the year of its archive, its last activity (dates).
	Task                         *Task
	TaskID, Status, Year, Edited string

	squashOnce sync.Once
	squashed   string
}

func (d *SearchDoc) isTask() bool { return d.Task != nil }

// text: the searchable text.
func (d *SearchDoc) text() string {
	if d.Section {
		return d.Title + "\n" + d.Heading + "\n" + d.Text
	}
	return d.ExcerptText
}

// date: the day for the date filters, a page's (or section's) last edit, a
// task's last activity.
func (d *SearchDoc) date() string {
	if d.isTask() {
		return d.Edited
	}
	return fmtDate(d.Mtime)
}

// hasPhrase: the squashed text, cached on the doc (which lives until its
// file changes), has the phrase.
func (d *SearchDoc) hasPhrase(phrase string) bool {
	d.squashOnce.Do(func() { d.squashed = squash(d.text()) })
	return strings.Contains(d.squashed, phrase)
}

// taskRefsAsTitles: page text with each [[task:…]] replaced by its task's
// title, what the reader sees there and what search should match.
func taskRefsAsTitles(content string) string {
	ms := taskRefRE.FindAllStringSubmatch(content, -1)
	if ms == nil {
		return content
	}
	refs := make([]string, len(ms))
	for i, m := range ms {
		refs[i] = strings.ToLower(m[1])
	}
	tasks := resolveTaskRefs(refs)
	return taskRefRE.ReplaceAllStringFunc(content, func(s string) string {
		if t := tasks[strings.ToLower(taskRefRE.FindStringSubmatch(s)[1])]; t != nil {
			return plainTaskTitle(t.Title)
		}
		return ""
	})
}

// pageCache keeps a value per page until its file changes (by mtime, so
// edits made outside the app count), or until fresh says it is stale.
type pageCache[T any] struct {
	mu sync.Mutex
	m  map[string]*pageCached[T]
}

type pageCached[T any] struct {
	mtime time.Time
	value T
	key   string
}

// perPage: build(slug, content, mtime) for every page, from the cache
// where it is fresh. Entries of pages that are gone are dropped. One bad
// file must not fail every search: a page deleted meanwhile is skipped, one
// that isn't UTF-8 is read with stand-ins for the undecodable bytes.
func perPage[T any](c *pageCache[T], build func(slug, content string, mtime time.Time) T,
	key string, fresh func(T, string) bool) []T {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.m == nil {
		c.m = map[string]*pageCached[T]{}
	}
	var out []T
	seen := map[string]bool{}
	for _, p := range iterPages() {
		st, err := os.Stat(p.Path)
		if err != nil {
			continue
		}
		mtime := st.ModTime()
		cached := c.m[p.Slug]
		if cached == nil || !cached.mtime.Equal(mtime) || fresh != nil && !fresh(cached.value, cached.key) {
			content, err := readText(p.Path)
			if err != nil {
				continue
			}
			content = strings.ToValidUTF8(content, "�")
			cached = &pageCached[T]{mtime, build(p.Slug, content, mtime), key}
			c.m[p.Slug] = cached
		}
		seen[p.Slug] = true
		out = append(out, cached.value)
	}
	for slug := range c.m {
		if !seen[slug] {
			delete(c.m, slug)
		}
	}
	return out
}

func repeatTokens(text string, times int) []string {
	t := tokenize(text)
	out := make([]string, 0, len(t)*times)
	for i := 0; i < times; i++ {
		out = append(out, t...)
	}
	return out
}

// pageSearchDoc: a page as a BM25 document, its text, its title
// (titleWeight extra times) and the words of its slug once, so a page is
// found by its file and folder names too without those outranking what the
// page says.
func pageSearchDoc(slug, content string, mtime time.Time) *SearchDoc {
	title := pageTitle(content, slug)
	text := stripLinkTargets(taskRefsAsTitles(content))
	tokens := append(tokenize(text), repeatTokens(title, titleWeight)...)
	return &SearchDoc{Slug: slug, Title: title, Mtime: mtime, TaskRefs: taskRefRE.MatchString(content),
		Tags: pageTags(content), ExcerptText: text, Tokens: append(tokens, tokenize(slug)...)}
}

var searchDocCache pageCache[*SearchDoc]

// pageSearchDocs: every page as a BM25 document. A page with [[task:…]] is
// indexed with the tasks' titles, so it is stale when the deck changes too.
func pageSearchDocs() []*SearchDoc {
	deckKey := deckMtimes()
	return perPage(&searchDocCache, pageSearchDoc, deckKey,
		func(d *SearchDoc, key string) bool { return !d.TaskRefs || key == deckKey })
}

// deckMtimes: the deck files' paths and mtimes, archives included (a cache
// key).
func deckMtimes() string {
	var b strings.Builder
	for _, p := range append([]string{deckFile(), completedPath()}, archiveGlob()...) {
		if st, err := os.Stat(p); err == nil {
			fmt.Fprintf(&b, "%s\x00%d\x00", p, st.ModTime().UnixNano())
		} else {
			fmt.Fprintf(&b, "%s\x00-\x00", p)
		}
	}
	return b.String()
}

var taskDocs struct {
	mu   sync.Mutex
	key  string
	docs []*SearchDoc
	done bool
}

// taskSearchDocs: every task as a BM25 document, the title weighted,
// cached until a deck file changes.
func taskSearchDocs() []*SearchDoc {
	key := deckMtimes()
	taskDocs.mu.Lock()
	defer taskDocs.mu.Unlock()
	if taskDocs.done && taskDocs.key == key {
		return taskDocs.docs
	}
	docs := []*SearchDoc{}
	for _, t := range allTasks() {
		title := t.Title
		var comments, tags, lowTags []string
		for _, c := range t.Comments {
			comments = append(comments, c.Text)
		}
		// Tags as "#tag" words, so a page's tag pill (/search?q=<tag>) finds
		// the tasks with that tag too, and the excerpt can show the match.
		for _, tag := range t.Tags {
			tags = append(tags, "#"+tag)
			lowTags = append(lowTags, strings.ToLower(tag))
		}
		text := stripLinkTargets(strings.TrimSpace(title + " " + t.Description + " " +
			strings.Join(comments, " ") + " " + strings.Join(tags, " ")))
		d := &SearchDoc{Slug: deckDirName, Title: "[Task] " + plainTaskTitle(title), ExcerptText: text,
			Tokens: append(tokenize(text), repeatTokens(plainTaskTitle(title), titleWeight)...),
			Task:   t, TaskID: t.ID, Status: "open", Tags: lowTags, Edited: lastActivity(t)}
		if !t.isOpen() {
			d.Status, d.Year = "done", pastYear(t)
		}
		docs = append(docs, d)
	}
	taskDocs.key, taskDocs.docs, taskDocs.done = key, docs, true
	return docs
}

// docIndex is BM25 over docs.
type docIndex struct {
	docs []*SearchDoc
	ix   *Index
}

func newDocIndex(docs []*SearchDoc) *docIndex {
	tokens := make([][]string, len(docs))
	for i, d := range docs {
		tokens[i] = d.Tokens
	}
	return &docIndex{docs, NewIndex(tokens)}
}

// search: the best `limit` docs for the query text.
func (x *docIndex) search(text string, limit int) []*SearchDoc {
	hits := x.ix.Search(text, limit)
	out := make([]*SearchDoc, len(hits))
	for i, h := range hits {
		out[i] = x.docs[h.Doc]
	}
	return out
}

// indexCache keeps the BM25 index over the current docs until one changes.
// The doc caches hand back the same docs until their file changes, so the
// docs' pointers are the key.
type indexCache struct {
	mu    sync.Mutex
	index *docIndex
}

func (c *indexCache) get(docs []*SearchDoc) *docIndex {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.index == nil || !slices.Equal(c.index.docs, docs) {
		c.index = newDocIndex(docs)
	}
	return c.index
}

var searchIndexCache, chunkIndexCache indexCache

func searchIndex() *docIndex {
	return searchIndexCache.get(append(pageSearchDocs(), taskSearchDocs()...))
}

// semanticText: a page as (title, body) for embedding and the section
// index: the tldr and tags lead, the frontmatter block and the H1 (the
// title already) are dropped, and so are [[deck …]] directives (markup, not
// content: pages holding nothing but a deck embed came out as each other's
// closest match).
func semanticText(content, slug string) (string, string) {
	meta, body := parseFrontmatter(taskRefsAsTitles(content))
	var lead []string
	for _, s := range []string{meta.Tldr, strings.Join(meta.Tags, " ")} {
		if s != "" {
			lead = append(lead, s)
		}
	}
	body = deckRE.ReplaceAllString(stripH1(body), "")
	if len(lead) > 0 {
		body = strings.Join(lead, "\n") + "\n\n" + body
	}
	return pageTitle(content, slug), body
}

// fuseDepth: how deep each ranking goes into the fusion (semantic search).
const fuseDepth = 30

// passes: whether a doc takes part in a search, it passes keep (when
// given) and the query's filters.
func passes(q *Query, d *SearchDoc, keep func(*SearchDoc) bool) bool {
	return (keep == nil || keep(d)) && (!q.HasFilters() || q.Accepts(d.text(), d.Tokens, d.Tags, d.date()))
}

// phraseFirst: ranked with the docs containing the query as a phrase moved
// to the front (in BM25 order), including ones the ranking had cut off: a
// page saying exactly what was typed beats one that has the words apart.
func phraseFirst(ranked []*SearchDoc, q *Query, x *docIndex, keep func(*SearchDoc) bool) []*SearchDoc {
	phrase := q.Phrase()
	if phrase == "" {
		return ranked
	}
	var hits []*SearchDoc
	first := map[*SearchDoc]bool{}
	for _, d := range x.search(q.RankingText(), len(x.docs)) {
		if passes(q, d, keep) && d.hasPhrase(phrase) {
			hits = append(hits, d)
			first[d] = true
		}
	}
	if hits == nil {
		return ranked
	}
	for _, d := range ranked {
		if !first[d] {
			hits = append(hits, d)
		}
	}
	return hits
}

// bm25: the best `depth` docs by BM25 for the query's ranking text, among
// those that pass its filters (and keep, when given).
func bm25(x *docIndex, q *Query, depth int, keep func(*SearchDoc) bool) []*SearchDoc {
	text := q.RankingText()
	if !q.HasFilters() && keep == nil {
		return x.search(text, depth)
	}
	var out []*SearchDoc
	for _, d := range x.search(text, len(x.docs)) {
		if passes(q, d, keep) {
			out = append(out, d)
		}
	}
	return out[:min(depth, len(out))]
}

func firstN(docs []*SearchDoc, n int) []*SearchDoc { return docs[:min(n, len(docs))] }

// rankedDocs: the `limit` best docs for the query by keywords. Docs that
// fail the query's filters, or keep when given, take no part.
func rankedDocs(q *Query, x *docIndex, keep func(*SearchDoc) bool, limit int) []*SearchDoc {
	if strings.TrimSpace(q.RankingText()) == "" {
		if len(q.Dates) == 0 {
			return nil
		}
		// Only date filters ("edited:2026-09"): everything that passes,
		// newest first. There are no words to rank by.
		var docs []*SearchDoc
		for _, d := range x.docs {
			if passes(q, d, keep) {
				docs = append(docs, d)
			}
		}
		sort.SliceStable(docs, func(i, j int) bool { return docs[i].date() > docs[j].date() })
		return firstN(docs, limit)
	}
	// Each ranking goes as deep as the fused list must be long.
	depth := limit
	if semantic != nil {
		depth = max(fuseDepth, limit)
	}
	ranked := bm25(x, q, depth, keep)
	if semantic == nil {
		return firstN(phraseFirst(ranked, q, x, keep), limit)
	}
	semantic.poke()
	pages, tasks := map[string]*SearchDoc{}, map[string]*SearchDoc{}
	for _, d := range x.docs {
		if !passes(q, d, keep) {
			continue
		}
		if d.isTask() {
			tasks[d.TaskID] = d
		} else {
			pages[d.Slug] = d
		}
	}
	text := q.RankingText()
	var pageHits []docScore
	for _, h := range semantic.search(text, keySet(pages), depth) {
		pageHits = append(pageHits, docScore{pages[h.Slug], h.Score})
	}
	tHits := taskHits(text, tasks, depth)
	if pageHits == nil && tHits == nil {
		return firstN(phraseFirst(ranked, q, x, keep), limit)
	}
	meaning := firstN(bySimilarity(pageHits, tHits), depth)
	return firstN(phraseFirst(Fuse([][]*SearchDoc{ranked, meaning}), q, x, keep), limit)
}

// bestSections: for each page in slugs, the section a result should open:
// one whose own text has the query as a phrase, else the best BM25-scored
// section whose own text has a query word, else, for a page found by
// meaning, its most similar section. A page matched only by its title gets
// none (every section would match equally), so its link opens the top of
// the page.
func bestSections(q *Query, slugs map[string]bool) map[string]*SearchDoc {
	text := q.RankingText()
	x := chunkIndex()
	var ranked []*SearchDoc
	for _, d := range x.search(text, len(x.docs)) {
		if !d.isTask() && slugs[d.Slug] {
			ranked = append(ranked, d)
		}
	}
	best := map[string]*SearchDoc{}
	if phrase := q.Phrase(); phrase != "" {
		for _, d := range ranked {
			if best[d.Slug] == nil && strings.Contains(squash(d.Heading+"\n"+d.Text), phrase) {
				best[d.Slug] = d
			}
		}
	}
	for _, d := range ranked {
		if best[d.Slug] == nil && Matches(d.Heading+"\n"+d.Text, text) {
			best[d.Slug] = d
		}
	}
	if semantic != nil && len(best) < len(slugs) {
		rest := map[string]bool{}
		for slug := range slugs {
			if best[slug] == nil {
				rest[slug] = true
			}
		}
		byKey := map[string]*SearchDoc{}
		for _, d := range x.docs {
			if !d.isTask() && rest[d.Slug] {
				byKey[d.Key] = d
			}
		}
		for _, h := range semantic.searchChunks(text, rest, max(len(byKey), 1)) {
			if d := byKey[h.ID]; d != nil && best[d.Slug] == nil {
				best[d.Slug] = d
			}
		}
	}
	return best
}

// taskStatus: "open", "done" or "won't do".
func taskStatus(t *Task) string {
	switch {
	case t.isOpen():
		return "open"
	case t.WontDo:
		return "won't do"
	}
	return "done"
}

// resultMeta: the small line under a result's title, telling apart
// results whose titles look alike: a page's folder and last edit, a task's
// list and status ("Deck · done 2026-05-01").
func resultMeta(d *SearchDoc, names map[string]DeckHome) string {
	if d.isTask() {
		status := taskStatus(d.Task)
		if status != "open" {
			status += " " + runePrefix(d.Task.CompletedAt, 10)
		}
		where := "Deck"
		if l := taskList(d.Task, names); l != "" {
			where += " › " + l
		}
		return where + " · " + status
	}
	edited := "edited " + fmtDate(d.Mtime)
	if i := strings.LastIndex(d.Slug, "/"); i >= 0 {
		return d.Slug[:i] + " · " + edited
	}
	return edited
}

const (
	searchLimit        = 15   // results in the search modal (/api/search default)
	searchPageLimit    = 50   // results on the /search page ("All results")
	searchMaxLimit     = 100  //
	scratchFilterLimit = 1000 // the Scratch page's filter lists every match
)

// doSearch: search results for the browser, at most limit. unlinked keeps
// only pages no other page links to (the search modal's "Unlinked only"),
// no tasks; scratch only scratch notes (the Scratch page's filter).
func doSearch(rq *Req, raw string, unlinked bool, limit int, scratch bool) []M {
	results := []M{}
	if strings.TrimSpace(raw) == "" {
		return results
	}
	q := ParseQuery(raw)
	text := q.RankingText() // what excerpts mark: the words, without operators
	var tests []func(*SearchDoc) bool
	if unlinked {
		linked := map[string]bool{}
		for slug, n := range allBacklinkCounts(rq) {
			if n > 0 {
				linked[slug] = true
			}
		}
		tests = append(tests, func(d *SearchDoc) bool { return !d.isTask() && !linked[d.Slug] })
	}
	if scratch {
		tests = append(tests, func(d *SearchDoc) bool { return !d.isTask() && isScratch(d.Slug) })
	}
	if rq.sharing || cfg.Users != nil {
		tests = append(tests, func(d *SearchDoc) bool { return d.isTask() || !hiddenPage(rq, d.Slug) })
	}
	var keep func(*SearchDoc) bool
	if tests != nil {
		keep = func(d *SearchDoc) bool {
			for _, t := range tests {
				if !t(d) {
					return false
				}
			}
			return true
		}
	}
	docs := rankedDocs(q, searchIndex(), keep, limit)
	slugs := map[string]bool{}
	for _, d := range docs {
		if !d.isTask() {
			slugs[d.Slug] = true
		}
	}
	sections := bestSections(q, slugs)
	names := deckLists()
	for _, d := range docs {
		var section *SearchDoc
		excerptOf := d.ExcerptText
		if !d.isTask() {
			if section = sections[d.Slug]; section != nil {
				excerptOf = strings.TrimSpace(section.Heading + "\n" + section.Text)
			}
		}
		r := M{"slug": d.Slug, "title": d.Title, "excerpt": htmlOf(Excerpt(excerptOf, text)),
			"meta": resultMeta(d, names)}
		if d.isTask() {
			r["type"], r["task_id"], r["status"] = "task", d.TaskID, d.Status
			if d.Year != "" {
				r["year"] = d.Year
			}
			tab := "open"
			if d.Status == "done" {
				tab = "completed"
			}
			list := taskList(d.Task, names)
			if list != "" {
				r["list"] = list
			}
			r["href"] = taskHref(d.TaskID, tab, list, d.Year)
		} else {
			href := pathURL("page", d.Slug)
			// The page highlights what matched (js/search.js); before the #anchor.
			if words := MatchedWords(d.ExcerptText, text, 10); len(words) > 0 {
				hl := make([]string, len(words))
				for i, w := range words {
					hl[i] = "hl=" + url.QueryEscape(w)
				}
				href += "?" + strings.Join(hl, "&")
			}
			if section != nil {
				href += section.Anchor
			}
			r["href"] = href
		}
		results = append(results, r)
	}
	return results
}

// The section index: like the page search, but over sections rather than
// pages, with their full text. An LLM needs the passages, and the best
// ones may share a page; the browser search takes a result's section from
// it too.

var chunkDocCache pageCache[[]*SearchDoc]

// sectionAnchorOf: "#<id>" of a section's heading as the rendered page has
// it, or "" when there is none: no heading, or one deeper than h3, which
// carries no id.
func sectionAnchorOf(heading string, level int) string {
	if heading == "" || level > 3 {
		return ""
	}
	return "#" + headingID(plainHeading(heading))
}

// pageChunks: a page's sections (chunkSections) as BM25 documents.
func pageChunks(slug, content string, mtime time.Time) []*SearchDoc {
	title, body := semanticText(content, slug)
	tags := pageTags(content)
	sections := chunkSections(body, maxChunkChars)
	if len(sections) == 0 {
		sections = []chunkSection{{}}
	}
	var chunks []*SearchDoc
	titleTokens, slugTokens := repeatTokens(title, titleWeight), tokenize(slug)
	for _, s := range sections {
		tokens := append(tokenize(s.Heading+"\n"+s.Body), titleTokens...)
		chunks = append(chunks, &SearchDoc{Slug: slug, Title: title, Section: true, Heading: s.Heading,
			Path: s.Path, Text: s.Body, Anchor: sectionAnchorOf(s.Heading, s.Level), Tags: tags, Mtime: mtime,
			Key: chunkKey(title, chunkText(s.Path, s.Body)), Tokens: append(tokens, slugTokens...)})
	}
	return chunks
}

// chunkIndex: BM25 over page sections and deck tasks (a task is one
// result, like a section), as the browser search ranks pages and tasks
// together.
func chunkIndex() *docIndex {
	var docs []*SearchDoc
	for _, chunks := range perPage(&chunkDocCache, pageChunks, "", nil) {
		docs = append(docs, chunks...)
	}
	return chunkIndexCache.get(append(docs, taskSearchDocs()...))
}

// inFolder: slug is the page folder or below it (case ignored).
func inFolder(slug, folder string) bool {
	if folder == "" {
		return true
	}
	slug, folder = strings.ToLower(slug), strings.ToLower(folder)
	return slug == folder || strings.HasPrefix(slug, folder+"/")
}

// retrieve: the sections and tasks most relevant to the query, best first.
// The query syntax applies. With under, only sections of pages in that
// folder, with scratch only of scratch notes; either way no tasks.
func retrieve(raw string, limit int, under string, scratch bool) []*SearchDoc {
	q := ParseQuery(raw)
	if strings.TrimSpace(q.RankingText()) == "" {
		return nil
	}
	x := chunkIndex()
	pagesOnly := under != "" || scratch
	pageOK := func(slug string) bool { return inFolder(slug, under) && (!scratch || isScratch(slug)) }
	var keep func(*SearchDoc) bool
	if pagesOnly {
		keep = func(d *SearchDoc) bool { return !d.isTask() && pageOK(d.Slug) }
	}
	ranked := bm25(x, q, fuseDepth, keep)
	if semantic != nil {
		semantic.poke()
		text := q.RankingText()
		byKey, slugs, tasks := map[string]*SearchDoc{}, map[string]bool{}, map[string]*SearchDoc{}
		for _, d := range x.docs {
			switch {
			case !passes(q, d, nil):
			case !d.isTask() && pageOK(d.Slug):
				byKey[d.Key], slugs[d.Slug] = d, true
			case d.isTask() && !pagesOnly:
				tasks[d.TaskID] = d
			}
		}
		var hits, tHits []docScore
		for _, h := range semantic.searchChunks(text, slugs, fuseDepth) {
			if d := byKey[h.ID]; d != nil {
				hits = append(hits, docScore{d, h.Score})
			}
		}
		if !pagesOnly {
			tHits = taskHits(text, tasks, fuseDepth)
		}
		if hits != nil || tHits != nil {
			ranked = Fuse([][]*SearchDoc{ranked, firstN(bySimilarity(hits, tHits), fuseDepth)})
		}
	}
	return firstN(phraseFirst(ranked, q, x, keep), limit)
}

// search_wiki's queries, one JSON line each (LEAF_QUERY_LOG), for turning
// into questions for leaf eval (eval.go). In .leaf/, so never in git.
// Past queryLogMax bytes the log moves to queries.1.jsonl (the one before
// is dropped), so it can't grow without end.
var queryLogMax int64 = 1_000_000

var queryLogMu sync.Mutex

func queryLogPath() string { return filepath.Join(cfg.Data, ".leaf", "queries.jsonl") }

// resultTarget: a result as the eval's questions name it, slug#anchor or
// task:<id>.
func resultTarget(d *SearchDoc) string {
	if d.isTask() {
		return "task:" + d.TaskID
	}
	return d.Slug + d.Anchor
}

// queryLogEntry: a line of the query log.
type queryLogEntry struct {
	At      string   `json:"at"`
	Q       string   `json:"q"`
	Under   string   `json:"under,omitempty"`
	Results []string `json:"results"`
}

// logQuery appends a search_wiki query and what it found to the query log.
func logQuery(raw, under string, hits []*SearchDoc) {
	if !cfg.QueryLog {
		return
	}
	targets := make([]string, len(hits))
	for i, d := range hits {
		targets[i] = resultTarget(d)
	}
	entry := queryLogEntry{At: now().Format("2006-01-02T15:04:05"), Q: raw, Under: under, Results: targets}
	path := queryLogPath()
	queryLogMu.Lock()
	defer queryLogMu.Unlock()
	err := os.MkdirAll(filepath.Dir(path), 0o777)
	if err == nil {
		if st, e := os.Stat(path); e == nil && st.Size() > queryLogMax {
			err = os.Rename(path, filepath.Join(filepath.Dir(path), "queries.1.jsonl"))
		}
	}
	var f *os.File
	if err == nil {
		f, err = os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o666)
	}
	if err == nil {
		_, err = f.WriteString(jsonText(entry) + "\n")
		if cerr := f.Close(); err == nil {
			err = cerr
		}
	}
	if err != nil {
		fmt.Fprintf(os.Stderr, "[queries] could not write %s: %v\n", path, err)
	}
}

// relatedSection: the "Related" section, the pages most similar in
// meaning (semantic search only). Pages already linked either way are left
// out; the point is connections you haven't written down.
func relatedSection(rq *Req, slug, path string, backlinks []PageRef) M {
	if semantic == nil {
		return nil
	}
	semantic.poke()
	files := map[string]string{}
	known := map[string]bool{}
	for _, p := range iterPages() {
		files[p.Slug], known[p.Slug] = p.Path, true
	}
	_, exclude := pageMeta(path, slug)
	exclude = maps.Clone(exclude)
	for _, b := range backlinks {
		exclude[b.Slug] = true
	}
	var related []PageRef
	for _, r := range semantic.related(slug, known, exclude, 5) {
		if !hiddenPage(rq, r.ID) {
			title, _ := pageMeta(files[r.ID], r.ID)
			related = append(related, PageRef{Slug: r.ID, Title: title})
		}
	}
	if related == nil {
		return nil
	}
	return M{"id": "related", "heading": "Related", "links": pageLinks(related)}
}

// bySimilarity: one semantic ranking from several (doc, similarity) lists,
// page sections and tasks: all come from the same model, so their cosine
// similarities compare directly.
func bySimilarity(hitLists ...[]docScore) []*SearchDoc {
	var all []docScore
	for _, hits := range hitLists {
		all = append(all, hits...)
	}
	sort.SliceStable(all, func(i, j int) bool { return all[i].score > all[j].score })
	out := make([]*SearchDoc, len(all))
	for i, h := range all {
		out[i] = h.doc
	}
	return out
}

type docScore struct {
	doc   *SearchDoc
	score float64
}

// taskHits: the semantic index's tasks among those given, as docs.
func taskHits(q string, tasks map[string]*SearchDoc, limit int) []docScore {
	var hits []docScore
	for _, h := range semantic.searchTasks(q, keySet(tasks), limit) {
		hits = append(hits, docScore{tasks[h.ID], h.Score})
	}
	return hits
}
