// OAuth for /mcp (docs/spec/mcp.md, Connecting with OAuth): an MCP client
// is given only the address of /mcp; its user signs in to Zone in a
// browser and allows it, and the client gets tokens of its own (a
// connection, grants.go). Zone is its own authorization server, as the MCP
// authorization spec (2025-11-25) has it: the metadata under /.well-known/
// (RFC 9728, RFC 8414), client registration that keeps nothing (RFC 7591),
// the authorization code with PKCE (S256), refresh tokens replaced at
// every use. Only with a login (someone has to sign in and allow), and
// not with mcp-oauth = no.
package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"slices"
	"strings"
	"sync"
	"time"
	"unicode"
	"unicode/utf8"
)

func oauthRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /.well-known/oauth-protected-resource", oauthResourceMeta)
	mux.HandleFunc("GET /.well-known/oauth-protected-resource/mcp", oauthResourceMeta)
	mux.HandleFunc("GET /.well-known/oauth-authorization-server", oauthServerMeta)
	mux.HandleFunc("POST /oauth/register", oauthRegister)
	mux.HandleFunc("GET /oauth/authorize", oauthAuthorize)
	mux.HandleFunc("POST /oauth/authorize", oauthAuthorize)
	mux.HandleFunc("POST /oauth/token", oauthToken)
}

// oauthOn: clients may connect through OAuth: the wiki has a login, and
// the settings don't turn it off.
func oauthOn(s *Settings) bool { return s.authOn() && !s.NoOAuth }

// oauthOpen: the OAuth routes a client's server calls with no session and
// from any origin: they act on nothing a cookie gives (a code and its
// verifier, a refresh token, or nothing).
func oauthOpen(path string) bool {
	return path == "/oauth/token" || path == "/oauth/register" || strings.HasPrefix(path, "/.well-known/oauth-")
}

// oauthScope: the one scope. What an assistant may do is its user's role.
const oauthScope = "mcp"

// oauthOff answers 404 where OAuth is off; true if it did.
func oauthOff(w http.ResponseWriter, r *http.Request) bool {
	if oauthOn(settings()) {
		return false
	}
	http.NotFound(w, r)
	return true
}

// mcpResource: /mcp's address as a client names it (the resource of RFC
// 8707), and the issuer: Zone's own address.
func mcpResource(s *Settings, r *http.Request) string { return siteURL(s.BaseURL, r, "/mcp") }
func oauthIssuer(s *Settings, r *http.Request) string { return siteURL(s.BaseURL, r, "") }

// resourceMetaURL: where the 401 of /mcp points a client.
func resourceMetaURL(s *Settings, r *http.Request) string {
	return siteURL(s.BaseURL, r, "/.well-known/oauth-protected-resource/mcp")
}

// oauthResourceMeta: the protected resource metadata (RFC 9728).
func oauthResourceMeta(w http.ResponseWriter, r *http.Request) {
	if oauthOff(w, r) {
		return
	}
	s := settings()
	writeJSON(w, http.StatusOK, map[string]any{
		"resource":                 mcpResource(s, r),
		"authorization_servers":    []string{oauthIssuer(s, r)},
		"scopes_supported":         []string{oauthScope},
		"bearer_methods_supported": []string{"header"},
		"resource_name":            appName,
	})
}

// oauthServerMeta: the authorization server metadata (RFC 8414).
func oauthServerMeta(w http.ResponseWriter, r *http.Request) {
	if oauthOff(w, r) {
		return
	}
	s := settings()
	at := func(path string) string { return siteURL(s.BaseURL, r, path) }
	writeJSON(w, http.StatusOK, map[string]any{
		"issuer":                                         oauthIssuer(s, r),
		"authorization_endpoint":                         at("/oauth/authorize"),
		"token_endpoint":                                 at("/oauth/token"),
		"registration_endpoint":                          at("/oauth/register"),
		"response_types_supported":                       []string{"code"},
		"grant_types_supported":                          []string{"authorization_code", "refresh_token"},
		"code_challenge_methods_supported":               []string{"S256"},
		"token_endpoint_auth_methods_supported":          []string{"none"},
		"scopes_supported":                               []string{oauthScope},
		"authorization_response_iss_parameter_supported": true,
	})
}

