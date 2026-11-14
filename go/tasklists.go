// Task lists: a list is a place a task is in, kept in a registry beside the
// task files (lists.json; package taskfiles): the shared lists in _tasks/,
// a user's private ones in private/<user>/_tasks/. A task names its list
// (Task.List), and its file says which registry that name is in, so a task
// in a private list is its user's alone and every other task is shared.
// Here: the lists a user sees, their pins, and the operations on lists
// (make, rename, delete, share, pin, settings), which the routes and the
// MCP tools share.
package main

import (
	"errors"
	"net/url"
	"slices"
	"strconv"
	"strings"
	"zonewiki/internal/mdtext"
	"zonewiki/internal/taskfiles"
)

// sharedRef: how a user names a shared list when they have a private list
// of that name (shared/gifts); their own is the bare name.
const sharedRef = "shared/"

// privateListName: the list a user's private task goes to when it has no
// other place (the migration; a restore or import whose list is gone).
const privateListName = "private"

// ListInfo: a list as one user sees it.
type ListInfo struct {
	Ref     string      // how this user names it: its name, or shared/<name>
	Name    string      // as its registry has it
	Owner   string      // whose private list; "" a shared one
	Params  TasksParams // its settings as an embed's params, with list=<Ref>
	NoShare bool        // left out in sharing mode
}

// Private: only its owner sees the list and the tasks in it.
func (l ListInfo) Private() bool { return l.Owner != "" }

// listSettingKeys: what List settings… sets on a list (the registry's
// keys), in the order they are kept.
var listSettingKeys = []string{"view", "plan", "limit", "labels", "new", "keep", "noshare"}

// listInfo: a registry's list as the user who names it ref sees it.
func listInfo(l *taskfiles.List, ref string) ListInfo {
	args := []string{"list=" + ref}
	for _, k := range taskfiles.ListSettings {
		if v := l.Settings[k]; v != "" && k != "noshare" && len(strings.Fields(v)) == 1 {
			args = append(args, k+"="+v)
		}
	}
	return ListInfo{Ref: ref, Name: l.Name, Owner: l.Owner, Params: parseTasksParams(strings.Join(args, " ")),
		NoShare: isTrue(l.Settings["noshare"])}
}

// listRegistries: the registries user reads: the shared one and, with
// users, their own (nil without). A visitor sees no list.
func listRegistries(user string) (shared, own *taskfiles.Registry) {
	if user == visitor {
		return &taskfiles.Registry{}, nil
	}
	shared = taskFiles.ReadLists("")
	if privateFolder(user) != "" {
		own = taskFiles.ReadLists(user)
	}
	return shared, own
}

// taskLists: the lists user sees, by how they name them (ListInfo.Ref):
// their private lists by name, the shared ones by name too, but for one
// whose name a private list of theirs has (shared/<name>).
func taskLists(user string) map[string]ListInfo {
	shared, own := listRegistries(user)
	out := map[string]ListInfo{}
	if own != nil {
		for _, l := range own.Lists {
			out[l.Name] = listInfo(l, l.Name)
		}
	}
	for _, l := range shared.Lists {
		ref := l.Name
		if _, twin := out[ref]; twin {
			ref = sharedRef + l.Name
		}
		out[ref] = listInfo(l, ref)
	}
	return out
}

// resolveList: the list a ref names among lists; shared/<name> also when
// the user has no private list of that name (the plain name would do).
func resolveList(ref string, lists map[string]ListInfo) (ListInfo, bool) {
	ref = cleanList(ref)
	if l, ok := lists[ref]; ok {
		return l, true
	}
	if name, ok := strings.CutPrefix(ref, sharedRef); ok {
		if l, ok := lists[name]; ok && !l.Private() {
			return l, true
		}
	}
	return ListInfo{}, false
}

// sharedList: the shared list named name among lists (a user's own list of
// that name aside).
func sharedList(name string, lists map[string]ListInfo) (ListInfo, bool) {
	name = cleanList(name)
	if l, ok := lists[sharedRef+name]; ok {
		return l, true
	}
	if l, ok := lists[name]; ok && !l.Private() {
		return l, true
	}
	return ListInfo{}, false
}

// listOnPage: the list an embed's list=<name> shows on page for user: on
// their own private page (and off any page, page "") what the name is to
// them; on any other page the shared list, so a shared page shows everyone
// the same one.
func listOnPage(name, page, user string, lists map[string]ListInfo) (ListInfo, bool) {
	if page != "" && !ownPrivate(user, page) && !strings.HasPrefix(cleanList(name), sharedRef) {
		return sharedList(name, lists)
	}
	return resolveList(name, lists)
}

