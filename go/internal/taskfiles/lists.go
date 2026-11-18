package taskfiles

import (
	"bytes"
	"encoding/json"
	"errors"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"zonewiki/internal/asker"
	"zonewiki/internal/atomicfile"
	"zonewiki/internal/jsonobj"
)

// The lists: lists.json beside the task files, the shared lists in _tasks/,
// a user's private ones in private/<user>/_tasks/. A list is a name and its
// settings; a task is in one by naming it (Task.List), and a task's file
// says which registry that name is in. A registry also holds whose pins it
// is (pinned, default: a user's own in their file, the wiki's in the shared
// one; mine: a user sees only their own tasks in Pinned) and, the shared one, the settings of All tasks.

// ListsName: the registry's file in a tasks folder.
const ListsName = "lists.json"

// ListsVersion: the format this Zone writes. A registry without a version
// (or no file) is a wiki from before lists were kept here; version 1 is
// one from before the Inbox (a task without a list is in its user's Inbox,
// and with users a shared task is always in a list).
const ListsVersion = 3

// ListSettings: a list's settings, in the order its line keeps them. Their
// values are text, as an embed writes them (limit "8", keep "1").
// home "off": the list's tasks are left out of My tasks.
var ListSettings = []string{"view", "limit", "group", "keep", "labels", "new", "noshare", "home"}

// List: a list as its registry keeps it. Owner is not in the file: whose
// registry it was read from ("" the shared one).
type List struct {
	Name     string
	Settings map[string]string
	Extra    map[string]json.RawMessage // keys Zone doesn't know, as the file had them
	Owner    string
}

// Registry: a lists.json.
type Registry struct {
	Version int
	Lists   []*List
	Pinned  []string          // the pinned lists' names, in order: the pills of the list switcher
	PinsSet bool              // the file says which are pinned, be it none ("pinned": [])
	Mine    map[string]string // the settings of My tasks: a user's own, without users the wiki's
	All     map[string]string // the settings of All tasks (the shared registry)
	// Order: the manual order of My tasks, task ids, first to last: a user's
	// own, without users the wiki's. OrderSet: the file has one (be it
	// empty); without, My tasks starts from the lists' order.
	Order    []string
	OrderSet bool
	Extra    map[string]json.RawMessage
}

// Owners: the users a shared list belongs to ("owners": ["anna"] in its
// entry): who may rename it, delete it and set its settings. None: a list
// from before lists had owners.
func (l *List) Owners() []string { return jsonobj.TextList(l.Extra["owners"]) }

// SetOwners: the list's owners from now (none: no key).
func (l *List) SetOwners(names []string) {
	if len(names) == 0 {
		delete(l.Extra, "owners")
		return
	}
	if l.Extra == nil {
		l.Extra = map[string]json.RawMessage{}
	}
	l.Extra["owners"] = json.RawMessage(strings.ReplaceAll(jsonobj.Text(names), ",", ", "))
}

// For: who a shared list is for ("for": ["@hiring", "anna"] in its entry:
// a group's members, a user); none: everyone. A list for some is kept apart
// from the registry everyone reads: its entry is the file list.json in its
// own folder (ListFileName), which is also what says, to every read of the
// tasks there, whom they are for.
func (l *List) For() []string { return jsonobj.TextList(l.Extra["for"]) }

// SetFor: who the list is for from now (none: everyone, no key).
func (l *List) SetFor(who []string) {
	if len(who) == 0 {
		delete(l.Extra, "for")
		return
	}
	if l.Extra == nil {
		l.Extra = map[string]json.RawMessage{}
	}
	l.Extra["for"] = json.RawMessage(strings.ReplaceAll(jsonobj.Text(who), ",", ", "))
}

// Gone: the entry is what a list for some left in its folder when it was
// deleted or renamed ("gone": true): no list anymore, but the folder's
// history is still only its members' to see (forOf).
func (l *List) Gone() bool { _, gone := l.Extra["gone"]; return gone }

