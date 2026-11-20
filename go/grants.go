// Connections (docs/spec/mcp.md, Connections): an assistant a user let in
// through OAuth (oauth.go). Each has an access token (an hour) and a
// refresh token (replaced at every use), kept only as hashes in
// .zone/grants (owner only, not committed: state, like the links), with
// whose it is, the client's name and where its answer went. The static MCP
// token stays in the settings file; these change by the hour, and a change
// here is no change of the settings.
package main

import (
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"time"
	"zonewiki/internal/settingsfile"
)

// How long a connection's tokens work: an access token an hour, a refresh
// token until it is used (a new one follows), at most refreshLifetime
// unused.
const (
	accessLifetime  = time.Hour
	refreshLifetime = 90 * 24 * time.Hour
)

// grant: a connection as .zone/grants keeps it.
type grant struct {
	ID             string    `json:"id"`
	User           string    `json:"user"`        // whose it is; "": the wiki's (no users)
	Client         string    `json:"client"`      // the client's name, as it gave it
	ClientHash     string    `json:"client_hash"` // hashToken of its client id: only it refreshes
	Redirect       string    `json:"redirect"`    // the host its answer went to
	less                     // what its user gave it less than they have
	Made           time.Time `json:"made"`            // when it was allowed
	Used           string    `json:"used"`            // the day it last called /mcp or refreshed
	Access         string    `json:"access"`          // hashToken of the access token
	AccessExpires  time.Time `json:"access_expires"`  //
	Refresh        string    `json:"refresh"`         // hashToken of the refresh token
	PrevRefresh    string    `json:"refresh_before"`  // the one before: seen again, it was copied
	RefreshExpires time.Time `json:"refresh_expires"` //
}

// less: what a connection was given less than its user has, as its user
// chose when they let it in (oauth.go): all zero, it acts as they do.
type less struct {
	ReadOnly  bool      `json:"read_only"`  // it reads and changes nothing
	NoPrivate bool      `json:"no_private"` // their private pages, notes and tasks aren't there for it
	Groups    *[]string `json:"groups"`     // of their groups only these (nil: all of them, also one they join later)
}

// asker: user through a connection that was given l less: who asks with
// its token.
func (l less) asker(user string) asker {
	a := asker{Name: user, ReadOnly: l.ReadOnly, NoPrivate: l.NoPrivate}
	if l.Groups != nil {
		a = a.WithGroups(*l.Groups)
	}
	return a
}

// sees: what a connection that sees less than its user does see, in words
// for the page ("the shared wiki, the group dev"); "" when it sees all
// they do.
func (l less) sees() string {
	if !l.NoPrivate && l.Groups == nil {
		return ""
	}
	parts := []string{"the shared wiki"}
	if !l.NoPrivate {
		parts = append(parts, "your private pages, notes and tasks")
	}
	switch {
	case l.Groups == nil:
		parts = append(parts, "your groups")
	case len(*l.Groups) == 1:
		parts = append(parts, "the group "+(*l.Groups)[0])
	case len(*l.Groups) > 1:
		parts = append(parts, "the groups "+strings.Join(*l.Groups, ", "))
	}
	return strings.Join(parts, "; ")
}

// beyond: l gives something had doesn't: it changes where had only reads,
// has the private place had hadn't, or a group had hadn't (every group,
// also one joined later, where had named some). What its user may take
// away without the password, and give only with it.
func (l less) beyond(had less) bool {
	switch {
	case had.ReadOnly && !l.ReadOnly, had.NoPrivate && !l.NoPrivate:
		return true
	case had.Groups == nil:
		return false
	case l.Groups == nil:
		return true
	}
	for _, g := range *l.Groups {
		if !slices.Contains(*had.Groups, g) {
			return true
		}
	}
	return false
}

func grantsPath() string { return statePath("grants") }

// grantsCache: the connections as the file at path had them when it was
// last read (stamp: its time and size), so a call of /mcp doesn't parse
// it. A command on the host writes the file too (zonewiki user remove).
var grantsCache struct {
	sync.Mutex
	path, stamp string
	all         []grant
}

