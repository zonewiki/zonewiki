package taskfiles

import (
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
	"zonewiki/internal/asker"
	"zonewiki/internal/taskmodel"
)

const registryText = `{
  "lists": [
    {"name": "shop", "view": "focus", "limit": "8", "keep": "1", "labels": "Catalogue,Shopping"},
    {"name": "task"}
  ],
  "pinned": ["task", "shop"],
  "mine": {"view": "focus", "limit": "5"},
  "all": {"view": "open"}
}
`

// A registry is written as it was read: the lists a line each, their
// settings in one order.
func TestRegistryRoundTrips(t *testing.T) {
	r, ok := ParseRegistry([]byte(registryText))
	if !ok {
		t.Fatal("not read")
	}
	if got := r.Encode(); got != registryText {
		t.Errorf("written as\n%s\nwant\n%s", got, registryText)
	}
	shop := r.Find("shop")
	if shop == nil || shop.Settings["limit"] != "8" || shop.Settings["view"] != "focus" || r.Find("nope") != nil {
		t.Errorf("shop = %+v", shop)
	}
	if !reflect.DeepEqual(r.Pinned, []string{"task", "shop"}) || r.All["view"] != "open" || r.Mine["view"] != "focus" || r.Mine["limit"] != "5" {
		t.Errorf("pins %v, all %v, mine %v", r.Pinned, r.All, r.Mine)
	}
}

// A registry has no version: .zone-format says the format of the wiki's
// files. One written before 2026-11-20 says one, which is dropped, not
// kept as a key Zone doesn't know. home, a list's being left out of My
// tasks, is a setting like the others.
func TestRegistryHasNoVersion(t *testing.T) {
	if got := (&Registry{}).Encode(); strings.Contains(got, "version") {
		t.Errorf("a new registry:\n%s", got)
	}
	r, _ := ParseRegistry([]byte(`{"version": 2, "lists": [{"home": "off", "name": "someday", "limit": 3}]}`))
	if l := r.Find("someday"); l == nil || l.Settings["home"] != "off" || len(l.Extra) != 0 || len(r.Extra) != 0 {
		t.Fatalf("someday = %+v, extra %v", l, r.Extra)
	}
	if got := r.Encode(); got != "{\n  \"lists\": [\n    {\"name\": \"someday\", \"limit\": \"3\", \"home\": \"off\"}\n  ]\n}\n" {
		t.Errorf("written as\n%s", got)
	}
}

// "mine" was true, until 2026-11, for a user whose Pinned showed only their
// own tasks: that reads as no settings of My tasks, and isn't written back.
func TestRegistryMineWasAFlag(t *testing.T) {
	r, ok := ParseRegistry([]byte(`{"pinned": ["a"], "mine": true}`))
	if !ok || len(r.Mine) != 0 {
		t.Fatalf("read as %v (ok %v)", r.Mine, ok)
	}
	if got := r.Encode(); strings.Contains(got, "mine") {
		t.Errorf("written with the key:\n%s", got)
	}
}

// A registry from before the first pinned list took the new tasks names
// that list in "default", whatever the order of its pins: it is read as
// the first pin, and written without the key.
func TestRegistryDefaultIsTheFirstPin(t *testing.T) {
	for text, want := range map[string][]string{
		`{"pinned": ["a", "b"], "default": "b"}`: {"b", "a"},
		`{"pinned": ["a", "b"], "default": "a"}`: {"a", "b"},
		`{"pinned": ["a"], "default": "C"}`:      {"c", "a"},
		`{"default": "b"}`:                       {"b"},
		`{"pinned": ["a", "b"]}`:                 {"a", "b"},
	} {
		r, ok := ParseRegistry([]byte(text))
		if !ok || !reflect.DeepEqual(r.Pinned, want) || !r.PinsSet {
			t.Errorf("%s: pins %v (set %v), want %v", text, r.Pinned, r.PinsSet, want)
		}
		if got := r.Encode(); strings.Contains(got, "default") {
			t.Errorf("%s: written with the key:\n%s", text, got)
		}
	}
	if r, _ := ParseRegistry([]byte(`{"lists": []}`)); r.PinsSet || len(r.Pinned) != 0 {
		t.Errorf("no pins read as set: %+v", r)
	}
}

