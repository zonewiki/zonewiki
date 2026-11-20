// Connect an assistant (/assistant, docs/spec/mcp.md): whoever is signed
// in makes their MCP token in the browser, as zonewiki user token (with
// users: their own) or zonewiki config token (without: the wiki's) does on
// the host, and revokes it. The token is shown once, with what to give an
// MCP client; Zone keeps its hash. Making one asks for the password again
// (where there is one): a token outlasts the session it was made in, so a
// session someone else got hold of mustn't be enough to make one.
//
// With OAuth (oauth.go) an assistant needs no token copied: it is given
// the address and its user allows it in the browser. The page lists those
// connections (grants.go), each to be changed (what it may do and see, the
// choices Allow offered) or ended on its own.
package main

import (
	"errors"
	"html/template"
	"net/http"
	"time"
	"zonewiki/internal/settingsfile"
)

func assistantRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /assistant", assistantPage)
	mux.HandleFunc("POST /assistant/token", assistantTokenRoute)
	mux.HandleFunc("POST /assistant/revoke", assistantRevokeRoute)
	mux.HandleFunc("POST /assistant/connections/{id}/revoke", assistantDisconnectRoute)
	mux.HandleFunc("POST /assistant/connections/{id}/change", assistantChangeRoute)
}

// assistantView: the page (assistant.html).
type assistantView struct {
	view
	BreadcrumbHTML template.HTML
	URL            string // /mcp's address, as a client is given it
	User           string // whose token it is; "": the wiki's (no users)
	Has            bool   // a token is set
	AskPassword    bool   // making one asks for the password (there is a login)
	Viewer         bool   // their assistant reads, and changes nothing
	Token          string // a token just made, shown once
	Done, Error    string // what the last change did, or why it wasn't made
	OAuth          bool   // assistants can sign in through the browser (oauth.go)
	Connections    []grantRow
	Changes        string // where what assistants changed is listed (changes.go); "" without git
}

// siteURL: path as an address to copy: on base (the base-url setting), or
// on the address the request came in on (r nil: the path alone).
func siteURL(base string, r *http.Request, path string) string {
	if base != "" {
		return base + path
	}
	if r == nil {
		return path
	}
	scheme := "http"
	if isHTTPS(r) {
		scheme = "https"
	}
	return scheme + "://" + r.Host + path
}

// tokenOwner: whose token the request's is about, as the latest settings
// have them: the user signed in, or with no users the wiki ("", s's own
// token). ok false: the user is no longer there.
func tokenOwner(r *http.Request) (s *Settings, name, kept string, ok bool) {
	s = latestSettings()
	if s.Users == nil {
		return s, "", s.MCPToken, true
	}
	u := s.Users[userOf(r).Name]
	if u == nil {
		return s, "", "", false
	}
	return s, u.Name, u.MCPToken, true
}

func assistantData(r *http.Request) *assistantView {
	s, name, kept, _ := tokenOwner(r)
	v := &assistantView{URL: siteURL(s.BaseURL, r, "/mcp"), User: name, Has: kept != "",
		AskPassword: s.authOn(), Viewer: s.isViewer(name), OAuth: oauthOn(s), Connections: grantsOf(name),
		BreadcrumbHTML: breadcrumb(newReq(r), []crumb{{"Assistant", ""}}, "", false, "", "")}
	for i := range v.Connections {
		c := &v.Connections[i]
		c.Reach, _ = chooseReach(s, userOf(r), c.had, nil)
		c.Reach.Change = true
		if cfg.GitEnabled {
			c.Changes = changesURL(changesOf{By: name, Via: clientName(c.Client)}, 0)
		}
	}
	if cfg.GitEnabled {
		v.Changes = changesURL(changesOf{By: name, Assistant: true}, 0)
	}
	return v
}

func showAssistant(w http.ResponseWriter, r *http.Request, status int, v *assistantView) {
	// The page may hold a token: no cache keeps it.
	w.Header().Set("Cache-Control", "no-store")
	page(w, newReq(r), status, "assistant.html", "Connect an assistant", shell{}, v)
}

// assistantPage: GET /assistant. ?done=revoke says so, from a fixed set
// (a link can't make the page say anything else).
func assistantPage(w http.ResponseWriter, r *http.Request) {
	if _, _, _, ok := tokenOwner(r); !ok {
		http.NotFound(w, r)
		return
	}
	v := assistantData(r)
	switch done := r.URL.Query().Get("done"); {
	case done == "revoke" && !v.Has:
		v.Done = "Revoked the token: an assistant using it is refused from now on."
	case done == "disconnect":
		v.Done = "Ended the connection: that assistant is refused from now on."
	case done == "change":
		v.Done = "Changed what that assistant may do and see, from its next call on."
	}
	showAssistant(w, r, http.StatusOK, v)
}

// passwordAgain: the form's password is the user name's (with no users
// the wiki's), as making a token, allowing an assistant and giving a
// connection more ask: each outlasts the session, so a session someone
// else got hold of mustn't be enough. A wrong one counts against the
// client like a wrong login, so these pages guess a password no faster
// than the login page does; wait > 0: it must wait first (right false).
func passwordAgain(r *http.Request, s *Settings, name string) (right bool, wait time.Duration) {
	hash := s.PasswordHash
	if s.Users != nil {
		hash = ""
		if u := s.Users[name]; u != nil {
			hash = u.PasswordHash
		}
	}
	return loginThrottle.try(clientKey(r), name, func() bool {
		return checkPassword(r.PostFormValue("password"), hash)
	})
}

