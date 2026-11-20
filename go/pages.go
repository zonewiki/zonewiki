// The wiki's pages as files: slugs and paths, listing,
// sharing mode, the link graph (link targets, backlinks, rewriting links on
// a rename), the task app's lists and home as pages declare them, url().
package main

import (
	"errors"
	"io/fs"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"zonewiki/internal/atomicfile"
	"zonewiki/internal/mdtext"
)

// Req is what a response depends on besides the wiki: the request (its
// sharing mode) and what is looked up once per request: the wiki's pages
// (one walk of its folders, however many parts of a page view list them),
// the task lists and the pinned lists they declare. A request that writes
// pages and then lists them would see the list from before: none does.
type Req struct {
	r          *http.Request
	user       asker // who is asking (userOf): its Name "" without users
	sharing    bool
	stamp      string // taskFiles.Stamp of the user (tasksStamp)
	pinnedDone bool
	pinned     []ListInfo // the pinned lists (pinnedLists), once
	counts     *TaskCounts
	files      []PageFile // iterPages, once
	stats      []pageStat // the same with their mtimes, once
	slugs      map[string]bool
	lists      map[string]ListInfo
	open       []*Task // openTasks, once
	openRead   bool
	recent     []*Task // recentClosed, once
	recentRead bool
	tasksPlace string // the view /tasks shows (tasksPage): where its Tasks links go
}

// pageFiles: iterPages, once per request (rq nil: listed now).
func (rq *Req) pageFiles() []PageFile {
	if rq == nil {
		return iterPages()
	}
	if rq.files == nil {
		rq.files = iterPages()
		if rq.files == nil {
			rq.files = []PageFile{}
		}
	}
	return rq.files
}

// openTasks: the open tasks the request's user sees, read once per request
// (rq nil: read now, shared ones only): the Tasks links' counts, the task
// places and a page's linked tasks all go by them, and reading them is
// reading and parsing the task files whole.
// recentClosed: the closed tasks of this month and the last that have news
// for the request's user (recentUpdates), read once a request.
func (rq *Req) recentClosed() []*Task {
	if rq == nil {
		return nil
	}
	if !rq.recentRead {
		rq.recent, rq.recentRead = recentUpdates(rq.user), true
	}
	return rq.recent
}

func (rq *Req) openTasks() []*Task {
	if rq == nil {
		return taskFiles.Open(asker{})
	}
	if !rq.openRead {
		rq.open, rq.openRead = taskFiles.Open(rq.user), true
	}
	return rq.open
}

// pageStat: a page's file and its mtime when the page was listed.
type pageStat struct {
	PageFile
	Mtime time.Time
}

// pageStats: every page with its file's mtime, once per request (rq nil:
// now), so that a search's indexes look at each file once between them. A
// page whose file is gone meanwhile is left out.
func (rq *Req) pageStats() []pageStat {
	if rq != nil && rq.stats != nil {
		return rq.stats
	}
	files := rq.pageFiles()
	out := make([]pageStat, 0, len(files))
	for _, p := range files {
		if st, err := os.Stat(p.Path); err == nil {
			out = append(out, pageStat{p, st.ModTime()})
		}
	}
	if rq != nil {
		rq.stats = out
	}
	return out
}

// slugSet: every page's slug (slugSet), once per request.
func (rq *Req) slugSet() map[string]bool {
	if rq == nil {
		return slugSet()
	}
	if rq.slugs == nil {
		rq.slugs = map[string]bool{}
		for _, p := range rq.pageFiles() {
			rq.slugs[p.Slug] = true
		}
	}
	return rq.slugs
}

// taskLists: taskLists for the user asking, once per request.
func (rq *Req) taskLists() map[string]ListInfo {
	if rq == nil {
		return taskLists(asker{})
	}
	if rq.lists == nil {
		rq.lists = taskLists(rq.user)
	}
	return rq.lists
}

func newReq(r *http.Request) *Req {
	return &Req{r: r, user: userOf(r), sharing: sharingOn(r)}
}

// who: the user asking, "" for none (rq nil: Zone itself, which has no
// private tasks of its own).
func (rq *Req) who() asker {
	if rq == nil {
		return asker{}
	}
	return rq.user
}

// pathURL is a path in Zone from its parts, each percent-encoded: all but
// letters, digits and _.-~/.
func pathURL(parts ...string) string {
	var b strings.Builder
	for _, p := range parts {
		b.WriteByte('/')
		for i := 0; i < len(p); i++ {
			c := p[i]
			if c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || strings.IndexByte("_.-~/", c) >= 0 {
				b.WriteByte(c)
			} else {
				const hexd = "0123456789ABCDEF"
				b.WriteByte('%')
				b.WriteByte(hexd[c>>4])
				b.WriteByte(hexd[c&15])
			}
		}
	}
	return b.String()
}

// isProtected: the home page, a user's private home and a group's home, never renamed or deleted.
func isProtected(slug string) bool {
	return slug == "index" || isPrivateHome(slug) || isGroupHome(slug)
}

// safePath is the page's file when it is inside the wiki and not in a
// folder iterPages skips (_assets, _tasks, .leaf, .git …: no pages there,
// so no route reads or writes a .md file there); "" otherwise.
//
// It is the file the slug names and no other, so that what a route opens
// is what placeOf judged (the slug, cleaned): nothing on the way to it is
// a symlink (a link would take a shared page's name to another user's
// page, or out of the wiki: a link is no page, as iterPages skips it), and
// the file system reads the name as it is written: no '\', which Windows
// takes for a folder's end, no name ending in '.' or ' ', which it drops
// (pagePart), and no short 8.3 name or other case that it would find
// another folder by (the path resolved is the path asked for).
//
// Nor for a name with a control character or a backslash
// (mdtext.BadNameRune) or a double quote at its start: a line break in a
// name started a commit message's next line, where git reads trailers
// ("Assistant: Claude"), and git quotes such a name where it lists files
// (cleanPath hides it).
func safePath(slug string) string {
	if strings.HasPrefix(slug, "/") || strings.HasPrefix(slug, `"`) || strings.ContainsFunc(slug, mdtext.BadNameRune) {
		return ""
	}
	clean := path.Clean(slug)
	parts := strings.Split(clean, "/")
	for i, part := range parts {
		if part == ".." || !pagePart(part) || i < len(parts)-1 && (strings.HasPrefix(part, "_") || strings.HasPrefix(part, ".")) {
			return ""
		}
	}
	target := filepath.Join(wikiRoot(), filepath.FromSlash(clean+".md"))
	if resolvePath(target) != target {
		return ""
	}
	// A link that leads nowhere resolves to itself: a write would go through it.
	if st, err := os.Lstat(target); err == nil && st.Mode()&fs.ModeSymlink != 0 {
		return ""
	}
	return target
}

