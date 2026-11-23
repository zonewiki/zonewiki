// The first-run page (docs/spec/security.md, First run): a wiki other
// devices can reach, with no login yet, shows and changes nothing until
// someone sets one, in the browser. On a NAS the container is up and on the
// LAN before anyone has run a command; the page is what its owner meets
// first, instead of an open wiki they never notice. The login goes into the
// settings file as zonewiki config passwd (one password) or zonewiki user
// add (a first user, the admin) would write it.
//
// The same form sets a login on purpose (Set a login in the menu): a wiki
// on localhost alone needs none, and whoever opens it may give it one
// (maySetLogin).
package main

import (
	"errors"
	"net/http"
	"strings"
	"unicode/utf8"
	"zonewiki/internal/settingsfile"
)

// needsSetup: other devices reach Zonewiki (ZONE_ALLOW_REMOTE, names in hosts,
// a port compose published beyond localhost, or Zonewiki started by hand on an
// address beyond loopback, as --addr 0.0.0.0:5080: Config.Published), it has
// no login, and that isn't on purpose (no-login = yes). On localhost alone
// Zonewiki needs no login, as ever.
func needsSetup() bool {
	s := settings()
	return !s.authOn() && !s.NoLogin && reachable(s)
}

// reachable: other devices reach Zonewiki (needsSetup's three ways).
func reachable(s *Settings) bool {
	return cfg.AllowRemote || cfg.Published || len(s.Hosts) > 0
}

// maySetLogin: the form answers: the wiki has no login, and either waits
// for one (needsSetup) or is on localhost alone, where whoever opens it
// reads and changes everything anyway and now may give it a login. Not a
// wiki open to the network on purpose (no-login = yes): there anyone on
// the network would lock everyone else out; its login is set on the host.
func maySetLogin() bool {
	s := settings()
	return !s.authOn() && (!reachable(s) || !s.NoLogin)
}

// setLoginHref: where the menu's Set a login leads in a wiki without a
// login: the form, or where it doesn't answer the manual's section on
// setting one on the host; "" with a login.
func setLoginHref() string {
	switch {
	case settings().authOn():
		return ""
	case maySetLogin():
		return "/setup"
	}
	return "/help/self-hosting#setting-a-login"
}

// setupFirstMsg: what anything but a page view is told while Zonewiki waits
// for its first login.
const setupFirstMsg = "This wiki has no login yet: open it in a browser and set a password first."

// setupExempt: what answers while Zonewiki waits for its first login: the
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

// passwordMin: the fewest characters of a password, set on a page or by a
// command (askPassword).
const passwordMin = 8

// setupView: the first-run page (setup.html).
type setupView struct {
	view
	Username, Error string
	ConfigName      string
	Asked           bool // Zonewiki waits for a login (needsSetup); false: set on purpose, from the menu
	HasToken        bool // the wiki has an MCP token, which a first user ends
}

// setupRoute: GET /setup shows the form, POST /setup sets the login: a
// password for the wiki, or with a name the first user, an admin. Only
// while Zonewiki has none (the settings file is checked again as it is
// written: of two people sending the form at once, one sets the login,
// the other is told to sign in), and only where it may be set in the
// browser (maySetLogin).
func setupRoute(w http.ResponseWriter, r *http.Request) {
	if !maySetLogin() {
		http.Redirect(w, r, "/", http.StatusFound)
		return
	}
	v := &setupView{ConfigName: configName(), Asked: needsSetup(), HasToken: settings().MCPToken != ""}
	if r.Method == http.MethodPost {
		name := strings.ToLower(strings.TrimSpace(r.PostFormValue("username")))
		password := r.PostFormValue("password")
		v.Username = name
		switch {
		case name != "" && !userNameRE.MatchString(name):
			v.Error = "A user name has lowercase letters, digits and hyphens (alice, mary-ann)."
		case reservedNames[name]:
			v.Error = reservedNameMsg(name)
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
				f.Unset("", "no-login") // it has one now
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
	title := "Set a password"
	if !v.Asked {
		title = "Set a login"
	}
	page(w, newReq(r), status, "setup.html", title, shell{noActions: true}, v)
}

// errHasLogin: the settings file has a login already (setupRoute).
var errHasLogin = errors.New("the wiki has a login already")