// readGrants: the connections whose refresh token hasn't expired, oldest
// first. The slice is the cache's: not to be changed.
func readGrants() ([]grant, error) {
	path := grantsPath()
	st, err := os.Stat(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	stamp := fmt.Sprint(st.ModTime().UnixNano(), " ", st.Size())
	grantsCache.Lock()
	defer grantsCache.Unlock()
	if grantsCache.path != path || grantsCache.stamp != stamp {
		b, err := os.ReadFile(path)
		if err != nil {
			return nil, err
		}
		var all []grant
		if err := json.Unmarshal(b, &all); err != nil {
			return nil, fmt.Errorf("%s: %w", filepath.Base(stateDirIn(cfg.Data))+"/grants", err)
		}
		grantsCache.path, grantsCache.stamp, grantsCache.all = path, stamp, all
	}
	live := make([]grant, 0, len(grantsCache.all))
	for _, g := range grantsCache.all {
		if now().Before(g.RefreshExpires) {
			live = append(live, g)
		}
	}
	return live, nil
}

// grantsMu with the file lock: one change of the connections at a time,
// the server's and a command's.
var grantsMu sync.Mutex

// changeGrants reads the connections, lets change edit them, and writes
// them back whole (expired ones gone), owner-only.
func changeGrants(change func(grants []grant) ([]grant, error)) error {
	grantsMu.Lock()
	defer grantsMu.Unlock()
	if err := os.MkdirAll(filepath.Dir(grantsPath()), 0o777); err != nil {
		return err
	}
	unlock, err := lockFile(grantsPath() + ".lock")
	if err != nil {
		return err
	}
	defer unlock()
	grants, err := readGrants()
	if err != nil {
		return err
	}
	if grants, err = change(slices.Clone(grants)); err != nil {
		return err
	}
	if grants == nil {
		grants = []grant{}
	}
	b, _ := json.MarshalIndent(grants, "", "  ")
	err = settingsfile.Write(grantsPath(), string(b)+"\n")
	grantsCache.Lock()
	grantsCache.stamp = "" // read again: the file is new
	grantsCache.Unlock()
	return err
}

// errNoGrant: the token belongs to no connection (wrong, expired, revoked:
// the same).
var errNoGrant = errors.New("no such connection")

func sameHash(a, b string) bool {
	return a != "" && subtle.ConstantTimeCompare([]byte(a), []byte(b)) == 1
}

func grantDay() string { return now().Format("2006-01-02") }

// grantTokens: a connection's new pair of tokens, to hand out once.
type grantTokens struct{ Access, Refresh string }

// renew gives g a new pair of tokens; the refresh token before is
// remembered.
func (g *grant) renew() grantTokens {
	t := grantTokens{newToken(), newToken()}
	g.PrevRefresh = g.Refresh
	g.Access, g.AccessExpires = hashToken(t.Access), now().Add(accessLifetime).UTC().Truncate(time.Second)
	g.Refresh, g.RefreshExpires = hashToken(t.Refresh), now().Add(refreshLifetime).UTC().Truncate(time.Second)
	g.Used = grantDay()
	return t
}

// newGrant: a connection of user's for the client (its name, the hash of
// its id, the host its answer goes to; l: what it is given less than user
// has), its first tokens, and its id.
func newGrant(user, client, clientHash, redirect string, l less) (grantTokens, string, error) {
	g := grant{ID: newToken()[:16], User: user, Client: client, ClientHash: clientHash, Redirect: redirect, less: l,
		Made: now().UTC().Truncate(time.Second)}
	t := g.renew()
	g.PrevRefresh = ""
	return t, g.ID, changeGrants(func(grants []grant) ([]grant, error) { return append(grants, g), nil })
}

// refreshGrant: the connection's next pair of tokens for the refresh token
// given by the client (clientHash). A refresh token used a second time was
// copied (the client, or a thief, has a newer one): the connection ends.
// may: the user may still have one (else nothing changes).
func refreshGrant(refresh, clientHash string, may func(user string) bool) (t grantTokens, user string, err error) {
	hash := hashToken(refresh)
	reused := false
	err = changeGrants(func(grants []grant) ([]grant, error) {
		for i := range grants {
			g := &grants[i]
			switch {
			case sameHash(g.Refresh, hash) && sameHash(g.ClientHash, clientHash):
				if !may(g.User) {
					return nil, errNoGrant
				}
				t, user = g.renew(), g.User
				return grants, nil
			case sameHash(g.PrevRefresh, hash):
				reused = true
				logf("connection %s (%s, %s): a refresh token was used twice; the connection is ended",
					g.ID, g.Client, userLabel(g.User))
				return slices.Delete(grants, i, i+1), nil
			}
		}
		return nil, errNoGrant
	})
	if reused {
		return grantTokens{}, "", errNoGrant
	}
	return t, user, err
}

func userLabel(user string) string {
	if user == "" {
		return "the wiki's"
	}
	return user + "'s"
}

// grantOf: the connection the access token belongs to, nil if none (wrong
// or expired). The day it is used is kept, once a day.
func grantOf(access string) *grant {
	if access == "" {
		return nil
	}
	grants, err := readGrants()
	if err != nil {
		logf("%v", err)
		return nil
	}
	hash := hashToken(access)
	for i := range grants {
		g := grants[i]
		if !sameHash(g.Access, hash) || !now().Before(g.AccessExpires) {
			continue
		}
		if g.Used != grantDay() {
			err := changeGrants(func(grants []grant) ([]grant, error) {
				for i := range grants {
					if grants[i].ID == g.ID {
						grants[i].Used = grantDay()
					}
				}
				return grants, nil
			})
			if err != nil {
				logf("%v", err)
			}
		}
		return &g
	}
	return nil
}

// revokeGrants ends the connections gone says to; how many there were.
func revokeGrants(gone func(g grant) bool) (n int, err error) {
	if _, statErr := os.Stat(grantsPath()); errors.Is(statErr, os.ErrNotExist) {
		return 0, nil // none were ever made: no file for it
	}
	err = changeGrants(func(grants []grant) ([]grant, error) {
		before := len(grants)
		grants = slices.DeleteFunc(grants, gone)
		n = before - len(grants)
		return grants, nil
	})
	return n, err
}

// revokeGrantsOf ends every connection of user's (they were removed, or
// set a new password by a reset link); a failure is logged.
func revokeGrantsOf(user string) int {
	n, err := revokeGrants(func(g grant) bool { return g.User == user })
	if err != nil {
		logf("ending %s connections: %v", userLabel(user), err)
	}
	return n
}

// grantRow: a connection as Connect an assistant lists it.
type grantRow struct {
	ID, Client, Redirect, Made, Used string
	ReadOnly                         bool
	Sees                             string // what it sees, when that is less than its user does ("": all of it; less.sees)
	had                              less   // what it was given less than its user has
	Reach                            reach  // Change…'s choices (assistantData)
	Open                             bool   // Change… comes back open: a change was refused (Error)
	Error                            string // why
}

// userGrant: the connection id of user's, if there is one.
func userGrant(user, id string) (grant, bool) {
	grants, err := readGrants()
	if err != nil {
		logf("%v", err)
	}
	for _, g := range grants {
		if g.ID == id && g.User == user {
			return g, true
		}
	}
	return grant{}, false
}

// errGrantChanged: a connection isn't as it was when its user chose what
// to change (another tab changed it meanwhile).
var errGrantChanged = errors.New("that connection was changed meanwhile: look at what it has now, and choose again")

// reachGrant gives the connection id of user's what to says it is given
// less than its user has, if it still has had (as its user saw it when
// they chose: else errGrantChanged); found false: none such. Its tokens
// stay; the next call with them is asked with the new reach
// (connectionUser), as is every refresh. Whether the change may be made
// (beyond, and the password) is for the caller to have asked.
func reachGrant(user, id string, had, to less) (found bool, err error) {
	err = changeGrants(func(grants []grant) ([]grant, error) {
		for i := range grants {
			g := &grants[i]
			if g.ID != id || g.User != user {
				continue
			}
			if g.less.beyond(had) || had.beyond(g.less) {
				return nil, errGrantChanged
			}
			g.less, found = to, true
			return grants, nil
		}
		return nil, errNoGrant
	})
	if errors.Is(err, errNoGrant) {
		return false, nil
	}
	return found, err
}

// grantsOf: user's connections, for the page, newest first.
func grantsOf(user string) []grantRow {
	grants, err := readGrants()
	if err != nil {
		logf("%v", err)
	}
	var rows []grantRow
	for _, g := range slices.Backward(grants) {
		if g.User == user {
			used := g.Used
			if day, err := time.Parse("2006-01-02", g.Used); err == nil {
				used = day.Format("2 Jan 2006")
			}
			rows = append(rows, grantRow{ID: g.ID, Client: g.Client, Redirect: g.Redirect,
				Made: g.Made.Local().Format("2 Jan 2006"), Used: used, ReadOnly: g.ReadOnly, Sees: g.sees(), had: g.less})
		}
	}
	return rows
}