// pagePart: name can be a folder's or a page's in a slug: the file system
// reads it as written (safePath), on every system Zone runs on.
func pagePart(name string) bool {
	return !strings.ContainsRune(name, '\\') && !strings.HasSuffix(name, ".") && !strings.HasSuffix(name, " ")
}

// wikiRoot: the wiki's folder, its symlinks followed (cfg.Data is so from
// the start, but a test may set another), which safePath finds pages in.
var wikiRoots struct {
	sync.Mutex
	data, root string
}

func wikiRoot() string {
	wikiRoots.Lock()
	defer wikiRoots.Unlock()
	if wikiRoots.data != cfg.Data || wikiRoots.root == "" {
		wikiRoots.data, wikiRoots.root = cfg.Data, resolvePath(cfg.Data)
	}
	return wikiRoots.root
}

// relInside: p relative to root, ok false when p is outside root.
func relInside(root, p string) (string, bool) {
	rel, err := filepath.Rel(root, p)
	if err != nil || rel == ".." || strings.HasPrefix(rel, "../") || filepath.IsAbs(rel) {
		return "", false
	}
	return rel, true
}

// resolvePath: p cleaned, its symlinks followed as far as the path exists.
func resolvePath(p string) string {
	p = filepath.Clean(p)
	if r, err := filepath.EvalSymlinks(p); err == nil {
		return r
	}
	dir, base := filepath.Split(p)
	if dir == "" || filepath.Clean(dir) == p {
		return p
	}
	return filepath.Join(resolvePath(filepath.Clean(dir)), base)
}

func exists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}

func isDir(path string) bool {
	st, err := os.Stat(path)
	return err == nil && st.IsDir()
}

// readText: a file's text, its line ends as "\n".
func readText(p string) (string, error) {
	b, err := os.ReadFile(p)
	return newlines(string(b)), err
}

// newlines: "\r\n" and "\r" as "\n" (in files and git's output), so a page
// saved on Windows reads the same.
func newlines(s string) string {
	if !strings.Contains(s, "\r") {
		return s
	}
	return strings.ReplaceAll(strings.ReplaceAll(s, "\r\n", "\n"), "\r", "\n")
}

// writeText writes a page (its folders made), atomically (atomicfile.Write):
// the search, the semantic index's worker and git never read half a page.
func writeText(p, text string) error { return atomicfile.Write(p, text) }

// PageFile is a page's file and slug.
type PageFile struct{ Path, Slug string }

// pageWalks counts iterPages' walks of the wiki (a test's measure).
var pageWalks atomic.Int64

// iterPages lists every page, skipping _* folders (assets, the tasks) and
// hidden ones (.zone, .git), which it doesn't walk into, and what safePath
// opens no page by: a symlink (to a file or a folder; whoever reads the
// list reads its files), a name the file system wouldn't read as written.
func iterPages() []PageFile {
	pageWalks.Add(1)
	var out []PageFile
	root := cfg.Data
	_ = filepath.WalkDir(root, func(p string, d fs.DirEntry, err error) error {
		if err != nil || p != root && d.Type()&fs.ModeSymlink != 0 {
			return nil
		}
		if d.IsDir() {
			if p != root && (strings.HasPrefix(d.Name(), "_") || strings.HasPrefix(d.Name(), ".") || !pagePart(d.Name())) {
				return filepath.SkipDir
			}
			return nil
		}
		if strings.HasSuffix(d.Name(), ".md") && pagePart(strings.TrimSuffix(d.Name(), ".md")) {
			rel, _ := filepath.Rel(root, p)
			out = append(out, PageFile{p, strings.TrimSuffix(filepath.ToSlash(rel), ".md")})
		}
		return nil
	})
	return out
}

func sortedPages() []PageFile { return sortPages(iterPages()) }

// sortPages sorts pages by slug, in place.
func sortPages(pages []PageFile) []PageFile {
	sort.Slice(pages, func(i, j int) bool { return pages[i].Slug < pages[j].Slug })
	return pages
}

func allSlugs() []string {
	var out []string
	for _, p := range sortedPages() {
		out = append(out, p.Slug)
	}
	return out
}

func slugSet() map[string]bool {
	out := map[string]bool{}
	for _, p := range iterPages() {
		out[p.Slug] = true
	}
	return out
}

// Quicknote is a note in the list.
type Quicknote struct {
	Slug, Title, Date string
	ts                time.Time
}

// listQuicknote: every note, newest first (in sharing mode without
// those not for sharing): the wiki's top level, or with users only the top
// of the user's private folder (inQuicknote).
func listQuicknote(rq *Req) []Quicknote {
	folders := []string{""}
	if own := privateFolder(rq.user); own != "" {
		folders = []string{own}
	}
	var notes []Quicknote
	for _, folder := range folders {
		prefix := ""
		if folder != "" {
			prefix = folder + "/"
		}
		entries, _ := os.ReadDir(filepath.Join(cfg.Data, filepath.FromSlash(folder)))
		for _, e := range entries {
			name := e.Name()
			if e.IsDir() || !strings.HasSuffix(name, ".md") || e.Type()&fs.ModeSymlink != 0 {
				continue // a link is no note, as it is no page (safePath)
			}
			slug := prefix + strings.TrimSuffix(name, ".md")
			m := quicknoteRE.FindStringSubmatch(slug)
			if m == nil || !inQuicknote(rq.user, slug) || hiddenPage(rq, slug) {
				continue
			}
			ts, err := time.ParseInLocation("2006-01-02_15-04-05", m[1], time.Local)
			if err != nil {
				continue
			}
			title, _ := pageMeta(filepath.Join(cfg.Data, filepath.FromSlash(slug)+".md"), slug)
			notes = append(notes, Quicknote{slug, title, ts.Format("2006-01-02 15:04"), ts})
		}
	}
	sort.SliceStable(notes, func(i, j int) bool { return notes[i].ts.After(notes[j].ts) })
	return notes
}