// ListFileName: the entry of a list for some, in the list's folder.
const ListFileName = "list.json"

// listFile: the entry in dir's list.json, nil when there is none (or it is
// no entry: such a folder is everyone's, as one without the file).
func listFile(dir string) *List {
	raw, err := os.ReadFile(filepath.Join(dir, ListFileName))
	if err != nil {
		return nil
	}
	r, ok := ParseRegistry([]byte(`{"lists": [` + string(raw) + `]}`))
	if !ok || len(r.Lists) != 1 {
		return nil
	}
	return r.Lists[0]
}

// What each folder's list.json said when it was last read, by its time and
// size: every read of a user's tasks asks whom each list's folder is for.
var forKept = struct {
	sync.Mutex
	of map[string]forEntry
}{of: map[string]forEntry{}}

type forEntry struct {
	mod  int64
	size int64
	who  []string
}

// forOf: whom the list's folder dir is for; none: everyone (no list.json).
func forOf(dir string) []string {
	path := filepath.Join(dir, ListFileName)
	info, err := os.Stat(path)
	forKept.Lock()
	defer forKept.Unlock()
	if err != nil {
		delete(forKept.of, path)
		return nil
	}
	if e, ok := forKept.of[path]; ok && e.mod == info.ModTime().UnixNano() && e.size == info.Size() {
		return e.who
	}
	var who []string
	if l := listFile(dir); l != nil {
		who = l.For()
	}
	forKept.of[path] = forEntry{info.ModTime().UnixNano(), info.Size(), who}
	return who
}

// seesDir: user sees the tasks in the shared list's folder dir: a list for
// everyone, or one they are among those it is for.
func (s *Store) seesDir(user asker.Asker, dir string) bool {
	who := forOf(dir)
	return len(who) == 0 || s.Member != nil && s.Member(user, who)
}

// SeesList: user sees the shared list named name and its tasks (seesDir);
// the tasks in no list, and a name that has no folder, are everyone's.
func (s *Store) SeesList(user asker.Asker, name string) bool {
	if user == Everyone || name == "" || !listDirRE.MatchString(name) {
		return true
	}
	return s.seesDir(user, s.dirOf("", name))
}

// ForOfList: whom the shared list named name is for, as its folder says
// (also what a list for some left there when it went); none: everyone.
func (s *Store) ForOfList(name string) []string {
	if name == "" || !listDirRE.MatchString(name) {
		return nil
	}
	return forOf(s.dirOf("", name))
}

// ForSome: the names of the shared lists whose folders are for some now,
// A–Z: a look at each list's folder (forOf keeps what it read), no read of
// a task.
func (s *Store) ForSome() []string {
	var names []string
	entries, _ := os.ReadDir(filepath.Join(s.Dir(""), ListsDirName))
	for _, e := range entries {
		if e.IsDir() && len(forOf(filepath.Join(s.Dir(""), ListsDirName, e.Name()))) > 0 {
			names = append(names, e.Name())
		}
	}
	return names
}

// Find: the list named name (as the file has it), nil when there is none.
func (r *Registry) Find(name string) *List {
	for _, l := range r.Lists {
		if l.Name == name {
			return l
		}
	}
	return nil
}

// registryKeys: the keys Zone reads. "default" (until 2026-11 the list a new
// task goes to, now the first pinned one) is read and no longer written.
// "mine" holds the settings of My tasks; until 2026-11 it was true for a
// user whose Pinned showed only their own tasks, which reads as no settings.
var registryKeys = map[string]bool{"version": true, "lists": true, "pinned": true, "default": true, "mine": true, "all": true, "order": true}

// textMap: an object's members that are text, a number or a bool, as text.
func textMap(raw json.RawMessage) map[string]string {
	o, _ := jsonobj.As[jsonobj.Obj](raw)
	out := map[string]string{}
	for k, v := range o {
		if s, ok := jsonobj.ScalarText(v); ok {
			out[k] = s
		}
	}
	return out
}