// taskList: the list a task is in, as the user who sees lists names it (a
// ref); "" for none, and for a list they don't see.
func taskList(t *Task, lists map[string]ListInfo) string {
	if t.List == "" {
		return ""
	}
	for _, ref := range []string{t.List, sharedRef + t.List} {
		if l, ok := lists[ref]; ok && l.Owner == t.Owner {
			return ref
		}
	}
	return ""
}

// inScope: a task belongs to a view: scope is a list's ref (its tasks),
// several joined by "|" (any of theirs), or "" for every task.
func inScope(t *Task, scope string, lists map[string]ListInfo) bool {
	if scope == "" {
		return true
	}
	ref := taskList(t, lists)
	return ref != "" && slices.Contains(strings.Split(scope, "|"), ref)
}

// pinnedLists: the lists user pinned, in order, the first being where a new
// task goes: their own pins when they have set some, else the wiki's (the
// shared registry's, which are everyone's without users).
func pinnedLists(user string, lists map[string]ListInfo) []ListInfo {
	shared, own := listRegistries(user)
	from, find := shared, sharedList
	if own != nil && own.PinsSet {
		from, find = own, resolveList
	}
	var out []ListInfo
	seen := map[string]bool{}
	for _, ref := range from.Pinned {
		if l, ok := find(ref, lists); ok && !seen[l.Ref] {
			seen[l.Ref] = true
			out = append(out, l)
		}
	}
	return out
}

// onlyMine: user has set Pinned to the tasks assigned to them ("mine" in
// their registry): their pinned lists as one show only those, the Tasks
// links count only those, and a new task from anywhere (New task, c t, an
// assistant's create_task without a list) is assigned to them. Only with
// users, and for one a task can be assigned to.
func onlyMine(user string) bool {
	if privateFolder(user) == "" || !settings().assignable(user) {
		return false
	}
	return taskFiles.ReadLists(user).Mine
}

// setOnlyMineOp turns onlyMine on or off for user.
func setOnlyMineOp(user string, on bool) error {
	if listsUnmigrated {
		return errListsUnmigrated
	}
	if err := mayWriteTasks(user); err != nil {
		return err
	}
	if privateFolder(user) == "" {
		return refuse("Only your tasks needs users: this wiki has none")
	}
	taskFiles.Lock()
	defer taskFiles.Unlock()
	reg, err := taskFiles.ReadListsStrict(user)
	if err != nil {
		return err
	}
	reg.Mine = on
	path, err := taskFiles.WriteListsLocked(user, user, reg)
	if err != nil {
		return err
	}
	msg := "Pinned shows everyone's tasks"
	if on {
		msg = "Pinned shows only my tasks"
	}
	return commitLists(user, msg, []string{path})
}

// lookupDefaultList: the list a new task of user's goes to where no one list
// is in view, their first pinned one; nil when they pinned none.
func lookupDefaultList(user string) *ListInfo {
	if pinned := pinnedLists(user, taskLists(user)); len(pinned) > 0 {
		return &pinned[0]
	}
	return nil
}

// ---- operations ----

// listsUnmigrated: the lists couldn't be taken over from the pages at
// start (migrateLists failed): no list is made or changed until a start
// succeeds, since a registry written now would stand for "taken over".
var listsUnmigrated bool

var errListsUnmigrated = refuse("The task lists couldn't be set up when Zone started (its log says why): nothing about lists can be changed until that is mended and Zone restarted")

// shareNeeded: a write would make private tasks shared, and didn't say so.
func shareNeeded(what string) error {
	return refuse(what + " would be shared: everyone who uses the wiki will see it. Say so to go on (share)")
}

var errSharedStays = refuse("A shared task stays shared: others have seen it. Make a new task in your private list and copy the text")

// checkListName: a list's name as it is kept (cleanList), or why it can't
// be one.
func checkListName(name string) (string, error) {
	list := cleanList(strings.TrimLeft(strings.TrimSpace(name), "#"))
	switch {
	case list == "":
		return "", refuse("A list needs a name")
	case strings.ContainsAny(list, "[],|=\"/`\n"):
		return "", refuse(strconv.Quote(list) + ": a list's name can't contain [ ] , | = / or \"")
	case len(list) > 60:
		return "", refuse("A list's name has at most 60 characters")
	}
	return list, nil
}

