package main

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
	"time"
)

// digestWiki: a wiki in git with alice, bob (both with an address) and
// vera (a viewer), a mail server, a shared list team, and the clock at 7:00
// of today. alice turned the morning's mail on.
func digestWiki(t *testing.T) (*fakeSMTP, time.Time) {
	t.Helper()
	lastCommitsWiki(t, false)
	user := func(name, more string) string {
		return "[user " + name + "]\npassword = " + hashPassword(name+"-pw") + "\nemail = " + name + "@example.org\n" + more
	}
	configWiki(t, "base-url = https://wiki.example.org\n"+user("alice", "")+user("bob", "")+user("vera", "role = viewer\n"))
	cfg.GitEnabled = true
	gitSetup()
	smtp := startSMTP(t, false)
	set(t, &cfg.Mail, smtp.config("none"))
	y, m, d := time.Now().Date()
	morning := time.Date(y, m, d, 7, 0, 0, 0, time.Local)
	clock := morning
	set(t, &now, func() time.Time { return clock })
	t.Cleanup(func() {
		digestState.Lock()
		digestState.by = nil
		digestState.Unlock()
	})
	if _, err := createListOp(asUser("alice"), "team", false, false, "", ""); err != nil {
		t.Fatal(err)
	}
	mailOn(t, "alice", "digest")
	return smtp, morning
}

func mailOn(t *testing.T, name, kinds string) {
	t.Helper()
	user := asUser(name)
	if err := setMineParams(user, mineParams(user).src.raw, map[string]string{"mail": kinds}); err != nil {
		t.Fatal(err)
	}
}

func digestTaskBy(t *testing.T, by string, fields map[string]string) *Task {
	t.Helper()
	data := jsonObj{}
	for k, v := range fields {
		data[k], _ = json.Marshal(v)
	}
	task, err := createTask(asUser(by), data, "Create task")
	if err != nil {
		t.Fatal(err)
	}
	return task
}

// mailsSent: how many mails the server took, and the last one's text.
func (s *fakeSMTP) mailsSent() (int, string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	n := 0
	for _, c := range s.commands {
		if strings.EqualFold(c, "DATA") {
			n++
		}
	}
	return n, strings.ReplaceAll(s.data, "\r\n", "\n")
}