// ParseRegistry reads a lists.json, leniently: a value of the wrong kind
// is its default, a list without a name is left out, of two lists with one
// name the first counts; ok false when the text is no JSON object.
func ParseRegistry(raw []byte) (*Registry, bool) {
	o, ok := jsonobj.Parse(raw)
	if !ok {
		return nil, false
	}
	r := &Registry{All: textMap(o["all"]), Mine: textMap(o["mine"]), PinsSet: o.Has("pinned"), OrderSet: o.Has("order")}
	for _, id := range jsonobj.TextList(o["order"]) {
		if id = strings.TrimSpace(id); id != "" && !slices.Contains(r.Order, id) {
			r.Order = append(r.Order, id)
		}
	}
	if n, ok := jsonobj.WholeNumber(o["version"]); ok && n > 0 {
		r.Version = n
	}
	seen := map[string]bool{}
	for _, lo := range jsonobj.List(o["lists"]) {
		l := &List{Name: listName(lo.Str("name")), Settings: map[string]string{}}
		if l.Name == "" || seen[l.Name] {
			continue
		}
		seen[l.Name] = true
		for k, v := range lo {
			switch s, isText := jsonobj.ScalarText(v); {
			case k == "name":
			case slices.Contains(ListSettings, k) && isText:
				if s != "" {
					l.Settings[k] = s
				}
			case !slices.Contains(ListSettings, k):
				if l.Extra == nil {
					l.Extra = map[string]json.RawMessage{}
				}
				l.Extra[k] = v
			}
		}
		r.Lists = append(r.Lists, l)
	}
	// A registry from before the first pinned list was the default one names
	// it in "default", whatever the order of its pins: it goes first.
	if first := listName(o.Str("default")); first != "" {
		r.Pinned, r.PinsSet = []string{first}, true
	}
	for _, name := range jsonobj.TextList(o["pinned"]) {
		if name = listName(name); name != "" && !slices.Contains(r.Pinned, name) {
			r.Pinned = append(r.Pinned, name)
		}
	}
	for k, v := range o {
		if !registryKeys[k] {
			if r.Extra == nil {
				r.Extra = map[string]json.RawMessage{}
			}
			r.Extra[k] = v
		}
	}
	return r, true
}

// listName: a name as a registry keeps it: trimmed, lowercase. What else a
// name may be (one word, no "#") is the server's to say.
func listName(s string) string { return strings.ToLower(strings.TrimSpace(s)) }

// member appends ,"key":value to an object's text (no comma when first).
func member(b *bytes.Buffer, first *bool, key string, value []byte) {
	if !*first {
		b.WriteString(", ")
	}
	*first = false
	k, _ := jsonobj.MarshalText(key)
	b.Write(k)
	b.WriteString(": ")
	b.Write(value)
}

// textObject: {"k": "v", …} on one line, keys first in order, the rest A–Z,
// then extra's A–Z.
func textObject(order []string, m map[string]string, extra map[string]json.RawMessage, lead ...string) []byte {
	var b bytes.Buffer
	b.WriteByte('{')
	first := true
	for i := 0; i+1 < len(lead); i += 2 {
		v, _ := jsonobj.MarshalText(lead[i+1])
		member(&b, &first, lead[i], v)
	}
	keys := slices.Clone(order)
	for _, k := range slices.Sorted(maps.Keys(m)) {
		if !slices.Contains(order, k) {
			keys = append(keys, k)
		}
	}
	for _, k := range keys {
		if v, ok := m[k]; ok && v != "" {
			text, _ := jsonobj.MarshalText(v)
			member(&b, &first, k, text)
		}
	}
	for _, k := range slices.Sorted(maps.Keys(extra)) {
		member(&b, &first, k, bytes.TrimSpace(extra[k]))
	}
	b.WriteByte('}')
	return b.Bytes()
}

