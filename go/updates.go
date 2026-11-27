// Updates (/updates; Updates in docs/spec/navigation.md): what is new for
// whoever is signed in, in one place, a section a kind: the tasks with
// news for them, which is the task app in its Updates mode (tasksembed.go:
// updatesScope), embedded as it is in a page. It is a page of its own, not
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
}

// updatesView: the page (updates.html).
type updatesView struct {
	view
	BreadcrumbHTML template.HTML
	// Tasks: the task app in its Updates mode; Write: the user may mark
	// news read (Mark all read).
	TasksHTML template.HTML
	Write     bool
}

// updatesCount: how many things are new for the request's user: what the
// top bar's bell says, and Updates shows.
func (rq *Req) updatesCount() int {
	if !rq.hasUpdates() {
		return 0
	}
	return rq.taskCounts().New
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
		TasksHTML:      htmlOf(buildTasksEmbed(rq, updatesParams, 0, false, "", tasksInitial(rq, updatesScope))),
		Write:          mayWriteTasks(rq.who()) == nil,
		BreadcrumbHTML: breadcrumb(rq, []crumb{{"Updates", ""}}, updatesHref, true, "Updates", ""),
	}
	page(w, rq, http.StatusOK, "updates.html", "Updates", shell{wide: true}, v)
}
