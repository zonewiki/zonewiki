// Signed-out sessions (docs/spec/security.md, Signing out): a session is a
// signed cookie, valid wherever a copy of it is, so signing out has to be
// remembered here. Zone keeps the ids of the sessions signed out in
// .zone/signed-out (owner only, not committed: state, like the session
// key), each until no cookie of that session can still be valid.
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"
	"zonewiki/internal/settingsfile"
)

// signedOut: the sessions signed out, by id, each with the time until
// which it is kept; in memory as the file at path has them (only the
// server writes it).
var signedOut struct {
	sync.Mutex
	path  string
	until map[string]time.Time
}

func signedOutPath() string { return statePath("signed-out") }

// loadSignedOut reads the file once per data folder; the caller holds the lock.
func loadSignedOut() {
	path := signedOutPath()
	if signedOut.until != nil && signedOut.path == path {
		return
	}
	signedOut.path, signedOut.until = path, map[string]time.Time{}
	b, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return
	}
	if err == nil {
		err = json.Unmarshal(b, &signedOut.until)
	}
	if err != nil {
		// Unreadable: those sessions work again, which is said, not
		// a reason to refuse everyone.
		fmt.Fprintf(os.Stderr, "Zone: %s: %v\n", filepath.Base(stateDirIn(cfg.Data))+"/signed-out", err)
		signedOut.until = map[string]time.Time{}
	}
}

// isSignedOut: the session with this id was signed out.
func isSignedOut(id string) bool {
	signedOut.Lock()
	defer signedOut.Unlock()
	loadSignedOut()
	until, found := signedOut.until[id]
	return found && now().Before(until)
}

// signOut ends the session with this id, wherever a cookie of it is: kept
// for a session's lifetime, after which every cookie of it issued until
// now has expired (and none was issued since: a signed-out one isn't
// renewed). Those past their time are dropped from the file.
func signOut(id string) error {
	signedOut.Lock()
	defer signedOut.Unlock()
	loadSignedOut()
	for old, until := range signedOut.until {
		if !now().Before(until) {
			delete(signedOut.until, old)
		}
	}
	signedOut.until[id] = now().Add(sessionLifetime).UTC().Truncate(time.Second)
	if err := os.MkdirAll(filepath.Dir(signedOut.path), 0o777); err != nil {
		return err
	}
	b, _ := json.MarshalIndent(signedOut.until, "", "  ")
	return settingsfile.Write(signedOut.path, string(b)+"\n")
}
