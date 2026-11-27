// The morning's mail (The morning's mail in docs/spec/tasks.md): once a
// day, to who turned it on (Settings, Notifications), what is new for them
// since the last one (updates on their tasks, changes to the pages they
// watch) and what is due today. A morning with nothing of those sends
// nothing. It is a listing made of the same rules the app shows by
// (newsFor, watched.news, mineFilter), asked for the user it goes to:
// nothing in it that they don't see.
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"slices"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"zonewiki/internal/atomicfile"
)

// The morning: the mail goes out from digestFrom o'clock, the wiki's time
// (its timezone, zone), and no longer from digestUntil: a Zonewiki that starts in the
// afternoon doesn't send the morning's mail to someone who is at their
// desk by then.
const (
	digestFrom  = 7
	digestUntil = 12
)

// digestMost: how many tasks or pages a part of the mail names before it
// counts the rest.
const digestMost = 20

// digestSent: the last morning a user's mail was looked into (Day,
// YYYY-MM-DD: sent or not, once a day) and the moment up to which they
// were told what is new (At, unix seconds).
type digestSent struct {
	Day string `json:"day"`
	At  int64  `json:"at"`
}

// digestState: .zone/digest.json, user → digestSent. Lost with Zonewiki's
// folder, the next morning's mail tells of the last day.
var digestState struct {
	sync.Mutex
	dir string
	by  map[string]digestSent
}

func digestPath() string { return statePath("digest.json") }

// loadDigests reads the file once for a wiki's folder (digestState locked).
func loadDigests() {
	if digestState.by != nil && digestState.dir == cfg.Data {
		return
	}
	digestState.dir, digestState.by = cfg.Data, map[string]digestSent{}
	if raw, err := os.ReadFile(digestPath()); err == nil {
		_ = json.Unmarshal(raw, &digestState.by)
	}
}

// digestsRunning: the morning's mails are being made or sent: Zonewiki
// doesn't exit for being idle meanwhile (idleLongEnough).
var digestsRunning atomic.Bool

// startDigests looks every minute whether the morning's mail is due, from
// the start on: a Zonewiki that is started for it (one asleep until its
// socket is asked) sends it then.
func startDigests() {
	go func() {
		for {
			sendDigests(now())
			writeWake(now())
			select {
			case <-time.After(time.Minute):
			case <-digestKick:
			}
		}
	}()
}

// digestKick: the loop looks again now instead of within the minute: the
// settings changed (an address was confirmed, the wiki's timezone set).
var digestKick = make(chan struct{}, 1)

func kickDigests() {
	select {
	case digestKick <- struct{}{}:
	default: // one waits already
	}
}

// sendDigests sends the morning's mail of the day t is in to every user
// who turned it on and wasn't looked into today, when t is in the morning.
// A user is noted as looked into before their mail goes out: a mail is
// rather lost than sent twice.
func sendDigests(t time.Time) {
	s := settings()
	if !cfg.Mail.on() || s.Users == nil || t.Hour() < digestFrom || t.Hour() >= digestUntil {
		return
	}
	digestsRunning.Store(true)
	looked := false
	defer func() {
		if looked {
			// From here a Zonewiki nobody asked counts its minute (exitWhenIdle).
			idle.last.Store(now().UnixNano())
		}
		digestsRunning.Store(false)
	}()
	day := isoDay(t)
	names := make([]string, 0, len(s.Users))
	for name := range s.Users {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		if !mails(s, name, "digest") {
			continue
		}
		digestState.Lock()
		loadDigests()
		last := digestState.by[name]
		if last.Day == day {
			digestState.Unlock()
			continue
		}
		digestState.by[name] = digestSent{Day: day, At: t.Unix()}
		looked = true
		text, err := json.MarshalIndent(digestState.by, "", "  ")
		if err == nil {
			err = atomicfile.WriteOwnerOnly(digestPath(), string(text)+"\n")
		}
		digestState.Unlock()
		if err != nil {
			// Not noted: the mail would go out again every minute.
			logf("the morning's mail: %s could not be written, so no mail to %s: %v", digestPath(), name, err)
			continue
		}
		since := time.Unix(last.At, 0)
		if last.At == 0 {
			since = t.Add(-24 * time.Hour)
		}
		d := digestFor(name, since, t)
		if !d.worthSending() {
			continue
		}
		subject, body := d.mail(s)
		if err := sendMail(cfg.Mail, s.Users[name].Email, subject, body); err != nil {
			logf("the morning's mail to %s: %v", name, err)
		}
	}
}

