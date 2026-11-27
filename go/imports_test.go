package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// sweepImports removes an import past its hour and an upload cut off long
// ago, and keeps one within its hour, an upload under way and one being
// imported, whatever its age.
func TestSweepImports(t *testing.T) {
	testWiki(t, nil)
	at := time.Date(2026, 11, 26, 12, 0, 0, 0, time.UTC)
	now = func() time.Time { return at }
	t.Cleanup(func() { now = wikiNow })
	put := func(id string, uploaded time.Time, meta bool) {
		t.Helper()
		dir := filepath.Join(importsDir(), id)
		if err := os.MkdirAll(filepath.Join(dir, "notes"), 0o700); err != nil {
			t.Fatal(err)
		}
		if meta {
			b, _ := json.Marshal(waitingImport{User: "alice", Folder: "v", At: uploaded})
			if err := os.WriteFile(filepath.Join(dir, "import.json"), b, 0o600); err != nil {
				t.Fatal(err)
			}
		}
		if err := os.Chtimes(dir, uploaded, uploaded); err != nil {
			t.Fatal(err)
		}
	}
	id := func(c byte) string {
		b := make([]byte, 32)
		for i := range b {
			b[i] = c
		}
		return string(b)
	}
	put(id('a'), at.Add(-59*time.Minute), true) // within its hour
	put(id('b'), at.Add(-61*time.Minute), true) // past it
	put(id('c'), at.Add(-61*time.Minute), true) // past it, but being imported
	put(id('d'), at.Add(-time.Minute), false)   // an upload under way
	put(id('e'), at.Add(-2*time.Hour), false)   // an upload cut off
	importsMu.Lock()
	importsMu.busy = map[string]bool{id('c'): true}
	importsMu.Unlock()
	t.Cleanup(func() { importsMu.busy = nil })
	sweepImports()
	for c, want := range map[byte]bool{'a': true, 'b': false, 'c': true, 'd': true, 'e': false} {
		if _, err := os.Stat(filepath.Join(importsDir(), id(c))); (err == nil) != want {
			t.Errorf("%c: there %v, want %v", c, err == nil, want)
		}
	}
	if _, ok := readWaiting(id('a')); !ok {
		t.Error("the import within its hour isn't read")
	}
	if _, ok := readWaiting(id('c')); ok {
		t.Error("an import past its hour is read")
	}
	clearImports()
	if _, err := os.Stat(importsDir()); !os.IsNotExist(err) {
		t.Errorf("a start leaves %s: %v", importsDir(), err)
	}
}
