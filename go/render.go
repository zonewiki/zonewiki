// A page's markdown to HTML: Zone's directives
// (wikilinks, [[tasks:]], [[task:…]]) around markdown.go, and the HTML after
// it (task chips, task list embeds, the frontmatter block, sources).
package main

import (
	"fmt"
	"html"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode"
	"zonewiki/internal/mdtext"
)

// wikilinker: Env.Wikilink for a render, [[Page]], [[Page|Label]],
// [[Page#Section]], [[#Section]] as links; to a missing page with class
// wikilink-missing, to a page hidden in sharing mode as its label only.
// slugs: the wiki's pages (nil: listed now). from: the page the text is on
// ("" for none, a task's notes): on one of the user's own private pages a
// link to a missing page that would be shared is marked
// (wikilink-shared-new) and carries ?from=private, so the editor it opens
// warns that the new page is shared and offers it as a private one; on a
// shared page a link to the user's private page is marked
// (wikilink-private), since others see it as plain text. A section's
// anchor is made from its code as written ([[Page#Run `make` now]]).
func wikilinker(rq *Req, slugs map[string]bool, from string) func(inner string) (open, label, close string) {
	if slugs == nil {
		slugs = rq.slugSet()
	}
	return func(inner string) (string, string, string) {
		page, section, label := mdtext.SplitWikilink(inner)
		anchor := ""
		if section != "" {
			anchor = "#" + html.EscapeString(mdtext.SectionAnchor(section))
		}
		if page == "" {
			if anchor == "" {
				anchor = "#"
			}
			return `<a href="` + anchor + `" class="wikilink">`, label, `</a>`
		}
		slug := page
		if !slugs[page] {
			slug = mdtext.ToSlug(page)
		}
		if hiddenPage(rq, slug) {
			// Sharing mode: the words stay (they are this page's), the link goes.
			return `<span class="wikilink-hidden">`, label, `</span>`
		}
		css, query, title := "wikilink", "", ""
		_, targetPrivate := pageOwner(slug)
		_, fromPrivate := pageOwner(from)
		if !slugs[slug] {
			css += " wikilink-missing"
			if from != "" && fromPrivate && !targetPrivate {
				css += " wikilink-shared-new"
				query, title = "?from=private", ` title="Creates a shared page"`
			}
		} else if from != "" && !fromPrivate && targetPrivate {
			css += " wikilink-private"
			title = ` title="Links to a private page: others see plain text"`
		}
		return `<a href="` + pathURL("page", slug) + query + anchor + `" class="` + css + `"` + title + `>`, label, `</a>`
	}
}

// RenderOpts: what a page view adds to a render. EditSlug gives each ## and
// ### heading its ✎, SectionTasks (findTaskBacklinks) a heading tasks link
// to its ☑ N chip; PreferLists are lists a task chip links into.
type RenderOpts struct {
	Page               string // the page rendered, "" none: its task list embeds make private tasks in the user's own folder, its links are marked by where it is (a preview gets it as ?page=)
	HideTags, HideTldr bool
	PreferLists        []string
	EditSlug           string
	SectionTasks       []TaskLink
	HeadingIDs         headingIDs // as data-pid in a preview (Env.HeadingIDs)
}

// renderMd renders a page's markdown.
func renderMd(rq *Req, text string, o RenderOpts) string {
	meta, text := mdtext.ParseFrontmatter(text)
	var prefer []string
	for _, l := range pageTaskLists(text) {
		prefer = append(prefer, l.Name)
	}
	prefer = append(prefer, o.PreferLists...)
	fmHTML := renderFrontmatterHTML(meta, !o.HideTags, !o.HideTldr)
	text, stash := mdtext.MaskCode(text)
	text, embeds := processTasksEmbeds(rq, text, o.Page != "" && ownPrivate(rq.who(), o.Page))
	text, refs := maskTaskRefs(text)
	text = mdtext.UnmaskCode(escapeWikilinkPipes(text), stash)
	env := &Env{EditSlug: o.EditSlug, SectionChips: sectionTaskChips(rq, o.SectionTasks, prefer), Host: requestHost(rq),
		HeadingIDs: o.HeadingIDs, Wikilink: wikilinker(rq, nil, o.Page)}
	result := markdown(text, env, false)
	result = injectTaskRefs(rq, result, refs, prefer)
	if len(embeds) > 0 {
		result = injectTasksEmbeds(result, embeds)
	}
	return fmHTML + result + renderSourcesHTML(rq, meta.Sources, result, o.HeadingIDs)
}

