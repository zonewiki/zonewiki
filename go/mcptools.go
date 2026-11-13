// The MCP tools that read (search_wiki, grep_wiki, read_page, list_pages,
// check_links, list_tags, history, zone_help), and the table of all the
// tools with their dispatch (mcpToolTable, callMCPTool). The tools that
// write pages are in mcpwrite.go and mcpreplace.go, the task tools in
// mcptasks.go, read_guides in mcpguides.go, what they share in mcpargs.go;
// the protocol is in mcp.go.
package main

import (
	"errors"
	"fmt"
	"maps"
	"net/http"
	"os"
	"slices"
	"sort"
	"strconv"
	"strings"
	"zonewiki/internal/jsonobj"
	"zonewiki/internal/mdtext"
	"zonewiki/internal/textsearch"
)

// ── Reading ─────────────────────────────────────────────────────────────────

// mcpPageLinks: for read_page, the pages that link to it and its links that
// lead nowhere, so the model sees where a page sits without reading others.
// Of the pages user may see; a link to one they may not isn't broken.
func mcpPageLinks(slug, content, user string) string {
	var linking []string
	for _, b := range findBacklinks(slug) {
		if canSee(user, b.Slug) {
			linking = append(linking, b.Slug)
		}
	}
	shown := strings.Join(linking[:min(len(linking), backlinksShown)], ", ")
	if shown == "" {
		shown = "no other page"
	}
	if len(linking) > backlinksShown {
		shown += fmt.Sprintf(", … and %d more", len(linking)-backlinksShown)
	}
	out := fmt.Sprintf("\n\n---\nLinked from (%d): %s", len(linking), shown)
	if missing := missingLinks(content, slugSet(), slug, user); len(missing) > 0 {
		out += "\nBroken links (no such page): " + strings.Join(sortedKeys(missing), ", ")
	}
	if semantic != nil {
		// As the page view's Related: pages close in meaning that aren't
		// linked either way, the connections not written down yet.
		semantic.poke()
		_, targets := pageMeta(safePath(slug), slug)
		linked := maps.Clone(targets)
		for _, s := range linking {
			linked[s] = true
		}
		if related := semantic.related(slug, visibleSlugs(user), linked, 5); len(related) > 0 {
			out += "\nRelated, not linked (close in meaning): " + joinIDs(related)
		}
	}
	return out
}

func readPage(args jsonObj, base, user string) (string, error) {
	raw, ok := jsonobj.As[string](args["slug"])
	if !ok || strings.TrimSpace(raw) == "" {
		return "", toolErr("slug must be a non-empty string")
	}
	slug := mcpPageSlug(raw, user)
	if slug == "" {
		return "", toolErr("No page " + strconv.Quote(raw) + ". Use search_wiki or list_pages to find its slug.")
	}
	content, err := readPageText(safePath(slug))
	if err != nil {
		return "", err
	}
	// The whole page's hash, whatever part is read: write_page's base_hash.
	head := "slug: " + slug + "\n" + pageInfo(slug, base) + "\nhash: " + keepGivenBase(content) + "\n"
	heading, err := strArg(args, "heading", false)
	if err != nil {
		return "", err
	}
	if heading = strings.TrimSpace(heading); heading != "" {
		lines := mdtext.SplitLines(content)
		start, end, ok := sectionLines(lines, heading)
		if !ok {
			return "", toolErr(noHeading(slug, content, heading) + " Leave out heading for the whole page.")
		}
		// Line numbers as grep_wiki gives them, so the two fit together.
		head += fmt.Sprintf("section: lines %d–%d of %d\n", start+1, end, len(lines))
		content = trimNewlines(strings.Join(lines[start:end], "")) + "\n"
	} else {
		head += guideIDLine(slug, content, user)
	}
	return head + "\n" + content + mcpTaskRefs(content, base, user) + mcpPageLinks(slug, content, user), nil
}

// isOrphan: no other page links to it. The home page, the default list's page and
// notes are reached otherwise (the module switcher), and the guide
// is read by the assistant, so they never are.
func isOrphan(slug string, counts map[string]int, home string) bool {
	return counts[slug] == 0 && !isProtected(slug) && slug != home && slug != guideSlug && !isQuicknote(slug)
}

