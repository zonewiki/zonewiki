// The tasks API: the /api/tasks routes. Each reads its request and answers;
// the writes themselves are in tasks.go, which the MCP tools share.
package main

import (
	"encoding/json"
	"errors"
	"io"
	"mime"
	"net/http"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"zonewiki/internal/jsonobj"
	"zonewiki/internal/taskfiles"
	"zonewiki/internal/taskmodel"
)

// tasksRoute answers a task list API request: a refusal as {"error": …} (400,
// 403 for a viewer's change to a shared task, 409 for a text someone else
// changed meanwhile),
// 404, or 503 when a task list file is unreadable (the client retries).
func tasksRoute(h func(w http.ResponseWriter, r *http.Request) error) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		err := h(w, r)
		rf, refused := refusalOf(err)
		switch {
		case err == nil:
		case errors.Is(err, taskfiles.ErrCorrupt):
			writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": err.Error()})
		case refused && (rf.kind == refusedInvalid || rf.kind == refusedReadOnly || rf.kind == refusedConflict):
			// tasks.js shows the error (a viewer's: readOnlyMsg).
			writeJSON(w, rf.kind.status(), map[string]string{"error": rf.msg})
		case refused:
			http.Error(w, rf.msg, rf.kind.status())
		default:
			logf("%s %s: %v", r.Method, r.URL.Path, err)
			http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		}
	}
}

// apiNewList: a list made where the page stays (New list… in the dialog
// that moves tasks to a list, js/taskselect.js): {name, private} makes it as
// the New list… dialog of /tasks does (createListOp) and answers how the
// user names it. It is pinned as every new list is, last (a pin is a pill
// of the switcher and nothing else). A refusal is {"error": …}.
func apiNewList(w http.ResponseWriter, r *http.Request) {
	user := userOf(r)
	name, private, err := newListFields(r)
	var ref string
	const pin = true
	if err == nil {
		ref, err = createListOp(user, name, private, pin, "")
	}
	rf, refused := refusalOf(err)
	switch {
	case err == nil:
		writeJSON(w, http.StatusCreated, map[string]any{"ref": ref, "private": private, "pinned": pin})
	case refused:
		writeJSON(w, rf.kind.status(), map[string]string{"error": rf.msg})
	default:
		// Written, but not committed (notCommitted), or a registry unreadable.
		logf("%s %s: %v", r.Method, r.URL.Path, err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "The list couldn't be made (Zone's log says why)"})
	}
}

// newListFields: the name and private of apiNewList's body.
func newListFields(r *http.Request) (name string, private bool, err error) {
	data, err := jsonObject(r)
	if err != nil {
		return "", false, err
	}
	if data.Has("name") {
		if name, err = asStr(data["name"], "name"); err != nil {
			return "", false, err
		}
	}
	private, err = boolField(data, "private")
	return strings.TrimSpace(name), private, err
}

// jsonObject is the request's JSON body, which must be an object (any
// content type: text/plain bodies too).
func jsonObject(r *http.Request) (jsonObj, error) {
	body, err := io.ReadAll(r.Body)
	if tooLarge(err) {
		return nil, refuseAs(refusedTooLarge, "Too large: at most "+strconv.Itoa(bodyMax>>20)+" MB")
	}
	o, ok := jsonobj.Parse(body)
	if !ok {
		return nil, refuse("Request body must be a JSON object")
	}
	return o, nil
}

func tasksRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /api/tasks", tasksRoute(func(w http.ResponseWriter, r *http.Request) error {
		// Zone-Tasks: the word of the tasks answered (taskfiles.Changes, read
		// before them: a write in between only shows as one more change).
		w.Header().Set("Zone-Tasks", taskfiles.Changes())
		writeJSON(w, http.StatusOK, apiTasks(taskFiles.Open(userOf(r))))
		return nil
	}))
	// A hidden task app, for a page without one that wants its edit modal
	// (js/actions.js openDefaultTask: c t, the New task button).
	mux.HandleFunc("GET /api/tasks/headless", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		rq := newReq(r)
		_, _ = io.WriteString(w, buildTasksEmbed(rq, TasksParams{}, 0, true, ""))
	})
	// Who a task can be assigned to, and who asks ("me"): for the
	// browser's completion and its @me. No names in sharing mode.
	mux.HandleFunc("GET /api/tasks/assignable", tasksRoute(func(w http.ResponseWriter, r *http.Request) error {
		writeJSON(w, http.StatusOK, assignablePeople(r))
		return nil
	}))
	mux.HandleFunc("POST /api/tasks/settings", writesPages(tasksSettingsRoute))
	mux.HandleFunc("POST /api/tasks/lists", writesPages(apiNewList))
	mux.HandleFunc("GET /api/tasks/closed", tasksRoute(func(w http.ResponseWriter, r *http.Request) error {
		year, err := yearArg(r)
		if err != nil {
			return err
		}
		writeJSON(w, http.StatusOK, apiTasks(taskFiles.ClosedInYear(userOf(r), year)))
		return nil
	}))
	mux.HandleFunc("GET /api/tasks/closed-years", tasksRoute(func(w http.ResponseWriter, r *http.Request) error {
		writeJSON(w, http.StatusOK, map[string]any{"years": closedYearsIn(userOf(r), queryScope(r))})
		return nil
	}))
	mux.HandleFunc("GET /api/tasks/closed-count", tasksRoute(func(w http.ResponseWriter, r *http.Request) error {
		in, n := scopeFilter(userOf(r), queryScope(r), taskLists(userOf(r))), 0
		for _, t := range taskFiles.ClosedInYear(userOf(r), taskFiles.CurrentYear()) {
			if in(t) {
				n++
			}
		}
		writeJSON(w, http.StatusOK, map[string]int{"count": n})
		return nil
	}))
	mux.HandleFunc("GET /api/tasks/due-count", func(w http.ResponseWriter, r *http.Request) {
		// ?places=1: also each place's counts, for the list switcher.
		// Zone-Tasks: a word that changes with every write of tasks or lists
		// (taskfiles.Changes). A page knows the word of the tasks it holds
		// (GET /api/tasks answers it too, and the tasks a page brings along
		// have it), asks here every minute, and loads its lists again when
		// the word is another one: someone else changed a task
		// (js/chrome.js). It says nothing of what changed, or whose.
		w.Header().Set("Zone-Tasks", taskfiles.Changes())
		rq := newReq(r)
		if r.URL.Query().Get("places") == "" {
			writeJSON(w, http.StatusOK, taskCounts(rq))
			return
		}
		c := taskCounts(rq)
		writeJSON(w, http.StatusOK, map[string]any{"count": c.Count, "overdue": c.Overdue, "today": c.Today, "focus": c.Focus, "places": rq.placeCounts()})
	})
	mux.HandleFunc("POST /api/tasks", tasksRoute(func(w http.ResponseWriter, r *http.Request) error {
		data, err := jsonObject(r)
		if err != nil {
			return err
		}
		t, err := createTask(userOf(r), data, "Create task")
		if err != nil {
			return err
		}
		writeJSON(w, http.StatusCreated, apiTask{t})
		return nil
	}))
	mux.HandleFunc("PUT /api/tasks/{id}", tasksRoute(func(w http.ResponseWriter, r *http.Request) error {
		data, err := jsonObject(r)
		if err != nil {
			return err
		}
		t, err := updateTask(userOf(r), r.PathValue("id"), data, "")
		if err != nil {
			return err
		}
		writeJSON(w, http.StatusOK, apiTask{t})
		return nil
	}))
	mux.HandleFunc("POST /api/tasks/{id}/toggle-check", tasksRoute(func(w http.ResponseWriter, r *http.Request) error {
		data, err := jsonObject(r)
		if err != nil {
			return err
		}
		idx, err := asInt(data["idx"], "idx")
		if err != nil {
			return err
		}
		if idx < 0 {
			return refuse("idx must not be negative")
		}
		t, err := toggleNoteCheck(userOf(r), r.PathValue("id"), idx)
		if err != nil {
			return err
		}
		writeJSON(w, http.StatusOK, apiTask{t})
		return nil
	}))
	mux.HandleFunc("POST /api/tasks/{id}/comments", tasksRoute(func(w http.ResponseWriter, r *http.Request) error {
		data, err := jsonObject(r)
		if err != nil {
			return err
		}
		text, err := asStr(data["text"], "text")
		if err != nil {
			return err
		}
		t, err := addComment(userOf(r), r.PathValue("id"), text, "Comment on task")
		if err != nil {
			return err
		}
		writeJSON(w, http.StatusCreated, apiTask{t})
		return nil
	}))
	mux.HandleFunc("PUT /api/tasks/{id}/comments/{cid}", tasksRoute(apiEditComment))
	mux.HandleFunc("DELETE /api/tasks/{id}/comments/{cid}", tasksRoute(apiDeleteComment))
	mux.HandleFunc("DELETE /api/tasks/{id}", tasksRoute(func(w http.ResponseWriter, r *http.Request) error {
		if err := deleteTask(userOf(r), r.PathValue("id"), "Delete task"); err != nil {
			return err
		}
		w.WriteHeader(http.StatusNoContent)
		return nil
	}))
	mux.HandleFunc("POST /api/tasks/batch-delete", tasksRoute(apiBatchDelete))
	mux.HandleFunc("POST /api/tasks/restore", tasksRoute(apiRestore))
	mux.HandleFunc("GET /api/tasks/export", tasksRoute(apiExport))
	mux.HandleFunc("POST /api/tasks/import", tasksRoute(apiImport))
	mux.HandleFunc("POST /api/tasks/batch-complete", tasksRoute(apiBatchComplete))
	mux.HandleFunc("POST /api/tasks/reorder", tasksRoute(apiReorder))
	mux.HandleFunc("POST /api/tasks/batch-tag", tasksRoute(func(w http.ResponseWriter, r *http.Request) error {
		data, err := jsonObject(r)
		if err != nil {
			return err
		}
		ids, err := idList(data)
		if err != nil {
			return err
		}
		var tags [2][]string
		for i, k := range []string{"add", "remove"} {
			if _, ok := listField(data, k); !ok {
				return refuse("add/remove must be lists of strings")
			}
			if data.Has(k) {
				if tags[i], err = asStrList(data[k], k); err != nil {
					return err
				}
			}
		}
		tasks, err := tagTasks(userOf(r), ids, tags[0], tags[1], "Tag tasks")
		if err != nil {
			return err
		}
		writeJSON(w, http.StatusOK, apiTasks(tasks))
		return nil
	}))
	mux.HandleFunc("POST /api/tasks/batch-update", tasksRoute(apiBatchUpdate))
}

