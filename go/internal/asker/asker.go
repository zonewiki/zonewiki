// Package asker: who is asking. Every question about who may see or change
// something is asked for an Asker, not for a user's name: a user, or
// something acting for them that was given less than they have (an
// assistant's connection). An Asker never has more than its user.
//
// Name says who it is: whose pages and tasks are its own, who a change is
// by. What it may see and change is asked of the Asker itself, where the
// rules are (go/access.go), so a limit holds at every door.
package asker

import (
	"encoding/json"
	"slices"
	"strings"
)

// Asker is whoever asks. Name is the user ("" in a wiki without users, and
// for Zonewiki itself). The rest is what it was given less than its user has,
// each only ever taking away; all zero: the user themselves, with all they
// may.
type Asker struct {
	Name string
	// ReadOnly: it reads what its user sees and changes nothing (an
	// assistant's connection its user let only read).
	ReadOnly bool
	// NoPrivate: it wasn't given its user's private place: their private
	// pages, notes and tasks are not there for it.
	NoPrivate bool
	// OnlyGroups: of its user's groups it was given only those in Groups
	// (space-separated; none: no group's pages and lists). False: every
	// group its user is in, also one they join later.
	OnlyGroups bool
	Groups     string
}

// HasPrivate: its user's private place is there for it.
func (a Asker) HasPrivate() bool { return !a.NoPrivate }

// HasGroup: the group's place is there for it, if its user is in the group
// (which is asked where the groups are: this only ever takes away).
func (a Asker) HasGroup(group string) bool {
	return !a.OnlyGroups || slices.Contains(strings.Fields(a.Groups), group)
}

// Narrowed: it was given less than its user sees (not: less than they may
// change, ReadOnly).
func (a Asker) Narrowed() bool { return a.NoPrivate || a.OnlyGroups }

// WithGroups: a given only these of its user's groups.
func (a Asker) WithGroups(groups []string) Asker {
	groups = slices.Clone(groups)
	slices.Sort(groups)
	a.OnlyGroups, a.Groups = true, strings.Join(slices.Compact(groups), " ")
	return a
}

// User: the user name themselves, with all they may: a person in the
// browser, and what Zonewiki asks about a user it knows by name (whom a task is
// given to, who gets a mail).
func User(name string) Asker { return Asker{Name: name} }

// String: the name, so a log line or a message that is handed an Asker
// names the user.
func (a Asker) String() string { return a.Name }

// MarshalJSON: the name, so an Asker put into an answer by mistake reads as
// what was meant, never as its fields.
func (a Asker) MarshalJSON() ([]byte, error) { return json.Marshal(a.Name) }
