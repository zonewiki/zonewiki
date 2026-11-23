// The admin page (/admin, docs/spec/users.md, Managing users): its
// section tabs, of which Users (/admin/users) is the one so far. There an
// admin sees the wiki's users and changes their roles, disables, enables and removes them
// in the browser, as zonewiki user does on the host (not themselves: an
// admin can't disable or remove their own account here). It changes the
// settings file the way the commands do (saveConfig: checked, locked, only
// onto the file as read), so the file stays the one place, and every rule
// of the commands holds here too (never back to no admin). An admin never
// sees another user's password or private pages: there is nothing here
// that shows or sets one.
package main

import (
	"html/template"
	"net/http"
	"net/url"
	"path/filepath"
	"slices"
	"strings"
	"sync/atomic"
	"time"
	"zonewiki/internal/settingsfile"
)

func adminRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /admin", adminOnly(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, adminTabs[0].Href, http.StatusFound) // the first tab
	}))
	mux.HandleFunc("GET /admin/users", usersPage)
	mux.HandleFunc("POST /admin/users/{name}/role", adminOnly(userRoleRoute))
	mux.HandleFunc("POST /admin/users/{name}/groups", adminOnly(userGroupsRoute))
	mux.HandleFunc("POST /admin/users/{name}/disable", adminOnly(userDisableRoute))
	mux.HandleFunc("POST /admin/users/{name}/enable", adminOnly(userDisableRoute))
	mux.HandleFunc("POST /admin/users/{name}/remove", adminOnly(userRemoveRoute))
	mux.HandleFunc("POST /admin/users/invite", adminOnly(inviteRoute))
	mux.HandleFunc("POST /admin/users/{name}/reset", adminOnly(resetRoute))
	mux.HandleFunc("POST /admin/users/{name}/revoke", adminOnly(revokeRoute))
	mux.HandleFunc("GET /welcome/{token}", welcomeRoute)
	mux.HandleFunc("POST /welcome/{token}", welcomeRoute)
}

// adminOnly: the route answers admins only; anyone else gets a 404, as for
// a page that isn't there.
func adminOnly(h http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if !settings().isAdmin(userOf(r).Name) {
			http.NotFound(w, r)
			return
		}
		h(w, r)
	}
}

// usersView: the Users tab (users.html).
type usersView struct {
	view
	Users []userRow
	Me    string
	Done  string // what the last change did ("" none), said above the list
	Error string // why the last change was refused
	Roles []string
	// Groups: every group there is, and the folders in groups/ no one sees,
	// for the Groups fields to choose from (they take nothing else).
	Groups []string
	// Stray: what lies in private/ that no one sees (the folder of a user
	// who is gone), by its path alone. Those in groups/ are on /groups.
	Stray []string
	Tabs  []adminTab // the admin page's section tabs
	Tab   string     // the one shown
	// The module switcher and Admin › Users, as on every view, so the top
	// row stays put; not in the navigation trail.
	BreadcrumbHTML template.HTML
	Links          []linkRow // the open invite and reset links
	// Sign-up, when it is on: the role it gives, the domains it takes,
	// whether an admin approves.
	Signup         bool
	SignupRole     string
	SignupDomains  []string
	SignupApproval bool
	// A link just made, shown once: its address, for whom, which kind.
	Link, LinkUser, LinkKind string
}

// userRow: a user as the Users tab lists them.
type userRow struct {
	Name, Role      string
	Email           string
	Groups          string // the groups their section names, "dev, hiring"
	Also            string // the groups they are in by way of those ("product"), "" none
	Token, Disabled bool
	Shared          bool // an account several people sign in to
	Pending         bool // signed up, waiting for approval
	Me, Private     bool // the admin themselves; has a private folder
	// Tasks: the open tasks still assigned to a user who can't be given
	// any (disabled, a viewer): to hand on.
	Tasks int
}

// adminTab: a section tab of the admin page.
type adminTab struct{ Key, Label, Href string }

// adminTabs: the admin page's sections, in order; /admin opens the first.
var adminTabs = []adminTab{{"users", "Users", "/admin/users"}}