var yearArgRE = regexp.MustCompile(`^\d{4}$`)

// yearArg: ?year=YYYY (a year of the Closed view), default this year.
func yearArg(r *http.Request) (string, error) {
	year := r.URL.Query().Get("year")
	if year == "" {
		year = taskFiles.CurrentYear()
	}
	if !yearArgRE.MatchString(year) {
		return "", refuse("year must be YYYY")
	}
	return year, nil
}

func apiEditComment(w http.ResponseWriter, r *http.Request) error {
	data, err := jsonObject(r)
	if err != nil {
		return err
	}
	text, err := asStr(data["text"], "text")
	if err != nil {
		return err
	}
	if text, err = commentText(text); err != nil {
		return err
	}
	taskFiles.Lock()
	defer taskFiles.Unlock()
	active, closed, t, err := findForWriteLocked(userOf(r), r.PathValue("id"))
	if err != nil {
		return err
	}
	i, err := taskComment(t, r.PathValue("cid"))
	if err != nil {
		return err
	}
	t.Comments[i].Text = text
	if err := taskFiles.WriteBothLocked(userOf(r), active, closed, "Edit task comment"); err != nil {
		return err
	}
	writeJSON(w, http.StatusOK, apiTask{t})
	return nil
}

func apiDeleteComment(w http.ResponseWriter, r *http.Request) error {
	taskFiles.Lock()
	defer taskFiles.Unlock()
	active, closed, t, err := findForWriteLocked(userOf(r), r.PathValue("id"))
	if err != nil {
		return err
	}
	i, err := taskComment(t, r.PathValue("cid"))
	if err != nil {
		return err
	}
	rememberBy(t) // the undo sends it back
	t.Comments = slices.Delete(t.Comments, i, i+1)
	if err := taskFiles.WriteBothLocked(userOf(r), active, closed, "Delete task comment"); err != nil {
		return err
	}
	writeJSON(w, http.StatusOK, apiTask{t})
	return nil
}