// The noshare tags of each page, by its file's mtime.
var (
	noshareMu    sync.Mutex
	noshareCache = map[string]noshareEntry{}
)

type noshareEntry struct {
	mtime  time.Time
	hidden bool
}

// isNoshare: a page the noshare setting names: in a noshare folder, or with a
// noshare tag in its frontmatter.
func isNoshare(slug string) bool {
	low := strings.ToLower(slug)
	s := settings()
	for _, f := range s.NoshareFolder {
		if strings.HasPrefix(low, f) || low == f[:len(f)-1] {
			return true
		}
	}
	if len(s.NoshareTags) == 0 {
		return false
	}
	p := safePath(slug)
	if p == "" {
		return false
	}
	st, err := os.Stat(p)
	if err != nil {
		return false
	}
	noshareMu.Lock()
	cached, ok := noshareCache[slug]
	noshareMu.Unlock()
	if ok && cached.mtime.Equal(st.ModTime()) {
		return cached.hidden
	}
	hidden := false
	if text, err := readText(p); err == nil {
		meta, _ := mdtext.ParseFrontmatter(text)
		for _, t := range meta.Tags {
			if s.NoshareTags[strings.ToLower(t)] {
				hidden = true
			}
		}
	}
	noshareMu.Lock()
	noshareCache[slug] = noshareEntry{st.ModTime(), hidden}
	noshareMu.Unlock()
	return hidden
}

// hiddenPage: a page to leave out of this response: another user's private
// page (canSee), or sharing mode is on and the page is not for sharing.
func hiddenPage(rq *Req, slug string) bool {
	return rq != nil && (!canSee(rq.user, slug) || rq.sharing && isNoshare(slug))
}

// refusePage answers a request to open a page it may not: 404 for one the
// user can't see, as for no page at all (it doesn't tell that the page
// exists; another user's private folder is 404 throughout), "Hidden page"
// for one sharing mode hides. False: go ahead.
func refusePage(w http.ResponseWriter, r *http.Request, rq *Req, slug string) bool {
	if !canSee(rq.user, slug) {
		http.NotFound(w, r)
		return true
	}
	if hiddenPage(rq, slug) {
		hiddenPageView(w, rq)
		return true
	}
	return false
}

// refuseWrite answers a change to a page the user may not change: 404, as
// refusePage, but 403 for a viewer's change to a shared page (readOnly).
// False: go ahead.
func refuseWrite(w http.ResponseWriter, r *http.Request, slug string) bool {
	switch {
	case readOnly(userOf(r), slug):
		http.Error(w, readOnlyWhy(userOf(r), slug), http.StatusForbidden)
		return true
	case !canWrite(userOf(r), slug):
		http.NotFound(w, r)
		return true
	}
	return false
}

// pageTaskRefs: the ids of the tasks a page refers to with [[task:…]],
// those user sees.
func pageTaskRefs(user asker, content string) map[string]bool {
	masked, _ := mdtext.MaskCode(content)
	var refs []string
	for _, m := range taskRefRE.FindAllStringSubmatch(masked, -1) {
		refs = append(refs, strings.ToLower(m[1]))
	}
	out := map[string]bool{}
	for _, t := range resolveTaskRefs(user, refs) {
		if t != nil {
			out[t.ID] = true
		}
	}
	return out
}

// resolveRelativeLink resolves a link target (without .md) against the
// source page's folder, ../ and ./ normalized; "" when nothing is left.
func resolveRelativeLink(href, source string) string {
	parts := strings.Split(source, "/")
	var norm []string
	for _, part := range append(parts[:len(parts)-1:len(parts)-1], strings.Split(href, "/")...) {
		switch {
		case part == "..":
			if len(norm) > 0 {
				norm = norm[:len(norm)-1]
			}
		case part != "" && part != ".":
			norm = append(norm, part)
		}
	}
	return strings.Join(norm, "/")
}

func hasAnyPrefix(s string, prefixes ...string) bool {
	for _, p := range prefixes {
		if strings.HasPrefix(s, p) {
			return true
		}
	}
	return false
}

// resolveMdLink: a relative .md link as a slug, "" when external.
func resolveMdLink(href, source string) string {
	if hasAnyPrefix(href, "http://", "https://", "//", "/") {
		return ""
	}
	return resolveRelativeLink(href[:len(href)-3], source)
}

// resolveBareLink: a bare relative link (no extension) as a slug, "" when external.
func resolveBareLink(href, source string) string {
	if hasAnyPrefix(href, "http://", "https://", "//", "/", "#") || strings.Contains(href, ":") {
		return ""
	}
	return resolveRelativeLink(href, source)
}

// linkTarget: the slug a relative markdown link from source leads to ("" when
// external), and whether it names the file (.md).
func linkTarget(href, source string) (string, bool) {
	if strings.HasSuffix(href, ".md") {
		return resolveMdLink(href, source), true
	}
	return resolveBareLink(href, source), false
}

// relHref: a relative link to target from the folder dir, as a .md link or bare.
func relHref(target, dir string, md bool) string {
	if md {
		return relPath(target, dir) + ".md"
	}
	return relPath(target, dir)
}

// wikilink is a [[…]] taken apart: its page, #section and |label, each
// kept as written.
type wikilink struct {
	page, section, label string
	hasSection, hasLabel bool
}

func parseWikilink(m string) wikilink {
	target, label, bar := strings.Cut(m[2:len(m)-2], "|")
	page, section, hash := strings.Cut(target, "#")
	return wikilink{page, section, label, hash, bar}
}

