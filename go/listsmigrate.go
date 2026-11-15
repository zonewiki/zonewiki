// The lists' migration, at start: a wiki from before lists were places
// (a list was a tag a page declared with [[tasks: list=<tag>]], pinned by
// pin= in that embed) gets its registries: each declared list an entry,
// each task its list in place of that tag, each pin in the registry of
// whose it is. One commit; no task changes its file, so nobody sees more
// or less than before. Run once: the shared registry's version says it is
// done.
package main

import (
	"encoding/json"
	"errors"
	"net/url"
	"os"
	"path/filepath"
	"slices"
	"sort"
	"strings"
	"zonewiki/internal/mdtext"
	"zonewiki/internal/taskfiles"
)

// oldAllTasksFile: where the settings of All tasks were kept
// ({"all": {…}}); they are in the shared registry now.
func oldAllTasksFile() string { return filepath.Join(taskFiles.Dir(""), "settings.json") }

// pinnedParam: an embed pinned its list (pin=1, or pin=default for the
// default list), before pins were the user's own.
func pinnedParam(p TasksParams) bool {
	pin := strings.TrimSpace(p.Get("pin"))
	return isTrue(pin) || strings.EqualFold(pin, "default")
}

// stripPins: page text without the pin= of its [[tasks: …]] embeds (code
// stays as written).
func stripPins(text string) string {
	masked, stash := mdtext.MaskCode(text)
	masked = tasksRE.ReplaceAllStringFunc(masked, func(m string) string {
		at := tasksRE.FindStringSubmatchIndex(m) // at[2:4]: the args
		var kept []string
		for _, word := range strings.Fields(m[at[2]:at[3]]) {
			if k, _, ok := strings.Cut(word, "="); !ok || strings.ToLower(k) != "pin" {
				kept = append(kept, word)
			}
		}
		args := strings.Join(kept, " ")
		if args != "" {
			args = " " + args
		}
		return strings.TrimRight(m[:at[2]], " \t") + args + m[at[3]:]
	})
	return mdtext.UnmaskCode(masked, stash)
}

// listOutOfFilter: a tag= value (an embed's, a /tasks link's) that asks for
// a list's former tag among tags that must all be there (a,b; not a|b, not
// -a): the list (the first such), and the value without it.
func listOutOfFilter(value string, isList func(string) bool) (list, rest string, ok bool) {
	if strings.Contains(value, "|") {
		return "", value, false
	}
	terms := strings.Split(value, ",")
	for i, term := range terms {
		name := strings.ToLower(strings.TrimLeft(strings.TrimSpace(term), "#"))
		if name != "" && !strings.HasPrefix(name, "-") && isList(name) {
			return name, strings.Join(slices.Delete(terms, i, i+1), ","), true
		}
	}
	return "", value, false
}

// tagsToLists: page text whose [[tasks: tag=<a list's former tag>]] embeds
// and /tasks?tag= links show the list instead (list=<name>, the other tags
// still a filter): the tag left the list's tasks, so the filter would find
// none of them. An embed or link that names a list already stays; code
// stays as written. isList: a (lowercase) name is a list the page sees.
func tagsToLists(text string, isList func(string) bool) string {
	masked, stash := mdtext.MaskCode(text)
	masked = tasksRE.ReplaceAllStringFunc(masked, func(m string) string {
		at := tasksRE.FindStringSubmatchIndex(m) // at[2:4]: the args
		args := m[at[2]:at[3]]
		if p := parseTasksParams(args); p.Has("list") || !p.Has("tag") {
			return m
		}
		changed := false
		args = embedWordRE.ReplaceAllStringFunc(args, func(word string) string {
			k, v, ok := strings.Cut(word, "=")
			if !ok || strings.ToLower(k) != "tag" || changed {
				return word
			}
			list, rest, ok := listOutOfFilter(v, isList)
			if !ok {
				return word
			}
			changed = true
			if rest == "" {
				return "list=" + list
			}
			return "list=" + list + " " + k + "=" + rest
		})
		return m[:at[2]] + args + m[at[3]:]
	})
	masked = tasksURLRE.ReplaceAllStringFunc(masked, func(m string) string {
		at := strings.Index(m, "/tasks?") + len("/tasks?")
		parts := strings.Split(m[at:], "&")
		for _, part := range parts {
			if k, _, _ := strings.Cut(part, "="); k == "list" || k == "pinned" {
				return m
			}
		}
		for i, part := range parts {
			k, v, _ := strings.Cut(part, "=")
			value, err := url.QueryUnescape(v)
			if k != "tag" || err != nil {
				continue
			}
			if list, rest, ok := listOutOfFilter(value, isList); ok {
				parts[i] = "list=" + url.QueryEscape(list)
				if rest != "" {
					parts[i] += "&tag=" + url.QueryEscape(rest)
				}
				return m[:at] + strings.Join(parts, "&")
			}
		}
		return m
	})
	return mdtext.UnmaskCode(masked, stash)
}

