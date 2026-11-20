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
	"cmp"
	"errors"
	"net/url"
	"slices"
	"strconv"
	"strings"
	"zonewiki/internal/mdtext"
	"zonewiki/internal/taskfiles"

	"golang.org/x/text/unicode/norm"
)

// sharedRef: how a user names a shared list when they have a private list
// of that name (shared/gifts); their own is the bare name.
const sharedRef = "shared/"

// generalListName: the shared list that takes a shared task that loses its
// list (with users a shared task is always in one): one in no list at start
// (fileInGeneral), a deleted list's when no other is named, a restored task
// whose list is gone. Made when first needed.
const generalListName = "general"

// ListInfo: a list as one user sees it.
type ListInfo struct {
	Ref     string      // how this user names it: its name, or shared/<name>
	Name    string      // as its registry has it
	Owner   string      // whose it is, its place (ownerPlace): "" everyone's, a user's name their private list, "@<group>" a group's
	Params  TasksParams // its settings as an embed's params, with list=<Ref>
	NoShare bool        // left out in sharing mode
	Owners  []string    // a shared or group's list's owners (canManageList); none: a list from before owners
}

// Private: only its owner sees the list and the tasks in it.
func (l ListInfo) Private() bool { return privateOwner(l.Owner) }

// Group: the group whose list it is (its members see it), "" for any other.
func (l ListInfo) Group() string {
	g, _ := taskfiles.GroupOf(l.Owner)
	return g
}

// LeftOut: the list's tasks are left out of My tasks (its setting home=off):
// a catalogue, a someday list. It counts for a list that is its user's own
// (a private one; without users any): a shared task is in My tasks by its
// assignee.
func (l ListInfo) LeftOut() bool { return strings.EqualFold(l.Params.Get("home"), "off") }

// listSettingKeys: what List settings… sets on a list (the registry's
// keys), in the order they are kept.
var listSettingKeys = []string{"view", "group", "limit", "labels", "new", "keep", "noshare", "home"}

// listInfo: a registry's list as the user who names it ref sees it.
func listInfo(l *taskfiles.List, ref string) ListInfo {
	args := []string{"list=" + ref}
	for _, k := range taskfiles.ListSettings {
		if v := l.Settings[k]; v != "" && k != "noshare" && len(strings.Fields(v)) == 1 {
			args = append(args, k+"="+v)
		}
	}
	return ListInfo{Ref: ref, Name: l.Name, Owner: l.Owner, Params: parseTasksParams(strings.Join(args, " ")),
		NoShare: isTrue(l.Settings["noshare"]), Owners: l.Owners()}
}

// listRegistries: the registries user reads: the shared one, with users
// their own (nil without), and those of the groups they are in (groupsFor).
// A visitor sees no list.
func listRegistries(user asker) (shared, own *taskfiles.Registry, groups []*taskfiles.Registry) {
	if user.Name == visitor {
		return &taskfiles.Registry{}, nil, nil
	}
	shared = taskFiles.ReadLists("")
	if privateFolder(user) != "" && user.HasPrivate() {
		own = taskFiles.ReadLists(user.Name)
	}
	if settings().Users != nil {
		for _, g := range groupsFor(user) {
			groups = append(groups, taskFiles.ReadLists(taskfiles.GroupOwner(g)))
		}
	}
	return shared, own, groups
}

// taskLists: the lists user sees, by how they name them (ListInfo.Ref):
// their private lists by name, the shared ones and their groups' by name
// too, but for one whose name a private list of theirs has (shared/<name>).
// A shared list's name is no other shared or group's list's (sharedNameTaken).
func taskLists(user asker) map[string]ListInfo {
	shared, own, groups := listRegistries(user)
	out := map[string]ListInfo{}
	if own != nil {
		for _, l := range own.Lists {
			out[l.Name] = listInfo(l, l.Name)
		}
	}
	// A group's lists before the shared ones: of a name kept twice (a merge,
	// a hand edit; no door makes one) its members see their group's.
	for _, reg := range append(groups, shared) {
		for _, l := range reg.Lists {
			ref := l.Name
			if have, twin := out[ref]; twin {
				if !have.Private() {
					continue // a name kept twice (a merge, a hand edit): the first counts
				}
				ref = sharedRef + l.Name
			}
			if info := listInfo(l, ref); canSeeList(user, info) {
				out[ref] = info
			}
		}
	}
	return out
}