func requestHost(rq *Req) string {
	if rq == nil || rq.r == nil {
		return ""
	}
	return rq.r.Host
}

// renderNotes renders task notes (or, inline, comments: one line each)
// as a page is, without what only a page has (frontmatter, [[tasks:]], ✎) and
// without heading ids, which would clash with the page's own.
func renderNotes(rq *Req, texts []string, inline bool) []string {
	links := wikilinker(rq, nil, "")
	out := make([]string, len(texts))
	for i, text := range texts {
		text, stash := mdtext.MaskCode(text)
		text, refs := maskTaskRefs(text)
		text = mdtext.UnmaskCode(escapeWikilinkPipes(text), stash)
		result := markdown(text, &Env{Host: requestHost(rq), HeadingIDs: headingNoID, Wikilink: links}, inline)
		out[i] = injectTaskRefs(rq, result, refs, nil)
	}
	return out
}

// [[task:<id>]] references: masked with private-use tokens before
// rendering, so markdown never touches a chip, then swapped for it. Text of
// the page's own that looks like a token is masked too and comes back as
// written (a ref starting with ).
var (
	taskRefTokenRE  = regexp.MustCompile("(\\d+)")
	taskRefMaskedRE = regexp.MustCompile("(\\d+)|" + `\[\[task:([0-9a-fA-F][0-9a-fA-F-]{3,35})\]\]`)
	// A reference alone in a list item is a row of its own.
	taskRefItemRE = regexp.MustCompile(`^(?:` + mdtext.SpaceClass + `|<p>)*` + "\\d+" + `(?:` + mdtext.SpaceClass + `|</p>)*</li>`)
)

func maskTaskRefs(text string) (string, []string) {
	var refs []string
	text = taskRefMaskedRE.ReplaceAllStringFunc(text, func(m string) string {
		g := taskRefMaskedRE.FindStringSubmatch(m)
		if g[2] == "" {
			refs = append(refs, m)
		} else {
			refs = append(refs, strings.ToLower(g[2]))
		}
		return "" + strconv.Itoa(len(refs)-1) + ""
	})
	return text, refs
}

func injectTaskRefs(rq *Req, html string, refs, prefer []string) string {
	if len(refs) == 0 {
		return html
	}
	var ids []string
	for _, r := range refs {
		if !strings.HasPrefix(r, "") {
			ids = append(ids, r)
		}
	}
	tasks := resolveTaskRefs(rq.who(), ids)
	day := isoDay(today())
	// <li> before a reference alone in its item: <li class="task-ref-item">.
	var b strings.Builder
	for {
		i := strings.Index(html, "<li>")
		if i < 0 {
			b.WriteString(html)
			break
		}
		b.WriteString(html[:i])
		if taskRefItemRE.MatchString(html[i+4:]) {
			b.WriteString(`<li class="task-ref-item">`)
		} else {
			b.WriteString("<li>")
		}
		html = html[i+4:]
	}
	lists := rq.taskLists()
	return taskRefTokenRE.ReplaceAllStringFunc(b.String(), func(m string) string {
		i, err := strconv.Atoi(taskRefTokenRE.FindStringSubmatch(m)[1])
		if err != nil || i >= len(refs) {
			return m
		}
		ref := refs[i]
		if strings.HasPrefix(ref, "") {
			return ref
		}
		return taskRefHTML(ref, tasks[ref], day, prefer, lists)
	})
}

// shortDate: "Oct 5" (this year) or "Oct 5, 2025", as the task app's cards show
// dates, in a <time class="local-date"> the browser rewrites in the locale
// setting (js/util.js localizeDates); "2025-10-05" with date-format = iso.
func shortDate(iso, day string) string {
	d, err := time.Parse("2006-01-02", runePrefix(iso, 10))
	if err != nil {
		return html.EscapeString(iso)
	}
	text := d.Format("Jan 2")
	if settings().DateFormat == "iso" {
		text = d.Format("2006-01-02")
	} else if runePrefix(iso, 4) != runePrefix(day, 4) {
		text += d.Format(", 2006")
	}
	return `<time class="local-date" datetime="` + d.Format("2006-01-02") + `">` + text + `</time>`
}

