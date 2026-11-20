package main

import (
	"compress/gzip"
	"encoding/json"
	"fmt"
	"os"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"
	"zonewiki/internal/mdtext"
	"zonewiki/internal/taskmodel"
	"zonewiki/internal/textsearch"
)

// The text functions' answers on every string in Zone's tests of 2026-10
// and some edge cases, recorded then from the Python Zone before it was
// removed: they still give them. The browser's copies of some of them
// (public/js/mdtext.js) are held to the same file by
// tests/test_text_parity.py, so the two sides can't drift apart. A
// deliberate change to one of them changes its cases here too:
// ZONE_GOLDEN_UPDATE=1 go test -run TestGolden writes what they give now
// (check the mismatches it logs first).
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
		if update {
			c[name] = gb
		}
	}
	for _, c = range golden.Cases {
		var text string
		_ = json.Unmarshal(c["text"], &text)
		slug := "folder/sub/page"
		sections := pageSections(text)
		check("split_sections", text, sections, c["split_sections"])
		var ranges []any
		for i := 0; i <= len(sections); i++ {
			if s, e, ok := sectionRange(text, i); ok {
				ranges = append(ranges, []int{s, e})
			} else {
				ranges = append(ranges, nil)
			}
		}
		check("section_char_range", text, ranges, c["section_char_range"])
		// Whatever the text, each ✎ has its section.
		_, shown := mdtext.ParseFrontmatter(mdtext.StripH1(text))
		if n := strings.Count(markdown(shown, &Env{EditSlug: "p"}, false), `class="section-edit-btn"`); n != len(sections)-1 {
			t.Errorf("%d ✎ for %d sections: %q", n, len(sections)-1, text)
		}
		meta, body := mdtext.ParseFrontmatter(text)
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
		masked, stash := mdtext.MaskCode(text)
		if stash == nil {
			stash = []string{}
		}
		check("mask_code", text, []any{masked, stash}, c["mask_code"])
		check("page_title", text, mdtext.PageTitle(text, "some-slug"), c["page_title"])
		check("strip_h1", text, mdtext.StripH1(text), c["strip_h1"])
		// checkboxes, toggle and toggle_notes are the browser's alone (its
		// count by lines, for a card's subtasks): tests/test_text_parity.py.
		check("to_slug", text, mdtext.ToSlug(text), c["to_slug"])
		check("section_anchor", text, mdtext.SectionAnchor(text), c["section_anchor"])
		// What the browser has too (js/mdtext.js): a heading's id, a
		// wikilink's parts, a query's words.
		check("heading_id", text, mdtext.HeadingID(text), c["heading_id"])
		wlPage, wlSection, wlLabel := mdtext.SplitWikilink(text)
		check("split_wikilink", text, []string{wlPage, wlSection, wlLabel}, c["split_wikilink"])
		check("query_text", text, textsearch.ParseQuery(text).Text, c["query_text"])
		check("plain_task_title", text, mdtext.PlainTaskTitle(text), c["plain_task_title"])
		check("slug_display_name", text, mdtext.SlugDisplayName(text), c["slug_display_name"])
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
		check("add_frontmatter_tags", text, mdtext.AddFrontmatterTags(text, []string{"new", "A"}), c["add_frontmatter_tags"])
		section, tags := mdtext.NoteAsSection(text, "Heading")
		if tags == nil {
			tags = []string{}
		}
		check("note_as_section", text, []any{section, tags}, c["note_as_section"])
		check("promoted_slug", text, mdtext.PromotedSlug("Folder X", text, "2026-01-02"), c["promoted_slug"])
		check("page_wikilink", text, mdtext.PageWikilink("s", text), c["page_wikilink"])
		check("is_note", text, isQuicknote(text), c["is_note"])
		check("clean_tags", text, taskmodel.CleanTags([]string{text, "#" + text}), c["clean_tags"])
		check("clean_list", text, nilIfEmpty(cleanList(text)), c["clean_list"])
		check("parse_date_word", text, nilIfEmpty(parseDateWord(text, day)), c["parse_date_word"])
		params := [][]string{}
		for _, m := range tasksRE.FindAllStringSubmatch(text, -1) {
			p := parseTasksParams(embedArgs(m))
			for _, k := range p.keys {
				params = append(params, []string{k, p.m[k]})
			}
		}
		check("tasks_params", text, params, c["tasks_params"])
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
