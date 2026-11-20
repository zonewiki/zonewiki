// The tasks' views by their names of 2026-11: a wiki from before is taken
// over once, when Zone starts (The views' names in docs/spec/tasks.md).
package main

import (
	"encoding/json"
	"slices"
	"strings"
	"zonewiki/internal/mdtext"
	"zonewiki/internal/taskfiles"
)

// oldViewNames: what to change in the settings of a view written before
// 2026-11 (an embed's params, a list's settings in a registry), as
// setEmbedArgs takes it (a setting → its value, "" takes it out); nil when
// they are as Zone writes them now.
//
// Until then a view had four names for two things: view=focus|open|plan
// said the tab and, with open and plan, also how the Open tab showed its
// tasks (open: one list; plan: the board), group= said by what the board
// was grouped, and plan=on|off had said the same before that. Now view=
// says which tasks (today, open, closed) and group= how the Open tab shows
// them (none, day, person, list), and nothing else says either:
//
//   - view=focus is view=today;
//   - view=plan is view=open with group=day, unless group= said by what;
//   - view=open, which was the one list, is view=open with group=none,
//     unless group= said otherwise (view=open alone now opens the Open tab
//     as the view's group says);
//   - plan=on|off goes: it is group=day|none where no group= was written;
//   - of three labels (from when Plan was a tab) the first and the last stay.
func oldViewNames(get func(key string) (string, bool)) map[string]string {
	low := func(key string) string {
		v, _ := get(key)
		return strings.ToLower(strings.TrimSpace(v))
	}
	set := map[string]string{}
	view, group, plan := low("view"), low("group"), low("plan")
	was := group
	if !slices.Contains(tasksGroups, group) {
		switch {
		case plan == "on":
			group = "day"
		case plan == "off":
			group = "none"
		case view == "open":
			group = "none"
		case view == "plan":
			group = "day"
		default:
			group = ""
		}
	}
	if _, ok := get("plan"); ok {
		set["plan"] = ""
	}
	switch view {
	case "focus":
		set["view"] = "today"
	case "plan":
		set["view"] = "open"
	}
	if group != "" && group != was {
		set["group"] = group
	}
	if labels, ok := get("labels"); ok {
		if names := strings.Split(labels, ","); len(names) == 3 {
			set["labels"] = names[0] + "," + names[2]
		}
	}
	if len(set) == 0 {
		return nil
	}
	return set
}

// oldOnlyViewName: a view's settings (get) say a name only Zone before
// 2026-11 wrote: view=focus, view=plan, or plan= at all. Proof that they
// were never renamed (view=open alone, or three labels, could be today's).
func oldOnlyViewName(get func(key string) (string, bool)) bool {
	view, _ := get("view")
	view = strings.ToLower(strings.TrimSpace(view))
	_, plan := get("plan")
	return view == "focus" || view == "plan" || plan
}

// registryHasOldViews: a registry whose settings (a list's, My tasks', All
// tasks') say a name only the old Zone wrote (oldOnlyViewName).
func registryHasOldViews(reg *taskfiles.Registry) bool {
	in := func(m map[string]string) func(string) (string, bool) {
		return func(k string) (string, bool) { v, ok := m[k]; return v, ok }
	}
	for _, l := range reg.Lists {
		if _, ok := l.Extra["plan"]; ok || oldOnlyViewName(in(l.Settings)) {
			return true
		}
	}
	return oldOnlyViewName(in(reg.Mine)) || oldOnlyViewName(in(reg.All))
}

// textHasOldViews: a page's [[tasks: …]] embeds (code aside) say a name
// only the old Zone wrote (oldOnlyViewName).
func textHasOldViews(text string) bool {
	if !strings.Contains(strings.ToLower(text), "[[tasks:") {
		return false
	}
	masked, _ := mdtext.MaskCode(text)
	for _, m := range tasksRE.FindAllStringSubmatch(masked, -1) {
		params := embedParams(m[1])
		if oldOnlyViewName(func(key string) (string, bool) { v, ok := params[key]; return v, ok }) {
			return true
		}
	}
	return false
}

// embedParams: an embed's args as key → value, lowercase keys, the first of
// a key written twice.
func embedParams(args string) map[string]string {
	params := map[string]string{}
	for _, word := range strings.Fields(args) {
		if k, v, ok := strings.Cut(word, "="); ok {
			if _, twice := params[strings.ToLower(k)]; !twice {
				params[strings.ToLower(k)] = v
			}
		}
	}
	return params
}

