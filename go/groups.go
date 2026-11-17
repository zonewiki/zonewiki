// Groups in the browser (docs/spec/users.md, Groups): /groups lists the
// groups a user is in and lets whoever may write make one, of named people,
// without an admin: they are its owner (owners in its [group …] section),
// and its owners say who is in it and what it is called. Who is in a group
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
	"strings"
	"zonewiki/internal/settingsfile"
)

func groupsRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /groups", groupsRoute)
	mux.HandleFunc("POST /groups", newGroupRoute)
	mux.HandleFunc("POST /groups/{group}/people", groupPeopleRoute)
	mux.HandleFunc("POST /groups/{group}/rename", writesPages(groupRenameRoute))
}

// namedMost: up to how many people a group is said by their names; a
// larger one by their number ("14 people").
const namedMost = 8

// groupsView: /groups (groups.html): the groups the user is in or owns.
type groupsView struct {
	view
	BreadcrumbHTML template.HTML
	Groups         []groupLink
	MayMake        bool     // the user may write: the form that makes a group
	People         []string // the wiki's users, for the fields to complete from
	Done, Error    string   // what the last change did, or why it was refused
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
	Member   bool // the user is in it (an owner may be none: no way to its pages then)
	Owners   []string
	Manages  bool
	// The fields: who is in it by name (not by way of a group it includes),
	// and its owners.
	PeopleText, OwnersText string
}

// groupsRoute: GET /groups: the groups the signed-in user is in, and those
// they own, each with who is in it; for who manages one the fields that set
// its people, its owners and its name, and for who may write the form that
// makes one. 404 without users (and for a visitor, who is no user).
func groupsRoute(w http.ResponseWriter, r *http.Request) {
	if u := latestSettings().Users[userOf(r)]; u == nil || u.Disabled {
		http.NotFound(w, r)
		return
	}
	q := r.URL.Query()
	showGroups(w, r, http.StatusOK, groupsDone(q.Get("done"), q.Get("group"), q.Get("left")), "", "", "")
}