func listPages(args jsonObj, base, user string) (string, error) {
	limit, err := mcpInt(args, "limit", 100, 1000)
	if err != nil {
		return "", err
	}
	under, err := underArg(args)
	if err != nil {
		return "", err
	}
	orphans, err := boolArg(args, "orphans")
	if err != nil {
		return "", err
	}
	onlyQuicknote, err := boolArg(args, "notes")
	if err != nil {
		return "", err
	}
	counts := allBacklinkCounts(&Req{user: user})
	home := ""
	if h := lookupDefaultList(user); h != nil {
		home = h.Slug
	}
	type row struct {
		mtime       int64
		slug, title string
	}
	var rows []row
	for _, p := range visiblePages(user) {
		if !inFolder(p.Slug, under) || orphans && !isOrphan(p.Slug, counts, home) || onlyQuicknote && !inQuicknote(user, p.Slug) {
			continue
		}
		st, err := os.Stat(p.Path)
		if err != nil {
			continue
		}
		title, _ := pageMeta(p.Path, p.Slug)
		rows = append(rows, row{st.ModTime().UnixNano(), p.Slug, title})
	}
	sort.Slice(rows, func(i, j int) bool {
		a, b := rows[i], rows[j]
		if a.mtime != b.mtime {
			return a.mtime > b.mtime
		}
		if a.slug != b.slug {
			return a.slug > b.slug
		}
		return a.title > b.title
	})
	where := underWhere(under)
	if len(rows) == 0 {
		switch {
		case onlyQuicknote:
			return "No notes" + where + ".", nil
		case orphans:
			return "Every page" + where + " is linked from another page.", nil
		case under != "":
			return "No pages under " + strconv.Quote(under) + ".", nil
		}
		return "The wiki has no pages.", nil
	}
	var lines []string
	for _, r := range rows[:min(limit, len(rows))] {
		lines = append(lines, fmt.Sprintf("%s | %s | edited %s | %d", r.slug, r.title,
			fmtDate(unixNano(r.mtime)), counts[r.slug]))
	}
	shown := fmt.Sprintf("all %d", len(rows))
	if len(lines) < len(rows) {
		shown = fmt.Sprintf("%d of %d", len(lines), len(rows))
	}
	what := "Pages"
	if onlyQuicknote {
		what = "Notes"
	} else if orphans {
		what = "Pages no other page links to (the home page, the Tasks home and notes aside)"
	}
	return what + where + ", most recently edited first (" + shown + "); " +
		"slug | title | last edit | pages linking to it:\n" + strings.Join(lines, "\n"), nil
}

func checkLinks(args jsonObj, base, user string) (string, error) {
	under, err := underArg(args)
	if err != nil {
		return "", err
	}
	// A link to a page user may not see isn't broken, nor suggested.
	slugs := slugSet()
	var all []string
	for _, s := range allSlugs() {
		if canSee(user, s) {
			all = append(all, s)
		}
	}
	var out []string
	total := 0
	for _, p := range sortedPages() {
		if !inFolder(p.Slug, under) || !canSee(user, p.Slug) {
			continue
		}
		content, err := readPageText(p.Path)
		if err != nil {
			continue
		}
		missing := missingLinks(content, slugs, p.Slug, user)
		if len(missing) == 0 {
			continue
		}
		var links []string
		for _, link := range sortedKeys(missing) {
			name := missing[link]
			if strings.HasPrefix(link, "[[") {
				name = mdtext.ToSlug(name)
			}
			if near := textsearch.CloseMatches(name, all, 1, 0.8); len(near) > 0 {
				link += " (maybe [[" + near[0] + "]]?)"
			}
			links = append(links, link)
		}
		total += len(links)
		out = append(out, p.Slug+": "+strings.Join(links, ", "))
	}
	where := underWhere(under)
	if len(out) == 0 {
		return "No broken links" + where + ".", nil
	}
	return fmt.Sprintf("%d links%s point to pages that don't exist, on %d pages "+
		"(fix the link, or create the page):\n", total, where, len(out)) + strings.Join(out, "\n"), nil
}

// How much of a long line grep_wiki and replace_in_pages show.
const grepLineChars = 300

func cutLine(line string) string { return cutRunes(strings.TrimSpace(line), grepLineChars, " …") }