func apiBatchDelete(w http.ResponseWriter, r *http.Request) error {
	data, err := jsonObject(r)
	if err != nil {
		return err
	}
	ids, err := idList(data)
	if err != nil {
		return err
	}
	if len(ids) == 0 {
		return refuse("Bad Request")
	}
	taskFiles.Lock()
	defer taskFiles.Unlock()
	if err := deleteTasksLocked(userOf(r), idSet(ids), "Delete tasks"); err != nil {
		return err
	}
	w.WriteHeader(http.StatusNoContent)
	return nil
}

// apiRestore re-inserts task snapshots (undo after delete), their ids and
// dates kept; ids that exist already are skipped.
func apiRestore(w http.ResponseWriter, r *http.Request) error {
	data, err := jsonObject(r)
	if err != nil {
		return err
	}
	snaps, ok := jsonobj.As[[]json.RawMessage](data["tasks"])
	if !ok || len(snaps) == 0 {
		return refuse("Bad Request")
	}
	taskFiles.Lock()
	defer taskFiles.Unlock()
	user := userOf(r)
	active, closed, err := taskFiles.ReadForWriteLocked(user)
	if err != nil {
		return err
	}
	existing := taskFiles.IDsLocked() // every owner's: ids stay unique across the files
	restored := []*Task{}
	lists, general := taskLists(user), false
	for _, s := range snaps {
		snap, ok := jsonobj.As[jsonObj](s)
		if !ok {
			continue
		}
		t := sanitizeSnapshot(snap)
		if t == nil || existing[t.ID] {
			continue
		}
		checkBy(user, t)
		general = placeSnapshot(user, t, snap, lists) || general
		existing[t.ID] = true
		if !t.IsOpen() {
			closed = append(closed, t)
		} else {
			active = append(active, t)
		}
		restored = append(restored, t)
	}
	if len(restored) > 0 {
		var also []string
		if general {
			path, err := ensureGeneralLocked()
			if err != nil {
				return err
			}
			also = append(also, path)
		}
		if err := taskFiles.WriteBothLocked(user, active, closed, "Restore tasks", nonEmpty(also)...); err != nil {
			return err
		}
	}
	writeJSON(w, http.StatusCreated, apiTasks(restored))
	return nil
}

// importMax: the most tasks one import takes.
const importMax = 10000

// importIDRE: an id an imported task may keep (a UUID, as Zone makes them).
var importIDRE = regexp.MustCompile(`^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$`)

// apiExport: the tasks a view shows (?list=, ?lists= or ?mine=1, ?tag=, ?assignee=, as an embed's params;
// none: every task the user sees), open and closed of every year,
// as a file to save: {zone_tasks: 1, list, tag, exported_at, tasks}, each
// task as the API shows it (with the list it is in). apiImport reads it back.
func apiExport(w http.ResponseWriter, r *http.Request) error {
	list, tag := queryScope(r), strings.TrimSpace(r.URL.Query().Get("tag"))
	lists := taskLists(userOf(r))
	assignee := r.URL.Query().Get("assignee")
	taskFiles.Lock()
	active, closed, err := taskFiles.ReadForWriteLocked(userOf(r))
	taskFiles.Unlock()
	if err != nil {
		return err
	}
	tasks := []*Task{}
	in := scopeFilter(userOf(r), list, lists)
	for _, t := range slices.Concat(active, closed) {
		if in(t) && tagParamMatches(t, tag) && assigneeParamMatches(t, assignee, userOf(r)) {
			tasks = append(tasks, t)
		}
	}
	name := strings.NewReplacer("/", "-", "|", "-").Replace(firstNonEmpty(list, "all"))
	if list == mineScope {
		list, name = "", "my-tasks" // no one list's
	}
	// The list's name is the request's: as a quoted, escaped parameter, so
	// a " in it can't end the file name and add one of its own.
	w.Header().Set("Content-Disposition", mime.FormatMediaType("attachment",
		map[string]string{"filename": "zone-tasks-" + name + "-" + isoDay(today()) + ".json"}))
	writeJSON(w, http.StatusOK, map[string]any{"zone_tasks": 1, "list": list, "tag": tag, "exported_at": timestamp(), "tasks": apiTasks(tasks)})
	return nil
}