// listSettingsOf: an embed's params as a list's settings: those a list
// has, each as List settings… would take it (one that it wouldn't is left
// out); noshare: its page is not for sharing.
func listSettingsOf(p TasksParams, noshare bool) map[string]string {
	out := map[string]string{}
	for _, k := range listSettingKeys {
		if v := strings.TrimSpace(p.Get(k)); v != "" {
			if set, err := checkEmbedSettings(map[string]string{k: v}, listSettingKeys); err == nil && set[k] != "" {
				out[k] = set[k]
			}
		}
	}
	if noshare {
		out["noshare"] = "1"
	}
	return out
}

// migrateLists takes the lists over from the pages, when that hasn't been
// done (migrateListsLocked). A failure is logged and stops every change to
// lists until a start succeeds (listsUnmigrated): the tasks stay as they
// are, and nothing is half taken over.
func migrateLists() {
	pagesMu.Lock()
	defer pagesMu.Unlock()
	taskFiles.Lock()
	defer taskFiles.Unlock()
	err := migrateListsLocked()
	if err == nil {
		err = migrateInboxLocked()
	}
	if err != nil {
		listsUnmigrated = true
		logf("The task lists couldn't be set up (lists stay as they were, and can't be changed, until Zone starts with this mended): %v", err)
	}
}

// oldPin: an embed that pinned something: on a page of owner's ("" a
// shared page), its list ("" none: a pinned view that is no list).
type oldPin struct {
	owner, list, slug string
	isDefault         bool
}

