// Uploads: _assets/ (a private page's in private/<user>/_assets/, a
// group's in groups/<group>/_assets/, a task's in
// _tasks/files/<id>/_assets/), served
// and saved (POST /api/upload).
package main

import (
	"crypto/rand"
	"encoding/hex"
	"io"
	"mime"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"zonewiki/internal/mdtext"
)

// Uploads are shown inline only when the browser treats them as passive
// media. Anything else (HTML, JS, …) would run with Zone's origin, and so
// could read and rewrite the wiki, so it is served as a download. SVG stays
// inline for <img> tags but gets a sandbox CSP, which disables its scripts
// when the file is opened directly.
const svgCSP = "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"

// uploadDir: where the uploads of a place are kept: _assets/ for the shared
// ones, private/<user>/_assets/ for a user's private ones and
// groups/<group>/_assets/ for a group's, in the folder like its pages. Their
// URLs follow the folders: /_assets/<file>, /private/<user>/_assets/<file>,
// /groups/<group>/_assets/<file>.
func uploadDir(p place) string {
	if folder := p.folder(); folder != "" {
		return filepath.Join(cfg.Data, filepath.FromSlash(folder), "_assets")
	}
	return cfg.Uploads
}

// uploadPlace: where a file that user uploads for page is kept: with the
// page, when it is in their own private folder or in a group's they are in
// (a file is seen by who sees the page it is for), else with the shared
// ones.
func uploadPlace(user, page string) place {
	if p := placeOf(page); p.theirs(user) {
		return p
	}
	return place{}
}

// taskUploadPlace: where a file that user uploads for the task id is kept:
// with the task (taskPlace), when the wiki has users and they see it (a new
// task's id is the one its dialog made up, which the task then gets);
// without users with the shared ones, as nothing is anyone's. ok false: not
// a task's id, or a task they don't see.
func taskUploadPlace(user, id string) (p place, ok bool) {
	if !taskFolderRE.MatchString(id) || !seesTaskFiles(user, id) {
		return place{}, false
	}
	if settings().Users == nil {
		return place{}, true
	}
	return taskPlace(id), true
}

// uploadRel: file in a place's uploads, as its path in the wiki
// ("groups/dev/_assets/a.png").
func uploadRel(p place, file string) string {
	if folder := p.folder(); folder != "" {
		return folder + "/_assets/" + file
	}
	return "_assets/" + file
}

// uploadURL: the URL of file in a place's uploads, as a markdown link takes
// it: percent-encoded (pathURL), since a space in a link's target ends the
// link and a bracket may ("My Deck (v2).pdf").
func uploadURL(p place, file string) string {
	return pathURL(strings.Split(uploadRel(p, file), "/")...)
}

// A link to an upload written with the file's name as it is, spaces and
// all: `[Deck](/_assets/My Deck.pdf)`, which markdown reads as text, not a
// link. The editor wrote such links for every attached file with a space in
// its name, until uploadURL encoded them. Group 2 is the target, up to the
// file's extension before the closing bracket.
var spacedUploadRE = regexp.MustCompile(`(\]\()(/(?:(?:` + keptDirs + `)/[a-z0-9-]+/)?_assets/[^\n<>"]*?\.[A-Za-z0-9]{1,8})(\))`)

// encodeUploadLinks: text with such links' targets percent-encoded, so they
// are links (code aside). The text on disk stays as written.
func encodeUploadLinks(text string) string {
	if !strings.Contains(text, "_assets/") {
		return text
	}
	masked, stash := mdtext.MaskCode(text)
	fixed := spacedUploadRE.ReplaceAllStringFunc(masked, func(m string) string {
		g := spacedUploadRE.FindStringSubmatch(m)
		if !strings.Contains(g[2], " ") {
			return m
		}
		target, err := url.PathUnescape(g[2]) // what is encoded already stays so
		if err != nil {
			target = g[2]
		}
		return g[1] + pathURL(target)[1:] + g[3]
	})
	if fixed == masked {
		return text
	}
	return mdtext.UnmaskCode(fixed, stash)
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
	serveUploadFile(w, r, uploadDir(place{ofUser, owner}), r.PathValue("file"))
}

