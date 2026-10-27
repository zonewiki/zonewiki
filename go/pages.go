// The wiki's pages as files: slugs and paths, listing,
// sharing mode, the link graph (link targets, backlinks, rewriting links on
// a rename), the deck's lists and home as pages declare them, url().
package main

import (
	"io/fs"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"
)

// Req is what a response depends on besides the wiki: the request (its
// sharing mode) and what is looked up once per request.
type Req struct {
	r        *http.Request
	user     string // who is asking (userOf): "" without users
	sharing  bool
	homeDone bool
	home     *DeckHome
	counts   *DeckCounts
}

func newReq(r *http.Request) *Req {
	return &Req{r: r, user: userOf(r), sharing: sharingOn(r)}
}

// who: the user asking, "" for none (rq nil: Leaf itself, which has no
// private tasks of its own).
func (rq *Req) who() string {
	if rq == nil {
		return ""
	}
	return rq.user
}

// pathURL is a path in Leaf from its parts, each percent-encoded ("/" kept), as
// urllib's quote(p, safe="/"): letters, digits, _.-~ and / stay.
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

// isProtected: the home page, never renamed or deleted.
func isProtected(slug string) bool { return slug == "index" }

// safePath is the page's file when it is inside the wiki and not in a
// folder iterPages skips (_assets, _deck, .leaf, .git …: no pages there,
// so no route reads or writes a .md file there); "" otherwise.
func safePath(slug string) string {
	if strings.HasPrefix(slug, "/") || strings.ContainsRune(slug, 0) {
		return ""
	}
	target := resolvePath(filepath.Join(cfg.Data, filepath.FromSlash(slug+".md")))
	rel, ok := relInside(cfg.Data, target)
	if !ok {
		return ""
	}
	parts := strings.Split(filepath.ToSlash(rel), "/")
	for _, part := range parts[:len(parts)-1] {
		if strings.HasPrefix(part, "_") || strings.HasPrefix(part, ".") {
			return ""
		}
	}
	return target
}

// relInside: p relative to root, ok false when p is outside root.
func relInside(root, p string) (string, bool) {
	rel, err := filepath.Rel(root, p)
	if err != nil || rel == ".." || strings.HasPrefix(rel, "../") || filepath.IsAbs(rel) {
		return "", false
	}
	return rel, true
}

// resolvePath is Path.resolve(): symlinks followed as far as the path exists.
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

// readText is Path.read_text: a file's text, its line ends as "\n".
func readText(p string) (string, error) {
	b, err := os.ReadFile(p)
	return newlines(string(b)), err
}

// newlines: "\r\n" and "\r" as "\n", Python's universal newlines (how it
// reads files and git's output), so a page saved on Windows reads the same.
func newlines(s string) string {
	if !strings.Contains(s, "\r") {
		return s
	}
	return strings.ReplaceAll(strings.ReplaceAll(s, "\r\n", "\n"), "\r", "\n")
}

func writeText(p, text string) error {
	if err := os.MkdirAll(filepath.Dir(p), 0o777); err != nil {
		return err
	}
	return os.WriteFile(p, []byte(text), 0o666)
}

// PageFile is a page's file and slug.
type PageFile struct{ Path, Slug string }

// iterPages lists every page, skipping _* folders (assets, the deck) and
// hidden ones (.leaf, .git), which it doesn't walk into.
func iterPages() []PageFile {
	var out []PageFile
	root := cfg.Data
	_ = filepath.WalkDir(root, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if d.IsDir() {
			if p != root && (strings.HasPrefix(d.Name(), "_") || strings.HasPrefix(d.Name(), ".")) {
				return filepath.SkipDir
			}
			return nil
		}
		if strings.HasSuffix(d.Name(), ".md") {
			rel, _ := filepath.Rel(root, p)
			out = append(out, PageFile{p, strings.TrimSuffix(filepath.ToSlash(rel), ".md")})
		}
		return nil
	})
	return out
}

