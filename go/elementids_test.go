package main

import (
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"testing"

	"zonewiki/internal/mdtext"
)

// The ids Zonewiki's own elements have, as its templates, scripts and
// styles name them (what a heading's id could be: lower case, letters,
// digits, "-" and "_"), and the globals its scripts read (a heading's id
// is a window.x too). A dynamic tail ({{.Sfx}}, + sfx, ${…}) counts as
// "-1", as a list embedded twice makes it.
var (
	tmplIDRE      = regexp.MustCompile(`(?:^|\s)(?:id|for)="((?:\{\{[^}]*\}\}|[^"{])*)"`)
	tmplOrRE      = regexp.MustCompile(`^\{\{or \.[a-z_]+ "([^"]+)"\}\}$`)
	tmplDictIDRE  = regexp.MustCompile(`"ID" "([^"]+)"`)
	jsByIDRE      = regexp.MustCompile("getElementById\\(\\s*['\"`]([^'\"`$]*)(\\$\\{|['\"`]\\s*\\+)?")
	jsIDAttrRE    = regexp.MustCompile(`(?:^|\s)id=\\?["']([^"'\\$]*)(\$\{)?`)
	jsIDPropRE    = regexp.MustCompile("\\.id\\s*=\\s*['\"`]([^'\"`$]*)(\\$\\{|['\"`]\\s*\\+)?")
	jsSelectorRE  = regexp.MustCompile("(?:querySelector(?:All)?|closest|matches)\\(\\s*['\"`]([^'\"`]*)")
	cssHashRE     = regexp.MustCompile(`#([A-Za-z_][\w-]*)`)
	jsWindowRE    = regexp.MustCompile(`\bwindow\.([a-z][a-z0-9_]*)\b`)
	hexColorRE    = regexp.MustCompile(`^(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$`)
	headingFormRE = regexp.MustCompile(`^[\p{Ll}\p{Lo}\p{N}_-]+$`)
)

// zonewikiIDsInSource: every id (or global) found, with where.
func zonewikiIDsInSource(t *testing.T) map[string]string {
	t.Helper()
	found := map[string]string{}
	add := func(id, where string, dynamic bool) {
		if dynamic {
			id = strings.TrimSuffix(id, "-") + "-1"
		}
		if id == "" || id == "user-content-1" {
			return
		}
		if _, ok := found[id]; !ok {
			found[id] = where
		}
	}
	var tmpls []string
	_ = filepath.WalkDir("templates", func(path string, d os.DirEntry, err error) error {
		if err == nil && strings.HasSuffix(path, ".html") {
			tmpls = append(tmpls, path)
		}
		return nil
	})
	for _, path := range tmpls {
		src, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		for _, m := range tmplIDRE.FindAllStringSubmatch(string(src), -1) {
			v := m[1]
			if o := tmplOrRE.FindStringSubmatch(v); o != nil {
				add(o[1], path, false)
				continue
			}
			if v == "{{.ID}}" {
				continue // list_for_select: its callers' "ID"s, below
			}
			lit, rest, dynamic := strings.Cut(v, "{{")
			if dynamic && (lit == "" || rest != ".Sfx}}") {
				t.Errorf("%s: id=%q: a dynamic id this test can't read", path, v)
			}
			add(lit, path, dynamic)
		}
		for _, m := range tmplDictIDRE.FindAllStringSubmatch(string(src), -1) {
			add(m[1], path, false)
		}
	}
	var scripts []string
	for _, pattern := range []string{"../public/*.js", "../public/js/*.js", "../public/*.css"} {
		files, _ := filepath.Glob(pattern)
		scripts = append(scripts, files...)
	}
	if len(tmpls) < 10 || len(scripts) < 10 {
		t.Fatalf("found %d templates and %d scripts", len(tmpls), len(scripts))
	}
	for _, path := range scripts {
		b, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		src := string(b)
		if strings.HasSuffix(path, ".css") {
			for _, m := range cssHashRE.FindAllStringSubmatch(src, -1) {
				if !hexColorRE.MatchString(m[1]) {
					add(m[1], path, false)
				}
			}
			continue
		}
		for _, re := range []*regexp.Regexp{jsByIDRE, jsIDAttrRE, jsIDPropRE} {
			for _, m := range re.FindAllStringSubmatch(src, -1) {
				add(m[1], path, m[2] != "")
			}
		}
		for _, m := range jsSelectorRE.FindAllStringSubmatch(src, -1) {
			for _, h := range cssHashRE.FindAllStringSubmatch(m[1], -1) {
				add(h[1], path, false)
			}
		}
		for _, m := range jsWindowRE.FindAllStringSubmatch(src, -1) {
			if m[1] != "location" && m[1] != "print" { // the window's own: a name never hides them
				add(m[1], path, false)
			}
		}
	}
	return found
}

