package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"
)

// ZONE_MAIL_SPOOL is a way to send mail by itself: with it mail is on, and
// a mail server beside it is refused.
func TestLoadMailSpool(t *testing.T) {
	m, err := loadMail(envMap{"ZONE_MAIL_SPOOL": "1"}.get)
	if err != nil || !m.Spool || !m.on() || m.Host != "" {
		t.Errorf("spool: %v %+v", err, m)
	}
	if m, _ := loadMail(envMap{}.get); m.on() {
		t.Error("mail is on with nothing set")
	}
	if m, _ := loadMail(envMap{"ZONE_SMTP_HOST": "m", "ZONE_MAIL_FROM": "w@example.org"}.get); !m.on() || m.Spool {
		t.Errorf("a server: %+v", m)
	}
	for _, key := range []string{"ZONE_SMTP_HOST", "ZONE_SMTP_PASSWORD", "ZONE_MAIL_FROM"} {
		_, err := loadMail(envMap{"ZONE_MAIL_SPOOL": "yes", key: "x"}.get)
		if err == nil || !strings.Contains(err.Error(), "ZONE_MAIL_SPOOL and "+key+" are both set") {
			t.Errorf("%s beside the folder: %v", key, err)
		}
	}
}

// A mail in the folder: one line of subject, no carriage return, a name
// that sorts by its time; the 501st is dropped and said so.
func TestSpoolMail(t *testing.T) {
	testWiki(t, nil)
	at := time.Date(2026, 11, 25, 19, 32, 48, 5, time.UTC)
	old := now
	now = func() time.Time { return at }
	defer func() { now = old }()
	if err := spoolMail("bob@example.org", "Two\r\nBcc: x@example.org lines", "Hello\r\nthere\n"); err != nil {
		t.Fatal(err)
	}
	dir := filepath.Join(cfg.Data, ".zone", "outbox")
	files, _ := os.ReadDir(dir)
	if len(files) != 1 || !regexp.MustCompile(`^20261125T193248\.000000005Z-[0-9a-f]{16}\.json$`).MatchString(files[0].Name()) {
		t.Fatalf("files: %v", files)
	}
	b, _ := os.ReadFile(filepath.Join(dir, files[0].Name()))
	var m spooledMail
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatal(err)
	}
	want := spooledMail{To: "bob@example.org", Subject: "Two Bcc: x@example.org lines", Body: "Hello\nthere\n", At: "2026-11-25T19:32:48Z"}
	if m != want {
		t.Errorf("mail = %+v, want %+v", m, want)
	}
	for i := 1; i < spoolMax; i++ {
		at = at.Add(time.Second)
		if err := spoolMail("bob@example.org", "s", "b"); err != nil {
			t.Fatal(err)
		}
	}
	err := spoolMail("carol@example.org", "One too many", "b")
	if err == nil || !strings.Contains(err.Error(), "holds 500 mails that nobody fetched: dropped the mail to carol@example.org (One too many)") {
		t.Errorf("the 501st: %v", err)
	}
	if files, _ = os.ReadDir(dir); len(files) != spoolMax {
		t.Errorf("%d files", len(files))
	}
	// Oldest first by name.
	if first, _ := os.ReadFile(filepath.Join(dir, files[0].Name())); !strings.Contains(string(first), "Two Bcc") {
		t.Errorf("the first file is %s", first)
	}
}

// A confirmation link is for one user and one address, for a day.
func TestConfirmToken(t *testing.T) {
	oldKey := sessionKey
	defer func() { sessionKey = oldKey }()
	sessionKey = []byte("a key for the test")
	at := time.Date(2026, 11, 25, 12, 0, 0, 0, time.UTC)
	old := now
	now = func() time.Time { return at }
	defer func() { now = old }()
	token := confirmToken("bob", "bob@example.org", at.Add(confirmLifetime))
	if !confirms(token, "bob", "bob@example.org") {
		t.Fatal("the link doesn't confirm")
	}
	for what, ok := range map[string]bool{
		"another user":    confirms(token, "alice", "bob@example.org"),
		"another address": confirms(token, "bob", "robert@example.org"),
		"no address":      confirms(token, "bob", ""),
		"cut short":       confirms(token[:len(token)-1], "bob", "bob@example.org"),
		"no token":        confirms("", "bob", "bob@example.org"),
		"a later end":     confirms("9"+token, "bob", "bob@example.org"),
	} {
		if ok {
			t.Errorf("%s confirms", what)
		}
	}
	// A name and an address that run together differently make another link.
	if confirms(confirmToken("bo", "b\nbob@example.org", at.Add(confirmLifetime)), "bo\nb", "bob@example.org") {
		t.Error("the user and the address aren't told apart")
	}
	at = at.Add(confirmLifetime - time.Second)
	if !confirms(token, "bob", "bob@example.org") {
		t.Error("the link ended before its day")
	}
	at = at.Add(time.Second)
	if confirms(token, "bob", "bob@example.org") {
		t.Error("the link works after its day")
	}
	sessionKey = []byte("another key")
	at = at.Add(-time.Hour)
	if confirms(token, "bob", "bob@example.org") {
		t.Error("a link signed with another key confirms")
	}
}

// Three links a user a day, ten a wiki an hour, kept in a file.
func TestCountConfirm(t *testing.T) {
	testWiki(t, nil)
	at := time.Date(2026, 11, 25, 12, 0, 0, 0, time.UTC)
	old := now
	now = func() time.Time { return at }
	defer func() { now = old }()
	count := func(name string) bool {
		t.Helper()
		ok, err := countConfirm(name)
		if err != nil {
			t.Fatal(err)
		}
		return ok
	}
	for i := 0; i < confirmsPerUserDay; i++ {
		if !count("bob") {
			t.Fatalf("bob's link %d isn't sent", i+1)
		}
	}
	if count("bob") {
		t.Error("bob gets a fourth link in a day")
	}
	at = at.Add(23 * time.Hour)
	if count("bob") {
		t.Error("bob gets a fourth link after 23 hours")
	}
	at = at.Add(time.Hour)
	if !count("bob") {
		t.Error("bob gets no link a day later")
	}
	at = at.Add(48 * time.Hour)
	for i := 0; i < confirmsPerWikiHour; i++ {
		if !count("user" + string(rune('a'+i))) {
			t.Fatalf("the wiki's link %d isn't sent", i+1)
		}
	}
	if count("zoe") {
		t.Error("the wiki sends an eleventh link in an hour")
	}
	at = at.Add(time.Hour)
	if !count("zoe") {
		t.Error("the wiki sends no link an hour later")
	}
	// A broken file counts as none, and is written anew.
	if err := os.WriteFile(confirmsPath(), []byte("{nonsense"), 0o600); err != nil {
		t.Fatal(err)
	}
	if !count("bob") {
		t.Error("a broken file stops the links")
	}
}
