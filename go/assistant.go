// Connect an assistant (/assistant, docs/spec/mcp.md): whoever is signed
// in makes their MCP token in the browser, as zonewiki user token (with
// users: their own) or zonewiki config token (without: the wiki's) does on
// the host, and revokes it. The token is shown once, with what to give an
// MCP client; Zone keeps its hash. Making one asks for the password again
// (where there is one): a token outlasts the session it was made in, so a
// session someone else got hold of mustn't be enough to make one.
package main

import (
	"html/template"
	"net/http"
	"zonewiki/internal/settingsfile"
)

func assistantRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /assistant", assistantPage)
	mux.HandleFunc("POST /assistant/token", assistantTokenRoute)
	mux.HandleFunc("POST /assistant/revoke", assistantRevokeRoute)
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
	u := s.Users[userOf(r)]
	if u == nil {
		return s, "", "", false
	}
	return s, u.Name, u.MCPToken, true
}

func assistantData(r *http.Request) *assistantView {
	s, name, kept, _ := tokenOwner(r)
	return &assistantView{URL: siteURL(s.BaseURL, r, "/mcp"), User: name, Has: kept != "",
		AskPassword: s.authOn(), Viewer: s.isViewer(name),
		BreadcrumbHTML: breadcrumb(newReq(r), []crumb{{"Assistant", ""}}, "", false, "", "")}
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
	if r.URL.Query().Get("done") == "revoke" && !v.Has {
		v.Done = "Revoked the token: an assistant using it is refused from now on."
	}
	showAssistant(w, r, http.StatusOK, v)
}

// setToken writes the owner's token (hash; "": none) to the settings file.
func setToken(r *http.Request, name, hash string) error {
	return changeSettings(userOf(r), func(f *settingsfile.File) error {
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
		hash := s.PasswordHash
		if s.Users != nil {
			hash = s.Users[name].PasswordHash
		}
		right, wait := loginThrottle.try(clientKey(r), func() bool {
			return checkPassword(r.PostFormValue("password"), hash)
		})
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