func (l wikilink) String() string {
	s := "[[" + l.page
	if l.hasSection {
		s += "#" + l.section
	}
	if l.hasLabel {
		s += "|" + l.label
	}
	return s + "]]"
}

const notSpace = `[^)#?\s\v\pZ\x{85}]`

// anyMdLinkRE: any markdown link's href, for link targets.
var anyMdLinkRE = regexp.MustCompile(`\[(?:[^\]]*)\]\((` + notSpace + `+)[^)]*\)`)

// pageLinkTargets: every slug a page links to (wikilinks, markdown links),
// not in code or math, which show them as text (mdtext.MaskCode).
func pageLinkTargets(content, slug string) map[string]bool {
	content, _ = mdtext.MaskCode(content)
	targets := map[string]bool{}
	for _, m := range mdtext.WikilinkRE.FindAllStringSubmatch(content, -1) {
		if page, _, _ := mdtext.SplitWikilink(m[1]); page != "" {
			targets[mdtext.ToSlug(page)] = true
		}
	}
	for _, m := range anyMdLinkRE.FindAllStringSubmatch(content, -1) {
		href := m[1]
		var resolved string
		if strings.HasPrefix(href, "/page/") {
			resolved = href[len("/page/"):] // absolute, as a rename re-points it
		} else {
			resolved, _ = linkTarget(href, slug)
		}
		if resolved != "" {
			targets[resolved] = true
		}
	}
	return targets
}

// Per page, by its file's mtime: title, link targets, task list embeds' params.
// Edits made outside the app show, as the mtime changes. A page that is
// gone (deleted, renamed, moved) leaves the cache when it is next asked
// for, or with the next look at every page (prunePageMeta).
var (
	metaMu     sync.Mutex
	metaCache  = map[string]pageMetaEntry{}
	metaPruned time.Time // prunePageMeta's last look
)

type pageMetaEntry struct {
	mtime   time.Time
	title   string
	targets map[string]bool
	embeds  []TasksParams
	// taskRefs: the page refers to tasks ([[task:…]]): search reads it with
	// their titles in place (visitorSkips).
	taskRefs bool
}

// pageTasksEmbeds: the params of each [[tasks: …]] on a page, outside code.
func pageTasksEmbeds(content string) []TasksParams {
	masked, _ := mdtext.MaskCode(content)
	var out []TasksParams
	for i, m := range tasksRE.FindAllStringSubmatch(masked, -1) {
		p := parseTasksParams(embedArgs(m))
		p.src = &embedSrc{i, embedArgs(m)}
		out = append(out, p)
	}
	return out
}

// TaskList is an embed that names a list ([[tasks: list=<name>]]): the
// name as written (cleaned) and the embed's params.
type TaskList struct {
	Name   string
	Params TasksParams
}

// embedsLists: those of a page's embeds that name a list.
func embedsLists(embeds []TasksParams) []TaskList {
	var out []TaskList
	for _, p := range embeds {
		if name := cleanList(p.Get("list")); name != "" {
			out = append(out, TaskList{name, p})
		}
	}
	return out
}

func pageMetaEntryOf(p, slug string) pageMetaEntry {
	st, err := os.Stat(p)
	if err != nil {
		metaMu.Lock()
		delete(metaCache, slug)
		metaMu.Unlock()
		return pageMetaEntry{title: mdtext.SlugDisplayName(slug), targets: map[string]bool{}}
	}
	metaMu.Lock()
	cached, ok := metaCache[slug]
	metaMu.Unlock()
	if ok && cached.mtime.Equal(st.ModTime()) {
		return cached
	}
	b, err := os.ReadFile(p)
	if err != nil {
		return pageMetaEntry{title: mdtext.SlugDisplayName(slug), targets: map[string]bool{}}
	}
	content := newlines(strings.ToValidUTF8(string(b), "�"))
	// A clone: the title is a piece of content, and would keep the whole file in memory.
	entry := pageMetaEntry{st.ModTime(), strings.Clone(mdtext.PageTitle(content, slug)), pageLinkTargets(content, slug), pageTasksEmbeds(content),
		taskRefRE.MatchString(content)}
	metaMu.Lock()
	metaCache[slug] = entry
	metaMu.Unlock()
	return entry
}

// prunePageMeta drops the cache's entries of pages that aren't among pages
// (every page, as just listed): without it each deleted or renamed page
// would stay for as long as Zone runs. At most once a minute: it goes
// through every page.
func prunePageMeta(pages []PageFile) {
	metaMu.Lock()
	defer metaMu.Unlock()
	if since := now().Sub(metaPruned); since >= 0 && since < time.Minute { // not if the clock went back
		return
	}
	metaPruned = now()
	there := make(map[string]bool, len(pages))
	for _, p := range pages {
		there[p.Slug] = true
	}
	for slug := range metaCache {
		if !there[slug] {
			delete(metaCache, slug)
		}
	}
}

// pageMeta: a page's title and outgoing link targets, cached by mtime.
func pageMeta(p, slug string) (string, map[string]bool) {
	e := pageMetaEntryOf(p, slug)
	return e.title, e.targets
}

// listPages: the pages with an embed of the list the request's user names
// ref that the request may show, A–Z by title (the Backlinks of
// /tasks?list=).
func (rq *Req) listPages(ref string) []PageRef {
	lists := rq.taskLists()
	list, ok := resolveList(ref, lists)
	var out []PageRef
	for _, p := range rq.pageFiles() {
		if !ok || hiddenPage(rq, p.Slug) {
			continue
		}
		e := pageMetaEntryOf(p.Path, p.Slug)
		for _, l := range embedsLists(e.embeds) {
			if shown, ok := listOnPage(l.Name, p.Slug, rq.who(), lists); ok && shown.Ref == list.Ref {
				out = append(out, PageRef{p.Slug, e.title})
				break
			}
		}
	}
	sort.SliceStable(out, func(i, j int) bool { return strings.ToLower(out[i].Title) < strings.ToLower(out[j].Title) })
	return out
}

