// Groups in the browser (docs/spec/users.md, Groups): /groups lists the
// groups a user is in and lets whoever may write make one, of named people,
// without an admin: they are its owner (owners in its [group …] section),
// and its owners say who is in it and what it is called. An admin sees every
// group there and manages each, also what it includes (a group made of
// groups) and the folders in groups/ no one sees. Who is in a group
// is still said in one place, on the users (groups in their sections): an
// owner's change writes it there, for their group alone, as an admin's
// does for any. A rename takes the group's folder, the lists that are for
// it and the settings along (renameGroupOp).
package main

import (
	"html/template"
	"io/fs"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"zonewiki/internal/mdtext"
	"zonewiki/internal/settingsfile"
)

func groupsRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /groups", groupsRoute)
	mux.HandleFunc("POST /groups", newGroupRoute)
	mux.HandleFunc("POST /groups/includes", adminOnly(groupIncludesRoute))
	mux.HandleFunc("POST /groups/{group}/people", groupPeopleRoute)
	mux.HandleFunc("POST /groups/{group}/rename", writesPages(groupRenameRoute))
	mux.HandleFunc("POST /groups/{group}/leave", groupLeaveRoute)
	mux.HandleFunc("POST /groups/{group}/remove", writesPages(groupRemoveRoute))
}

// namedMost: up to how many people a group is said by their names; a
// larger one by their number ("14 people").
const namedMost = 8

// groupsView: /groups (groups.html): the groups the user is in or owns, and
// for an admin every other group too.
type groupsView struct {
	view
	BreadcrumbHTML template.HTML
	Groups         []groupLink
	Others         []groupLink // an admin's: the groups they are in nor own
	Admin          bool        // the fields only an admin has: what a group includes, a group of groups
	MayMake        bool        // the user may write: the form that makes a group
	People         []string    // the wiki's users, for the fields to choose from
	AllGroups      []string    // every group there is, for an admin's fields to choose from
	Stray          []string    // an admin's: folders in groups/ that no one sees
	Done, Error    string      // what the last change did, or why it was refused
	// What the form that makes a group held when it was refused.
	NewName, NewPeople string
}

// groupLink: a group as /groups lists it: who is in it and by which way (up
// to namedMost by name: those it is named on, Direct, then each group it
// includes with its people, Via; a larger one by Count, with how many it is
// named on), the groups it is made of, how many pages its folder holds, its
// owners, and for who manages it the fields' values.
type groupLink struct {
	Name     string
	Direct   []string
	Via      []groupVia
	Count    int  // everyone in it, by whatever way
	Named    int  // of them, those it is named on
	Counted  bool // too many to name: Count says it
	Includes []string
	Pages    int
	Folder   bool // groups/<name>/ is there (said to an admin who isn't in it)
	Member   bool // the user is in it (an owner may be none: no way to its pages then)
	Owners   []string
	Manages  bool
	Leaves   bool // they may take themselves out (mayLeave)
	// Remove…, for who manages it: why it can't be removed yet ("": it can:
	// a list is for it, or a group includes it and the user is no admin).
	KeptBy string
	// The fields: who is in it by name (not by way of a group it includes),
	// its owners, and (an admin's) the groups it includes.
	PeopleText, OwnersText, IncludesText string
}

// groupVia: a group another includes, and who is in it now.
type groupVia struct {
	Name    string
	Members []string
}

// groupsRoute: GET /groups: the groups the signed-in user is in, and those
// they own, each with who is in it; for who manages one the fields that set
// its people, its owners and its name, and for who may write the form that
// makes one. 404 without users (and for a visitor, who is no user).
func groupsRoute(w http.ResponseWriter, r *http.Request) {
	if u := latestSettings().Users[userOf(r).Name]; u == nil || u.Disabled {
		http.NotFound(w, r)
		return
	}
	q := r.URL.Query()
	done := groupsDone(q.Get("done"), q.Get("group"), q.Get("left"))
	if g := q.Get("group"); q.Get("done") == "left" && groupNameRE.MatchString(g) && !latestSettings().memberOf(userOf(r).Name, g) {
		done = "You are no longer in " + g + ": what is for @" + g + " you no longer see. Its owners can put you back."
	}
	if q.Get("done") == "removed" {
		done = groupRemoved(q.Get("group"), q.Get("folder"), q.Get("pages"))
	}
	showGroups(w, r, http.StatusOK, done, "", "", "")
}

// groupsDone: what the change before did, from a fixed set and names that
// are a group's and users' (a link can't make the page say anything else).
func groupsDone(done, group, left string) string {
	s := latestSettings()
	// A group an admin made of no groups any more may be gone: that is said.
	if !groupNameRE.MatchString(group) || (done != "includes" && !slices.Contains(knownGroups(s), group)) {
		return ""
	}
	switch done {
	case "made":
		return "Made the group " + group + ": only its people see its pages and the task lists that are for it. New page and New list beside it start one."
	case "renamed":
		return "The group is called " + group + " now. Its people see its pages and its lists as before: what was for it is for @" + group + " (its folder is " + groupsDir + "/" + group + "/)."
	case "people", "includes":
		out := groupLine(s, group) + "."
		var gone []string
		for _, n := range strings.Split(left, ",") {
			if s.Users[n] != nil && !s.memberOf(n, group) {
				gone = append(gone, n)
			}
		}
		if len(gone) > 0 {
			out += " No longer in it: " + strings.Join(gone, ", ") + ". What is for @" + group + " they no longer see."
		}
		return out
	}
	return ""
}