// renameViewsInSettings changes a registry's settings of a view (a list's,
// My tasks', All tasks') to the names of 2026-11; true when it did.
func renameViewsInSettings(settings map[string]string) bool {
	set := oldViewNames(func(key string) (string, bool) { v, ok := settings[key]; return v, ok })
	for k, v := range set {
		if v == "" {
			delete(settings, k)
		} else {
			settings[k] = v
		}
	}
	return len(set) > 0
}

// renameViewsInText: a page's text with its [[tasks: …]] embeds written by
// the names of 2026-11; code stays as written.
func renameViewsInText(text string) string {
	if !strings.Contains(strings.ToLower(text), "[[tasks:") {
		return text
	}
	masked, stash := mdtext.MaskCode(text)
	masked = tasksRE.ReplaceAllStringFunc(masked, func(m string) string {
		at := tasksRE.FindStringSubmatchIndex(m) // at[2:4]: the args
		args := m[at[2]:at[3]]
		params := embedParams(args)
		set := oldViewNames(func(key string) (string, bool) { v, ok := params[key]; return v, ok })
		if set == nil {
			return m
		}
		return m[:at[2]] + setEmbedArgs(args, set) + m[at[3]:]
	})
	return mdtext.UnmaskCode(masked, stash)
}

// migrateViewsLocked takes a wiki over to the views' names of 2026-11, when
// Zone starts, after the lists' migrations and under their locks (pagesMu,
// the task files' lock); one commit, "Name the tasks' views Today and Open".
// Once: the shared registry says version 3 afterwards, and so does each
// user's; a wiki without one, .zone-format (noteWikiFormat), which a start
// writes after this.
//
// The registries' settings (each list's, My tasks', All tasks') and every
// page's [[tasks: …]] embeds are rewritten (oldViewNames); what a view
// shows stays what it was. Links (/tasks?view=plan) stay as written: the
// old names are unknown from now on, and such a link opens the view's
// default tab.
//
// Until 2026-11-20 the Inbox's migration wrote version 3 itself, so a wiki
// that went from before the Inbox straight to these names was taken for
// renamed. Such a wiki (a shared registry at this version, and no
// .zone-format yet: this runs only then) is looked through once more, and
// only a registry or a page that says a name only the old Zone wrote is
// rewritten (oldOnlyViewName): one at this version may as well be by
// today's names (a fresh registry, one made by hand), where view=open alone
// is the board.
func migrateViewsLocked() error {
	shared, err := taskFiles.ReadListsStrict("")
	if err != nil {
		return err
	}
	mend := shared.Version >= taskfiles.ListsVersion
	var written []string
	for _, o := range slices.Backward(taskFiles.EveryOwner()) { // the shared one, whose version says "done", last
		if !exists(taskFiles.ListsPath(o)) {
			continue // no registry of their own: none is made for this
		}
		reg := shared
		if o != "" {
			if reg, err = taskFiles.ReadListsStrict(o); err != nil {
				return err
			}
		}
		if mend && !registryHasOldViews(reg) {
			continue
		}
		for _, l := range reg.Lists {
			// plan= is no setting of a list anymore: the registry read it as
			// a key it doesn't know.
			if raw, ok := l.Extra["plan"]; ok {
				var plan string
				if json.Unmarshal(raw, &plan) == nil {
					l.Settings["plan"] = plan
				}
				delete(l.Extra, "plan")
			}
			renameViewsInSettings(l.Settings)
		}
		renameViewsInSettings(reg.Mine)
		renameViewsInSettings(reg.All)
		reg.Version = taskfiles.ListsVersion
		path, err := taskFiles.WriteListsOf(o, reg)
		if err != nil {
			return err
		}
		written = append(written, path...)
	}
	newText := map[string]string{}
	var pagePaths []string
	for _, p := range sortedPages() {
		text, err := readText(p.Path)
		if err != nil {
			continue
		}
		if mend && !textHasOldViews(text) {
			continue
		}
		if n := renameViewsInText(text); n != text {
			newText[p.Path] = n
			pagePaths = append(pagePaths, p.Path)
		}
	}
	if _, err := keepPendingPages(pagePaths, "pages before the tasks' views were renamed"); err != nil {
		logf("tasks: the pages keep their embeds' old view names (view=focus, view=plan, plan=: they open the default tab now): %v", err)
		pagePaths = nil
	}
	for _, path := range pagePaths {
		if writeText(path, newText[path]) == nil {
			written = append(written, path)
		}
	}
	if written = nonEmpty(written); len(written) == 0 {
		return nil
	}
	logf("tasks: the views are named Today and Open: %d pages rewritten", len(pagePaths))
	taskFiles.Release()
	_ = gitCommit("", "Name the tasks' views Today and Open", written...)
	return nil
}
