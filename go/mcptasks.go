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
func mcpTaskText(t *Task, base, user string) string {
	status := taskStatus(t)
	fact := "status: " + status
	if status != "open" {
		fact += " " + runePrefix(t.CompletedAt, 10)
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
	// Comments: the user's status lines, oldest first, each with its time
	// (and with users, who wrote it).
	var comments []string
	for _, c := range t.Comments {
		if c.Text != "" {
			at := strings.ReplaceAll(runePrefix(c.At, 16), "T", " ")
			if c.By != "" {
				at += " (" + c.By + ")"
			}
			comments = append(comments, "- "+at+": "+c.Text)
		}
	}
	if len(comments) > 0 {
		text += "\n\nComments:\n" + strings.Join(comments, "\n")
	}
	return text
}

// mcpTaskRefs: for read_page, what each [[task:…]] on the page stands for,
// since the raw markdown holds only the id.
func mcpTaskRefs(content, base, user string) string {
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

func searchTasks(args jsonObj, base, user string) (string, error) {
	query, err1 := strArg(args, "query", false)
	tag, err2 := strArg(args, "tag", false)
	if err1 != nil || err2 != nil {
		return "", toolErr("query and tag must be strings")
	}
	status := "open"
	if args.Has("status") {
		status, _ = jsonobj.As[string](args["status"])
	}
	if status != "open" && status != "done" && status != "all" {
		return "", toolErr("status must be open, done or all")
	}
	limit, err := mcpInt(args, "limit", 50, 200)
	if err != nil {
		return "", err
	}
	want := map[string]bool{}
	for _, t := range strings.Split(tag, ",") {
		if t = strings.ToLower(strings.TrimLeft(strings.TrimSpace(t), "#")); t != "" {
			want[t] = true
		}
	}
	q := textsearch.ParseQuery(query)
	var docs []*SearchDoc
	for _, d := range taskSearchDocs() {
		if !canSeeTask(user, d.Task) || status != "all" && (status == "open") != d.Task.IsOpen() {
			continue
		}
		have := taskTagsLower(d.Task)
		all := true
		for w := range want {
			all = all && have[w]
		}
		if all && (!q.HasFilters() || q.Accepts(d.text(), d.Tags, d.date()) && !q.HoldsExcluded(d.tokens())) {
			docs = append(docs, d)
		}
	}
	if strings.TrimSpace(q.Text) != "" {
		ranked := newDocIndex(docs).search(q.Text, max(len(docs), 1))
		if semantic != nil {
			semantic.poke()
			byID := map[string]*SearchDoc{}
			for _, d := range docs {
				byID[d.taskID()] = d
			}
			ranked = textsearch.Fuse([][]*SearchDoc{ranked, bySimilarity(taskHits(q.Text, byID, max(len(byID), 1)))})
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
		sort.SliceStable(done, func(i, j int) bool {
			return done[i].Task.CompletedAt > done[j].Task.CompletedAt
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
		body = append(body, mcpTaskText(d.Task, base, user))
	}
	return "Today is " + day + ". " + count + ":\n\n" + strings.Join(body, "\n\n---\n\n"), nil
}

var (
	taskLinkRE = regexp.MustCompile(`task[:=]([0-9a-f-]{4,36})`)
	taskIDRE   = regexp.MustCompile(`^[0-9a-f][0-9a-f-]{3,35}$`)
)

// findTask: the task a reference names: its id or a prefix of it (the
// 8-character short id the tools show), a [[task:…]] reference or a
// /tasks?task=… link; of the tasks user sees.
func findTask(raw, user string) (*Task, error) {
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
	for _, f := range [][2]string{{"title", "title"}, {"notes", "description"}} {
		if args.Has(f[0]) {
			s, err := strArg(args, f[0], false)
			if err != nil {
				return nil, err
			}
			data.Set(f[1], strings.TrimSpace(s))
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
	for _, f := range []string{"keep", "private"} {
		if args.Has(f) {
			b, ok := jsonobj.As[bool](args[f])
			if !ok {
				return nil, toolErr(f + " must be true or false")
			}
			data.Set(f, b)
		}
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
func tagHints(task *Task, user string) string {
	var others []*SearchDoc
	for _, d := range taskSearchDocs() {
		if d.taskID() != task.ID && canSeeTask(user, d.Task) {
			others = append(others, d)
		}
	}
	have := taskTagsLower(task)
	text := textsearch.ParseQuery(task.Title + " " + task.Description).Text
	counts := map[string]int{}
	var order []string
	if strings.TrimSpace(text) != "" {
		for _, d := range newDocIndex(others).search(text, similarTasks) {
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
	for _, d := range others {
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

// homeTags: a new task's tags, the default list's new-task tags (its list, its
// single tags, as its "+ New" and c t give them) before the ones given, so
// the task shows in user's tasks. Not when the given tags already name a
// declared list: the task is filed there.
func homeTags(tags json.RawMessage, user string) json.RawMessage {
	l, ok := jsonobj.As[[]string](tags)
	if s, isStr := jsonobj.As[string](tags); isStr {
		l, ok = splitComma(s), true
	}
	if !ok && !jsonobj.IsNull(tags) {
		return tags // createTask refuses it
	}
	home := lookupDefaultList(user)
	if home == nil {
		return tags
	}
	lists := taskLists(user)
	for _, t := range l {
		if _, ok := lists[strings.ToLower(strings.TrimLeft(t, "#"))]; ok {
			return tags
		}
	}
	b, _ := json.Marshal(taskmodel.CleanTags(append(newTaskTags(home.Params), l...)))
	return b
}

func createTaskTool(args jsonObj, base, user string) (string, error) {
	if _, err := strArg(args, "title", true); err != nil {
		return "", err
	}
	data, err := taskFields(args, nil)
	if err != nil {
		return "", err
	}
	data["tags"] = homeTags(data["tags"], user)
	// As c t in the browser: private when the user's default list is theirs alone.
	if h := lookupDefaultList(user); !data.Has("private") && h != nil && ownPrivate(user, h.Slug) {
		data.Set("private", true)
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

// otherArgs: the arguments besides those named, A–Z.
func otherArgs(args jsonObj, allowed ...string) []string {
	var other []string
	for k := range args {
		if !slices.Contains(allowed, k) {
			other = append(other, k)
		}
	}
	sort.Strings(other)
	return other
}

// updateTaskTool: one task's fields, status and comment; status deleted
// deletes it; ids instead of id tags several tasks (tagTasksTool).
func updateTaskTool(args jsonObj, base, user string) (string, error) {
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
	if status == "deleted" {
		if other := otherArgs(args, "id", "status", "summary"); len(other) > 0 {
			return "", toolErr("status deleted takes nothing else, not " + strings.Join(other, ", "))
		}
		msg, err := commitMsg("Delete task", args)
		if err != nil {
			return "", err
		}
		if err := deleteTask(user, task.ID, msg); err != nil {
			return "", err
		}
		return "Deleted task " + shortID(task.ID) + ": " + task.Title, nil
	}
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
			return "", toolErr("status must be open, done, wont_do, skip or deleted")
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

func tagTasksTool(args jsonObj, user string) (string, error) {
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
