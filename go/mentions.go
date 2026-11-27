// Mentions on pages (Mentions on pages in docs/spec/navigation.md): @name
// in a page's text tells that user, once, when a commit adds the name: on
// Updates (updates.go), by mail for who turned it on (notify.go), and in
// the morning's mail (digest.go). Found where every commit of a page is
// made (gitCommit), so the editor's Done, a section's edit and an
// assistant's write are told alike; a file changed on disk, an import and
// a write of many pages at once tell nobody. A mention is kept with what
// its user watches (watched.Mentions), theirs alone, until they open the
// page or mark everything read.
package main

import (
	"os"
	"path/filepath"
	"slices"
	"sort"
	"strings"
	"time"
	"zonewiki/internal/mdtext"
)

// mentionMostPages: a commit that changes more pages than this tells
// nobody: a folder moved, a tag renamed, a text replaced in many pages is
// not someone writing to someone.
const mentionMostPages = 20

// pageMention: a mention of a user on a page, in their watch file by the
// page's slug: when, by whom, and the commit that added the name.
type pageMention struct {
	At  int64  `json:"at"`
	By  string `json:"by"`
	Sha string `json:"sha,omitempty"`
}

// namesIn: the users text names with @name, each once, in the text's
// order: those ok says yes to, not in code, not the @ of a mail address
// (mentionRE).
func namesIn(text string, ok func(name string) bool) []string {
	if !strings.Contains(text, "@") {
		return nil
	}
	masked, _ := mdtext.MaskCode(text)
	var out []string
	for _, m := range mentionRE.FindAllStringSubmatch(strings.ToLower(masked), -1) {
		if name := m[1]; !slices.Contains(out, name) && ok(name) {
			out = append(out, name)
		}
	}
	return out
}

// canBeTold: name is a user who can be told something: not disabled, not
// waiting for approval. A viewer too: they read pages, and can be named on
// one.
func (s *Settings) canBeTold(name string) bool {
	u := s.Users[name]
	return u != nil && !u.Disabled && !u.Pending
}

// pageTextNow: the text of the wiki's file rel ("/" between) as it is on
// disk, ok false when it isn't there.
func pageTextNow(rel string) (string, bool) {
	b, err := os.ReadFile(filepath.Join(cfg.Data, filepath.FromSlash(rel)))
	return string(b), err == nil
}

// mentionable: the pages among a commit's changed paths (in the wiki, "/"
// between) that can tell of a mention: markdown files outside Zonewiki's own
// folders, as slugs by their paths.
func mentionable(changed []string) map[string]string {
	out := map[string]string{}
	for _, rel := range changed {
		slug, isPage := strings.CutSuffix(rel, ".md")
		first, _, _ := strings.Cut(rel, "/")
		if !isPage || strings.HasPrefix(first, ".") || strings.HasPrefix(first, "_") {
			continue
		}
		out[rel] = slug
	}
	return out
}

// mentionsBefore: who the pages a commit is about to change named before
// it (their text in HEAD), for what the commit adds: all of them together,
// so a page that only moved names nobody anew. nil when the commit can
// tell nobody (no users, nobody's write, too many pages).
func mentionsBefore(by string, changed []string) (pages map[string]string, before []string, tells bool) {
	s := settings()
	if s.Users == nil || by == "" || by == visitor {
		return nil, nil, false
	}
	pages = mentionable(changed)
	if len(pages) == 0 || len(pages) > mentionMostPages {
		return nil, nil, false
	}
	// Only where a page names someone now: most commits read nothing more.
	named := false
	for rel := range pages {
		if text, ok := pageTextNow(rel); ok && strings.Contains(text, "@") {
			named = true
			break
		}
	}
	if !named {
		return nil, nil, false
	}
	for rel := range pages {
		if r := runGit("--literal-pathspecs", "show", "HEAD:"+rel); r.Code == 0 {
			for _, name := range namesIn(r.Stdout, s.canBeTold) {
				if !slices.Contains(before, name) {
					before = append(before, name)
				}
			}
		}
	}
	return pages, before, true
}

// tellMentions notes, after by's commit sha of pages, who their text names
// that before didn't name (mentionsBefore), for each who reads the page:
// in their own file, and by mail for who turned that on. Not for naming
// oneself.
func tellMentions(by, sha string, pages map[string]string, before []string) {
	s := settings()
	for _, rel := range sortedKeys(pages) {
		slug := pages[rel]
		text, ok := pageTextNow(rel)
		if !ok {
			continue // the page went with this commit
		}
		for _, name := range namesIn(text, s.canBeTold) {
			if name == by || slices.Contains(before, name) || !canSee(asUser(name), slug) {
				continue
			}
			changeWatches(name, func(w *watched) bool {
				if w.Mentions == nil {
					w.Mentions = map[string]pageMention{}
				}
				w.Mentions[slug] = pageMention{At: now().Unix(), By: by, Sha: sha}
				return true
			})
			if mails(s, name, "mentioned") {
				pageMentionMail(s, name, by, slug)
			}
		}
	}
}