// groupListNames: the names of every group's lists, and whose: what a list
// made, renamed or moved can't be called, and what a commit message names
// only to its group (hideNames).
func groupListNames() map[string]string {
	out := map[string]string{}
	if settings().Users == nil {
		return out
	}
	for _, g := range knownGroups(settings()) {
		for _, l := range taskFiles.ReadLists(taskfiles.GroupOwner(g)).Lists {
			out[l.Name] = g
		}
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
func listOnPage(name, page string, user asker, lists map[string]ListInfo) (ListInfo, bool) {
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

// inboxRef: what names the Inbox where a list is named: the tasks in no
// list (/tasks?list=inbox, [[tasks: list=inbox]], the filter word
// list:inbox). With users those are each user's own, private: a new task
// goes there unless it is made in a list or names one, and a shared task is
// always in a list. Without users they are the wiki's. No list is called so
// (checkListName). "none" names them too.
const inboxRef = "inbox"

// isInbox: ref names the Inbox: "inbox" or "none", but for a user who sees
// a list of that name (one made before the name was kept).
func isInbox(ref string, lists map[string]ListInfo) bool {
	if r := cleanList(ref); r != inboxRef && r != "none" {
		return false
	}
	_, ok := resolveList(ref, lists)
	return !ok
}

// inboxOwner: whose files hold the tasks of user's Inbox: their own with
// users, the shared ones without.
func inboxOwner(user asker) string {
	if privateFolder(user) != "" {
		return user.Name
	}
	return ""
}

// inScope: a task belongs to a view: scope is a list's ref (its tasks),
// several joined by "|" (any of theirs), "inbox" for the tasks in no list
// (isInbox), or "" for every task.
func inScope(t *Task, scope string, lists map[string]ListInfo) bool {
	if scope == "" {
		return true
	}
	if isInbox(scope, lists) {
		return t.List == ""
	}
	ref := taskList(t, lists)
	return ref != "" && slices.Contains(strings.Split(scope, "|"), ref)
}

// pinnedLists: the lists user pinned, in order: the pills of their list
// switcher, and nothing else. A user's pins are their own (their registry;
// none until they pin one); without users the wiki's (the shared registry).
func pinnedLists(user asker, lists map[string]ListInfo) []ListInfo {
	owner := pinOwner(user)
	if owner == "" && settings().Users != nil || lacksPrivate(user) {
		return nil
	}
	var out []ListInfo
	seen := map[string]bool{}
	for _, ref := range taskFiles.ReadLists(owner).Pinned {
		if l, ok := resolveList(ref, lists); ok && !seen[l.Ref] {
			seen[l.Ref] = true
			out = append(out, l)
		}
	}
	return out
}

// ---- My tasks ----

// mineScope: the scope of My tasks (mineFilter), as paramsScope and
// queryScope give it. No list's ref: a name has no "|".
const mineScope = "|mine"

// mineAssigns: tasks can be assigned to user, so assignment counts in their
// My tasks. Not without users, nor for one no task can be given to (a
// viewer): there no task is anyone's.
func mineAssigns(user asker) bool {
	s := settings()
	return s.Users != nil && s.assignable(user.Name)
}

// mineFilter: My tasks, the home view the Tasks links open and count: what
// is user's. A task of their own (a private one: in their Inbox or a
// private list) unless its list is left out (ListInfo.LeftOut), and a
// shared task assigned to them, wherever it is. An unassigned shared task
// is its list's, not anyone's. Where no task can be assigned (without
// users; a viewer: mineAssigns) every list is the user's own: every task
// but those of a list left out. The browser has the same rule
// (TasksCore.inMine; go/testdata/mine_cases.json runs through both).
func mineFilter(user asker, lists map[string]ListInfo) func(*Task) bool {
	assigns := mineAssigns(user)
	return func(t *Task) bool {
		ref := taskList(t, lists)
		return inMine(t, ref != "" && lists[ref].LeftOut(), user, assigns)
	}
}

// inMine: mineFilter's rule for one task: out, its list is left out of My
// tasks; assigns, assignment counts for user (mineAssigns).
func inMine(t *Task, out bool, user asker, assigns bool) bool {
	if assigns && !t.Private() {
		return t.Assignee == user.Name
	}
	return !out
}

// scopeFilter: the tasks a view of scope shows user: My tasks (mineScope),
// or what inScope says of a list's ref, "none" or "".
func scopeFilter(user asker, scope string, lists map[string]ListInfo) func(*Task) bool {
	if scope == mineScope {
		return mineFilter(user, lists)
	}
	return func(t *Task) bool { return inScope(t, scope, lists) }
}

// mineSettingKeys: what My tasks' settings dialog sets. mail: the kinds of
// mail about tasks the user turned on, "," between (mailKinds in notify.go).
var mineSettingKeys = []string{"view", "group", "limit", "labels", "mail"}

// mineOwner: whose registry holds user's settings of My tasks: their own,
// without users the wiki's.
func mineOwner(user asker) string {
	if privateFolder(user) != "" {
		return user.Name
	}
	return ""
}

// mineRegistry: the registry My tasks' settings and order are in
// (mineOwner's: the user's own with users), as user reads it: an empty one
// for an asker its user's private place isn't there for.
func mineRegistry(user asker) *taskfiles.Registry {
	if lacksPrivate(user) {
		return &taskfiles.Registry{}
	}
	return taskFiles.ReadLists(mineOwner(user))
}

// mineParams: the params of My tasks (/tasks?mine=1) for user: its own
// settings ("mine" in mineOwner's registry: the tab, Plan, the limit, the
// tabs' names), of the scope mine=1. A value that can't be read is left
// out, the others stay (validSettings).
func mineParams(user asker) TasksParams {
	set := validSettings(mineRegistry(user).Mine, mineSettingKeys, taskFiles.ListsPath(mineOwner(user)))
	raw := setEmbedArgs("", set)
	p := parseTasksParams(raw).With("mine", "1")
	p.src = &embedSrc{-1, raw}
	return p
}

// setMineParams writes user's settings of My tasks with those of set
// changed, in a commit by user; was: the params as the dialog saw them.
func setMineParams(user asker, was string, set map[string]string) error {
	if err := mayWriteTasks(user); err != nil {
		return err
	}
	set, err := checkEmbedSettings(set, mineSettingKeys)
	if err != nil {
		return err
	}
	if set["mail"] != "" && !mineAssigns(user) {
		return refuse("There is no mail about assigned tasks where no task can be assigned to you")
	}
	owner := mineOwner(user)
	taskFiles.Lock()
	defer taskFiles.Unlock()
	if mineParams(user).src.raw != was {
		return errEmbedChanged
	}
	reg, err := taskFiles.ReadListsStrict(owner)
	if err != nil {
		return err
	}
	reg.Mine = parseTasksParams(setEmbedArgs(was, set)).m
	path, err := taskFiles.WriteListsLocked(user, owner, reg)
	if err != nil {
		return err
	}
	return commitLists(user, "Change the settings of My tasks", path)
}

// mineOrder: the manual order of user's My tasks, task ids first to last.
// It is the view's own (their registry's "order"; without users the
// wiki's): a list's order (Task.SortOrder) is the list's, for everyone who
// looks at it, and what one ranks in My tasks, across lists, moves nothing
// there. Until they rank something it is the lists' order of their tasks.
func mineOrder(user asker) []string {
	if reg := mineRegistry(user); reg.OrderSet {
		return append([]string{}, reg.Order...)
	}
	in := mineFilter(user, taskLists(user))
	var ranked []*Task
	for _, t := range taskFiles.Open(user) {
		if t.SortOrder != nil && in(t) {
			ranked = append(ranked, t)
		}
	}
	slices.SortStableFunc(ranked, func(a, b *Task) int { return cmp.Compare(*a.SortOrder, *b.SortOrder) })
	ids := make([]string, len(ranked))
	for i, t := range ranked {
		ids[i] = t.ID
	}
	return ids
}

// setMineOrder keeps ids as the manual order of user's My tasks: those of
// open tasks they see, each once (so what is closed or gone drops out), in
// a commit by user.
func setMineOrder(user asker, ids []string) error {
	if err := mayWriteTasks(user); err != nil {
		return err
	}
	open := map[string]bool{}
	for _, t := range taskFiles.Open(user) {
		open[t.ID] = true
	}
	order := []string{}
	for _, id := range ids {
		if open[id] {
			order, open[id] = append(order, id), false
		}
	}
	owner := mineOwner(user)
	taskFiles.Lock()
	defer taskFiles.Unlock()
	reg, err := taskFiles.ReadListsStrict(owner)
	if err != nil {
		return err
	}
	reg.Order, reg.OrderSet = order, true
	path, err := taskFiles.WriteListsLocked(user, owner, reg)
	if err != nil {
		return err
	}
	return commitLists(user, "Reorder My tasks", path)
}

// ---- operations ----

// shareNeeded: a write would make private tasks shared, and didn't say so.
func shareNeeded(what string) error {
	return refuse(what + " would be shared: everyone who uses the wiki will see it. Say so to go on (share)")
}

var errSharedListed = refuse("A shared task is always in a list: move it to another shared list")

var errSharedStays = refuse("A shared task stays shared: others have seen it. Make a new task in your private list and copy the text")

// checkListName: a list's name as it is kept (cleanList), or why it can't
// be one.
func checkListName(name string) (string, error) {
	// In its composed form (NFC): "café" typed with a combining accent is
	// the café of a keyboard that has é, as a file system may give either.
	list := norm.NFC.String(cleanList(strings.TrimLeft(strings.TrimSpace(name), "#")))
	switch {
	case list == "":
		return "", refuse("A list needs a name")
	case strings.ContainsAny(list, "[],|=\"/`\n"), strings.ContainsFunc(list, mdtext.BadNameRune):
		return "", refuse(strconv.Quote(list) + ": a list's name can't contain [ ] , | = / \" \\ or a line break")
	case len(list) > 60:
		return "", refuse("A list's name has at most 60 characters")
	case list == inboxRef || list == "none":
		return "", refuse("A list can't be called " + list + ": that names the Inbox, the tasks in no list")
	}
	return list, nil
}

// listForWrite: the list ref names for user, which they may change.
func listForWrite(user asker, ref string, lists map[string]ListInfo) (ListInfo, error) {
	if err := mayWriteTasks(user); err != nil {
		return ListInfo{}, err
	}
	l, ok := resolveList(ref, lists)
	if !ok || !canWriteList(user, l) {
		return ListInfo{}, refuseAs(refusedNotFound, "No list #"+cleanList(ref))
	}
	return l, nil
}

// listForManage: the list ref names for user, which they may rename,
// delete and set (canManageList): its owners and the admins, for a shared
// list with users.
func listForManage(user asker, ref string, lists map[string]ListInfo) (ListInfo, error) {
	l, err := listForWrite(user, ref, lists)
	if err != nil {
		return l, err
	}
	if !canManageList(user, l) {
		return ListInfo{}, refuseAs(refusedReadOnly, notOwnerMsg(l))
	}
	return l, nil
}

// notOwnerMsg: why a member who is no owner can't change the list itself.
func notOwnerMsg(l ListInfo) string {
	if len(l.Owners) == 0 {
		return "#" + l.Name + " is changed by an admin of this wiki: its tasks are yours to change, the list itself (its name, its settings, deleting it) is not."
	}
	return "#" + l.Name + " is " + strings.Join(l.Owners, "'s and ") + "'s list: its tasks are yours to change, the list itself (its name, its settings, deleting it) is theirs and an admin's."
}

// listNameKey: what tells list names apart to who reads them: their
// compatibility form (NFKC: "ﬁ" is "fi", a full-width "ｔｅａｍ" is "team",
// a combining accent its letter's) in lower case. Two names with one key
// look alike, so one list's name is taken for the other (nameTaken,
// regTaken, sharedNameTaken): no list sits beside another of the same
// look to take its tasks.
func listNameKey(name string) string { return strings.ToLower(norm.NFKC.String(name)) }

// nameTaken: user sees a list called name, or by a name that looks the same
// (listNameKey), but for the list except names (its own name, for a rename).
func nameTaken(name string, lists map[string]ListInfo, except ...string) bool {
	key := listNameKey(name)
	for ref, l := range lists {
		if listNameKey(l.Name) == key && !slices.Contains(except, ref) {
			return true
		}
	}
	return false
}

// regTaken: the registry reg has a list called name, or by a name that
// looks the same (listNameKey), but except.
func regTaken(reg *taskfiles.Registry, name string, except ...string) bool {
	key := listNameKey(name)
	return slices.ContainsFunc(reg.Lists, func(l *taskfiles.List) bool {
		return listNameKey(l.Name) == key && !slices.Contains(except, l.Name)
	})
}

// sharedNameTaken: a shared or a group's list can't be called name: a list
// of the shared registry or of any group's has it, or a name that looks the
// same (listNameKey), but except. So a list's name names one list to all who
// see it, whichever groups they are in: that a name is taken is all anyone
// else learns of a group's list.
func sharedNameTaken(name string, except ...string) bool {
	if regTaken(taskFiles.ReadLists(""), name, except...) {
		return true
	}
	key := listNameKey(name)
	for other := range groupListNames() {
		if listNameKey(other) == key && !slices.Contains(except, other) {
			return true
		}
	}
	return false
}

// nonEmpty: paths without the empty ones (a file that didn't change).
func nonEmpty(paths []string) []string {
	return slices.DeleteFunc(paths, func(p string) bool { return p == "" })
}

// commitLists commits what a list operation wrote, by user.
func commitLists(user asker, message string, paths []string) error {
	paths = nonEmpty(paths)
	if len(paths) == 0 {
		return nil
	}
	// The files are written: no reader waits for the commit.
	taskFiles.Release()
	return notCommitted(gitCommit(user.Name, message, paths...))
}

// pinOwner: whose registry holds user's pins: their own with users, else
// the shared one.
func pinOwner(user asker) string {
	if privateFolder(user) != "" {
		return user.Name
	}
	return ""
}

// cleanGroup: the group a list is to be of, as it is named ("dev" or "@dev";
// "" or "everyone": none, a shared list), or why that can't be: a group's
// list is its members', so it is one of a group user is in (groupsFor), or
// a list they would no longer see.
func cleanGroup(user asker, text string) (string, error) {
	g := strings.TrimPrefix(strings.ToLower(strings.TrimSpace(text)), "@")
	switch {
	case g == "" || g == "everyone":
		return "", nil
	case settings().Users == nil:
		return "", refuse("A group's list needs users: this wiki has none")
	case !groupNameRE.MatchString(g) || !slices.Contains(knownGroups(settings()), g):
		return "", refuse(strconv.Quote(g) + " is no group's name")
	case !inGroup(user, g):
		return "", refuse("You aren't in the group " + g + ": a group's list is its members', and you wouldn't see it")
	}
	return g, nil
}

// createListOp makes a list called name, shared or (private) user's own;
// returns how user names it. A name of a list they see is refused. pin:
// the new list is pinned for user, last (it would else be out of sight
// under More), in the same commit. group: a group user is in whose list it
// is (cleanGroup; "": a shared list, for everyone), kept in its folder.
func createListOp(user asker, name string, private, pin bool, group, suffix string) (string, error) {
	if err := mayWriteTasks(user); err != nil {
		return "", err
	}
	list, err := checkListName(name)
	if err != nil {
		return "", err
	}
	g, err := cleanGroup(user, group)
	if err != nil {
		return "", err
	}
	owner := ""
	if g != "" {
		owner = taskfiles.GroupOwner(g)
	}
	if private {
		if privateFolder(user) == "" {
			return "", refuse("A private list needs users: this wiki has none")
		}
		if g != "" {
			return "", refuse("A private list is yours alone: a group's list is its members'")
		}
		if lacksPrivate(user) {
			return "", refuse(noPrivateMsg)
		}
		owner = user.Name
	}
	// The pin is kept with the user's own lists: none for an asker those
	// aren't there for.
	pin = pin && !lacksPrivate(user)
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
	if regTaken(reg, list) || !privateOwner(owner) && sharedNameTaken(list) {
		return "", refuseAs(refusedExists, "There is a list #"+list+" already")
	}
	made := &taskfiles.List{Name: list, Settings: map[string]string{}, Owner: owner}
	// A shared or group's list belongs to who made it (with users).
	if !privateOwner(owner) && privateFolder(user) != "" {
		made.SetOwners([]string{user.Name})
	}
	reg.Lists = append(reg.Lists, made)
	// The pin is in the user's registry, which is the list's own unless the
	// list is shared and the wiki has users.
	pins := reg
	if pin && pinOwner(user) != owner {
		if pins, err = taskFiles.ReadListsStrict(pinOwner(user)); err != nil {
			return "", err
		}
	}
	if pin {
		pins.Pinned = append(pins.Pinned, list)
	}
	path, err := taskFiles.WriteListsLocked(user, owner, reg)
	if err != nil {
		return "", err
	}
	paths := path
	if pins != reg {
		if path, err = taskFiles.WriteListsLocked(user, pinOwner(user), pins); err != nil {
			return "", err
		}
		paths = append(paths, path...)
	}
	return list, commitLists(user, "Create list #"+list+suffix, paths)
}

// ensureGeneralLocked: the shared list "general", made when there is none
// of that name: where a shared task goes that loses its list (with users).
// Returns the registry's path when it was written, "" when it wasn't.
func ensureGeneralLocked() (string, error) {
	reg, err := taskFiles.ReadListsStrict("")
	if err != nil {
		return "", err
	}
	if reg.Find(generalListName) != nil {
		return "", nil
	}
	reg.Lists = append(reg.Lists, &taskfiles.List{Name: generalListName, Settings: map[string]string{}})
	paths, err := taskFiles.WriteListsOf("", reg)
	if err != nil || len(paths) == 0 {
		return "", err
	}
	return taskFiles.ListsPath(""), nil
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
func renameListOp(user asker, ref, newName, suffix string, check ...func(pages []string) error) (*listRename, error) {
	lists := taskLists(user)
	l, err := listForManage(user, ref, lists)
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
	if nameTaken(name, lists, l.Ref) {
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
	if !l.Private() && sharedNameTaken(name, l.Name) || l.Private() && regTaken(regs[l.Owner], name, l.Name) {
		return nil, refuseAs(refusedExists, "There is a list #"+name+" already")
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
		o, private := placeOf(p.Slug).user(), !placeOf(p.Slug).shared()
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
		taskFiles.Hold()
		if failed = taskfiles.Write(w.path, w.tasks); failed == nil {
			written = append(written, w.path)
		}
	}
	if failed == nil {
		// The list's tasks say its new name: into the folder of that name.
		written = append(written, taskFiles.FileByListLocked([]string{l.Owner})...)
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
		var path []string
		path, failed = taskFiles.WriteListsOf(o, regs[o])
		written = append(written, path...)
	}
	if len(written) > 0 {
		failed = errors.Join(failed, notCommitted(gitCommit(user.Name, "Rename list #"+l.Name+" to #"+name+suffix, written...)))
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
// into to (nil: no list, which for a private list's tasks is their user's
// Inbox: they stay private); a task that leaves a private list for a shared
// one is shared, and its history says so and gives it to user (sharedBy).
// Returns how many moved.
func moveListTasks(user asker, tasks []*Task, from ListInfo, to *ListInfo) int {
	n := 0
	for _, t := range tasks {
		if t.List != from.Name || t.Owner != from.Owner {
			continue
		}
		t.List, n = "", n+1
		owner := from.Owner
		if to != nil {
			t.List, owner = to.Name, to.Owner
		}
		if t.Owner != owner {
			if privateOwner(t.Owner) && !privateOwner(owner) {
				sharedBy(t, user.Name, true)
			}
			t.Owner = owner
		}
	}
	return n
}

// deleteListOp deletes the list user names ref; its tasks go to the list
// to names, never away. With no list named (""), a private list's go to
// their user's Inbox, and a shared list's, which with users are always in a
// list, to the shared list "general" (made for them when there is none);
// without users they are in no list, the wiki's Inbox. A private list's
// tasks that would become shared need share. wontDo: its open tasks are closed
// undone on the way, as Won't do closes one (no next occurrence, a keep
// task too), so a list that is over leaves no open tasks behind; they can
// be reopened from Closed. Pins of it go; embeds that name it stay, and say
// that there is no such list.
//
// Into a list fewer people see (a list for some that someone who sees its
// tasks now is none of) its tasks go as any task goes there: only those
// user made, or any by an admin (mayNarrowTask); and, as narrowing a list
// does, it asks first (askError; confirmed: asked and said yes) when open
// tasks would be with someone who doesn't see that list.
func deleteListOp(user asker, ref, to string, share, wontDo, confirmed bool, suffix string) (int, error) {
	taskFiles.Lock()
	defer taskFiles.Unlock()
	lists := taskLists(user)
	l, err := listForManage(user, ref, lists)
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
			return 0, refuse("Move its tasks to another list")
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
	case has && l.Private() && target != nil && !target.Private() && !share:
		return 0, shareNeeded("The tasks of #" + l.Name)
	case has && !l.Private() && target == nil && settings().Users != nil && l.Name == generalListName:
		return 0, refuse("With users a shared task is always in a list: name the list the tasks of #" + l.Name + " go to")
	}
	toGeneral := has && !l.Private() && target == nil && settings().Users != nil
	if toGeneral {
		target = &ListInfo{Ref: generalListName, Name: generalListName}
	}
	if has && target != nil && !l.Private() && !target.Private() {
		if lost := taskLosers(l.Owner, target.Owner); len(lost) > 0 {
			moving, _ := selectTasks(slices.Concat(active, closed), func(t *Task) bool { return t.List == l.Name && t.Owner == l.Owner })
			if slices.ContainsFunc(moving, func(t *Task) bool { return !mayNarrowTask(user, t) }) {
				return 0, refuse(namesOr(lost, 3, "") + " would no longer see the tasks of #" + l.Name + ": #" + target.Name +
					" is for fewer people. Only who made a task, or an admin, moves it there: move them to a list as wide")
			}
			if n, names := assignedOutside(moving, target.Owner); n > 0 && !wontDo && !confirmed {
				who := ", who don't see #"
				if len(names) == 1 {
					who = ", who doesn't see #"
				}
				return 0, &askError{outsideTasks(n, "of #"+l.Name) + " assigned to " + strings.Join(names, ", ") + who + target.Name + ". " +
					"The tasks keep their names and count as unassigned there, to hand on; for them again, they are theirs as before. Delete again to go on."}
			}
		}
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
	if toGeneral && reg.Find(generalListName) == nil {
		reg.Lists = append(reg.Lists, &taskfiles.List{Name: generalListName, Settings: map[string]string{}})
	}
	if wontDo {
		stamp := timestamp()
		closing, closingIDs := selectTasks(active, func(t *Task) bool { return t.List == l.Name && t.Owner == l.Owner && t.IsOpen() })
		for _, t := range closing {
			t.ClosedAt, t.WontDo = stamp, true
			logEvent(t, user.Name, "wontdo", "", "")
		}
		active = withoutIDs(active, closingIDs)
		closed = append(closed, closing...)
	}
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
		paths = append(paths, path...)
	}
	return n, taskFiles.WriteBothLocked(user, active, closed, "Delete list #"+l.Name+suffix, nonEmpty(paths)...)
}

// shareListOp makes user's private list ref a shared one, its tasks with
// it: everyone who uses the wiki then sees them, or the members of group
// (cleanGroup: one user is in; "": everyone). Never the other way.
func shareListOp(user asker, ref, group, suffix string) (string, error) {
	g, err := cleanGroup(user, group)
	if err != nil {
		return "", err
	}
	to := ""
	if g != "" {
		to = taskfiles.GroupOwner(g)
	}
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
	own, err := taskFiles.ReadListsStrict(user.Name)
	if err != nil {
		return "", err
	}
	shared, err := taskFiles.ReadListsStrict(to)
	if err != nil {
		return "", err
	}
	entry := own.Find(l.Name)
	if entry == nil {
		return "", refuseAs(refusedConflict, "The lists have changed since. Reload and try again.")
	}
	if sharedNameTaken(l.Name) {
		return "", refuseAs(refusedExists, "There is a shared list #"+l.Name+" already: rename yours first, or delete it and move its tasks there")
	}
	active, closed, err := taskFiles.ReadForWriteLocked(user)
	if err != nil {
		return "", err
	}
	own.Lists = slices.DeleteFunc(own.Lists, func(x *taskfiles.List) bool { return x.Name == l.Name })
	entry.Owner = to
	entry.SetOwners([]string{user.Name}) // shared now, and theirs to manage
	shared.Lists = append(shared.Lists, entry)
	dest := ListInfo{Name: l.Name, Owner: to}
	moveListTasks(user, slices.Concat(active, closed), l, &dest)
	var paths []string
	for o, reg := range map[string]*taskfiles.Registry{to: shared, user.Name: own} {
		path, err := taskFiles.WriteListsLocked(user, o, reg)
		if err != nil {
			return "", err
		}
		paths = append(paths, path...)
	}
	slices.Sort(paths)
	msg := "Share list #" + l.Name
	if g != "" {
		msg += " with the group " + g
	}
	return l.Name, taskFiles.WriteBothLocked(user, active, closed, msg+suffix, paths...)
}

// pinListOp pins the list user names ref ("1": last), makes it their first
// pinned list ("first"), or unpins it ("0"). The pins are the user's own
// (their registry); without users the wiki's.
func pinListOp(user asker, ref, pin string) error {
	if err := mayWriteTasks(user); err != nil {
		return err
	}
	if lacksPrivate(user) {
		return refuseAs(refusedReadOnly, noPrivateMsg)
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
	reg.PinsSet = true
	path, err := taskFiles.WriteListsLocked(user, owner, reg)
	if err != nil {
		return err
	}
	return commitLists(user, msg, path)
}

// pinOrderOp puts user's pinned lists in the order refs names them (the
// row's pills, dragged): the pinned lists among refs first, as named, then
// the pinned ones it leaves out, as they were. Nothing is pinned or unpinned by it; an order that is the one
// they have writes nothing.
func pinOrderOp(user asker, refs []string) error {
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
	return commitLists(user, "Reorder the pinned lists", path)
}

// placeWords: a list's place as a commit or a message says it: "everyone",
// "the group dev".
func placeWords(owner string) string {
	if g, ok := taskfiles.GroupOf(owner); ok {
		return "the group " + g
	}
	return "everyone"
}

// setListPlaceOp moves the shared or group's list user names ref, with its
// tasks, to the place group says (cleanGroup: a group user is in; "":
// everyone's), for who manages it (canManageList): its entry goes to that
// place's registry and its tasks' files to that place's folder, in one
// commit. To fewer people it asks first when open tasks there are given to
// someone who would no longer see them (askError; confirmed: asked and said
// yes): they keep their names and count as no one's (assigneeGone).
func setListPlaceOp(user asker, ref, group string, confirmed bool) error {
	taskFiles.Lock()
	defer taskFiles.Unlock()
	l, err := listForManage(user, ref, taskLists(user))
	if err != nil {
		return err
	}
	if l.Private() {
		return refuse("#" + l.Name + " is private, yours alone: Make shared… gives it to everyone or to a group")
	}
	g, err := cleanGroup(user, group)
	if err != nil {
		return err
	}
	to := ""
	if g != "" {
		to = taskfiles.GroupOwner(g)
	}
	if to == l.Owner {
		return refuseAs(refusedUnchanged, "#"+l.Name+" is "+placeWords(to)+"'s already")
	}
	active, closed, err := taskFiles.ReadForWriteLocked(user)
	if err != nil {
		return err
	}
	if !confirmed {
		mine, _ := selectTasks(active, func(t *Task) bool { return t.Owner == l.Owner && t.List == l.Name })
		if n, names := assignedOutside(mine, to); n > 0 {
			return &askError{outsideTasks(n, "here") + " assigned to " + strings.Join(names, ", ") + ", who will no longer see the list. " +
				"The tasks keep their names and count as unassigned here, to hand on; in the group again, they are theirs as before. Save again to go on."}
		}
	}
	from, err := taskFiles.ReadListsStrict(l.Owner)
	if err != nil {
		return err
	}
	dest, err := taskFiles.ReadListsStrict(to)
	if err != nil {
		return err
	}
	entry := from.Find(l.Name)
	if entry == nil || dest.Find(l.Name) != nil {
		return refuseAs(refusedConflict, "The lists have changed since. Reload and try again.")
	}
	from.Lists = slices.DeleteFunc(from.Lists, func(x *taskfiles.List) bool { return x.Name == l.Name })
	entry.Owner = to
	dest.Lists = append(dest.Lists, entry)
	moveListTasks(user, slices.Concat(active, closed), l, &ListInfo{Name: l.Name, Owner: to})
	var paths []string
	for _, o := range []string{l.Owner, to} {
		reg := map[string]*taskfiles.Registry{l.Owner: from, to: dest}[o]
		path, err := taskFiles.WriteListsLocked(user, o, reg)
		if err != nil {
			return err
		}
		paths = append(paths, path...)
	}
	return taskFiles.WriteBothLocked(user, active, closed, "Move list #"+l.Name+" to "+placeWords(to), paths...)
}

// assignedOutside: of tasks, how many are open and given to someone who
// doesn't see the place to (an owner, ownerPlace), and those people, A–Z:
// what a list moved there, or tasks moved into a list there, would leave
// counting as no one's (assigneeGone).
func assignedOutside(tasks []*Task, to string) (n int, names []string) {
	p := ownerPlace(to)
	if p.shared() {
		return 0, nil
	}
	for _, t := range tasks {
		if t.IsOpen() && !privateOwner(t.Owner) && t.Assignee != "" && !p.sees(asUser(t.Assignee)) {
			if n++; !slices.Contains(names, t.Assignee) {
				names = append(names, t.Assignee)
			}
		}
	}
	slices.Sort(names)
	return n, names
}

// outsideTasks: "3 open tasks <where> are", for assignedOutside's question.
func outsideTasks(n int, where string) string {
	if n == 1 {
		return "1 open task " + where + " is"
	}
	return strconv.Itoa(n) + " open tasks " + where + " are"
}

// askError: an operation that wants a yes first: what it would do, said;
// the door asks, and calls again confirmed.
type askError struct{ msg string }

func (e *askError) Error() string { return e.msg }

// setListSettingsOp changes the settings of the list user names ref: set
// maps a setting (listSettingKeys) to its value, "" taking it out.
func setListSettingsOp(user asker, ref string, set map[string]string) error {
	set, err := checkEmbedSettings(set, listSettingKeys)
	if err != nil {
		return err
	}
	taskFiles.Lock()
	defer taskFiles.Unlock()
	l, err := listForManage(user, ref, taskLists(user))
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
	return commitLists(user, "Change the settings of list #"+l.Name, path)
}