// apiImport adds an export's tasks: {file, list, tag, new, also}. file is
// what apiExport wrote (or a bare list of tasks); list is the list imported
// into (its ref), where the tasks go, and tag and new the params of that
// view, whose tags they get as a new task there would (newTaskTags).
// Without list (All tasks, a tag's view) a task goes where its own list
// says, as an undo's does (placeSnapshot). A task exported private stays
// private wherever it is imported: into a shared list it goes to the user's
// list "private" instead. A task keeps its id, and one whose id is here
// already is skipped, so the same file imported twice adds nothing. also:
// more tags for them, the user's own for this import. Answers {added,
// skipped, ids}: ids, the added tasks', for the notice's Undo
// (batch-delete).
func apiImport(w http.ResponseWriter, r *http.Request) error {
	data, err := jsonObject(r)
	if err != nil {
		return err
	}
	user := userOf(r)
	if err := mayWriteTasks(user); err != nil {
		return err
	}
	notExport := refuse("Not a file of tasks exported from Zone")
	snaps, ok := jsonobj.As[[]json.RawMessage](data["file"])
	if !ok {
		file, isObj := jsonobj.As[jsonObj](data["file"])
		if snaps, ok = jsonobj.As[[]json.RawMessage](file["tasks"]); !isObj || !ok {
			return notExport
		}
	}
	if len(snaps) > importMax {
		return refuse("Too many tasks: at most " + strconv.Itoa(importMax) + " in one import")
	}
	str := func(k string) string { s, _ := jsonobj.As[string](data[k]); return s }
	add := taskmodel.CleanTags(strings.Split(str("new"), ","))
	if len(add) == 0 {
		add = newTaskTags(parseTasksParams("tag=" + strings.Join(strings.Fields(str("tag")), "")))
	}
	// also: tags of the user's own for this import, to find its tasks by.
	add = taskmodel.CleanTags(append(add, strings.Split(str("also"), ",")...))
	taskFiles.Lock()
	defer taskFiles.Unlock()
	lists := taskLists(user)
	var into *ListInfo
	intoInbox := isInbox(str("list"), lists)
	if ref := cleanList(str("list")); ref != "" && !intoInbox {
		l, ok := resolveList(ref, lists)
		if !ok || !canWriteList(user, l) {
			return refuse("No list #" + ref)
		}
		into = &l
	}
	general := false
	active, closed, err := taskFiles.ReadForWriteLocked(user)
	if err != nil {
		return err
	}
	existing := taskFiles.IDsLocked() // every owner's: ids stay unique across the files
	skipped, unassigned, ids := 0, 0, []string{}
	for _, s := range snaps {
		snap, _ := jsonobj.As[jsonObj](s)
		t := sanitizeSnapshot(snap)
		if t == nil {
			return notExport
		}
		// Assigned to someone who can't be given tasks here (another
		// wiki's user): imported as no one's, and said so.
		who, _ := jsonobj.As[string](snap["assignee"])
		lost := settings().Users != nil && taskmodel.CleanAssignee(who) != "" && t.Assignee == ""
		if id, _ := jsonobj.As[string](snap["id"]); !importIDRE.MatchString(id) {
			t.ID = uuid4()
		}
		if existing[t.ID] {
			skipped++
			continue
		}
		// An admin's import keeps the file's names (a wiki moved, a
		// backup put back); anyone else's only their own.
		if !settings().isAdmin(user) {
			checkBy(user, t)
		}
		existing[t.ID] = true
		if lost {
			unassigned++
		}
		t.Tags = retag(t.Tags, add, nil)
		switch private := jsonobj.True(snap["private"]) && privateFolder(user) != ""; {
		case intoInbox && privateFolder(user) != "":
			// Into the Inbox: the importer's own, whatever the file says.
			t.List, t.Owner, t.Assignee = "", user, ""
		case intoInbox:
			t.List, t.Owner = "", ""
		case into == nil:
			general = placeSnapshot(user, t, snap, lists) || general
		case private && !into.Private():
			// A task exported private stays private, wherever it is
			// imported: in the importer's Inbox.
			t.List, t.Owner, t.Assignee = "", user, ""
		default:
			t.List, t.Owner = into.Name, into.Owner
			if into.Private() {
				t.Assignee = "" // a private task has no assignee
			}
		}
		if t.IsOpen() {
			active = append(active, t)
		} else {
			closed = append(closed, t)
		}
		ids = append(ids, t.ID)
	}
	if len(ids) > 0 {
		var also []string
		if general {
			path, err := ensureGeneralLocked()
			if err != nil {
				return err
			}
			also = append(also, path)
		}
		if err := taskFiles.WriteBothLocked(user, active, closed, "Import "+strconv.Itoa(len(ids))+" tasks", nonEmpty(also)...); err != nil {
			return err
		}
	}
	out := map[string]any{"added": len(ids), "skipped": skipped, "ids": ids}
	if unassigned > 0 {
		out["unassigned"] = unassigned
	}
	writeJSON(w, http.StatusOK, out)
	return nil
}

