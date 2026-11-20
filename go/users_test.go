package main

import (
	"runtime"
	"strings"
	"testing"
	"time"
)

func TestPasswordHash(t *testing.T) {
	h := hashPassword("correct horse ✓")
	if !strings.HasPrefix(h, "pbkdf2-sha256:600000:") || strings.Contains(h, "$") {
		t.Fatalf("hash %q", h)
	}
	if !checkPassword("correct horse ✓", h) || checkPassword("correct horse", h) || checkPassword("", h) {
		t.Error("checkPassword")
	}
	if h == hashPassword("correct horse ✓") {
		t.Error("no salt")
	}
	for _, bad := range []string{"", "plain", "pbkdf2-sha256:0:AA:AA", "pbkdf2-sha256:1:!!:AA", "bcrypt:1:AA:AA",
		"pbkdf2-sha256:1::AA"} {
		if _, _, _, err := parseHash(bad); err == nil {
			t.Errorf("%q parses", bad)
		}
		if checkPassword("", bad) {
			t.Errorf("%q checks", bad)
		}
	}
}

func TestAuthenticate(t *testing.T) {
	testWiki(t, nil)
	settings().Users = map[string]*User{"alice": {Name: "alice", PasswordHash: hashPassword("pw")}}
	if u := authenticate(" Alice ", "pw"); u == nil || u.Name != "alice" {
		t.Error("alice")
	}
	if authenticate("alice", "nope") != nil || authenticate("bob", "pw") != nil || authenticate("", "") != nil {
		t.Error("let in")
	}
}

// Key derivations run in a few slots at once, wiki-wide: with every slot
// taken a check waits, and goes on once one is free (not refused).
func TestKeyDerivationsWaitForASlot(t *testing.T) {
	hash := hashPassword("pw")
	for range cap(keySlots) {
		keySlots <- struct{}{}
	}
	done := make(chan bool, 1)
	go func() { done <- checkPassword("pw", hash) }()
	select {
	case <-done:
		t.Fatal("a check ran with every slot taken")
	case <-time.After(200 * time.Millisecond):
	}
	<-keySlots
	select {
	case ok := <-done:
		if !ok {
			t.Error("the right password, checked once a slot was free, is wrong")
		}
	case <-time.After(10 * time.Second):
		t.Fatal("the check still waits with a slot free")
	}
	for range cap(keySlots) - 1 {
		<-keySlots
	}
	if cap(keySlots) > max(1, runtime.NumCPU()/2) {
		t.Errorf("%d slots on %d cores", cap(keySlots), runtime.NumCPU())
	}
}

// An unknown name costs what a wrong password does: the dummy hash it is
// checked against parses, with a real hash's iterations and key length, and
// matches no password, the empty one included.
func TestDummyHash(t *testing.T) {
	iterations, salt, key, err := parseHash(dummyHash)
	if err != nil || iterations != hashIterations || len(salt) != 16 || len(key) != hashLen {
		t.Fatalf("dummyHash %q: %d iterations, salt %d, key %d, %v", dummyHash, iterations, len(salt), len(key), err)
	}
	_, _, realKey, _ := parseHash(hashPassword("pw"))
	if len(realKey) != len(key) {
		t.Errorf("key %d bytes, a real hash's %d", len(key), len(realKey))
	}
	if checkPassword("", dummyHash) || checkPassword("pw", dummyHash) {
		t.Error("the dummy hash matches")
	}
}
