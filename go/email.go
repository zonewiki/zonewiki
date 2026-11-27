// A user's mail address (/email, docs/spec/users.md, Mail addresses): a
// user sets and removes their own in the browser, with their password
// again, as their password and token are changed (password.go,
// assistant.go); an admin sets anyone's on the Users tab. No mail goes to
// an address until its owner confirmed it: an address set in the browser
// is kept as email-pending in the settings file, Zonewiki mails it one link,
// and the link, opened by that user signed in and its button pressed,
// makes it their email, the address every mail asks for (mails in
// notify.go). An address set on the host (zonewiki user email, the file
// by hand) or by sign-up is confirmed as it is.
//
// The link holds nothing to keep: when it ends, and a MAC over the user,
// the address and that time (sign, the session key). It is good for the
// address it was sent to alone, and once: confirming takes the pending
// address away.
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"html/template"
	"net/http"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"
	"zonewiki/internal/settingsfile"
)

// How long a confirmation link works.
const confirmLifetime = 24 * time.Hour

// Confirmation links a wiki mails, at most, whoever asks: a text Zonewiki
// fixes, to an address a person chose, is still a mail to a stranger.
const (
	confirmsPerUserDay  = 3
	confirmsPerWikiHour = 10
)

func emailRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /email", emailPage)
	mux.HandleFunc("POST /email", emailRoute)
	mux.HandleFunc("POST /email/resend", emailResendRoute)
	mux.HandleFunc("GET /email/confirm/{token}", emailConfirmRoute)
	mux.HandleFunc("POST /email/confirm/{token}", emailConfirmRoute)
	mux.HandleFunc("POST /admin/users/{name}/email", adminOnly(userEmailRoute))
}

// emailView: the page (email.html).
type emailView struct {
	view
	BreadcrumbHTML template.HTML
	User           string
	Shared         bool   // a shared account's: not set here (sharedAccount)
	Email          string // the confirmed address, "" none
	Pending        string // the address waiting for its owner's confirmation
	Done, Error    string
	// The confirmation page (/email/confirm/…): Confirm is the address the
	// link is for; Dead: the link doesn't work for who is signed in.
	Confirm string
	Dead    bool
}

// emailOwner: whose address the request is about, as the latest settings
// have them. ok false: there is nobody to mail (no users, a visitor, a
// user no longer there) or no mail to send.
func emailOwner(r *http.Request) (s *Settings, u *User, ok bool) {
	s = latestSettings()
	if !cfg.Mail.on() || s.Users == nil {
		return s, nil, false
	}
	u = s.Users[userOf(r).Name]
	return s, u, u != nil
}

// hasMailTab: the settings pages have a Mail tab for who asks: a user of a
// wiki that sends mail.
func hasMailTab(rq *Req) bool {
	return cfg.Mail.on() && settings().Users[rq.user.Name] != nil
}

func sharedEmailMsg(name string) string {
	return name + " is an account several people sign in to: its mail address is set by whoever runs this wiki, not here."
}

func showEmail(w http.ResponseWriter, r *http.Request, status int, u *User, done, refused string) {
	rq := newReq(r)
	// As the latest settings have them: a change made a moment ago may
	// still wait for the requests running.
	if latest := latestSettings().Users[u.Name]; latest != nil {
		u = latest
	}
	v := &emailView{User: u.Name, Shared: u.Shared, Email: u.Email, Pending: u.EmailPending, Done: done, Error: refused,
		BreadcrumbHTML: breadcrumb(rq, settingsCrumb(rq, "Mail"), "", false, "", "")}
	page(w, rq, status, "email.html", "Your mail address", shell{settings: "email"}, v)
}

// emailDone: what ?done= says the change before did, from a fixed set (a
// link can't make the page say anything else).
var emailDone = map[string]string{
	"sent":      "Check your mail: Zonewiki sent a link to confirm the address.",
	"unsent":    "The address is kept, but Zonewiki sent no link: too many were sent lately. Send the link again later.",
	"failed":    "The address is kept, but Zonewiki couldn't send the link. Send the link again later.",
	"removed":   "Your mail address is removed: Zonewiki sends you no mail.",
	"confirmed": "Your mail address is confirmed.",
	"same":      "That is your address already.",
}

// emailPage: GET /email.
func emailPage(w http.ResponseWriter, r *http.Request) {
	_, u, ok := emailOwner(r)
	if !ok {
		http.NotFound(w, r)
		return
	}
	showEmail(w, r, http.StatusOK, u, emailDone[r.URL.Query().Get("done")], "")
}

// cleanEmail: an address as it is kept: lowercase, as the settings file's.
func cleanEmail(s string) string { return strings.ToLower(strings.TrimSpace(s)) }

// setPending writes name's address to confirm into the settings file
// (email "": none at all), taking the confirmed one out: what a user's
// mail goes to is never an address they replaced.
func setPending(by, name, email string) error {
	return changeSettings(by, func(f *settingsfile.File) error {
		if f.User(name) == nil {
			return errors.New("there is no user " + name + " any more.")
		}
		f.Unset(name, "email")
		if email == "" {
			f.Unset(name, "email-pending")
		} else {
			f.Set(name, "email-pending", email)
		}
		return nil
	})
}

