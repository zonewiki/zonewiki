// Invite and reset links (docs/spec/users.md, Invite and reset links): a
// link that lets one person set a password, once. An invite adds a new
// user with the role the admin chose; a reset sets an existing user's new
// password. Zone doesn't mail these: the admin copies the link and sends it any
// way they like. Zone keeps only the token's hash, in .zone/links (owner
// only, not committed: it is state, like the session key), with whom it is
// for, what it does, who made it and when it expires.
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"slices"
	"sort"
	"strings"
	"sync"
	"time"
	"unicode/utf8"
	"zonewiki/internal/settingsfile"
)

// How long a link works.
const (
	inviteLifetime = 7 * 24 * time.Hour
	resetLifetime  = 24 * time.Hour
)

// welcomeLink: an invite or reset link as .zone/links keeps it.
type welcomeLink struct {
	Hash    string    `json:"hash"`            // hashToken of the link's token
	User    string    `json:"user"`            // "" for a sign-up: they choose it
	Kind    string    `json:"kind"`            // "invite", "reset" or "signup" (signup.go)
	Email   string    `json:"email,omitempty"` // a sign-up's address
	Role    string    `json:"role,omitempty"`
	By      string    `json:"by,omitempty"`
	Expires time.Time `json:"expires"`
}

func linksPath() string { return statePath("links") }

// readLinks: the links that still work, oldest first.
func readLinks() ([]welcomeLink, error) {
	b, err := os.ReadFile(linksPath())
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var all []welcomeLink
	if err := json.Unmarshal(b, &all); err != nil {
		return nil, fmt.Errorf("%s: %w", filepath.Base(stateDirIn(cfg.Data))+"/links", err)
	}
	return slices.DeleteFunc(all, func(l welcomeLink) bool { return !now().Before(l.Expires) }), nil
}

// linksMu with the file lock: one change of the links at a time, the
// server's and a command's.
var linksMu sync.Mutex

// changeLinks reads the links, lets change edit them, and writes them back
// whole (expired ones gone), owner-only.
func changeLinks(change func(links []welcomeLink) ([]welcomeLink, error)) error {
	linksMu.Lock()
	defer linksMu.Unlock()
	if err := os.MkdirAll(filepath.Dir(linksPath()), 0o777); err != nil {
		return err
	}
	unlock, err := lockFile(linksPath() + ".lock")
	if err != nil {
		return err
	}
	defer unlock()
	links, err := readLinks()
	if err != nil {
		return err
	}
	if links, err = change(links); err != nil {
		return err
	}
	if links == nil {
		links = []welcomeLink{}
	}
	b, _ := json.MarshalIndent(links, "", "  ")
	return settingsfile.Write(linksPath(), string(b)+"\n")
}

// linkFor: the open link for name, nil if there is none.
func linkFor(links []welcomeLink, name string) *welcomeLink {
	for i := range links {
		if links[i].User == name {
			return &links[i]
		}
	}
	return nil
}

// makeLink makes a link for name (an invite with its role, or a reset) by
// the admin by, replacing an open one for name; it returns the token, to
// show once. An invite is for a name no user has, a reset for a user there.
func makeLink(s *Settings, kind, name, role, by string) (string, error) {
	switch {
	case !userNameRE.MatchString(name):
		return "", fmt.Errorf("%q isn't a user name. Use lowercase letters, digits and hyphens (e.g. alice, mary-ann).", name)
	case s.Users == nil:
		return "", errors.New("this wiki has no users: add the first one on the host (zonewiki user add NAME).")
	case kind == "invite" && s.Users[name] != nil:
		return "", fmt.Errorf("%s is a user already. For a new password, make them a reset link.", name)
	case kind == "invite" && !slices.Contains(roles, role):
		return "", fmt.Errorf("%q isn't a role. Use admin, editor or viewer.", role)
	case kind == "reset" && s.Users[name] == nil:
		return "", fmt.Errorf("there is no user %s.", name)
	}
	token := newToken()
	lifetime := inviteLifetime
	if kind == "reset" {
		lifetime, role = resetLifetime, ""
	}
	err := changeLinks(func(links []welcomeLink) ([]welcomeLink, error) {
		links = slices.DeleteFunc(links, func(l welcomeLink) bool { return l.User == name })
		return append(links, welcomeLink{Hash: hashToken(token), User: name, Kind: kind, Role: role, By: by,
			Expires: now().Add(lifetime).UTC().Truncate(time.Second)}), nil
	})
	return token, err
}