// linkList: the list a link to a task opens it in: one of prefer (the
// lists the linking page declares) when the task is in it, else its own.
func linkList(t *Task, prefer []string, lists map[string]TasksHome) string {
	tags := taskTagsLower(t)
	var hits []string
	for _, p := range prefer {
		if tags[strings.ToLower(p)] {
			hits = append(hits, strings.ToLower(p))
		}
	}
	if len(hits) > 0 {
		sort.Strings(hits)
		return hits[0]
	}
	return taskList(t, lists)
}

// taskRefHTML: a task as an inline chip: its checkbox (completes it, js/page.js),
// its title linking to it in the tasks, badges for what its card shows.
func taskRefHTML(ref string, t *Task, day string, prefer []string, lists map[string]TasksHome) string {
	if t == nil {
		return `<span class="task-ref task-ref-missing" title="No task with id ` + html.EscapeString(ref) + `">task ` + html.EscapeString(ref) + `?</span>`
	}
	title := html.EscapeString(mdtext.PlainTaskTitle(t.Title))
	done, wont := !t.isOpen(), t.WontDo
	list := linkList(t, prefer, lists)
	href := html.EscapeString(taskLink(t, list))
	cls := "task-ref"
	var badges []string
	if done {
		mark := "✓"
		if wont {
			cls += " task-ref-wontdo"
			mark = "✗ won't do"
		} else {
			cls += " task-ref-done"
		}
		badges = append(badges, `<span class="task-ref-badge">`+mark+" "+shortDate(t.CompletedAt, day)+`</span>`)
	} else {
		due, start := t.DueDate, t.StartDate
		if due != "" {
			state := ""
			if due < day {
				state = " overdue"
			} else if due == day {
				state = " due-today"
			}
			badges = append(badges, `<span class="task-ref-badge task-ref-due`+state+`" title="Due `+html.EscapeString(due)+`">⏰ `+shortDate(due, day)+`</span>`)
		}
		if start != "" && start >= day {
			state, label := " start-later", shortDate(start, day)
			if start == day {
				state, label = " start-today", "today"
			}
			badges = append(badges, `<span class="task-ref-badge task-ref-start`+state+`" title="Start `+html.EscapeString(start)+`">📅 `+label+`</span>`)
		}
		if t.Recurrence != nil {
			badges = append(badges, `<span class="task-ref-badge" title="Repeats">↻</span>`)
		}
		if t.Keep {
			last := ""
			if t.LastDone != "" {
				last = " ✓ " + shortDate(t.LastDone, day)
			}
			badges = append(badges, `<span class="task-ref-badge" title="Keep task: stays after done">⟲`+last+`</span>`)
		}
	}
	if list != "" {
		badges = append(badges, `<span class="task-ref-badge task-ref-list">`+html.EscapeString(list)+`</span>`)
	}
	checked, disabled := "", ""
	if done && !wont {
		checked = " checked"
	}
	if wont {
		disabled = " disabled"
	}
	return `<span class="` + cls + `" data-task-id="` + html.EscapeString(t.ID) + `">` +
		`<input type="checkbox" class="task-ref-check" aria-label="Done: ` + title + `"` + checked + disabled + `>` +
		`<a class="task-ref-title" href="` + href + `">` + title + `</a>` + strings.Join(badges, "") + `</span>`
}

// sectionTaskChips: {heading id: its ☑ N chip}: a section that tasks link
// to lists them in a <details> after its heading.
func sectionTaskChips(rq *Req, tasks []TaskLink, prefer []string) map[string]string {
	var order []string
	byAnchor := map[string][]TaskLink{}
	for _, t := range tasks {
		if t.Anchor != "" {
			if _, ok := byAnchor[t.Anchor]; !ok {
				order = append(order, t.Anchor)
			}
			byAnchor[t.Anchor] = append(byAnchor[t.Anchor], t)
		}
	}
	chips := map[string]string{}
	if len(order) == 0 {
		return chips
	}
	lists := rq.taskLists()
	for _, anchor := range order {
		ts := byAnchor[anchor]
		var items strings.Builder
		for _, t := range ts {
			fmt.Fprintf(&items, `<li><a href="%s">%s</a></li>`,
				html.EscapeString(taskHref(t.ID, "open", linkList(taskLinkObj(t), prefer, lists), "")),
				html.EscapeString(mdtext.PlainTaskTitle(t.Title)))
		}
		s := ""
		if len(ts) > 1 {
			s = "s"
		}
		chips[anchor] = fmt.Sprintf(`<details class="section-tasks"><summary title="%d linked task%s">☑ %d</summary><ul>%s</ul></details>`,
			len(ts), s, len(ts), items.String())
	}
	return chips
}

