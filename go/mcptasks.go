// The MCP tools for tasks: search_tasks, create_task, update_task (tasks as
// the model reads them, its arguments as the fields of a task write).
package main

import (
	"encoding/json"
	"fmt"
	"regexp"
	"slices"
	"sort"
	"strconv"
	"strings"
	"zonewiki/internal/jsonobj"
	"zonewiki/internal/mdtext"
	"zonewiki/internal/taskmodel"
	"zonewiki/internal/textsearch"
)

var recurrenceUnits = map[string]string{"daily": "day", "weekly": "week", "monthly": "month", "yearly": "year"}

func shortID(id string) string { return runePrefix(id, shortIDLen) }

// mcpTaskText: one task for user's LLM: title, a line of facts
// (status, private, tags, dates, repeat, list), its link in the tasks, its
// notes and comments.
func mcpTaskText(t *Task, base string, user asker) string {
	status := taskStatus(t)
	fact := "status: " + status
	if status != "open" {
		fact += " " + runePrefix(t.ClosedAt, 10)
	}
	facts := []string{fact}
	if t.Owner != "" {
		facts = append(facts, "private")
	}
	if len(t.Tags) > 0 {
		var tags []string
		for _, tag := range t.Tags {
			tags = append(tags, "#"+tag)
		}
		facts = append(facts, "tags: "+strings.Join(tags, " "))
	}
	if s := settings(); t.Assignee != "" && s.Users != nil {
		who := "assigned to: " + t.Assignee
		if t.Assignee == user.Name {
			who += " (the user)"
		} else if !s.assignable(t.Assignee) {
			who += " (who can no longer be given tasks: it counts as no one's)"
		} else if assigneeGone(t) {
			who += " (who doesn't see its list anymore: it counts as no one's)"
		}
		facts = append(facts, who)
	}
	// What it has for the user that they haven't seen. Reading it here
	// doesn't see it for them: their own look does.
	if news := newsFor(t, user); news.Kind != "" {
		what := map[string]string{newsGiven: "gave it to the user", newsComment: "commented", newsMention: "mentioned the user",
			newsClosed: "closed it", newsReopened: "reopened it", newsHandedOn: "gave it to someone else"}[news.Kind]
		facts = append(facts, "update: "+news.By+" "+what+" ("+strings.Replace(news.At, "T", " ", 1)+")")
	}
	if settings().Users != nil && slices.Contains(t.Followers, user.Name) {
		facts = append(facts, "the user follows it")
	}
	if maker := t.Maker(); maker != "" && maker != t.Assignee && settings().Users != nil {
		if maker == user.Name {
			maker += " (the user)"
		}
		facts = append(facts, "from: "+maker)
	}
	if t.Importance != "" {
		facts = append(facts, "importance: "+t.Importance)
	}
	if t.StartDate != "" {
		facts = append(facts, "start: "+t.StartDate)
	}
	if t.DueDate != "" {
		facts = append(facts, "due: "+t.DueDate)
	}
	if rec := t.Recurrence; rec != nil {
		if unit, ok := recurrenceUnits[rec.Type]; ok {
			every := rec.Type
			if rec.Interval != 1 {
				every = "every " + strconv.Itoa(rec.Interval) + " " + unit + "s"
			}
			if rec.From == "completion" {
				every += " after completion"
			}
			facts = append(facts, "repeats "+every)
		}
	}
	if t.Keep {
		keep := "keep task (back to the list when done"
		if t.LastDone != "" {
			keep += ", last done " + t.LastDone
		}
		facts = append(facts, keep+")")
	}
	list := taskList(t, taskLists(user))
	if list != "" {
		facts = append(facts, "list: "+list)
	}
	text := "Task: " + t.Title + "\n" + strings.Join(facts, " · ") +
		"\nid: " + shortID(t.ID) + "\nurl: " + base + taskLink(t, list)
	if notes := strings.TrimSpace(t.Description); notes != "" {
		text += "\n\n" + notes
	}
	// Comments: the user's comments (markdown), oldest first, each with its time
	// (and with users, who wrote it).
	var comments []string
	for _, c := range t.Comments {
		if c.Text != "" {
			at := strings.ReplaceAll(runePrefix(c.At, 16), "T", " ")
			if c.By != "" {
				at += " (" + c.By + ")"
			}
			// A comment of several lines: the next ones under its first.
			comments = append(comments, "- "+at+": "+strings.ReplaceAll(c.Text, "\n", "\n  "))
		}
	}
	if len(comments) > 0 {
		text += "\n\nComments:\n" + strings.Join(comments, "\n")
	}
	return text
}

