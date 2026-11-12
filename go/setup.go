// The first-run page (docs/spec/security.md, First run): a wiki other
// devices can reach, with no login yet, shows and changes nothing until
// someone sets one, in the browser. On a NAS the container is up and on the
// LAN before anyone has run a command; the page is what its owner meets
// first, instead of an open wiki they never notice. The login goes into the
// settings file as zonewiki config passwd (one password) or zonewiki user
// add (a first user, the admin) would write it.
package main

import (
	"errors"
	"net/http"
	"strings"
	"unicode/utf8"
	"zonewiki/internal/settingsfile"
)

// needsSetup: other devices reach Zone (ZONE_ALLOW_REMOTE, names in hosts,
// or a port compose published beyond localhost: Config.Published), it has
// no login, and that isn't on purpose (no-login = yes). On localhost alone
// Zone needs no login, as ever.
func needsSetup() bool {
	s := settings()
	return !s.authOn() && !s.NoLogin && (cfg.AllowRemote || cfg.Published || len(s.Hosts) > 0)
}

// setupFirstMsg: what anything but a page view is told while Zone waits
// for its first login.
const setupFirstMsg = "This wiki has no login yet: open it in a browser and set a password first."

// setupExempt: what answers while Zone waits for its first login: the
// page itself, the browser code it needs, the healthcheck, and /mcp, which
// has a token of its own or is off.
func setupExempt(r *http.Request) bool {
	switch p := r.URL.Path; {
	case p == "/setup", p == "/api/health", p == "/mcp":
		return true
	case strings.HasPrefix(p, "/public/"):
		return r.Method == "GET" || r.Method == "HEAD"
	}
	return false
}

// passwordMin: the fewest characters of a password set on the page.
const passwordMin = 8

// setupView: the first-run page (setup.html).
type setupView struct {
	view
	Username, Error string
	ConfigName      string
}

// setupRoute: GET /setup shows the form, POST /setup sets the login: a
// password for the wiki, or with a name the first user, an admin. Only
// while Zone has none (the settings file is checked again as it is
// written: of two people sending the form at once, one sets the login,
// the other is told to sign in).
func setupRoute(w http.ResponseWriter, r *http.Request) {
	if !needsSetup() {
		http.Redirect(w, r, "/", http.StatusFound)
		return
	}
	v := &setupView{ConfigName: configName()}
	if r.Method == http.MethodPost {
		name := strings.ToLower(strings.TrimSpace(r.PostFormValue("username")))
		password := r.PostFormValue("password")
		v.Username = name
		switch {
		case name != "" && !userNameRE.MatchString(name):
			v.Error = "A user name has lowercase letters, digits and hyphens (alice, mary-ann)."
		case utf8.RuneCountInString(password) < passwordMin:
			v.Error = "Choose a password of at least 8 characters."
		case password != r.PostFormValue("again"):
			v.Error = "The passwords differ."
		default:
			hash := hashPassword(password)
			err := changeSettings(name, func(f *settingsfile.File) error {
				if _, ok := f.Top["password"]; ok || len(f.Users) > 0 {
					return errHasLogin
				}
				if name == "" {
					f.Set("", "password", hash)
					return nil
				}
				f.Set(name, "password", hash)
				f.Set(name, "role", roleAdmin) // the first user manages the others
				f.Unset("", "mcp-token")       // with users each has their own
				return nil
			})
			if errors.Is(err, errHasLogin) {
				http.Redirect(w, r, "/login", http.StatusSeeOther)
				return
			}
			if err != nil {
				serverError(w, r, err)
				return
			}
			logf("the wiki's login was set in the browser (/setup), from %s", clientKey(r))
			setSessionHash(w, r, name, hash)
			http.Redirect(w, r, "/", http.StatusSeeOther)
			return
		}
	}
	status := http.StatusOK
	if v.Error != "" {
		status = http.StatusBadRequest
	}
	page(w, newReq(r), status, "setup.html", "Set a password", shell{noActions: true}, v)
}

// errHasLogin: the settings file has a login already (setupRoute).
var errHasLogin = errors.New("the wiki has a login already")