// No heading of a page takes an id Zonewiki's own elements have, nor a
// global its scripts read (a heading's id is a window.x as well): a
// heading "Modal" ahead of the search modal, or "Mermaid" ahead of the
// diagrams' script, would stand in for it. Each such id is a zonewikiID,
// so a heading of that name gets "user-content-" in front.
func TestZonewikisIDsAreNoHeadingsIDs(t *testing.T) {
	found := zonewikiIDsInSource(t)
	for _, known := range []string{"modal", "search-modal", "task-edit-id-1", "tasks-app-1", "img-lightbox", "pick-menu-1", "mermaid", "editor-preview", "list-new-for"} {
		if _, ok := found[known]; !ok {
			t.Errorf("the scan didn't find %q", known)
		}
	}
	var missing []string
	for id, where := range found {
		if headingFormRE.MatchString(id) && !zonewikiID(id) {
			missing = append(missing, id+" ("+where+")")
		}
	}
	slices.Sort(missing)
	if len(missing) > 0 {
		t.Errorf("not in zonewikiIDs (go/elementids.go), so a heading could take them:\n%s", strings.Join(missing, "\n"))
	}
}

// A heading named as one of Zonewiki's ids gets "user-content-" in front,
// in the page, its side column and a [[Page#Section]] link to it alike; a
// second of that name, one named as a list embedded twice has its ids
// ("Tasks app 2"), and one named as the first already is stay apart.
func TestHeadingsTakeNoneOfZonewikisIDs(t *testing.T) {
	var toc []tocEntry
	src := "# Modal\n## Search modal\n## Modal\n## Tasks app 2\n## User content modal\n### Mermaid\n## Modal window\n"
	html := markdown(src, &Env{HeadingIDs: headingAsID, TOC: &toc}, false)
	expectIn(t, html, `<h1 id="user-content-modal">`, `<h2 id="user-content-search-modal">`,
		`<h2 id="user-content-modal-1">`, `<h2 id="user-content-tasks-app-2">`,
		`<h2 id="user-content-modal-2">`, `<h3 id="user-content-mermaid">`, `<h2 id="modal-window">`)
	if len(toc) != 6 || toc[0].ID != "user-content-search-modal" || toc[4].ID != "user-content-mermaid" {
		t.Errorf("toc: %+v", toc)
	}
	for section, want := range map[string]string{"Modal": "user-content-modal", "Mermaid": "user-content-mermaid",
		"Tasks app 2": "user-content-tasks-app-2", "Modal window": "modal-window", "Über *alles*": "über-alles"} {
		if got := sectionAnchor(section); got != want {
			t.Errorf("sectionAnchor(%q) = %q, want %q", section, got, want)
		}
	}
	// A search preview's data-pid is the page's id.
	expectIn(t, markdown("## Modal\n", &Env{HeadingIDs: headingPID}, false), `data-pid="user-content-modal"`)
	for id := range zonewikiIDSet {
		if mdtext.HeadingID(id) != id {
			t.Errorf("zonewikiIDs has %q, an id no heading could take", id)
		}
	}
}
