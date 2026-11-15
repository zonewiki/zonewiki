// Telling a user that a task was given to them (Assigning tasks in
// docs/spec/users.md): a mail, at once, when someone else assigns them a
// task. The task writes call tellAssigned after a write went through
// (createTask, updateTaskLocked, apiBatchUpdate), with who had each task
// before. No mail without an SMTP server (mail.go), to a user without an
// address, or to one who turned it off in their My tasks' settings
// ("mail": "off" in their registry's mine). In the app such a task is
// marked new on its card (TasksCore.givenToMe), from its history alone.
package main

import (
	"fmt"
	"sort"
	"strings"
)

// assignedBefore: who had each task (by id) before a write; a task the
// write made is not in it.
type assignedBefore map[string]string

// mailsAssigned: user gets a mail when a task is assigned to them: the
// deployment sends mail, they have an address, they aren't disabled, and
// they didn't turn it off.
func mailsAssigned(s *Settings, user string) bool {
	u := s.Users[user]
	return cfg.Mail.Host != "" && u != nil && u.Email != "" && s.assignable(user) &&
		taskFiles.ReadLists(user).Mine["mail"] != "off"
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
		if to := t.Assignee; to != "" && to != by && to != before[t.ID] && t.IsOpen() && t.Owner == "" {
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
		"then its menu, List settings, and untick \"Mail me when a task is assigned to me\".\n")
	return b.String()
}