// mcpTaskRefs: for read_page, what each [[task:…]] on the page stands for,
// since the raw markdown holds only the id.
func mcpTaskRefs(content, base string, user asker) string {
	masked, _ := mdtext.MaskCode(content)
	var refs []string
	seen := map[string]bool{}
	for _, m := range taskRefRE.FindAllStringSubmatch(masked, -1) {
		if ref := strings.ToLower(m[1]); !seen[ref] {
			seen[ref] = true
			refs = append(refs, ref)
		}
	}
	if len(refs) == 0 {
		return ""
	}
	tasks := resolveTaskRefs(user, refs)
	lists := taskLists(user)
	var lines []string
	for _, ref := range refs {
		t := tasks[ref]
		if t == nil {
			lines = append(lines, "- [[task:"+ref+"]]: no such task (deleted)")
			continue
		}
		facts := []string{taskStatus(t)}
		if t.StartDate != "" {
			facts = append(facts, "start "+t.StartDate)
		}
		if t.DueDate != "" {
			facts = append(facts, "due "+t.DueDate)
		}
		lines = append(lines, "- [[task:"+ref+"]]: "+t.Title+" ("+strings.Join(facts, ", ")+") "+
			base+taskLink(t, taskList(t, lists)))
	}
	return "\n\n---\nTasks referenced on this page ([[task:…]]):\n" + strings.Join(lines, "\n")
}

