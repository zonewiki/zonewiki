package main

import (
	"strings"
	"testing"
)

// The boxes the server ticks are the boxes the page shows: the Nth tick
// changes the line of the Nth box rendered, whatever "- [ ]" lines the
// render shows no box for (each case's "hidden" one). The lines' count
// (mdtext.TaskItemLines) took those for boxes, so a tick went to the line
// before the one ticked.
func TestCheckboxesAreThoseRendered(t *testing.T) {
	testWiki(t, nil)
	hidden := map[string]string{
		"comment":        "<!--\n- [ ] hidden\n-->\n",
		"raw html":       "<div>\n- [ ] hidden\n</div>\n",
		"indented code":  "Text.\n\n    - [ ] hidden\n",
		"fence in quote": "> ```\n> - [ ] hidden\n> ```\n",
		"code in a list": "- a\n\n      - [ ] hidden\n",
		"math":           "$$\n- [ ] hidden\n$$\n",
		"fenced code":    "```\n- [ ] hidden\n```\n",
		"frontmatter":    "",
	}
	for name, h := range hidden {
		content := "# CB\n\n- [ ] first\n\n" + h + "\n> - [x] second\n\n1. [ ] third [x] trailing\n   - [ ] fourth\n"
		if name == "frontmatter" {
			content = "---\ntags: [a]\ntldr: - [ ] hidden\n---\n" + content
		}
		html := renderMd(&Req{}, content, RenderOpts{})
		items := taskItems(content, true)
		if n := strings.Count(html, `class="task-list-item-checkbox"`); n != 4 || len(items) != 4 {
			t.Fatalf("%s: %d boxes rendered, %d found, want 4", name, n, len(items))
		}
		before := strings.Split(content, "\n")
		for i, label := range []string{"first", "second", "third", "fourth"} {
			after := strings.Split(toggleNthCheckbox(content, i, true, now()), "\n")
			var changed []string
			for j := range before {
				if before[j] != after[j] {
					changed = append(changed, before[j])
				}
			}
			if len(changed) != 1 || !strings.Contains(changed[0], label) {
				t.Errorf("%s: box %d changed %q", name, i, changed)
			}
		}
		if toggleNthCheckbox(content, 4, true, now()) != content {
			t.Errorf("%s: a box past the last changed the text", name)
		}
	}
}

// A task's notes have no frontmatter: a leading --- block is text, and
// its box counts. Lines ending otherwise than in \n count as SplitLines
// counts them.
func TestNotesCheckboxes(t *testing.T) {
	notes := "---\n- [ ] one\n---\n<!-- - [ ] not -->\n- [ ] two\r\n- [ ] three"
	if n := len(taskItems(notes, false)); n != 3 {
		t.Fatalf("%d boxes, want 3", n)
	}
	got := toggleNthCheckbox(notes, 2, false, now())
	if !strings.Contains(got, "- [x] three <!--d:") || !strings.Contains(got, "- [ ] two\r\n") {
		t.Errorf("%q", got)
	}
	if got := toggleNthCheckbox(notes, 1, false, now()); !strings.HasPrefix(got[strings.Index(got, "- [x] two"):], "- [x] two <!--d:") {
		t.Errorf("%q", got)
	}
}