// showGroups renders /groups, with what was done or refused.
func showGroups(w http.ResponseWriter, r *http.Request, status int, done, refused, newName, newPeople string) {
	s, user := latestSettings(), userOf(r)
	rq := newReq(r)
	admin := s.isAdmin(user.Name) && !user.ReadOnly && !s.isViewer(user.Name)
	v := &groupsView{BreadcrumbHTML: breadcrumb(rq, []crumb{{"Groups", ""}}, "/groups", true, "Groups", ""),
		MayMake: mayMakeGroup(s, user), Admin: admin, Done: done, Error: refused, NewName: newName, NewPeople: newPeople}
	mine := slices.Clone(s.groupsOf(user.Name))
	for _, g := range sortedKeys(s.Owners) {
		if s.ownerOf(user.Name, g) && !slices.Contains(mine, g) {
			mine = append(mine, g)
		}
	}
	slices.Sort(mine)
	v.Groups = groupLinks(rq, s, user, mine)
	if admin {
		var others []string
		for _, g := range knownGroups(s) {
			if !slices.Contains(mine, g) {
				others = append(others, g)
			}
		}
		v.Others = groupLinks(rq, s, user, others)
		v.AllGroups = knownGroups(s)
		v.Stray = strayGroups()
	}
	if v.MayMake {
		for _, name := range sortedKeys(s.Users) {
			if u := s.Users[name]; !u.Disabled {
				v.People = append(v.People, name)
			}
		}
	}
	page(w, rq, status, "groups.html", "Groups", shell{}, v)
}

// groupLinks: the groups named, as /groups lists them for user.
func groupLinks(rq *Req, s *Settings, user asker, groups []string) []groupLink {
	var out []groupLink
	for _, g := range groups {
		who := s.groupMembers(g)
		link := groupLink{Name: g, Count: len(who), Includes: s.Includes[g], Owners: s.Owners[g],
			Folder: isDir(filepath.Join(cfg.Data, groupsDir, g)),
			Member: s.memberOf(user.Name, g), Manages: s.managesGroup(user.Name, g), Leaves: mayLeave(s, user, g)}
		for _, name := range who {
			if slices.Contains(s.Users[name].Groups, g) {
				link.Direct = append(link.Direct, name)
			}
		}
		link.Named = len(link.Direct)
		if len(who) <= namedMost {
			for _, in := range s.Includes[g] {
				link.Via = append(link.Via, groupVia{in, s.groupMembers(in)})
			}
		} else {
			link.Direct, link.Counted = nil, true
		}
		if link.Member {
			link.Pages = len(pagesIn(rq, groupsDir+"/"+g))
		}
		if link.Manages {
			if err := groupKept(s, user, g); err != nil {
				link.KeptBy = err.Error()
			}
			link.PeopleText, link.OwnersText = strings.Join(s.directMembers(g), ", "), strings.Join(s.Owners[g], ", ")
			link.IncludesText = strings.Join(s.Includes[g], ", ")
		}
		out = append(out, link)
	}
	return out
}

// knownGroups: every group there is: someone is in it, it has a section
// (what it includes, its owners, even with nobody in it), or a group is
// said to include it. What a field takes as a group's name is one of these
// (groupsNamed).
func knownGroups(s *Settings) []string {
	out := s.groups()
	more := sortedKeys(s.Owners)
	for _, in := range s.Includes {
		more = append(more, in...)
	}
	for _, g := range more {
		if !slices.Contains(out, g) {
			out = append(out, g)
		}
	}
	slices.Sort(out)
	return out
}

// strayGroupNames: the groups whose folder in groups/ no one sees, by the
// folder's name: an admin puts someone in one of them, and the folder is
// that group's again.
func strayGroupNames() []string {
	var out []string
	for _, p := range strayGroups() {
		if g := strings.ToLower(strings.TrimPrefix(p, groupsDir+"/")); groupNameRE.MatchString(g) && isDir(filepath.Join(cfg.Data, p)) {
			out = append(out, g)
		}
	}
	return out
}

// groupsNamed: the groups text names, each one of those given (a field
// takes only a group there is), or why not.
func groupsNamed(text string, known []string) ([]string, error) {
	named, bad := parseGroups(text)
	if bad != "" {
		return nil, refuse(`"` + bad + `" isn't a group's name: ` + groupNameRule + ".")
	}
	for _, g := range named {
		if !slices.Contains(known, g) {
			return nil, refuse(`There is no group called ` + g + `. A group is made on Groups first.`)
		}
	}
	return named, nil
}

// refuseGroups: the change wasn't made; /groups again, saying why (a
// refusal's own status, 409 for anything else).
func refuseGroups(w http.ResponseWriter, r *http.Request, err error, newName, newPeople string) {
	status := http.StatusConflict
	if ref, ok := refusalOf(err); ok {
		if ref.kind == refusedNotFound {
			http.NotFound(w, r)
			return
		}
		status = ref.kind.status()
	}
	showGroups(w, r, status, "", err.Error(), newName, newPeople)
}

