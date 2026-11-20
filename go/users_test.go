package main

import (
	"net/http"
	"net/http/httptest"
	"net/url"
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
	if u, right := authenticate(" Alice ", "pw"); u == nil || u.Name != "alice" || !right {
		t.Error("alice")
	}
	for _, c := range [][2]string{{"alice", "nope"}, {"bob", "pw"}, {"", ""}} {
		if _, right := authenticate(c[0], c[1]); right {
			t.Errorf("%q let in", c)
		}
	}
}

// A login checks one password, whoever it names: an unknown name, a
// wrong or right password, a disabled or a waiting user's (whose login
// said so with a second check, which told by its time which names are
// disabled).
func TestOneCheckATry(t *testing.T) {
	testWiki(t, nil)
	hash := hashPassword("pw")
	s := settings()
	s.Users = map[string]*User{"alice": {Name: "alice", PasswordHash: hash},
		"dora": {Name: "dora", PasswordHash: hash, Disabled: true},
		"pat":  {Name: "pat", PasswordHash: hash, Disabled: true, Pending: true}}
	loginThrottle = newThrottle()
	t.Cleanup(func() { loginThrottle = newThrottle() })
	for _, c := range []struct{ name, password, says string }{
		{"nobody", "pw", "Incorrect user name"}, {"alice", "nope", "Incorrect user name"}, {"alice", "pw", ""},
		{"dora", "nope", "Incorrect user name"}, {"dora", "pw", "is disabled"}, {"pat", "nope", "Incorrect user name"},
		{"pat", "pw", "waits for an admin"},
	} {
		form := url.Values{"username": {c.name}, "password": {c.password}}
		r := httptest.NewRequest("POST", "/login", strings.NewReader(form.Encode()))
		r.Header.Set("Content-Type", "application/x-www-form-urlencoded")
		r.RemoteAddr = "203.0.113." + itoa(len(c.name)+len(c.password)) + ":1"
		w := httptest.NewRecorder()
		before := keysDerived.Load()
		login(w, r)
		if n := keysDerived.Load() - before; n != 1 {
			t.Errorf("%s/%s: %d checks", c.name, c.password, n)
		}
		if c.says == "" && w.Code != http.StatusFound || c.says != "" && !strings.Contains(w.Body.String(), c.says) {
			t.Errorf("%s/%s: %d %q", c.name, c.password, w.Code, w.Body.String())
		}
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