// oauthError answers a client's call with an OAuth error (RFC 6749, 5.2).
func oauthError(w http.ResponseWriter, status int, code, description string) {
	w.Header().Set("Cache-Control", "no-store")
	writeJSON(w, status, map[string]string{"error": code, "error_description": description})
}

// ── Clients ──────────────────────────────────────────────────────────────────

// oauthClient: what a client registered: its name and where its answers
// may go. Zone keeps none of it: the client id is this, signed with Zone's
// key (sign), so a stranger's registrations leave nothing behind.
type oauthClient struct {
	Name      string   `json:"n"`
	Redirects []string `json:"r"`
}

const clientIDPrefix = "zc1."

func (c oauthClient) id() string {
	raw, _ := json.Marshal(c)
	payload := base64.RawURLEncoding.EncodeToString(raw)
	return clientIDPrefix + payload + "." + sign("oauth-client:"+payload)
}

// clientOf: the client a client id names, if Zone signed it.
func clientOf(id string) (c oauthClient, ok bool) {
	rest, found := strings.CutPrefix(id, clientIDPrefix)
	payload, sig, cut := strings.Cut(rest, ".")
	if !found || !cut || !hmac.Equal([]byte(sig), []byte(sign("oauth-client:"+payload))) {
		return c, false
	}
	raw, err := base64.RawURLEncoding.DecodeString(payload)
	if err != nil || json.Unmarshal(raw, &c) != nil || len(c.Redirects) == 0 {
		return c, false
	}
	return c, true
}

// Where an answer may go: an https address, or http on this machine (a
// native client listening on a port of its own choosing, RFC 8252).
func loopbackHost(name string) bool {
	return name == "localhost" || name == "127.0.0.1" || name == "::1"
}

// The host is ASCII (an international name as its punycode, xn--…), as the
// question and Connect an assistant show it: a name in other letters that
// look like Latin ones ("clаude.ai" with a Cyrillic а) can't pass there for
// the name it copies.
func validRedirect(uri string) bool {
	u, err := url.Parse(uri)
	if err != nil || len(uri) > 1000 || u.Fragment != "" || u.User != nil || u.Hostname() == "" ||
		strings.IndexFunc(u.Host, func(r rune) bool { return r >= utf8.RuneSelf }) >= 0 {
		return false
	}
	return u.Scheme == "https" || u.Scheme == "http" && loopbackHost(u.Hostname())
}

// allows: uri is one the client registered. A loopback address matches
// whatever its port (the client picks one as it starts).
func (c oauthClient) allows(uri string) bool {
	if slices.Contains(c.Redirects, uri) {
		return validRedirect(uri)
	}
	u, err := url.Parse(uri)
	if err != nil || !validRedirect(uri) || u.Scheme != "http" {
		return false
	}
	for _, registered := range c.Redirects {
		reg, err := url.Parse(registered)
		if err == nil && reg.Scheme == "http" && loopbackHost(reg.Hostname()) && reg.Hostname() == u.Hostname() &&
			reg.Path == u.Path && reg.RawQuery == u.RawQuery {
			return true
		}
	}
	return false
}

// clientName: the name a client gave, as Zone shows it: one line, short.
func clientName(name string) string {
	name = strings.Join(strings.FieldsFunc(name, func(r rune) bool {
		return unicode.IsControl(r) || unicode.IsSpace(r)
	}), " ")
	if utf8.RuneCountInString(name) > 80 {
		name = string([]rune(name)[:80])
	}
	if name == "" {
		return noClientName
	}
	return name
}

// noClientName: what a client that gave no name is called.
const noClientName = "An MCP client"