// pinnedLists: the user's pinned lists (pinnedLists), once per request.
func (rq *Req) pinnedLists() []ListInfo {
	if rq == nil {
		return pinnedLists(asker{}, taskLists(asker{}))
	}
	if !rq.pinnedDone {
		rq.pinned, rq.pinnedDone = pinnedLists(rq.user, rq.taskLists()), true
	}
	return rq.pinned
}

// allBacklinkCounts: how many pages link to each slug, of those the
// request may see (rq nil: all).
func allBacklinkCounts(rq *Req) map[string]int {
	counts := map[string]int{}
	for _, p := range rq.pageFiles() {
		if hiddenPage(rq, p.Slug) {
			continue
		}
		_, targets := pageMeta(p.Path, p.Slug)
		for t := range targets {
			if t != p.Slug {
				counts[t]++
			}
		}
	}
	return counts
}

// PageRef is a page as {slug, title}.
type PageRef struct{ Slug, Title string }

// findBacklinks: the pages with a link to slug, A–Z by title.
func findBacklinks(slug string) []PageRef { return findBacklinksIn(iterPages(), slug) }

// findBacklinksIn: findBacklinks among pages.
func findBacklinksIn(pages []PageFile, slug string) []PageRef {
	var out []PageRef
	for _, p := range pages {
		if p.Slug == slug {
			continue
		}
		title, targets := pageMeta(p.Path, p.Slug)
		if targets[slug] {
			out = append(out, PageRef{p.Slug, title})
		}
	}
	prunePageMeta(pages)
	sort.SliceStable(out, func(i, j int) bool { return strings.ToLower(out[i].Title) < strings.ToLower(out[j].Title) })
	return out
}

// mdLinkRewriteRE: a markdown link, the prefix, the href up to any #anchor,
// ?query or title, and the rest inside the parentheses. Not an image's:
// RE2 has no lookbehind (?<!!): mdLinks checks for the "!".
var mdLinkRewriteRE = regexp.MustCompile(`^(\[[^\]]*\]\()(` + notSpace + `+)([^)]*\))`)

// mdLinkMatch is a markdown link found by mdLinks: its byte range and groups.
type mdLinkMatch struct {
	start, end         int
	prefix, href, rest string
}

// mdLinks finds the markdown links (not images) in text: from each "["
// not after "!", leftmost
// first, going on after a match's end.
func mdLinks(text string) []mdLinkMatch {
	var out []mdLinkMatch
	for i := 0; i < len(text); {
		j := strings.IndexByte(text[i:], '[')
		if j < 0 {
			break
		}
		i += j
		if i > 0 && text[i-1] == '!' {
			i++
			continue
		}
		m := mdLinkRewriteRE.FindStringSubmatchIndex(text[i:])
		if m == nil {
			i++
			continue
		}
		out = append(out, mdLinkMatch{i, i + m[1], text[i+m[2] : i+m[3]], text[i+m[4] : i+m[5]], text[i+m[6] : i+m[7]]})
		i += m[1]
	}
	return out
}

// replaceMdLinks rewrites each markdown link with fn (its text as written
// when fn returns it unchanged).
func replaceMdLinks(text string, fn func(m mdLinkMatch) string) string {
	var b strings.Builder
	last := 0
	for _, m := range mdLinks(text) {
		b.WriteString(text[last:m.start])
		b.WriteString(fn(m))
		last = m.end
	}
	b.WriteString(text[last:])
	return b.String()
}

// mdLinkMisses: the markdown links in content (code aside) that lead to no
// page: each link as written → the slug it resolves to. External links,
// anchors, folders (a trailing /), files (another extension) and Zone's own
// _folders and views are not checked.
func mdLinkMisses(content, slug string, slugs map[string]bool) map[string]string {
	text, _ := mdtext.MaskCode(content)
	misses := map[string]string{}
	for _, m := range mdLinks(text) {
		href := m.href
		var target string
		switch {
		case strings.HasPrefix(href, "/page/"):
			target = href[len("/page/"):]
		case hasAnyPrefix(href, "http://", "https://", "//", "/", "#") || strings.Contains(href, ":") || strings.HasSuffix(href, "/"):
			continue
		case strings.HasSuffix(href, ".md"):
			target = resolveMdLink(href, slug)
		case strings.Contains(href[strings.LastIndex(href, "/")+1:], "."):
			continue
		default:
			target = resolveBareLink(href, slug)
		}
		if target != "" && strings.HasPrefix(strings.Split(target, "/")[0], "_") {
			continue // Zone's own folders and views
		}
		if target != "" && !slugs[target] {
			misses[text[m.start:m.end]] = target
		}
	}
	return misses
}

// posixDir is posixpath.dirname ("a" → "").
func posixDir(p string) string {
	if i := strings.LastIndex(p, "/"); i >= 0 {
		return strings.TrimRight(p[:i], "/")
	}
	return ""
}

// relPath is posixpath.relpath(target, start) for slugs.
func relPath(target, start string) string {
	if start == "" {
		start = "."
	}
	rel, err := filepath.Rel(start, target)
	if err != nil {
		return target
	}
	return filepath.ToSlash(rel)
}

// rewritePageLinks points every link to oldSlug in text at newSlug. With a
// section (a heading's text), links without an #anchor of their own point
// at that heading of newSlug (a note appended to a page is that
// section now). Wikilinks and markdown links (relative, .md, /page/) keep
// their style, anchor and title; relative hrefs are recomputed from
// source's folder. Code is left alone. A wikilink that names its page
// keeps its words where not every reader of source sees newSlug
// (keepWords).
func rewritePageLinks(text, oldSlug, newSlug, source, section string) string {
	return rewriteLinksSeenBy(text, oldSlug, newSlug, source, section, placeOf(source))
}

