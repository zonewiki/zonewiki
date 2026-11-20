package main

import (
	"bytes"
	"compress/gzip"
	"io"
	"io/fs"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"zonefiles"
)

// builtInOnly: the test serves the binary's own copies of the browser code
// and the docs, as a binary with no public/ beside it does.
func builtInOnly(t *testing.T) {
	t.Helper()
	oldPublic, oldDocs := publicDir, docsDir
	publicDir, docsDir = "", ""
	t.Cleanup(func() { publicDir, docsDir = oldPublic, oldDocs })
}

// fetch: GET path from srv as a browser on localhost would, with headers
// (name, value, …); the response and its body.
func fetch(t *testing.T, srv *httptest.Server, path string, headers ...string) (*http.Response, []byte) {
	t.Helper()
	req, _ := http.NewRequest("GET", srv.URL+path, nil)
	req.Host = "localhost:5000" // the Host check's
	for i := 0; i+1 < len(headers); i += 2 {
		req.Header.Set(headers[i], headers[i+1])
	}
	// The transport would ask for gzip itself and unpack it out of sight.
	resp, err := (&http.Client{Transport: &http.Transport{DisableCompression: true}}).Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	return resp, body
}

// The binary holds every file of public/ as it is in the repository: a
// file go:embed would leave out (a name starting with . or _) fails here.
func TestTheBinaryHoldsAllOfPublic(t *testing.T) {
	n := 0
	err := filepath.WalkDir(filepath.Join("..", "public"), func(p string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return err
		}
		rel, _ := filepath.Rel(filepath.Join("..", "public"), p)
		want, _ := os.ReadFile(p)
		got, err := fs.ReadFile(builtInPublic, filepath.ToSlash(rel))
		if err != nil || !bytes.Equal(got, want) {
			t.Errorf("public/%s: not in the binary as it is on disk (%v)", rel, err)
		}
		n++
		return nil
	})
	if err != nil || n < 50 {
		t.Fatalf("%d files, %v", n, err)
	}
}

