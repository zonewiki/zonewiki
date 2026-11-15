package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"sort"
	"strconv"
	"strings"
	"testing"
	"zonewiki/internal/jsonobj"
	"zonewiki/internal/taskfiles"
	"zonewiki/internal/taskmodel"
)

// A task's last activity: its completion, history, comments, creation.
func TestLastActivity(t *testing.T) {
	// As a file has them: a junk history entry is no event.
	task := func(s string) *Task { o, _ := jsonobj.Parse([]byte(s)); return taskmodel.FromJSON(o) }
	for _, c := range []struct {
		t    *Task
		want string
	}{
		{task(`{"created_at": "2026-01-02T10:00:00"}`), "2026-01-02"},
		{task(`{"created_at": "2026-01-02T10:00:00", "history": [{"at": "2026-03-04T09:00", "event": "start"}]}`), "2026-03-04"},
		{task(`{"created_at": "2026-01-02T10:00:00", "closed_at": "2026-05-06T08:00:00",
			"history": [{"at": "2026-03-04T09:00"}, "junk"]}`), "2026-05-06"},
		{task(`{"created_at": "2026-01-02T10:00:00", "comments": [{"at": "2026-04-01T09:00", "text": "x"}]}`), "2026-04-01"},
		{task(`{}`), ""},
	} {
		if got := lastActivity(c.t); got != c.want {
			t.Errorf("lastActivity(%+v) = %q, want %q", c.t, got, c.want)
		}
	}
}

// Dates as <time> elements the browser shows in the wiki's locale (js/util.js
// localizeDates); another year's with its year, date-format = iso as ISO.
func TestShortDate(t *testing.T) {
	testWiki(t, nil)
	for _, c := range [][3]string{
		{"2026-10-05", "2026-10-18", `<time class="local-date" datetime="2026-10-05">Oct 5</time>`},
		{"2025-10-05T09:30", "2026-10-18", `<time class="local-date" datetime="2025-10-05">Oct 5, 2025</time>`},
		{"<b>", "2026-10-18", "&lt;b&gt;"},
	} {
		if got := shortDate(c[0], c[1]); got != c[2] {
			t.Errorf("shortDate(%q) = %q, want %q", c[0], got, c[2])
		}
	}
	settings().DateFormat = "iso"
	if got := shortDate("2026-10-05T09:30", "2026-10-18"); got != `<time class="local-date" datetime="2026-10-05">2026-10-05</time>` {
		t.Errorf("iso: %q", got)
	}
}

func TestParseTasksParams(t *testing.T) {
	for raw, want := range map[string]map[string]string{
		"":         {},
		"tag=work": {"tag": "work"},
		"tag=work view=plan limit=5 sort=due_date": {"tag": "work", "view": "plan", "limit": "5", "sort": "due_date"},
		"list=groceries":     {"list": "groceries"},
		"Tag=work VIEW=plan": {"tag": "work", "view": "plan"},
	} {
		if got := parseTasksParams(raw).m; !reflect.DeepEqual(got, want) {
			t.Errorf("parseTasksParams(%q) = %v, want %v", raw, got, want)
		}
	}
}

// ownList: user's private list "own", made when it isn't there.
func ownList(t *testing.T, user string) string {
	t.Helper()
	if _, ok := taskLists(user)["own"]; !ok {
		if _, err := createListOp(user, "own", true, false, ""); err != nil {
			t.Fatal(err)
		}
	}
	return "own"
}

// teamList: the shared list "team", made when it isn't there.
func teamList(t *testing.T, user string) string {
	t.Helper()
	if _, ok := resolveList("team", taskLists(user)); !ok {
		if _, err := createListOp(user, "team", false, false, ""); err != nil {
			t.Fatal(err)
		}
	}
	return "team"
}

// newTestTask creates a task as user through the API's path; private asks
// for it to be theirs alone: in their private list "own". With users a
// shared task is always in a list: one that isn't private and names none
// (more: "list") is in the shared list "team"; pass "list", "" for the
// user's Inbox. Without users it is in no list.
func newTestTask(t *testing.T, user, title string, private bool, more ...string) *Task {
	t.Helper()
	data := jsonObj{}
	data.Set("title", title)
	if private {
		data.Set("list", ownList(t, user))
	} else if inboxOwner(user) != "" && !slices.Contains(more, "list") {
		data.Set("list", teamList(t, user))
	}
	for i := 0; i+1 < len(more); i += 2 {
		data.Set(more[i], more[i+1])
	}
	task, err := createTask(user, data, "Create task")
	if err != nil {
		t.Fatal(err)
	}
	return task
}