func migrateListsLocked() error {
	shared, err := taskFiles.ReadListsStrict("")
	if err != nil {
		return err
	}
	if shared.Version >= 1 {
		return nil
	}
	owners := taskFiles.EveryOwner()
	regs := map[string]*taskfiles.Registry{"": shared}
	for _, o := range owners[1:] {
		if regs[o], err = taskFiles.ReadListsStrict(o); err != nil {
			return err
		}
		// A user's registry with a version: a wiki whose lists are places
		// already, and that has no shared list.
		if regs[o].Version >= 1 {
			return nil
		}
	}
	// Every task file, read before anything is decided: one that can't be
	// read stops it all.
	type taskFile struct {
		taskfiles.File
		tasks   []*Task
		changed bool
	}
	var files []*taskFile
	privateTags := map[string]map[string]bool{} // owner → the tags their private tasks carry
	for _, f := range taskFiles.Files(owners) {
		tasks, err := taskfiles.ReadStrict(f.Path)
		if err != nil {
			return errors.New(f.Path + ": " + err.Error())
		}
		files = append(files, &taskFile{File: f, tasks: tasks})
		if f.Owner != "" && privateTags[f.Owner] == nil {
			privateTags[f.Owner] = map[string]bool{}
		}
		for _, t := range tasks {
			for tag := range taskTagsLower(t) {
				if f.Owner != "" {
					privateTags[f.Owner][tag] = true
				}
			}
		}
	}
	// 1. The lists the pages declare, each by its first page A–Z: a shared
	// page's a shared list; a private page's its user's own, unless a shared
	// page declares the name too and they have no private task with it
	// (their embed then shows the shared list).
	type decl struct {
		owner, name string
		params      TasksParams
		slug        string
	}
	var decls []decl
	var pins []oldPin
	var pinPages []PageFile
	pages := sortedPages()
	for _, p := range pages {
		o, private := pageOwner(p.Slug)
		if private && (o == "" || regs[o] == nil) {
			continue // a folder in private/ that is no user's
		}
		e := pageMetaEntryOf(p.Path, p.Slug)
		pinned := false
		for _, params := range e.embeds {
			name := cleanList(params.Get("list"))
			if name != "" {
				if clean, err := checkListName(name); err != nil {
					logf("lists: %s declares a list that can't be one, left as it is: %v", p.Slug, err)
					name = ""
				} else {
					name = clean
					decls = append(decls, decl{o, name, params, p.Slug})
				}
			}
			if pinnedParam(params) {
				pinned = true
				pins = append(pins, oldPin{o, name, p.Slug, strings.EqualFold(strings.TrimSpace(params.Get("pin")), "default")})
			}
		}
		if pinned {
			pinPages = append(pinPages, p)
		}
	}
	made := 0
	add := func(o, name string, params TasksParams, slug string) {
		if regs[o].Find(name) == nil {
			regs[o].Lists = append(regs[o].Lists, &taskfiles.List{Name: name, Owner: o, Settings: listSettingsOf(params, isNoshare(slug))})
			made++
		}
	}
	for _, d := range decls {
		if d.owner == "" {
			add("", d.name, d.params, d.slug)
		}
	}
	for _, d := range decls {
		if d.owner != "" && (shared.Find(d.name) == nil || privateTags[d.owner][d.name]) {
			add(d.owner, d.name, d.params, d.slug)
		}
	}
	// 2. The tasks: the first A–Z of a task's tags that names a list of its
	// own kind (a shared task: the shared lists; a private one: its user's)
	// is its list from now on, and that tag goes. A private task without one
	// is in its user's Inbox.
	moved := 0
	for _, f := range files {
		reg := regs[f.Owner]
		for _, t := range f.tasks {
			was, tags := t.List, len(t.Tags)
			if t.List != "" && reg.Find(t.List) == nil {
				t.List = "" // a list field from long ago, naming nothing
			}
			if t.List == "" {
				var hits []string
				for tag := range taskTagsLower(t) {
					if reg.Find(tag) != nil {
						hits = append(hits, tag)
					}
				}
				if sort.Strings(hits); len(hits) > 0 {
					t.List = hits[0]
				}
			}
			if t.List != "" {
				t.Tags = slices.DeleteFunc(t.Tags, func(tag string) bool { return strings.ToLower(tag) == t.List })
			}
			if t.List != was || len(t.Tags) != tags {
				f.changed = true
				moved++
			}
		}
	}
	// 3. The pins: a shared page's are the wiki's (everyone's who set none
	// of their own), a private page's its user's, who then has theirs
	// first and the wiki's after, as before. A pin of a view that is no
	// list has nowhere to go.
	byName := func(a, b oldPin) int { return strings.Compare(a.list, b.list) }
	var sharedPins []oldPin
	ownPins := map[string][]oldPin{}
	for _, p := range pins {
		switch {
		case p.list == "":
			logf("lists: %s pinned a view that is no list ([[tasks: … pin=…]] without list=); pins are of lists now, so it is no longer pinned", p.slug)
		case p.owner == "":
			sharedPins = append(sharedPins, p)
		default:
			ownPins[p.owner] = append(ownPins[p.owner], p)
		}
	}
	slices.SortStableFunc(sharedPins, byName)
	// order: the pins' refs as their reader names them, the default one
	// (pin=default, else the first) first, each once.
	order := func(pins []oldPin, ref func(oldPin) string) []string {
		if i := slices.IndexFunc(pins, func(p oldPin) bool { return p.isDefault }); i > 0 {
			def := pins[i]
			pins = slices.Insert(slices.Delete(slices.Clone(pins), i, i+1), 0, def)
		}
		var out []string
		for _, p := range pins {
			if r := ref(p); r != "" && !slices.Contains(out, r) {
				out = append(out, r)
			}
		}
		return out
	}
	wikiPins := order(sharedPins, func(p oldPin) string {
		if shared.Find(p.list) == nil {
			return ""
		}
		return p.list
	})
	if len(wikiPins) > 0 {
		shared.Pinned, shared.PinsSet = wikiPins, true
	}
	for _, o := range owners[1:] {
		own := ownPins[o]
		if len(own) == 0 {
			continue
		}
		slices.SortStableFunc(own, byName)
		reg := regs[o]
		refs := order(append(own, sharedPins...), func(p oldPin) string {
			switch {
			case p.owner != "" && reg.Find(p.list) != nil, p.owner != "" && shared.Find(p.list) != nil:
				return p.list // their own list, or (with none of that name) the shared one
			case p.owner == "" && shared.Find(p.list) != nil && reg.Find(p.list) != nil:
				return sharedRef + p.list
			case p.owner == "" && shared.Find(p.list) != nil:
				return p.list
			}
			return ""
		})
		reg.Pinned, reg.PinsSet = refs, true
	}
	// 4. The settings of All tasks.
	var oldAll map[string]map[string]string
	rawAll, errAll := os.ReadFile(oldAllTasksFile())
	if errAll == nil {
		if json.Unmarshal(rawAll, &oldAll) != nil {
			logf("lists: %s can't be read; All tasks starts with no settings", oldAllTasksFile())
		} else if set, err := checkEmbedSettings(oldAll["all"], allSettingKeys); err == nil && len(shared.All) == 0 {
			shared.All = map[string]string{}
			for k, v := range set {
				if v != "" {
					shared.All[k] = v
				}
			}
		}
	}
	// Nothing of the old kind: a wiki that never had lists. Its registry is
	// written when its first list is made.
	if made == 0 && moved == 0 && len(pins) == 0 && errAll != nil {
		return nil
	}
	// Written in an order a crash can't make wrong: the tasks first (run
	// again, they keep the lists they got), then the users' registries, the
	// shared one (whose version says "done"), and last the pages' pin=.
	var written []string
	for _, f := range files {
		if f.changed {
			if err := taskfiles.Write(f.Path, f.tasks); err != nil {
				return err
			}
			written = append(written, f.Path)
		}
	}
	for _, o := range slices.Backward(owners) { // the shared one last
		reg := regs[o]
		if o != "" && len(reg.Lists) == 0 && !reg.PinsSet {
			continue
		}
		reg.Version = 1 // lists as places; the Inbox comes next (migrateInboxLocked)
		path, err := taskFiles.WriteListsOf(o, reg)
		if err != nil {
			return err
		}
		written = append(written, path)
	}
	if errAll == nil {
		if err := os.Remove(oldAllTasksFile()); err == nil {
			written = append(written, oldAllTasksFile())
		}
	}
	// The pages: pin= goes, and an embed or a link that filtered by a
	// list's former tag (tag=shop) shows the list (list=shop): the tag left
	// the list's tasks. A private page sees its user's lists and the shared.
	pinned := map[string]bool{}
	for _, p := range pinPages {
		pinned[p.Path] = true
	}
	newText := map[string]string{}
	var pagePaths []string
	for _, p := range pages {
		o, private := pageOwner(p.Slug)
		if private && (o == "" || regs[o] == nil) {
			continue
		}
		text, err := readText(p.Path)
		if err != nil || !pinned[p.Path] && !strings.Contains(text, "tag=") {
			continue
		}
		n := tagsToLists(text, func(name string) bool {
			return shared.Find(name) != nil || o != "" && regs[o].Find(name) != nil
		})
		if pinned[p.Path] {
			n = stripPins(n)
		}
		if n != text {
			newText[p.Path] = n
			pagePaths = append(pagePaths, p.Path)
		}
	}
	if _, err := keepPendingPages(pagePaths, "pages before the task lists were set up"); err != nil {
		logf("lists: the pages keep their pin= (it means nothing now) and their tag= of a list's former tag (it finds none of the list's tasks: write list=): %v", err)
		pagePaths = nil
	}
	for _, path := range pagePaths {
		if writeText(path, newText[path]) == nil {
			written = append(written, path)
		}
	}
	logf("lists: %d lists taken over from the pages, %d tasks given their list, %d pages rewritten", made, moved, len(pagePaths))
	_ = gitCommit("", "Make task lists their own", nonEmpty(written)...)
	return nil
}

