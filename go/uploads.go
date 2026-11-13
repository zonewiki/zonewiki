// Uploads: _assets/ (a private page's in private/<user>/_assets/), served
// and saved (POST /api/upload).
package main

import (
	"crypto/rand"
	"encoding/hex"
	"io"
	"mime"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
)

// Uploads are shown inline only when the browser treats them as passive
// media. Anything else (HTML, JS, …) would run with Zone's origin, and so
// could read and rewrite the wiki, so it is served as a download. SVG stays
// inline for <img> tags but gets a sandbox CSP, which disables its scripts
// when the file is opened directly.
const svgCSP = "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"

// uploadDir: where owner's uploads are kept: _assets/ for the shared ones
// (owner ""), private/<owner>/_assets/ for a user's private ones, in their
// folder like their pages. Their URLs follow the folders: /_assets/<file>
// and /private/<owner>/_assets/<file>.
func uploadDir(owner string) string {
	if owner == "" {
		return cfg.Uploads
	}
	return filepath.Join(cfg.Data, privateDir, owner, "_assets")
}

// uploadURL: the URL of file in owner's uploads.
func uploadURL(owner, file string) string {
	if owner == "" {
		return "/_assets/" + file
	}
	return "/" + privateDir + "/" + owner + "/_assets/" + file
}

// serveUpload: a shared upload, a file straight in _assets/.
func serveUpload(w http.ResponseWriter, r *http.Request) {
	filename := r.PathValue("path")
	if strings.Contains(filename, "/") {
		http.NotFound(w, r)
		return
	}
	serveUploadFile(w, r, cfg.Uploads, filename)
}

// servePrivateUpload: a user's private upload, to that user only (404 for
// anyone else, as for a file that isn't there).
func servePrivateUpload(w http.ResponseWriter, r *http.Request) {
	owner := r.PathValue("user")
	if owner == "" || owner != userOf(r) || settings().Users[owner] == nil {
		http.NotFound(w, r)
		return
	}
	serveUploadFile(w, r, uploadDir(owner), r.PathValue("file"))
}

// serveUploadFile: filename in dir, inline only when the browser treats it
// as passive content.
func serveUploadFile(w http.ResponseWriter, r *http.Request, dir, filename string) {
	mimeType := mediaType(filename)
	inline := strings.HasPrefix(mimeType, "image/") || strings.HasPrefix(mimeType, "audio/") ||
		strings.HasPrefix(mimeType, "video/") || mimeType == "application/pdf" || mimeType == "text/plain"
	disposition := ""
	if inline {
		w.Header().Set("Content-Type", mime.TypeByExtension(filepath.Ext(filename)))
	} else {
		// A download keeps its name but not its type: an uploaded .js served
		// as text/javascript would run from <script src=…> (a download
		// doesn't stop that), and script-src 'self' trusts Zone's own origin.
		w.Header().Set("Content-Type", "application/octet-stream")
		disposition = "attachment"
	}
	if mimeType == "image/svg+xml" {
		w.Header().Set("Content-Security-Policy", svgCSP)
	}
	serveFile(w, r, dir, filename, disposition)
}

// mediaType is a file name's media type without parameters
// ("application/octet-stream" when unknown).
func mediaType(name string) string {
	t, _, err := mime.ParseMediaType(mime.TypeByExtension(strings.ToLower(filepath.Ext(name))))
	if err != nil || t == "" {
		return "application/octet-stream"
	}
	return t
}

// Types Go may not know without the system's mime.types (the container has
// none), so uploads of them are shown inline.
func init() {
	for ext, t := range map[string]string{
		".txt": "text/plain; charset=utf-8", ".bmp": "image/bmp", ".ico": "image/vnd.microsoft.icon",
		".tif": "image/tiff", ".tiff": "image/tiff", ".heic": "image/heic",
		".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".oga": "audio/ogg",
		".opus": "audio/opus", ".wav": "audio/x-wav", ".flac": "audio/flac", ".aac": "audio/aac",
		".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
		".mkv": "video/x-matroska", ".ogv": "video/ogg", ".md": "text/markdown",
	} {
		_ = mime.AddExtensionType(ext, t)
	}
}

// uploadExts: the extensions of common pasted types, where the system's
// table has several (or none).
var uploadExts = map[string]string{"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
	"image/webp": ".webp", "image/svg+xml": ".svg", "text/plain": ".txt"}