// setToken writes the owner's token (hash; "": none) to the settings file.
func setToken(r *http.Request, name, hash string) error {
	return changeSettings(userOf(r).Name, func(f *settingsfile.File) error {
		if hash == "" {
			f.Unset(name, "mcp-token")
		} else {
			f.Set(name, "mcp-token", hash)
		}
		return nil
	})
}

// assistantTokenRoute: POST /assistant/token makes a new token, replacing
// the one before, and shows it, once. With a login it takes the password
// again; a wrong one counts against the client like a wrong login, so the
// page guesses a password no faster than the login page does.
func assistantTokenRoute(w http.ResponseWriter, r *http.Request) {
	s, name, _, ok := tokenOwner(r)
	if !ok {
		http.NotFound(w, r)
		return
	}
	if s.authOn() {
		right, wait := passwordAgain(r, s, name)
		if wait > 0 || !right {
			v, status := assistantData(r), http.StatusForbidden
			v.Error = "Incorrect password: no token was made."
			if wait > 0 {
				v.Error, status = waitMessage(wait), http.StatusTooManyRequests
			}
			showAssistant(w, r, status, v)
			return
		}
	}
	token := newToken()
	if err := setToken(r, name, hashToken(token)); err != nil {
		v := assistantData(r)
		v.Error = err.Error()
		showAssistant(w, r, http.StatusConflict, v)
		return
	}
	v := assistantData(r)
	v.Token = token
	showAssistant(w, r, http.StatusOK, v)
}

// assistantRevokeRoute: POST /assistant/revoke takes the token away. No
// password: it only ends access.
func assistantRevokeRoute(w http.ResponseWriter, r *http.Request) {
	_, name, _, ok := tokenOwner(r)
	if !ok {
		http.NotFound(w, r)
		return
	}
	if err := setToken(r, name, ""); err != nil {
		v := assistantData(r)
		v.Error = err.Error()
		showAssistant(w, r, http.StatusConflict, v)
		return
	}
	http.Redirect(w, r, "/assistant?done=revoke", http.StatusSeeOther)
}

// assistantDisconnectRoute: POST /assistant/connections/{id}/revoke ends
// one of the owner's connections (no other's: 404). No password: it only
// ends access.
func assistantDisconnectRoute(w http.ResponseWriter, r *http.Request) {
	_, name, _, ok := tokenOwner(r)
	if !ok {
		http.NotFound(w, r)
		return
	}
	id := r.PathValue("id")
	n, err := revokeGrants(func(g grant) bool { return g.ID == id && g.User == name })
	if err != nil {
		serverError(w, r, err)
		return
	}
	if n == 0 {
		http.NotFound(w, r)
		return
	}
	http.Redirect(w, r, "/assistant?done=disconnect", http.StatusSeeOther)
}

// assistantChangeRoute: POST /assistant/connections/{id}/change sets what
// one of the owner's connections may do and see (no other's: 404), from
// the choices Allow offered (chooseReach), which go no further than the
// user. What only takes away needs no password; what gives anything back
// or more, a mixed change too, takes it again, as Allow does (refused: the
// page with the form open as chosen, and why). Its tokens stay, and the
// next call with them is asked with what was chosen.
func assistantChangeRoute(w http.ResponseWriter, r *http.Request) {
	s, name, _, ok := tokenOwner(r)
	if !ok {
		http.NotFound(w, r)
		return
	}
	id := r.PathValue("id")
	g, found := userGrant(name, id)
	if !found {
		http.NotFound(w, r)
		return
	}
	if r.ParseForm() != nil {
		http.Error(w, "The form can't be read.", http.StatusBadRequest)
		return
	}
	chosen, to := chooseReach(s, userOf(r), g.less, r.PostForm)
	chosen.Change = true
	// refuse: the page, with the connection's form open and why (kept: as
	// chosen, else as it is now).
	refuse := func(status int, why string, kept bool) {
		v := assistantData(r)
		for i := range v.Connections {
			if c := &v.Connections[i]; c.ID == id {
				c.Open, c.Error = true, why
				if kept {
					c.Reach = chosen
				}
			}
		}
		showAssistant(w, r, status, v)
	}
	if to.beyond(g.less) {
		if r.PostFormValue("password") == "" { // no guess: not counted
			refuse(http.StatusForbidden, "That gives it more than it has: enter your password. Nothing was changed.", true)
			return
		}
		if right, wait := passwordAgain(r, s, name); wait > 0 {
			refuse(http.StatusTooManyRequests, waitMessage(wait), true)
			return
		} else if !right {
			refuse(http.StatusForbidden, "Incorrect password: nothing was changed.", true)
			return
		}
	}
	found, err := reachGrant(name, id, g.less, to)
	switch {
	case errors.Is(err, errGrantChanged):
		refuse(http.StatusConflict, err.Error(), false)
		return
	case err != nil:
		serverError(w, r, err)
		return
	case !found:
		http.NotFound(w, r)
		return
	}
	http.Redirect(w, r, "/assistant?done=change", http.StatusSeeOther)
}
