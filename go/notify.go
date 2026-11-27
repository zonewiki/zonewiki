// Telling a user that a task was given to them (Assigning tasks in
// docs/spec/users.md): a mail, at once, when someone else assigns them a
// task. The task writes call tellAssigned after a write went through
// (createTask, updateTaskLocked, apiBatchUpdate), with who had each task
// before. No mail without an SMTP server (mail.go), to a user without an
// address, or to one who didn't turn that mail on (mails; Settings,
// Notifications). In the app such a task is marked on its card and
// counted on the Tasks links (newsFor), from the task alone.
package main

import (
	"fmt"
	"slices"
	"sort"
	"strings"
)

// assignedBefore: who had each task (by id) before a write; a task the
// write made is not in it.
type assignedBefore map[string]string

// The kinds of mail a user can turn on (Settings, Notifications): a task
// given to them, a mention of them, a comment on a task they have, gave or
// follow, and the morning's mail (digest.go: what is new for them, the
// watched pages that changed, what is due). Those turned on are in their
// watch file (watched.Mail). None is turned on until they do it.
var mailKinds = []string{"assigned", "mentioned", "comments", "digest"}

// mailChoices: the kinds of mail user can have, in mailKinds' order: all of
// them for one a task can be given to; the morning's mail alone for a
// viewer, whom nothing is given (theirs tells of the pages they watch), but
// not for an account several people sign in to; none for anyone else (a
// disabled user, one waiting for approval).
func (s *Settings) mailChoices(user string) []string {
	switch u := s.Users[user]; {
	case u == nil:
		return nil
	case s.assignable(user):
		return mailKinds
	case s.mayViewerDigest(user) && !u.Shared:
		return []string{"digest"}
	}
	return nil
}

// mails: user gets the mails of kind: the deployment sends mail, they have
// a confirmed address, the kind is one of theirs to have (mailChoices) and
// they turned it on.
func mails(s *Settings, user, kind string) bool {
	u := s.Users[user]
	if !cfg.Mail.on() || u == nil || u.Email == "" {
		return false
	}
	return slices.Contains(s.mailChoices(user), kind) && slices.Contains(watchesOf(user).Mail, kind)
}

// mayViewerDigest: user is a viewer who can have the morning's mail, of
// the pages they watch: one who isn't disabled or waiting for approval.
func (s *Settings) mayViewerDigest(user string) bool {
	u := s.Users[user]
	return u != nil && u.Role == roleViewer && !u.Disabled && !u.Pending
}

// setMails turns on the kinds of mail in on for user, of those they can
// have, and the others off; it writes their watch file, beside the wiki,
// and makes no commit. Who wants the morning's mail may have changed, and
// with it when Zonewiki asks to be started (.zone/wake).
func setMails(s *Settings, user string, on []string) {
	var kinds []string
	for _, k := range s.mailChoices(user) { // in one order, whatever was sent
		if slices.Contains(on, k) {
			kinds = append(kinds, k)
		}
	}
	changeWatches(user, func(w *watched) bool {
		if slices.Equal(w.Mail, kinds) {
			return false
		}
		w.Mail = kinds
		return true
	})
	writeWake(now())
}

// mailFooter: a mail's last line: why Zonewiki sent it (why: "when someone
// gives you a task") and where its reader turns it off.
func mailFooter(s *Settings, why string) string {
	where := "open Settings, then Notifications"
	if s.BaseURL != "" {
		where = "open " + s.BaseURL + "/notifications"
	}
	return "\n" + appName + " sends this mail " + why + ". To turn it off, " + where + ".\n"
}

// taskMail: a mail about one task, sent in the background (a task write
// doesn't wait for a mail server); a failure is logged.
func taskMail(s *Settings, name, subject, intro string, t *Task, text, kindLine string) {
	var b strings.Builder
	b.WriteString(intro + "\n\n  " + oneLine(t.Title) + "\n")
	if s.BaseURL != "" {
		b.WriteString("  " + s.BaseURL + "/tasks?task=" + t.ID + "\n")
	}
	if text = strings.TrimSpace(text); text != "" {
		b.WriteString("\n" + runePrefix(text, 2000) + "\n")
	}
	b.WriteString(mailFooter(s, kindLine))
	to, body := s.Users[name].Email, b.String()
	go func() {
		if err := sendMail(cfg.Mail, to, subject, body); err != nil {
			logf("mail to %s about a task: %v", name, err)
		}
	}()
}