// uploadExt is the extension for a media type (".bin" when unknown): what
// a pasted file without a name is saved as.
func uploadExt(mimeType string) string {
	t, _, _ := mime.ParseMediaType(mimeType)
	if ext, ok := uploadExts[t]; ok {
		return ext
	}
	if exts, _ := mime.ExtensionsByType(t); len(exts) > 0 {
		return exts[0]
	}
	return ".bin"
}

func apiUpload(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseMultipartForm(32 << 20); err != nil {
		if tooLarge(err) {
			tooLargeError(w, settings().uploadMax())
			return
		}
		http.Error(w, "Bad Request", http.StatusBadRequest)
		return
	}
	f, header, err := r.FormFile("file")
	if err != nil {
		http.Error(w, "Bad Request", http.StatusBadRequest)
		return
	}
	defer f.Close()
	fileType := header.Header.Get("Content-Type")
	originalName := baseName(header.Filename)
	if originalName == "" {
		originalName = "upload" + uploadExt(fileType)
	}
	// An upload for a page in the user's private folder goes in theirs.
	owner := ""
	if o, private := pageOwner(r.FormValue("page")); private && o != "" && o == userOf(r) {
		owner = o
	}
	if settings().isViewer(userOf(r)) {
		http.Error(w, readOnlyMsg, http.StatusForbidden)
		return
	}
	// unique=1: a random file name (clipboard screenshots).
	if r.FormValue("unique") == "1" {
		ext := strings.ToLower(suffix(originalName))
		if ext == "" {
			ext = uploadExt(fileType)
		}
		raw := make([]byte, 16)
		_, _ = rand.Read(raw)
		filename := hex.EncodeToString(raw) + ext
		if !saveUpload(w, r, f, owner, filename) {
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"url": uploadURL(owner, filename)})
		return
	}
	// The client may ask for a name (after the conflict dialog).
	filename := originalName
	if requested := strings.TrimSpace(r.FormValue("filename")); requested != "" {
		filename = baseName(requested)
	}
	if filename == "" || strings.HasPrefix(filename, ".") {
		http.Error(w, "Bad Request", http.StatusBadRequest) // "..", ".git" and friends: not an asset name
		return
	}
	dest := filepath.Join(uploadDir(owner), filename)
	if _, err := os.Stat(dest); err == nil && r.FormValue("overwrite") != "1" {
		writeJSON(w, http.StatusConflict, map[string]any{"conflict": true, "filename": filename})
		return
	}
	if !saveUpload(w, r, f, owner, filename) {
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"url": uploadURL(owner, filename)})
}

// uploadMu keeps two uploads from writing one file at once.
var uploadMu sync.Mutex

// saveUpload writes an upload to owner's uploads (uploadDir) and commits it.
func saveUpload(w http.ResponseWriter, r *http.Request, src io.ReadSeeker, owner, filename string) bool {
	dest := filepath.Join(uploadDir(owner), filename)
	uploadMu.Lock()
	defer uploadMu.Unlock()
	// What it adds: its size, less the file's it replaces.
	size, _ := src.Seek(0, io.SeekEnd)
	if _, err := src.Seek(0, io.SeekStart); err != nil {
		serverError(w, r, err)
		return false
	}
	if st, err := os.Stat(dest); err == nil {
		size -= st.Size()
	}
	if err := checkRoom(size); err != nil {
		pe, _ := refusalOf(err)
		http.Error(w, pe.msg, http.StatusInsufficientStorage)
		return false
	}
	err := os.MkdirAll(filepath.Dir(dest), 0o777)
	var out *os.File
	if err == nil {
		out, err = os.Create(dest)
	}
	if err == nil {
		_, err = io.Copy(out, src)
		if cerr := out.Close(); err == nil {
			err = cerr
		}
	}
	if err != nil {
		logf("saving upload %s: %v", filename, err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return false
	}
	name := filename
	if owner != "" {
		name = privateDir + "/" + owner + "/_assets/" + filename
	}
	added(size)
	gitCommit(userOf(r), "Upload "+name, dest)
	return true
}

// baseName is a slash-separated path's last part ("" for ".").
func baseName(p string) string {
	p = strings.TrimRight(p, "/")
	if i := strings.LastIndex(p, "/"); i >= 0 {
		p = p[i+1:]
	}
	if p == "." {
		return ""
	}
	return p
}

// suffix is a file name's extension: "" for
// ".bashrc", "." for "name.".
func suffix(name string) string {
	i := strings.LastIndex(name, ".")
	if i <= 0 || strings.Trim(name, ".") == "" {
		return ""
	}
	return name[i:]
}