func searchTasks(args jsonObj, base string, user asker) (string, error) {
	query, err1 := strArg(args, "query", false)
	tag, err2 := strArg(args, "tag", false)
	assignee, err3 := strArg(args, "assignee", false)
	list, err4 := strArg(args, "list", false)
	if err1 != nil || err2 != nil || err3 != nil || err4 != nil {
		return "", toolErr("query, tag, list and assignee must be strings")
	}
	lists := taskLists(user)
	scope := ""
	if list = cleanList(strings.TrimLeft(strings.TrimSpace(list), "#")); list != "" {
		if l, ok := resolveList(list, lists); ok {
			scope = l.Ref
		} else if isInbox(list, lists) {
			scope = inboxRef // list "inbox" (or "none"): the Inbox, the tasks in no list
		} else {
			return "", toolErr("No list #" + list + " (list_tags shows the lists)")
		}
	}
	if assignee != "" && settings().Users == nil {
		return "", toolErr("This wiki has no users, so its tasks have no assignees")
	}
	status := "open"
	if args.Has("status") {
		status, _ = jsonobj.As[string](args["status"])
	}
	// updated: the tasks with news for the user (newsFor), open or closed.
	updated := status == "updated"
	if updated {
		status = "all"
	}
	if status != "open" && status != "closed" && status != "all" {
		return "", toolErr("status must be open, closed, all or updated")
	}
	limit, err := mcpInt(args, "limit", 50, 200)
	if err != nil {
		return "", err
	}
	order := "deadline"
	if args.Has("order") {
		order, _ = jsonobj.As[string](args["order"])
	}
	if order != "deadline" && order != "manual" {
		return "", toolErr("order must be deadline or manual")
	}
	want := map[string]bool{}
	for _, t := range strings.Split(tag, ",") {
		if t = strings.ToLower(strings.TrimLeft(strings.TrimSpace(t), "#")); t != "" {
			want[t] = true
		}
	}
	q := textsearch.ParseQuery(query)
	// updated: the tasks the user's Tasks links count (updatesFor): of the
	// open ones and those closed this month or last. A doc keeps a task's
	// record, not its history, so they are asked for by id.
	news := map[string]bool{}
	if updated {
		for _, t := range updatesFor(user, taskFiles.Open(user), recentUpdates(user)) {
			news[t.ID] = true
		}
	}
	var docs []*SearchDoc
	for _, d := range taskSearchDocs() {
		if !canSeeTask(user, d.Task) || status != "all" && (status == "open") != d.Task.IsOpen() || updated && !news[d.taskID()] {
			continue
		}
		have := taskTagsLower(d.Task)
		all := true
		for w := range want {
			all = all && have[w]
		}
		if all && inScope(d.Task, scope, lists) && assigneeParamMatches(d.Task, assignee, user) && (!q.HasFilters() || q.Accepts(d.text(), d.Tags, d.date()) && !q.HoldsExcluded(d.tokens())) {
			docs = append(docs, d)
		}
	}
	if strings.TrimSpace(q.Text) != "" {
		// Ranked by the wiki's search index, among these tasks: it has
		// every task's words in its file, so none is read to rank them.
		x := searchIndex(&Req{user: user})
		among := make(map[*SearchDoc]bool, len(docs))
		for _, d := range docs {
			among[d] = true
		}
		ranked := x.searchWhere(q.Text, max(len(docs), 1), func(d *SearchDoc) bool { return among[d] })
		if semantic != nil {
			semantic.poke()
			byID := map[string]*SearchDoc{}
			for _, d := range docs {
				byID[d.taskID()] = d
			}
			ranked = hybrid(ranked, bySimilarity(taskHits(q.Text, byID, max(len(byID), 1))), q, x)
		}
		docs = ranked
	} else {
		var open, done []*SearchDoc
		for _, d := range docs {
			if d.Task.IsOpen() {
				open = append(open, d)
			} else {
				done = append(done, d)
			}
		}
		or9 := func(s string) string { return firstNonEmpty(s, "9999") }
		if order == "manual" {
			// What the user ranked in My tasks is their own order, across
			// lists (mineOrder): those first, as they stand there; then by
			// the lists' order.
			own := map[string]int{}
			if reg := mineRegistry(user); reg.OrderSet {
				for i, id := range reg.Order {
					own[id] = i + 1
				}
			}
			sort.SliceStable(open, func(i, j int) bool {
				a, b := open[i].Task, open[j].Task
				if x, y := own[a.ID], own[b.ID]; x != 0 || y != 0 {
					return x != 0 && (y == 0 || x < y)
				}
				return manualBefore(a, b)
			})
		} else {
			sort.SliceStable(open, func(i, j int) bool {
				a, b := open[i].Task, open[j].Task
				if x, y := or9(a.DueDate), or9(b.DueDate); x != y {
					return x < y
				}
				if x, y := or9(a.StartDate), or9(b.StartDate); x != y {
					return x < y
				}
				return strings.ToLower(a.Title) < strings.ToLower(b.Title)
			})
		}
		sort.SliceStable(done, func(i, j int) bool {
			return done[i].Task.ClosedAt > done[j].Task.ClosedAt
		})
		docs = append(open, done...)
	}
	day := isoDay(today())
	if len(docs) == 0 {
		return "Today is " + day + ". No matching tasks.", nil
	}
	count := fmt.Sprintf("%d matching tasks", len(docs))
	if len(docs) > limit {
		count += fmt.Sprintf(", the first %d shown", limit)
	}
	var body []string
	for _, d := range docs[:min(limit, len(docs))] {
		body = append(body, mcpTaskText(d.wholeTask(), base, user))
	}
	return "Today is " + day + ". " + count + ":\n\n" + strings.Join(body, "\n\n---\n\n"), nil
}

// manualBefore: a comes before b in search_tasks' manual order, where the
// user ranked neither in My tasks: the tasks ranked in their lists
// (sort_order) top to bottom, then
// those not yet ranked, newest first. (The Open view shows the unranked
// above, as new ones wait there to be ranked; for "what first" they come
// after.)
func manualBefore(a, b *Task) bool {
	switch {
	case a.SortOrder != nil && b.SortOrder != nil:
		return *a.SortOrder < *b.SortOrder
	case a.SortOrder != nil || b.SortOrder != nil:
		return a.SortOrder != nil
	}
	return a.CreatedAt > b.CreatedAt
}

var (
	taskLinkRE = regexp.MustCompile(`task[:=]([0-9a-f-]{4,36})`)
	taskIDRE   = regexp.MustCompile(`^[0-9a-f][0-9a-f-]{3,35}$`)
)