// rewriteLinksSeenBy: rewritePageLinks for a text that those who see
// readers' place read, which needn't be source's: a task's (taskReaders).
func rewriteLinksSeenBy(text, oldSlug, newSlug, source, section string, readers place) string {
	text, stash := mdtext.MaskCode(text)
	text = mdtext.WikilinkRE.ReplaceAllStringFunc(text, func(m string) string {
		l := parseWikilink(m)
		if page := strings.TrimSpace(l.page); page == "" || page != oldSlug && mdtext.ToSlug(l.page) != oldSlug {
			return m
		}
		l.page = newSlug
		if !l.hasSection && section != "" {
			l.section, l.hasSection = section, true
		}
		return keepWords(l, m, readers).String()
	})
	sourceDir := posixDir(source)
	anchor := ""
	if section != "" {
		anchor = "#" + mdtext.SectionAnchor(section)
	}
	text = replaceMdLinks(text, func(m mdLinkMatch) string {
		rest := m.rest
		if !strings.HasPrefix(rest, "#") {
			rest = anchor + rest
		}
		if strings.HasPrefix(m.href, "/page/") {
			if m.href[len("/page/"):] == oldSlug {
				return m.prefix + "/page/" + newSlug + rest
			}
			return text[m.start:m.end]
		}
		target, isMd := linkTarget(m.href, source)
		if target != oldSlug || target == "" {
			return text[m.start:m.end]
		}
		return m.prefix + relHref(newSlug, sourceDir, isMd) + rest
	})
	return mdtext.UnmaskCode(text, stash)
}

// updateReferences rewrites links to oldSlug in every page and task
// after a rename (to a section of newSlug: rewritePageLinks); returns the
// files it changed, for the rename's commit, and what failed (linkErrors).
func updateReferences(oldSlug, newSlug, section string) ([]string, error) {
	var modified []string
	var errs []error
	for _, p := range iterPages() {
		content, err := readText(p.Path)
		if err != nil {
			errs = append(errs, err)
			continue
		}
		if n := rewritePageLinks(content, oldSlug, newSlug, p.Slug, section); n != content {
			if err := writeText(p.Path, n); err != nil {
				errs = append(errs, err)
			} else {
				modified = append(modified, p.Path)
			}
		}
	}
	changed, err := renamePageRefs(func(owner, text string) string {
		return rewriteLinksSeenBy(text, oldSlug, newSlug, "", section, taskReaders(owner))
	})
	return append(modified, changed...), linkErrors(append(errs, err))
}

// keepWords: l, a wikilink m re-pointed, with the words m showed as its
// label when it has none and not everyone who sees readers' place (where
// the link is written) sees its new page: to them a link to a page they
// don't see is plain text (wikilinker), which would otherwise be its new
// path, a page's name in a place they don't see (a page moved into a
// group's folder, or renamed there).
func keepWords(l wikilink, m string, readers place) wikilink {
	if l.hasLabel || readers.within(placeOf(l.page)) {
		return l
	}
	_, _, words := mdtext.SplitWikilink(m[2 : len(m)-2])
	l.label, l.hasLabel = words, true
	return l
}

// taskReaders: the place of those who see the tasks of owner's files: a
// user's private ones, a group's, or the shared ones (everyone's)
// (ownerPlace).
func taskReaders(owner string) place { return ownerPlace(owner) }

// linkErrors: what failed while links were re-pointed (nil: nothing), as
// linksLeft: the move itself is done and committed with the links that
// were, the others still point at the old name.
func linkErrors(errs []error) error {
	if err := errors.Join(errs...); err != nil {
		return &linksLeft{err}
	}
	return nil
}

// linksLeft: a move is done, but not every link to it could be re-pointed
// (a page that can't be read or written). Not a failure: the routes go on
// as after a move (it is logged), the MCP tools say so (partly). What it
// says names no file: the page that kept its link may be one its reader
// doesn't see (another user's private page), so which it is, and why, is
// for Zone's log alone (errDetail).
type linksLeft struct{ err error }

func (e *linksLeft) Error() string {
	return "not every link to it could be re-pointed (check_links finds them)"
}

func (e *linksLeft) Unwrap() error { return e.err }

// errDetail: err as the log says it: links left with what failed and
// where, anything else as it says itself.
func errDetail(err error) string {
	if l := (*linksLeft)(nil); errors.As(err, &l) {
		return l.Error() + ": " + l.err.Error()
	}
	return err.Error()
}

// partly: err without the links a move left (logged, and said in note);
// the error it is otherwise.
func partly(err error) (note string, _ error) {
	var l *linksLeft
	if errors.As(err, &l) {
		logf("%s", errDetail(l))
		return "\nNote: " + l.Error() + ".", nil
	}
	return "", err
}

// notYoursFolder: what is said of a folder a page can't go to because its
// place isn't the user's: another user's folder, or a group's they aren't in
// (which says nothing of whether there is such a group).
func notYoursFolder(slug string) string {
	if dir, _, _ := strings.Cut(strings.ToLower(path.Clean(slug)), "/"); dir == groupsDir && settings().Users != nil {
		return "that folder is a group's you aren't in"
	}
	return "that folder is another user's"
}

// moveSure: what the door has asked already, of a move that changes who
// sees a page (docs/spec/users.md, Who may move a page to other readers):
// wider, that more people will see it; narrower, that some no longer will.
// A door that hasn't asked gets the question back (askError) and asks.
type moveSure struct{ wider, narrower bool }

// sure: nothing is left to ask (what Zone itself moves, and an assistant's
// move to more people, which its user asked for).
var sure = moveSure{wider: true, narrower: true}

// narrowedBy, widenedBy: of the moves (old → new slug), the old slugs of the
// pages that would be seen by fewer people (place.within: someone who sees
// the page now wouldn't: a shared page into a private or a group's folder,
// a group's page into a private folder or another group's that doesn't
// include its own), and of those that would be seen by more, A–Z.
func narrowedBy(moves map[string]string) []string {
	var out []string
	for _, o := range sortedKeys(moves) {
		if !placeOf(o).within(placeOf(moves[o])) {
			out = append(out, o)
		}
	}
	return out
}

func widenedBy(moves map[string]string) []string {
	var out []string
	for _, o := range sortedKeys(moves) {
		if from, to := placeOf(o), placeOf(moves[o]); from != to && from.within(to) {
			out = append(out, o)
		}
	}
	return out
}