func grepWiki(args jsonObj, base, user string) (string, error) {
	pattern, ok := jsonobj.As[string](args["pattern"])
	if !ok || strings.TrimSpace(pattern) == "" {
		return "", toolErr("pattern must be a non-empty string")
	}
	under, err := underArg(args)
	if err != nil {
		return "", err
	}
	limit, err := mcpInt(args, "limit", 50, 500)
	if err != nil {
		return "", err
	}
	ignoreCase, err := boolArg(args, "ignore_case")
	if err != nil {
		return "", err
	}
	found, err := grepPages(pattern, ignoreCase, visibleSlugs(user))
	if rf, ok := refusalOf(err); ok {
		return "", toolErr("grep_wiki: " + rf.msg)
	}
	var hits []GrepHit
	pages := map[string]bool{}
	for _, h := range found {
		if inFolder(h.Slug, under) {
			hits = append(hits, h)
			pages[h.Slug] = true
		}
	}
	where := underWhere(under)
	if len(hits) == 0 {
		return "No line" + where + " matches " + strconv.Quote(pattern) + ".", nil
	}
	var out []string
	last := ""
	for _, h := range hits[:min(limit, len(hits))] {
		if h.Slug != last {
			if len(out) > 0 {
				out = append(out, "")
			}
			out = append(out, h.Slug)
			last = h.Slug
		}
		out = append(out, fmt.Sprintf("  %d: %s", h.Number, cutLine(h.Line)))
	}
	head := fmt.Sprintf("%d matching lines on %d pages%s", len(hits), len(pages), where)
	if len(hits) > limit {
		head += fmt.Sprintf(", the first %d shown (raise limit, or narrow the pattern or folder)", limit)
	}
	if !cfg.GitEnabled {
		head += "; git isn't installed, so the pattern was matched as plain text"
	}
	return head + ":\n" + strings.Join(out, "\n"), nil
}

// fileLabel: a changed file as the model knows it, a page by its slug, the
// task files as tasks.
func fileLabel(fp string) string {
	if strings.HasPrefix(fp, tasksDirName+"/") || strings.Contains(fp, "/"+tasksDirName+"/") {
		return "tasks"
	}
	return strings.TrimSuffix(fp, ".md")
}

func noGit() error { return toolErr("There is no history: git isn't installed where Zone runs.") }

func history(args jsonObj, base, user string) (string, error) {
	if !cfg.GitEnabled {
		return "", noGit()
	}
	limit, err := mcpInt(args, "limit", 20, 200)
	if err != nil {
		return "", err
	}
	raw, err := strArg(args, "slug", false)
	if err != nil {
		return "", err
	}
	version, err := strArg(args, "version", false)
	if err != nil {
		return "", err
	}
	raw, version = strings.TrimSpace(raw), strings.ToLower(strings.TrimSpace(version))
	if version != "" && raw == "" {
		return "", toolErr("version needs the page's slug too")
	}
	if raw == "" {
		since, err := dateArg(args, "since")
		if err != nil {
			return "", err
		}
		under, err := underArg(args)
		if err != nil {
			return "", err
		}
		return recentChanges(since, under, limit, user), nil
	}
	slug, err := pageOrNewSlug(raw, user) // a deleted page has a history too
	if err != nil {
		return "", err
	}
	path := safePath(slug)
	entries := pageLog(user, path)
	if len(entries) == 0 {
		return "", toolErr("No history for " + strconv.Quote(raw) + ". Use list_pages to find its slug.")
	}
	if version != "" {
		return pageChange(slug, path, entries, version)
	}
	var rows []string
	for _, e := range entries[:min(limit, len(entries))] {
		rows = append(rows, fmt.Sprintf("%s | %s | +%d -%d | %s", runePrefix(e.Sha, 7), e.Time, e.Added, e.Deleted,
			e.Msg+byAuthor(e.Author)))
	}
	shown := fmt.Sprintf("all %d", len(entries))
	if len(rows) < len(entries) {
		shown = fmt.Sprintf("%d of %d", len(rows), len(entries))
	}
	state := ""
	if !exists(path) {
		state = " (deleted: restore_page brings back a version)"
	} else if isUncommitted(path) {
		state = " (it has changes not committed yet, newer than the first version listed)"
	}
	return "Versions of " + slug + state + ", newest first (" + shown + "); version | when | lines added and " +
		"removed | what (\"(MCP)\": written by an assistant). history with a version shows " +
		"what it changed:\n" + strings.Join(rows, "\n"), nil
}