// findTask: the task a reference names: its id or a prefix of it (the
// 8-character short id the tools show), a [[task:…]] reference or a
// /tasks?task=… link; of the tasks user sees.
func findTask(raw string, user asker) (*Task, error) {
	raw = strings.ToLower(strings.TrimSpace(raw))
	ref := raw
	if m := taskLinkRE.FindStringSubmatch(raw); m != nil {
		ref = m[1]
	}
	if !taskIDRE.MatchString(ref) {
		return nil, toolErr(strconv.Quote(raw) + " is not a task id; search_tasks shows each task's id")
	}
	found := findTasks(user, ref)
	if len(found) == 0 {
		return nil, toolErr("No task with id " + strconv.Quote(ref) + ". search_tasks shows each task's id.")
	}
	if len(found) > 1 {
		return nil, toolErr("Several tasks have ids starting with " + strconv.Quote(ref) + "; give more of the id")
	}
	return found[0], nil
}

// dateArg: a date argument, YYYY-MM-DD or a word quick-add knows
// ("tomorrow", "fri", "+3d"), resolved with Zone's today; "" for none.
func dateArg(args jsonObj, name string) (string, error) {
	s, err := strArg(args, name, false)
	if err != nil || strings.TrimSpace(s) == "" {
		return "", err
	}
	day := parseDateWord(strings.TrimSpace(s), today())
	if day == "" {
		return "", toolErr(name + " must be a date: YYYY-MM-DD, today, tomorrow, a weekday or +3d/+2w")
	}
	return day, nil
}

// tagListArg: a list-of-tags argument (a comma-separated string works too),
// each without its leading #.
func tagListArg(args jsonObj, name string) ([]string, error) {
	l, err := listArg(args, name, name+" must be a list of tags", splitComma)
	var out []string
	for _, s := range l {
		if s = strings.TrimLeft(strings.TrimSpace(s), "#"); s != "" {
			out = append(out, s)
		}
	}
	return out, err
}

func splitComma(s string) []string { return strings.Split(s, ",") }

// taskFields: the tasks API's fields from the tools' arguments (only those
// given); task, when updating one, for add_tags/remove_tags and repeat.
func taskFields(args jsonObj, task *Task) (jsonObj, error) {
	data := jsonObj{}
	// An assistant whose tool list is from before the field was renamed:
	// told, since an argument a tool doesn't know is dropped without a word.
	if args.Has("notes") {
		return nil, toolErr("A task's text is its description: give it as description, not notes")
	}
	for _, f := range []string{"title", "description"} {
		if args.Has(f) {
			s, err := strArg(args, f, false)
			if err != nil {
				return nil, err
			}
			data.Set(f, strings.TrimSpace(s))
		}
	}
	for _, f := range [][2]string{{"due", "due_date"}, {"start", "start_date"}} {
		if args.Has(f[0]) {
			day, err := dateArg(args, f[0])
			if err != nil {
				return nil, err
			}
			data.Set(f[1], jsonobj.Nullable(day))
		}
	}
	if title, _ := jsonobj.As[string](data["title"]); data.Has("title") && title == "" {
		return nil, toolErr("title must be a non-empty string")
	}
	// The API reads the name: a user's, "me", or none ("", "none").
	if args.Has("assignee") {
		s, err := strArg(args, "assignee", false)
		if err != nil {
			return nil, err
		}
		data.Set("assignee", s)
	}
	// An assistant whose tool list is from before lists were places.
	if args.Has("private") {
		return nil, toolErr("A task is private by its list: give list (the name of one of the user's private lists; list_tags shows them), not private")
	}
	if args.Has("list") {
		s, err := strArg(args, "list", false)
		if err != nil {
			return nil, err
		}
		data.Set("list", strings.TrimLeft(strings.TrimSpace(s), "#"))
	}
	for _, f := range []string{"keep", "share", "follow"} {
		if args.Has(f) {
			b, ok := jsonobj.As[bool](args[f])
			if !ok {
				return nil, toolErr(f + " must be true or false")
			}
			data.Set(f, b)
		}
	}
	if args.Has("importance") {
		s, ok := jsonobj.As[string](args["importance"])
		if s = strings.ToLower(strings.TrimSpace(s)); !ok || s != "high" && s != "normal" && s != "low" {
			return nil, toolErr("importance must be high, normal or low")
		}
		data.Set("importance", s)
	}
	if args.Has("tags") {
		data["tags"] = args["tags"]
	}
	if task != nil && (args.Has("add_tags") || args.Has("remove_tags")) {
		tags := append([]string{}, task.Tags...)
		if data.Has("tags") {
			tags = jsonobj.TextList(data["tags"])
		}
		add, err := tagListArg(args, "add_tags")
		if err != nil {
			return nil, err
		}
		remove, err := tagListArg(args, "remove_tags")
		if err != nil {
			return nil, err
		}
		data.Set("tags", retag(tags, add, remove))
	}
	if args.Has("repeat") || args.Has("repeat_every") || args.Has("repeat_from") {
		repeat, interval, from := "none", json.RawMessage("1"), "due"
		if task != nil && task.Recurrence != nil {
			repeat, from = task.Recurrence.Type, task.Recurrence.From
			interval = json.RawMessage(strconv.Itoa(task.Recurrence.Interval))
		}
		if args.Has("repeat") {
			repeat, _ = jsonobj.As[string](args["repeat"])
		}
		switch repeat {
		case "daily", "weekly", "monthly", "yearly":
			data.Set("rec_type", repeat)
		case "none":
			data.Set("rec_type", nil)
		default:
			return nil, toolErr("repeat must be daily, weekly, monthly, yearly or none")
		}
		if args.Has("repeat_every") {
			interval = args["repeat_every"]
		}
		data["rec_interval"] = interval
		if args.Has("repeat_from") {
			from, _ = jsonobj.As[string](args["repeat_from"])
		}
		if from != "due" && from != "completion" {
			return nil, toolErr("repeat_from must be due or completion")
		}
		data.Set("rec_from", from)
	}
	return data, nil
}

