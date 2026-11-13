// Files served as they are: the browser code in public/ (gzipped and kept
// by the browser where they don't change), and a file sent with its type.
package main

import (
	"bytes"
	"compress/gzip"
	"mime"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// servePublic serves the browser code (public/): files only, nothing
// outside the folder. The vendored libraries (public/vendor/) change only
// with an update of Zone, so the browser keeps them a day without asking;
// their scripts and styles go gzipped to a browser that takes it (Mermaid:
// 5.5 MB, 1.5 MB gzipped).
func servePublic(w http.ResponseWriter, r *http.Request) {
	name := r.PathValue("path")
	if strings.HasPrefix(name, "vendor/") {
		w.Header().Set("Cache-Control", "public, max-age=86400")
		if ext := filepath.Ext(name); ext == ".js" || ext == ".css" {
			w.Header().Set("Vary", "Accept-Encoding")
			if acceptsGzip(r) && serveGzipped(w, r, publicDir, name) {
				return
			}
		}
	}
	serveFile(w, r, publicDir, name, "")
}

// acceptsGzip: the request's Accept-Encoding takes gzip (not with q=0).
func acceptsGzip(r *http.Request) bool {
	for _, part := range strings.Split(r.Header.Get("Accept-Encoding"), ",") {
		coding, params, _ := strings.Cut(strings.TrimSpace(part), ";")
		if strings.EqualFold(strings.TrimSpace(coding), "gzip") {
			q := strings.ReplaceAll(params, " ", "")
			return q != "q=0" && q != "q=0.0" && q != "q=0.00" && q != "q=0.000"
		}
	}
	return false
}

// gzipped: files gzipped once, by path, until the file changes.
var gzipped = struct {
	sync.Mutex
	m map[string]gzipEntry
}{m: map[string]gzipEntry{}}

type gzipEntry struct {
	mtime time.Time
	data  []byte
}

// serveGzipped serves name from dir gzipped (as serveFile, a file inside
// dir); false when there is no such file, for serveFile to answer.
func serveGzipped(w http.ResponseWriter, r *http.Request, dir, name string) bool {
	path, ok := fileIn(dir, name)
	if !ok {
		return false
	}
	st, err := os.Stat(path)
	if err != nil || st.IsDir() {
		return false
	}
	gzipped.Lock()
	e, ok := gzipped.m[path]
	gzipped.Unlock()
	if !ok || !e.mtime.Equal(st.ModTime()) {
		raw, err := os.ReadFile(path)
		if err != nil {
			return false
		}
		var buf bytes.Buffer
		zw, _ := gzip.NewWriterLevel(&buf, gzip.BestCompression)
		zw.Write(raw)
		zw.Close()
		e = gzipEntry{st.ModTime(), buf.Bytes()}
		gzipped.Lock()
		gzipped.m[path] = e
		gzipped.Unlock()
	}
	w.Header().Set("Content-Type", mime.TypeByExtension(filepath.Ext(name)))
	w.Header().Set("Content-Encoding", "gzip")
	http.ServeContent(w, r, name, e.mtime, bytes.NewReader(e.data))
	return true
}

// fileIn: the path of name (slash-separated) inside dir; false for a name
// that could reach outside it (.., an empty part, a NUL).
func fileIn(dir, name string) (string, bool) {
	for _, part := range strings.Split(name, "/") {
		if part == "" || part == "." || part == ".." || strings.ContainsRune(part, 0) {
			return "", false
		}
	}
	return filepath.Join(dir, filepath.FromSlash(name)), true
}

// serveFile serves name from dir (a file, never a folder or anything
// outside dir); a disposition ("attachment") makes it a download.
func serveFile(w http.ResponseWriter, r *http.Request, dir, name, disposition string) {
	path, ok := fileIn(dir, name)
	if !ok {
		http.NotFound(w, r)
		return
	}
	f, err := os.Open(path)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil || st.IsDir() {
		http.NotFound(w, r)
		return
	}
	if disposition != "" {
		w.Header().Set("Content-Disposition",
			mime.FormatMediaType(disposition, map[string]string{"filename": filepath.Base(name)}))
	}
	http.ServeContent(w, r, name, st.ModTime(), f)
}