// Without a public/ folder, Zone serves the binary's copy as it serves the
// folder: each file with its type and time (so a browser asks again only
// when it changed), the vendored scripts and styles gzipped and kept a day,
// nothing that isn't a file in it.
func TestServesTheBuiltInBrowserCode(t *testing.T) {
	testWiki(t, nil)
	builtInOnly(t)
	srv := httptest.NewServer(newHandler())
	defer srv.Close()

	// Each file as the folder serves it: the same type, headers and bytes.
	folder, _ := filepath.Abs(filepath.Join("..", "public"))
	fromFolder := func(path string, headers ...string) (*http.Response, []byte) {
		publicDir = folder
		defer func() { publicDir = "" }()
		return fetch(t, srv, path, headers...)
	}
	for path, typ := range map[string]string{
		"/public/style.css":     "text/css",
		"/public/zonewiki.js":   "text/javascript",
		"/public/favicon.svg":   "image/svg+xml",
		"/public/icon-192.png":  "image/png",
		"/public/manifest.json": "application/json",
		"/public/vendor/source-serif/source-serif-4-latin.woff2": "font/woff2",
		"/public/vendor/mermaid/mermaid.min.js":                  "text/javascript",
		"/public/vendor/katex/LICENSE":                           "text/plain",
	} {
		for _, enc := range []string{"", "gzip"} {
			resp, body := fetch(t, srv, path, "Accept-Encoding", enc)
			disk, diskBody := fromFolder(path, "Accept-Encoding", enc)
			if resp.StatusCode != 200 || !strings.HasPrefix(resp.Header.Get("Content-Type"), typ) || !bytes.Equal(body, diskBody) {
				t.Errorf("%s (%s): %d %q, %d bytes, from the folder %d", path, enc, resp.StatusCode, resp.Header.Get("Content-Type"), len(body), len(diskBody))
			}
			for _, h := range []string{"Content-Type", "Content-Encoding", "Cache-Control", "Vary", "Content-Length", "Accept-Ranges"} {
				if resp.Header.Get(h) != disk.Header.Get(h) {
					t.Errorf("%s (%s): %s %q, from the folder %q", path, enc, h, resp.Header.Get(h), disk.Header.Get(h))
				}
			}
		}
		resp, _ := fetch(t, srv, path)
		modified := resp.Header.Get("Last-Modified")
		if modified == "" {
			t.Errorf("%s: no Last-Modified", path)
		}
		if resp, _ := fetch(t, srv, path, "If-Modified-Since", modified); resp.StatusCode != http.StatusNotModified {
			t.Errorf("%s again: %d, not 304", path, resp.StatusCode)
		}
	}
	if resp, _ := fetch(t, srv, "/public/style.css"); resp.Header.Get("Cache-Control") != "" {
		t.Errorf("style.css kept without asking: %q", resp.Header.Get("Cache-Control"))
	}

	// A vendored library's style, gzipped for a browser that takes it.
	const katex = "/public/vendor/katex/katex.min.css"
	want, _ := os.ReadFile(filepath.Join("..", filepath.FromSlash(katex)))
	resp, body := fetch(t, srv, katex, "Accept-Encoding", "gzip, br")
	if resp.StatusCode != 200 || resp.Header.Get("Content-Encoding") != "gzip" ||
		resp.Header.Get("Content-Type") != "text/css; charset=utf-8" ||
		resp.Header.Get("Cache-Control") != "public, max-age=86400" || resp.Header.Get("Vary") != "Accept-Encoding" {
		t.Fatalf("gzipped: %d %v", resp.StatusCode, resp.Header)
	}
	zr, err := gzip.NewReader(bytes.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	if got, _ := io.ReadAll(zr); !bytes.Equal(got, want) || len(body) >= len(want) {
		t.Errorf("gzipped: %d bytes unpacked to %d, want %d", len(body), len(got), len(want))
	}
	if resp, _ := fetch(t, srv, katex, "If-Modified-Since", resp.Header.Get("Last-Modified"), "Accept-Encoding", "gzip"); resp.StatusCode != http.StatusNotModified {
		t.Errorf("gzipped again: %d, not 304", resp.StatusCode)
	}
	resp, body = fetch(t, srv, katex, "Accept-Encoding", "gzip;q=0")
	if resp.Header.Get("Content-Encoding") != "" || !bytes.Equal(body, want) {
		t.Errorf("gzip;q=0: %q, %d bytes", resp.Header.Get("Content-Encoding"), len(body))
	}

	// What isn't a file of public/: none of it, no folder listed.
	for _, path := range []string{"/public/nope.css", "/public/vendor/", "/public/vendor/katex", "/public/js/",
		"/public/vendor/nope.js"} {
		if resp, body := fetch(t, srv, path, "Accept-Encoding", "gzip"); resp.StatusCode != http.StatusNotFound {
			t.Errorf("%s: %d %.80q", path, resp.StatusCode, body)
		}
	}
	// Nor anything beside it in the binary (zonefiles.FS holds the docs
	// and the licenses too): a name that would climb out is refused before
	// it is looked up.
	for _, name := range []string{"../LICENSE", "vendor/../../README.md", "../docs/spec/security.md", "./style.css",
		"js//page.js", "", "style.css\x00", "vendor/katex/../katex/katex.min.css"} {
		for _, gzip := range []string{"", "gzip"} {
			r := httptest.NewRequest("GET", "/public/x", nil)
			r.SetPathValue("path", name)
			r.Header.Set("Accept-Encoding", gzip)
			w := httptest.NewRecorder()
			servePublic(w, r)
			if w.Code != http.StatusNotFound {
				t.Errorf("%q (%s): %d", name, gzip, w.Code)
			}
		}
	}
}

// A folder on disk wins over the binary's copy, for the browser code and
// for the docs: a checkout serves what it has as it is edited.
func TestAFolderWinsOverTheBuiltInCopy(t *testing.T) {
	testWiki(t, nil)
	builtInOnly(t)
	dir := t.TempDir()
	public := filepath.Join(dir, "public")
	os.MkdirAll(filepath.Join(public, "vendor"), 0o777)
	os.WriteFile(filepath.Join(public, "style.css"), []byte("body{}"), 0o666)
	os.WriteFile(filepath.Join(public, "vendor", "x.js"), []byte("let x = 1;"), 0o666)
	publicDir = public
	srv := httptest.NewServer(newHandler())
	defer srv.Close()
	if resp, body := fetch(t, srv, "/public/style.css"); resp.StatusCode != 200 || string(body) != "body{}" {
		t.Errorf("style.css: %d %q", resp.StatusCode, body)
	}
	if resp, _ := fetch(t, srv, "/public/zonewiki.js"); resp.StatusCode != http.StatusNotFound {
		t.Errorf("zonewiki.js from the binary though the folder has none: %d", resp.StatusCode)
	}
	resp, body := fetch(t, srv, "/public/vendor/x.js", "Accept-Encoding", "gzip")
	if zr, err := gzip.NewReader(bytes.NewReader(body)); err != nil || resp.Header.Get("Content-Encoding") != "gzip" {
		t.Errorf("x.js: %v %v", err, resp.Header)
	} else if got, _ := io.ReadAll(zr); string(got) != "let x = 1;" {
		t.Errorf("x.js: %q", got)
	}

	// The docs beside the folder (a checkout's) are Help's, not the binary's.
	if docsRoot() != zonefiles.FS {
		t.Error("public/ without docs beside it: not the binary's docs")
	}
	os.MkdirAll(filepath.Join(dir, "docs", "spec"), 0o777)
	os.MkdirAll(filepath.Join(dir, "docs", "manual"), 0o777)
	os.WriteFile(filepath.Join(dir, "docs", "manual", "README.md"), []byte("# Our own manual\n"), 0o666)
	if resp, body := fetch(t, srv, "/help"); resp.StatusCode != 200 || !strings.Contains(string(body), "Our own manual") {
		t.Errorf("/help: %d, not the folder's manual", resp.StatusCode)
	}
	// --docs names the folder; one without the docs has none (Help says so).
	publicDir, docsDir = "", dir
	if pages := manualPages(); len(pages) != 1 || pages[0].path != "docs/manual/README.md" {
		t.Errorf("--docs: %v", pages)
	}
	docsDir = filepath.Join(dir, "nowhere")
	if docsRoot() != nil || len(manualPages()) != 0 {
		t.Error("--docs naming a folder without the docs: docs found")
	}
}

// Without a docs folder, Help, its screenshots and zone_help are the
// binary's copies of the manual and the spec, every page of them.
func TestTheBuiltInDocs(t *testing.T) {
	testWiki(t, nil)
	builtInOnly(t)
	srv := httptest.NewServer(newHandler())
	defer srv.Close()
	onDisk, _ := filepath.Glob(filepath.Join("..", "docs", "manual", "*.md"))
	if pages := manualPages(); len(pages) != len(onDisk) || len(pages) < 10 {
		t.Errorf("%d pages of the manual, %d on disk", len(pages), len(onDisk))
	}
	specOnDisk, _ := filepath.Glob(filepath.Join("..", "docs", "spec", "*.md"))
	if spec := specDocs(); len(spec) != len(specOnDisk) {
		t.Errorf("%d spec files, %d on disk", len(spec), len(specOnDisk))
	}
	for path, want := range map[string]string{"/help": "Zone&#39;s manual", "/help/tasks": "<h1", "/help/spec/deployment": "Deployment"} {
		if resp, body := fetch(t, srv, path); resp.StatusCode != 200 || !strings.Contains(string(body), want) {
			t.Errorf("%s: %d", path, resp.StatusCode)
		}
	}
	want, _ := os.ReadFile(filepath.Join("..", "docs", "screenshots", "page.png"))
	resp, body := fetch(t, srv, "/help/screenshots/page.png")
	if resp.StatusCode != 200 || resp.Header.Get("Content-Type") != "image/png" || !bytes.Equal(body, want) {
		t.Errorf("screenshot: %d %q", resp.StatusCode, resp.Header.Get("Content-Type"))
	}
	for _, path := range []string{"/help/screenshots/make_screenshots.py", "/help/screenshots/nope.png"} {
		if resp, _ := fetch(t, srv, path); resp.StatusCode != http.StatusNotFound {
			t.Errorf("%s: %d", path, resp.StatusCode)
		}
	}
	if hits := manualFind("quick-add a task with a deadline", 3); len(hits) == 0 || hits[0].topic != "manual/tasks" {
		t.Errorf("zone_help's search: %v", hits)
	}
	topics := map[string]bool{}
	for _, d := range manualTopics() {
		topics[d.name] = true
	}
	if !topics["readme"] || !topics["manual/self-hosting"] || !topics["deployment"] || topics["testing"] {
		t.Errorf("zone_help's topics: %v", topics)
	}
}

// publicNear takes a folder for Zone's browser code only when it holds
// zonewiki.js: a binary in ~/Downloads on a Mac, where ../public is
// ~/Public, serves its own copy.
func TestFindsOnlyZonesPublic(t *testing.T) {
	home := t.TempDir()
	bin := filepath.Join(home, "Downloads")
	os.MkdirAll(bin, 0o777)
	os.MkdirAll(filepath.Join(home, "public"), 0o777)
	os.WriteFile(filepath.Join(home, "public", "notes.txt"), []byte("mine"), 0o666)
	if got := publicNear(bin); got != "" {
		t.Errorf("took %s", got)
	}
	os.WriteFile(filepath.Join(home, "public", "zonewiki.js"), nil, 0o666)
	if got := publicNear(bin); got != filepath.Join(bin, "..", "public") {
		t.Errorf("a checkout's public/: %q", got)
	}
	os.MkdirAll(filepath.Join(bin, "public"), 0o777)
	os.WriteFile(filepath.Join(bin, "public", "zonewiki.js"), nil, 0o666)
	if got := publicNear(bin); got != filepath.Join(bin, "public") {
		t.Errorf("public/ beside the binary: %q", got)
	}
}

// zonewiki licenses prints every license file the binary holds: Zone's,
// THIRD_PARTY_LICENSES and each vendored library's.
func TestLicensesCommand(t *testing.T) {
	var out, errs bytes.Buffer
	if code := licensesMain(nil, &out, &errs); code != 0 {
		t.Fatalf("%d: %s", code, errs.String())
	}
	text := out.String()
	vendored, _ := filepath.Glob(filepath.Join("..", "public", "vendor", "*", "LICENSE"))
	if len(vendored) < 5 {
		t.Fatalf("%d vendored licenses", len(vendored))
	}
	for _, p := range append(vendored, filepath.Join("..", "LICENSE"), filepath.Join("..", "THIRD_PARTY_LICENSES"),
		filepath.Join("..", "public", "vendor", "mermaid", "THIRD_PARTY_LICENSES"),
		filepath.Join("..", "public", "vendor", "mermaid", "COPYING.GPL-3.0")) {
		rel, _ := filepath.Rel("..", p)
		want, _ := os.ReadFile(p)
		if !strings.Contains(text, "==> "+filepath.ToSlash(rel)+" <==\n\n"+string(want)) {
			t.Errorf("%s: not printed", rel)
		}
	}
	if !strings.HasPrefix(text, "==> LICENSE <==\n\n") || !strings.Contains(text, "GNU AFFERO GENERAL PUBLIC LICENSE") {
		t.Errorf("Zone's own license first: %.100q", text)
	}
	out.Reset()
	if code := licensesMain([]string{"--help"}, &out, &errs); code != 0 || !strings.Contains(out.String(), "zonewiki licenses") {
		t.Errorf("--help: %d %q", code, out.String())
	}
}