// The tasks closest to a new one whose tags are suggested, and how many.
const (
	similarTasks  = 10
	tagsSuggested = 5
)

// tagHints: for a task just created, the tags on the tasks most like it
// (by its title and notes; open and done ones), most common first, which
// it lacks, so the model can offer them; and its tags no other task or page
// uses, which may be near-duplicates of existing ones.
func tagHints(task *Task, user asker) string {
	// The other tasks the user sees, by the wiki's search index (no task is
	// read to find the ones like this one).
	other := func(d *SearchDoc) bool { return d.isTask() && d.taskID() != task.ID && canSeeTask(user, d.Task) }
	have := taskTagsLower(task)
	text := textsearch.ParseQuery(task.Title + " " + task.Description).Text
	counts := map[string]int{}
	var order []string
	if strings.TrimSpace(text) != "" {
		for _, d := range searchIndex(&Req{user: user}).searchWhere(text, similarTasks, other) {
			for _, tag := range d.Task.Tags {
				tag = strings.ToLower(tag)
				if have[tag] {
					continue
				}
				if counts[tag] == 0 {
					order = append(order, tag)
				}
				counts[tag]++
				have[tag] = true // once per task
			}
			have = taskTagsLower(task)
		}
	}
	out := ""
	if len(order) > 0 {
		// Most common first; ties: the order the closest tasks named them.
		sort.SliceStable(order, func(i, j int) bool { return counts[order[i]] > counts[order[j]] })
		var hints []string
		for _, t := range order[:min(len(order), tagsSuggested)] {
			hints = append(hints, fmt.Sprintf("#%s (%d)", t, counts[t]))
		}
		out += "\n\nTags on similar tasks (offer the fitting ones; update_task's add_tags adds them): " +
			strings.Join(hints, ", ")
	}
	used := map[string]bool{}
	for _, d := range taskSearchDocs() {
		if !other(d) {
			continue
		}
		for t := range taskTagsLower(d.Task) {
			used[t] = true
		}
	}
	for _, p := range visiblePages(user) {
		if content, err := readPageText(p.Path); err == nil {
			for _, t := range mdtext.PageTags(content) {
				used[t] = true
			}
		}
	}
	var fresh []string
	for t := range have {
		if !used[t] {
			fresh = append(fresh, "#"+t)
		}
	}
	sort.Strings(fresh)
	if len(fresh) > 0 && len(used) > 0 {
		out += "\n\nNew tags, used nowhere else yet: " + strings.Join(fresh, ", ") +
			". If one means an existing tag (list_tags), use that instead."
	}
	return out
}