// emailRoute: POST /email: the address (email) and the password they have
// (password); remove=1 takes the address out.
func emailRoute(w http.ResponseWriter, r *http.Request) {
	s, u, ok := emailOwner(r)
	if !ok {
		http.NotFound(w, r)
		return
	}
	if u.Shared {
		// Before the password is looked at, as its password (password.go).
		showEmail(w, r, http.StatusForbidden, u, "", sharedEmailMsg(u.Name))
		return
	}
	right, wait := passwordAgain(r, s, u.Name)
	if wait > 0 {
		showEmail(w, r, http.StatusTooManyRequests, u, "", waitMessage(wait))
		return
	}
	email, remove := cleanEmail(r.PostFormValue("email")), r.PostFormValue("remove") != ""
	switch {
	case !right:
		showEmail(w, r, http.StatusForbidden, u, "", "Incorrect password: nothing was changed.")
		return
	case remove:
		email = ""
	case !validEmail(email):
		showEmail(w, r, http.StatusBadRequest, u, "", "That isn't a mail address. Write it as name@example.org.")
		return
	case email == u.Email:
		http.Redirect(w, r, "/email?done=same", http.StatusSeeOther)
		return
	}
	if err := setPending(u.Name, u.Name, email); err != nil {
		showEmail(w, r, http.StatusConflict, u, "", err.Error())
		return
	}
	if remove {
		http.Redirect(w, r, "/email?done=removed", http.StatusSeeOther)
		return
	}
	http.Redirect(w, r, "/email?done="+mailConfirm(s, r, u.Name, email), http.StatusSeeOther)
}

// emailResendRoute: POST /email/resend mails the link for the address that
// waits once more. No password: it sends what was asked for with one, to
// the address given then, and at most confirmsPerUserDay a day.
func emailResendRoute(w http.ResponseWriter, r *http.Request) {
	s, u, ok := emailOwner(r)
	if !ok || u.EmailPending == "" {
		http.NotFound(w, r)
		return
	}
	http.Redirect(w, r, "/email?done="+mailConfirm(s, r, u.Name, u.EmailPending), http.StatusSeeOther)
}

// confirmToken: the token of a link that confirms email as name's until
// expires.
func confirmToken(name, email string, expires time.Time) string {
	at := strconv.FormatInt(expires.Unix(), 10)
	// The name's length first: no name and address read as another pair.
	return at + "." + sign(fmt.Sprintf("email-confirm:%d:%s\n%s\n%s", len(name), name, email, at))
}

// confirms: token is a link Zonewiki made for name and email that still
// works.
func confirms(token, name, email string) bool {
	at, _, found := strings.Cut(token, ".")
	expires, err := strconv.ParseInt(at, 10, 64)
	if !found || err != nil || email == "" || !now().Before(time.Unix(expires, 0)) {
		return false
	}
	return sameSecret(token, confirmToken(name, email, time.Unix(expires, 0)))
}

// mailConfirm mails email the link that confirms it as name's, unless the
// wiki sent too many; what happened, as the page says it (emailDone):
// "sent", "unsent" or "failed".
func mailConfirm(s *Settings, r *http.Request, name, email string) string {
	ok, err := countConfirm(name)
	if err != nil {
		logf("counting the confirmation links: %v", err)
		return "failed"
	}
	if !ok {
		logf("no confirmation link to %s for %s: the wiki sent too many lately", email, name)
		return "unsent"
	}
	link := siteURL(s.BaseURL, r, "/email/confirm/"+confirmToken(name, email, now().Add(confirmLifetime)))
	where := siteURL(s.BaseURL, r, "")
	err = sendMail(cfg.Mail, email, "Confirm your mail address for "+appName, fmt.Sprintf(
		"Someone, maybe you, gave this address as the mail address of the user %s on %s (%s).\n\n"+
			"To confirm that %s may be mailed here, open this link within 24 hours, signed in as %s, and press Confirm:\n\n%s\n\n"+
			"If it wasn't you, you can ignore this mail: no other mail is sent here unless the address is confirmed.\n",
		name, appName, where, name, name, link))
	if err != nil {
		logf("confirmation link to %s for %s: %v", email, name, err)
		return "failed"
	}
	return "sent"
}

