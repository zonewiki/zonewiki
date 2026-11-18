// Package asker: who is asking. Every question about who may see or change
// something is asked for an Asker, not for a user's name: a user, or
// something acting for them that was given less than they have (an
// assistant's connection). An Asker never has more than its user.
//
// Name says who it is: whose pages and tasks are its own, who a change is
// by. What it may see and change is asked of the Asker itself, where the
// rules are (go/access.go), so a limit holds at every door.
package asker

import "encoding/json"

// Asker is whoever asks. Name is the user ("" in a wiki without users, and
// for Zone itself). The rest is what it was given less than its user has,
// each only ever taking away; all zero: the user themselves, with all they
// may.
type Asker struct {
	Name string
	// ReadOnly: it reads what its user sees and changes nothing (an
	// assistant's connection its user let only read).
	ReadOnly bool
}

// User: the user name themselves, with all they may: a person in the
// browser, and what Zone asks about a user it knows by name (whom a task is
// given to, who gets a mail).
func User(name string) Asker { return Asker{Name: name} }

// String: the name, so a log line or a message that is handed an Asker
// names the user.
func (a Asker) String() string { return a.Name }

// MarshalJSON: the name, so an Asker put into an answer by mistake reads as
// what was meant, never as its fields.
func (a Asker) MarshalJSON() ([]byte, error) { return json.Marshal(a.Name) }
