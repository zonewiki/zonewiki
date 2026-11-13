// Sign-up (docs/spec/users.md, Sign-up): with ZONE_SIGNUP on in the
// deployment, a mail server (mail.go), users and base-url, anyone may ask
// for an account at /signup with their mail address. Zone mails them a
// link (a welcome link of kind "signup", links.go); on its page they
// choose a user name and a password, and the account is made with the
// settings' signup-role, or waits for an admin (signup-approval). The
// address is confirmed before any account exists, and the form says the
// same whatever the address: it tells no one who has an account.
package main

import (
	"errors"
	"fmt"
	"net/http"
	"net/mail"
	"regexp"
	"slices"
	"strings"
	"sync"
	"time"
	"zonewiki/internal/settingsfile"
)

// How long a sign-up link works.
const signupLifetime = 24 * time.Hour

// Mails sent for sign-ups, at most: per client, and in all, in an hour
// (in memory: a restart forgets), so the form can't be used to flood
// someone's inbox or the mail server's quota; and sign-up links open at
// once, so .zone/links stays small.
const (
	signupsPerClient = 5
	signupsPerHour   = 50
	signupsOpen      = 200
)

var (
	signupsByClient = newHourlyCap(signupsPerClient)
	signupsInAll    = newHourlyCap(signupsPerHour)
)

// signupOn: people may sign up: the deployment allows it (ZONE_SIGNUP,
// with a mail server), the wiki has users, and base-url says the wiki's
// address, which the mail's link is on (never the address a request came
// in on: a forged Host would send the link elsewhere).
func signupOn(s *Settings) bool {
	return cfg.Signup && cfg.Mail.Host != "" && s.Users != nil && s.BaseURL != ""
}

// signupOffReason: why sign-up is off though the deployment allows it,
// for the log at start; "" if it isn't off for a reason in the settings.
func signupOffReason(s *Settings) string {
	switch {
	case !cfg.Signup:
		return ""
	case s.Users == nil:
		return "sign-up (ZONE_SIGNUP) is off: the wiki has no users. Add the first with: zonewiki user add NAME"
	case s.BaseURL == "":
		return "sign-up (ZONE_SIGNUP) is off: set base-url, the address the mail's link is on " +
			"(zonewiki config set base-url https://wiki.example.org)"
	}
	return ""
}

// signupWho: " with an address at a.org or b.org" for the log, "" for
// any address.
func signupWho(s *Settings) string {
	if len(s.SignupDomains) == 0 {
		return ""
	}
	return " with an address at " + orList(s.SignupDomains)
}

var domainRE = regexp.MustCompile(`^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z][a-z0-9-]*[a-z0-9]$`)

// validEmail: a bare address, user@domain, nothing else (no name, no
// comments), as a mail header can carry it.
func validEmail(s string) bool {
	if len(s) > 254 || strings.ContainsAny(s, " \t\r\n<>\"(),;:\\[]") {
		return false
	}
	a, err := mail.ParseAddress(s)
	if err != nil || a.Name != "" || a.Address != s {
		return false
	}
	_, domain, _ := strings.Cut(s, "@")
	return domainRE.MatchString(strings.ToLower(domain))
}

// domainAllowed: the address is in one of the signup-domains (or there
// are none).
func domainAllowed(s *Settings, email string) bool {
	if len(s.SignupDomains) == 0 {
		return true
	}
	_, domain, _ := strings.Cut(email, "@")
	return slices.Contains(s.SignupDomains, domain)
}

// userWithEmail: the user whose address email is, nil if none.
func userWithEmail(s *Settings, email string) *User {
	for _, name := range sortedKeys(s.Users) {
		if u := s.Users[name]; u.Email != "" && strings.EqualFold(u.Email, email) {
			return u
		}
	}
	return nil
}

// hourlyCap counts what a key did in the last hour, up to a limit.
type hourlyCap struct {
	sync.Mutex
	limit int
	by    map[string][]time.Time
}

func newHourlyCap(limit int) *hourlyCap {
	return &hourlyCap{limit: limit, by: map[string][]time.Time{}}
}

// take counts one for key; false when key had limit in the last hour.
func (c *hourlyCap) take(key string) bool {
	c.Lock()
	defer c.Unlock()
	recent := slices.DeleteFunc(c.by[key], func(t time.Time) bool { return now().Sub(t) >= time.Hour })
	if len(recent) >= c.limit {
		c.by[key] = recent
		return false
	}
	c.by[key] = append(recent, now())
	return true
}

// signupView: the sign-up page (signup.html).
type signupView struct {
	view
	Email, Error string
	Sent         bool // the mail went out: check your inbox
	Domains      []string
}

func signupRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /signup", signupRoute)
	mux.HandleFunc("POST /signup", signupRoute)
}

