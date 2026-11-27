// Watching pages (Watching pages in docs/spec/navigation.md): a user
// watches a page or a folder, and is told when someone else changes it: a
// count on the menu's button, the changes in Changes (Watched), and in the
// morning's mail (digest.go). What a user watches is theirs, kept beside
// the wiki in Zonewiki's folder as their looks at tasks are (taskseen.go),
// not in the pages: a watch makes no commit and changes no page.
package main

import (
	"encoding/json"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"slices"
	"sort"
	"strconv"
	"strings"
	"sync"
	"zonewiki/internal/atomicfile"
)

// watched: what a user watches and what they have seen of it, their file
// .zone/watch/<user>.json. Watch: a page by its slug, a folder by its path
// with a "/" after it (every page in it, those made later too), each with
// when the watch began (unix seconds): what was there before is no news.
// Seen: of a watched page, the commit that was its last when the user last
// looked at it (or began to watch it).
type watched struct {
	Watch map[string]int64  `json:"watch"`
	Seen  map[string]seenAt `json:"seen,omitempty"`
	// Mail: the kinds of mail the user turned on (mailKinds in notify.go),
	// on Settings, Notifications. Kept here, with what they watch: theirs
	// alone, no commit, and a viewer's as anyone's.
	Mail []string `json:"mail,omitempty"`
}

// seenAt: a commit a user has seen a page up to: its time (unix seconds)
// and which it was, since the history's times are whole seconds and a page
// can be changed twice in one.
type seenAt struct {
	At  int64  `json:"at"`
	Sha string `json:"sha,omitempty"`
}

// after: a commit made at that time is after the one seen, and not it.
func (s seenAt) after(at int64, sha string) bool {
	return at >= s.At && (s.Sha == "" && at > s.At || s.Sha != "" && sha != s.Sha)
}

// watchStore: every user's watches, read once for a wiki's folder and
// written when one changes. Lost with Zonewiki's folder, nobody watches
// anything, and nothing else happens.
var watchStore = struct {
	sync.Mutex
	dir string
	by  map[string]*watched
}{}

func watchDir() string { return statePath("watch") }

// watchPath: user's file, "" for a name that is no file's.
func watchPath(user string) string {
	if user == "" || user != filepath.Base(user) || strings.HasPrefix(user, ".") {
		return ""
	}
	return filepath.Join(watchDir(), user+".json")
}

// loadWatches reads every user's file, once for a wiki's folder
// (watchStore locked). A file that can't be read is no watches.
func loadWatches() {
	dir := watchDir()
	if watchStore.by != nil && watchStore.dir == dir {
		return
	}
	watchStore.dir, watchStore.by = dir, map[string]*watched{}
	entries, _ := os.ReadDir(dir)
	for _, e := range entries {
		user, isJSON := strings.CutSuffix(e.Name(), ".json")
		if e.IsDir() || !isJSON {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(dir, e.Name()))
		if err != nil {
			continue
		}
		w := &watched{}
		if json.Unmarshal(raw, w) == nil && (len(w.Watch) > 0 || len(w.Mail) > 0) {
			watchStore.by[user] = w
		}
	}
}

func (w watched) clone() watched {
	out := watched{Watch: make(map[string]int64, len(w.Watch)), Seen: make(map[string]seenAt, len(w.Seen)), Mail: slices.Clone(w.Mail)}
	for k, v := range w.Watch {
		out.Watch[k] = v
	}
	for k, v := range w.Seen {
		out.Seen[k] = v
	}
	return out
}

// watchesOf: what user watches (a copy); nothing for one who watches
// nothing.
func watchesOf(user string) watched {
	watchStore.Lock()
	defer watchStore.Unlock()
	loadWatches()
	if w := watchStore.by[user]; w != nil {
		return w.clone()
	}
	return watched{}
}