// usersPage: GET /admin/users, the list. ?done= says what the change before did,
// from a fixed set (a link can't make the page say anything else).
func usersPage(w http.ResponseWriter, r *http.Request) {
	if !settings().isAdmin(userOf(r).Name) {
		http.NotFound(w, r)
		return
	}
	done := ""
	name := r.URL.Query().Get("user")
	if r.URL.Query().Get("done") == "revoke" && userNameRE.MatchString(name) {
		done = "Revoked the link for " + name + ": it no longer works."
	} else if u := latestSettings().Users[name]; u != nil {
		switch r.URL.Query().Get("done") {
		case "role":
			done = name + " is " + withArticle(u.Role) + " now."
		case "groups":
			if done = name + " is in no group now."; len(u.Groups) > 0 {
				done = name + " is in " + strings.Join(u.Groups, ", ") + " now."
			}
			// A group this left empty keeps its folder, which no one sees then.
			for _, p := range strayGroups() {
				if g := strings.TrimPrefix(p, groupsDir+"/"); g != p && groupNameRE.MatchString(g) && isDir(filepath.Join(cfg.Data, p)) {
					done += " Nobody is in " + g + " now: its pages stay in the wiki, in " + p + "/, seen by no one."
				}
			}
		case "disable":
			done = "Disabled " + name + ": they can't sign in, and their MCP token no longer works. Their files stay."
		case "enable":
			done = "Enabled " + name + ": they can sign in again."
		case "approve":
			done = "Approved " + name + ": they can sign in now."
		}
	} else if r.URL.Query().Get("done") == "remove" && userNameRE.MatchString(name) {
		done = "Removed " + name + "."
		if isDir(filepath.Join(cfg.Data, privateDir, name)) {
			done += " Their private pages and tasks stay in the wiki, in " + privateDir + "/" + name + "/, seen by no one."
		}
	}
	showUsers(w, r, http.StatusOK, done, "")
}

// showUsers renders the Users tab, with what was done or refused. It
// lists the users as the latest settings have them: a change made by this
// admin a moment ago may still wait for the requests running.
func showUsers(w http.ResponseWriter, r *http.Request, status int, done, refused string) {
	v := usersData(r)
	v.Done, v.Error = done, refused
	page(w, newReq(r), status, "users.html", "Admin · Users", shell{}, v)
}

// usersData: what the Users tab lists, the users and the open links.
func usersData(r *http.Request) *usersView {
	s, me := latestSettings(), userOf(r)
	v := &usersView{Me: me.Name, Roles: roles, Groups: groupChoices(s), Links: openLinks(), Tabs: adminTabs, Tab: "users",
		Signup: signupOn(s), SignupRole: s.SignupRole, SignupDomains: s.SignupDomains, SignupApproval: s.SignupApproval,
		BreadcrumbHTML: breadcrumb(newReq(r), []crumb{{"Admin", "/admin"}, {"Users", ""}}, "", false, "", "")}
	v.Stray = strayPrivate()
	assigned := assignedOpen()
	for _, name := range sortedKeys(s.Users) {
		u := s.Users[name]
		row := userRow{Name: name, Role: u.Role, Email: u.Email, Groups: strings.Join(u.Groups, ", "), Token: u.MCPToken != "",
			Also:     strings.Join(slices.DeleteFunc(slices.Clone(u.In), func(g string) bool { return slices.Contains(u.Groups, g) }), ", "),
			Disabled: u.Disabled, Pending: u.Pending, Shared: u.Shared,
			Me: name == me.Name, Private: isDir(filepath.Join(cfg.Data, privateDir, name))}
		if !s.assignable(name) {
			row.Tasks = assigned[name]
		}
		v.Users = append(v.Users, row)
	}
	return v
}

// settingsAuthor: who made the settings change being reloaded, so its
// line in the log names them (tookSettings); "" for a change by hand or a
// command.
var settingsAuthor atomic.Pointer[string]

// changeSettings applies edit to the settings file and saves it as the
// commands do (saveConfig), then has Zonewiki take it now instead of within
// the second (it takes effect once the requests running end, this one
// too). The log names user as who made it.
func changeSettings(user string, edit func(f *settingsfile.File) error) error {
	f, err := readConfigFile(configPath())
	if err != nil {
		return err
	}
	if f == nil {
		f = settingsfile.Empty()
	}
	if err := edit(f); err != nil {
		return err
	}
	if err := saveConfig(f); err != nil {
		return err
	}
	settingsAuthor.Store(&user)
	reloadMu.Lock()
	lastLook = time.Time{}
	reloadMu.Unlock()
	reloadSettings()
	return nil
}

// groupChoices: what a user's Groups field takes: every group there is,
// and the groups whose folder no one sees (putting someone in one gives
// the folder its people again).
func groupChoices(s *Settings) []string {
	out := knownGroups(s)
	for _, g := range strayGroupNames() {
		if !slices.Contains(out, g) {
			out = append(out, g)
		}
	}
	slices.Sort(out)
	return out
}