// When to be started (.zone/wake; When to be started in
// docs/spec/deployment.md): a Zonewiki that sleeps while nobody asks sends
// the morning's mail only if something starts it in the morning. So it says
// when: one line, the next moment it has something to do by itself, in UTC
// (2026-11-26T06:00:00Z). That is the next digestFrom o'clock of the wiki
// while a user has the morning's mail turned on; today's, while one of
// them still waits for this morning's. With nobody who wants the mail there
// is no file. It means "from then on": whoever starts Zonewiki sets the pace.

func wakePath() string { return statePath("wake") }

// nextWake: the moment the file names at t, ok false for none.
func nextWake(s *Settings, t time.Time) (at time.Time, ok bool) {
	if !cfg.Mail.on() || s.Users == nil {
		return time.Time{}, false
	}
	t = t.In(zone())
	day := isoDay(t)
	wants, waits := false, false
	digestState.Lock()
	loadDigests()
	for name := range s.Users {
		if mails(s, name, "digest") {
			wants = true
			waits = waits || digestState.by[name].Day != day
		}
	}
	digestState.Unlock()
	if !wants {
		return time.Time{}, false
	}
	morning := time.Date(t.Year(), t.Month(), t.Day(), digestFrom, 0, 0, 0, zone())
	if t.Hour() < digestFrom || (waits && t.Hour() < digestUntil) {
		return morning, true
	}
	return time.Date(t.Year(), t.Month(), t.Day()+1, digestFrom, 0, 0, 0, zone()), true
}

// wakeWritten: what the file holds, as this Zonewiki last left it ("": no
// file), so a minute that changes nothing writes nothing.
var wakeWritten struct {
	sync.Mutex
	dir, line string
	known     bool
}

// writeWake leaves .zone/wake as nextWake says at t: written when it
// changes, removed when nobody wants the mail.
func writeWake(t time.Time) {
	line := ""
	if at, ok := nextWake(settings(), t); ok {
		line = at.UTC().Format(time.RFC3339) + "\n"
	}
	wakeWritten.Lock()
	defer wakeWritten.Unlock()
	if wakeWritten.known && wakeWritten.dir == cfg.Data && wakeWritten.line == line {
		return
	}
	var err error
	if line == "" {
		if err = os.Remove(wakePath()); os.IsNotExist(err) {
			err = nil
		}
	} else {
		err = atomicfile.WriteOwnerOnly(wakePath(), line)
	}
	if err != nil {
		logf("when to be started: %s could not be written: %v", wakePath(), err)
		return
	}
	wakeWritten.dir, wakeWritten.line, wakeWritten.known = cfg.Data, line, true
}

// digest: what a morning's mail tells one user.
type digest struct {
	user  string
	since time.Time
	// Updates: the tasks with news that came since the last mail, the
	// latest first; OlderUpdates: how many more have news from before it,
	// still unread.
	Updates      []digestTask
	OlderUpdates int
	// Mentions: the pages that mention the user since the last mail and
	// that they haven't opened; OlderMentions: how many more from before it.
	Mentions      []shownMention
	OlderMentions int
	// Pages: the watched pages changed since the last mail that have news;
	// OlderPages: how many more have news from before it.
	Pages      []digestPage
	OlderPages int
	// DueToday and Overdue: of My tasks.
	DueToday, Overdue []digestTask
}

