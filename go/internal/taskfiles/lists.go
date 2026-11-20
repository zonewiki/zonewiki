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
	"zonewiki/internal/asker"
	"zonewiki/internal/atomicfile"
	"zonewiki/internal/jsonobj"
)

// The lists: lists.json beside the task files, the shared lists in _tasks/,
// a user's private ones in private/<user>/_tasks/, a group's in
// groups/<group>/_tasks/. A list is a name and its
// settings; a task is in one by naming it (Task.List), and a task's file
// says which registry that name is in. A registry also holds the pins
// (pinned: a user's own in their file, the wiki's in the shared one), the
// settings and the order of My tasks (mine, order) and, the shared one, the
// settings of All tasks.

// ListsName: the registry's file in a tasks folder.
const ListsName = "lists.json"

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

// Find: the list named name (as the file has it), nil when there is none.
func (r *Registry) Find(name string) *List {
	for _, l := range r.Lists {
		if l.Name == name {
			return l
		}
	}
	return nil
}

// registryKeys: the keys Zone reads; any other is kept as it is. "version"
// (until 2026-11-20 the registry's format, which .zone-format says now) is
// no longer read or written.
var registryKeys = map[string]bool{"version": true, "lists": true, "pinned": true, "mine": true, "all": true, "order": true}

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

// Encode: the registry's text: each list on a line of its own (so a list is added or changed by hand in one line, and a commit
// shows the list that changed), the pins, the settings of All tasks, and
// the keys Zone doesn't know, A–Z. What is empty has no key.
func (r *Registry) Encode() string {
	var b bytes.Buffer
	b.WriteString("{\n  \"lists\": [")
	n := 0
	for _, l := range r.Lists {
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

// ListsPath: owner's registry.
func (s *Store) ListsPath(owner string) string { return filepath.Join(s.Dir(owner), ListsName) }

// own marks the registry's lists as owner's.
func (r *Registry) own(owner string) *Registry {
	for _, l := range r.Lists {
		l.Owner = owner
	}
	return r
}

// ReadLists: owner's registry, for reading: an empty one when
// there is no file or it can't be read.
func (s *Store) ReadLists(owner string) *Registry {
	r := &Registry{}
	if raw, err := os.ReadFile(s.ListsPath(owner)); err == nil {
		if parsed, ok := ParseRegistry(raw); ok {
			r = parsed
		}
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
// over every owner's files (a start's #general). Returns the paths of the
// files that changed.
func (s *Store) WriteListsOf(owner string, r *Registry) ([]string, error) {
	s.Hold()
	path, text := s.ListsPath(owner), r.Encode()
	if raw, err := os.ReadFile(path); err == nil && string(raw) == text {
		return nil, nil
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o777); err != nil {
		return nil, err
	}
	if err := atomicfile.Write(path, text); err != nil {
		return nil, err
	}
	changed(path)
	return []string{path}, nil
}