// inboxListName: what a list called "inbox" is called from the Inbox on,
// which keeps that name.
const inboxListName = "inbox-list"

// migrateInboxLocked takes a wiki over to the Inbox, when Zone starts,
// after migrateListsLocked and under its locks; one commit.
//
// Once for each registry that says version 1 (lists as places, from before
// the Inbox; it says 2 afterwards), the shared one and each user's own:
//  1. A list called "inbox" is called "inbox-list": the name is the Inbox's.
//  2. With users, a user's list "private" that has no settings and was
//     where their new tasks went (pinned by them, or they pinned nothing)
//     becomes their Inbox: its tasks lose their list, the list and its pin
//     go. Zone made that list, as a place for tasks without one.
//  3. The lists whose tasks were not in a user's My tasks, since they had
//     not pinned them, are left out of it (home=off): without users any
//     list, with users each user's private lists. Nobody's My tasks grows.
//
// At every start, with users: a shared task without a list goes to the
// shared list "general", made for it and pinned for the wiki: a shared task
// is always in a list. That takes over the tasks that were in no list, and
// a wiki whose users were turned on later.
func migrateInboxLocked() error {
	shared, err := taskFiles.ReadListsStrict("")
	if err != nil {
		return err
	}
	users := settings().Users != nil
	owners := taskFiles.EveryOwner()
	regs := map[string]*taskfiles.Registry{"": shared}
	for _, o := range owners[1:] {
		if regs[o], err = taskFiles.ReadListsStrict(o); err != nil {
			return err
		}
	}
	// first: a registry from before the Inbox (version 1). Each registry
	// says so for itself: a wiki whose users have lists and that never had a
	// shared one has no shared registry to say it.
	first := map[string]bool{}
	any := users
	for _, o := range owners {
		first[o] = regs[o].Version == 1
		any = any || first[o]
	}
	if !any {
		return nil
	}
	type taskFile struct {
		taskfiles.File
		tasks   []*Task
		changed bool
	}
	var files []*taskFile
	for _, f := range taskFiles.Files(owners) {
		tasks, err := taskfiles.ReadStrict(f.Path)
		if err != nil {
			return errors.New(f.Path + ": " + err.Error())
		}
		files = append(files, &taskFile{File: f, tasks: tasks})
	}
	regChanged := map[string]bool{}
	// relist: owner's tasks in the list from are in to from now on.
	relist := func(owner, from, to string) (n int) {
		for _, f := range files {
			if f.Owner != owner {
				continue
			}
			for _, t := range f.tasks {
				if t.List == from {
					t.List, f.changed, n = to, true, n+1
				}
			}
		}
		return n
	}
	renamed, inboxed, leftOut, general := 0, 0, 0, 0
	{
		// 1. "inbox" is the Inbox's name.
		for _, o := range owners {
			l := regs[o].Find(inboxRef)
			if !first[o] || l == nil || regs[o].Find(inboxListName) != nil {
				continue
			}
			l.Name = inboxListName
			relist(o, inboxRef, inboxListName)
			regChanged[o], renamed = true, renamed+1
			for _, p := range owners {
				if o != "" && p != o {
					continue // a private list is in its owner's pins alone
				}
				own := p != "" && o == "" && regs[p].Find(inboxRef) != nil
				if renameRefs(regs[p], func(r string) string {
					switch {
					case r == inboxRef && !own:
						return inboxListName
					case o == "" && r == sharedRef+inboxRef:
						return sharedRef + inboxListName
					}
					return r
				}) {
					regChanged[p] = true
				}
			}
		}
		// 2. A user's list "private" that took their new tasks is their Inbox.
		for _, o := range owners[1:] {
			reg := regs[o]
			l := reg.Find(privateListName)
			if !first[o] || l == nil || len(l.Settings) > 0 {
				continue
			}
			pinnedOwn := false
			for _, r := range reg.Pinned {
				pinnedOwn = pinnedOwn || reg.Find(r) != nil || shared.Find(strings.TrimPrefix(r, sharedRef)) != nil
			}
			if !slices.Contains(reg.Pinned, privateListName) && (pinnedOwn || !reg.PinsSet && len(shared.Pinned) > 0) {
				continue // an ordinary list of theirs, not pinned: it stays one
			}
			inboxed += relist(o, privateListName, "")
			reg.Lists = slices.DeleteFunc(reg.Lists, func(x *taskfiles.List) bool { return x == l })
			reg.Pinned = slices.DeleteFunc(reg.Pinned, func(r string) bool { return r == privateListName })
			regChanged[o] = true
		}
		// 3. What wasn't in My tasks stays out of it.
		for _, o := range owners {
			if !first[o] || o == "" && users {
				continue // a shared task is in My tasks by its assignee
			}
			reg := regs[o]
			for _, l := range reg.Lists {
				if slices.Contains(reg.Pinned, l.Name) || l.Settings["home"] != "" {
					continue
				}
				l.Settings["home"] = "off"
				regChanged[o], leftOut = true, leftOut+1
			}
		}
		for _, o := range owners {
			if first[o] {
				regs[o].Version, regChanged[o] = taskfiles.ListsVersion, true
			}
		}
	}
	// With users a shared task is always in a list.
	if users {
		if general = relist("", "", generalListName); general > 0 && shared.Find(generalListName) == nil {
			shared.Lists = append(shared.Lists, &taskfiles.List{Name: generalListName, Settings: map[string]string{}})
			if !slices.Contains(shared.Pinned, generalListName) {
				shared.Pinned, shared.PinsSet = append(shared.Pinned, generalListName), true
			}
			regChanged[""] = true
		}
	}
	// The tasks first, then the users' registries, the shared one last: its
	// version says "done", and a start that was interrupted runs again.
	var written []string
	for _, f := range files {
		if f.changed {
			if err := taskfiles.Write(f.Path, f.tasks); err != nil {
				return err
			}
			written = append(written, f.Path)
		}
	}
	for _, o := range slices.Backward(owners) {
		if !regChanged[o] {
			continue
		}
		path, err := taskFiles.WriteListsOf(o, regs[o])
		if err != nil {
			return err
		}
		written = append(written, path)
	}
	if written = nonEmpty(written); len(written) == 0 {
		return nil
	}
	logf("lists: the Inbox: %d tasks of lists \"private\" are in their users' Inbox, %d shared tasks without a list are in #%s, %d lists are left out of My tasks, %d lists called inbox renamed to %s",
		inboxed, general, generalListName, leftOut, renamed, inboxListName)
	_ = gitCommit("", "Give every task a list: the Inbox", written...)
	return nil
}