// byAuthor: " (by alice)" after a commit's message; nothing for Zone's own.
func byAuthor(author string) string {
	if author == "" {
		return ""
	}
	return " (by " + author + ")"
}

func versionOf(entries []GitEntry, version string) int {
	if !shaRE.MatchString(version) {
		return -1
	}
	return versionIndex(entries, version)
}

// pageChange: what one version changed on the page, as a diff.
func pageChange(slug, path string, entries []GitEntry, version string) (string, error) {
	i := versionOf(entries, version)
	if i < 0 {
		return "", toolErr(strconv.Quote(version) + " is not a version of " + slug + "; history lists them.")
	}
	e := entries[i]
	diff, _ := versionDiff(path, entries, e.Sha)
	if diff == "" {
		diff = "(no changes)"
	}
	return slug + ", version " + runePrefix(e.Sha, 7) + " of " + e.Time + ": " + e.Msg + "\n\n" + diff, nil
}

// recentChanges: the wiki's latest commits, as user sees them: only the
// files they may see, without a commit that changed none of those.
func recentChanges(since, under string, limit int, user string) string {
	entries := gitLogAll(since, under, limit, func(path string) bool { return canSeePath(user, path) })
	var out, pending []string
	for _, f := range gitUncommittedFiles() {
		if !canSeePath(user, f.Filepath) {
			continue
		}
		if label := fileLabel(f.Filepath); under == "" || inFolder(label, under) {
			if f.Rel != "" {
				pending = append(pending, label+" ("+f.Rel+")")
			} else {
				pending = append(pending, label+" (deleted)")
			}
		}
	}
	if len(pending) > 0 {
		out = append(out, "Not committed yet (edited in the browser or on disk): "+strings.Join(pending, ", "))
	}
	where, when := underWhere(under), ""
	if since != "" {
		when = " since " + since
	}
	if len(entries) == 0 {
		return strings.Join(append(out, "No changes"+where+when+"."), "\n\n")
	}
	var rows []string
	for _, e := range entries {
		var labels []string
		for _, f := range e.Files {
			if l := fileLabel(f); !slices.Contains(labels, l) {
				labels = append(labels, l)
			}
		}
		rows = append(rows, e.Time+" | "+runePrefix(e.Sha, 7)+" | "+hideNames(user, e.Msg)+byAuthor(e.Author)+" | "+
			strings.Join(labels, ", "))
	}
	theLimit := ""
	if len(rows) == limit {
		theLimit = ", the limit"
	}
	out = append(out, fmt.Sprintf("Changes%s%s, newest first (%d%s); when | version | what (\"(MCP)\": written by "+
		"an assistant) | pages:\n", where, when, len(rows), theLimit)+strings.Join(rows, "\n"))
	return strings.Join(out, "\n\n")
}

func listTags(args jsonObj, base, user string) (string, error) {
	counts := map[string]*[3]int{} // tag → pages, open tasks, done tasks
	count := func(tag string, column int) {
		if counts[tag] == nil {
			counts[tag] = &[3]int{}
		}
		counts[tag][column]++
	}
	for _, p := range visiblePages(user) {
		content, err := readPageText(p.Path)
		if err != nil {
			continue
		}
		seen := map[string]bool{}
		for _, t := range mdtext.PageTags(content) {
			if !seen[t] {
				seen[t] = true
				count(t, 0)
			}
		}
	}
	for _, t := range allTasks(user) {
		column := 2
		if taskStatus(t) == "open" {
			column = 1
		}
		for tag := range taskTagsLower(t) {
			count(tag, column)
		}
	}
	if len(counts) == 0 {
		return "No tags yet: neither pages (frontmatter tags:) nor tasks have any.", nil
	}
	lists := taskLists(user)
	tags := sortedKeys(counts)
	sum := func(t string) int { c := counts[t]; return c[0] + c[1] + c[2] }
	sort.SliceStable(tags, func(i, j int) bool { return sum(tags[i]) > sum(tags[j]) })
	var lines []string
	for _, t := range tags {
		c := counts[t]
		list := ""
		if _, ok := lists[t]; ok {
			list = " (a list)"
		}
		lines = append(lines, fmt.Sprintf("%s%s | %d | %d | %d", t, list, c[0], c[1], c[2]))
	}
	return "Tags in use, most used first; reuse these rather than inventing near-duplicates. " +
		"tag | pages | open tasks | done tasks:\n" + strings.Join(lines, "\n"), nil
}