// emailConfirmRoute: /email/confirm/<token>. Opening it changes nothing
// (mail scanners open links); its button confirms. Signed out, the login
// page comes first and comes back here.
func emailConfirmRoute(w http.ResponseWriter, r *http.Request) {
	_, u, ok := emailOwner(r)
	if !ok {
		http.NotFound(w, r)
		return
	}
	rq := newReq(r)
	v := &emailView{User: u.Name, BreadcrumbHTML: breadcrumb(rq, []crumb{{"Confirm your mail address", ""}}, "", false, "", "")}
	show := func(status int) { page(w, rq, status, "email.html", "Confirm your mail address", shell{}, v) }
	if !confirms(r.PathValue("token"), u.Name, u.EmailPending) {
		v.Dead = true
		show(http.StatusNotFound)
		return
	}
	v.Confirm = u.EmailPending
	if r.Method != http.MethodPost {
		show(http.StatusOK)
		return
	}
	email := u.EmailPending
	err := changeSettings(u.Name, func(f *settingsfile.File) error {
		fu := f.User(u.Name)
		if fu == nil || cleanEmail(fu.Values["email-pending"].Value) != email {
			return errors.New("the address was changed meanwhile: this link is for " + email + ".")
		}
		for _, other := range f.Users {
			if other.Name != u.Name && strings.EqualFold(other.Values["email"].Value, email) {
				return errors.New("another user of this wiki has the address " + email + " already: an address is one user's.")
			}
		}
		f.Set(u.Name, "email", email)
		f.Unset(u.Name, "email-pending")
		return nil
	})
	if err != nil {
		v.Error = err.Error()
		show(http.StatusConflict)
		return
	}
	http.Redirect(w, r, "/email?done=confirmed", http.StatusSeeOther)
}

// userEmailRoute: POST /admin/users/{name}/email: an admin sets a user's
// address (email; empty: none). It counts as not confirmed, the admin's
// own too: the address gets the link, and its owner confirms.
func userEmailRoute(w http.ResponseWriter, r *http.Request) {
	u := targetUser(w, r)
	if u == nil {
		return
	}
	email := cleanEmail(r.PostFormValue("email"))
	switch {
	case email != "" && !validEmail(email):
		showUsers(w, r, http.StatusBadRequest, "", fmt.Sprintf("%q isn't a mail address (name@example.org).", email))
		return
	case email == u.Email && (email != "" || u.EmailPending == ""), email != "" && email == u.EmailPending:
		afterChange(w, r, "", u.Name) // as it is
		return
	}
	if err := setPending(userOf(r).Name, u.Name, email); err != nil {
		refuseChange(w, r, err)
		return
	}
	done := "email-removed"
	if email != "" {
		done = "email-kept"
		if cfg.Mail.on() {
			done = "email-" + mailConfirm(latestSettings(), r, u.Name, email)
		}
	}
	afterChange(w, r, done, u.Name)
}

// usersEmailDone: what the Users tab says after an admin set name's
// address (?done=email-…).
func usersEmailDone(done, name string) string {
	switch done {
	case "email-sent":
		return "Sent " + name + " a link to confirm the address: no mail goes to it until they did."
	case "email-unsent":
		return name + "'s address is kept, but no link was sent: too many were sent lately. They send it again on their Mail settings."
	case "email-failed":
		return name + "'s address is kept, but Zonewiki couldn't send the link. They send it again on their Mail settings."
	case "email-kept":
		return name + "'s address is kept, not confirmed: this wiki sends no mail."
	case "email-removed":
		return "Removed " + name + "'s mail address: Zonewiki sends them no mail."
	}
	return ""
}

// The confirmation links sent (.zone/confirms, a JSON list of who for and
// when, owner-only and not committed): kept in a file, since a Zonewiki
// that sleeps when idle forgets what is in memory, and its caps would
// start anew with each start.
type confirmSent struct {
	User string    `json:"user"`
	At   time.Time `json:"at"`
}

func confirmsPath() string { return statePath("confirms") }

var confirmsMu sync.Mutex

// countConfirm counts a confirmation link for name; false when name had
// confirmsPerUserDay in the last day, or the wiki confirmsPerWikiHour in
// the last hour.
func countConfirm(name string) (bool, error) {
	confirmsMu.Lock()
	defer confirmsMu.Unlock()
	if err := os.MkdirAll(filepath.Dir(confirmsPath()), 0o777); err != nil {
		return false, err
	}
	unlock, err := lockFile(confirmsPath() + ".lock")
	if err != nil {
		return false, err
	}
	defer unlock()
	var sent []confirmSent
	if b, err := os.ReadFile(confirmsPath()); err == nil {
		_ = json.Unmarshal(b, &sent) // a broken file counts as none: it is rewritten
	} else if !errors.Is(err, os.ErrNotExist) {
		return false, err
	}
	t := now()
	sent = slices.DeleteFunc(sent, func(c confirmSent) bool { return t.Sub(c.At) >= 24*time.Hour })
	user, hour := 0, 0
	for _, c := range sent {
		if c.User == name {
			user++
		}
		if t.Sub(c.At) < time.Hour {
			hour++
		}
	}
	if user >= confirmsPerUserDay || hour >= confirmsPerWikiHour {
		return false, nil
	}
	sent = append(sent, confirmSent{User: name, At: t.UTC().Truncate(time.Second)})
	b, _ := json.MarshalIndent(sent, "", "  ")
	return true, settingsfile.Write(confirmsPath(), string(b)+"\n")
}