func createTaskTool(args jsonObj, base string, user asker) (string, error) {
	if _, err := strArg(args, "title", true); err != nil {
		return "", err
	}
	data, err := taskFields(args, nil)
	if err != nil {
		return "", err
	}
	// As c t in the browser: without a list, into the user's Inbox, which
	// with users is theirs alone. A task for someone needs a shared list:
	// say which there are rather than refuse with the write's own words.
	if data.Has("assignee") && !data.Null("assignee") && inboxOwner(user) != "" {
		s, _ := asStr(data["assignee"], "assignee")
		ref, _ := asStr(data["list"], "list")
		lists := taskLists(user)
		if name := taskmodel.CleanAssignee(s); name != "" && name != "none" && (!data.Has("list") || data.Null("list") || cleanList(ref) == "" || isInbox(ref, lists)) {
			var shared []string
			for _, r := range sortedKeys(lists) {
				if !lists[r].Private() {
					shared = append(shared, r)
				}
			}
			if len(shared) == 0 {
				return "", toolErr("A task assigned to someone has to be in a shared list, and this wiki has none: make one with create_task_list, then pass it as list")
			}
			return "", toolErr("A task assigned to someone has to be in a shared list (without one it is in the user's Inbox, which only they see). Pass list: " + strings.Join(shared, ", "))
		}
	}
	msg, err := commitMsg("Create task", args)
	if err != nil {
		return "", err
	}
	task, err := createTask(user, data, msg)
	if err != nil {
		return "", err
	}
	return "Created (today is " + isoDay(today()) + ").\n\n" + mcpTaskText(task, base, user) + tagHints(task, user), nil
}

// taskStatusFields: a status as the tasks API's field and its value.
var taskStatusFields = map[string]struct {
	field string
	value bool
}{"done": {"completed", true}, "open": {"completed", false}, "wont_do": {"wont_do", true}, "skip": {"skip", true}}

// otherArgs: the arguments the client sent besides those named, A–Z (not
// the connection's name callMCPTool adds, viaArg: no client sent it).
func otherArgs(args jsonObj, allowed ...string) []string {
	var other []string
	for k := range args {
		if k != viaArg && !slices.Contains(allowed, k) {
			other = append(other, k)
		}
	}
	sort.Strings(other)
	return other
}

// updateTaskTool: one task's fields, status and comment; ids instead of id
// tags several tasks (tagTasksTool). A task is never deleted: wont_do
// closes one undone.
func updateTaskTool(args jsonObj, base string, user asker) (string, error) {
	if args.Has("ids") {
		if args.Has("id") {
			return "", toolErr("Give id or ids, not both")
		}
		if other := otherArgs(args, "ids", "add_tags", "remove_tags", "summary"); len(other) > 0 {
			return "", toolErr("With ids only add_tags and remove_tags work, not " + strings.Join(other, ", ") +
				"; change the rest one task (id) at a time")
		}
		return tagTasksTool(args, user)
	}
	raw, err := strArg(args, "id", true)
	if err != nil {
		return "", err
	}
	task, err := findTask(raw, user)
	if err != nil {
		return "", err
	}
	status, _ := jsonobj.As[string](args["status"])
	var comment *string
	if args.Has("comment") {
		c, err := strArg(args, "comment", true)
		if err != nil {
			return "", err
		}
		comment = &c
	}
	data, err := taskFields(args, task)
	if err != nil {
		return "", err
	}
	if args.Has("status") {
		f, ok := taskStatusFields[status]
		if !ok {
			return "", toolErr("status must be open, done, wont_do or skip (a task can't be deleted: wont_do closes it undone)")
		}
		data.Set(f.field, f.value)
	}
	if len(data) == 0 && comment == nil {
		return "", toolErr("Nothing to change: give the fields to set, a status or a comment")
	}
	suffix, err := msgSuffix(args)
	if err != nil {
		return "", err
	}
	if len(data) > 0 {
		if task, err = updateTask(user, task.ID, data, suffix); err != nil {
			return "", err
		}
	}
	if comment != nil {
		if task, err = addComment(user, task.ID, *comment, "Comment on task"+suffix); err != nil {
			return "", err
		}
	}
	head := "Comment added."
	if len(data) > 0 && comment != nil {
		head = "Updated, comment added."
	} else if len(data) > 0 {
		head = "Updated."
	}
	out := head + "\n\n" + mcpTaskText(task, base, user)
	if status == "done" || status == "skip" {
		for _, t := range taskFiles.Open(user) {
			if t.SpawnedFrom == task.ID && t.IsOpen() {
				out += "\n\nNext occurrence:\n\n" + mcpTaskText(t, base, user)
				break
			}
		}
	}
	return out, nil
}

// More ids than this in one call is likely a mistake (a whole tasks retagged).
const tagTasksMax = 200