// oauthRegister: POST /oauth/register (RFC 7591), for public clients:
// the name and redirect URIs come back with a client id that holds them.
func oauthRegister(w http.ResponseWriter, r *http.Request) {
	if oauthOff(w, r) {
		return
	}
	var req struct {
		Name      string   `json:"client_name"`
		Redirects []string `json:"redirect_uris"`
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, 64<<10))
	if err != nil || json.Unmarshal(body, &req) != nil {
		oauthError(w, http.StatusBadRequest, "invalid_client_metadata", "The body isn't a JSON object of client metadata.")
		return
	}
	if len(req.Redirects) == 0 || len(req.Redirects) > 10 || !allOf(req.Redirects, validRedirect) {
		oauthError(w, http.StatusBadRequest, "invalid_redirect_uri",
			"Give one to ten redirect_uris, each https, or http on localhost or 127.0.0.1, its host in ASCII (punycode for an international name).")
		return
	}
	c := oauthClient{Name: clientName(req.Name), Redirects: req.Redirects}
	w.Header().Set("Cache-Control", "no-store")
	writeJSON(w, http.StatusCreated, map[string]any{
		"client_id":                  c.id(),
		"client_id_issued_at":        now().Unix(),
		"client_name":                c.Name,
		"redirect_uris":              c.Redirects,
		"token_endpoint_auth_method": "none",
		"grant_types":                []string{"authorization_code", "refresh_token"},
		"response_types":             []string{"code"},
		"scope":                      oauthScope,
	})
}

func allOf[T any](items []T, ok func(T) bool) bool {
	for _, item := range items {
		if !ok(item) {
			return false
		}
	}
	return true
}

// ── Allowing a client ────────────────────────────────────────────────────────

// authRequest: what a client asks /oauth/authorize, and the page's form
// sends again.
type authRequest struct {
	ClientID, RedirectURI, State, Challenge, Resource string
	client                                            oauthClient
}

// consentView: the page (oauth.html): the question, or what is wrong
// with it, or where the answer goes.
type consentView struct {
	view
	Req      *authRequest
	User     string // who allows; "": the wiki (no users)
	Client   string // the client's name, as it gave it
	Host     string // where the answer goes
	Loopback bool   // to this machine: any program on it can ask
	Viewer   bool
	ReadOnly bool // chosen: it reads and changes nothing
	Choose   bool // there is something to choose of what it sees (users)
	Private  bool // chosen: the user's private place
	Groups   []consentGroup
	Sees     string // after Allow: what it sees, when that is less than the user does
	Error    string // the password was wrong
	Problem  string // the request can't be answered
	Back     string // the address the answer goes to (after Allow or Deny, or with a problem the client can be told)
	Allowed  bool
}

// consentGroup: one of the user's groups on the question, and whether its
// place is given.
type consentGroup struct {
	Name  string
	Given bool
}