// groupsDone: what the change before did, from a fixed set and names that
// are a group's and users' (a link can't make the page say anything else).
func groupsDone(done, group, left string) string {
	s := latestSettings()
	if !groupNameRE.MatchString(group) || !slices.Contains(s.groups(), group) {
		return ""
	}
	switch done {
	case "made":
		return "Made the group " + group + ": its pages go in " + groupsDir + "/" + group + "/, and a task list can be for @" + group + "."
	case "renamed":
		return "The group is called " + group + " now: its pages are in " + groupsDir + "/" + group + "/, and what was for it is for @" + group + "."
	case "people":
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
	v := &groupsView{BreadcrumbHTML: breadcrumb(rq, []crumb{{"Groups", ""}}, "/groups", true, "Groups", ""),
		MayMake: mayMakeGroup(s, user), Done: done, Error: refused, NewName: newName, NewPeople: newPeople}
	var mine []string
	if u := s.Users[user]; u != nil && !u.Disabled {
		mine = slices.Clone(u.In)
	}
	for _, g := range sortedKeys(s.Owners) {
		if s.ownerOf(user, g) && !slices.Contains(mine, g) {
			mine = append(mine, g)
		}
	}
	slices.Sort(mine)
	for _, g := range mine {
		who := s.groupMembers(g)
		link := groupLink{Name: g, Count: len(who), Includes: s.Includes[g], Owners: s.Owners[g],
			Member: s.memberOf(user, g), Manages: s.managesGroup(user, g)}
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
			link.PeopleText, link.OwnersText = strings.Join(s.directMembers(g), ", "), strings.Join(s.Owners[g], ", ")
		}
		v.Groups = append(v.Groups, link)
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
func mayMakeGroup(s *Settings, user string) bool {
	u := s.Users[user]
	return u != nil && !u.Disabled && !s.isViewer(user)
}

// groupNameTaken: something goes by the group name already, so a new group
// (or one renamed) can't take it: a group there is (someone is in it, or it
// has a section), a name a group is said to include, a folder in groups/
// (whose pages would be the new group's: a group nobody is in keeps its
// folder), or a task list that is for it (which would be the new group's
// too). The names of the groups are no secret among a wiki's users (a
// list's Who it is for… completes from them), so saying "taken" tells
// nothing new.
func groupNameTaken(s *Settings, name string) bool {
	if slices.Contains(s.groups(), name) {
		return true
	}
	for _, in := range s.Includes {
		if slices.Contains(in, name) {
			return true
		}
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
	return "The name " + name + " is taken (by a group, a folder in " + groupsDir + "/ or a task list that is for it). Choose another."
}

// oneGroupName: the one group's name text names, or why it is none.
func oneGroupName(text string) (string, error) {
	named, bad := parseGroups(text)
	if bad != "" || len(named) != 1 {
		return "", refuse(`"` + strings.TrimSpace(text) + `" isn't a group's name: lowercase letters, digits and hyphens, at most 40.`)
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
func makeGroupOp(user, nameText, people string) (string, error) {
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
	if !slices.Contains(members, user) {
		members = append(members, user)
	}
	return name, changeSettings(user, func(f *settingsfile.File) error {
		// Judged on the file as it is read for the change, not as it was a
		// moment ago: two who make the same group don't both get it.
		now := settingsOf(f.Text())
		if groupNameTaken(now, name) {
			return refuseAs(refusedExists, groupTakenMsg(name))
		}
		for _, m := range members {
			if now.Users[m] == nil {
				return refuse(`"` + m + `" is not a user of this wiki.`)
			}
		}
		setMembers(f, now, name, members)
		setOwners(f, name, []string{user})
		return nil
	})
}

// groupPeopleOp sets who is in group by name, and its owners, for one who
// manages it (an owner, an admin); to anyone else there is no such group.
// Who is in it by way of a group it includes is not this field's. A group
// keeps an owner unless an admin takes the last out (it is an admin's
// then, as a group made on the host). Returns who is no longer in it.
func groupPeopleOp(user, group, people, owners string) (left []string, err error) {
	before := latestSettings()
	if !groupNameRE.MatchString(group) || !slices.Contains(before.groups(), group) || !before.managesGroup(user, group) {
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
	if len(own) == 0 && !before.isAdmin(user) {
		return nil, refuse("A group keeps an owner: name who takes it over before you take yourself out.")
	}
	err = changeSettings(user, func(f *settingsfile.File) error {
		now := settingsOf(f.Text())
		if !now.managesGroup(user, group) {
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
}

// renameGroupOp gives the group old the name nameText, for one who manages
// it: what is the group's by its name follows, so nobody loses anything.
// The lists that are for it are for the new name (one commit), its folder
// groups/<old>/ moves to groups/<name>/ with every link to its pages
// re-pointed and its files along (moveFolder's commit, and one for the
// files no page shows), and then the settings name it anew (the users, the
// groups that include it, its section). Until that last step nobody is in
// the new name's group, so what has moved is seen by no one for that
// moment, never by more. A step that fails puts the ones before it back.
// The caller holds pagesMu.
func renameGroupOp(user, old, nameText string) (string, error) {
	s := latestSettings()
	if !groupNameRE.MatchString(old) || !slices.Contains(s.groups(), old) || !s.managesGroup(user, old) {
		return "", errNotFound
	}
	name, err := oneGroupName(nameText)
	if err != nil {
		return "", err
	}
	if name == old {
		return "", refuseAs(refusedUnchanged, "The group is called "+old+" already.")
	}
	if groupNameTaken(s, name) {
		return "", refuseAs(refusedExists, groupTakenMsg(name))
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
	if err := renameGroupInLists(user, old, name); err != nil {
		return "", err
	}
	if err := moveGroupFolder(user, old, name); err != nil {
		if back := renameGroupInLists(user, name, old); back != nil {
			logf("renaming the group %s to %s failed (%v), and its lists are for @%s still: %v", old, name, err, name, back)
		}
		return "", err
	}
	err = changeSettings(user, func(f *settingsfile.File) error {
		renameGroupInFile(f, settingsOf(f.Text()), old, name)
		return nil
	})
	if err != nil {
		if back := moveGroupFolder(user, name, old); back != nil {
			logf("renaming the group %s to %s failed (%v), and its pages are in %s/%s still: %v", old, name, err, groupsDir, name, back)
		}
		if back := renameGroupInLists(user, name, old); back != nil {
			logf("renaming the group %s to %s failed (%v), and its lists are for @%s still: %v", old, name, err, name, back)
		}
		return "", err
	}
	return name, nil
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
func renameGroupInLists(user, old, name string) error {
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
func moveGroupFolder(user, old, name string) error {
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
	if len(paths) > 0 {
		if _, err := keepPendingPages(paths, folder+"/"); err != nil {
			return err
		}
		if _, _, err := moveFolder(user, folder, newFolder, what+": "+folder+"/ is "+newFolder+"/"); err != nil {
			if _, linksOnly := err.(*linksLeft); !linksOnly {
				return err
			}
			logf("renaming the group %s to %s: %v", old, name, err)
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
		if err := notCommitted(gitCommit(user, what+": the files in "+newFolder+"/", rest...)); err != nil {
			return err
		}
	}
	return moveErr
}

// noGroupsHere: the request is by no user who can use the wiki (a wiki
// without users has no groups): answered 404, as GET /groups is.
func noGroupsHere(w http.ResponseWriter, r *http.Request) bool {
	if u := latestSettings().Users[userOf(r)]; u == nil || u.Disabled {
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

// groupRenameRoute: POST /groups/{group}/rename: the group's new name
// (renameGroupOp). from=admin goes back to the admin page, where an admin
// renames a group they are not in.
func groupRenameRoute(w http.ResponseWriter, r *http.Request) {
	if noGroupsHere(w, r) {
		return
	}
	name, err := renameGroupOp(userOf(r), r.PathValue("group"), r.PostFormValue("name"))
	if err != nil {
		if r.PostFormValue("from") == "admin" && settings().isAdmin(userOf(r)) {
			if ref, ok := refusalOf(err); !ok || ref.kind != refusedNotFound {
				refuseChange(w, r, err)
				return
			}
		}
		refuseGroups(w, r, err, "", "")
		return
	}
	to := "/groups?done=renamed&group=" + url.QueryEscape(name)
	if r.PostFormValue("from") == "admin" && settings().isAdmin(userOf(r)) {
		to = "/admin/users?done=renamed&group=" + url.QueryEscape(name)
	}
	http.Redirect(w, r, to, http.StatusSeeOther)
}