// serveGroupUpload: a group's upload, to its members only (404 for anyone
// else, as for a file that isn't there, and for a group that isn't).
func serveGroupUpload(w http.ResponseWriter, r *http.Request) {
	p := placeOf(groupsDir + "/" + r.PathValue("group") + "/_assets")
	if p.kind != ofGroup || p.name != r.PathValue("group") || !p.sees(userOf(r)) {
		http.NotFound(w, r)
		return
	}
	serveUploadFile(w, r, uploadDir(p), r.PathValue("file"))
}

// serveTaskUpload: a task's file, to who sees the task (404 for anyone
// else, as for a file that isn't there).
func serveTaskUpload(w http.ResponseWriter, r *http.Request) {
	p := taskPlace(r.PathValue("task"))
	if !p.sees(userOf(r)) {
		http.NotFound(w, r)
		return
	}
	serveUploadFile(w, r, uploadDir(p), r.PathValue("file"))
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
	owner := uploadPlace(userOf(r), r.FormValue("page"))
	// task: the file is for a task (its id; the dialog's, for a new one).
	if id := r.FormValue("task"); id != "" {
		var ok bool
		if owner, ok = taskUploadPlace(userOf(r), id); !ok {
			http.Error(w, "No such task", http.StatusNotFound)
			return
		}
	}
	if onlyReads(r) {
		msg := readOnlyMsg
		if !settings().isViewer(userOf(r)) {
			msg = readOnlyConnectionMsg
		}
		http.Error(w, msg, http.StatusForbidden)
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
	if filename = uploadName(filename); filename == "" {
		http.Error(w, "Bad Request", http.StatusBadRequest) // "..", ".git" and friends: not an asset name
		return
	}
	// An assistant with its token has no dialog to answer: its file gets a
	// free name, and never replaces one (mcpfiles.go).
	if byToken(r) {
		filename = freeUploadName(owner, filename)
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

// saveUpload: storeUpload for the route, which answers what went wrong.
func saveUpload(w http.ResponseWriter, r *http.Request, src io.ReadSeeker, owner place, filename string) bool {
	err := storeUpload(userOf(r), owner, filename, src, "")
	if pe, ok := refusalOf(err); ok {
		http.Error(w, pe.msg, pe.kind.status())
		return false
	}
	if err != nil {
		logf("saving upload %s: %v", filename, err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return false
	}
	return true
}

// storeUpload writes an upload to a place's uploads (uploadDir) and commits
// it as user, the commit's message ending with via (" (MCP)" for a tool's).
// A wiki with no room for it refuses (checkRoom).
func storeUpload(user string, owner place, filename string, src io.ReadSeeker, via string) error {
	dest := filepath.Join(uploadDir(owner), filename)
	uploadMu.Lock()
	defer uploadMu.Unlock()
	// What it adds: its size, less the file's it replaces.
	size, _ := src.Seek(0, io.SeekEnd)
	if _, err := src.Seek(0, io.SeekStart); err != nil {
		return err
	}
	if st, err := os.Stat(dest); err == nil {
		size -= st.Size()
	}
	if err := checkRoom(size); err != nil {
		return err
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
		return err
	}
	name := filename
	if !owner.shared() {
		name = uploadRel(owner, filename)
	}
	added(size)
	// Written; a commit that fails is said, as for any write (notCommitted).
	return notCommitted(gitCommit(user, "Upload "+name+via, dest))
}

// freeUploadName: filename, or with a number before its extension
// (deck-2.pdf, deck-3.pdf …) when the place's uploads have a file of that name:
// an upload that asks nobody (an assistant's) never replaces a file.
func freeUploadName(owner place, filename string) string {
	stem, ext := filename, suffix(filename)
	stem = strings.TrimSuffix(stem, ext)
	name := filename
	for n := 2; exists(filepath.Join(uploadDir(owner), name)); n++ {
		name = stem + "-" + strconv.Itoa(n) + ext
	}
	return name
}

// uploadName: the name an upload is kept under, from the one it came with:
// its last part, and not a dot file ("..", ".git" and friends). "" for none.
func uploadName(name string) string {
	name = baseName(strings.TrimSpace(name))
	if strings.HasPrefix(name, ".") {
		return ""
	}
	return name
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