// mayMakeGroup: user may make a group: a user who may write (no viewer,
// not disabled).
func mayMakeGroup(s *Settings, user asker) bool {
	u := s.Users[user.Name]
	return u != nil && !u.Disabled && !user.ReadOnly && !s.isViewer(user.Name)
}

// groupNameTaken: something goes by the group name already, so a new group
// (or one renamed) can't take it: a group there is (someone is in it, or it
// has a section), a name a group is said to include, a folder in groups/
// (whose pages would be the new group's: a group nobody is in keeps its
// folder), or a task list that is for it (which would be the new group's
// too). The names of the groups are no secret among a wiki's users (a
// list's Who it is for… completes from them), so saying "taken" tells
// nothing new.
//
// And what a group of that name left behind: its folder's history (a group
// renamed or ended: its pages' versions are still read at groups/<name>/…,
// by who is in a group of that name) and a deleted list that was for it
// (whose folder's history is its members'). A new group of the name would
// be shown both, so the name stays taken; but for the group that went by it
// (as: the group being renamed, whose was names it), which takes its own
// name back.
func groupNameTaken(s *Settings, name, as string) bool {
	if groupNameUsed(s, name) {
		return true
	}
	if as != "" && slices.Contains(s.Was[as], name) {
		return false
	}
	for _, list := range taskFiles.ForSome() { // also the folders deleted and renamed lists left
		if slices.Contains(taskFiles.ForOfList(list), "@"+name) {
			return true
		}
	}
	return cfg.GitEnabled && strings.TrimSpace(runGit("log", "-1", "--format=%H", "--", groupsDir+"/"+name).Stdout) != ""
}

// groupNameUsed: something goes by the name now (groupNameTaken).
func groupNameUsed(s *Settings, name string) bool {
	if s.isGroup(name) {
		return true
	}
	entries, _ := os.ReadDir(filepath.Join(cfg.Data, groupsDir))
	for _, e := range entries {
		if strings.ToLower(e.Name()) == name {
			return true
		}
	}
	for _, l := range taskFiles.ReadLists("").Lists {
		if slices.Contains(l.For(), "@"+name) {
			return true
		}
	}
	return false
}

// groupTakenMsg: what who asks for a name that is taken is told.
func groupTakenMsg(name string) string {
	return "The name " + name + " is taken (by a group, a folder in " + groupsDir + "/ or a task list that is for it, now or before). Choose another."
}

// oneGroupName: the one group's name text names, or why it is none.
func oneGroupName(text string) (string, error) {
	named, bad := parseGroups(text)
	if bad != "" || len(named) != 1 {
		return "", refuse(`"` + strings.TrimSpace(text) + `" isn't a group's name: ` + groupNameRule + ".")
	}
	return named[0], nil
}

// usersNamed: the users text names, or the first that is none.
func usersNamed(s *Settings, text string) ([]string, error) {
	names, bad := parseNames(text)
	if bad == "" {
		for _, n := range names {
			if s.Users[n] == nil {
				bad = n
				break
			}
		}
	}
	if bad != "" {
		return nil, refuse(`"` + bad + `" is not a user of this wiki.`)
	}
	return names, nil
}

// setMembers writes who is in group by name, in the file f whose settings
// are s: each user's groups gain or lose that one name, and nothing else of
// theirs changes.
func setMembers(f *settingsfile.File, s *Settings, group string, members []string) {
	for _, name := range sortedKeys(s.Users) {
		groups := s.Users[name].Groups
		has, wanted := slices.Contains(groups, group), slices.Contains(members, name)
		if has == wanted {
			continue
		}
		groups = slices.DeleteFunc(slices.Clone(groups), func(g string) bool { return g == group })
		if wanted {
			groups = append(groups, group)
			slices.Sort(groups)
		}
		setGroups(f, name, groups)
	}
}

// makeGroupOp makes the group name for user, who is in it and its owner,
// with the people named (users of the wiki). Refused for who may not write,
// and for a name that is taken (groupNameTaken).
func makeGroupOp(user asker, nameText, people string) (string, error) {
	s := latestSettings()
	if !mayMakeGroup(s, user) {
		return "", errReadOnly
	}
	name, err := oneGroupName(nameText)
	if err != nil {
		return "", err
	}
	members, err := usersNamed(s, people)
	if err != nil {
		return "", err
	}
	if !slices.Contains(members, user.Name) {
		members = append(members, user.Name)
	}
	return name, changeSettings(user.Name, func(f *settingsfile.File) error {
		// Judged on the file as it is read for the change, not as it was a
		// moment ago: two who make the same group don't both get it.
		now := settingsOf(f.Text())
		if groupNameTaken(now, name, "") {
			return refuseAs(refusedExists, groupTakenMsg(name))
		}
		for _, m := range members {
			if now.Users[m] == nil {
				return refuse(`"` + m + `" is not a user of this wiki.`)
			}
		}
		setMembers(f, now, name, members)
		setOwners(f, name, []string{user.Name})
		return nil
	})
}