// targetUser: the user a POST /admin/users/{name}/… names, nil if there is none
// (the page answers 404, as for a page that isn't there).
func targetUser(w http.ResponseWriter, r *http.Request) *User {
	u := latestSettings().Users[r.PathValue("name")]
	if u == nil {
		http.NotFound(w, r)
	}
	return u
}

// afterChange: back to the list, saying what was done; to the wiki's home
// if the admin is an admin no longer (they demoted themselves), as the
// list is no longer theirs.
func afterChange(w http.ResponseWriter, r *http.Request, done, name string) {
	to := "/admin/users?done=" + url.QueryEscape(done) + "&user=" + url.QueryEscape(name)
	if !latestSettings().isAdmin(userOf(r).Name) {
		to = "/"
	}
	http.Redirect(w, r, to, http.StatusSeeOther)
}

// refuseChange: the change wasn't made; the list again, saying why.
func refuseChange(w http.ResponseWriter, r *http.Request, err error) {
	status := http.StatusConflict // what the settings file itself refuses (saveConfig): no refusal of a kind
	if ref, ok := refusalOf(err); ok {
		status = ref.kind.status()
	}
	showUsers(w, r, status, "", err.Error())
}

func userRoleRoute(w http.ResponseWriter, r *http.Request) {
	u := targetUser(w, r)
	if u == nil {
		return
	}
	role := r.PostFormValue("role")
	if !slices.Contains(roles, role) {
		badRequestPage(w)
		return
	}
	err := changeSettings(userOf(r).Name, func(f *settingsfile.File) error {
		setUserRole(f, u.Name, role)
		return nil
	})
	if err != nil {
		refuseChange(w, r, err)
		return
	}
	afterChange(w, r, "role", u.Name)
}

// userGroupsRoute: POST /admin/users/{name}/groups: the groups the user is
// in from now, as the field names them (groups = dev, hiring; empty: none):
// groups there are, or a folder in groups/ no one sees (groupChoices). A
// new group is made on /groups.
func userGroupsRoute(w http.ResponseWriter, r *http.Request) {
	u := targetUser(w, r)
	if u == nil {
		return
	}
	groups, err := groupsNamed(r.PostFormValue("groups"), groupChoices(latestSettings()))
	if err != nil {
		refuseChange(w, r, err)
		return
	}
	if err := changeSettings(userOf(r).Name, func(f *settingsfile.File) error {
		setGroups(f, u.Name, groups)
		return nil
	}); err != nil {
		refuseChange(w, r, err)
		return
	}
	afterChange(w, r, "groups", u.Name)
}

// userDisableRoute: POST /admin/users/{name}/disable or /enable.
func userDisableRoute(w http.ResponseWriter, r *http.Request) {
	u := targetUser(w, r)
	if u == nil {
		return
	}
	disable := filepath.Base(r.URL.Path) == "disable"
	if disable && u.Name == userOf(r).Name {
		refuseChange(w, r, refuse("not changed: you can't disable yourself here. Another admin can."))
		return
	}
	err := changeSettings(userOf(r).Name, func(f *settingsfile.File) error {
		setUserDisabled(f, u.Name, disable)
		return nil
	})
	if err != nil {
		refuseChange(w, r, err)
		return
	}
	done := filepath.Base(r.URL.Path)
	if !disable && u.Pending {
		done = "approve"
	}
	afterChange(w, r, done, u.Name)
}

func userRemoveRoute(w http.ResponseWriter, r *http.Request) {
	u := targetUser(w, r)
	if u == nil {
		return
	}
	if u.Name == userOf(r).Name {
		// Not here: a slip would lock the admin out, and whenever it would
		// be allowed (another admin left) that admin can do it. The last
		// user is always the admin themselves, so this also keeps the wiki
		// from going without a login, which is for the host on purpose.
		refuseChange(w, r, refuse("not changed: you can't remove yourself here. Another admin can, "+
			"or it's done on the host: zonewiki user remove "+u.Name+"."))
		return
	}
	err := changeSettings(userOf(r).Name, func(f *settingsfile.File) error {
		dropOwner(f, u.Name)
		f.RemoveUser(u.Name)
		return nil
	})
	if err != nil {
		refuseChange(w, r, err)
		return
	}
	if _, err := revokeLink(u.Name); err != nil { // a reset link of theirs
		logf("removing %s's link: %v", u.Name, err)
	}
	if err := noteRemoved(u.Name); err != nil { // no stranger signs up as them
		logf("noting %s removed: %v", u.Name, err)
	}
	revokeGrantsOf(u.Name) // their assistants' connections
	afterChange(w, r, "remove", u.Name)
}