func apiBatchComplete(w http.ResponseWriter, r *http.Request) error {
	data, err := jsonObject(r)
	if err != nil {
		return err
	}
	ids, err := idList(data)
	if err != nil {
		return err
	}
	markCompleted := true
	if data.Has("completed") {
		if markCompleted, err = asBool(data["completed"], "completed"); err != nil {
			return err
		}
	}
	// wont_do: close them undone, as PUT's wont_do does one (no next
	// occurrence, a keep task archived too).
	wontDo, err := boolField(data, "wont_do")
	if err != nil {
		return err
	}
	if wontDo && !markCompleted {
		return refuse("wont_do closes tasks; it can't come with completed false")
	}
	if len(ids) == 0 {
		return refuse("Bad Request")
	}
	set := idSet(ids)
	taskFiles.Lock()
	defer taskFiles.Unlock()
	active, closed, err := taskFiles.ReadForWriteLocked(userOf(r))
	if err != nil {
		return err
	}
	stamp := timestamp()
	updated := []*Task{}
	if wontDo {
		moving, movingIDs := selectTasks(active, func(t *Task) bool { return set[t.ID] && t.IsOpen() })
		for _, t := range moving {
			t.ClosedAt, t.WontDo = stamp, true
			logEvent(t, userOf(r), "wontdo", "", "")
			updated = append(updated, t)
		}
		active = withoutIDs(active, movingIDs)
		closed = append(closed, moving...)
	} else if markCompleted {
		for _, t := range active {
			if set[t.ID] && t.Keep && t.IsOpen() {
				keepDone(userOf(r), t) // stays open, back in the pool
				updated = append(updated, t)
			}
		}
		moving, movingIDs := selectTasks(active, func(t *Task) bool { return set[t.ID] && t.IsOpen() && !t.Keep })
		var spawned []*Task
		for _, t := range moving {
			if s := completeTask(userOf(r), t, stamp, active); s != nil {
				spawned = append(spawned, s)
			}
			updated = append(updated, t)
		}
		active = append(withoutIDs(active, movingIDs), spawned...)
		closed = append(closed, moving...)
	} else {
		moving, movingIDs := selectTasks(closed, func(t *Task) bool { return set[t.ID] && !t.IsOpen() })
		for _, t := range moving {
			active = uncompleteTask(userOf(r), t, active, set)
			updated = append(updated, t)
		}
		closed = withoutIDs(closed, movingIDs)
		active = append(active, moving...)
	}
	msg := "Reopen tasks"
	if wontDo {
		msg = "Abandon tasks"
	} else if markCompleted {
		msg = "Complete tasks"
	}
	if err := taskFiles.WriteBothLocked(userOf(r), active, closed, msg); err != nil {
		return err
	}
	writeJSON(w, http.StatusOK, apiTasks(updated))
	return nil
}