// digestTask: a task in the mail: its title, what is to say of it (what
// happened; since when it is overdue), and its id for the link.
type digestTask struct{ ID, Title, Says string }

// digestPage: a watched page in the mail, and who changed it last.
type digestPage struct{ Slug, By string }

// worthSending: something is new since the last mail, or due today. What
// is only overdue, or was told before and not read, sends no mail of its
// own: it would be the same mail every morning.
func (d digest) worthSending() bool {
	return len(d.Updates)+len(d.Mentions)+len(d.Pages)+len(d.DueToday) > 0
}

// digestFor: the morning's mail for the user name at t, with what is new
// since since. Everything is asked as that user: the tasks they see, the
// pages they watch and see.
func digestFor(name string, since, t time.Time) digest {
	user := asUser(name)
	d := digest{user: name, since: since}
	day, sinceMinute := isoDay(t), since.Format("2006-01-02T15:04")
	// A viewer's is of the pages they watch alone: nothing is given to
	// them, and every list counts as theirs (mineFilter), so what is due
	// would be the whole wiki's.
	tasks := settings().assignable(name)
	open := taskFiles.Open(user)
	// Updates: by when their latest news came, the latest first.
	type update struct {
		digestTask
		at string
	}
	var updates []update
	for _, task := range updatesFor(user, open, recentUpdates(user)) {
		if !tasks {
			break
		}
		all := newsAll(task, user)
		if len(all) == 0 {
			continue
		}
		if at := all[len(all)-1].At; at >= sinceMinute {
			updates = append(updates, update{digestTask{task.ID, oneLine(task.Title), newsText(all, "you", " · ")}, at})
		} else {
			d.OlderUpdates++
		}
	}
	sort.SliceStable(updates, func(i, j int) bool { return updates[i].at > updates[j].at })
	for _, u := range updates {
		d.Updates = append(d.Updates, u.digestTask)
	}
	// Mentions on pages, the latest first.
	for _, m := range mentionsFor(user) {
		if m.At.After(since) {
			d.Mentions = append(d.Mentions, m)
		} else {
			d.OlderMentions++
		}
	}
	// Watched pages, A–Z.
	news := watchNewsFor(user, nil)
	for slug, c := range news {
		if c.at <= since.Unix() {
			d.OlderPages++
			continue
		}
		by := commitAuthor(c.author)
		if m := (commitMark{Assistant: c.assistant}); m.ByAssistant() {
			if by == "" {
				by = "an assistant"
			} else if by == name {
				by = "your assistant"
			} else {
				by += "'s assistant"
			}
			if m.Via() != "" {
				by += " (" + m.Via() + ")"
			}
		}
		d.Pages = append(d.Pages, digestPage{slug, by})
	}
	sort.Slice(d.Pages, func(i, j int) bool { return d.Pages[i].Slug < d.Pages[j].Slug })
	// What is due, of My tasks: the earliest first.
	mine := mineFilter(user, taskLists(user))
	var due []*Task
	for _, task := range open {
		if tasks && task.DueDate != "" && task.DueDate <= day && mine(task) {
			due = append(due, task)
		}
	}
	slices.SortStableFunc(due, func(a, b *Task) int { return strings.Compare(a.DueDate, b.DueDate) })
	for _, task := range due {
		if taskOverdue(task, day) {
			d.Overdue = append(d.Overdue, digestTask{task.ID, oneLine(task.Title), "due " + task.DueDate})
		} else {
			d.DueToday = append(d.DueToday, digestTask{task.ID, oneLine(task.Title), ""})
		}
	}
	return d
}

