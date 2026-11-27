// Updates (/updates; Updates in docs/spec/navigation.md): what is new for
// whoever is signed in, in one place, a section a kind: the pages that
// mention them (mentions.go), the pages they watch that changed (watch.go),
// and the tasks with news for them, which is
// the task app in its Updates mode (tasksembed.go: updatesScope), embedded
// as it is in a page. It is a page of its own, not
// a view of Tasks: what it tells of is not tasks alone. The top bar's bell
// leads here and says how many there are (updatesCount).
package main

import (
	"html/template"
	"net/http"
)

// updatesHref: where Updates is.
const updatesHref = "/updates"

func updatesRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /updates", updatesPage)
	mux.HandleFunc("POST /updates/read", updatesReadRoute)
	mux.HandleFunc("GET /api/mentionable", mentionableRoute)
}

// updatesView: the page (updates.html).
type updatesView struct {
	view
	BreadcrumbHTML template.HTML
	// Mentions: the pages that mention the user, the latest first.
	Mentions []shownMention
	// Watches: they can watch pages (mayWatch, with git): the section is
	// there; Watching: how many pages and folders they watch; Watched: the
	// watched pages with news, the latest change first.
	Watches  bool
	Watching int
	Watched  []shownWatched
	// Unread: something of the page's own rows is unread (a mention, a
	// watched page): Mark all read shows from the first paint.
	Unread bool
	// Tasks: the task app in its Updates mode.
	TasksHTML template.HTML
}

// updatesCount: how many things are new for the request's user: what the
// top bar's bell says, and Updates shows.
func (rq *Req) updatesCount() int {
	if !rq.hasUpdates() {
		return 0
	}
	return rq.taskCounts().New + len(mentionsFor(rq.who())) + rq.watchCount()
}

// updatesReadRoute: POST /updates/read: every mention on a page and every
// watched page's news is read (Mark all read; the tasks' news the task app
// reads itself). The user's
// own, and nothing of the wiki: no commit.
func updatesReadRoute(w http.ResponseWriter, r *http.Request) {
	rq := newReq(r)
	if !rq.hasUpdates() {
		http.NotFound(w, r)
		return
	}
	readMention(rq.who().Name, "", true)
	readAllWatched(rq)
	w.WriteHeader(http.StatusNoContent)
}

// updatesPage: GET /updates. Who has nothing to be told (without users, in
// sharing mode, a visitor) is sent to where they came from: the home page.
func updatesPage(w http.ResponseWriter, r *http.Request) {
	rq := newReq(r)
	if !rq.hasUpdates() {
		http.Redirect(w, r, "/", http.StatusFound)
		return
	}
	v := &updatesView{
		Mentions:       mentionsFor(rq.who()),
		Watches:        mayWatch(rq.who()) && cfg.GitEnabled,
		Watched:        watchedShown(rq),
		Watching:       len(watchesOf(rq.who().Name).Watch),
		TasksHTML:      htmlOf(buildTasksEmbed(rq, updatesParams, 0, false, "", tasksInitial(rq, updatesScope))),
		BreadcrumbHTML: breadcrumb(rq, []crumb{{"Updates", ""}}, updatesHref, true, "Updates", ""),
	}
	v.Unread = len(v.Mentions)+len(v.Watched) > 0
	page(w, rq, http.StatusOK, "updates.html", "Updates", shell{wide: true}, v)
}

// mentionableRoute: GET /api/mentionable?page=<slug>: the users a text on
// that page can name with @name so that they are told ({"names": […]}),
// for the editor's completion: who reads the page, without who asks; with
// no page (a task's text) who a task can be given to. No names in sharing
// mode, for a page the asker doesn't see, without users or for a visitor.
func mentionableRoute(w http.ResponseWriter, r *http.Request) {
	slug := r.URL.Query().Get("page")
	names := []string{}
	if !sharingOn(r) && (slug == "" || safePath(slug) != "") {
		names = append(names, mentionableOn(userOf(r), slug, false)...)
	}
	writeJSON(w, http.StatusOK, M{"names": names})
}