// checkReaders: the moves (old → new slug) as far as they change who sees
// the pages, for user. **To more people: whoever may write, asked first. To
// fewer: an admin, or whoever shared the page while nobody else changed it
// (mayNarrow), asked first; anyone else is refused.** One rule for a
// rename, a folder's move, Share…, a note's move and an assistant's
// rename_page; what the door asked already is in sure.
func checkReaders(user asker, moves map[string]string, sure moveSure) error {
	if narrow := narrowedBy(moves); len(narrow) > 0 {
		for _, o := range narrow {
			if !mayNarrow(user, o, moves[o]) {
				return refuse(narrowRefused(narrow, moves))
			}
		}
		if !sure.narrower {
			return &askError{narrowAsk(narrow, moves)}
		}
	}
	if wide := widenedBy(moves); len(wide) > 0 && !sure.wider {
		return &askError{widenAsk(wide, moves)}
	}
	return nil
}

// mayNarrow: user may move the page old to newSlug, where fewer people see
// it: an admin, who moves it only where they may write themselves
// (checkTarget: a group they are in, their own folder); or whoever put it
// where it is, while nobody else has changed it since (tookBack). In a
// wiki without an admin (from before roles) only the latter: nobody stood
// for the others there before either.
func mayNarrow(user asker, old, newSlug string) bool {
	return settings().isAdmin(user.Name) || tookBack(user, old, newSlug)
}

// tookBackFirst: how many of a page's commits tookBack reads first; only
// when those are all the user's, and none of them moved the page, does it
// read the rest.
const tookBackFirst = 50

// tookBack: the page old is user's to take back to newSlug: every commit of
// it since it came to where it is (the move that shared it, or its first
// commit) is theirs, nothing of anyone else's is in it uncommitted, and
// newSlug's place is the one it came from or wider: the mistake of a
// minute ago is undone, and nobody's work is taken from them. The commits
// are read back to that move or that first commit, however many there are:
// a stretch of the user's own commits, however long, proves nothing about
// those before it (fifty edits of their own would make anyone's page theirs).
func tookBack(user asker, old, newSlug string) bool {
	path := safePath(old)
	if path == "" || !cfg.GitEnabled {
		return false
	}
	draft := draftAuthor(path)
	if draft != "" && draft != user.Name {
		return false
	}
	entries := gitLog(path, tookBackFirst)
	if len(entries) == 0 {
		return draft == user.Name // not in git yet: theirs if they are who wrote it
	}
	if len(entries) == tookBackFirst {
		if ours, decided := tookBackFrom(user, entries, newSlug); decided {
			return ours
		}
		entries = gitLog(path, 0) // all theirs so far: the rest decides
	}
	ours, _ := tookBackFrom(user, entries, newSlug)
	return ours
}

// tookBackFrom: tookBack's answer from a page's commits, newest first, as
// far as they go: decided false when one isn't user's or the move here
// isn't one to take back to newSlug, true for that move; undecided when
// every commit is user's and none moved the page, which is theirs only if
// these are all its commits (made here by them, and theirs alone since).
func tookBackFrom(user asker, entries []GitEntry, newSlug string) (ours, decided bool) {
	for i, e := range entries {
		if e.Author != user.Name {
			return false, true
		}
		if i+1 < len(entries) && entries[i+1].Path != e.Path {
			// This commit moved it here, from there.
			return placeOf(strings.TrimSuffix(entries[i+1].Path, ".md")).within(placeOf(newSlug)), true
		}
	}
	return true, false
}

// placeSeers: the users who see what is kept at p, A–Z (who can sign in).
func placeSeers(p place) []string {
	var out []string
	s := settings()
	for _, name := range sortedKeys(s.Users) {
		if !s.Users[name].Disabled && p.sees(asUser(name)) {
			out = append(out, name)
		}
	}
	return out
}

// placeWho: who sees what is kept at p, in words: "everyone who uses this
// wiki", "@dev (anna, bo)", "only alice", "nobody".
func placeWho(p place) string {
	switch p.kind {
	case ofEveryone:
		return "everyone who uses this wiki"
	case ofUser:
		return "only " + p.name
	case ofGroup:
		return "@" + p.name + " (" + namesOr(placeSeers(p), 8, "nobody") + ")"
	}
	return "nobody"
}

// namesOr: names in a row, the first most of them and how many more
// ("anna, bo and 12 others"); none: the word given.
func namesOr(names []string, most int, none string) string {
	switch {
	case len(names) == 0:
		return none
	case len(names) > most:
		return strings.Join(names[:most], ", ") + " and " + itoa(len(names)-most) + " others"
	}
	return strings.Join(names, ", ")
}

// losers: who sees the page at from and wouldn't at to.
func losers(from, to place) []string {
	var out []string
	for _, name := range placeSeers(from) {
		if !to.sees(asUser(name)) {
			out = append(out, name)
		}
	}
	return out
}

// narrowAsk, widenAsk, narrowRefused: what a move to other readers says
// before it is made, and what it says to who may not make it.
func narrowAsk(narrow []string, moves map[string]string) string {
	o := narrow[0]
	from, to := placeOf(o), placeOf(moves[o])
	what := "“" + o + "”"
	if len(narrow) > 1 {
		what = itoa(len(narrow)) + " pages (" + o + " …)"
	}
	lost := losers(from, to)
	if len(lost) == 0 {
		// Nobody loses it today (the same people see both places), but who comes later will.
		return what + " will be for fewer people: there " + placeWho(to) + " sees it, now " + placeWho(from) +
			" does. Nobody who sees it now loses it; who joins later may not get it."
	}
	return namesOr(lost, 3, "") + " will no longer see " + what + ": there " + placeWho(to) +
		" sees it. They keep its versions up to now in the history, and links to it on pages they see become plain text."
}

func widenAsk(wide []string, moves map[string]string) string {
	o := wide[0]
	what := "“" + o + "”"
	if len(wide) > 1 {
		what = itoa(len(wide)) + " pages (" + o + " …)"
	}
	return what + " will be seen and changed by " + placeWho(placeOf(moves[o])) + ". Now " + placeWho(placeOf(o)) + " sees it."
}

