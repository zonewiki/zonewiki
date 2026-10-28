// How Leaf protects the wiki (docs/spec/security.md): the Host and Origin
// checks, the login gate and its session cookie, the page policy, and the
// throttle on wrong passwords and MCP tokens.
package main

import (
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// now is the clock of the throttle and the session (a test sets it).
var now = time.Now

// DNS rebinding: a page on evil.example whose name later resolves to
// 127.0.0.1 reaches Leaf with "Host: evil.example". Only localhost, and the
// names in the hosts setting (a reverse proxy's public name, any port), get
// in. LEAF_ALLOW_REMOTE without hosts turns the check off (LAN access by IP).
var allowedHosts = map[string]bool{"localhost:5000": true, "127.0.0.1:5000": true}

func hostAllowed(host string) bool {
	hosts := settings().Hosts
	if cfg.AllowRemote && len(hosts) == 0 {
		return true
	}
	return allowedHosts[host] || hosts[hostName(host)]
}

// Cross-site request forgery: without it, any page open in the browser could
// post a form to http://localhost:5000/delete/<page> (or /edit, or the deck
// API, which takes text/plain bodies without a CORS preflight), and the Host
// check passes because the Host really is localhost. Browsers send Origin on
// every cross-site POST, so a write whose Origin names another site, or is
// "null" (a sandboxed iframe), is refused. Leaf's own forms, fetches and the
// pagehide beacon are same-origin; tools like curl send no Origin.
func crossSite(r *http.Request) bool {
	switch r.Method {
	case "POST", "PUT", "PATCH", "DELETE":
	default:
		return false
	}
	origins, sent := r.Header["Origin"]
	if !sent {
		return false
	}
	origin := origins[0]
	if origin == "null" {
		return true
	}
	u, err := url.Parse(origin)
	return err != nil || u.Host != r.Host
}

// What a page may run. Pages render their raw HTML as written, and pages are
// also written by AI clients (MCP), which may have read a prompt injection: a
// <script>, an onerror= or a javascript: link in a page would run with the
// user's session and could read and rewrite the whole wiki. So only Leaf's own
// script files run (script-src 'self'; Leaf has no inline scripts or
// handlers), no plugins, no <base> that re-points those files, no form that
// posts elsewhere. Clickjacking: another site could load Leaf invisibly in a
// frame and trick a signed-in user into clicking Delete or Commit, so only
// Leaf's own pages may frame it (a page's raw HTML may show an upload in an
// <iframe>). A response with its own policy (an SVG upload's) keeps it and
// gets only frame-ancestors added.
const pageCSP = "script-src 'self'; object-src 'none'; base-uri 'self'; " +
	"form-action 'self'; frame-ancestors 'self'"

func contentPolicy(h http.Header) {
	csp := h.Get("Content-Security-Policy")
	if csp == "" {
		h.Set("Content-Security-Policy", pageCSP)
	} else if !strings.Contains(csp, "frame-ancestors") {
		h.Set("Content-Security-Policy", csp+"; frame-ancestors 'self'")
	}
	h.Set("X-Frame-Options", "SAMEORIGIN") // browsers without CSP
	// A JSON or text response is never run as a script (<script src=…>).
	h.Set("X-Content-Type-Options", "nosniff")
	if strings.Contains(h.Get("Content-Type"), "text/html") {
		h.Set("Cache-Control", "no-store")
	}
}

// isHTTPS: the browser's connection is HTTPS, Leaf's own (rare) or a
// reverse proxy's, which says so in X-Forwarded-Proto.
func isHTTPS(r *http.Request) bool {
	proto, _, _ := strings.Cut(r.Header.Get("X-Forwarded-Proto"), ",")
	return r.TLS != nil || strings.ToLower(strings.TrimSpace(proto)) == "https"
}

// secretKey signs session cookies: one Leaf made once and keeps in
// .leaf/secret_key in the wiki folder (owner-only, kept out of git), so a
// restart doesn't sign everyone out. A wiki folder Leaf can't write to gets
// a new key per start.
func secretKey(dataDir string) []byte {
	path := filepath.Join(dataDir, ".leaf", "secret_key")
	if b, err := os.ReadFile(path); err == nil {
		if k := strings.TrimSpace(string(b)); len(k) >= 32 {
			return []byte(k)
		}
	}
	raw := make([]byte, 32)
	_, _ = rand.Read(raw)
	key := []byte(hex.EncodeToString(raw))
	if os.Mkdir(filepath.Dir(path), 0o777) == nil || isDir(filepath.Dir(path)) {
		_ = os.WriteFile(path, key, 0o600)
		_ = os.Chmod(path, 0o600) // WriteFile keeps an existing file's mode
	}
	return key
}

// The login session: a cookie signed with the secret key, valid for 30 days
// and renewed as it is used. It says who signed in: with users, their name;
// with the wiki's one password, no one in particular ("").
const (
	sessionCookie   = "session"
	sessionLifetime = 30 * 24 * time.Hour
)

var sessionKey []byte

type session struct {
	Auth   bool   `json:"auth"`
	Issued int64  `json:"iat"`
	User   string `json:"user,omitempty"`
}

func sign(payload string) string {
	m := hmac.New(sha256.New, sessionKey)
	m.Write([]byte(payload))
	return base64.RawURLEncoding.EncodeToString(m.Sum(nil))
}

// signedIn: the request carries a valid session cookie, user is who it
// belongs to; renew says it is old enough (a day) to be given a fresh one.
// With users, the session must name one of them, so a user taken out of
// the settings is signed out at once; without, it must name no one, so
// switching between a password and users signs everyone out.
func signedIn(r *http.Request) (user string, ok, renew bool) {
	c, err := r.Cookie(sessionCookie)
	if err != nil {
		return "", false, false
	}
	payload, sig, found := strings.Cut(c.Value, ".")
	if !found || !hmac.Equal([]byte(sig), []byte(sign(payload))) {
		return "", false, false
	}
	raw, err := base64.RawURLEncoding.DecodeString(payload)
	var s session
	if err != nil || json.Unmarshal(raw, &s) != nil || !s.Auth {
		return "", false, false
	}
	users := settings().Users
	if users != nil && users[s.User] == nil || users == nil && s.User != "" {
		return "", false, false
	}
	age := now().Sub(time.Unix(s.Issued, 0))
	return s.User, age < sessionLifetime, age > 24*time.Hour
}

// The cookie is Secure (HTTPS only) when the request came over HTTPS, so
// behind an HTTPS proxy the browser never sends it in the clear; over plain
// http (localhost, the LAN) it still works. A client faking the header only
// gets a cookie its own http requests leave out.
func setSession(w http.ResponseWriter, r *http.Request, user string) {
	raw, _ := json.Marshal(session{Auth: true, Issued: now().Unix(), User: user})
	payload := base64.RawURLEncoding.EncodeToString(raw)
	http.SetCookie(w, &http.Cookie{
		Name: sessionCookie, Value: payload + "." + sign(payload), Path: "/",
		Expires: now().Add(sessionLifetime), HttpOnly: true, Secure: isHTTPS(r),
		SameSite: http.SameSiteLaxMode,
	})
}

// The user a request is made by (users.go): the checks put the session's
// user, /mcp its token's, into the request's context. "": no users, or
// none signed in.
type userKey struct{}

func withUser(r *http.Request, user string) *http.Request {
	if user == "" {
		return r
	}
	return r.WithContext(context.WithValue(r.Context(), userKey{}, user))
}

func userOf(r *http.Request) string {
	if r == nil {
		return ""
	}
	u, _ := r.Context().Value(userKey{}).(string)
	return u
}

func clearSession(w http.ResponseWriter, r *http.Request) {
	http.SetCookie(w, &http.Cookie{Name: sessionCookie, Value: "", Path: "/", MaxAge: -1,
		HttpOnly: true, Secure: isHTTPS(r), SameSite: http.SameSiteLaxMode})
}

// sameSecret compares in constant time, as bytes, so non-ASCII passwords work.
func sameSecret(given, secret string) bool {
	return subtle.ConstantTimeCompare([]byte(given), []byte(secret)) == 1
}

// clientKey is who is guessing, for the throttle: the client's address.
// Behind a reverse proxy (the request comes from a loopback or private
// address) that is the address the proxy saw and appended last to
// X-Forwarded-For, so clients aren't all one.
func clientKey(r *http.Request) string {
	addr, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		addr = r.RemoteAddr
	}
	var forwarded []string
	for _, a := range strings.Split(r.Header.Get("X-Forwarded-For"), ",") {
		if a = strings.TrimSpace(a); a != "" {
			forwarded = append(forwarded, a)
		}
	}
	ip := net.ParseIP(addr)
	local := ip != nil && (ip.IsPrivate() || ip.IsLoopback() || ip.IsLinkLocalUnicast() || ip.IsUnspecified())
	if len(forwarded) > 0 && local {
		return forwarded[len(forwarded)-1]
	}
	return addr
}

