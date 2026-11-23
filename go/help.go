// Zonewiki's manual (docs/manual/) in the browser: /help, a page of it at
// /help/<name>, the spec beside it at /help/spec/<name> (the manual links
// there for the full rules), and the screenshots both show. The same
// rendering writes them out as static HTML for a website (zonewiki manual
// DIR). The files are the ones zone_help serves (manual.go); a missing
// docs folder makes /help a page that says so.
package main

import (
	"encoding/json"
	"fmt"
	"html/template"
	"io"
	"io/fs"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

func helpRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /help", helpPage)
	mux.HandleFunc("GET /help/{name...}", helpPage)
}

// helpDoc is one document of the help: a page of the manual, the manual's
// contents (README.md, name ""), a spec file, or the spec's list.
type helpDoc struct {
	name string // "", "tasks", "spec", "spec/tasks"
	path string // the file; "" for the spec's list, which is made
}

var helpNameRE = regexp.MustCompile(`^[a-z0-9][a-z0-9-]*$`)

// manualPages: the manual's pages in reading order: the contents
// (README.md) first, then the pages in the order its links name them, then
// any it doesn't link, by name.
func manualPages() []helpDoc {
	root := docsRoot()
	if root == nil {
		return nil
	}
	dir := "docs/manual"
	readme := path.Join(dir, "README.md")
	if !fsExists(root, readme) {
		return nil
	}
	out := []helpDoc{{"", readme}}
	seen := map[string]bool{}
	text, _ := readDoc(readme)
	for _, m := range manualLinkRE.FindAllStringSubmatch(text, -1) {
		name := m[1]
		if p := path.Join(dir, name+".md"); !seen[name] && fsExists(root, p) {
			seen[name] = true
			out = append(out, helpDoc{name, p})
		}
	}
	paths, _ := fs.Glob(root, path.Join(dir, "*.md"))
	sort.Strings(paths)
	for _, p := range paths {
		name := strings.TrimSuffix(filepath.Base(p), ".md")
		if name != "README" && !seen[name] && helpNameRE.MatchString(name) {
			out = append(out, helpDoc{name, p})
		}
	}
	return out
}

var manualLinkRE = regexp.MustCompile(`\]\(([a-z0-9][a-z0-9-]*)\.md(?:#[^)]*)?\)`)

// specDocs: the spec's files, by name.
func specDocs() []helpDoc {
	root := docsRoot()
	if root == nil {
		return nil
	}
	paths, _ := fs.Glob(root, "docs/spec/*.md")
	sort.Strings(paths)
	var out []helpDoc
	for _, p := range paths {
		if name := strings.TrimSuffix(filepath.Base(p), ".md"); helpNameRE.MatchString(name) {
			out = append(out, helpDoc{"spec/" + name, p})
		}
	}
	return out
}

// findHelpDoc: the document a /help path names.
func findHelpDoc(name string) (helpDoc, bool) {
	if name == "spec" {
		return helpDoc{name: "spec"}, len(specDocs()) > 0
	}
	docs := manualPages()
	if strings.HasPrefix(name, "spec/") {
		docs = specDocs()
	}
	for _, d := range docs {
		if d.name == name {
			return d, true
		}
	}
	return helpDoc{}, false
}

// helpText: a document's markdown; the spec's list is made from the spec's
// titles.
func helpText(d helpDoc) (string, error) {
	if d.path != "" {
		return readDoc(d.path)
	}
	var b strings.Builder
	b.WriteString("# Zonewiki's spec\n\nHow each feature of Zonewiki behaves, in detail, written for whoever builds Zonewiki.\n" +
		"The [manual](../manual/README.md) says how to use it.\n\n")
	for _, s := range specDocs() {
		text, err := readDoc(s.path)
		if err != nil {
			continue
		}
		name := strings.TrimPrefix(s.name, "spec/")
		fmt.Fprintf(&b, "- [%s](%s.md)\n", manualTitle(name, text), name)
	}
	return b.String(), nil
}

// helpFolder: the docs folder a document's relative links start from.
func helpFolder(d helpDoc) string {
	if d.name == "spec" || strings.HasPrefix(d.name, "spec/") {
		return "spec"
	}
	return "manual"
}

var docLinkRE = regexp.MustCompile(`(href|src)="([^"]*)"`)