// Encode: the registry's text: the version, then each list on a line of
// its own (so a list is added or changed by hand in one line, and a commit
// shows the list that changed), the pins, the settings of All tasks, and
// the keys Zone doesn't know, A–Z. What is empty has no key.
func (r *Registry) Encode() string {
	var b bytes.Buffer
	version := r.Version // a registry keeps its version: a start takes an older one over
	if version <= 0 {
		version = ListsVersion
	}
	b.WriteString("{\n  \"version\": ")
	b.WriteString(jsonobj.Text(version))
	b.WriteString(",\n  \"lists\": [")
	n := 0
	for _, l := range r.Lists {
		// A list for some is not named in the file everyone reads: its entry
		// is in its own folder (WriteListsOf).
		if len(l.For()) > 0 {
			continue
		}
		if n++; n > 1 {
			b.WriteByte(',')
		}
		b.WriteString("\n    ")
		b.Write(l.encode())
	}
	if n > 0 {
		b.WriteString("\n  ")
	}
	b.WriteByte(']')
	if len(r.Pinned) > 0 {
		b.WriteString(",\n  \"pinned\": ")
		b.WriteString(strings.ReplaceAll(jsonobj.Text(r.Pinned), ",", ", "))
	} else if r.PinsSet {
		b.WriteString(",\n  \"pinned\": []")
	}
	if len(r.Mine) > 0 {
		b.WriteString(",\n  \"mine\": ")
		b.Write(textObject(ListSettings, r.Mine, nil))
	}
	if len(r.All) > 0 {
		b.WriteString(",\n  \"all\": ")
		b.Write(textObject(ListSettings, r.All, nil))
	}
	if len(r.Order) > 0 {
		b.WriteString(",\n  \"order\": ")
		b.WriteString(jsonobj.Text(r.Order))
	} else if r.OrderSet {
		b.WriteString(",\n  \"order\": []")
	}
	for _, k := range slices.Sorted(maps.Keys(r.Extra)) {
		b.WriteString(",\n  ")
		b.WriteString(jsonobj.Text(k))
		b.WriteString(": ")
		b.Write(bytes.TrimSpace(r.Extra[k]))
	}
	b.WriteString("\n}\n")
	return b.String()
}

// encode: a list's entry, on one line.
func (l *List) encode() []byte { return textObject(ListSettings, l.Settings, l.Extra, "name", l.Name) }

// withListFiles adds to the shared registry r the lists for some, each
// from its folder's list.json (what one left there when it went is no
// list), A–Z after the others.
func (s *Store) withListFiles(r *Registry) *Registry {
	entries, _ := os.ReadDir(filepath.Join(s.Dir(""), ListsDirName))
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		l := listFile(filepath.Join(s.Dir(""), ListsDirName, e.Name()))
		if l == nil || l.Gone() || l.Name != e.Name() || len(l.For()) == 0 {
			continue
		}
		// The registry has an entry of that name too (a merge, a file edited
		// by hand): the folder's file is what the reads of its tasks go by,
		// so the list is for whom it says: never a list everyone is shown
		// whose tasks only some see.
		if have := r.Find(l.Name); have != nil {
			if len(have.For()) == 0 {
				have.SetFor(l.For())
			}
			continue
		}
		r.Lists = append(r.Lists, l)
	}
	return r
}