func apiReorder(w http.ResponseWriter, r *http.Request) error {
	data, err := jsonObject(r)
	if err != nil {
		return err
	}
	order, ok1 := listField(data, "order")
	rawUpdates, ok2 := listField(data, "updates")
	updates := make([]jsonObj, len(rawUpdates))
	for i, u := range rawUpdates {
		var isObj bool
		if updates[i], isObj = jsonobj.As[jsonObj](u); !isObj {
			ok2 = false
		}
	}
	if !ok1 || !ok2 {
		return refuse("order must be a list of ids, updates a list of objects")
	}
	if len(order) == 0 && len(updates) == 0 {
		return refuse("Bad Request")
	}
	taskFiles.Lock()
	defer taskFiles.Unlock()
	active, err := taskFiles.ReadOpenStrict(userOf(r))
	if err != nil {
		return err
	}
	byID := map[string]*Task{}
	for _, t := range active {
		byID[t.ID] = t
	}
	for i, id := range order {
		if s, ok := jsonobj.As[string](id); ok && byID[s] != nil {
			f := float64(i)
			byID[s].SortOrder = &f
		}
	}
	for _, u := range updates {
		id, _ := jsonobj.As[string](u["id"])
		t := byID[id]
		// null clears it: the task leaves the manual order.
		if u.Null("sort_order") && t != nil {
			t.SortOrder = nil
			continue
		}
		if f, ok := jsonobj.As[float64](u["sort_order"]); ok && t != nil {
			t.SortOrder = &f
		}
	}
	if err := taskFiles.WriteOpenLocked(userOf(r), active, "Reorder tasks"); err != nil {
		return err
	}
	w.WriteHeader(http.StatusNoContent)
	return nil
}

// apiBatchUpdate changes fields of several tasks in one commit: tasks is a
// list of {id, …fields as PUT takes them}; status changes are refused, ids
// no longer there skipped.
func apiBatchUpdate(w http.ResponseWriter, r *http.Request) error {
	data, err := jsonObject(r)
	if err != nil {
		return err
	}
	raw, ok := jsonobj.As[[]json.RawMessage](data["tasks"])
	valid := ok && len(raw) > 0
	changes := make([]jsonObj, len(raw))
	for i, c := range raw {
		o, isObj := jsonobj.As[jsonObj](c)
		if _, isStr := jsonobj.As[string](o["id"]); !isObj || !isStr {
			valid = false
			break
		}
		changes[i] = o
	}
	if !valid {
		return refuse("tasks must be a list of objects with an id")
	}
	for _, c := range changes {
		for _, k := range []string{"completed", "wont_do", "skip"} {
			if c.Has(k) {
				return refuse("batch-update changes fields, not status")
			}
		}
	}
	taskFiles.Lock()
	defer taskFiles.Unlock()
	active, closed, err := taskFiles.ReadForWriteLocked(userOf(r))
	if err != nil {
		return err
	}
	byID := map[string]*Task{}
	for _, t := range slices.Concat(active, closed) {
		byID[t.ID] = t
	}
	updated := []*Task{}
	before := assignedBefore{}
	for _, change := range changes {
		id, _ := jsonobj.As[string](change["id"])
		t := byID[id]
		if t == nil {
			continue
		}
		if _, seen := before[id]; !seen {
			before[id] = t.Assignee
		}
		rememberBy(t)
		if err := applyTaskFields(userOf(r), t, change, false); err != nil {
			return err
		}
		if change.Has("history") {
			t.History = cleanHistory(change["history"])
			checkBy(userOf(r), t)
		}
		updated = append(updated, t)
	}
	if len(updated) > 0 {
		if err := taskFiles.WriteBothLocked(userOf(r), active, closed, "Update tasks"); err != nil {
			return err
		}
		tellAssigned(userOf(r), before, updated...)
	}
	writeJSON(w, http.StatusOK, apiTasks(updated))
	return nil
}
