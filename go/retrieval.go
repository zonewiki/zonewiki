// Search and retrieval: BM25 over pages and tasks
// (search.go), fused with the optional semantic index (embed.go), for the
// browser (doSearch) and for LLMs (retrieve: sections rather than pages).
package main

import (
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"fmt"
	"maps"
	"math"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"zonewiki/internal/mdtext"
	"zonewiki/internal/textsearch"
)

// SearchDoc is a page, a page's section or a task as BM25 indexes it. A
// page's or section's text isn't kept, nor its tokens, nor what a section
// shows (its heading, path, anchor, semantic key): a big wiki has hundreds
// of thousands of sections, and they were most of its memory. They are read
// from its file again when asked for (body, tokens, section), which a search
// does only for the docs it shows.
type SearchDoc struct {
	Slug, Title string
	Mtime       time.Time       // a page's or section's file
	Tags        []string        // for #tag queries
	Tokens      func() []string // a task's tokens (and a test doc's); a page's and a section's come from its file (tokens)
	TaskRefs    bool            // a page with [[task:…]], indexed with the task app's titles
	Section     bool            // a section (the section index)
	nth         int32           // a section: which of its page's sections it is
	file        *pageFile       // a page's or section's file

	// A task: the whole task (the MCP tools' dates and tags), and what its
	// doc adds (tk).
	Task *Task
	tk   *taskDocInfo

	key textsearch.DocKey // what its tokens are made from, hashed (docKey)
}

// taskDocInfo: what a task's doc holds beside the task: its searchable text,
// its status ("open", "done"), the year of its archive, its last activity
// (dates).
type taskDocInfo struct{ text, status, year, edited string }

func (d *SearchDoc) isTask() bool { return d.Task != nil }

// taskID: a task's id, "" for a page or section.
func (d *SearchDoc) taskID() string {
	if d.Task == nil {
		return ""
	}
	return d.Task.ID
}

// tokens: what BM25 indexes the doc by: a page's or section's from its file
// (indexed), a task's from its text.
func (d *SearchDoc) tokens() []string {
	if d.Tokens != nil {
		return d.Tokens()
	}
	if d.file != nil {
		toks, _, _ := d.indexed()
		return toks
	}
	return nil
}

// body: a page's text as search sees it, a section's own text, a task's
// text. False when the page is gone; a page changed since the doc was made
// gives its text now (the next search makes its doc anew).
func (d *SearchDoc) body() (string, bool) {
	body, _, ok := d.bodyNow()
	return body, ok
}

// bodyNow: body, and whether it is still what the doc was made from (false:
// the file changed since).
func (d *SearchDoc) bodyNow() (body string, same, ok bool) {
	if d.file == nil {
		if d.tk != nil {
			return d.tk.text, true, true
		}
		return "", true, true
	}
	t, ok := docTexts.of(d.file, d.Section)
	switch {
	case !ok:
		return "", false, false
	case int(d.nth) < len(t.texts):
		return t.texts[d.nth], t.same, true
	}
	return "", false, true
}

// section: a section's place in its page as its file has it now (its
// heading, path, level, text); none for a page or task, or a page gone.
func (d *SearchDoc) section() chunkSection {
	if !d.Section || d.file == nil {
		return chunkSection{}
	}
	t, ok := docTexts.of(d.file, true)
	if !ok || int(d.nth) >= len(t.secs) {
		return chunkSection{}
	}
	return t.secs[d.nth]
}

// heading, path, anchor: a section's heading, its path of headings, the
// #anchor it opens at (sectionAnchorOf); "" for a page or task.
func (d *SearchDoc) heading() string { return d.section().Heading }
func (d *SearchDoc) path() string    { return d.section().Path }
func (d *SearchDoc) anchor() string {
	if !d.Section {
		return ""
	}
	s := d.section()
	return sectionAnchorOf(s.Heading, s.Level)
}

// semKey: a section's key in the semantic index, the hash its vector has
// (chunkKey).
func (d *SearchDoc) semKey() string {
	s := d.section()
	return chunkKey(d.Title, chunkText(s.Path, s.Body))
}

// text: the searchable text.
func (d *SearchDoc) text() string {
	body, _ := d.body()
	if d.Section {
		return d.Title + "\n" + d.heading() + "\n" + body
	}
	return body
}

// indexed: a page's or section's tokens and its searchable text squashed,
// from its file, folded once for both (Squash and fold work rune by rune,
// so a section's text squashed is its title's and its own, one after the
// other). same: false when its file changed since the doc was made, so they
// are no longer what its key says.
func (d *SearchDoc) indexed() (toks []string, squashed string, same bool) {
	body, same, _ := d.bodyNow()
	own := body
	if d.Section {
		own = d.heading() + "\n" + body
	}
	toks, squashed = textsearch.TokensAndSquash(own)
	if title := textsearch.Squash(d.Title); d.Section && title != "" {
		squashed = strings.TrimSuffix(title+" "+squashed, " ")
	}
	return slices.Concat(toks, repeatTokens(d.Title, textsearch.TitleWeight), textsearch.Tokenize(d.Slug)), squashed, same
}

// squashed: the searchable text squashed, for phrases.
func (d *SearchDoc) squashed() string { return docTexts.squashed(d) }

// date: the day for the date filters, a page's (or section's) last edit, a
// task's last activity.
func (d *SearchDoc) date() string {
	if d.isTask() {
		return d.tk.edited
	}
	return fmtDate(d.Mtime)
}

// hasPhrase: the squashed text has the phrase.
func (d *SearchDoc) hasPhrase(phrase string) bool { return strings.Contains(d.squashed(), phrase) }

// pageFile: the file a page's docs were made from, which their texts are
// read from again; the page doc has one, and its sections share another.
// mtime: the file's when the docs were made.
type pageFile struct {
	slug, path string
	mtime      time.Time
}

// docTextsMax: how many bytes of texts read again docTexts keeps. Past it,
// it starts over: a search reads the texts of the docs it shows and of those
// it checks for a phrase, and the next search mostly the same ones.
const docTextsMax = 16 << 20