// revokeLink deletes name's open link; false if there was none.
func revokeLink(name string) (found bool, err error) {
	err = changeLinks(func(links []welcomeLink) ([]welcomeLink, error) {
		n := len(links)
		links = slices.DeleteFunc(links, func(l welcomeLink) bool { return l.User == name })
		found = len(links) < n
		return links, nil
	})
	return found, err
}

// findLink: the open link token opens, nil if none does (wrong, used or
// expired: the same).
func findLink(token string) *welcomeLink {
	if token == "" {
		return nil
	}
	links, err := readLinks()
	if err != nil {
		logf("%v", err)
		return nil
	}
	for i := range links {
		if tokenMatches(token, links[i].Hash) {
			return &links[i]
		}
	}
	return nil
}

// linkURL: the link as the admin copies it: on base (the base-url
// setting), or the address the admin's request came in on (r nil: the
// path alone).
func linkURL(base string, r *http.Request, token string) string {
	return siteURL(base, r, "/welcome/"+token)
}

// A stolen admin session could fill the list with links: at most
// linksPerHour per admin in an hour. In memory: a restart forgets.
const linksPerHour = 10

var linksMade = newHourlyCap(linksPerHour)

// mayMakeLink counts a link by admin; false when they made linksPerHour
// in the last hour.
func mayMakeLink(admin string) bool { return linksMade.take(admin) }

// linkRow: an open link as the Users tab lists it.
type linkRow struct {
	User, Kind, Role, By, Expires string
}

// openLinks: the open links an admin made, for the Users tab, by name
// (sign-ups' are no one's to revoke: they expire within a day).
func openLinks() []linkRow {
	links, err := readLinks()
	if err != nil {
		logf("%v", err)
	}
	links = slices.DeleteFunc(links, func(l welcomeLink) bool { return l.Kind == "signup" })
	sort.Slice(links, func(i, j int) bool { return links[i].User < links[j].User })
	var rows []linkRow
	for _, l := range links {
		rows = append(rows, linkRow{User: l.User, Kind: l.Kind, Role: l.Role, By: l.By,
			Expires: l.Expires.Local().Format("2 Jan 2006 15:04")})
	}
	return rows
}

// The Users tab's link routes (admins only: adminOnly).

// inviteRoute: POST /admin/users/invite, name and role: the Users tab with the
// link, shown once.
func inviteRoute(w http.ResponseWriter, r *http.Request) {
	linkRoute(w, r, "invite", r.PostFormValue("name"), r.PostFormValue("role"))
}

// resetRoute: POST /admin/users/{name}/reset.
func resetRoute(w http.ResponseWriter, r *http.Request) {
	if targetUser(w, r) == nil {
		return
	}
	linkRoute(w, r, "reset", r.PathValue("name"), "")
}

func linkRoute(w http.ResponseWriter, r *http.Request, kind, name, role string) {
	if !mayMakeLink(userOf(r)) {
		showUsers(w, r, http.StatusTooManyRequests, "", fmt.Sprintf("You made %d links in the last hour: "+
			"try again later.", linksPerHour))
		return
	}
	token, err := makeLink(latestSettings(), kind, name, role, userOf(r))
	if err != nil {
		showUsers(w, r, http.StatusConflict, "", "not made: "+err.Error())
		return
	}
	v := usersData(r)
	v.Link, v.LinkUser, v.LinkKind = linkURL(settings().BaseURL, r, token), name, kind
	page(w, newReq(r), http.StatusOK, "users.html", "Admin · Users", shell{}, v)
}

// revokeRoute: POST /admin/users/{name}/revoke, name's open link.
func revokeRoute(w http.ResponseWriter, r *http.Request) {
	name := r.PathValue("name")
	found, err := revokeLink(name)
	if err != nil {
		serverError(w, r, err)
		return
	}
	if !found {
		http.NotFound(w, r)
		return
	}
	http.Redirect(w, r, "/admin/users?done=revoke&user="+name, http.StatusSeeOther)
}