// zoneHelp: Zone's documentation (manual.go), passages for a query, a doc
// or one of its sections, or the list of docs with their headings.
func zoneHelp(args jsonObj, base, user string) (string, error) {
	var query, topic, heading string
	for _, a := range []struct {
		name string
		v    *string
	}{{"query", &query}, {"topic", &topic}, {"heading", &heading}} {
		s, err := strArg(args, a.name, false)
		if err != nil {
			return "", err
		}
		*a.v = strings.TrimSpace(s)
	}
	topic = strings.TrimSuffix(strings.ToLower(topic), ".md")
	docs := manualTopics()
	if len(docs) == 0 {
		return "", toolErr("Zone's documentation isn't installed with this server.")
	}
	var names []string
	paths := map[string]string{}
	for _, d := range docs {
		names = append(names, d.name)
		paths[d.name] = d.path
	}
	if topic != "" {
		path, ok := paths[topic]
		if !ok {
			return "", toolErr("No topic " + strconv.Quote(topic) + ". Topics: " + strings.Join(names, ", ") + ".")
		}
		text, err := readText(path)
		if err != nil {
			return "", err
		}
		if heading == "" {
			return "topic: " + topic + "\n\n" + text, nil
		}
		lines := mdtext.SplitLines(text)
		start, end, ok := sectionLines(lines, heading)
		if !ok {
			return "", toolErr(noHeading(topic, text, heading) + " Leave out heading for the whole doc.")
		}
		return "topic: " + topic + "\n\n" + trimNewlines(strings.Join(lines[start:end], "")) + "\n", nil
	}
	if heading != "" {
		return "", toolErr("heading needs a topic.")
	}
	if query != "" {
		limit, err := mcpInt(args, "limit", 5, 20)
		if err != nil {
			return "", err
		}
		hits := manualFind(query, limit)
		if len(hits) == 0 {
			return "Nothing in Zone's documentation matches. Try other words, or leave out query for the list of topics.", nil
		}
		var out []string
		for i, d := range hits {
			if d.heading != "" {
				out = append(out, fmt.Sprintf("[%d] %s › %s\ntopic: %s\nheading: %s\n\n%s", i+1, d.title, d.heading, d.topic, d.heading, d.text))
			} else {
				out = append(out, fmt.Sprintf("[%d] %s\ntopic: %s\n\n%s", i+1, d.title, d.topic, d.text))
			}
		}
		return strings.Join(out, "\n\n---\n\n") + "\n\nThe whole section: zone_help with its topic and heading.", nil
	}
	out := []string{"Zone's documentation: the README (what Zone is, its features, setup) and the spec, " +
		"one doc per area. Read one with topic, a section with topic and heading."}
	for _, d := range docs {
		text, _ := readText(d.path)
		found := manualHeadings(text)
		title := d.name
		if len(found) > 0 {
			title = found[0].text
		}
		out = append(out, "\n"+d.name+": "+title)
		for _, h := range found[min(1, len(found)):] {
			if h.level <= 3 {
				out = append(out, "  "+strings.Repeat("  ", max(0, h.level-2))+h.text)
			}
		}
	}
	return strings.Join(out, "\n"), nil
}

func searchWiki(args jsonObj, base, user string) (string, error) {
	query, ok := jsonobj.As[string](args["query"])
	if !ok || strings.TrimSpace(query) == "" {
		return "", toolErr("query must be a non-empty string")
	}
	under, err := underArg(args)
	if err != nil {
		return "", err
	}
	if under != "" && !anyPageIn(under, user) {
		return "", toolErr("No pages under " + strconv.Quote(under) + ". list_pages shows the slugs, " +
			"whose folders are the part before the last '/'.")
	}
	limit, err := mcpInt(args, "limit", 8, 20)
	if err != nil {
		return "", err
	}
	quicknote, err := boolArg(args, "notes")
	if err != nil {
		return "", err
	}
	hits := retrieve(&Req{user: user}, query, limit, under, quicknote)
	logQuery(query, under, hits)
	if len(hits) == 0 {
		return "No matching sections or tasks. Try other words, or list_pages to browse.", nil
	}
	var out []string
	for i, d := range hits {
		if d.isTask() {
			out = append(out, fmt.Sprintf("[%d] ", i+1)+mcpTaskText(d.Task, base, user))
			continue
		}
		body, ok := d.body()
		if !ok {
			continue // the page is gone
		}
		where := d.Title
		if path := d.path(); path != "" {
			where += " › " + path
		}
		out = append(out, fmt.Sprintf("[%d] %s\nslug: %s\nedited: %s\nurl: %s%s%s\nfile: %s\n\n%s", i+1, where, d.Slug,
			fmtDate(d.Mtime), base, pathURL("page", d.Slug), d.anchor(), hostFile(d.Slug), body))
	}
	return strings.Join(out, "\n\n---\n\n"), nil
}