// groupPeopleOp sets who is in group by name, and its owners, for one who
// manages it (an owner, an admin); to anyone else there is no such group.
// Who is in it by way of a group it includes is not this field's. A group
// keeps an owner unless an admin takes the last out (it is an admin's
// then, as a group made on the host). Returns who is no longer in it.
func groupPeopleOp(user asker, group, people, owners string) (left []string, err error) {
	before := latestSettings()
	if !groupNameRE.MatchString(group) || !slices.Contains(knownGroups(before), group) || !before.managesGroup(user.Name, group) {
		return nil, errNotFound
	}
	members, err := usersNamed(before, people)
	if err != nil {
		return nil, err
	}
	own, err := usersNamed(before, owners)
	if err != nil {
		return nil, err
	}
	if len(own) == 0 && !before.isAdmin(user.Name) {
		return nil, refuse("A group keeps an owner: name who takes it over before you take yourself out.")
	}
	err = changeSettings(user.Name, func(f *settingsfile.File) error {
		now := settingsOf(f.Text())
		if !now.managesGroup(user.Name, group) {
			return errNotFound
		}
		setMembers(f, now, group, members)
		setOwners(f, group, own)
		return nil
	})
	if err != nil {
		return nil, err
	}
	return leftGroup(before, latestSettings(), group), nil
}

// mayLeave: user may take themselves out of group: its name is on them
// (who is in it by way of a group it includes leaves that one), it has
// owners (a group without is an admin's, and who is in it is the admin's
// to say), and they don't manage it (who does has Manage…, where a group
// keeps an owner). A viewer changes nothing.
func mayLeave(s *Settings, user asker, group string) bool {
	u := s.Users[user.Name]
	return u != nil && !u.Disabled && !user.ReadOnly && !s.isViewer(user.Name) && slices.Contains(u.Groups, group) &&
		len(s.Owners[group]) > 0 && !s.managesGroup(user.Name, group)
}

// leaveGroupOp takes user out of group, for themselves: the group's name
// goes from their groups, and with it what is the group's from their sight
// (to fewer, and only for the one who asks). Its owners put them back.
func leaveGroupOp(user asker, group string) error {
	if !groupNameRE.MatchString(group) || !mayLeave(latestSettings(), user, group) {
		return errNotFound
	}
	return changeSettings(user.Name, func(f *settingsfile.File) error {
		now := settingsOf(f.Text())
		if !mayLeave(now, user, group) {
			return errNotFound
		}
		setGroups(f, user.Name, slices.DeleteFunc(slices.Clone(now.Users[user.Name].Groups), func(g string) bool { return g == group }))
		return nil
	})
}

// renameGroupInFile gives the group old the name name wherever the settings
// file f (whose settings are s) names it: on the users, in the groups that
// include it, and its own section.
func renameGroupInFile(f *settingsfile.File, s *Settings, old, name string) {
	swap := func(groups []string) []string {
		out := slices.Clone(groups)
		for i, g := range out {
			if g == old {
				out[i] = name
			}
		}
		slices.Sort(out)
		return out
	}
	for _, u := range sortedKeys(s.Users) {
		if groups := s.Users[u].Groups; slices.Contains(groups, old) {
			setGroups(f, u, swap(groups))
		}
	}
	for _, g := range sortedKeys(s.Includes) {
		if in := s.Includes[g]; slices.Contains(in, old) {
			setIncludes(f, g, swap(in))
		}
	}
	f.RenameGroup(old, name)
	// The names it went by: its folder's history under them is its own.
	was := slices.DeleteFunc(append(slices.Clone(s.Was[old]), old), func(g string) bool { return g == name })
	slices.Sort(was)
	setGroupKey(f, name, "was", slices.Compact(was))
}

// renameGroupOp gives the group old the name nameText, for one who manages
// it: what is the group's by its name follows, so nobody loses anything.
// The lists that are for it are for the new name (one commit), its folder
// groups/<old>/ moves to groups/<name>/ with every link to its pages
// re-pointed and its files along (moveFolder's commit, and one for the
// files no page shows), and then the settings name it anew (the users, the
// groups that include it, its section). Until that last step nobody is in
// the new name's group, so what has moved is seen by no one for that
// moment, never by more. A step that fails puts the ones before it back;
// one that is made but for its commit counts as made, and the rename goes
// on to the settings: it returns the new name with that error, and the
// change is in the Uncommitted list. The caller holds pagesMu.
func renameGroupOp(user asker, old, nameText string) (string, error) {
	s := latestSettings()
	if !groupNameRE.MatchString(old) || !slices.Contains(knownGroups(s), old) || !s.managesGroup(user.Name, old) {
		return "", errNotFound
	}
	name, err := oneGroupName(nameText)
	if err != nil {
		return "", err
	}
	if name == old {
		return "", refuseAs(refusedUnchanged, "The group is called "+old+" already.")
	}
	if groupNameTaken(s, name, old) {
		return "", refuseAs(refusedExists, groupTakenMsg(name))
	}
	if err := groupMoveRefused(user, groupsDir+"/"+old, groupsDir+"/"+name); err != nil {
		return "", err
	}
	// The settings as they will be are good, before anything moves.
	f, err := readConfigFile(configPath())
	if err != nil || f == nil {
		return "", writeFailed("The settings file can't be read", errOr(err, os.ErrNotExist))
	}
	renameGroupInFile(f, settingsOf(f.Text()), old, name)
	parsed, err := parseConfig(f.Text())
	if err == nil {
		_, err = loadSettings(parsed)
	}
	if err != nil {
		return "", err
	}
	// A step that fails undoes the ones before it and stops. One that is
	// made but for its commit (writtenAnyway) is made: the rename goes on,
	// since stopping there would leave the group's lists or pages under a
	// name the settings don't know, seen by no one, and says so at the end.
	var uncommitted error
	if err := renameGroupInLists(user, old, name); err != nil {
		if !writtenAnyway(err) {
			return "", err
		}
		uncommitted = err
	}
	if err := moveGroupFolder(user, old, name); err != nil {
		if !writtenAnyway(err) {
			if back := renameGroupInLists(user, name, old); back != nil && !writtenAnyway(back) {
				logf("renaming the group %s to %s failed (%v), and its lists are for @%s still: %v", old, name, err, name, back)
			}
			return "", err
		}
		uncommitted = errOr(uncommitted, err)
	}
	err = changeSettings(user.Name, func(f *settingsfile.File) error {
		renameGroupInFile(f, settingsOf(f.Text()), old, name)
		return nil
	})
	if err != nil {
		if back := moveGroupFolder(user, name, old); back != nil && !writtenAnyway(back) {
			logf("renaming the group %s to %s failed (%v), and its pages are in %s/%s still: %v", old, name, err, groupsDir, name, back)
		}
		if back := renameGroupInLists(user, name, old); back != nil && !writtenAnyway(back) {
			logf("renaming the group %s to %s failed (%v), and its lists are for @%s still: %v", old, name, err, name, back)
		}
		return "", err
	}
	return name, uncommitted
}

