// Change password (/password, docs/spec/users.md): whoever is signed in
// sets a new password in the browser, as zonewiki user passwd (with users:
// their own) or zonewiki config passwd (without: the wiki's) does on the
// host. It takes the password they have: a session someone else got hold
// of mustn't be enough to take the account over. The new password ends the
// sessions of the old one (signedIn); this one goes on.
package main

import (
	"errors"
	"html/template"
	"net/http"
	"unicode/utf8"
	"zonewiki/internal/settingsfile"
)

func passwordRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /password", passwordPage)
	mux.HandleFunc("POST /password", passwordRoute)
}

// passwordView: the page (password.html).
type passwordView struct {
	view
	BreadcrumbHTML template.HTML
	User           string // whose password it is; "": the wiki's (no users)
	Shared         bool   // a shared account's: not changed here (sharedAccount)
	Done           bool   // it was just changed
	Error          string // why it wasn't
}

// passwordOwner: whose password the request's is about, as the latest
// settings have them: the user signed in, or with no users the wiki ("").
// ok false: there is no login, or the user is no longer there. shared: an
// account several people sign in to, whose password isn't changed here.
func passwordOwner(r *http.Request) (name, hash string, shared, ok bool) {
	s := latestSettings()
	if s.Users == nil {
		return "", s.PasswordHash, false, s.authOn()
	}
	u := s.Users[userOf(r).Name]
	if u == nil {
		return "", "", false, false
	}
	return u.Name, u.PasswordHash, u.Shared, true
}

// sharedPasswordMsg: why a shared account's password isn't changed in the
// browser.
func sharedPasswordMsg(name string) string {
	return name + " is an account several people sign in to: its password is set by whoever runs this wiki, not here."
}

func showPassword(w http.ResponseWriter, r *http.Request, status int, v *passwordView) {
	v.BreadcrumbHTML = breadcrumb(newReq(r), settingsCrumb(newReq(r), "Password"), "", false, "", "")
	title := "Change the password"
	if v.User != "" {
		title = "Change your password"
	}
	page(w, newReq(r), status, "password.html", title, shell{settings: "password"}, v)
}

// passwordPage: GET /password. ?done says it was changed (a link can make
// the page say only that).
func passwordPage(w http.ResponseWriter, r *http.Request) {
	name, _, shared, ok := passwordOwner(r)
	if !ok {
		http.NotFound(w, r)
		return
	}
	showPassword(w, r, http.StatusOK, &passwordView{User: name, Shared: shared, Done: !shared && r.URL.Query().Has("done")})
}

// passwordRoute: POST /password: the password they have (current), the new
// one twice (password, again). A wrong current one counts against the
// client like a wrong login, so the page guesses a password no faster than
// the login page does.
func passwordRoute(w http.ResponseWriter, r *http.Request) {
	name, old, shared, ok := passwordOwner(r)
	if !ok {
		http.NotFound(w, r)
		return
	}
	v := &passwordView{User: name, Shared: shared}
	if shared {
		// Before the password is looked at: nothing here tells a guess
		// from the right one, and nothing counts against the client.
		v.Error = sharedPasswordMsg(name)
		showPassword(w, r, http.StatusForbidden, v)
		return
	}
	right, wait := loginThrottle.try(clientKey(r), name, func() bool {
		return checkPassword(r.PostFormValue("current"), old)
	})
	if wait > 0 {
		v.Error = waitMessage(wait)
		showPassword(w, r, http.StatusTooManyRequests, v)
		return
	}
	password := r.PostFormValue("password")
	switch {
	case !right:
		v.Error = "Incorrect password: nothing was changed."
	case utf8.RuneCountInString(password) < passwordMin:
		v.Error = "Choose a password of at least 8 characters."
	case password != r.PostFormValue("again"):
		v.Error = "The new passwords differ."
	}
	if v.Error != "" {
		showPassword(w, r, http.StatusForbidden, v)
		return
	}
	hash := hashPassword(password)
	err := changeSettings(name, func(f *settingsfile.File) error {
		switch {
		case name != "" && f.User(name) == nil:
			return errors.New("there is no user " + name + " any more.")
		case name == "" && len(f.Users) > 0:
			return errors.New("this wiki has users now: sign in again.")
		case name != "" && truthy(f.User(name).Values["shared"].Value):
			return errors.New(sharedPasswordMsg(name)) // made shared meanwhile
		}
		f.Set(name, "password", hash)
		return nil
	})
	if err != nil {
		v.Error = err.Error()
		showPassword(w, r, http.StatusConflict, v)
		return
	}
	// This session goes on, as the same one (its id kept: signing out still
	// ends it everywhere), now holding the new password's stamp.
	if s, ok := readSession(r); ok {
		s.Pw = stampOf(hash)
		writeSession(w, r, s)
	}
	http.Redirect(w, r, "/password?done", http.StatusSeeOther)
}