// mail: the mail's subject and text. Links only where Zonewiki knows its
// own address (base-url), as its other mails.
func (d digest) mail(s *Settings) (subject, body string) {
	var parts []string
	count := func(n int, one, many string) {
		if n == 1 {
			parts = append(parts, "1 "+one)
		} else if n > 1 {
			parts = append(parts, itoa(n)+" "+many)
		}
	}
	count(len(d.Updates), "update", "updates")
	count(len(d.Mentions), "mention", "mentions")
	count(len(d.Pages), "watched page changed", "watched pages changed")
	count(len(d.DueToday), "task due today", "tasks due today")
	subject = appName + ": " + strings.Join(parts, ", ")

	var b strings.Builder
	link := func(path string) {
		if s.BaseURL != "" {
			b.WriteString("  " + s.BaseURL + path + "\n")
		}
	}
	tasks := func(heading string, list []digestTask) {
		if len(list) == 0 {
			return
		}
		b.WriteString("\n" + heading + "\n")
		for i, t := range list {
			if i == digestMost {
				fmt.Fprintf(&b, "\n  and %d more\n", len(list)-i)
				break
			}
			b.WriteString("\n  " + t.Title + "\n")
			if t.Says != "" {
				b.WriteString("  " + t.Says + "\n")
			}
			link("/tasks?task=" + t.ID)
		}
	}
	fmt.Fprintf(&b, "Good morning, %s. On %s since %s:\n", d.user, appName, d.since.Format("Jan 2, 15:04"))
	tasks("UPDATES ON YOUR TASKS", d.Updates)
	if d.OlderUpdates > 0 && len(d.Updates) > 0 {
		fmt.Fprintf(&b, "\n  and %d %s from before, not read yet\n", d.OlderUpdates, plural(d.OlderUpdates, "update"))
	}
	if len(d.Updates) > 0 && s.BaseURL != "" {
		b.WriteString("\n  All of them: " + s.BaseURL + updatesHref + "\n")
	}
	if len(d.Mentions) > 0 {
		b.WriteString("\nMENTIONS\n")
		for i, m := range d.Mentions {
			if i == digestMost {
				fmt.Fprintf(&b, "\n  and %d more\n", len(d.Mentions)-i)
				break
			}
			b.WriteString("\n  " + oneLine(m.Title) + "\n  " + m.By + " mentioned you\n")
			link(m.Href)
		}
		if d.OlderMentions > 0 {
			fmt.Fprintf(&b, "\n  and %d %s from before, not read yet\n", d.OlderMentions, plural(d.OlderMentions, "mention"))
		}
		if s.BaseURL != "" {
			b.WriteString("\n  All of them: " + s.BaseURL + updatesHref + "\n")
		}
	}
	if len(d.Pages) > 0 {
		b.WriteString("\nPAGES YOU WATCH\n")
		for i, p := range d.Pages {
			if i == digestMost {
				fmt.Fprintf(&b, "\n  and %d more\n", len(d.Pages)-i)
				break
			}
			b.WriteString("\n  " + p.Slug)
			if p.By != "" {
				b.WriteString(", changed by " + p.By)
			}
			b.WriteString("\n")
			link(pathURL("page", p.Slug))
		}
		if d.OlderPages > 0 {
			fmt.Fprintf(&b, "\n  and %d %s changed before, not looked at yet\n", d.OlderPages, plural(d.OlderPages, "page"))
		}
		if s.BaseURL != "" {
			b.WriteString("\n  What changed: " + s.BaseURL + watchedHref + "\n")
		}
	}
	tasks("DUE TODAY", d.DueToday)
	tasks("OVERDUE", d.Overdue)
	if s.BaseURL != "" && len(d.DueToday)+len(d.Overdue) > 0 {
		b.WriteString("\n  Your tasks: " + s.BaseURL + mineHref + "\n")
	}
	if s.mayViewerDigest(d.user) {
		b.WriteString(mailFooter(s, "on a morning when pages you watch changed or a page mentions you"))
	} else {
		b.WriteString(mailFooter(s, "on a morning that has something new for you or due today"))
	}
	return subject, b.String()
}