// docTexts: the texts of pages and sections read again from their files,
// and the docs' texts squashed.
var docTexts textCache

type textCache struct {
	mu       sync.Mutex
	files    map[*pageFile]fileTexts
	squashes map[*SearchDoc]string
	bytes    int
}

// fileTexts: a page doc's text, or each of its sections' (nil: the file
// can't be read); same: the file still had the mtime its docs were made at.
type fileTexts struct {
	texts []string
	secs  []chunkSection // a section's file: its sections (texts: their bodies)
	same  bool
}

// keep: room for n more bytes, starting over when they don't fit.
func (c *textCache) keep(n int) {
	if c.files == nil || c.bytes+n > docTextsMax {
		c.files, c.squashes, c.bytes = map[*pageFile]fileTexts{}, map[*SearchDoc]string{}, 0
	}
	c.bytes += n
}

// of: the texts of f (sections: its sections'), read from its file when not
// kept; false when it can't be read.
func (c *textCache) of(f *pageFile, sections bool) (fileTexts, bool) {
	c.mu.Lock()
	t, ok := c.files[f]
	c.mu.Unlock()
	if ok {
		return t, t.texts != nil
	}
	pageReads.Add(1)
	content, err := readText(f.path)
	if err == nil {
		// Its mtime after the read: if it's the docs', so is what was read.
		st, err := os.Stat(f.path)
		t.same = err == nil && st.ModTime().Equal(f.mtime)
		content = strings.ToValidUTF8(content, "�")
		if sections {
			t.secs = searchSections(f.slug, content)
			for _, s := range t.secs {
				t.texts = append(t.texts, s.Body)
			}
		} else {
			t.texts = []string{searchText(f.slug, content)}
		}
	}
	n := 0
	for _, s := range t.texts {
		n += len(s)
	}
	for _, s := range t.secs {
		n += len(s.Heading) + len(s.Path)
	}
	c.mu.Lock()
	c.keep(n)
	c.files[f] = t
	c.mu.Unlock()
	return t, t.texts != nil
}

func (c *textCache) squashed(d *SearchDoc) string {
	c.mu.Lock()
	sq, ok := c.squashes[d]
	c.mu.Unlock()
	if ok {
		return sq
	}
	sq = textsearch.Squash(d.text())
	c.mu.Lock()
	c.keep(len(sq))
	c.squashes[d] = sq
	c.mu.Unlock()
	return sq
}

// taskRefsAsTitles: the text of the page slug with each [[task:…]]
// replaced by its task's title, what the reader sees there and what search
// should match; only tasks everyone who sees the page sees (a private
// page's owner's own, else shared ones), so the index can't carry a
// private task's title into a shared page.
func taskRefsAsTitles(slug, content string) string {
	ms := taskRefRE.FindAllStringSubmatch(content, -1)
	if ms == nil {
		return content
	}
	refs := make([]string, len(ms))
	for i, m := range ms {
		refs[i] = strings.ToLower(m[1])
	}
	owner, _ := pageOwner(slug)
	tasks := resolveTaskRefs(owner, refs)
	return taskRefRE.ReplaceAllStringFunc(content, func(s string) string {
		if t := tasks[strings.ToLower(taskRefRE.FindStringSubmatch(s)[1])]; t != nil {
			return mdtext.PlainTaskTitle(t.Title)
		}
		return ""
	})
}

// indexKeyVersion: changes when what a doc's tokens are made of, or how
// they are made (textsearch.Tokenize), changes, so that no index file on disk
// is taken for docs it no longer fits.
const indexKeyVersion = "1"

