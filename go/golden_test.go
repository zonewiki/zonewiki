package main

import (
	"compress/gzip"
	"encoding/json"
	"fmt"
	"os"
	"reflect"
	"sort"
	"testing"
	"time"
)

// The answers of the Python Zone (until 2026-10) on every string in Zone's
// tests then and some edge cases, recorded before it was removed: the text
// functions still give them. A deliberate change to one of them changes its
// cases here too: ZONE_GOLDEN_UPDATE=1 go test -run TestGolden writes what
// they give now (check the mismatches it logs first).
func TestGolden(t *testing.T) {
	f, err := os.Open("testdata/golden.json.gz")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	zr, err := gzip.NewReader(f)
	if err != nil {
		t.Fatal(err)
	}
	var golden struct {
		Now   string                       `json:"now"`
		Today string                       `json:"today"`
		Cases []map[string]json.RawMessage `json:"cases"`
	}
	if err := json.NewDecoder(zr).Decode(&golden); err != nil {
		t.Fatal(err)
	}
	at, _ := time.ParseInLocation("2006-01-02T15:04:05", golden.Now, time.Local)
	clock(t, at)
	day, _ := time.ParseInLocation("2006-01-02", golden.Today, time.Local)

	update := os.Getenv("ZONE_GOLDEN_UPDATE") == "1"
	failures := map[string]int{}
	var c map[string]json.RawMessage
	check := func(name, text string, got any, raw json.RawMessage) {
		// Through JSON both, so Go's types compare as Python's.
		gb, _ := json.Marshal(got)
		var g, w any
		_ = json.Unmarshal(gb, &g)
		_ = json.Unmarshal(raw, &w)
		if reflect.DeepEqual(g, w) {
			return
		}
		if failures[name]++; failures[name] <= 3 || update {
			t.Errorf("%s(%q):\n got %s\nwant %s", name, text, gb, raw)
		}
		if update && name != "deck_params" {
			c[name] = gb
		}
	}
	for _, c = range golden.Cases {
		var text string
		_ = json.Unmarshal(c["text"], &text)
		slug := "folder/sub/page"
		sections := splitSections(text)
		check("split_sections", text, sections, c["split_sections"])
		var ranges []any
		for i := 0; i <= len(sections); i++ {
			if s, e, ok := sectionCharRange(text, i); ok {
				ranges = append(ranges, []int{s, e})
			} else {
				ranges = append(ranges, nil)
			}
		}
		check("section_char_range", text, ranges, c["section_char_range"])
		meta, body := parseFrontmatter(text)
		m := map[string]any{}
		if meta.Tags != nil {
			m["tags"] = meta.Tags
		}
		if meta.Tldr != "" {
			m["tldr"] = meta.Tldr
		}
		if meta.Sources != nil {
			m["sources"] = meta.Sources
		}
		check("parse_frontmatter", text, []any{m, body}, c["parse_frontmatter"])
		masked, stash := maskCode(text)
		if stash == nil {
			stash = []string{}
		}
		check("mask_code", text, []any{masked, stash}, c["mask_code"])
		check("page_title", text, pageTitle(text, "some-slug"), c["page_title"])
		check("strip_h1", text, stripH1(text), c["strip_h1"])
		var toggles, notes []string
		for i := range 4 {
			toggles = append(toggles, toggleNthCheckbox(text, i, true))
		}
		for i := range 2 {
			notes = append(notes, toggleNthCheckbox(text, i, false))
		}
		check("toggle", text, toggles, c["toggle"])
		check("toggle_notes", text, notes, c["toggle_notes"])
		check("to_slug", text, toSlug(text), c["to_slug"])
		check("section_anchor", text, sectionAnchor(text), c["section_anchor"])
		check("plain_task_title", text, plainTaskTitle(text), c["plain_task_title"])
		check("slug_display_name", text, slugDisplayName(text), c["slug_display_name"])
		targets := []string{}
		for s := range pageLinkTargets(text, slug) {
			targets = append(targets, s)
		}
		sort.Strings(targets)
		check("page_link_targets", text, targets, c["page_link_targets"])
		check("md_link_misses", text, mdLinkMisses(text, slug, map[string]bool{"folder/a": true, "x": true}), c["md_link_misses"])
		check("rewrite_page_links", text, rewritePageLinks(text, "folder/a", "other/b", slug, ""), c["rewrite_page_links"])
		check("rewrite_to_section", text, rewritePageLinks(text, "x", "y", slug, "New Heading"), c["rewrite_to_section"])
		check("rebase_links", text, rebaseLinks(text, slug, "moved/page", map[string]string{"folder/a": "z/a"}), c["rebase_links"])
		check("add_frontmatter_tags", text, addFrontmatterTags(text, []string{"new", "A"}), c["add_frontmatter_tags"])
		section, tags := noteAsSection(text, "Heading")
		if tags == nil {
			tags = []string{}
		}
		check("note_as_section", text, []any{section, tags}, c["note_as_section"])
		check("promoted_slug", text, promotedSlug("Folder X", text, "2026-01-02"), c["promoted_slug"])
		check("page_wikilink", text, pageWikilink("s", text), c["page_wikilink"])
		check("is_scratch", text, isQuicknote(text), c["is_scratch"]) // the golden data's name for it
		check("clean_tags", text, cleanTags([]string{text, "#" + text}), c["clean_tags"])
		check("clean_list", text, nilIfEmpty(cleanList(text)), c["clean_list"])
		check("parse_date_word", text, nilIfEmpty(parseDateWord(text, day)), c["parse_date_word"])
		params := [][]string{}
		for _, m := range tasksRE.FindAllStringSubmatch(text, -1) {
			p := parseTasksParams(embedArgs(m))
			for _, k := range p.keys {
				params = append(params, []string{k, p.m[k]})
			}
		}
		var want [][][]string
		_ = json.Unmarshal(c["deck_params"], &want)
		var flat [][]string
		for _, w := range want {
			flat = append(flat, w...)
		}
		if flat == nil {
			flat = [][]string{}
		}
		b, _ := json.Marshal(flat)
		check("deck_params", text, params, b)
	}
	if len(failures) > 0 {
		t.Log(fmt.Sprint("mismatches by function: ", failures))
	}
	if update && len(failures) > 0 {
		out, err := os.Create("testdata/golden.json.gz")
		if err != nil {
			t.Fatal(err)
		}
		zw := gzip.NewWriter(out)
		if err := json.NewEncoder(zw).Encode(golden); err != nil {
			t.Fatal(err)
		}
		if err := zw.Close(); err != nil {
			t.Fatal(err)
		}
		if err := out.Close(); err != nil {
			t.Fatal(err)
		}
	}
}

func nilIfEmpty(s string) any {
	if s == "" {
		return nil
	}
	return s
}