// taskLinkObj is a TaskLink as a task, for linkList.
func taskLinkObj(t TaskLink) *Task {
	return &Task{ID: t.ID, Title: t.Title, Tags: t.Tags}
}

// renderTagsPillsHTML: tags as pills searching #tag (pages and tasks with
// that tag); a tag with a space in it can't be #tag and stays a word search.
func renderTagsPillsHTML(tags []string) string {
	if len(tags) == 0 {
		return ""
	}
	var b strings.Builder
	for _, t := range tags {
		q := "#" + t
		if strings.IndexFunc(t, unicode.IsSpace) >= 0 {
			q = t
		}
		fmt.Fprintf(&b, `<a class="task-tag page-tag" href="/search?%s">%s</a>`,
			url.Values{"q": {q}}.Encode(), html.EscapeString(t))
	}
	return `<div class="page-tags">` + b.String() + `</div>`
}

// renderFrontmatterHTML: tags and tldr as a collapsed block (the page view
// shows both in its meta row instead).
func renderFrontmatterHTML(meta mdtext.Frontmatter, showTags, showTldr bool) string {
	if len(meta.Tags) == 0 && meta.Tldr == "" && len(meta.Sources) == 0 {
		return ""
	}
	var parts []string
	if showTldr && meta.Tldr != "" {
		parts = append(parts, `<p class="page-tldr">`+html.EscapeString(meta.Tldr)+`</p>`)
	}
	if showTags {
		if pills := renderTagsPillsHTML(meta.Tags); pills != "" {
			parts = append(parts, pills)
		}
	}
	if len(parts) == 0 {
		return ""
	}
	return `<details class="page-frontmatter"><summary>tldr</summary>` + strings.Join(parts, "") + `</details>`
}

// renderSourcesHTML: frontmatter sources as a list at the article's end,
// each inline markdown, under a ## heading with id "sources" (unless the
// article has one of that id), carried as ids says (data-pid in a preview).
func renderSourcesHTML(rq *Req, sources []string, article string, ids headingIDs) string {
	if len(sources) == 0 {
		return ""
	}
	// Its id as the article's headings have theirs (Env.HeadingIDs).
	attr := map[headingIDs]string{headingAsID: "id", headingPID: "data-pid"}[ids]
	hid := "sources"
	for n := 1; attr != "" && strings.Contains(article, attr+`="`+hid+`"`); n++ {
		hid = "sources-" + strconv.Itoa(n)
	}
	env := &Env{Host: requestHost(rq), Wikilink: wikilinker(rq, nil, "")}
	var items strings.Builder
	for _, s := range sources {
		items.WriteString("<li>" + markdown(s, env, true) + "</li>")
	}
	h2 := "<h2>"
	if attr != "" {
		h2 = `<h2 ` + attr + `="` + hid + `">`
	}
	return `<section class="page-sources">` + h2 + `Sources</h2><ul>` + items.String() + `</ul></section>`
}

// taskHref: the link that opens one task in the tasks on tab; year: the
// Completed year of a task in an earlier year's archive.
func taskHref(id, tab, list, year string) string {
	q := []string{"task=" + url.QueryEscape(id), "view=" + url.QueryEscape(tab)}
	if list != "" {
		q = append(q, "list="+url.QueryEscape(list))
	}
	if year != "" {
		q = append(q, "year="+url.QueryEscape(year))
	}
	return "/tasks?" + strings.Join(q, "&")
}

// taskLink: taskHref for a task as it is now: open on Open, done on
// Completed (in its year's archive for an earlier year).
func taskLink(t *Task, list string) string {
	if t.isOpen() {
		return taskHref(t.ID, "open", list, "")
	}
	return taskHref(t.ID, "completed", list, pastYear(t))
}