// docLinks rewrites a rendered document's relative links, written for the
// files in docs/ (tasks.md#views, ../spec/tasks.md, ../screenshots/page.png),
// to where the help has them: to(target), with target a help path ("",
// "tasks", "spec", "spec/tasks", "screenshots/page.png") and its #fragment.
// Other links stay as they are.
func docLinks(html, folder string, to func(target, fragment string) string) string {
	return docLinkRE.ReplaceAllStringFunc(html, func(attr string) string {
		m := docLinkRE.FindStringSubmatch(attr)
		link := m[2]
		if link == "" || strings.HasPrefix(link, "#") || strings.HasPrefix(link, "/") || strings.Contains(strings.SplitN(link, "#", 2)[0], ":") {
			return attr
		}
		target, fragment, _ := strings.Cut(link, "#")
		p := path.Join(folder, target) // manual/tasks.md, spec, screenshots/page.png
		var name string
		switch {
		case p == "manual" || p == "manual/README.md":
			name = ""
		case p == "spec" || p == "spec/README.md":
			name = "spec"
		case strings.HasPrefix(p, "manual/") && strings.HasSuffix(p, ".md"):
			name = strings.TrimSuffix(strings.TrimPrefix(p, "manual/"), ".md")
		case strings.HasPrefix(p, "spec/") && strings.HasSuffix(p, ".md"):
			name = strings.TrimSuffix(p, ".md")
		case strings.HasPrefix(p, "screenshots/") && strings.HasSuffix(p, ".png"):
			name = p
		default:
			return attr
		}
		if strings.Contains(strings.TrimPrefix(strings.TrimPrefix(name, "spec/"), "screenshots/"), "/") {
			return attr
		}
		return m[1] + `="` + template.HTMLEscapeString(to(name, fragment)) + `"`
	})
}

// renderHelp: a document's HTML, its links pointed by to (docLinks), its
// ## and ### headings in toc (nil: not wanted).
func renderHelp(d helpDoc, text, host string, to func(target, fragment string) string, toc *[]tocEntry) string {
	html := markdown(text, &Env{Host: host, HeadingIDs: headingAsID, TOC: toc}, false)
	return docLinks(html, helpFolder(d), to)
}

// helpHref: where a help path is in the browser.
func helpHref(target, fragment string) string {
	href := "/help"
	if target != "" {
		href += "/" + target
	}
	if fragment != "" {
		href += "#" + fragment
	}
	return href
}

// helpView: a help page (help.html).
type helpView struct {
	view
	BreadcrumbHTML template.HTML
	RailTocHTML    template.HTML
	ArticleHTML    template.HTML
	Missing        bool   // Zonewiki's docs aren't installed with this server
	Prev, Next     *crumb // the manual's pages before and after, in reading order
}

// helpPage: GET /help and /help/<name>: the manual, the spec, a screenshot.
func helpPage(w http.ResponseWriter, r *http.Request) {
	rq := newReq(r)
	name := strings.Trim(r.PathValue("name"), "/")
	if shot, ok := strings.CutPrefix(name, "screenshots/"); ok {
		if root := docsRoot(); root != nil && strings.HasSuffix(shot, ".png") && !strings.Contains(shot, "/") {
			serveFS(w, r, root, "docs/screenshots/"+shot, "")
			return
		}
		http.NotFound(w, r)
		return
	}
	if len(manualPages()) == 0 && name == "" {
		v := &helpView{Missing: true}
		v.BreadcrumbHTML = breadcrumb(rq, []crumb{{"Help", ""}}, "", false, "", "")
		page(w, rq, http.StatusOK, "help.html", "Help", shell{}, v)
		return
	}
	d, ok := findHelpDoc(name)
	if !ok {
		http.NotFound(w, r)
		return
	}
	text, err := helpText(d)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	title := manualTitle(name, text)
	var toc []tocEntry
	article := renderHelp(d, text, r.Host, helpHref, &toc)
	crumbs := []crumb{{"Help", "/help"}}
	if strings.HasPrefix(d.name, "spec/") {
		crumbs = append(crumbs, crumb{"Spec", "/help/spec"})
	}
	if d.name == "" {
		crumbs = []crumb{{"Help", ""}}
	} else {
		crumbs = append(crumbs, crumb{title, ""})
	}
	v := &helpView{BreadcrumbHTML: breadcrumb(rq, crumbs, "", false, "", ""),
		RailTocHTML: railToc(toc), ArticleHTML: template.HTML(article)}
	v.Prev, v.Next = manualNeighbours(d.name, func(n string) string { return helpHref(n, "") })
	page(w, rq, http.StatusOK, "help.html", title, shell{wide: true}, v)
}