// tellMentioned mails the users a comment or a description of by's named
// with @name (logMentions), those who turned that mail on.
func tellMentioned(by string, t *Task, named []string, text ...string) {
	s := settings()
	if by == "" || s.Users == nil || !cfg.Mail.on() {
		return
	}
	for _, name := range named {
		if name != by && mails(s, name, "mentioned") && canSeeTask(asUser(name), t) {
			taskMail(s, name, by+" mentioned you: "+oneLine(t.Title), by+" mentioned you on "+appName+", in this task:", t,
				strings.Join(text, ""), "when someone mentions you in a task")
		}
	}
}

// tellComment mails a comment by on a shared task to who has it, who made
// it and who follows it (not to its author, nor to one the comment names: they get the
// mention's mail), those who turned that mail on; and the mention's mail to
// who it names.
func tellComment(by string, t *Task, text string, named []string) {
	s := settings()
	if by == "" || s.Users == nil || !cfg.Mail.on() || t.Private() {
		return
	}
	tellMentioned(by, t, named, text)
	var told []string
	for _, name := range append([]string{t.Assignee, t.CreatedBy}, t.Followers...) {
		// (Who made it or has it may be none of its list's members by now: a
		// list narrowed since. They don't see the task, so no mail tells them.)
		if name == "" || name == by || slices.Contains(named, name) || slices.Contains(told, name) || !mails(s, name, "comments") ||
			!canSeeTask(asUser(name), t) {
			continue
		}
		told = append(told, name)
		taskMail(s, name, by+" commented: "+oneLine(t.Title), by+" commented on this task on "+appName+":", t, text,
			"when someone comments on a task you have, gave or follow")
	}
}

// tellAssigned mails each user the open tasks among tasks that by gave them
// with this write: one mail a user, sent in the background, since a task
// write doesn't wait for a mail server; a failure is logged. Nothing for a
// task one takes oneself, or without users.
func tellAssigned(by string, before assignedBefore, tasks ...*Task) {
	s := settings()
	if by == "" || s.Users == nil || !cfg.Mail.on() {
		return
	}
	given := map[string][]*Task{}
	for _, t := range tasks {
		if to := t.Assignee; to != "" && to != by && to != before[t.ID] && t.IsOpen() && !t.Private() && canSeeTask(asUser(to), t) {
			given[to] = append(given[to], t)
		}
	}
	names := make([]string, 0, len(given))
	for name := range given {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		if !mails(s, name, "assigned") {
			continue
		}
		to, subject, body := s.Users[name].Email, "", assignedMail(s, by, given[name])
		if n := len(given[name]); n == 1 {
			subject = by + " gave you a task: " + oneLine(given[name][0].Title)
		} else {
			subject = fmt.Sprintf("%s gave you %d tasks", by, n)
		}
		go func() {
			if err := sendMail(cfg.Mail, to, subject, body); err != nil {
				logf("mail to %s about assigned tasks: %v", name, err)
			}
		}()
	}
}

// oneLine: a title as a mail's subject and one line of its text.
func oneLine(s string) string {
	return runePrefix(strings.Join(strings.Fields(s), " "), 120)
}

// assignedMail: the text of the mail about tasks by gave a user: each
// task's title and, where Zonewiki knows its own address (base-url), its link.
func assignedMail(s *Settings, by string, tasks []*Task) string {
	var b strings.Builder
	if len(tasks) == 1 {
		fmt.Fprintf(&b, "%s assigned this task to you on %s:\n", by, appName)
	} else {
		fmt.Fprintf(&b, "%s assigned these tasks to you on %s:\n", by, appName)
	}
	for _, t := range tasks {
		b.WriteString("\n  " + oneLine(t.Title) + "\n")
		if s.BaseURL != "" {
			b.WriteString("  " + s.BaseURL + "/tasks?task=" + t.ID + "\n")
		}
	}
	if s.BaseURL != "" {
		b.WriteString("\nAll your tasks: " + s.BaseURL + mineHref + "\n")
	}
	b.WriteString(mailFooter(s, "when someone gives you a task"))
	return b.String()
}
