// The login gate's routes: /login and /logout.
package main

import (
	"fmt"
	"net/http"
	"os"
	"strings"
)

// isLocalPath: url is a same-origin path. "//host" and "/\host" start with
// "/" but browsers resolve them to another origin, so they don't count.
func isLocalPath(u string) bool {
	return strings.HasPrefix(u, "/") && !strings.HasPrefix(u, "//") && !strings.HasPrefix(u, "/\\")
}

func login(w http.ResponseWriter, r *http.Request) {
	s := settings()
	if !s.authOn() {
		http.Redirect(w, r, "/", http.StatusFound)
		return
	}
	errorText, status := "", http.StatusOK
	if r.Method == "POST" {
		// Refused unchecked while the client must wait, even the right
		// password: guessing doesn't go on in the meantime.
		user := ""
		ok, wait := loginThrottle.try(clientKey(r), loginAccount(r), func() (ok bool) {
			user, ok = checkLogin(r)
			return ok
		})
		switch {
		case wait > 0:
			errorText, status = waitMessage(wait), http.StatusTooManyRequests
		case ok:
			setSession(w, r, user)
			next := r.URL.Query().Get("next")
			if !r.URL.Query().Has("next") || !isLocalPath(next) {
				next = "/"
			}
			http.Redirect(w, r, next, http.StatusFound)
			return
		default:
			errorText = "Incorrect password."
			if s.Users != nil {
				errorText = "Incorrect user name or password."
			}
			if u := disabledLogin(r); s.Users != nil && u != nil {
				// Said only to whoever knows the password; still a failure,
				// so it guesses no faster.
				errorText = "Your account is disabled. Ask an admin of this wiki."
				if u.Pending {
					errorText = "Your account waits for an admin of this wiki to approve it."
				}
			}
		}
	}
	page(w, newReq(r), status, "login.html", "Sign in", shell{noActions: true, badgeInForm: true}, &loginView{
		Error: errorText, NextURL: r.URL.Query().Get("next"), Username: r.PostFormValue("username"),
		Signup: signupOn(s)})
}

// loginView: the login form (login.html); the shell says whether it asks
// for a user name.
type loginView struct {
	view
	Error, NextURL, Username string
	Signup                   bool // people may sign up: a link to /signup
}

// checkLogin: the login form's password is right, for the user it names
// (with users) or the wiki's password (without).
func checkLogin(r *http.Request) (user string, ok bool) {
	s := settings()
	if s.Users == nil {
		return "", checkPassword(r.PostFormValue("password"), s.PasswordHash)
	}
	if u := authenticate(r.PostFormValue("username"), r.PostFormValue("password")); u != nil && !u.Disabled {
		return u.Name, true
	}
	return "", false
}

// loginAccount: whose password the login form tries, for the throttle: the
// user it names (with users), as a name is compared; "?" for what is no
// user's name, so made-up ones count together; "" for the wiki's password.
func loginAccount(r *http.Request) string {
	if settings().Users == nil {
		return ""
	}
	name := strings.ToLower(strings.TrimSpace(r.PostFormValue("username")))
	if len(name) > 64 || !userNameRE.MatchString(name) {
		return "?"
	}
	return name
}

// disabledLogin: the disabled user the login form names, with their
// password; nil if it names none.
func disabledLogin(r *http.Request) *User {
	u := settings().Users[strings.ToLower(strings.TrimSpace(r.PostFormValue("username")))]
	if u != nil && u.Disabled && checkPassword(r.PostFormValue("password"), u.PasswordHash) {
		return u
	}
	return nil
}

// logout ends the session, not only this browser's cookie of it: a copy
// of the cookie taken before stops working too (signout.go).
func logout(w http.ResponseWriter, r *http.Request) {
	if s, ok := readSession(r); ok {
		if err := signOut(s.ID); err != nil {
			fmt.Fprintln(os.Stderr, "Zone: signing out:", err)
		}
	}
	clearSession(w, r)
	http.Redirect(w, r, "/login", http.StatusFound)
}