// A file edited by hand still reads: values of another kind as text, a
// list without a name left out, a name twice once, unknown keys kept.
func TestRegistryReadLeniently(t *testing.T) {
	r, ok := ParseRegistry([]byte(`{"lists": [
		{"name": " Shop ", "limit": 8, "keep": true, "colour": "red", "view": ["x"]},
		{"limit": "3"}, "junk", {"name": "shop", "view": "open"}, {"name": "b"}],
		"pinned": ["Shop", "shop", 7], "default": 3, "all": "no", "theme": {"dark": true}}`))
	if !ok {
		t.Fatal("not read")
	}
	want := `{
  "lists": [
    {"name": "shop", "limit": "8", "keep": "true", "colour": "red"},
    {"name": "b"}
  ],
  "pinned": ["3", "shop", "7"],
  "theme": {"dark": true}
}
`
	if got := r.Encode(); got != want {
		t.Errorf("written as\n%s\nwant\n%s", got, want)
	}
	for _, bad := range []string{"", "[]", "nope", `"x"`} {
		if _, ok := ParseRegistry([]byte(bad)); ok {
			t.Errorf("%q read as a registry", bad)
		}
	}
}

// The shared lists are in _tasks/lists.json, a user's in their folder;
// each list says whose it is.
func TestListsPerOwner(t *testing.T) {
	f := newStore(t, "alice", "bob")
	if r := f.ReadLists(""); r.PinsSet || len(r.Lists) != 0 {
		t.Errorf("no file: %+v", r)
	}
	f.Lock()
	defer f.Unlock()
	shared := &Registry{Lists: []*List{{Name: "shop"}}}
	own := &Registry{Lists: []*List{{Name: "gifts", Settings: map[string]string{"view": "open"}}}}
	p1, err1 := f.WriteListsLocked(asker.User("alice"), "", shared)
	p2, err2 := f.WriteListsLocked(asker.User("alice"), "alice", own)
	if err1 != nil || err2 != nil {
		t.Fatal(err1, err2)
	}
	if !slices.Equal(p1, []string{filepath.Join(f.root, "_tasks", "lists.json")}) ||
		!slices.Equal(p2, []string{filepath.Join(f.root, "private", "alice", "_tasks", "lists.json")}) {
		t.Errorf("written to %q and %q", p1, p2)
	}
	if l := f.ReadLists("alice").Find("gifts"); l == nil || l.Owner != "alice" || l.Settings["view"] != "open" {
		t.Errorf("alice's gifts: %+v", l)
	}
	if l := f.ReadLists("").Find("shop"); l == nil || l.Owner != "" {
		t.Errorf("the shared shop: %+v", l)
	}
	if f.ReadLists("bob").Find("gifts") != nil {
		t.Error("bob has alice's list")
	}
	// Written again unchanged: no file to commit.
	if p, err := f.WriteListsLocked(asker.User("alice"), "alice", own); len(p) != 0 || err != nil {
		t.Errorf("an unchanged registry written: %q, %v", p, err)
	}
}

// Nobody writes another user's lists, and a viewer writes none.
func TestListsWriteRefused(t *testing.T) {
	f := newStore(t, "alice", "bob")
	f.viewer = "bob"
	f.Lock()
	defer f.Unlock()
	if _, err := f.WriteListsLocked(asker.User("alice"), "bob", &Registry{}); err == nil {
		t.Error("alice wrote bob's lists")
	}
	if _, err := f.WriteListsLocked(asker.User("bob"), "", &Registry{}); err == nil {
		t.Error("a viewer wrote the lists")
	}
	if _, err := os.Stat(f.ListsPath("bob")); !errors.Is(err, os.ErrNotExist) {
		t.Error("a refused write left a file")
	}
}

// An unreadable registry is no empty one for a write, which would then
// drop every list.
func TestListsCorruptRefusesWrite(t *testing.T) {
	f := newStore(t)
	if err := os.MkdirAll(f.Dir(""), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(f.ListsPath(""), []byte("{ half"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := f.ReadListsStrict(""); !errors.Is(err, ErrCorrupt) {
		t.Errorf("read for a write: %v", err)
	}
	if r := f.ReadLists(""); len(r.Lists) != 0 {
		t.Errorf("read: %+v", r)
	}
	if r, err := f.ReadListsStrict("nobody"); err != nil || len(r.Lists) != 0 {
		t.Errorf("no file, read for a write: %+v, %v", r, err)
	}
}

// A task's list is in its file after its tags, and a task without one
// has no such key, so a wiki without lists keeps its files as they were.
func TestTaskListInFile(t *testing.T) {
	f := newStore(t)
	with, without := task("a", "", ""), task("b", "", "")
	with.List = "shop"
	if err := Write(f.OpenPath(""), []*Task{with, without}); err != nil {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile(f.OpenPath(""))
	text := string(raw)
	if strings.Count(text, `"list"`) != 1 || !strings.Contains(text, "\"tags\": [],\n    \"list\": \"shop\",\n") {
		t.Errorf("the file:\n%s", text)
	}
	back := ReadLenient(f.OpenPath(""))
	if back[0].List != "shop" || back[1].List != "" {
		t.Errorf("read back: %q, %q", back[0].List, back[1].List)
	}
	again, _ := taskmodel.Encode(back)
	if again != text {
		t.Error("written differently the second time")
	}
}