func TestTheMorningsMail(t *testing.T) {
	smtp, morning := digestWiki(t)
	day := isoDay(morning)
	// What is alice's: a task she gave bob, which he comments on; a page
	// she watches, which he changes; a task due today, one overdue; and
	// what isn't: bob's own task, a page she doesn't watch.
	given := digestTaskBy(t, "alice", map[string]string{"title": "Offer for Acme", "list": "team", "assignee": "bob"})
	if _, err := addComment(asUser("bob"), given.ID, "Waiting for the contract", "Comment"); err != nil {
		t.Fatal(err)
	}
	digestTaskBy(t, "alice", map[string]string{"title": "Call the bank", "due_date": day})
	digestTaskBy(t, "alice", map[string]string{"title": "File the taxes", "due_date": isoDay(morning.AddDate(0, 0, -3))})
	digestTaskBy(t, "bob", map[string]string{"title": "Bob's secret errand", "due_date": day})
	commitPage(t, "roadmap.md", "alice", "Create roadmap")
	commitPage(t, "other.md", "alice", "Create other")
	setWatch(asUser("alice"), "roadmap", true)
	commitPage(t, "roadmap.md", "bob", "Update roadmap")
	commitPage(t, "other.md", "bob", "Update other")

	// Before 7 and from noon on: nothing.
	sendDigests(morning.Add(-time.Minute))
	sendDigests(morning.Add(5 * time.Hour))
	if n, _ := smtp.mailsSent(); n != 0 {
		t.Fatalf("%d mails outside the morning", n)
	}
	sendDigests(morning.Add(30 * time.Minute))
	n, mail := smtp.mailsSent()
	if n != 1 {
		t.Fatalf("%d mails, want alice's alone (bob didn't turn it on)", n)
	}
	for _, want := range []string{
		"To: alice@example.org\n",
		"Subject: Zonewiki: 1 update, 1 watched page changed, 1 task due today\n",
		"Good morning, alice.",
		"UPDATES ON YOUR TASKS\n\n  Offer for Acme\n  bob commented\n  https://wiki.example.org/tasks?task=" + given.ID + "\n",
		"All of them: https://wiki.example.org/tasks?updates=1\n",
		"PAGES YOU WATCH\n\n  roadmap, changed by bob\n  https://wiki.example.org/page/roadmap\n",
		"What changed: https://wiki.example.org/changes?watched=1\n",
		"DUE TODAY\n\n  Call the bank\n",
		"OVERDUE\n\n  File the taxes\n  due " + isoDay(morning.AddDate(0, 0, -3)) + "\n",
		"To turn it off, open My tasks, then its menu, List settings.\n",
	} {
		if !strings.Contains(mail, want) {
			t.Errorf("the mail lacks %q:\n%s", want, mail)
		}
	}
	for _, not := range []string{"Bob's secret errand", "other"} {
		if strings.Contains(strings.SplitN(mail, "Good morning", 2)[1], not) {
			t.Errorf("the mail names %q:\n%s", not, mail)
		}
	}
	// Once a day.
	sendDigests(morning.Add(40 * time.Minute))
	if n, _ := smtp.mailsSent(); n != 1 {
		t.Fatalf("%d mails after a second look that morning", n)
	}

	// The next morning nothing is new: the update and the page were told
	// (she has looked at the page since), and what was due is only overdue
	// now. No mail.
	readAllWatched(&Req{user: asUser("alice")})
	next := morning.AddDate(0, 0, 1)
	sendDigests(next.Add(30 * time.Minute))
	if n, mail := smtp.mailsSent(); n != 1 {
		t.Fatalf("a mail on a morning with nothing new:\n%s", mail)
	}

	// The morning after, bob has commented again: one mail, with the
	// update, what is overdue along with it, and no page.
	set(t, &now, func() time.Time { return next.Add(3 * time.Hour) })
	if _, err := addComment(asUser("bob"), given.ID, "Signed", "Comment"); err != nil {
		t.Fatal(err)
	}
	sendDigests(next.AddDate(0, 0, 1).Add(30 * time.Minute))
	n, mail = smtp.mailsSent()
	if n != 2 || !strings.Contains(mail, "Subject: Zonewiki: 1 update\n") || !strings.Contains(mail, "bob commented twice") ||
		strings.Contains(mail, "PAGES YOU WATCH") || strings.Contains(mail, "DUE TODAY") ||
		!strings.Contains(mail, "OVERDUE\n\n  File the taxes") || !strings.Contains(mail, "\n  Call the bank\n  due "+day) {
		t.Fatalf("%d mails; the last:\n%s", n, mail)
	}
}

func TestTheMorningsMailIsForWhoSeesIt(t *testing.T) {
	smtp, morning := digestWiki(t)
	mailOn(t, "bob", "digest")
	// alice's private page, which bob watches by its folder, and her
	// private task: nothing of either is bob's news.
	setWatch(asUser("bob"), folderKey("private/alice"), true)
	commitPage(t, "private/alice/diary.md", "alice", "Update private/alice/diary")
	digestTaskBy(t, "alice", map[string]string{"title": "Her private errand", "due_date": isoDay(morning)})
	sendDigests(morning.Add(30 * time.Minute))
	n, mail := smtp.mailsSent()
	if n != 1 || !strings.Contains(mail, "To: alice@example.org") || !strings.Contains(mail, "Her private errand") {
		t.Fatalf("%d mails, want alice's about her own task:\n%s", n, mail)
	}
	if d := digestFor("bob", morning.Add(-24*time.Hour), morning); d.worthSending() || d.OlderPages+len(d.Overdue) > 0 {
		t.Errorf("bob's mail has something of alice's: %+v", d)
	}
	// A viewer can't turn it on, and one who is disabled gets none.
	if err := setMineParams(asUser("vera"), mineParams(asUser("vera")).src.raw, map[string]string{"mail": "digest"}); err == nil {
		t.Error("a viewer turned the mail on")
	}
	settings().Users["alice"].Disabled = true
	digestState.Lock()
	digestState.by = nil
	digestState.Unlock()
	_ = os.Remove(digestPath())
	sendDigests(morning.Add(45 * time.Minute))
	if n, _ := smtp.mailsSent(); n != 1 {
		t.Errorf("%d mails: one went to a disabled user", n)
	}
}

func TestNoMorningsMailWithoutAMailServer(t *testing.T) {
	_, morning := digestWiki(t)
	set(t, &cfg.Mail, mailConfig{})
	digestTaskBy(t, "alice", map[string]string{"title": "Call the bank", "due_date": isoDay(morning)})
	sendDigests(morning.Add(30 * time.Minute))
	if exists(digestPath()) {
		t.Error("a morning was noted where no mail can go out")
	}
}