func titles(tasks []*Task) []string {
	out := []string{}
	for _, t := range tasks {
		out = append(out, t.Title)
	}
	sort.Strings(out)
	return out
}

func fileText(t *testing.T, path string) string {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return string(b)
}

// With users, a private task lives in its user's own files, which only
// they read; the files never say "private" (whose files they are does),
// the API does.
func TestPrivateTasksInTheirUsersFiles(t *testing.T) {
	testWiki(t, nil)
	withUsers(t, "alice", "bob")
	newTestTask(t, "alice", "Shared one", false)
	mine := newTestTask(t, "alice", "Alice's own", true)
	if mine.Owner != "alice" {
		t.Fatalf("owner %q", mine.Owner)
	}
	if got := titles(taskFiles.Open("alice")); !reflect.DeepEqual(got, []string{"Alice's own", "Shared one"}) {
		t.Errorf("alice sees %v", got)
	}
	if got := titles(taskFiles.Open("bob")); !reflect.DeepEqual(got, []string{"Shared one"}) {
		t.Errorf("bob sees %v", got)
	}
	if got := titles(taskFiles.Open("")); !reflect.DeepEqual(got, []string{"Shared one"}) {
		t.Errorf("Zone sees %v", got)
	}
	shared, own := fileText(t, taskFiles.OpenPath("")), fileText(t, filepath.Join(cfg.Data, "private/alice/_tasks/tasks.json"))
	if strings.Contains(shared, "Alice's own") || !strings.Contains(own, "Alice's own") ||
		strings.Contains(own, "Shared one") || strings.Contains(own+shared, `"private"`) {
		t.Errorf("files:\n%s\n%s", shared, own)
	}
	if _, err := os.Stat(taskFiles.OpenPath("bob")); err == nil {
		t.Error("bob got files of his own for no task")
	}
	b, _ := json.Marshal(apiTask{mine})
	if !strings.Contains(string(b), `"private":true`) {
		t.Errorf("API JSON %s", b)
	}
	// Another user's private task is no task at all.
	for _, err := range []error{
		func() error { _, err := updateTask("bob", mine.ID, jsonObj{}, ""); return err }(),
		func() error { _, err := addComment("bob", mine.ID, "hi", "c"); return err }(),
	} {
		if err != errNotFound {
			t.Errorf("bob's write: %v", err)
		}
	}
	if err := deleteTask("bob", mine.ID, "d"); err != nil || len(taskFiles.Open("alice")) != 2 {
		t.Errorf("bob's delete took alice's task: %v", err)
	}
	if len(findTasks("bob", strings.ToLower(mine.ID[:8]))) != 0 || len(findTasks("alice", mine.ID)) != 1 {
		t.Error("findTasks")
	}
	if refs := resolveTaskRefs("bob", []string{mine.ID[:8]}); refs[mine.ID[:8]] != nil {
		t.Error("bob resolves alice's [[task:…]]")
	}
	if refs := resolveTaskRefs("alice", []string{mine.ID[:8]}); refs[mine.ID[:8]] == nil {
		t.Error("alice doesn't resolve her own [[task:…]]")
	}
}