func narrowRefused(narrow []string, moves map[string]string) string {
	o := narrow[0]
	from, to := placeOf(o), placeOf(moves[o])
	what := "“" + moves[o] + "” is seen by fewer people than “" + o + "” is now"
	if len(narrow) > 1 {
		what = itoa(len(narrow)) + " of the pages would be seen by fewer people (" + o + " …)"
	}
	if lost := losers(from, to); len(lost) > 0 {
		what += " (" + namesOr(lost, 3, "") + " would no longer see it)"
	}
	return what + ". A page goes to fewer people by an admin, or by whoever shared it while nobody else has changed it. Ask an admin."
}

// rebaseLinks re-points the links in text, written by page source, for a
// page now at newSource and for pages that moved (moves: old → new slug).
// Every relative link still reaches the same page or file from where the
// page is now; wikilinks name pages, so only their targets change.
func rebaseLinks(text, source, newSource string, moves map[string]string) string {
	text, stash := mdtext.MaskCode(text)
	text = mdtext.WikilinkRE.ReplaceAllStringFunc(text, func(m string) string {
		l := parseWikilink(m)
		key := strings.TrimSpace(l.page)
		old := key
		if _, ok := moves[key]; !ok {
			old = mdtext.ToSlug(key)
		}
		if _, ok := moves[old]; key == "" || !ok {
			return m
		}
		l.page = moves[old]
		return keepWords(l, m, placeOf(newSource)).String()
	})
	sourceDir := posixDir(newSource)
	text = replaceMdLinks(text, func(m mdLinkMatch) string {
		if strings.HasPrefix(m.href, "/page/") {
			if n, ok := moves[m.href[len("/page/"):]]; ok {
				return m.prefix + "/page/" + n + m.rest
			}
			return text[m.start:m.end]
		}
		target, isMd := linkTarget(m.href, source)
		if target == "" {
			return text[m.start:m.end]
		}
		newTarget := target
		if n, ok := moves[target]; ok {
			newTarget = n
		}
		if newSource == source && newTarget == target {
			return text[m.start:m.end]
		}
		return m.prefix + relHref(newTarget, sourceDir, isMd) + m.rest
	})
	return mdtext.UnmaskCode(text, stash)
}

// rewriteMovedLinks: after the pages in moves are at their new paths, point
// the links to them at the new slugs (pages and tasks) and re-base the
// moved pages' own links. Returns the files that changed, and what failed
// (linkErrors): a file that can't be written doesn't stop the others.
func rewriteMovedLinks(moves map[string]string) ([]string, error) {
	inverse := map[string]string{}
	for o, n := range moves {
		inverse[n] = o
	}
	var modified []string
	var errs []error
	seen := map[string]bool{}
	add := func(p string) {
		if !seen[p] {
			seen[p] = true
			modified = append(modified, p)
		}
	}
	for _, p := range iterPages() {
		source := p.Slug
		if o, ok := inverse[p.Slug]; ok {
			source = o
		}
		content, err := readText(p.Path)
		if err != nil {
			errs = append(errs, err)
			continue
		}
		if n := rebaseLinks(content, source, p.Slug, moves); n != content {
			if err := writeText(p.Path, n); err != nil {
				errs = append(errs, err)
			} else {
				add(p.Path)
			}
		}
	}
	changed, err := renamePageRefs(func(owner, text string) string {
		for _, o := range sortedKeys(moves) {
			text = rewriteLinksSeenBy(text, o, moves[o], "", "", taskReaders(owner))
		}
		return text
	})
	for _, c := range changed {
		add(c)
	}
	return modified, linkErrors(append(errs, err))
}

// folderChoice: a folder as a field suggests it: its path, and for the
// user's private folder and their groups' the place's name ("👥 dev").
type folderChoice struct{ Path, Label string }

// folderChoices: pageFolders, with the user's own places first and named:
// their private folder and the folders of their groups, then what is in
// them, then the wiki's folders, each A–Z. What is typed and saved is the
// path all the same.
func folderChoices(rq *Req) []folderChoice {
	var own, wiki []folderChoice
	for _, f := range pageFolders(rq) {
		p := placeOf(f + "/x")
		switch {
		case p.kind == ofUser && p.own(rq.who()) && f == p.folder():
			own = append(own, folderChoice{f, "🔒 Private"})
		case p.kind == ofGroup && f == p.folder():
			own = append(own, folderChoice{f, "👥 " + p.name})
		case p.shared():
			wiki = append(wiki, folderChoice{Path: f})
		case p.kind == ofNoOne:
			// private/ itself: what is put there no one sees.
		default:
			own = append(own, folderChoice{Path: f})
		}
	}
	return append(own, wiki...)
}

// pageFolders: the folders that hold pages, A–Z, for filing a note
// (hidden pages don't name theirs in sharing mode).
func pageFolders(rq *Req) []string {
	set := map[string]bool{}
	for _, p := range iterPages() {
		if hiddenPage(rq, p.Slug) {
			continue
		}
		parts := strings.Split(p.Slug, "/")
		parts = parts[:len(parts)-1]
		for i := 1; i <= len(parts); i++ {
			set[strings.Join(parts[:i], "/")] = true
		}
	}
	// The folders of the user's groups are there before a page is.
	if rq != nil && !rq.sharing {
		for _, g := range groupsFor(rq.user) {
			set[groupsDir+"/"+g] = true
		}
	}
	// groups/ itself is no place for a page: what is put there no one sees.
	if settings().Users != nil {
		delete(set, groupsDir)
	}
	return sortedKeys(set)
}

// pageFromInput: the slug a page field names: a slug, /page/<slug> (an
// #anchor dropped) or the page's name.
func pageFromInput(raw string) string {
	raw = strings.TrimPrefix(strings.TrimSpace(raw), "/page/")
	raw, _, _ = strings.Cut(raw, "#")
	raw = strings.Trim(raw, "/")
	if raw != "" {
		if p := safePath(raw); p != "" && exists(p) {
			return raw
		}
	}
	return mdtext.ToSlug(raw)
}

func fmtDate(t time.Time) string { return t.Local().Format("2006-01-02") }