// watchers: the users who watch something, A–Z.
func watchers() []string {
	watchStore.Lock()
	defer watchStore.Unlock()
	loadWatches()
	names := make([]string, 0, len(watchStore.by))
	for name := range watchStore.by {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

// changeWatches changes user's watches under the store's lock and writes
// their file (none for one who watches nothing any more and has no mail
// turned on). change says
// whether it changed anything.
func changeWatches(user string, change func(w *watched) bool) {
	path := watchPath(user)
	if path == "" {
		return
	}
	watchStore.Lock()
	defer watchStore.Unlock()
	loadWatches()
	w := watchStore.by[user]
	if w == nil {
		w = &watched{Watch: map[string]int64{}}
	}
	if w.Seen == nil {
		w.Seen = map[string]seenAt{}
	}
	if !change(w) {
		return
	}
	for slug := range w.Seen { // a look at what is no longer watched says nothing
		if _, ok := w.covers(slug); !ok {
			delete(w.Seen, slug)
		}
	}
	var err error
	if len(w.Watch) == 0 && len(w.Mail) == 0 {
		delete(watchStore.by, user)
		if err = os.Remove(path); os.IsNotExist(err) {
			err = nil
		}
	} else {
		watchStore.by[user] = w
		var text []byte
		if text, err = json.MarshalIndent(w, "", "  "); err == nil {
			err = atomicfile.WriteOwnerOnly(path, string(text)+"\n")
		}
	}
	if err != nil {
		logf("watching: what %s watches could not be written: %v", user, err)
	}
}

// folderKey: a folder as a watch names it.
func folderKey(folder string) string { return folder + "/" }

// covers: the page slug is watched, by itself or by a folder it is in,
// and since when (the earliest of them).
func (w watched) covers(slug string) (since int64, ok bool) {
	take := func(at int64) {
		if !ok || at < since {
			since, ok = at, true
		}
	}
	if at, found := w.Watch[slug]; found {
		take(at)
	}
	for i := 0; i < len(slug); i++ {
		if slug[i] == '/' {
			if at, found := w.Watch[slug[:i+1]]; found {
				take(at)
			}
		}
	}
	return since, ok
}

// folderOf: the watched folder the page slug is in (the nearest), "" for
// none.
func (w watched) folderOf(slug string) string {
	for i := len(slug) - 1; i > 0; i-- {
		if slug[i] == '/' {
			if _, ok := w.Watch[slug[:i+1]]; ok {
				return slug[:i]
			}
		}
	}
	return ""
}

// folders: w watches a folder (then every page is asked, not only those
// named).
func (w watched) folders() bool {
	for key := range w.Watch {
		if strings.HasSuffix(key, "/") {
			return true
		}
	}
	return false
}

// pathspecs: what w watches as git names it (a page's file, a folder with
// its "/"), A–Z; with under only what is in that folder, and of a watched
// folder around it the folder under.
func (w watched) pathspecs(under string) []string {
	var paths []string
	for key := range w.Watch {
		folder, isFolder := strings.CutSuffix(key, "/")
		switch {
		case isFolder && under != "" && inFolder(under, folder):
			paths = append(paths, under+"/")
		case isFolder && inFolder(folder, under):
			paths = append(paths, key)
		case !isFolder && (under == "" || strings.HasPrefix(key, under+"/")):
			paths = append(paths, key+".md")
		}
	}
	sort.Strings(paths)
	return slices.Compact(paths)
}

// mayWatch: user is someone who can watch: a user of a wiki with users.
// Without them there is nobody else whose change would be news; a public
// wiki's visitor is no one. A viewer watches as anyone: it changes nothing
// of the wiki.
func mayWatch(user asker) bool {
	return settings().Users != nil && user.Name != "" && user.Name != visitor
}

// ownChange: a commit by this author, through this assistant ("" for
// none), is user's own doing, which is no news to them. What their
// assistant wrote is news: they see what it did (First-class for people
// and for their assistants in AGENTS.md).
func ownChange(user asker, author, assistant string) bool {
	return assistant == "" && author != "" && author == user.Name
}

// news: the page slug, whose last commit is c, has news for user: they
// watch it, see it, and someone else changed it since the watch began and
// since they last looked.
func (w watched) news(user asker, slug string, c lastCommit) bool {
	return w.newer(user, slug, c.at, c.sha, commitAuthor(c.author), c.assistant) && canSee(user, slug)
}

// newer: the commit sha, made at that time by author (through assistant)
// on the page slug, is one user hasn't seen: not their own, made since the
// watch began (in its second counts: a change is rather told twice than
// not) and after the commit they last saw.
func (w watched) newer(user asker, slug string, at int64, sha, author, assistant string) bool {
	since, ok := w.covers(slug)
	return ok && at >= since && w.Seen[slug].after(at, sha) && !ownChange(user, author, assistant)
}

// watchNews: the watched pages with news for the request's user, by slug,
// A–Z, each with its last commit; none in sharing mode (as Updates), for
// who watches nothing, or while who changed the pages last isn't known
// (lastCommitsNow).
func watchNews(rq *Req) map[string]lastCommit {
	if rq.sharing {
		return nil
	}
	return watchNewsFor(rq.who(), rq)
}

// watchNewsFor: the watched pages with news for user (rq: the request
// that asks, nil for none: a tool, the morning's mail).
func watchNewsFor(user asker, rq *Req) map[string]lastCommit {
	if !mayWatch(user) {
		return nil
	}
	w := watchesOf(user.Name)
	if len(w.Watch) == 0 || !cfg.GitEnabled || !lastCommitsNow(rq) {
		return nil
	}
	return w.newsIn(user)
}

// freshFor: a function saying whether a commit of Changes is news to user:
// it changed a watched page that has news for them, after what they saw of
// it. nil for who has no news.
func freshFor(user asker, news map[string]lastCommit) func(LogEntry) bool {
	if len(news) == 0 {
		return nil
	}
	w := watchesOf(user.Name)
	return func(e LogEntry) bool {
		for _, f := range e.Files {
			slug, isPage := strings.CutSuffix(f, ".md")
			if _, has := news[slug]; isPage && has && w.newer(user, slug, e.At, e.Sha, e.Author, e.Assistant) {
				return true
			}
		}
		return false
	}
}

// newsIn: the pages with news for user among the pages' last commits as
// they are known (lastCommits).
func (w watched) newsIn(user asker) map[string]lastCommit {
	out := map[string]lastCommit{}
	lastCommits.RLock()
	defer lastCommits.RUnlock()
	if w.folders() {
		for name, c := range lastCommits.pages {
			if slug, isPage := strings.CutSuffix(name, ".md"); isPage && w.news(user, slug, c) {
				out[slug] = c
			}
		}
		return out
	}
	for slug := range w.Watch {
		if c, ok := lastCommits.pages[slug+".md"]; ok && w.news(user, slug, c) {
			out[slug] = c
		}
	}
	return out
}

// watchCount: how many watched pages have news for the request's user,
// once a request.
func (rq *Req) watchCount() int {
	if rq.watchNews == nil {
		n := len(watchNews(rq))
		rq.watchNews = &n
	}
	return *rq.watchNews
}

// setWatch: user watches the page or folder key from now on, or no longer
// (on false). A page is seen as it is when its watch begins.
func setWatch(user asker, key string, on bool) {
	var last lastCommit
	if on && cfg.GitEnabled && !strings.HasSuffix(key, "/") && lastCommitsNow(nil) {
		lastCommits.RLock()
		last = lastCommits.pages[key+".md"]
		lastCommits.RUnlock()
	}
	changeWatches(user.Name, func(w *watched) bool {
		_, has := w.Watch[key]
		switch {
		case on && !has:
			w.Watch[key] = now().Unix()
			if _, covered := w.Seen[key]; !covered && last.sha != "" {
				w.Seen[key] = seenAt{last.at, last.sha}
			}
		case !on && has:
			delete(w.Watch, key)
		default:
			return false
		}
		return true
	})
}

// lookedAt: the request's user looked at the page slug (its view, or the
// diff of its last change): its news is read. Nothing is written for a
// page without news for them.
func lookedAt(rq *Req, slug string) {
	c, ok := watchNews(rq)[slug]
	if !ok {
		return
	}
	rq.watchNews = nil
	changeWatches(rq.who().Name, func(w *watched) bool {
		w.Seen[slug] = seenAt{c.at, c.sha}
		return true
	})
}

// lastCommitIs: the page slug's last commit is sha (a diff of it is a look
// at the page as it is).
func lastCommitIs(slug, sha string) bool {
	lastCommits.RLock()
	defer lastCommits.RUnlock()
	c, ok := lastCommits.pages[slug+".md"]
	return ok && c.sha != "" && strings.HasPrefix(c.sha, sha) && len(sha) >= 7
}

// readAllWatched: every watched page's news is read by the request's
// user (Mark all read).
func readAllWatched(rq *Req) {
	news := watchNews(rq)
	if len(news) == 0 {
		return
	}
	rq.watchNews = nil
	changeWatches(rq.who().Name, func(w *watched) bool {
		for slug, c := range news {
			w.Seen[slug] = seenAt{c.at, c.sha}
		}
		return true
	})
}

// watchesMoved: pages moved (slug → new slug), and with folder a whole
// folder to newFolder: every user's watches of them follow, as the links
// do (with a folder, the watch of the folder itself, and of every page and
// folder inside it).
func watchesMoved(moves map[string]string, folder, newFolder string) {
	to := func(key string) (string, bool) {
		if n, ok := moves[key]; ok {
			return n, true
		}
		if folder != "" && strings.HasPrefix(key, folder+"/") {
			return newFolder + key[len(folder):], true
		}
		return "", false
	}
	for _, user := range watchers() {
		changeWatches(user, func(w *watched) bool {
			changed := false
			changed = moveKeys(w.Watch, to)
			return moveKeys(w.Seen, to) || changed
		})
	}
}

// moveKeys: the entries of m under the keys to names, under their new
// ones; whether there were any.
func moveKeys[V any](m map[string]V, to func(string) (string, bool)) bool {
	moved := map[string]V{}
	for key, v := range m {
		if n, ok := to(key); ok {
			moved[n] = v
			delete(m, key)
		}
	}
	for key, v := range moved {
		m[key] = v
	}
	return len(moved) > 0
}

// watchKey: the watch a request names: its page (a page the user sees,
// which is there) or its folder (a path pages can be in; whether there are
// any, and whose, the answer doesn't say). "" for neither.
func watchKey(user asker, page, folder string) string {
	if page = strings.TrimSuffix(strings.TrimSpace(page), ".md"); page != "" {
		if path := safePath(page); path != "" && canSee(user, page) && exists(path) {
			return page
		}
		return ""
	}
	if folder = cleanFolder(folder); folder != "" && safePath(folder+"/page") != "" {
		return folderKey(folder)
	}
	return ""
}

// watchRoute: POST /watch: the page (page) or folder (folder) is watched
// by who asks, or with on=0 no longer; back to where it was asked (back).
func watchRoute(w http.ResponseWriter, r *http.Request) {
	user := userOf(r)
	if !mayWatch(user) {
		http.NotFound(w, r)
		return
	}
	key := watchKey(user, r.PostFormValue("page"), r.PostFormValue("folder"))
	if key == "" {
		http.NotFound(w, r)
		return
	}
	setWatch(user, key, r.PostFormValue("on") != "0")
	http.Redirect(w, r, watchBack(r, key), http.StatusSeeOther)
}

// watchBack: where a watch's form goes back to: the address it names, in
// Zonewiki; else the page, or the watched changes.
func watchBack(r *http.Request, key string) string {
	if back := r.PostFormValue("back"); isLocalPath(back) {
		return back
	}
	if key != "" && !strings.HasSuffix(key, "/") {
		return pathURL("page", key)
	}
	return watchedHref
}

// watchedHref: Changes with only what the user watches.
const watchedHref = "/changes?watched=1"

// watchReadRoute: POST /watch/read: every watched page's news is read.
func watchReadRoute(w http.ResponseWriter, r *http.Request) {
	rq := newReq(r)
	if !mayWatch(rq.user) {
		http.NotFound(w, r)
		return
	}
	readAllWatched(rq)
	http.Redirect(w, r, watchBack(r, ""), http.StatusSeeOther)
}

// watchedItem: something the user watches, as Changes lists it: Key the
// watch, Label what it is called, Href where it is.
type watchedItem struct {
	Key, Label, Href string
	Folder           bool
}

// watchedItems: what user watches, A–Z, of what they see.
func watchedItems(user asker) []watchedItem {
	var items []watchedItem
	for key := range watchesOf(user.Name).Watch {
		if folder, isFolder := strings.CutSuffix(key, "/"); isFolder {
			if canSee(user, folder+"/page") {
				items = append(items, watchedItem{Key: folder, Label: key, Href: "/changes?under=" + url.QueryEscape(folder), Folder: true})
			}
		} else if canSee(user, key) {
			items = append(items, watchedItem{Key: key, Label: key, Href: pathURL("page", key)})
		}
	}
	sort.Slice(items, func(i, j int) bool { return items[i].Label < items[j].Label })
	return items
}

// noWatchMsg: what a tool answers where nobody can watch.
const noWatchMsg = "This wiki has no users, so there is nobody else whose change would be news: nothing is watched here."

// watchPageTool: the MCP tool watch_page: the user watches a page, or a
// folder (with a slash after it), or with watch false no longer. A tool
// that writes, though nothing of the wiki: a connection that only reads
// doesn't change what its user watches either.
func watchPageTool(args jsonObj, base string, user asker) (string, error) {
	if !mayWatch(user) {
		return "", toolErr(noWatchMsg)
	}
	raw, err := strArg(args, "slug", true)
	if err != nil {
		return "", err
	}
	on := true
	if !args.Null("watch") {
		if on, err = boolArg(args, "watch"); err != nil {
			return "", err
		}
	}
	raw = strings.TrimSpace(raw)
	key := ""
	if strings.HasSuffix(raw, "/") {
		key = watchKey(user, "", raw)
	} else if slug := mcpPageSlug(raw, user); slug != "" {
		key = watchKey(user, slug, "")
	}
	if key == "" {
		return "", toolErr("No page " + strconv.Quote(raw) + ". Use list_pages to find its slug; a folder takes a slash after it ('projects/').")
	}
	setWatch(user, key, on)
	what := "the page " + key
	if strings.HasSuffix(key, "/") {
		what = "the folder " + key + " (every page in it, those made later too)"
	}
	if !on {
		return "The user no longer watches " + what + ".", nil
	}
	return "The user watches " + what + " now: a change someone else makes there is news to them, in " + base + watchedHref +
		" and in history with watched.", nil
}