// Completing a private task files it in its user's closed.json, and the
// next occurrence of a series stays private, in its list; taking it out of
// its private list leaves it in its user's Inbox, private still; into a
// shared list it moves to the shared files, when the write says share.
func TestPrivateTaskMovesAndCompletes(t *testing.T) {
	testWiki(t, nil)
	withUsers(t, "alice", "bob")
	task := newTestTask(t, "alice", "Water plants", true, "due_date", isoDay(today()), "rec_type", "weekly")
	if titles(taskFiles.Open("bob")); len(taskFiles.Open("bob")) != 0 || len(taskFiles.Open("alice")) != 1 {
		t.Fatalf("after private: bob %v, alice %v", titles(taskFiles.Open("bob")), titles(taskFiles.Open("alice")))
	}
	if strings.Contains(fileText(t, taskFiles.OpenPath("")), "Water") {
		t.Error("still in the shared file")
	}
	done := jsonObj{}
	done.Set("completed", true)
	if _, err := updateTask("alice", task.ID, done, ""); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(fileText(t, taskFiles.ClosedPath("alice")), task.ID) || strings.Contains(fileText(t, taskFiles.ClosedPath("")), "Water") {
		t.Error("completed into the wrong file")
	}
	open := taskFiles.Open("alice")
	if len(open) != 1 || open[0].SpawnedFrom != task.ID || open[0].Owner != "alice" || open[0].List != "own" || len(taskFiles.Open("bob")) != 0 {
		t.Errorf("next occurrence %+v", open)
	}
	if got := taskFiles.ClosedInYear("bob", taskFiles.CurrentYear()); len(got) != 0 {
		t.Errorf("bob's Completed %v", titles(got))
	}
	inbox := jsonObj{}
	inbox.Set("list", "")
	if got, err := updateTask("alice", open[0].ID, inbox, ""); err != nil || got.List != "" || got.Owner != "alice" {
		t.Fatalf("into her Inbox: %+v, %v", got, err)
	}
	if len(taskFiles.Open("bob")) != 0 || !strings.Contains(fileText(t, taskFiles.OpenPath("alice")), "Water") || strings.Contains(fileText(t, taskFiles.OpenPath("")), "Water") {
		t.Error("a task in her Inbox left her files")
	}
	shared := jsonObj{}
	shared.Set("list", teamList(t, "alice"))
	if _, err := updateTask("alice", open[0].ID, shared, ""); err == nil {
		t.Fatal("shared without saying so")
	}
	shared.Set("share", true)
	if _, err := updateTask("alice", open[0].ID, shared, ""); err != nil {
		t.Fatal(err)
	}
	if len(taskFiles.Open("bob")) != 1 || fileText(t, taskFiles.OpenPath("alice")) != "[]\n" {
		t.Errorf("back to shared: bob %v, alice's file %q", titles(taskFiles.Open("bob")), fileText(t, taskFiles.OpenPath("alice")))
	}
}

// A task moving from a private file to the shared one is never missing
// from a read (GET /api/tasks, allTasks): the move writes two files.
func TestReadsSeeAMovingTask(t *testing.T) {
	const n = 100
	for name, read := range map[string]func(string) []*Task{"Open": taskFiles.Open, "All": taskFiles.All} {
		testWiki(t, nil)
		withUsers(t, "alice")
		var ids []string
		for i := range n {
			ids = append(ids, newTestTask(t, "alice", "Scarf "+strconv.Itoa(i), true).ID)
		}
		teamList(t, "alice")
		stop := make(chan struct{})
		go func() {
			defer close(stop)
			for _, id := range ids {
				data := jsonObj{}
				data.Set("list", "team")
				data.Set("share", true)
				if _, err := updateTask("alice", id, data, ""); err != nil {
					t.Error(err)
					return
				}
			}
		}()
	reading:
		for {
			select {
			case <-stop:
				break reading
			default:
			}
			if got := read("alice"); len(got) != n {
				<-stop
				t.Fatalf("%s: %d tasks while they move", name, len(got))
			}
		}
	}
}

// Without users nothing is private: no private list is made, and the API's
// JSON is as before (no "private"). private is no field of a write.
func TestNoPrivateTasksWithoutUsers(t *testing.T) {
	testWiki(t, nil)
	if _, err := createListOp("", "mine", true, false, ""); err == nil {
		t.Error("a private list without users")
	}
	data := jsonObj{}
	data.Set("title", "x")
	data.Set("private", true)
	if _, err := createTask("", data, "c"); err == nil {
		t.Error("private taken as a field of a write")
	}
	task := newTestTask(t, "", "y", false)
	if b, _ := json.Marshal(apiTask{task}); strings.Contains(string(b), "private") {
		t.Errorf("JSON %s", b)
	}
}