// listForWrite: the list ref names for user, which they may change.
func listForWrite(user, ref string, lists map[string]ListInfo) (ListInfo, error) {
	if listsUnmigrated {
		return ListInfo{}, errListsUnmigrated
	}
	if err := mayWriteTasks(user); err != nil {
		return ListInfo{}, err
	}
	l, ok := resolveList(ref, lists)
	if !ok || !canWriteList(user, l) {
		return ListInfo{}, refuseAs(refusedNotFound, "No list #"+cleanList(ref))
	}
	return l, nil
}

// nameTaken: user sees a list called name.
func nameTaken(name string, lists map[string]ListInfo) bool {
	_, own := lists[name]
	_, twin := lists[sharedRef+name]
	return own || twin
}

// nonEmpty: paths without the empty ones (a file that didn't change).
func nonEmpty(paths []string) []string {
	return slices.DeleteFunc(paths, func(p string) bool { return p == "" })
}

// commitLists commits what a list operation wrote, by user.
func commitLists(user, message string, paths []string) error {
	paths = nonEmpty(paths)
	if len(paths) == 0 {
		return nil
	}
	return notCommitted(gitCommit(user, message, paths...))
}

// seedPins: a user's own registry that says nothing of pins yet gets the
// wiki's, which they have seen as theirs until now: their first pin (or
// unpin, or new order) starts from those.
func seedPins(user, owner string, reg *taskfiles.Registry, lists map[string]ListInfo) {
	if owner != "" && !reg.PinsSet {
		for _, p := range pinnedLists(user, lists) {
			reg.Pinned = append(reg.Pinned, p.Ref)
		}
	}
	reg.PinsSet = true
}

// pinOwner: whose registry holds user's pins: their own with users, else
// the shared one.
func pinOwner(user string) string {
	if privateFolder(user) != "" {
		return user
	}
	return ""
}

// createListOp makes a list called name, shared or (private) user's own;
// returns how user names it. A name of a list they see is refused. pin:
// the new list is pinned for user, last (a list made in the browser, which
// would else be out of sight under More; not an assistant's), in the same
// commit.
func createListOp(user, name string, private, pin bool, suffix string) (string, error) {
	if listsUnmigrated {
		return "", errListsUnmigrated
	}
	if err := mayWriteTasks(user); err != nil {
		return "", err
	}
	list, err := checkListName(name)
	if err != nil {
		return "", err
	}
	owner := ""
	if private {
		if privateFolder(user) == "" {
			return "", refuse("A private list needs users: this wiki has none")
		}
		owner = user
	}
	taskFiles.Lock()
	defer taskFiles.Unlock()
	lists := taskLists(user)
	if nameTaken(list, lists) {
		return "", refuseAs(refusedExists, "There is a list #"+list+" already")
	}
	reg, err := taskFiles.ReadListsStrict(owner)
	if err != nil {
		return "", err
	}
	if reg.Find(list) != nil {
		return "", refuseAs(refusedExists, "There is a list #"+list+" already")
	}
	reg.Lists = append(reg.Lists, &taskfiles.List{Name: list, Settings: map[string]string{}, Owner: owner})
	// The pin is in the user's registry, which is the list's own unless the
	// list is shared and the wiki has users.
	pins := reg
	if pin && pinOwner(user) != owner {
		if pins, err = taskFiles.ReadListsStrict(pinOwner(user)); err != nil {
			return "", err
		}
	}
	if pin {
		seedPins(user, pinOwner(user), pins, lists)
		pins.Pinned = append(pins.Pinned, list)
	}
	path, err := taskFiles.WriteListsLocked(user, owner, reg)
	if err != nil {
		return "", err
	}
	paths := []string{path}
	if pins != reg {
		if path, err = taskFiles.WriteListsLocked(user, pinOwner(user), pins); err != nil {
			return "", err
		}
		paths = append(paths, path)
	}
	return list, commitLists(user, "Create list #"+list+suffix, paths)
}

// ensurePrivateListLocked: user's list "private", made when they have none
// of that name: where a private task goes that has no other place. Returns
// the registry's path when it was written.
func ensurePrivateListLocked(user string) (string, error) {
	reg, err := taskFiles.ReadListsStrict(user)
	if err != nil {
		return "", err
	}
	if reg.Find(privateListName) != nil {
		return "", nil
	}
	reg.Lists = append(reg.Lists, &taskfiles.List{Name: privateListName, Settings: map[string]string{}, Owner: user})
	return taskFiles.WriteListsOf(user, reg)
}

