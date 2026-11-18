// Telling a user that a task was given to them (Assigning tasks in
// docs/spec/users.md): a mail, at once, when someone else assigns them a
// task. The task writes call tellAssigned after a write went through
// (createTask, updateTaskLocked, apiBatchUpdate), with who had each task
// before. No mail without an SMTP server (mail.go), to a user without an
// address, or to one who didn't turn that mail on in their My tasks'
// settings (mails). In the app such a task is marked on its card and
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

// The kinds of mail about tasks a user can turn on (My tasks' settings):
// a task given to them, a mention of them, a comment on a task they have or
// gave. "mail" in their registry's mine names those turned on, "," between.
// None is turned on until they do it: "" and "off" (what turned the one
// mail off until 2026-11, when it was on by itself) are none.
var mailKinds = []string{"assigned", "mentioned", "comments"}

// mails: user gets the mails of kind: the deployment sends mail, they have
// an address, they aren't disabled, and they turned that kind on.
func mails(s *Settings, user, kind string) bool {
	u := s.Users[user]
	return cfg.Mail.Host != "" && u != nil && u.Email != "" && s.assignable(user) &&
		slices.Contains(strings.Split(taskFiles.ReadLists(user).Mine["mail"], ","), kind)
}

// mailsAssigned: user gets a mail when a task is assigned to them.
func mailsAssigned(s *Settings, user string) bool { return mails(s, user, "assigned") }

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
	b.WriteString("\n" + appName + " sends this mail " + kindLine + ". To turn it off, open My tasks, then its menu, List settings.\n")
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
	if by == "" || s.Users == nil || cfg.Mail.Host == "" {
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
	if by == "" || s.Users == nil || cfg.Mail.Host == "" || t.Owner != "" {
		return
	}
	tellMentioned(by, t, named, text)
	var told []string
	for _, name := range append([]string{t.Assignee, t.Maker()}, t.Followers...) {
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
	if by == "" || s.Users == nil || cfg.Mail.Host == "" {
		return
	}
	given := map[string][]*Task{}
	for _, t := range tasks {
		if to := t.Assignee; to != "" && to != by && to != before[t.ID] && t.IsOpen() && t.Owner == "" && canSeeTask(asUser(to), t) {
			given[to] = append(given[to], t)
		}
	}
	names := make([]string, 0, len(given))
	for name := range given {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		if !mailsAssigned(s, name) {
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
// task's title and, where Zone knows its own address (base-url), its link.
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
	b.WriteString("\n" + appName + " sends this mail when someone gives you a task. To turn it off, open My tasks, " +
		"then its menu, List settings.\n")
	return b.String()
}