// docKey: a doc's key in an index file, a hash of its kind and of what its
// tokens are made from: equal keys, equal tokens.
func docKey(kind string, parts ...string) textsearch.DocKey {
	h := sha256.New()
	h.Write([]byte(indexKeyVersion + "\x00" + kind))
	for _, p := range parts {
		h.Write([]byte{0})
		h.Write([]byte(p))
	}
	var k textsearch.DocKey
	copy(k[:], h.Sum(nil))
	return k
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

// perPage: build(slug, content, mtime) for every page of pages (listed with
// their mtimes, rq.pageStats), from the cache where it is fresh. Entries of
// pages that are gone are dropped. One bad file must not fail every search:
// a page deleted meanwhile is skipped, one that isn't UTF-8 is read with
// stand-ins for the undecodable bytes.
func perPage[T any](c *pageCache[T], pages []pageStat, build func(slug, path, content string, mtime time.Time) T,
	key string, fresh func(T, string) bool) []T {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.m == nil {
		c.m = map[string]*pageCached[T]{}
	}
	var out []T
	seen := map[string]bool{}
	for _, p := range pages {
		mtime := p.Mtime
		cached := c.m[p.Slug]
		if cached == nil || !cached.mtime.Equal(mtime) || fresh != nil && !fresh(cached.value, cached.key) {
			pageReads.Add(1)
			content, err := readText(p.Path)
			if err != nil {
				continue
			}
			content = strings.ToValidUTF8(content, "�")
			cached = &pageCached[T]{mtime, build(p.Slug, p.Path, content, mtime), key}
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
	t := textsearch.Tokenize(text)
	out := make([]string, 0, len(t)*times)
	for i := 0; i < times; i++ {
		out = append(out, t...)
	}
	return out
}

// searchText: a page's text as search sees it: [[task:…]] as the tasks'
// titles, without link targets.
func searchText(slug, content string) string {
	return textsearch.StripLinkTargets(taskRefsAsTitles(slug, content))
}

// pageSearchDoc: a page as a BM25 document, its text, its title
// (titleWeight extra times) and the words of its slug once, so a page is
// found by its file and folder names too without those outranking what the
// page says. Its text and tokens are read from the file again when asked for.
func pageSearchDoc(slug, path, content string, mtime time.Time) *SearchDoc {
	// Clones: the title and tags are pieces of content, and would keep the whole
	// file in memory for as long as the doc lives.
	title := strings.Clone(mdtext.PageTitle(content, slug))
	tags := mdtext.PageTags(content)
	for i, tag := range tags {
		tags[i] = strings.Clone(tag)
	}
	return &SearchDoc{Slug: slug, Title: title, Mtime: mtime, TaskRefs: taskRefRE.MatchString(content),
		Tags: tags, file: &pageFile{slug, path, mtime}, key: docKey("page", slug, title, searchText(slug, content))}
}

var searchDocCache pageCache[*SearchDoc]

// pageSearchDocs: every page as a BM25 document. A page with [[task:…]] is
// indexed with the task app's titles, so it is stale when the tasks changes too.
func pageSearchDocs(rq *Req) []*SearchDoc {
	seedPageDocs()
	tasksKey := tasksMtimes()
	return perPage(&searchDocCache, rq.pageStats(), pageSearchDoc, tasksKey,
		func(d *SearchDoc, key string) bool { return !d.TaskRefs || key == tasksKey })
}

// tasksMtimes: every owner's tasks files' paths and mtimes, archives
// included (a cache key).
func tasksMtimes() string {
	var b strings.Builder
	var paths []string
	for _, o := range everyOwner() {
		paths = append(append(paths, tasksFile(o), completedPath(o)), archiveGlob(o)...)
	}
	for _, p := range paths {
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
	byID map[string]*SearchDoc
	done bool
}

// taskSearchDocs: every task as a BM25 document, the title weighted,
// cached until a task list file changes; every owner's (searches keep those
// their user sees: canSeeTask).
func taskSearchDocs() []*SearchDoc {
	key := tasksMtimes()
	taskDocs.mu.Lock()
	defer taskDocs.mu.Unlock()
	if taskDocs.done && taskDocs.key == key {
		return taskDocs.docs
	}
	docs := []*SearchDoc{}
	previous := taskDocs.byID
	taskDocs.byID = map[string]*SearchDoc{}
	for _, t := range everyTask() {
		// A task that didn't change keeps its doc, so the index can tell
		// what did (the pointers are what it compares).
		if old := previous[t.ID]; old != nil && reflect.DeepEqual(old.Task, t) {
			taskDocs.byID[t.ID] = old
			docs = append(docs, old)
			continue
		}
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
		text := textsearch.StripLinkTargets(strings.TrimSpace(title + " " + t.Description + " " +
			strings.Join(comments, " ") + " " + strings.Join(tags, " ")))
		d := &SearchDoc{Slug: tasksDirName, Title: "[Task] " + mdtext.PlainTaskTitle(title),
			Tokens: func() []string {
				return slices.Concat(textsearch.Tokenize(text), repeatTokens(mdtext.PlainTaskTitle(title), textsearch.TitleWeight))
			},
			Task: t, tk: &taskDocInfo{text: text, status: "open", edited: lastActivity(t)}, Tags: lowTags,
			key: docKey("task", t.ID, mdtext.PlainTaskTitle(title), text)}
		if !t.isOpen() {
			d.tk.status, d.tk.year = "done", pastYear(t)
		}
		taskDocs.byID[t.ID] = d
		docs = append(docs, d)
	}
	taskDocs.key, taskDocs.docs, taskDocs.done = key, docs, true
	return docs
}

// docIndex is BM25 over docs.
type docIndex struct {
	docs []*SearchDoc // the docs it is over, in order
	byID []*SearchDoc // by the number the index gives them (docs, but for an index with changes)
	ids  []int        // each of docs' number in ix (nil: its place in docs)
	ix   *textsearch.Index
}

func newDocIndex(docs []*SearchDoc) *docIndex {
	return &docIndex{docs: docs, byID: docs, ix: textsearch.NewIndexOf(len(docs), func(i int) []string { return docs[i].tokens() })}
}

// idAt: the number in ix of docs[i].
func (x *docIndex) idAt(i int) int {
	if x.ids == nil {
		return i
	}
	return x.ids[i]
}

// search: the best `limit` docs for the query text.
func (x *docIndex) search(text string, limit int) []*SearchDoc {
	return x.searchWhere(text, limit, nil)
}

// searchWhere: search among the docs accept is true for (nil: all).
func (x *docIndex) searchWhere(text string, limit int, accept func(*SearchDoc) bool) []*SearchDoc {
	if accept == nil {
		return x.searchWhereID(text, limit, nil)
	}
	return x.searchWhereID(text, limit, func(_ int, d *SearchDoc) bool { return accept(d) })
}

// searchWhereID: searchWhere, accept told each doc's number in the index too.
func (x *docIndex) searchWhereID(text string, limit int, accept func(id int, d *SearchDoc) bool) []*SearchDoc {
	var ok func(int) bool
	if accept != nil {
		ok = func(i int) bool { return accept(i, x.byID[i]) }
	}
	hits := x.ix.SearchWhere(text, limit, ok)
	out := make([]*SearchDoc, len(hits))
	for i, h := range hits {
		out[i] = x.byID[h.Doc]
	}
	return out
}

// indexCache keeps the BM25 indexes over the current docs.
//
// With users, what is private is in no one's index but its owner's: an index
// is made from the shared docs, and a user's own private docs are added to it
// for them (Index.With), so another user's words, counts and lengths take no
// part in the ranking (document frequencies, lengths and the vocabulary
// typo matching draws on would otherwise show through).
//
// A change to a page or task doesn't build the index again: it keeps the one
// made at the last rebuild (base), leaves out the docs that are gone from it
// (dead) and adds the new ones (delta), searched with the statistics of what
// is left, exactly as an index built over that would. Only when the changes
// pass a few percent of the docs (maxChanges) is it built again. Docs don't
// keep their tokens, so a change and a rebuild work out the ones they need.
//
// The base is a file in .zone/index/ (file), mapped into memory rather than
// read into Go's heap, and written anew by a rebuild. A start finds it there:
// the docs whose key (docKey) it has get their numbers back, and the others
// are changes, as after an edit. Where no file can be written, the base is
// kept in memory.
//
// The doc caches hand back the same docs until their file changes, so the
// docs' pointers are the key.
type indexCache struct {
	file      string // in .zone/index/
	mu        sync.Mutex
	started   bool              // startFile was asked
	opened    *textsearch.Index // the base's file as a start found it, for load
	users     string            // who the docs were split for (usersKey)
	all       []*SearchDoc
	shared    []*SearchDoc // the shared docs now
	private   []*SearchDoc
	sharedGen int       // counts the changes to shared
	base      *docIndex // over the shared docs as they were at the last rebuild
	baseID    map[*SearchDoc]int
	reordered bool         // base's docs (from its file) are in another order than now: ties go by the view's
	dead      []int        // base docs no longer shared docs, by number
	delta     []*SearchDoc // shared docs that are not in base, in order
	views     map[string]*indexView
}

// indexView: the index of one viewer, with their own private docs.
type indexView struct {
	base *docIndex
	gen  int
	own  []*SearchDoc
	idx  *docIndex
}

// maxChanges: how many docs may differ from the base before it is built
// again; more only slow every search down, fewer rebuild too often.
func maxChanges(n int) int { return max(32, n/50) }

// docShared: d is in no private folder or tasks (always, without users).
func docShared(d *SearchDoc) bool {
	if d.isTask() {
		return d.Task.Owner == ""
	}
	_, private := pageOwner(d.Slug)
	return !private
}

// usersKey: which users there are; what is private depends on it (nothing is
// without users), so a settings reload that adds or removes one splits the
// docs again though none changed.
func usersKey() string {
	users := settings().Users
	if users == nil {
		return ""
	}
	return "users:" + strings.Join(sortedKeys(users), ",")
}

// get: the index rq searches (nil: Zone itself, over everything).
func (c *indexCache) get(rq *Req, docs []*SearchDoc) *docIndex {
	c.mu.Lock()
	defer c.mu.Unlock()
	key := usersKey()
	usersChanged := c.base == nil || key != c.users
	if usersChanged || !slices.Equal(c.all, docs) {
		c.users, c.all = key, slices.Clone(docs)
		var shared, private []*SearchDoc
		for _, d := range docs {
			if docShared(d) {
				shared = append(shared, d)
			} else {
				private = append(private, d)
			}
		}
		c.private = private
		if usersChanged || !slices.Equal(c.shared, shared) {
			c.shared = shared
			c.sharedGen++
			c.update(usersChanged)
		}
	}
	var own []*SearchDoc
	user := ""
	if rq != nil {
		user = rq.user
	}
	for _, d := range c.private {
		if rq == nil || docVisible(user, d) {
			own = append(own, d)
		}
	}
	if len(own) == 0 && len(c.dead) == 0 && len(c.delta) == 0 && !c.reordered {
		return c.base
	}
	viewKey := "user:" + user
	if rq == nil {
		viewKey = "zone"
	} else if len(own) == 0 {
		viewKey = "shared" // the same for everyone with nothing private of their own
	}
	if v := c.views[viewKey]; v != nil && v.base == c.base && v.gen == c.sharedGen && slices.Equal(v.own, own) {
		return v.idx
	}
	if c.views == nil {
		c.views = map[string]*indexView{}
	}
	idx := c.view(own)
	c.views[viewKey] = &indexView{c.base, c.sharedGen, own, idx}
	return idx
}

// update: the shared docs are c.shared now. Finds what differs from the base,
// or builds it again (rebuild, or when that is more than maxChanges). Without
// a base yet, it is the file's, when there is one.
func (c *indexCache) update(rebuild bool) {
	if c.base == nil {
		rebuild = !c.load()
	}
	if !rebuild {
		inShared := make(map[*SearchDoc]bool, len(c.shared))
		for _, d := range c.shared {
			inShared[d] = true
		}
		var dead []int
		for id, d := range c.base.docs {
			if !inShared[d] {
				dead = append(dead, id)
			}
		}
		var delta []*SearchDoc
		for _, d := range c.shared {
			if _, ok := c.baseID[d]; !ok {
				delta = append(delta, d)
			}
		}
		if len(dead)+len(delta) <= maxChanges(len(c.base.docs)) {
			c.dead, c.delta = dead, delta
			return
		}
	}
	c.build()
}

// indexPath: where the base's file is ("": nowhere, no wiki).
func (c *indexCache) indexPath() string {
	if cfg.Data == "" || c.file == "" {
		return ""
	}
	return statePath("index", c.file)
}

// writeIndexFiles: false where the wiki is only read (zonewiki eval): a
// file there is read, but a rebuild stays in memory.
var writeIndexFiles = true

// goneDoc stands in the base for a doc of its file that is no longer there:
// it is in no shared docs, so it is left out (dead).
var goneDoc = &SearchDoc{}

// load: the base from its file, the docs it still has numbered as there.
// False when there is none to use: then it is built.
func (c *indexCache) load() bool {
	path := c.indexPath()
	if path == "" {
		return false
	}
	ix, err := c.opened, error(nil)
	c.opened = nil
	if ix == nil {
		ix, err = textsearch.OpenIndex(path)
	}
	if err != nil {
		if !errors.Is(err, os.ErrNotExist) {
			logf("search index: %v; building it again", err)
		}
		return false
	}
	keys := ix.Keys()
	ids := make(map[textsearch.DocKey][]int, len(keys))
	for id, k := range keys {
		ids[k] = append(ids[k], id)
	}
	docs := make([]*SearchDoc, len(keys))
	baseID := make(map[*SearchDoc]int, len(c.shared))
	last, reordered := -1, false
	for _, d := range c.shared {
		same := ids[d.key]
		if len(same) == 0 {
			continue
		}
		id := same[0]
		ids[d.key] = same[1:]
		// In another order than now (a task moved in its list, say): equal
		// scores go by the order now, as in an index built anew (view).
		reordered = reordered || id < last
		last, docs[id], baseID[d] = id, d, id
	}
	for id, d := range docs {
		if d == nil {
			docs[id] = goneDoc
		}
	}
	c.base, c.baseID, c.dead, c.delta = &docIndex{docs: docs, byID: docs, ix: ix}, baseID, nil, nil
	c.reordered = reordered
	return true
}

// build: the base anew over the shared docs, written to its file and mapped,
// or kept in memory when that fails. With a base from a file, the new file
// is merged from it (MergeIndex): only the docs it doesn't have are read
// and tokenized.
func (c *indexCache) build() {
	docs := c.shared
	var ix *textsearch.Index
	if path := c.indexPath(); path != "" && writeIndexFiles {
		indexDoc := func(d *SearchDoc) textsearch.IndexDoc {
			if d.file != nil {
				toks, squashed, same := d.indexed()
				key := d.key
				if !same {
					// Its file changed since the doc was made: the next search
					// makes it anew; a start must not take these for the doc
					// its key names (no doc has the zero key).
					key = textsearch.DocKey{}
				}
				return textsearch.IndexDoc{Tokens: toks, Key: key, Text: squashed, Meta: docMeta(d)}
			}
			return textsearch.IndexDoc{Tokens: d.tokens(), Key: d.key, Text: textsearch.Squash(d.text())}
		}
		err := errors.New("no file to merge with")
		if c.base != nil && c.base.ix.Keys() != nil {
			// The docs the file has already come from it: only the others
			// are read and tokenized.
			old := c.base
			err = textsearch.MergeIndex(path, old.ix, len(docs), func(i int) textsearch.MergeDoc {
				if id, ok := c.baseID[docs[i]]; ok {
					return textsearch.MergeDoc{Old: id}
				}
				return textsearch.MergeDoc{Old: -1, Doc: indexDoc(docs[i])}
			})
		}
		if err != nil {
			err = textsearch.WriteIndex(path, len(docs), func(i int) textsearch.IndexDoc { return indexDoc(docs[i]) })
		}
		if err == nil {
			ix, err = textsearch.OpenIndex(path)
		}
		if err != nil {
			logf("search index: %v; kept in memory", err)
		}
	}
	if ix == nil {
		ix = textsearch.NewIndexOf(len(docs), func(i int) []string { return docs[i].tokens() })
	}
	c.base, c.reordered = &docIndex{docs: docs, byID: docs, ix: ix}, false
	c.baseID = make(map[*SearchDoc]int, len(docs))
	for id, d := range docs {
		c.baseID[d] = id
	}
	c.dead, c.delta = nil, nil
}

// view: the index of a viewer whose private docs are own: the base without
// its dead docs, with the delta and own docs added, the docs in the order
// they come in (c.all), as a fresh index over them would have them.
func (c *indexCache) view(own []*SearchDoc) *docIndex {
	n := len(c.base.byID)
	extra := slices.Concat(c.delta, own)
	extraID := make(map[*SearchDoc]int, len(extra))
	tokens := make([][]string, len(extra))
	for i, d := range extra {
		extraID[d] = n + i
		tokens[i] = d.tokens()
	}
	dead := make(map[int]bool, len(c.dead))
	for _, id := range c.dead {
		dead[id] = true
	}
	pos := make([]int32, n+len(extra))
	live := make([]*SearchDoc, 0, n-len(dead)+len(extra))
	ids := make([]int, 0, cap(live))
	for _, d := range c.all {
		id, ok := c.baseID[d]
		if !ok {
			id, ok = extraID[d]
		}
		if !ok {
			continue // another user's
		}
		pos[id] = int32(len(live))
		live = append(live, d)
		ids = append(ids, id)
	}
	return &docIndex{docs: live, byID: slices.Concat(c.base.byID, extra), ids: ids, ix: c.base.ix.With(dead, tokens, pos)}
}

// docVisible: user may see the search doc d (a page's, or a task's).
func docVisible(user string, d *SearchDoc) bool {
	if d.isTask() {
		return canSeeTask(user, d.Task)
	}
	return canSee(user, d.Slug)
}

var (
	searchIndexCache = indexCache{file: "pages.idx"}
	chunkIndexCache  = indexCache{file: "sections.idx"}
)

// searchIndex: the index rq searches (nil: Zone itself, over everything).
func searchIndex(rq *Req) *docIndex {
	return searchIndexCache.get(rq, append(pageSearchDocs(rq), taskSearchDocs()...))
}

// semanticText: a page as (title, body) for embedding and the section
// index: the tldr and tags lead, the frontmatter block and the H1 (the
// title already) are dropped, and so are [[tasks: …]] directives (markup, not
// content: pages holding nothing but a task list embed came out as each other's
// closest match).
func semanticText(content, slug string) (string, string) {
	meta, body := mdtext.ParseFrontmatter(taskRefsAsTitles(slug, content))
	var lead []string
	for _, s := range []string{meta.Tldr, strings.Join(meta.Tags, " ")} {
		if s != "" {
			lead = append(lead, s)
		}
	}
	body = tasksRE.ReplaceAllString(mdtext.StripH1(body), "")
	if len(lead) > 0 {
		body = strings.Join(lead, "\n") + "\n\n" + body
	}
	return mdtext.PageTitle(content, slug), body
}

// fuseDepth: how deep each ranking goes into the fusion (semantic search).
const fuseDepth = 30

// hasPhrase: d, number id in x (-1: not known), has the phrase in its text
// squashed: the copy in the index file when there is one, else its own.
func (x *docIndex) hasPhrase(id int, d *SearchDoc, phrase string) bool {
	if has, known := x.ix.TextHas(id, phrase); known {
		return has
	}
	return d.hasPhrase(phrase)
}

// filter: whether a doc (with its number in x; -1: not known) takes part in
// a search, it passes keep (when given) and the query's filters. The docs a
// -word excludes come from the index, so a filter is made once per search,
// not per doc.
func (x *docIndex) filter(q *textsearch.Query, keep func(*SearchDoc) bool) func(int, *SearchDoc) bool {
	excluded := map[*SearchDoc]bool{}
	for _, stem := range q.ExcludeStems {
		for _, id := range x.ix.DocsWith(stem) {
			excluded[x.byID[id]] = true
		}
	}
	filtered := q.HasFilters()
	return func(id int, d *SearchDoc) bool {
		return (keep == nil || keep(d)) && !excluded[d] &&
			(!filtered || q.AcceptsWith(func(p string) bool { return x.hasPhrase(id, d, p) }, d.Tags, d.date()))
	}
}

// phraseFirst: ranked with the docs containing the query as a phrase moved
// to the front (in BM25 order), including ones the ranking had cut off: a
// page saying exactly what was typed beats one that has the words apart.
func phraseFirst(ranked []*SearchDoc, q *textsearch.Query, x *docIndex, keep func(*SearchDoc) bool) []*SearchDoc {
	phrase := q.Phrase()
	if phrase == "" {
		return ranked
	}
	var hits []*SearchDoc
	first := map[*SearchDoc]bool{}
	passes := x.filter(q, keep)
	for _, d := range x.searchWhereID(q.RankingText(), len(x.docs), func(id int, d *SearchDoc) bool {
		return passes(id, d) && x.hasPhrase(id, d, phrase)
	}) {
		hits = append(hits, d)
		first[d] = true
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
func bm25(x *docIndex, q *textsearch.Query, depth int, keep func(*SearchDoc) bool) []*SearchDoc {
	text := q.RankingText()
	if !q.HasFilters() && keep == nil {
		return x.search(text, depth)
	}
	return x.searchWhereID(text, depth, x.filter(q, keep))
}

func firstN(docs []*SearchDoc, n int) []*SearchDoc { return docs[:min(n, len(docs))] }

// rankedDocs: the `limit` best docs for the query by keywords. Docs that
// fail the query's filters, or keep when given, take no part.
func rankedDocs(q *textsearch.Query, x *docIndex, keep func(*SearchDoc) bool, limit int) []*SearchDoc {
	if strings.TrimSpace(q.RankingText()) == "" {
		if len(q.Dates) == 0 {
			return nil
		}
		// Only date filters ("edited:2026-09"): everything that passes,
		// newest first. There are no words to rank by.
		var docs []*SearchDoc
		passes := x.filter(q, keep)
		for i, d := range x.docs {
			if passes(x.idAt(i), d) {
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
	passes := x.filter(q, keep)
	for i, d := range x.docs {
		if !passes(x.idAt(i), d) {
			continue
		}
		if d.isTask() {
			tasks[d.taskID()] = d
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
	return firstN(phraseFirst(textsearch.Fuse([][]*SearchDoc{ranked, meaning}), q, x, keep), limit)
}

// bestSections: for each page in slugs, the section a result should open:
// one whose own text has the query as a phrase, else the best BM25-scored
// section whose own text has a query word, else, for a page found by
// meaning, its most similar section. A page matched only by its title gets
// none (every section would match equally), so its link opens the top of
// the page.
func bestSections(rq *Req, q *textsearch.Query, slugs map[string]bool) map[string]*SearchDoc {
	text := q.RankingText()
	x := chunkIndex(rq)
	ranked := x.searchWhere(text, len(x.docs), func(d *SearchDoc) bool { return !d.isTask() && slugs[d.Slug] })
	best := map[string]*SearchDoc{}
	own := func(d *SearchDoc) string { // the section's own text
		body, _ := d.body()
		return d.heading() + "\n" + body
	}
	if phrase := q.Phrase(); phrase != "" {
		for _, d := range ranked {
			if best[d.Slug] == nil && strings.Contains(textsearch.Squash(own(d)), phrase) {
				best[d.Slug] = d
			}
		}
	}
	for _, d := range ranked {
		if best[d.Slug] == nil && textsearch.Matches(own(d), text) {
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
				byKey[d.semKey()] = d
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

// hiddenDoc: a search result to leave out for rq (nil: Zone, which sees
// all): another user's private task (canSeeTask), a page hiddenPage hides.
func hiddenDoc(rq *Req, d *SearchDoc) bool {
	if d.isTask() {
		return rq != nil && !canSeeTask(rq.user, d.Task)
	}
	return hiddenPage(rq, d.Slug)
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
// list and status ("Tasks · done 2026-05-01").
func resultMeta(d *SearchDoc, names map[string]TasksHome) string {
	if d.isTask() {
		status := taskStatus(d.Task)
		if status != "open" {
			status += " " + runePrefix(d.Task.CompletedAt, 10)
		}
		where := "Tasks"
		if l := taskList(d.Task, names); l != "" {
			where += " › " + l
		}
		return where + " · " + status
	}
	edited := "edited " + fmtDate(d.Mtime)
	lock := ""
	if _, private := pageOwner(d.Slug); private {
		lock = "🔒 " // a user's own private page: no result shows another's
	}
	if i := strings.LastIndex(d.Slug, "/"); i >= 0 {
		return lock + d.Slug[:i] + " · " + edited
	}
	return lock + edited
}

const (
	searchLimit      = 15   // results in the search modal (/api/search default)
	searchPageLimit  = 50   // results on the /search page ("All results")
	searchMaxLimit   = 100  //
	notesFilterLimit = 1000 // Notes page's filter lists every match
)

// doSearch: search results for the browser, at most limit. unlinked keeps
// only pages no other page links to (the search modal's "Unlinked only"),
// no tasks; quicknote only notes (Notes page's filter).
func doSearch(rq *Req, raw string, unlinked bool, limit int, quicknote bool) []M {
	results := []M{}
	if strings.TrimSpace(raw) == "" {
		return results
	}
	q := textsearch.ParseQuery(raw)
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
	if quicknote {
		tests = append(tests, func(d *SearchDoc) bool { return !d.isTask() && inQuicknote(rq.user, d.Slug) })
	}
	if rq.sharing || settings().Users != nil {
		tests = append(tests, func(d *SearchDoc) bool { return !hiddenDoc(rq, d) })
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
	docs := rankedDocs(q, searchIndex(rq), keep, limit)
	slugs := map[string]bool{}
	for _, d := range docs {
		if !d.isTask() {
			slugs[d.Slug] = true
		}
	}
	sections := bestSections(rq, q, slugs)
	names := rq.taskLists()
	for _, d := range docs {
		var section *SearchDoc
		body, ok := d.body()
		if !ok {
			continue // the page is gone
		}
		excerptOf := body
		if !d.isTask() {
			if section = sections[d.Slug]; section != nil {
				sectionBody, _ := section.body()
				excerptOf = strings.TrimSpace(section.heading() + "\n" + sectionBody)
			}
		}
		r := M{"slug": d.Slug, "title": d.Title, "excerpt": htmlOf(textsearch.Excerpt(excerptOf, text)),
			"meta": resultMeta(d, names)}
		if d.isTask() {
			r["type"], r["task_id"], r["status"] = "task", d.taskID(), d.tk.status
			if d.tk.year != "" {
				r["year"] = d.tk.year
			}
			tab := "open"
			if d.tk.status == "done" {
				tab = "completed"
			}
			list := taskList(d.Task, names)
			if list != "" {
				r["list"] = list
			}
			r["href"] = taskHref(d.taskID(), tab, list, d.tk.year)
		} else {
			href := pathURL("page", d.Slug)
			// The page highlights what matched (js/search.js); before the #anchor.
			if words := textsearch.MatchedWords(body, text, 10); len(words) > 0 {
				hl := make([]string, len(words))
				for i, w := range words {
					hl[i] = "hl=" + url.QueryEscape(w)
				}
				href += "?" + strings.Join(hl, "&")
			}
			if section != nil {
				href += section.anchor()
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
	return "#" + mdtext.HeadingID(mdtext.PlainHeading(heading))
}

// searchSections: a page's sections as the section index has them
// (chunkSections of semanticText), at least one.
func searchSections(slug, content string) []chunkSection {
	_, body := semanticText(content, slug)
	sections := chunkSections(body, maxChunkChars)
	if len(sections) == 0 {
		sections = []chunkSection{{}}
	}
	return sections
}

// pageChunks: a page's sections (searchSections) as BM25 documents. Their
// text and tokens, and their headings, paths, anchors and semantic keys, are
// read from the file again when asked for (section): a doc holds only
// what all of its page's share and which section it is.
func pageChunks(slug, path, content string, mtime time.Time) []*SearchDoc {
	// Clones, as in pageSearchDoc: the title and tags are pieces of content,
	// and would keep the whole file in memory for as long as the sections
	// live.
	title, _ := semanticText(content, slug)
	title = strings.Clone(title)
	tags := mdtext.PageTags(content)
	for i, tag := range tags {
		tags[i] = strings.Clone(tag)
	}
	file := &pageFile{slug, path, mtime}
	var chunks []*SearchDoc
	for i, s := range searchSections(slug, content) {
		chunks = append(chunks, &SearchDoc{Slug: slug, Title: title, Section: true, Tags: tags, Mtime: mtime,
			key: docKey("section", slug, title, s.Heading, s.Body), file: file, nth: int32(i)})
	}
	return chunks
}

// chunkIndex: BM25 over page sections and tasks (a task is one
// result, like a section), as the browser search ranks pages and tasks
// together.
func chunkIndex(rq *Req) *docIndex {
	seedChunkDocs()
	var docs []*SearchDoc
	for _, chunks := range perPage(&chunkDocCache, rq.pageStats(), pageChunks, "", nil) {
		docs = append(docs, chunks...)
	}
	return chunkIndexCache.get(rq, append(docs, taskSearchDocs()...))
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
// folder, with quicknote only of notes; either way no tasks. Only
// pages rq may see (nil: Zone itself, which sees all).
func retrieve(rq *Req, raw string, limit int, under string, quicknote bool) []*SearchDoc {
	q := textsearch.ParseQuery(raw)
	if strings.TrimSpace(q.RankingText()) == "" {
		return nil
	}
	x := chunkIndex(rq)
	pagesOnly := under != "" || quicknote
	pageOK := func(slug string) bool {
		return inFolder(slug, under) && (!quicknote || inQuicknote(rq.who(), slug)) && !hiddenPage(rq, slug)
	}
	var keep func(*SearchDoc) bool
	if pagesOnly || rq != nil && settings().Users != nil {
		keep = func(d *SearchDoc) bool {
			return d.isTask() && !pagesOnly && !hiddenDoc(rq, d) || !d.isTask() && pageOK(d.Slug)
		}
	}
	ranked := bm25(x, q, fuseDepth, keep)
	if semantic != nil {
		semantic.poke()
		text := q.RankingText()
		bySlug, slugs, tasks := map[string][]*SearchDoc{}, map[string]bool{}, map[string]*SearchDoc{}
		passes := x.filter(q, nil)
		for i, d := range x.docs {
			switch {
			case !passes(x.idAt(i), d):
			case !d.isTask() && pageOK(d.Slug):
				bySlug[d.Slug], slugs[d.Slug] = append(bySlug[d.Slug], d), true
			case d.isTask() && !pagesOnly && !hiddenDoc(rq, d):
				tasks[d.taskID()] = d
			}
		}
		// A hit names its section by its key, which a doc works out from
		// its page (semKey): only for the pages hit.
		var hits, tHits []docScore
		keyed := map[string]map[string]*SearchDoc{}
		for _, h := range semantic.searchChunks(text, slugs, fuseDepth) {
			byKey := keyed[h.Slug]
			if byKey == nil {
				byKey = map[string]*SearchDoc{}
				for _, d := range bySlug[h.Slug] {
					byKey[d.semKey()] = d
				}
				keyed[h.Slug] = byKey
			}
			if d := byKey[h.ID]; d != nil {
				hits = append(hits, docScore{d, h.Score})
			}
		}
		if !pagesOnly {
			tHits = taskHits(text, tasks, fuseDepth)
		}
		if hits != nil || tHits != nil {
			ranked = textsearch.Fuse([][]*SearchDoc{ranked, firstN(bySimilarity(hits, tHits), fuseDepth)})
		}
	}
	return firstN(phraseFirst(ranked, q, x, keep), limit)
}

// search_wiki's queries, one JSON line each (query-log in the settings file), for turning
// into questions for zonewiki eval (eval.go). In .zone/, so never in git.
// Past queryLogMax bytes the log moves to queries.1.jsonl (the one before
// is dropped), so it can't grow without end.
var queryLogMax int64 = 1_000_000

var queryLogMu sync.Mutex

func queryLogPath() string { return statePath("queries.jsonl") }

// resultTarget: a result as the eval's questions name it, slug#anchor or
// task:<id>.
func resultTarget(d *SearchDoc) string {
	if d.isTask() {
		return "task:" + d.taskID()
	}
	return d.Slug + d.anchor()
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
	if !settings().QueryLog {
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
		if !hiddenPage(rq, p.Slug) { // before the top five, so they are five the user may see
			files[p.Slug], known[p.Slug] = p.Path, true
		}
	}
	_, exclude := pageMeta(path, slug)
	exclude = maps.Clone(exclude)
	for _, b := range backlinks {
		exclude[b.Slug] = true
	}
	var related []PageRef
	for _, r := range semantic.related(slug, known, exclude, 5) {
		title, _ := pageMeta(files[r.ID], r.ID)
		related = append(related, PageRef{Slug: r.ID, Title: title})
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

// pageReads counts the pages read from their files for search's docs and
// their texts (a test's measure).
var pageReads atomic.Int64

// startFile: the base's file as a start finds it, opened once, so that its
// docs' metas make the docs (seedPageDocs, seedChunkDocs) and load takes it
// from there without opening it again. Nil when there is none to read.
func (c *indexCache) startFile() *textsearch.Index {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.started {
		return c.opened
	}
	c.started = true
	if path := c.indexPath(); path != "" && c.base == nil {
		c.opened, _ = textsearch.OpenIndex(path) // load says what's wrong with it
	}
	return c.opened
}

// metaVersion: changes when docMeta does, so an older file's metas are
// read as none (the docs are made from the pages then).
const metaVersion = 1

// docMeta: what makes a page's or section's doc again without reading its
// file (docFromMeta), kept with it in the index file; nil for a task, whose
// doc comes from the tasks' files anyway.
func docMeta(d *SearchDoc) []byte {
	if d.file == nil {
		return nil
	}
	b := []byte{metaVersion, 0}
	if d.Section {
		b[1] |= 1
	}
	if d.TaskRefs {
		b[1] |= 2
	}
	b = binary.AppendUvarint(b, uint64(d.nth))
	b = binary.AppendVarint(b, d.Mtime.UnixNano())
	b = binary.AppendUvarint(b, uint64(len(d.Tags)))
	for _, s := range append([]string{d.Slug, d.Title}, d.Tags...) {
		b = binary.AppendUvarint(b, uint64(len(s)))
		b = append(b, s...)
	}
	return b
}

// docFromMeta: the doc docMeta describes, with the key the file has for
// it; false for a meta that isn't one (a task's, another version's, cut).
func docFromMeta(b []byte, key textsearch.DocKey) (*SearchDoc, bool) {
	if len(b) < 2 || b[0] != metaVersion {
		return nil, false
	}
	flags, b := b[1], b[2:]
	num := func() (uint64, bool) {
		v, n := binary.Uvarint(b)
		if n <= 0 {
			return 0, false
		}
		b = b[n:]
		return v, true
	}
	nth, ok1 := num()
	mtime, n := binary.Varint(b)
	if !ok1 || n <= 0 || nth > math.MaxInt32 {
		return nil, false
	}
	b = b[n:]
	ntags, ok := num()
	if !ok || ntags > uint64(len(b)) {
		return nil, false
	}
	strs := make([]string, 2+ntags)
	for i := range strs {
		l, ok := num()
		if !ok || l > uint64(len(b)) {
			return nil, false
		}
		strs[i], b = string(b[:l]), b[l:]
	}
	d := &SearchDoc{Slug: strs[0], Title: strs[1], Mtime: time.Unix(0, mtime), Section: flags&1 != 0,
		TaskRefs: flags&2 != 0, nth: int32(nth), key: key}
	if ntags > 0 {
		d.Tags = strs[2:]
	}
	d.file = &pageFile{d.Slug, filepath.Join(cfg.Data, filepath.FromSlash(d.Slug)+".md"), d.Mtime}
	return d, true
}

// seedPageDocs: at a start, the page docs from the pages index's file
// instead of from the pages, for each page it names (perPage keeps them
// while the page's mtime is the one they were made at), so the first search
// reads no page that hasn't changed since. A page with [[task:…]] isn't
// taken from the file: its doc depends on the tasks too.
func seedPageDocs() {
	searchDocCache.mu.Lock()
	defer searchDocCache.mu.Unlock()
	if searchDocCache.m != nil {
		return
	}
	searchDocCache.m = map[string]*pageCached[*SearchDoc]{}
	ix := searchIndexCache.startFile()
	if ix == nil {
		return
	}
	for id, key := range ix.Keys() {
		meta, _ := ix.Meta(id)
		if d, ok := docFromMeta(meta, key); ok && !d.Section && !d.TaskRefs && key != (textsearch.DocKey{}) {
			searchDocCache.m[d.Slug] = &pageCached[*SearchDoc]{d.Mtime, d, ""}
		}
	}
}

// seedChunkDocs: seedPageDocs for the sections index: each page's sections,
// one after the other in the file, become its docs, sharing its file, title
// and tags as pageChunks' do.
func seedChunkDocs() {
	chunkDocCache.mu.Lock()
	defer chunkDocCache.mu.Unlock()
	if chunkDocCache.m != nil {
		return
	}
	chunkDocCache.m = map[string]*pageCached[[]*SearchDoc]{}
	ix := chunkIndexCache.startFile()
	if ix == nil {
		return
	}
	var page []*SearchDoc
	whole := true
	flush := func() {
		if len(page) > 0 && whole {
			chunkDocCache.m[page[0].Slug] = &pageCached[[]*SearchDoc]{page[0].Mtime, page, ""}
		}
		page, whole = nil, true
	}
	for id, key := range ix.Keys() {
		meta, _ := ix.Meta(id)
		d, ok := docFromMeta(meta, key)
		if !ok || !d.Section {
			flush()
			continue
		}
		if len(page) > 0 && d.Slug != page[0].Slug {
			flush()
		}
		if int(d.nth) != len(page) || key == (textsearch.DocKey{}) {
			whole = false
		}
		if len(page) > 0 { // what a page's sections share, once
			first := page[0]
			if !d.Mtime.Equal(first.Mtime) {
				whole = false
			}
			d.Slug, d.Title, d.Tags, d.file = first.Slug, first.Title, first.Tags, first.file
		}
		page = append(page, d)
	}
	flush()
}