// groupMoveRefused: why the pages in the folder from can't move to to
// (where they would be the same path below it) for user, who manages the
// group: one would land where safePath opens no page (a link on the way),
// or would become a guide there that wasn't one where it was (a page a
// shared guide pulls in: only an admin writes those, adminsOnly, as every
// user's assistant follows them). Who manages a group moves its pages
// whoever wrote them, when it is renamed or ends, so where they come from
// isn't asked, but where they land is, as a move asks it (checkTarget).
func groupMoveRefused(user asker, from, to string) error {
	for _, p := range sortedPages() {
		rest, ok := strings.CutPrefix(p.Slug, from+"/")
		if !ok {
			continue
		}
		n := to + "/" + rest
		switch {
		case safePath(n) == "":
			return refuse("Nothing was moved: " + n + " can't be a page (a folder on the way is a link, or a name the files can't have).")
		case adminsOnly(user, n) && !adminsOnly(user, p.Slug):
			return refuseAs(refusedReadOnly, "Nothing was moved: "+n+" would be a page a guide pulls in, which every user's assistant follows, "+
				"so only an admin of this wiki changes it. Choose another name, or ask an admin.")
		}
	}
	return nil
}

// groupKept: why the group can't be removed by user yet (nil: it can): a
// task list is for it (a list has its own owners and its own For: theirs to
// change or delete first), or another group includes it and user is no
// admin (what a group is made of is an admin's). The lists user doesn't see
// are counted, not named.
func groupKept(s *Settings, user asker, group string) error {
	var named []string
	hidden := 0
	for _, l := range taskFiles.ReadLists("").Lists {
		if !slices.Contains(l.For(), "@"+group) {
			continue
		}
		if taskFiles.SeesList(user, l.Name) {
			named = append(named, "#"+l.Name)
		} else {
			hidden++
		}
	}
	if len(named)+hidden > 0 {
		what := strings.Join(named, ", ")
		if hidden > 0 && what != "" {
			what += " and " + itoa(hidden) + " you don't see"
		} else if hidden > 0 {
			what = itoa(hidden) + " you don't see"
		}
		return refuseAs(refusedConflict, "A task list is for @"+group+" ("+what+"): its owners say whom it is for instead, or delete it, first.")
	}
	if s.isAdmin(user.Name) {
		return nil
	}
	for _, g := range sortedKeys(s.Includes) {
		if slices.Contains(s.Includes[g], group) {
			return refuseAs(refusedConflict, "The group "+g+" is made of "+group+" among others: an admin takes it out of "+g+" first, or removes it.")
		}
	}
	return nil
}

// removeGroupInFile takes the group name out of the settings file f (whose
// settings are s): off the users, out of the groups that include it, and
// its section.
func removeGroupInFile(f *settingsfile.File, s *Settings, name string) {
	without := func(groups []string) []string {
		return slices.DeleteFunc(slices.Clone(groups), func(g string) bool { return g == name })
	}
	for _, u := range sortedKeys(s.Users) {
		if groups := s.Users[u].Groups; slices.Contains(groups, name) {
			setGroups(f, u, without(groups))
		}
	}
	for _, g := range sortedKeys(s.Includes) {
		if in := s.Includes[g]; g != name && slices.Contains(in, name) {
			setIncludes(f, g, without(in))
		}
	}
	f.RemoveGroup(name)
}

// The answers to what becomes of a removed group's pages.
const (
	pagesMove   = "move"
	pagesDelete = "delete"
)