// Throttle slows a guesser of passwords (and MCP tokens) down: after free
// failures in a row from one client, each further try waits, 1 minute,
// then doubling up to an hour. A success clears the client; a client quiet
// for a day is forgotten. Per client, so a guesser elsewhere doesn't lock
// the owner out. In memory: a restart forgets.
type Throttle struct {
	free           int
	first, longest time.Duration
	mu             sync.Mutex
	fails          map[string]failures
}

type failures struct {
	count int
	last  time.Time
}

func newThrottle() *Throttle {
	return &Throttle{free: 5, first: time.Minute, longest: time.Hour, fails: map[string]failures{}}
}

// wait is how long key must wait before it may try again (0: now).
func (t *Throttle) wait(key string) time.Duration {
	t.mu.Lock()
	f := t.fails[key]
	t.mu.Unlock()
	if f.count < t.free {
		return 0
	}
	delay := time.Duration(math.Min(float64(t.first)*math.Pow(2, float64(f.count-t.free)), float64(t.longest)))
	return max(0, f.last.Add(delay).Sub(now()))
}

func (t *Throttle) failed(key string) {
	n := now()
	t.mu.Lock()
	defer t.mu.Unlock()
	for k, f := range t.fails {
		if n.Sub(f.last) >= 24*time.Hour {
			delete(t.fails, k)
		}
	}
	t.fails[key] = failures{t.fails[key].count + 1, n}
}

func (t *Throttle) succeeded(key string) {
	t.mu.Lock()
	delete(t.fails, key)
	t.mu.Unlock()
}

var (
	loginThrottle = newThrottle()
	mcpThrottle   = newThrottle()
)

func waitMessage(d time.Duration) string {
	minutes := max(1, int(math.RoundToEven(d.Seconds()/60)))
	s := ""
	if minutes > 1 {
		s = "s"
	}
	return fmt.Sprintf("Too many wrong attempts. Try again in %d minute%s.", minutes, s)
}