// renameRefs: a registry's pins with the refs rename changes renamed;
// true when one was.
func renameRefs(reg *taskfiles.Registry, rename func(ref string) string) bool {
	changed := false
	for i, ref := range reg.Pinned {
		if n := rename(ref); n != ref {
			reg.Pinned[i], changed = n, true
		}
	}
	return changed
}

// dropRefs: a registry's pins without those drop names; true when one went.
func dropRefs(reg *taskfiles.Registry, drop func(ref string) bool) bool {
	n := len(reg.Pinned)
	reg.Pinned = slices.DeleteFunc(reg.Pinned, drop)
	return len(reg.Pinned) != n
}

// renameListInText: page or task text with the lists its [[tasks: list=…]]
// embeds and its links to /tasks?list= name renamed as rename says (a ref
// to a ref; the same for one it leaves); code stays as written.
func renameListInText(text string, rename func(ref string) string) string {
	masked, stash := mdtext.MaskCode(text)
	masked = tasksRE.ReplaceAllStringFunc(masked, func(m string) string {
		at := tasksRE.FindStringSubmatchIndex(m) // at[2:4]: the args
		args := embedWordRE.ReplaceAllStringFunc(m[at[2]:at[3]], func(word string) string {
			k, v, ok := strings.Cut(word, "=")
			if ok && strings.ToLower(k) == "list" {
				if n := rename(cleanList(v)); n != cleanList(v) {
					return k + "=" + n
				}
			}
			return word
		})
		return m[:at[2]] + args + m[at[3]:]
	})
	masked = tasksURLRE.ReplaceAllStringFunc(masked, func(m string) string {
		at := strings.Index(m, "/tasks?") + len("/tasks?")
		parts := strings.Split(m[at:], "&")
		for i, part := range parts {
			k, v, _ := strings.Cut(part, "=")
			value, err := url.QueryUnescape(v)
			if err != nil || k != "list" {
				continue
			}
			if n := rename(cleanList(value)); n != cleanList(value) {
				parts[i] = k + "=" + url.QueryEscape(n)
			}
		}
		return m[:at] + strings.Join(parts, "&")
	})
	return mdtext.UnmaskCode(masked, stash)
}

// listRename: what a rename did, as far as its user may see.
type listRename struct {
	Ref   string   // how user names the list now
	Tasks int      // the tasks in it
	Pages []string // the pages whose embeds and links were re-pointed
}