// oauthAuthorize: GET /oauth/authorize asks whoever is signed in (the
// checks send anyone else to the login first) whether the client may act
// as them; POST is their answer. A client or redirect URI Zone doesn't
// know gets a page, never a redirect: the answer goes only where a
// registration said.
func oauthAuthorize(w http.ResponseWriter, r *http.Request) {
	if oauthOff(w, r) {
		return
	}
	s := settings()
	get := r.URL.Query().Get
	if r.Method == http.MethodPost {
		get = r.PostFormValue
	}
	q := &authRequest{ClientID: get("client_id"), RedirectURI: get("redirect_uri"), State: get("state"),
		Challenge: get("code_challenge"), Resource: get("resource")}
	v := &consentView{Req: q}
	show := func(status int) {
		w.Header().Set("Cache-Control", "no-store")
		page(w, newReq(r), status, "oauth.html", "Connect an assistant", shell{noActions: true}, v)
	}
	var known bool
	if q.client, known = clientOf(q.ClientID); !known {
		v.Problem = "This request names no client this wiki knows. Start again from your assistant: add this wiki there once more."
		show(http.StatusBadRequest)
		return
	}
	if !q.client.allows(q.RedirectURI) {
		v.Problem = "This request asks for its answer at an address its client didn't register. Nothing was allowed."
		show(http.StatusBadRequest)
		return
	}
	to, _ := url.Parse(q.RedirectURI)
	v.Client, v.Host, v.Loopback = q.client.Name, to.Host, loopbackHost(to.Hostname())
	// answer: the address the client gets its answer at.
	answer := func(params ...string) string {
		u := *to
		vals := u.Query()
		for i := 0; i+1 < len(params); i += 2 {
			vals.Set(params[i], params[i+1])
		}
		if q.State != "" {
			vals.Set("state", q.State)
		}
		vals.Set("iss", oauthIssuer(s, r))
		u.RawQuery = vals.Encode()
		return u.String()
	}
	// What the client got wrong is the client's to hear: the page says so,
	// with the way back.
	problem := func(code, text string) {
		v.Problem, v.Back = text, answer("error", code)
		show(http.StatusBadRequest)
	}
	switch {
	case get("response_type") != "code":
		problem("unsupported_response_type", "The assistant asked in a way this wiki doesn't answer (response_type must be code).")
		return
	case len(q.Challenge) != 43 || get("code_challenge_method") != "S256":
		problem("invalid_request", "The assistant sent no PKCE challenge (S256), which this wiki requires.")
		return
	case !resourceIsMCP(s, r, q.Resource):
		problem("invalid_target", "The assistant asked for another address than this wiki's "+mcpResource(s, r)+".")
		return
	}
	user := userOf(r)
	if !validGrantUser(s, user.Name) {
		http.NotFound(w, r)
		return
	}
	v.User, v.Viewer = user.Name, s.isViewer(user.Name)
	// A viewer's reads only, and is kept so (the question says it changes
	// nothing): made an editor later, they don't find it writing.
	v.ReadOnly = v.Viewer || r.Method == http.MethodPost && r.PostFormValue("may") == "read"
	// What it sees of the user's places (with users: without, all is
	// shared): everything, unless the form came with its choices (choose)
	// and one is left out. The shared wiki is always there.
	given := less{ReadOnly: v.ReadOnly}
	chosen := r.Method == http.MethodPost && r.PostFormValue("choose") != ""
	v.Choose, v.Private = s.Users != nil, true
	var groups []string
	for _, g := range groupsFor(user) {
		in := !chosen || slices.Contains(r.PostForm["see"], "group:"+g)
		v.Groups = append(v.Groups, consentGroup{g, in})
		if in {
			groups = append(groups, g)
		}
	}
	if chosen && v.Choose {
		v.Private = slices.Contains(r.PostForm["see"], "private")
		given.NoPrivate = !v.Private
		if len(groups) != len(v.Groups) {
			if groups == nil {
				groups = []string{}
			}
			given.Groups = &groups
		}
	}
	if r.Method != http.MethodPost {
		show(http.StatusOK)
		return
	}
	if r.PostFormValue("answer") != "allow" {
		v.Back = answer("error", "access_denied")
		w.Header().Set("Refresh", "0; url="+v.Back)
		show(http.StatusOK)
		return
	}
	// The password again, as for a token made on Connect an assistant: a
	// connection outlasts the session, so a session someone else got hold
	// of mustn't be enough to make one.
	hash := s.PasswordHash
	if s.Users != nil {
		hash = s.Users[user.Name].PasswordHash
	}
	right, wait := loginThrottle.try(clientKey(r), func() bool {
		return checkPassword(r.PostFormValue("password"), hash)
	})
	if wait > 0 || !right {
		status := http.StatusForbidden
		v.Error = "Incorrect password: nothing was allowed."
		if wait > 0 {
			v.Error, status = waitMessage(wait), http.StatusTooManyRequests
		}
		show(status)
		return
	}
	code := newCode(authCode{user: user.Name, client: q.client.Name, clientHash: hashToken(q.ClientID),
		redirectURI: q.RedirectURI, redirectHost: to.Host, challenge: q.Challenge, less: given})
	// Not a redirect: the page policy lets a form lead only to Zone itself
	// (form-action 'self', which browsers hold a form's redirects to), so
	// the answer is a page that sends the browser on, and shows the way as
	// a link.
	v.Back, v.Allowed, v.Sees = answer("code", code), true, given.sees()
	w.Header().Set("Refresh", "0; url="+v.Back)
	show(http.StatusOK)
}