// signupRoute: GET /signup shows the form, POST /signup mails the link.
func signupRoute(w http.ResponseWriter, r *http.Request) {
	s := settings()
	if !signupOn(s) {
		http.NotFound(w, r)
		return
	}
	if _, ok, _ := signedIn(r); ok {
		http.Redirect(w, r, "/", http.StatusFound)
		return
	}
	v := &signupView{Domains: s.SignupDomains}
	status := http.StatusOK
	if r.Method == http.MethodPost {
		v.Email = strings.TrimSpace(r.PostFormValue("email"))
		if local, domain, ok := strings.Cut(v.Email, "@"); ok {
			v.Email = local + "@" + strings.ToLower(domain)
		}
		switch {
		case !validEmail(v.Email):
			v.Error, status = "That isn't a mail address. Write it as name@example.org.", http.StatusBadRequest
		case !domainAllowed(s, v.Email):
			v.Error, status = "This wiki takes sign-ups only with an address at "+orList(s.SignupDomains)+".",
				http.StatusBadRequest
		case !signupsByClient.take(clientKey(r)) || !signupsInAll.take(""):
			v.Error, status = "Too many sign-ups in the last hour: try again later.", http.StatusTooManyRequests
		default:
			if err := mailSignup(s, v.Email); err != nil {
				logf("sign-up mail to %s: %v", v.Email, err)
				v.Error, status = "Zone couldn't send the mail: try again later.", http.StatusBadGateway
				if errors.Is(err, errTooManySignups) {
					v.Error, status = "Too many sign-ups are waiting: try again later.", http.StatusTooManyRequests
				}
				break
			}
			v.Sent = true
		}
	}
	page(w, newReq(r), status, "signup.html", "Sign up", shell{noActions: true}, v)
}

var errTooManySignups = errors.New("too many sign-up links open")

// mailSignup mails email its sign-up link; to an address that has an
// account already, a reminder of its user name instead, so the form says
// the same either way.
func mailSignup(s *Settings, email string) error {
	app := appName
	if u := userWithEmail(s, email); u != nil {
		return sendMail(cfg.Mail, email, "Your account on "+app, fmt.Sprintf(
			"Someone, maybe you, asked for an account on %s (%s) with this address.\n\n"+
				"You have one already: your user name is %s. Sign in at %s/login\n\n"+
				"If you forgot your password, ask an admin of the wiki for a new one.\n"+
				"If it wasn't you, you can ignore this mail.\n", app, s.BaseURL, u.Name, s.BaseURL))
	}
	token := newToken()
	err := changeLinks(func(links []welcomeLink) ([]welcomeLink, error) {
		links = slices.DeleteFunc(links, func(l welcomeLink) bool { return l.Kind == "signup" && strings.EqualFold(l.Email, email) })
		open := 0
		for _, l := range links {
			if l.Kind == "signup" {
				open++
			}
		}
		if open >= signupsOpen {
			return nil, errTooManySignups
		}
		return append(links, welcomeLink{Hash: hashToken(token), Kind: "signup", Email: email,
			Expires: now().Add(signupLifetime).UTC().Truncate(time.Second)}), nil
	})
	if err != nil {
		return err
	}
	err = sendMail(cfg.Mail, email, "Confirm your account on "+app, fmt.Sprintf(
		"Someone, maybe you, asked for an account on %s (%s) with this address.\n\n"+
			"To make it, open this link within 24 hours and choose your user name and password:\n\n%s\n\n"+
			"If it wasn't you, you can ignore this mail: no account is made.\n",
		app, s.BaseURL, linkURL(s.BaseURL, nil, token)))
	if err != nil {
		_ = changeLinks(func(links []welcomeLink) ([]welcomeLink, error) {
			return slices.DeleteFunc(links, func(l welcomeLink) bool { return l.Hash == hashToken(token) }), nil
		})
	}
	return err
}

// orList: "a.org", "a.org or b.org", "a.org, b.org or c.org".
func orList(items []string) string {
	if len(items) < 2 {
		return strings.Join(items, "")
	}
	return strings.Join(items[:len(items)-1], ", ") + " or " + items[len(items)-1]
}

// useSignup: the account a sign-up link makes, named name with password,
// in the settings file (logged as theirs), and the link gone. It returns
// the password's hash, for the session, and whether the account waits for
// an admin. A name that is taken, or isn't one, is refused with a
// message for the form, and the link stays.
func useSignup(link *welcomeLink, name, password string) (hash string, pending bool, err error) {
	s := latestSettings()
	links, _ := readLinks()
	switch {
	case !userNameRE.MatchString(name):
		return "", false, refuse("Use lowercase letters, digits and hyphens for your user name (e.g. alice, mary-ann).")
	case reservedNames[name]:
		return "", false, refuse(reservedNameMsg(name))
	case s.Users[name] != nil, linkFor(links, name) != nil:
		return "", false, refuse("The user name " + name + " is taken: choose another.")
	}
	hash, pending = hashPassword(password), s.SignupApproval
	err = changeSettings(name, func(f *settingsfile.File) error {
		switch {
		case len(f.Users) == 0:
			return errors.New("this wiki has no users any more, so it takes no sign-ups.")
		case f.User(name) != nil:
			return refuse("The user name " + name + " is taken: choose another.")
		}
		for _, u := range f.Users {
			if strings.EqualFold(u.Values["email"].Value, link.Email) {
				return refuse("There is an account with your address already: sign in with it.")
			}
		}
		f.Set(name, "password", hash)
		if s.SignupRole != roleEditor {
			f.Set(name, "role", s.SignupRole)
		}
		f.Set(name, "email", link.Email)
		if pending {
			f.Set(name, "pending", "yes")
		}
		return nil
	})
	if err != nil {
		return "", false, err
	}
	if err := changeLinks(func(links []welcomeLink) ([]welcomeLink, error) {
		return slices.DeleteFunc(links, func(l welcomeLink) bool { return l.Hash == link.Hash }), nil
	}); err != nil {
		logf("removing the sign-up link for %s: %v", link.Email, err)
	}
	return hash, pending, nil
}
