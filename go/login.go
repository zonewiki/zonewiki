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
		var named *User // the user the form names, whose password it has
		ok, wait := loginThrottle.try(clientKey(r), loginAccount(r), func() bool {
			var right bool
			named, right = checkLogin(r)
			if !right {
				named = nil
			}
			return right && (named == nil || !named.Disabled)
		})
		switch {
		case wait > 0:
			errorText, status = waitMessage(wait), http.StatusTooManyRequests
		case ok:
			user := ""
			if named != nil {
				user = named.Name
			}
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
			if named != nil && named.Disabled {
				// Said only to whoever knows the password (the one check
				// of the try said so: a second would tell by its time
				// which names are disabled); still a failure, so it
				// guesses no faster.
				errorText = "Your account is disabled. Ask an admin of this wiki."
				if named.Pending {
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

// checkLogin: the user the login form names (nil without users, or for
// a name that is none) and whether its password is right, theirs or the
// wiki's (without users): one check whoever is named, disabled or not.
func checkLogin(r *http.Request) (named *User, right bool) {
	s := settings()
	if s.Users == nil {
		return nil, checkPassword(r.PostFormValue("password"), s.PasswordHash)
	}
	return authenticate(r.PostFormValue("username"), r.PostFormValue("password"))
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