// resourceIsMCP: the resource a client names (RFC 8707) is this wiki's
// /mcp, or it names none.
func resourceIsMCP(s *Settings, r *http.Request, resource string) bool {
	return resource == "" || strings.TrimRight(resource, "/") == mcpResource(s, r)
}

// validGrantUser: user may have a connection: one of the users, not
// disabled; or without users the wiki ("").
func validGrantUser(s *Settings, user string) bool {
	if s.Users == nil {
		return user == ""
	}
	u := s.Users[user]
	return u != nil && !u.Disabled
}

// ── Codes and tokens ─────────────────────────────────────────────────────────

// authCode: an authorization code, for a minute and one use, in memory (a
// restart drops the few that are open). Used, it is remembered a while
// longer (spentFor), with the connection made with it: one used again was
// copied, and the connection is ended (RFC 6749, 4.1.2).
type authCode struct {
	user, client, clientHash, redirectURI, redirectHost, challenge string
	less                                                           less // what was allowed less than the user has
	expires                                                        time.Time
	taken                                                          bool   // used once: no code any more
	grant                                                          string // the connection made with it ("" while it is made, or none was)
	again                                                          bool   // used again while its connection was made
}

const (
	codeLifetime = time.Minute
	spentFor     = 10 * time.Minute
)

var authCodes = struct {
	sync.Mutex
	byHash map[string]authCode
}{byHash: map[string]authCode{}}

func newCode(c authCode) string {
	code := newToken()
	c.expires = now().Add(codeLifetime)
	authCodes.Lock()
	defer authCodes.Unlock()
	for hash, old := range authCodes.byHash {
		if !now().Before(old.expires) {
			delete(authCodes.byHash, hash)
		}
	}
	authCodes.byHash[hashToken(code)] = c
	return code
}

// takeCode: the code's request, once. A code used before is none, and
// copied: copied is the connection made with it, which the caller ends
// ("" when that is still being made: spendCode ends it then).
func takeCode(code string) (c authCode, ok bool, copied string) {
	authCodes.Lock()
	defer authCodes.Unlock()
	hash := hashToken(code)
	c, found := authCodes.byHash[hash]
	switch {
	case !found || !now().Before(c.expires):
		return c, false, ""
	case c.taken:
		c.again = true
		authCodes.byHash[hash] = c
		return c, false, c.grant
	}
	c.taken, c.expires = true, now().Add(spentFor)
	authCodes.byHash[hash] = c
	return c, true, ""
}

// spendCode: the connection id was made with the code; false when the code
// was used again meanwhile (copied: the caller ends the connection).
func spendCode(code, id string) bool {
	authCodes.Lock()
	defer authCodes.Unlock()
	hash := hashToken(code)
	c, found := authCodes.byHash[hash]
	if !found {
		return true
	}
	c.grant = id
	authCodes.byHash[hash] = c
	return !c.again
}

// endCopied ends the connection id, made with a code that was used again.
func endCopied(id string) {
	if n, err := revokeGrants(func(g grant) bool { return g.ID == id }); err != nil {
		logf("ending connection %s: %v", id, err)
	} else if n > 0 {
		logf("connection %s: its authorization code was used twice; the connection is ended", id)
	}
}