// writeListFiles writes the entries of the shared registry's lists for some
// into their folders, and mends the folders whose list.json no longer says
// what r does: a list that is for everyone again loses the file (its entry
// is in lists.json); one that is gone (deleted, renamed) keeps it, marked
// gone, so the folder's history stays its members'. Returns the paths
// written and removed.
func (s *Store) writeListFiles(r *Registry) ([]string, error) {
	var paths []string
	write := func(dir string, l *List) error {
		path, text := filepath.Join(dir, ListFileName), string(l.encode())+"\n"
		if raw, err := os.ReadFile(path); err == nil && string(raw) == text {
			return nil
		}
		if err := os.MkdirAll(dir, 0o777); err != nil {
			return err
		}
		if err := atomicfile.Write(path, text); err != nil {
			return err
		}
		paths = append(paths, path)
		return nil
	}
	base := filepath.Join(s.Dir(""), ListsDirName)
	for _, l := range r.Lists {
		if len(l.For()) > 0 && listDirRE.MatchString(l.Name) {
			if err := write(filepath.Join(base, l.Name), l); err != nil {
				return paths, err
			}
		}
	}
	entries, _ := os.ReadDir(base)
	for _, e := range entries {
		dir := filepath.Join(base, e.Name())
		was := listFile(dir)
		if !e.IsDir() || was == nil || was.Gone() {
			continue
		}
		switch now := r.Find(e.Name()); {
		case now != nil && len(now.For()) > 0:
		case now != nil: // for everyone again
			if os.Remove(filepath.Join(dir, ListFileName)) == nil {
				paths = append(paths, filepath.Join(dir, ListFileName))
			}
		default: // gone: the folder's history stays its members'
			gone := &List{Name: was.Name, Settings: map[string]string{}, Extra: map[string]json.RawMessage{"gone": json.RawMessage("true")}}
			gone.SetFor(was.For())
			if err := write(dir, gone); err != nil {
				return paths, err
			}
		}
	}
	for _, p := range paths {
		changed(p)
	}
	return paths, nil
}

// ListsPath: owner's registry.
func (s *Store) ListsPath(owner string) string { return filepath.Join(s.Dir(owner), ListsName) }

// own marks the registry's lists as owner's.
func (r *Registry) own(owner string) *Registry {
	for _, l := range r.Lists {
		l.Owner = owner
	}
	return r
}

// ReadLists: owner's registry, for reading: an empty one (version 0) when
// there is no file or it can't be read.
func (s *Store) ReadLists(owner string) *Registry {
	r := &Registry{}
	if raw, err := os.ReadFile(s.ListsPath(owner)); err == nil {
		if parsed, ok := ParseRegistry(raw); ok {
			r = parsed
		}
	}
	if owner == "" {
		s.withListFiles(r)
	}
	return r.own(owner)
}

// ReadListsStrict: owner's registry, for a write: a file that is there but
// unusable is ErrCorrupt, never an empty registry (a write would then drop
// every list).
func (s *Store) ReadListsStrict(owner string) (*Registry, error) {
	raw, err := os.ReadFile(s.ListsPath(owner))
	r := &Registry{}
	switch {
	case errors.Is(err, os.ErrNotExist):
	case err != nil:
		return nil, ErrCorrupt
	default:
		var ok bool
		if r, ok = ParseRegistry(raw); !ok {
			return nil, ErrCorrupt
		}
	}
	if owner == "" {
		s.withListFiles(r)
	}
	return r.own(owner), nil
}

// WriteListsLocked writes owner's registry for user (who must be allowed
// to write, and to write owner's files: the shared ones or their own);
// returns the paths of the files that changed, for the commit.
func (s *Store) WriteListsLocked(user asker.Asker, owner string, r *Registry) ([]string, error) {
	if err := s.MayWrite(user); err != nil {
		return nil, err
	}
	if !slices.Contains(s.Owners(user), owner) {
		return nil, errors.New("tasks: the lists of " + owner + " in a write by " + user.Name)
	}
	return s.WriteListsOf(owner, r)
}

// WriteListsOf writes owner's registry, whoever asks: for what Zone does
// over every owner's files (a migration at start). The shared one's lists
// for some go into their folders (writeListFiles), before the registry: a
// list narrowed is hidden before it leaves the file everyone reads. Returns
// the paths of the files that changed.
func (s *Store) WriteListsOf(owner string, r *Registry) ([]string, error) {
	s.Hold()
	var paths []string
	if owner == "" {
		var err error
		if paths, err = s.writeListFiles(r); err != nil {
			return paths, err
		}
	}
	path, text := s.ListsPath(owner), r.Encode()
	if raw, err := os.ReadFile(path); err == nil && string(raw) == text {
		return paths, nil
	}
	if err := atomicfile.Write(path, text); err != nil {
		return paths, err
	}
	changed(path)
	return append(paths, path), nil
}