// pageMentionMail: the mail about a mention on a page, sent in the
// background (a write doesn't wait for a mail server); a failure is logged.
func pageMentionMail(s *Settings, name, by, slug string) {
	title := mdtext.SlugDisplayName(slug)
	var b strings.Builder
	b.WriteString(by + " mentioned you on " + appName + ", on this page:\n\n  " + oneLine(title) + "\n")
	if s.BaseURL != "" {
		b.WriteString("  " + s.BaseURL + mentionHref(slug, name) + "\n")
	}
	b.WriteString(mailFooter(s, "when someone mentions you"))
	to, subject, body := s.Users[name].Email, by+" mentioned you: "+oneLine(title), b.String()
	go func() {
		if err := sendMail(cfg.Mail, to, subject, body); err != nil {
			logf("mail to %s about a mention on %s: %v", name, slug, err)
		}
	}()
}

// mentionHref: the page slug, at the place that names name: the browser
// scrolls to the text and marks it (a text fragment; one that doesn't know
// them opens the page).
func mentionHref(slug, name string) string {
	return pathURL("page", slug) + "#:~:text=%40" + name
}

// shownMention: a mention as Updates and the morning's mail show it.
type shownMention struct {
	Slug, Title, By, Href string
	At                    time.Time
}

// mentionsFor: the mentions of user on pages they haven't opened since,
// the latest first: of pages that are there and that they see (a page
// moved or narrowed since tells nothing).
func mentionsFor(user asker) []shownMention {
	if user.Name == "" || user.Name == visitor || settings().Users == nil {
		return nil
	}
	var out []shownMention
	for slug, m := range watchesOf(user.Name).Mentions {
		if !canSee(user, slug) || !exists(filepath.Join(cfg.Data, filepath.FromSlash(slug+".md"))) {
			continue
		}
		out = append(out, shownMention{Slug: slug, Title: mdtext.SlugDisplayName(slug), By: m.By,
			Href: mentionHref(slug, user.Name), At: time.Unix(m.At, 0)})
	}
	sort.Slice(out, func(i, j int) bool {
		if !out[i].At.Equal(out[j].At) {
			return out[i].At.After(out[j].At)
		}
		return out[i].Slug < out[j].Slug
	})
	return out
}

// readMention: user opened the page slug: its mention of them is read.
// all: every mention is (Mark all read).
func readMention(user, slug string, all bool) {
	if len(watchesOf(user).Mentions) == 0 {
		return
	}
	changeWatches(user, func(w *watched) bool {
		if all {
			had := len(w.Mentions) > 0
			w.Mentions = nil
			return had
		}
		if _, has := w.Mentions[slug]; !has {
			return false
		}
		delete(w.Mentions, slug)
		return true
	})
}

// mentionChips: whether @name is shown as a chip to the request's reader,
// on the page slug: the name of a user who is told by it (canBeTold, and
// sees the page), and whether it is the reader's own. On no page ("": a
// task's text) the name of a user a task can be given to, as a task's
// mentions go (mentionsOf). nil, so every name stays text, without users
// and for a visitor: who isn't signed in isn't shown who the users are.
func mentionChips(rq *Req, slug string) func(name string) (chip, me bool) {
	s := settings()
	if rq == nil || s.Users == nil || rq.who().Name == "" || rq.who().Name == visitor {
		return nil
	}
	told := mentionableOn(rq.who(), slug, true)
	return func(name string) (bool, bool) {
		return slices.Contains(told, name), name == rq.who().Name
	}
}

// mentionableOn: the users a text on the page slug can name, so that they
// are told, as user may know them: who sees the page and can be told, on
// no page ("") who a task can be given to. Nobody for a page user doesn't
// see. With self, user's own name too (it is shown as a chip; naming
// oneself tells nobody).
func mentionableOn(user asker, slug string, self bool) []string {
	s := settings()
	if s.Users == nil || user.Name == "" || user.Name == visitor {
		return nil
	}
	var names []string
	switch {
	case slug == "":
		names = s.assignableUsers()
	case !canSee(user, slug):
		return nil
	default:
		names = slices.DeleteFunc(placeSeers(placeOf(slug)), func(name string) bool { return !s.canBeTold(name) })
	}
	if !self {
		names = slices.DeleteFunc(slices.Clone(names), func(name string) bool { return name == user.Name })
	}
	return names
}
