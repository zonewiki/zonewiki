// Files served as they are: the browser code in public/ (gzipped and kept
// by the browser where they don't change), from a folder or the copy built
// into the binary (files.go), and a file sent with its type.
package main

import (
	"bytes"
	"compress/gzip"
	"io"
	"io/fs"
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
// with an update of Zonewiki, so the browser keeps them a day without asking;
// their scripts and styles go gzipped to a browser that takes it (Mermaid:
// 5.5 MB, 1.5 MB gzipped).
func servePublic(w http.ResponseWriter, r *http.Request) {
	name := r.PathValue("path")
	files := publicFiles()
	if strings.HasPrefix(name, "vendor/") {
		w.Header().Set("Cache-Control", "public, max-age=86400")
		if ext := filepath.Ext(name); ext == ".js" || ext == ".css" {
			w.Header().Set("Vary", "Accept-Encoding")
			if acceptsGzip(r) && serveGzipped(w, r, files, name) {
				return
			}
		}
	}
	serveFS(w, r, files, name, "")
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

// gzipped: the browser code's files gzipped once, by name, until the file
// changes (its time or size).
var gzipped = struct {
	sync.Mutex
	m map[string]gzipEntry
}{m: map[string]gzipEntry{}}

type gzipEntry struct {
	mtime time.Time
	size  int64
	data  []byte
}

// serveGzipped serves name from files gzipped (as serveFS, a file inside
// them); false when there is no such file, for serveFS to answer.
func serveGzipped(w http.ResponseWriter, r *http.Request, files fs.FS, name string) bool {
	if !validName(name) {
		return false
	}
	st, err := fs.Stat(files, name)
	if err != nil || st.IsDir() {
		return false
	}
	mtime := modTime(st)
	gzipped.Lock()
	e, ok := gzipped.m[name]
	gzipped.Unlock()
	if !ok || !e.mtime.Equal(mtime) || e.size != st.Size() {
		raw, err := fs.ReadFile(files, name)
		if err != nil {
			return false
		}
		var buf bytes.Buffer
		zw, _ := gzip.NewWriterLevel(&buf, gzip.BestCompression)
		zw.Write(raw)
		zw.Close()
		e = gzipEntry{mtime, st.Size(), buf.Bytes()}
		gzipped.Lock()
		gzipped.m[name] = e
		gzipped.Unlock()
	}
	w.Header().Set("Content-Type", mime.TypeByExtension(filepath.Ext(name)))
	w.Header().Set("Content-Encoding", "gzip")
	http.ServeContent(w, r, name, e.mtime, bytes.NewReader(e.data))
	return true
}

// validName: name (slash-separated) is a path inside a folder: false for
// one that could reach outside it (.., an empty part, a NUL, a '\\', which
// Windows reads as a folder's end).
func validName(name string) bool {
	for _, part := range strings.Split(name, "/") {
		if part == "" || part == "." || part == ".." || strings.ContainsAny(part, "\\\x00") {
			return false
		}
	}
	return true
}

// serveFS serves name from files, a folder's (os.DirFS) or the binary's (a
// file, never a folder or anything outside it); a disposition
// ("attachment") makes it a download.
func serveFS(w http.ResponseWriter, r *http.Request, files fs.FS, name, disposition string) {
	if !validName(name) {
		http.NotFound(w, r)
		return
	}
	f, err := files.Open(name)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	serveOpened(w, r, f, name, disposition)
}

// servePath serves the file at path (one its caller found: uploadPath), by
// name, as serveFS does.
func servePath(w http.ResponseWriter, r *http.Request, path, name, disposition string) {
	f, err := os.Open(path)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	serveOpened(w, r, f, name, disposition)
}

// serveOpened serves the open file f by name and closes it (a folder is
// none: 404).
func serveOpened(w http.ResponseWriter, r *http.Request, f fs.File, name, disposition string) {
	defer f.Close()
	st, err := f.Stat()
	content, seeks := f.(io.ReadSeeker) // a folder's files and the binary's all do
	if err != nil || st.IsDir() || !seeks {
		http.NotFound(w, r)
		return
	}
	if disposition != "" {
		w.Header().Set("Content-Disposition",
			mime.FormatMediaType(disposition, map[string]string{"filename": filepath.Base(name)}))
	}
	http.ServeContent(w, r, name, modTime(st), content)
}