func anyPageIn(folder, user string) bool {
	for _, p := range visiblePages(user) {
		if inFolder(p.Slug, folder) {
			return true
		}
	}
	return false
}

// ── Dispatch ────────────────────────────────────────────────────────────────

// toolWrites: what a tool changes. A tool whose definition isn't
// readOnlyHint says which (TestMCPToolDefinitions), so a new writing tool
// can't be forgotten here.
type toolWrites int

const (
	toolWritesNothing toolWrites = iota
	toolWritesTasks              // tasks only: no pages, so not under pagesMu
	toolWritesPages              // pages (and maybe tasks): run under pagesMu, as the routes that do are (writesPages)
)

// mcpTool: a tool's function and what it writes. A tool that writes is none
// for a viewer, who isn't offered it either (toolList).
type mcpTool struct {
	run    func(args jsonObj, base, user string) (string, error)
	writes toolWrites
}

// mcpToolTable: every tool mcp_tools.json defines, by name.
var mcpToolTable = map[string]mcpTool{
	"search_wiki":      {run: searchWiki},
	"search_tasks":     {run: searchTasks},
	"grep_wiki":        {run: grepWiki},
	"read_page":        {run: readPage},
	"list_pages":       {run: listPages},
	"check_links":      {run: checkLinks},
	"history":          {run: history},
	"list_tags":        {run: listTags},
	"zone_help":        {run: zoneHelp},
	"read_guides":      {run: readGuides},
	"write_page":       {writePage, toolWritesPages},
	"edit_page":        {editPageTool, toolWritesPages},
	"replace_in_pages": {replaceInPages, toolWritesPages},
	"append_to_page":   {appendToPage, toolWritesPages},
	"file_note":        {fileNote, toolWritesPages},
	"rename_page":      {renamePageTool, toolWritesPages},
	"delete_page":      {deletePageTool, toolWritesPages},
	"restore_page":     {restorePage, toolWritesPages},
	"rename_task_tag":  {renameTaskTag, toolWritesPages}, // the tag in tasks, and in the pages' embeds
	"create_task":      {createTaskTool, toolWritesTasks},
	"update_task":      {updateTaskTool, toolWritesTasks},
}

// writeTool: the tool changes the wiki (pages, notes, tasks).
func writeTool(name string) bool { return mcpToolTable[name].writes != toolWritesNothing }

// callMCPTool runs a tool for the request r, as its token's user: links in
// its answer use base-url (the settings file), else the address the client reached Zone at.
func callMCPTool(r *http.Request, name string, args jsonObj) (string, error) {
	base := settings().BaseURL
	if base == "" {
		scheme := "http"
		if r.TLS != nil {
			scheme = "https"
		}
		base = scheme + "://" + r.Host
	}
	tool, ok := mcpToolTable[name]
	if !ok {
		return "", toolErr("Unknown tool: " + name)
	}
	if tool.writes == toolWritesPages {
		pagesMu.Lock()
		defer pagesMu.Unlock()
	}
	if tool.writes != toolWritesNothing && settings().isViewer(userOf(r)) {
		return "", toolErr(readOnlyMsg)
	}
	out, err := tool.run(args, base, userOf(r))
	// What an operation refused (a bad date, a page that isn't there, a
	// viewer's change to something shared), from wherever in the tool, is
	// the tool's error, which the model can correct.
	if rf, ok := refusalOf(err); ok {
		err = toolErr(rf.msg)
	} else if errors.Is(err, errTasksCorrupt) {
		err = toolErr("The tasks' storage can't be read right now; try again")
	}
	return out, err
}

func joinIDs(hits []idScore) string {
	ids := make([]string, len(hits))
	for i, h := range hits {
		ids[i] = h.ID
	}
	return strings.Join(ids, ", ")
}