// renameListOp renames the list user names ref to newName: in its
// registry, on its tasks (open, closed, archived), in the users' pins, and
// where pages and tasks' text name it ([[tasks: list=…]], /tasks?list=),
// in one commit. The door holds pagesMu (it writes pages). A name of a list
// user sees is refused: two lists are made one by deleting one into the
// other. A shared list's name is everyone's, so the rename reaches every
// user's pins, private pages and private tasks' text too, as a page
// rename's links do; a private list's only its owner's.
//
// check (a door's own, optional) is asked before anything is written, with
// the pages the rename will change as far as user sees them: its refusal
// stops the rename (an assistant's guides, checkGuides).
func renameListOp(user, ref, newName, suffix string, check ...func(pages []string) error) (*listRename, error) {
	lists := taskLists(user)
	l, err := listForWrite(user, ref, lists)
	if err != nil {
		return nil, err
	}
	name, err := checkListName(newName)
	if err != nil {
		return nil, err
	}
	if name == l.Name {
		return nil, refuseAs(refusedUnchanged, "The list is already called #"+name)
	}
	if nameTaken(name, lists) {
		return nil, refuseAs(refusedExists, "There is a list #"+name+" already. To make the two one, delete #"+l.Name+" and move its tasks there.")
	}
	taskFiles.Lock()
	defer taskFiles.Unlock()
	// Whose registries name it: a private list only its owner's, a shared
	// one everyone's.
	owners := []string{l.Owner}
	if !l.Private() {
		owners = taskFiles.EveryOwner()
	}
	regs := map[string]*taskfiles.Registry{}
	for _, o := range owners {
		if regs[o], err = taskFiles.ReadListsStrict(o); err != nil {
			return nil, err
		}
	}
	entry := regs[l.Owner].Find(l.Name)
	if entry == nil || regs[l.Owner].Find(name) != nil {
		return nil, refuseAs(refusedConflict, "The lists have changed since. Reload and try again.")
	}
	// How the text of owner o (their pages, their tasks; "" the shared
	// ones) names the list before and after: o's own list of a name comes
	// first for them.
	renameFor := func(o string) func(string) string {
		has := func(n string) bool { return o != "" && regs[o] != nil && regs[o].Find(n) != nil }
		return func(r string) string {
			switch {
			case l.Private() && o == l.Owner && r == l.Name:
				return name
			case l.Private():
				return r
			case r == sharedRef+l.Name, r == l.Name && !has(l.Name):
				if has(name) {
					return sharedRef + name
				}
				return name
			}
			return r
		}
	}
	entry.Name = name
	done := &listRename{Ref: name}
	// The tasks: its own (their list), and the text of every task that may
	// name it.
	type taskWrite struct {
		path  string
		tasks []*Task
	}
	var writes []taskWrite
	for _, f := range taskFiles.Files(owners) {
		tasks, err := taskfiles.ReadStrict(f.Path)
		if err != nil {
			return nil, err
		}
		changed, rename := false, renameFor(f.Owner)
		for _, t := range tasks {
			if f.Owner == l.Owner && t.List == l.Name {
				t.List, changed = name, true
				done.Tasks++
			}
			for _, field := range []*string{&t.Title, &t.Description} {
				if n := renameListInText(*field, rename); n != *field {
					*field, changed = n, true
				}
			}
		}
		if changed {
			writes = append(writes, taskWrite{f.Path, tasks})
		}
	}
	// The pages: shared ones and those of the owners above.
	type pageWrite struct {
		PageFile
		text string
	}
	var pages []pageWrite
	for _, p := range iterPages() {
		o, private := pageOwner(p.Slug)
		if private && !slices.Contains(owners, o) || l.Private() && o != l.Owner {
			continue
		}
		text, err := readText(p.Path)
		if err != nil {
			logf("rename of list #%s: %v", l.Name, err)
			return nil, refuse("Nothing was renamed: a page can't be read (Zone's log says which)")
		}
		if n := renameListInText(text, renameFor(o)); n != text {
			pages = append(pages, pageWrite{p, n})
			if canSee(user, p.Slug) {
				done.Pages = append(done.Pages, p.Slug)
			}
		}
	}
	for _, ok := range check {
		if err := ok(done.Pages); err != nil {
			return nil, err
		}
	}
	paths := make([]string, len(pages))
	for i, p := range pages {
		paths[i] = p.Path
	}
	if _, err := keepPendingPages(paths, "pages before renaming list #"+l.Name); err != nil {
		return nil, err
	}
	var written []string
	var failed error
	for _, p := range pages {
		if failed = writeText(p.Path, p.text); failed != nil {
			break
		}
		written = append(written, p.Path)
	}
	for _, w := range writes {
		if failed != nil {
			break
		}
		if failed = taskfiles.Write(w.path, w.tasks); failed == nil {
			written = append(written, w.path)
		}
	}
	for _, o := range owners {
		if failed != nil {
			break
		}
		if o != l.Owner && !renameRefs(regs[o], renameFor(o)) {
			continue
		}
		if o == l.Owner {
			renameRefs(regs[o], renameFor(o))
		}
		var path string
		if path, failed = taskFiles.WriteListsOf(o, regs[o]); path != "" {
			written = append(written, path)
		}
	}
	if len(written) > 0 {
		failed = errors.Join(failed, notCommitted(gitCommit(user, "Rename list #"+l.Name+" to #"+name+suffix, written...)))
	}
	if ul := taskLists(user); failed == nil {
		for r, x := range ul {
			if x.Name == name && x.Owner == l.Owner {
				done.Ref = r
			}
		}
	}
	return done, failed
}

// moveListTasks puts the tasks of the list from (of those tasks holds)
// into to (nil: no list); a task that leaves a private list for a shared
// place is shared, and its history says so. Returns how many moved.
func moveListTasks(user string, tasks []*Task, from ListInfo, to *ListInfo) int {
	n := 0
	for _, t := range tasks {
		if t.List != from.Name || t.Owner != from.Owner {
			continue
		}
		t.List, n = "", n+1
		owner := ""
		if to != nil {
			t.List, owner = to.Name, to.Owner
		}
		if t.Owner != owner {
			t.Owner = owner
			logEvent(t, user, "shared", "", "")
		}
	}
	return n
}