// removeGroupOp ends the group, for one who manages it. What is in its
// folder goes first, as pages says: **move** takes the pages to the folder
// folderText of the shared wiki (the group's name when empty: groups/dev/x
// is dev/x, its home dev/index), with every link re-pointed and the files
// they show, so everyone who uses the wiki sees them; **delete** deletes
// the pages and their files (they stay in git). A folder with anything in
// it wants one of the two. Then the settings forget the group: off its
// people, out of the groups made of it, its section. In that order, so
// nothing is in a folder no group is named as while the group is still
// there; a step that is made but for its commit counts as made
// (writtenAnyway), and the removal goes on and says so. A list that is for
// the group keeps it (groupKept). Returns the folder the pages are in now
// ("" when none moved). The caller holds pagesMu.
func removeGroupOp(user asker, group, pages, folderText string) (folder string, err error) {
	s := latestSettings()
	if !groupNameRE.MatchString(group) || !slices.Contains(knownGroups(s), group) || !s.managesGroup(user.Name, group) {
		return "", errNotFound
	}
	if err := groupKept(s, user, group); err != nil {
		return "", err
	}
	from := groupsDir + "/" + group
	dir := filepath.Join(cfg.Data, groupsDir, group)
	var paths []string
	for _, p := range sortedPages() {
		if strings.HasPrefix(p.Slug, from+"/") {
			paths = append(paths, p.Path)
		}
	}
	holds := false // anything at all in the folder: pages, files
	_ = filepath.WalkDir(dir, func(p string, d fs.DirEntry, err error) error {
		if err == nil && !d.IsDir() {
			holds = true
		}
		return nil
	})
	var uncommitted error
	switch {
	case !holds:
	case pages == pagesMove:
		if folder = mdtext.ToSlug(strings.Trim(strings.TrimSpace(folderText), "/")); folder == "" {
			folder = group
		}
		switch {
		case safePath(folder+"/page") == "":
			return "", refuse(strconv.Quote(folder) + ": pages can't go in folders starting with '_' or '.'")
		case !placeOf(folder + "/page").shared():
			return "", refuse(strconv.Quote(folder) + " is no folder of the shared wiki: the pages go where everyone sees them")
		case exists(filepath.Join(cfg.Data, filepath.FromSlash(folder))):
			return "", refuseAs(refusedExists, "There is a folder "+strconv.Quote(folder)+" in the wiki already: give the pages' folder another name.")
		}
		if err := groupMoveRefused(user, from, folder); err != nil {
			return "", err
		}
		if len(paths) > 0 {
			if _, err := keepPendingPages(paths, from+"/"); err != nil {
				return "", err
			}
			if _, _, err := moveFolder(user, from, folder, "Remove the group "+group+": "+from+"/ is "+folder+"/"); err != nil {
				if _, linksOnly := err.(*linksLeft); !linksOnly && !writtenAnyway(err) {
					return "", err // nothing moved
				}
				uncommitted = err
				logf("removing the group %s: %s", group, errDetail(err))
			}
		}
		// What no page shows goes with the folder (it stays in git).
		if err := removeGroupFolder(user, group, "the files no page showed"); err != nil {
			uncommitted = errOr(uncommitted, err)
		}
	case pages == pagesDelete:
		if _, err := keepPendingPages(paths, from+"/"); err != nil {
			return "", err
		}
		if err := removeGroupFolder(user, group, "its pages"); err != nil {
			if !writtenAnyway(err) {
				return "", err
			}
			uncommitted = err
		}
	default:
		return "", refuse("The group has pages: say what becomes of them, move them to the wiki or delete them.")
	}
	if err := changeSettings(user.Name, func(f *settingsfile.File) error {
		now := settingsOf(f.Text())
		if !now.managesGroup(user.Name, group) {
			return errNotFound
		}
		removeGroupInFile(f, now, group)
		return nil
	}); err != nil {
		return folder, err
	}
	return folder, uncommitted
}

// removeGroupFolder deletes what is left of the group's folder (pages,
// files), in one commit that says what.
func removeGroupFolder(user asker, group, what string) error {
	dir := filepath.Join(cfg.Data, groupsDir, group)
	if !isDir(dir) {
		return nil
	}
	if err := os.RemoveAll(dir); err != nil {
		return err
	}
	forgetUsage()
	return notCommitted(gitCommit(user.Name, "Remove the group "+group+": "+what, dir))
}

// groupRemoveRoute: POST /groups/{group}/remove: the group ends, and its
// pages go to the wiki (pages=move, folder) or are deleted (pages=delete)
// (removeGroupOp).
func groupRemoveRoute(w http.ResponseWriter, r *http.Request) {
	if noGroupsHere(w, r) {
		return
	}
	group := r.PathValue("group")
	folder, err := removeGroupOp(userOf(r), group, r.PostFormValue("pages"), r.PostFormValue("folder"))
	pages := ""
	if folder == "" && r.PostFormValue("pages") == pagesDelete {
		pages = pagesDelete
	}
	if err != nil && writtenAnyway(err) {
		// Removed, but not all of it is committed: the page says both.
		showGroups(w, r, http.StatusInternalServerError, groupRemoved(group, folder, pages), err.Error(), "", "")
		return
	}
	if err != nil {
		refuseGroups(w, r, err, "", "")
		return
	}
	http.Redirect(w, r, "/groups?done=removed&group="+url.QueryEscape(group)+"&folder="+url.QueryEscape(folder)+"&pages="+pages, http.StatusSeeOther)
}