func tagTasksTool(args jsonObj, user asker) (string, error) {
	ids, ok := jsonobj.As[[]json.RawMessage](args["ids"])
	if !ok || len(ids) == 0 {
		return "", toolErr("ids must be a non-empty list of task ids")
	}
	for _, id := range ids {
		if _, ok := jsonobj.As[string](id); !ok {
			return "", toolErr("ids must be a non-empty list of task ids")
		}
	}
	if len(ids) > tagTasksMax {
		return "", toolErr(fmt.Sprintf("At most %d tasks per call", tagTasksMax))
	}
	add, err := tagListArg(args, "add_tags")
	if err != nil {
		return "", err
	}
	remove, err := tagListArg(args, "remove_tags")
	if err != nil {
		return "", err
	}
	if len(add) == 0 && len(remove) == 0 {
		return "", toolErr("Give add_tags or remove_tags")
	}
	var found []string
	before := map[string][]string{}
	for _, id := range ids {
		raw, _ := jsonobj.As[string](id)
		t, err := findTask(raw, user) // an unknown id fails the whole call
		if err != nil {
			return "", err
		}
		if _, ok := before[t.ID]; !ok {
			found = append(found, t.ID)
			before[t.ID] = append([]string{}, t.Tags...)
		}
	}
	msg, err := commitMsg("Tag tasks", args)
	if err != nil {
		return "", err
	}
	tasks, err := tagTasks(user, found, add, remove, msg)
	if err != nil {
		return "", err
	}
	changed := 0
	var lines []string
	for _, t := range tasks {
		tags := t.Tags
		if !slices.Equal(tags, before[t.ID]) {
			changed++
		}
		shown := "no tags"
		if len(tags) > 0 {
			shown = "#" + strings.Join(tags, " #")
		}
		lines = append(lines, "- "+t.Title+" ("+shortID(t.ID)+"): "+shown)
	}
	s := ""
	if len(tasks) != 1 {
		s = "s"
	}
	head := fmt.Sprintf("Tags changed on %d of %d task%s", changed, len(tasks), s)
	if changed < len(tasks) {
		head += " (the others had them so already)"
	}
	return head + ".\n\n" + strings.Join(lines, "\n"), nil
}

// renameTaskListTool: a list renamed, with its tasks, the pins, and the
// embeds and links on pages that name it (renameListOp).
func renameTaskListTool(args jsonObj, base string, user asker) (string, error) {
	ref, err := strArg(args, "list", true)
	if err != nil {
		return "", err
	}
	name, err := strArg(args, "new_name", true)
	if err != nil {
		return "", err
	}
	suffix, err := msgSuffix(args)
	if err != nil {
		return "", err
	}
	done, err := renameListOp(user, ref, name, suffix, func(pages []string) error { return checkGuides(args, pages, user) })
	if err != nil {
		return "", err
	}
	out := fmt.Sprintf("Renamed the list #%s to #%s, in one commit: %d %s", cleanList(ref), done.Ref, done.Tasks, plural(done.Tasks, "task"))
	if len(done.Pages) > 0 {
		out += fmt.Sprintf(", %d %s (%s)", len(done.Pages), plural(len(done.Pages), "page"), strings.Join(done.Pages, ", "))
	}
	return out + ".\n" + base + listHref(done.Ref), nil
}

// createTaskListTool: a new task list, shared or (private) the user's own.
func createTaskListTool(args jsonObj, base string, user asker) (string, error) {
	name, err := strArg(args, "name", true)
	if err != nil {
		return "", err
	}
	private, err := boolArg(args, "private")
	if err != nil {
		return "", err
	}
	who, err := strArg(args, "for", false)
	if err != nil {
		return "", err
	}
	suffix, err := msgSuffix(args)
	if err != nil {
		return "", err
	}
	ref, err := createListOp(user, name, private, true, who, suffix)
	if err != nil {
		return "", err
	}
	kind := "shared: everyone who uses the wiki sees it and its tasks"
	if l, ok := resolveList(ref, taskLists(user)); ok && len(l.For) > 0 {
		kind = "for " + forWords(l.For) + ": only they see it and its tasks, and its tasks are given only to them"
	}
	if private {
		kind = "private: only the user sees it and its tasks"
	}
	return "Created the list #" + ref + " (" + kind + "). create_task's and update_task's list puts a task in it; " +
		"[[tasks: list=" + ref + "]] shows it on a page.\n" + base + listHref(ref), nil
}