// deleteListOp deletes the list user names ref; its tasks go to the list
// to names ("": to no list), never away. A private list's tasks that would
// become shared that way need share. Pins of it go; embeds that name it
// stay, and say that there is no such list.
func deleteListOp(user, ref, to string, share bool, suffix string) (int, error) {
	taskFiles.Lock()
	defer taskFiles.Unlock()
	lists := taskLists(user)
	l, err := listForWrite(user, ref, lists)
	if err != nil {
		return 0, err
	}
	var target *ListInfo
	if cleanList(to) != "" {
		t, err := listForWrite(user, to, lists)
		if err != nil {
			return 0, err
		}
		if t.Ref == l.Ref {
			return 0, refuse("Move its tasks to another list, or to no list")
		}
		target = &t
	}
	active, closed, err := taskFiles.ReadForWriteLocked(user)
	if err != nil {
		return 0, err
	}
	has := slices.ContainsFunc(slices.Concat(active, closed), func(t *Task) bool { return t.List == l.Name && t.Owner == l.Owner })
	switch {
	case has && !l.Private() && target != nil && target.Private():
		return 0, errSharedStays
	case has && l.Private() && (target == nil || !target.Private()) && !share:
		return 0, shareNeeded("The tasks of #" + l.Name)
	}
	owners := []string{l.Owner}
	if !l.Private() {
		owners = taskFiles.EveryOwner()
	}
	regs := map[string]*taskfiles.Registry{}
	for _, o := range owners {
		if regs[o], err = taskFiles.ReadListsStrict(o); err != nil {
			return 0, err
		}
	}
	reg := regs[l.Owner]
	reg.Lists = slices.DeleteFunc(reg.Lists, func(x *taskfiles.List) bool { return x.Name == l.Name })
	n := moveListTasks(user, slices.Concat(active, closed), l, target)
	var paths []string
	for _, o := range owners {
		own := o != "" && regs[o].Find(l.Name) != nil // their own list of the name: the plain name is that one
		gone := dropRefs(regs[o], func(r string) bool {
			if l.Private() {
				return r == l.Name
			}
			return r == sharedRef+l.Name || r == l.Name && !own
		})
		if o != l.Owner && !gone {
			continue
		}
		path, err := taskFiles.WriteListsOf(o, regs[o])
		if err != nil {
			return 0, err
		}
		paths = append(paths, path)
	}
	return n, taskFiles.WriteBothLocked(user, active, closed, "Delete list #"+l.Name+suffix, nonEmpty(paths)...)
}

// shareListOp makes user's private list ref a shared one, its tasks with
// it: everyone who uses the wiki then sees them. Never the other way.
func shareListOp(user, ref, suffix string) (string, error) {
	taskFiles.Lock()
	defer taskFiles.Unlock()
	lists := taskLists(user)
	l, err := listForWrite(user, ref, lists)
	if err != nil {
		return "", err
	}
	if !l.Private() {
		return "", refuseAs(refusedUnchanged, "#"+l.Name+" is shared already")
	}
	own, err := taskFiles.ReadListsStrict(user)
	if err != nil {
		return "", err
	}
	shared, err := taskFiles.ReadListsStrict("")
	if err != nil {
		return "", err
	}
	entry := own.Find(l.Name)
	if entry == nil {
		return "", refuseAs(refusedConflict, "The lists have changed since. Reload and try again.")
	}
	if shared.Find(l.Name) != nil {
		return "", refuseAs(refusedExists, "There is a shared list #"+l.Name+" already: rename yours first, or delete it and move its tasks there")
	}
	active, closed, err := taskFiles.ReadForWriteLocked(user)
	if err != nil {
		return "", err
	}
	own.Lists = slices.DeleteFunc(own.Lists, func(x *taskfiles.List) bool { return x.Name == l.Name })
	entry.Owner = ""
	shared.Lists = append(shared.Lists, entry)
	dest := ListInfo{Name: l.Name}
	moveListTasks(user, slices.Concat(active, closed), l, &dest)
	var paths []string
	for o, reg := range map[string]*taskfiles.Registry{"": shared, user: own} {
		path, err := taskFiles.WriteListsLocked(user, o, reg)
		if err != nil {
			return "", err
		}
		if path != "" {
			paths = append(paths, path)
		}
	}
	slices.Sort(paths)
	return l.Name, taskFiles.WriteBothLocked(user, active, closed, "Share list #"+l.Name+suffix, paths...)
}