// groupRemoved: what a removal did, from names that are a group's and a
// folder's (a link can't make the page say anything else): "" unless the
// group is gone, and the folder named is one of the wiki's.
func groupRemoved(group, folder, pages string) string {
	if !groupNameRE.MatchString(group) || slices.Contains(knownGroups(latestSettings()), group) {
		return ""
	}
	switch {
	case folder != "" && folder == mdtext.ToSlug(folder) && placeOf(folder+"/page").shared() && isDir(filepath.Join(cfg.Data, filepath.FromSlash(folder))):
		return "Removed the group " + group + ": its pages are in " + folder + "/ now, for everyone who uses the wiki."
	case pages == pagesDelete:
		return "Removed the group " + group + ", and deleted its pages (they are in git still, on the host)."
	}
	return "Removed the group " + group + "."
}

// errOr: err, or other when there is none.
func errOr(err, other error) error {
	if err != nil {
		return err
	}
	return other
}

// renameGroupInLists: the shared lists that are for @old are for @name,
// whoever sees them (the one who renames a group may be in none of them: it
// is the group's name that changes, not who a list is for).
func renameGroupInLists(user asker, old, name string) error {
	if err := mayWriteTasks(user); err != nil {
		return err
	}
	taskFiles.Lock()
	defer taskFiles.Unlock()
	reg, err := taskFiles.ReadListsStrict("")
	if err != nil {
		return err
	}
	changed := false
	for _, l := range reg.Lists {
		who := l.For()
		if i := slices.Index(who, "@"+old); i >= 0 {
			who[i] = "@" + name
			l.SetFor(who)
			changed = true
		}
	}
	if !changed {
		return nil
	}
	paths, err := taskFiles.WriteListsOf("", reg)
	if err != nil {
		return err
	}
	return commitLists(user, "The group "+old+" is called "+name+": the lists for it", paths)
}

// moveGroupFolder moves the folder of the group old to that of the group
// name: its pages with every link to them re-pointed and the files they
// show (moveFolder), then what else lies there (a file no page shows), and
// a home that still says what Zone wrote says the new name. Nothing to do
// for a group without a folder.
func moveGroupFolder(user asker, old, name string) error {
	folder, newFolder := groupsDir+"/"+old, groupsDir+"/"+name
	dir, newDir := filepath.Join(cfg.Data, groupsDir, old), filepath.Join(cfg.Data, groupsDir, name)
	if !isDir(dir) {
		return nil
	}
	var paths []string
	for _, p := range sortedPages() {
		if strings.HasPrefix(p.Slug, folder+"/") {
			paths = append(paths, p.Path)
		}
	}
	what := "The group " + old + " is called " + name
	var after error // the pages moved, but not all of it is committed
	if len(paths) > 0 {
		if _, err := keepPendingPages(paths, folder+"/"); err != nil {
			return err
		}
		if _, _, err := moveFolder(user, folder, newFolder, what+": "+folder+"/ is "+newFolder+"/"); err != nil {
			if _, linksOnly := err.(*linksLeft); !linksOnly && !writtenAnyway(err) {
				return err // nothing moved
			}
			// The pages moved: the rest of the folder follows them, and what
			// went wrong (a link, the commit) is said after.
			after = err
			logf("renaming the group %s to %s: %s", old, name, errDetail(err))
		}
	}
	// What no page shows stays with the group: the rest of the folder.
	var rest, dirs []string
	var moveErr error
	_ = filepath.WalkDir(dir, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if d.IsDir() {
			dirs = append(dirs, p)
			return nil
		}
		rel, _ := filepath.Rel(dir, p)
		to := filepath.Join(newDir, rel)
		if exists(to) {
			// It went along with a page that shows it (a copy, where another
			// page shows it too): the one left here goes. Another file by
			// that name stays where it is.
			was, err1 := os.ReadFile(p)
			is, err2 := os.ReadFile(to)
			if err1 == nil && err2 == nil && string(was) == string(is) && os.Remove(p) == nil {
				rest = append(rest, p)
			}
			return nil
		}
		if err := os.MkdirAll(filepath.Dir(to), 0o777); err == nil {
			err = os.Rename(p, to)
		}
		if err != nil && moveErr == nil {
			moveErr = err
		}
		if err == nil {
			rest = append(rest, p, to)
		}
		return nil
	})
	for i := len(dirs) - 1; i >= 0; i-- { // the deepest first
		_ = os.Remove(dirs[i]) // fails when not empty
	}
	if home := filepath.Join(newDir, "index.md"); exists(home) {
		if text, err := readText(home); err == nil && text == groupHomeText(old) {
			if err := writeText(home, groupHomeText(name)); err == nil {
				rest = append(rest, home)
			}
		}
	}
	if len(rest) > 0 {
		if err := notCommitted(gitCommit(user.Name, what+": the files in "+newFolder+"/", rest...)); err != nil {
			return err
		}
	}
	if writtenAnyway(after) {
		return after
	}
	if moveErr != nil {
		// The pages are in the new folder: the group has moved, but for a file.
		return &writeError{msg: "the group's pages are in " + newFolder + "/, but a file stayed in " + folder + "/", err: moveErr, written: true}
	}
	return nil
}