// pkceMatches: verifier is the one the challenge was made from (S256).
func pkceMatches(verifier, challenge string) bool {
	if len(verifier) < 43 || len(verifier) > 128 {
		return false
	}
	sum := sha256.Sum256([]byte(verifier))
	return hmac.Equal([]byte(base64.RawURLEncoding.EncodeToString(sum[:])), []byte(challenge))
}

// tokenRefusal: why /oauth/token gives no tokens.
type tokenRefusal struct{ code, description string }

func (e *tokenRefusal) Error() string { return e.description }

// oauthToken: POST /oauth/token: a code for a connection's first tokens,
// or a refresh token for its next. What fails counts against the client
// as a wrong MCP token does.
func oauthToken(w http.ResponseWriter, r *http.Request) {
	if oauthOff(w, r) {
		return
	}
	var tokens grantTokens
	var err error
	_, wait := mcpThrottle.try(clientKey(r), func() bool {
		tokens, err = grantTokensFor(r)
		return err == nil
	})
	if wait > 0 {
		w.Header().Set("Retry-After", itoa(int(wait.Seconds())+1))
		oauthError(w, http.StatusTooManyRequests, "slow_down", waitMessage(wait))
		return
	}
	var refusal *tokenRefusal
	switch {
	case errors.As(err, &refusal):
		oauthError(w, http.StatusBadRequest, refusal.code, refusal.description)
		return
	case err != nil:
		serverError(w, r, err)
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Pragma", "no-cache")
	writeJSON(w, http.StatusOK, map[string]any{
		"access_token": tokens.Access, "token_type": "Bearer", "expires_in": int(accessLifetime.Seconds()),
		"refresh_token": tokens.Refresh, "scope": oauthScope,
	})
}

func grantTokensFor(r *http.Request) (grantTokens, error) {
	s := settings()
	none := grantTokens{}
	clientID := r.PostFormValue("client_id")
	if _, known := clientOf(clientID); !known {
		return none, &tokenRefusal{"invalid_client", "No client this wiki knows: register again."}
	}
	if !resourceIsMCP(s, r, r.PostFormValue("resource")) {
		return none, &tokenRefusal{"invalid_target", "The resource isn't this wiki's " + mcpResource(s, r) + "."}
	}
	switch r.PostFormValue("grant_type") {
	case "authorization_code":
		code := r.PostFormValue("code")
		c, found, copied := takeCode(code)
		switch {
		case copied != "":
			endCopied(copied)
			fallthrough
		case !found:
			return none, &tokenRefusal{"invalid_grant", "The code is wrong, used or older than a minute."}
		case !sameHash(c.clientHash, hashToken(clientID)) || c.redirectURI != r.PostFormValue("redirect_uri"):
			return none, &tokenRefusal{"invalid_grant", "The code was given to another client or redirect URI."}
		case !pkceMatches(r.PostFormValue("code_verifier"), c.challenge):
			return none, &tokenRefusal{"invalid_grant", "The code_verifier doesn't match the code's challenge."}
		case !validGrantUser(s, c.user):
			return none, &tokenRefusal{"invalid_grant", "Whoever allowed this can't sign in any more."}
		}
		tokens, id, err := newGrant(c.user, c.client, c.clientHash, c.redirectHost, c.less)
		if err == nil && !spendCode(code, id) {
			endCopied(id)
			return none, &tokenRefusal{"invalid_grant", "The code is wrong, used or older than a minute."}
		}
		return tokens, err
	case "refresh_token":
		tokens, _, err := refreshGrant(r.PostFormValue("refresh_token"), hashToken(clientID),
			func(user string) bool { return validGrantUser(s, user) })
		if errors.Is(err, errNoGrant) {
			return none, &tokenRefusal{"invalid_grant", "The refresh token is wrong, used, expired or revoked: connect again."}
		}
		return tokens, err
	}
	return none, &tokenRefusal{"unsupported_grant_type", "Use authorization_code or refresh_token."}
}