// What Zone does over all task lists reaches every user's files: a rename's
// links, the archive of closed tasks, the search index.
func TestEveryUsersFiles(t *testing.T) {
	testWiki(t, map[string]string{"old": "# Old\n"})
	withUsers(t, "alice", "bob")
	mine := newTestTask(t, "alice", "See [[old]]", true)
	changed, err := renamePageRefs(func(_, s string) string { return strings.ReplaceAll(s, "[[old]]", "[[new]]") })
	if err != nil || len(changed) != 1 || !strings.Contains(fileText(t, taskFiles.OpenPath("alice")), "[[new]]") {
		t.Errorf("rename: %v %v", changed, err)
	}
	last := jsonObj{}
	last.Set("completed", true)
	if _, err := updateTask("alice", mine.ID, last, ""); err != nil {
		t.Fatal(err)
	}
	// Closed years ago: the start of Zone files it into her archive, that month's.
	done := taskfiles.ReadLenient(taskFiles.ClosedPath("alice"))
	done[0].ClosedAt = "2001-05-01T10:00:00"
	if err := taskfiles.Write(taskFiles.ClosedPath("alice"), done); err != nil {
		t.Fatal(err)
	}
	taskFiles.ArchiveOldClosed()
	if !strings.Contains(fileText(t, taskFiles.ArchivePath("alice", "2001-05")), mine.ID) || strings.Contains(fileText(t, taskFiles.ClosedPath("alice")), mine.ID) {
		t.Error("not archived in alice's files")
	}
	if exists(taskFiles.ArchivePath("", "2001-05")) || exists(taskFiles.ArchivePath("bob", "2001-05")) {
		t.Error("archived in someone else's files")
	}
	found := false
	for _, d := range taskSearchDocs() {
		found = found || d.taskID() == mine.ID && d.Task.Owner == "alice"
	}
	if !found {
		t.Error("the search index lacks alice's task")
	}
	if !taskFiles.IDsLocked()[mine.ID] {
		t.Error("taskIDsLocked")
	}
}

// A shared task stays shared, whoever asks: it never goes into a private
// list; a private one can be shared, and then stays so.
func TestASharedTaskStaysShared(t *testing.T) {
	testWiki(t, map[string]string{})
	withUsers(t, "alice", "bob")
	task := newTestTask(t, "bob", "Errand", false)
	private := jsonObj{}
	private.Set("list", ownList(t, "alice"))
	if _, err := updateTask("alice", task.ID, private, ""); err == nil {
		t.Fatal("alice made bob's shared task private")
	}
	if got := titles(taskFiles.Open("bob")); !reflect.DeepEqual(got, []string{"Errand"}) || len(taskFiles.Open("alice")) != 1 {
		t.Errorf("bob sees %v", got)
	}
	mine := newTestTask(t, "alice", "Mine", true)
	// Into no list is into her Inbox: that shares nothing, share or not.
	inbox := jsonObj{}
	inbox.Set("list", nil)
	inbox.Set("share", true)
	if got, err := updateTask("alice", mine.ID, inbox, ""); err != nil || got.Owner != "alice" || len(taskFiles.Open("bob")) != 1 {
		t.Fatalf("into her Inbox with share: %+v, %v", got, err)
	}
	shared := jsonObj{}
	shared.Set("list", "team")
	shared.Set("share", true)
	got, err := updateTask("alice", mine.ID, shared, "")
	if err != nil {
		t.Fatal(err)
	}
	if len(taskFiles.Open("bob")) != 2 || got.History[len(got.History)-1].Event != "shared" {
		t.Errorf("bob sees %v; history %+v", titles(taskFiles.Open("bob")), got.History)
	}
	if _, err := updateTask("alice", mine.ID, private, ""); err == nil {
		t.Error("shared, then private again")
	}
	// Nor into an Inbox, hers or (asked by him) bob's: a shared task is
	// always in a list.
	for _, who := range []string{"alice", "bob"} {
		if _, err := updateTask(who, mine.ID, inbox, ""); err != errSharedListed {
			t.Errorf("%s put a shared task into an Inbox: %v", who, err)
		}
	}
	if len(taskFiles.Open("bob")) != 2 || len(taskFiles.Open("")) != 2 {
		t.Errorf("bob sees %v", titles(taskFiles.Open("bob")))
	}
}