// pinListOp pins the list user names ref ("1": last), makes it their first
// pinned list ("first": where a new task goes), or unpins it ("0"). The pins are the
// user's own (their registry); without users the wiki's. A user who has set
// none starts from the wiki's.
func pinListOp(user, ref, pin string) error {
	if listsUnmigrated {
		return errListsUnmigrated
	}
	if err := mayWriteTasks(user); err != nil {
		return err
	}
	taskFiles.Lock()
	defer taskFiles.Unlock()
	lists := taskLists(user)
	l, ok := resolveList(ref, lists)
	if !ok {
		return refuseAs(refusedNotFound, "No list #"+cleanList(ref))
	}
	owner := pinOwner(user)
	reg, err := taskFiles.ReadListsStrict(owner)
	if err != nil {
		return err
	}
	seedPins(user, owner, reg, lists)
	// Without users the registry's names are the refs.
	is := func(r string) bool { x, ok := resolveList(r, lists); return ok && x.Ref == l.Ref }
	msg := "Pin list #" + l.Name
	switch pin {
	case "0":
		reg.Pinned = slices.DeleteFunc(reg.Pinned, is)
		msg = "Unpin list #" + l.Name
	case "1":
		if !slices.ContainsFunc(reg.Pinned, is) {
			reg.Pinned = append(reg.Pinned, l.Ref)
		}
	case "first":
		reg.Pinned = append([]string{l.Ref}, slices.DeleteFunc(reg.Pinned, is)...)
		msg = "Pin list #" + l.Name + " first"
	default:
		return refuse("pin is 0, 1 or first")
	}
	path, err := taskFiles.WriteListsLocked(user, owner, reg)
	if err != nil {
		return err
	}
	return commitLists(user, msg, []string{path})
}

// pinOrderOp puts user's pinned lists in the order refs names them (the
// row's pills, dragged): the pinned lists among refs first, as named, then
// the pinned ones it leaves out, as they were. The first is where a new
// task goes. Nothing is pinned or unpinned by it; an order that is the one
// they have writes nothing.
func pinOrderOp(user string, refs []string) error {
	if listsUnmigrated {
		return errListsUnmigrated
	}
	if err := mayWriteTasks(user); err != nil {
		return err
	}
	taskFiles.Lock()
	defer taskFiles.Unlock()
	lists := taskLists(user)
	var was, now []string
	for _, l := range pinnedLists(user, lists) {
		was = append(was, l.Ref)
	}
	for _, ref := range refs {
		if l, ok := resolveList(cleanList(ref), lists); ok && slices.Contains(was, l.Ref) && !slices.Contains(now, l.Ref) {
			now = append(now, l.Ref)
		}
	}
	for _, ref := range was {
		if !slices.Contains(now, ref) {
			now = append(now, ref)
		}
	}
	if slices.Equal(was, now) {
		return nil
	}
	owner := pinOwner(user)
	reg, err := taskFiles.ReadListsStrict(owner)
	if err != nil {
		return err
	}
	reg.Pinned, reg.PinsSet = now, true
	path, err := taskFiles.WriteListsLocked(user, owner, reg)
	if err != nil {
		return err
	}
	return commitLists(user, "Reorder the pinned lists", []string{path})
}

// setListSettingsOp changes the settings of the list user names ref: set
// maps a setting (listSettingKeys) to its value, "" taking it out.
func setListSettingsOp(user, ref string, set map[string]string) error {
	set, err := checkEmbedSettings(set, listSettingKeys)
	if err != nil {
		return err
	}
	taskFiles.Lock()
	defer taskFiles.Unlock()
	l, err := listForWrite(user, ref, taskLists(user))
	if err != nil {
		return err
	}
	reg, err := taskFiles.ReadListsStrict(l.Owner)
	if err != nil {
		return err
	}
	entry := reg.Find(l.Name)
	if entry == nil {
		return refuseAs(refusedConflict, "The lists have changed since. Reload and try again.")
	}
	for k, v := range set {
		if v == "" {
			delete(entry.Settings, k)
		} else {
			entry.Settings[k] = v
		}
	}
	path, err := taskFiles.WriteListsLocked(user, l.Owner, reg)
	if err != nil {
		return err
	}
	return commitLists(user, "Change the settings of list #"+l.Name, []string{path})
}