// noGroupsHere: the request is by no user who can use the wiki (a wiki
// without users has no groups): answered 404, as GET /groups is.
func noGroupsHere(w http.ResponseWriter, r *http.Request) bool {
	if u := latestSettings().Users[userOf(r).Name]; u == nil || u.Disabled {
		http.NotFound(w, r)
		return true
	}
	return false
}

// dropOwner takes user out of the owners of every group, in the settings
// file f: a user who is removed owns nothing, and whoever is added by that
// name later doesn't either.
func dropOwner(f *settingsfile.File, user string) {
	s := settingsOf(f.Text())
	for _, g := range sortedKeys(s.Owners) {
		if owners := s.Owners[g]; slices.Contains(owners, user) {
			setOwners(f, g, slices.DeleteFunc(slices.Clone(owners), func(o string) bool { return o == user }))
		}
	}
}

// newGroupRoute: POST /groups: a new group, name and people (makeGroupOp).
func newGroupRoute(w http.ResponseWriter, r *http.Request) {
	if noGroupsHere(w, r) {
		return
	}
	name, err := makeGroupOp(userOf(r), r.PostFormValue("name"), r.PostFormValue("people"))
	if err != nil {
		refuseGroups(w, r, err, r.PostFormValue("name"), r.PostFormValue("people"))
		return
	}
	http.Redirect(w, r, "/groups?done=made&group="+url.QueryEscape(name), http.StatusSeeOther)
}

// groupPeopleRoute: POST /groups/{group}/people: who is in the group by
// name, and its owners (groupPeopleOp).
func groupPeopleRoute(w http.ResponseWriter, r *http.Request) {
	if noGroupsHere(w, r) {
		return
	}
	group := r.PathValue("group")
	left, err := groupPeopleOp(userOf(r), group, r.PostFormValue("people"), r.PostFormValue("owners"))
	if err != nil {
		refuseGroups(w, r, err, "", "")
		return
	}
	http.Redirect(w, r, "/groups?done=people&group="+url.QueryEscape(group)+"&left="+url.QueryEscape(strings.Join(left, ",")),
		http.StatusSeeOther)
}

// groupLeaveRoute: POST /groups/{group}/leave: whoever asks is no longer
// in the group (leaveGroupOp).
func groupLeaveRoute(w http.ResponseWriter, r *http.Request) {
	if noGroupsHere(w, r) {
		return
	}
	group := r.PathValue("group")
	if err := leaveGroupOp(userOf(r), group); err != nil {
		refuseGroups(w, r, err, "", "")
		return
	}
	http.Redirect(w, r, "/groups?done=left&group="+url.QueryEscape(group), http.StatusSeeOther)
}

// groupIncludesRoute: POST /groups/includes, an admin's: the groups the
// group `group` is made of from now, as the field names them (dev, design;
// empty: none, the group is its name on users again), each a group there
// is. `group` may be a new name: a group made of groups. Who is in one of
// them is in it (Groups in docs/spec/users.md). The answer says who is in
// it now, and who no longer is.
func groupIncludesRoute(w http.ResponseWriter, r *http.Request) {
	group, err := oneGroupName(r.PostFormValue("group"))
	if err != nil {
		refuseGroups(w, r, err, "", "")
		return
	}
	before := latestSettings()
	// new: the form that makes a group of groups. It makes one, and never
	// changes what a group there is includes (Manage… does), nor takes a
	// name something goes by, as no new group does.
	if r.PostFormValue("new") == "1" {
		if slices.Contains(knownGroups(before), group) {
			refuseGroups(w, r, refuseAs(refusedExists, "There is a group "+group+" already. What it includes is under its Manage…."), "", "")
			return
		}
		if groupNameTaken(before, group, "") {
			refuseGroups(w, r, refuseAs(refusedExists, groupTakenMsg(group)), "", "")
			return
		}
	}
	includes, err := groupsNamed(r.PostFormValue("includes"), knownGroups(before))
	if err != nil {
		refuseGroups(w, r, err, "", "")
		return
	}
	if err := changeSettings(userOf(r).Name, func(f *settingsfile.File) error {
		setIncludes(f, group, includes)
		return nil
	}); err != nil {
		refuseGroups(w, r, err, "", "")
		return
	}
	// Who left it, by name.
	http.Redirect(w, r, "/groups?done=includes&group="+url.QueryEscape(group)+
		"&left="+url.QueryEscape(strings.Join(leftGroup(before, latestSettings(), group), ",")), http.StatusSeeOther)
}

// groupRenameRoute: POST /groups/{group}/rename: the group's new name
// (renameGroupOp).
func groupRenameRoute(w http.ResponseWriter, r *http.Request) {
	if noGroupsHere(w, r) {
		return
	}
	name, err := renameGroupOp(userOf(r), r.PathValue("group"), r.PostFormValue("name"))
	if err != nil && name != "" {
		// Renamed, but not all of it is committed: the page says both.
		showGroups(w, r, http.StatusInternalServerError, groupsDone("renamed", name, ""), err.Error(), "", "")
		return
	}
	if err != nil {
		refuseGroups(w, r, err, "", "")
		return
	}
	http.Redirect(w, r, "/groups?done=renamed&group="+url.QueryEscape(name), http.StatusSeeOther)
}