// manualNeighbours: the manual's pages before and after name in reading
// order, as links made by href (nil at either end, and for what isn't a
// page of the manual).
func manualNeighbours(name string, href func(string) string) (prev, next *crumb) {
	pages := manualPages()
	for i, p := range pages {
		if p.name != name {
			continue
		}
		link := func(d helpDoc) *crumb {
			text, _ := readDoc(d.path)
			label := manualTitle(d.name, text)
			if d.name == "" {
				label = "Contents"
			}
			return &crumb{label, href(d.name)}
		}
		if i > 0 {
			prev = link(pages[i-1])
		}
		if i+1 < len(pages) {
			next = link(pages[i+1])
		}
	}
	return prev, next
}

// The static help (zonewiki manual DIR): each document as an HTML fragment,
// for a website to put in its own page around it.
const manualUsage = `Usage: zonewiki manual [--docs FOLDER] DIR

Writes Zonewiki's manual and spec to DIR as HTML fragments, for a website:
DIR/index.html (the manual's contents), DIR/<page>.html, DIR/spec/index.html,
DIR/spec/<name>.html, the screenshots in DIR/screenshots/, and DIR/pages.json,
which lists every fragment with its title, the manual's pages in reading
order first. Links between them are relative. A fragment is the document's
body only: no <html>, no styles.

  --docs FOLDER  the folder of README.md and docs/ (default: found from the binary)`

// manualFile: a help path's file in the static help.
func manualFile(target string) string {
	switch {
	case target == "":
		return "index.html"
	case target == "spec":
		return "spec/index.html"
	case strings.HasPrefix(target, "screenshots/"):
		return target
	}
	return target + ".html"
}

// manualMain: zonewiki manual DIR.
func manualMain(args []string, stdout, stderr io.Writer) int {
	var out string
	for i := 0; i < len(args); i++ {
		switch a := strings.TrimPrefix(args[i], "-"); {
		case isHelp(args[i]):
			fmt.Fprintln(stdout, manualUsage)
			return 0
		case a == "-docs" || a == "docs":
			if i+1 >= len(args) {
				fmt.Fprintln(stderr, manualUsage)
				return 2
			}
			i++
			docsDir = args[i]
		case strings.HasPrefix(args[i], "-") || out != "":
			fmt.Fprintln(stderr, manualUsage)
			return 2
		default:
			out = args[i]
		}
	}
	if out == "" {
		fmt.Fprintln(stderr, manualUsage)
		return 2
	}
	if docsDir == "" {
		publicDir = findPublic()
	}
	pages := manualPages()
	if len(pages) == 0 {
		fmt.Fprintln(stderr, "zonewiki manual: no docs/manual/README.md found; name the folder with --docs")
		return 1
	}
	type entry struct {
		Name   string `json:"name"`  // the help path: "", "tasks", "spec", "spec/tasks"
		File   string `json:"file"`  // the fragment, relative to DIR
		Title  string `json:"title"` // the document's first heading
		Manual bool   `json:"manual"`
	}
	var list []entry
	docs := append(append(pages, helpDoc{name: "spec"}), specDocs()...)
	for _, d := range docs {
		text, err := helpText(d)
		if err != nil {
			fmt.Fprintln(stderr, "zonewiki manual:", err)
			return 1
		}
		file := manualFile(d.name)
		from := path.Dir(file)
		html := renderHelp(d, text, "", func(target, fragment string) string {
			rel, _ := filepath.Rel(from, manualFile(target))
			if fragment != "" {
				rel += "#" + fragment
			}
			return filepath.ToSlash(rel)
		}, nil)
		if err := writeOut(filepath.Join(out, file), []byte(html)); err != nil {
			fmt.Fprintln(stderr, "zonewiki manual:", err)
			return 1
		}
		list = append(list, entry{d.name, file, manualTitle(d.name, text), helpFolder(d) == "manual"})
	}
	shots, _ := fs.Glob(docsRoot(), "docs/screenshots/*.png")
	for _, s := range shots {
		data, err := fs.ReadFile(docsRoot(), s)
		if err == nil {
			err = writeOut(filepath.Join(out, "screenshots", filepath.Base(s)), data)
		}
		if err != nil {
			fmt.Fprintln(stderr, "zonewiki manual:", err)
			return 1
		}
	}
	data, _ := json.MarshalIndent(list, "", "  ")
	if err := writeOut(filepath.Join(out, "pages.json"), append(data, '\n')); err != nil {
		fmt.Fprintln(stderr, "zonewiki manual:", err)
		return 1
	}
	fmt.Fprintf(stdout, "Wrote %d documents to %s\n", len(list), out)
	return 0
}

func writeOut(p string, data []byte) error {
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		return err
	}
	return os.WriteFile(p, data, 0o644)
}