func sortedPages() []PageFile {
	pages := iterPages()
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

// ScratchNote is a scratch note in the list.
type ScratchNote struct {
	Slug, Title, Date string
	ts                time.Time
}

// listScratch: every scratch note, newest first (in sharing mode without
// those not for sharing): the wiki's top level, and with users the top of
// the user's private folder.
func listScratch(rq *Req) []ScratchNote {
	folders := []string{""}
	if own := privateFolder(rq.user); own != "" {
		folders = append(folders, own)
	}
	var notes []ScratchNote
	for _, folder := range folders {
		prefix := ""
		if folder != "" {
			prefix = folder + "/"
		}
		entries, _ := os.ReadDir(filepath.Join(cfg.Data, filepath.FromSlash(folder)))
		for _, e := range entries {
			name := e.Name()
			if e.IsDir() || !strings.HasSuffix(name, ".md") {
				continue
			}
			slug := prefix + strings.TrimSuffix(name, ".md")
			m := scratchRE.FindStringSubmatch(slug)
			if m == nil || !isScratch(slug) || hiddenPage(rq, slug) {
				continue
			}
			ts, err := time.ParseInLocation("2006-01-02_15-04-05", m[1], time.Local)
			if err != nil {
				continue
			}
			title, _ := pageMeta(filepath.Join(cfg.Data, filepath.FromSlash(slug)+".md"), slug)
			notes = append(notes, ScratchNote{slug, title, ts.Format("2006-01-02 15:04"), ts})
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

// isNoshare: a page LEAF_NOSHARE names: in a noshare folder, or with a
// noshare tag in its frontmatter.
func isNoshare(slug string) bool {
	low := strings.ToLower(slug)
	for _, f := range cfg.NoshareFolder {
		if strings.HasPrefix(low, f) || low == f[:len(f)-1] {
			return true
		}
	}
	if len(cfg.NoshareTags) == 0 {
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
		meta, _ := parseFrontmatter(text)
		for _, t := range meta.Tags {
			if cfg.NoshareTags[strings.ToLower(t)] {
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
// refusePage. False: go ahead.
func refuseWrite(w http.ResponseWriter, r *http.Request, slug string) bool {
	if !canWrite(userOf(r), slug) {
		http.NotFound(w, r)
		return true
	}
	return false
}

// pageTaskRefs: the ids of the tasks a page refers to with [[task:…]],
// those user sees.
func pageTaskRefs(user, content string) map[string]bool {
	masked, _ := maskCode(content)
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

// pageLinkTargets: every slug a page links to (wikilinks, markdown links).
func pageLinkTargets(content, slug string) map[string]bool {
	targets := map[string]bool{}
	for _, m := range wikilinkRE.FindAllStringSubmatch(content, -1) {
		if page, _, _ := splitWikilink(m[1]); page != "" {
			targets[toSlug(page)] = true
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

// Per page, by its file's mtime: title, link targets, deck embeds' params.
// Edits made outside the app show, as the mtime changes.
var (
	metaMu    sync.Mutex
	metaCache = map[string]pageMetaEntry{}
)

type pageMetaEntry struct {
	mtime   time.Time
	title   string
	targets map[string]bool
	embeds  []DeckParams
}

// pageDeckEmbeds: the params of each [[deck …]] on a page, outside code.
func pageDeckEmbeds(content string) []DeckParams {
	masked, _ := maskCode(content)
	var out []DeckParams
	for _, m := range deckRE.FindAllStringSubmatch(masked, -1) {
		out = append(out, parseDeckParams(m[1]))
	}
	return out
}

// DeckList is a list an embed declares: its name and params.
type DeckList struct {
	Name   string
	Params DeckParams
}

func embedsLists(embeds []DeckParams) []DeckList {
	var out []DeckList
	for _, p := range embeds {
		if name := cleanList(p.Get("list")); name != "" {
			out = append(out, DeckList{name, p})
		}
	}
	return out
}

// pageDeckLists: each [[deck list=…]] on a page, outside code.
func pageDeckLists(content string) []DeckList { return embedsLists(pageDeckEmbeds(content)) }

func pageMetaEntryOf(p, slug string) pageMetaEntry {
	st, err := os.Stat(p)
	if err != nil {
		return pageMetaEntry{title: slugDisplayName(slug), targets: map[string]bool{}}
	}
	metaMu.Lock()
	cached, ok := metaCache[slug]
	metaMu.Unlock()
	if ok && cached.mtime.Equal(st.ModTime()) {
		return cached
	}
	b, err := os.ReadFile(p)
	if err != nil {
		return pageMetaEntry{title: slugDisplayName(slug), targets: map[string]bool{}}
	}
	content := newlines(strings.ToValidUTF8(string(b), "�"))
	entry := pageMetaEntry{st.ModTime(), pageTitle(content, slug), pageLinkTargets(content, slug), pageDeckEmbeds(content)}
	metaMu.Lock()
	metaCache[slug] = entry
	metaMu.Unlock()
	return entry
}

// pageMeta: a page's title and outgoing link targets, cached by mtime.
func pageMeta(p, slug string) (string, map[string]bool) {
	e := pageMetaEntryOf(p, slug)
	return e.title, e.targets
}

// DeckHome is a page with a deck embed: the deck's home, or a list's.
type DeckHome struct {
	Slug, Title string
	Params      DeckParams
}

// deckLists: every list a page user sees declares ([[deck list=<name>]]),
// by name; when several pages declare one, the first slug A–Z is its home.
// A list declared on a private page is its owner's alone: for others its
// name is a plain tag.
func deckLists(user string) map[string]DeckHome {
	out := map[string]DeckHome{}
	for _, p := range sortedPages() {
		if !canSee(user, p.Slug) {
			continue
		}
		e := pageMetaEntryOf(p.Path, p.Slug)
		for _, l := range embedsLists(e.embeds) {
			if _, ok := out[l.Name]; !ok {
				out[l.Name] = DeckHome{p.Slug, e.title, l.Params}
			}
		}
	}
	return out
}

// taskList: the list a task belongs to: the first (A–Z) of its tags a page
// declares as a list (names: deckLists), or "".
func taskList(t *Task, names map[string]DeckHome) string {
	var hits []string
	for tag := range taskTagsLower(t) {
		if _, ok := names[tag]; ok {
			hits = append(hits, tag)
		}
	}
	sort.Strings(hits)
	if len(hits) > 0 {
		return hits[0]
	}
	return ""
}

// lookupDeckHome: the page with a [[deck … home=1]] embed, user's own
// deck: one in their private folder first, else a shared one (the first
// slug A–Z when several declare one); nil: the Deck link goes to /deck.
func lookupDeckHome(user string) *DeckHome {
	var shared *DeckHome
	for _, p := range sortedPages() {
		own := ownPrivate(user, p.Slug)
		if !canSee(user, p.Slug) || shared != nil && !own {
			continue
		}
		e := pageMetaEntryOf(p.Path, p.Slug)
		for _, params := range e.embeds {
			if isTrue(params.Get("home")) {
				if own {
					return &DeckHome{p.Slug, e.title, params}
				}
				shared = &DeckHome{p.Slug, e.title, params}
				break
			}
		}
	}
	return shared
}

// deckHome is lookupDeckHome, once per request.
func (rq *Req) deckHome() *DeckHome {
	if rq == nil {
		return lookupDeckHome("")
	}
	if !rq.homeDone {
		rq.home, rq.homeDone = lookupDeckHome(rq.user), true
	}
	return rq.home
}

// homePrivate: the deck home is in the user's own folder, so a task from
// New task or c t is private.
func (rq *Req) homePrivate() bool {
	h := rq.deckHome()
	return h != nil && ownPrivate(rq.who(), h.Slug)
}

func (rq *Req) deckHomeSlug() string {
	if h := rq.deckHome(); h != nil {
		return h.Slug
	}
	return ""
}

// allBacklinkCounts: how many pages link to each slug, of those the
// request may see (rq nil: all).
func allBacklinkCounts(rq *Req) map[string]int {
	counts := map[string]int{}
	for _, p := range iterPages() {
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
func findBacklinks(slug string) []PageRef {
	var out []PageRef
	for _, p := range iterPages() {
		if p.Slug == slug {
			continue
		}
		title, targets := pageMeta(p.Path, p.Slug)
		if targets[slug] {
			out = append(out, PageRef{p.Slug, title})
		}
	}
	sort.SliceStable(out, func(i, j int) bool { return strings.ToLower(out[i].Title) < strings.ToLower(out[j].Title) })
	return out
}

// mdLinkRewriteRE: a markdown link, the prefix, the href up to any #anchor,
// ?query or title, and the rest inside the parentheses. Not an image's:
// Python's (?<!!) is the check in mdLinks.
var mdLinkRewriteRE = regexp.MustCompile(`^(\[[^\]]*\]\()(` + notSpace + `+)([^)]*\))`)

// mdLinkMatch is a markdown link found by mdLinks: its byte range and groups.
type mdLinkMatch struct {
	start, end         int
	prefix, href, rest string
}

// mdLinks finds the markdown links (not images) in text as Python's
// _MD_LINK_REWRITE_RE.finditer does: from each "[" not after "!", leftmost
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
// anchors, folders (a trailing /), files (another extension) and Leaf's own
// _folders and views are not checked.
func mdLinkMisses(content, slug string, slugs map[string]bool) map[string]string {
	text, _ := maskCode(content)
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
			continue // Leaf's own folders and views
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
// at that heading of newSlug (a scratch note appended to a page is that
// section now). Wikilinks and markdown links (relative, .md, /page/) keep
// their style, anchor and title; relative hrefs are recomputed from
// source's folder. Code is left alone.
func rewritePageLinks(text, oldSlug, newSlug, source, section string) string {
	text, stash := maskCode(text)
	text = wikilinkRE.ReplaceAllStringFunc(text, func(m string) string {
		l := parseWikilink(m)
		if page := strings.TrimSpace(l.page); page == "" || page != oldSlug && toSlug(l.page) != oldSlug {
			return m
		}
		l.page = newSlug
		if !l.hasSection && section != "" {
			l.section, l.hasSection = section, true
		}
		return l.String()
	})
	sourceDir := posixDir(source)
	anchor := ""
	if section != "" {
		anchor = "#" + sectionAnchor(section)
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
	return unmaskCode(text, stash)
}

// updateReferences rewrites links to oldSlug in every page and deck task
// after a rename (to a section of newSlug: rewritePageLinks); returns the
// files it changed, for the rename's commit.
func updateReferences(oldSlug, newSlug, section string) []string {
	var modified []string
	for _, p := range iterPages() {
		content, err := readText(p.Path)
		if err != nil {
			continue
		}
		if n := rewritePageLinks(content, oldSlug, newSlug, p.Slug, section); n != content {
			if writeText(p.Path, n) == nil {
				modified = append(modified, p.Path)
			}
		}
	}
	changed, _ := renamePageRefs(func(text string) string {
		return rewritePageLinks(text, oldSlug, newSlug, "", section)
	})
	return append(modified, changed...)
}

// rebaseLinks re-points the links in text, written by page source, for a
// page now at newSource and for pages that moved (moves: old → new slug).
// Every relative link still reaches the same page or file from where the
// page is now; wikilinks name pages, so only their targets change.
func rebaseLinks(text, source, newSource string, moves map[string]string) string {
	text, stash := maskCode(text)
	text = wikilinkRE.ReplaceAllStringFunc(text, func(m string) string {
		l := parseWikilink(m)
		key := strings.TrimSpace(l.page)
		old := key
		if _, ok := moves[key]; !ok {
			old = toSlug(key)
		}
		if _, ok := moves[old]; key == "" || !ok {
			return m
		}
		l.page = moves[old]
		return l.String()
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
	return unmaskCode(text, stash)
}

// rewriteMovedLinks: after the pages in moves are at their new paths, point
// the links to them at the new slugs (pages and tasks) and re-base the
// moved pages' own links. Returns the files that changed.
func rewriteMovedLinks(moves map[string]string) []string {
	inverse := map[string]string{}
	for o, n := range moves {
		inverse[n] = o
	}
	var modified []string
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
			continue
		}
		if n := rebaseLinks(content, source, p.Slug, moves); n != content {
			if writeText(p.Path, n) == nil {
				add(p.Path)
			}
		}
	}
	for _, o := range sortedKeys(moves) {
		n := moves[o]
		changed, _ := renamePageRefs(func(text string) string { return rewritePageLinks(text, o, n, "", "") })
		for _, c := range changed {
			add(c)
		}
	}
	return modified
}

// pageFolders: the folders that hold pages, A–Z, for filing a scratch note
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
	return toSlug(raw)
}

func fmtDate(t time.Time) string { return t.Local().Format("2006-01-02") }