// The welcome page: /welcome/<token>, signed out. Opening it changes
// nothing (mail scanners and chat previews open links); only its form
// uses the link.

// welcomeThrottle: a client trying links that don't work is slowed down
// as one guessing passwords.
var welcomeThrottle = newThrottle()

// welcomeView: the welcome page (welcome.html).
type welcomeView struct {
	view
	User, Kind, Error string
	Email             string // a sign-up's address
	Dead              bool   // the link doesn't work
	Waiting           bool   // a sign-up done, waiting for an admin's approval
}

func welcomeRoute(w http.ResponseWriter, r *http.Request) {
	// The address holds the token: no other site is told it (every page's
	// Referrer-Policy: same-origin, contentPolicy). Not no-referrer: under
	// it a browser sends the form with "Origin: null", which the Origin
	// check refuses, and the link wouldn't work.
	rq, key := newReq(r), clientKey(r)
	dead := func(status int, msg string) {
		page(w, rq, status, "welcome.html", "Link", shell{noActions: true}, &welcomeView{Dead: true, Error: msg})
	}
	var link *welcomeLink
	ok, wait := welcomeThrottle.try(key, func() bool {
		link = findLink(r.PathValue("token"))
		return link != nil && settings().Users != nil && (link.Kind != "signup" || signupOn(settings()))
	})
	if wait > 0 {
		dead(http.StatusTooManyRequests, waitMessage(wait))
		return
	}
	if !ok {
		dead(http.StatusNotFound, "")
		return
	}
	v := &welcomeView{User: link.User, Kind: link.Kind, Email: link.Email}
	if r.Method == http.MethodPost {
		password := r.PostFormValue("password")
		if link.Kind == "signup" {
			v.User = strings.ToLower(strings.TrimSpace(r.PostFormValue("username")))
		}
		switch {
		case link.Kind == "signup" && v.User == "":
			v.Error = "Choose a user name."
		case utf8.RuneCountInString(password) < passwordMin:
			v.Error = "Choose a password of at least 8 characters."
		case password != r.PostFormValue("again"):
			v.Error = "The passwords differ."
		case link.Kind == "signup":
			hash, pending, err := useSignup(link, v.User, password)
			if err != nil {
				v.Error = err.Error()
				break
			}
			if pending {
				v.Waiting = true
				break
			}
			setSessionHash(w, r, v.User, hash)
			http.Redirect(w, r, "/", http.StatusSeeOther)
			return
		default:
			hash, err := useLink(link, password)
			if err != nil {
				v.Error = err.Error()
				break
			}
			setSessionHash(w, r, link.User, hash)
			http.Redirect(w, r, "/", http.StatusSeeOther)
			return
		}
	}
	title := "Welcome"
	switch link.Kind {
	case "reset":
		title = "New password"
	case "signup":
		title = "Sign up"
	}
	page(w, rq, http.StatusOK, "welcome.html", title, shell{noActions: true}, v)
}

// useLink: the link's change (the user added, or their new password) in
// the settings file (logged as the user's: they chose it), and
// the link gone; the password's hash, for the session.
func useLink(link *welcomeLink, password string) (string, error) {
	hash := hashPassword(password)
	err := changeSettings(link.User, func(f *settingsfile.File) error {
		exists := f.User(link.User) != nil
		switch {
		case link.Kind == "invite" && exists:
			return errors.New("there is a user " + link.User + " already. Ask whoever sent the link.")
		case link.Kind == "reset" && !exists:
			return errors.New("there is no user " + link.User + " any more. Ask whoever sent the link.")
		case len(f.Users) == 0:
			return errors.New("this wiki has no users any more. Ask whoever sent the link.")
		}
		f.Set(link.User, "password", hash)
		if link.Kind == "invite" && link.Role != "" && link.Role != roleEditor {
			f.Set(link.User, "role", link.Role)
		}
		return nil
	})
	if err != nil {
		return "", err
	}
	if _, err := revokeLink(link.User); err != nil {
		logf("removing %s's link: %v", link.User, err)
	}
	return hash, nil
}
