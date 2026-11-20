// Uploads: _assets/ (a private page's in private/<user>/_assets/, a
// group's in groups/<group>/_assets/, a task's in
// _tasks/files/<id>/_assets/), served
// and saved (POST /api/upload).
package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"io/fs"
	"mime"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"
	"zonewiki/internal/atomicfile"
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
// (a file is seen by who sees the page it is for), with the shared ones for
// a shared page or none. ok false: page is in a place that isn't user's
// (another user's private folder, a group's they aren't in, one their
// assistant's connection wasn't given, no one's): the file is refused, as
// left with the shared ones it would be everyone's, which whoever sent it
// for that page didn't mean. Judged by the place alone, so a refusal says
// nothing of whether the page is there.
func uploadPlace(user asker, page string) (place, bool) {
	switch p := placeOf(strings.Trim(page, "/")); {
	case p.shared():
		return place{}, true
	case p.theirs(user):
		return p, true
	}
	return place{}, false
}

// taskUploadPlace: where a file that user uploads for the task id is kept:
// with the task (taskPlace), when the wiki has users and they see it (a new
// task's id is the one its dialog made up, which the task then gets: the
// first file for it makes the folder theirs until then, claimUnsaved);
// without users with the shared ones, as nothing is anyone's. ok false: not
// a task's id, or a task they don't see, or a folder that is another's or
// no one's (a task deleted for good).
func taskUploadPlace(user asker, id string) (p place, ok bool) {
	if !taskFolderRE.MatchString(id) {
		return place{}, false
	}
	if settings().Users == nil {
		return place{}, true
	}
	if !seesTaskFiles(user, id) && !claimUnsaved(user, id) {
		return place{}, false
	}
	return taskPlace(id), true
}

// unsavedFiles: the task folders that got their first file before any task
// had their id (a new task's dialog pastes a file for the id it made up,
// before its first Save), by id: who put it there, and when. Until a task
// has the id its files are theirs alone (seesTaskFiles); the record goes
// when the task is made (createTask), so the files are its readers' from
// then on, and a task deleted for good leaves them to no one (until an
// undo brings the task back). Kept in Zone's folder, out of git
// (.zone/unsaved-files.json); one older than unsavedKept is forgotten (a
// dialog closed without a Save), and its files are no one's.
var unsavedFiles = struct {
	sync.Mutex
	path string // the file the records were read from (a test's wiki is another)
	by   map[string]unsavedFile
}{}

type unsavedFile struct {
	By string `json:"by"`
	At string `json:"at"`
}

// unsavedKept: how long a folder stays its uploader's without a task.
const unsavedKept = 30 * 24 * time.Hour

// loadUnsaved: the records, read once for a wiki's folder (unsavedFiles
// locked). A file that can't be read is no records: the folders are no
// one's.
func loadUnsaved() {
	path := statePath("unsaved-files.json")
	if unsavedFiles.by != nil && unsavedFiles.path == path {
		return
	}
	unsavedFiles.path, unsavedFiles.by = path, map[string]unsavedFile{}
	if raw, err := os.ReadFile(path); err == nil {
		if json.Unmarshal(raw, &unsavedFiles.by) != nil || unsavedFiles.by == nil {
			unsavedFiles.by = map[string]unsavedFile{}
		}
	}
}

// unsavedRecord: the record of the folder of the task id (unsavedFiles
// locked), ok false when there is none or it is too old.
func unsavedRecord(id string) (unsavedFile, bool) {
	u, ok := unsavedFiles.by[id]
	if at, err := time.ParseInLocation("2006-01-02T15:04", u.At, time.Local); !ok || err != nil || now().Sub(at) > unsavedKept {
		return unsavedFile{}, false
	}
	return u, true
}

// unsavedBy: who put the first file in the folder of the task id while no
// task had it, "" for none (also once a task was made with it).
func unsavedBy(id string) string {
	unsavedFiles.Lock()
	defer unsavedFiles.Unlock()
	loadUnsaved()
	u, _ := unsavedRecord(id)
	return u.By
}

// writeUnsaved writes the records, those too old left out (unsavedFiles
// locked).
func writeUnsaved() error {
	for id := range unsavedFiles.by {
		if _, ok := unsavedRecord(id); !ok {
			delete(unsavedFiles.by, id)
		}
	}
	text, err := json.MarshalIndent(unsavedFiles.by, "", "  ")
	if err != nil {
		return err
	}
	return atomicfile.WriteOwnerOnly(unsavedFiles.path, string(text)+"\n") // users' names and their tasks' ids
}

// claimUnsaved: user puts the first file in the folder of the task id,
// which no task has (in any case of the id: Find) and nothing is in yet
// (also as the file system spells it), and which nobody claimed: it is
// theirs from now on, until a task has the id. False for anything else: a
// task's folder (seesTaskFiles answers for it), another's, or one with
// files and no record (a task deleted for good, a record forgotten), and
// for an asker that may keep no such folder (keepsUnsaved).
func claimUnsaved(user asker, id string) bool {
	if !keepsUnsaved(user) || taskFiles.Find(id) != nil {
		return false
	}
	unsavedFiles.Lock()
	defer unsavedFiles.Unlock()
	loadUnsaved()
	if _, ok := unsavedRecord(id); ok {
		return false
	}
	if _, err := os.Stat(filepath.Join(cfg.Data, filepath.FromSlash(taskFilesDir), id)); !errors.Is(err, os.ErrNotExist) {
		return false
	}
	unsavedFiles.by[id] = unsavedFile{By: user.Name, At: now().Format("2006-01-02T15:04")}
	if err := writeUnsaved(); err != nil {
		logf("%s: %v", unsavedFiles.path, err)
		delete(unsavedFiles.by, id)
		return false
	}
	return true
}

// dropUnsaved: a task has the id now: its files are its readers'.
func dropUnsaved(id string) {
	unsavedFiles.Lock()
	defer unsavedFiles.Unlock()
	loadUnsaved()
	if _, ok := unsavedFiles.by[id]; !ok {
		return
	}
	delete(unsavedFiles.by, id)
	if err := writeUnsaved(); err != nil {
		logf("%s: %v", unsavedFiles.path, err)
	}
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
	serveUploadFile(w, r, place{}, r.PathValue("path"))
}

// servePrivateUpload: a user's private upload, to that user only (404 for
// anyone else, as for a file that isn't there).
func servePrivateUpload(w http.ResponseWriter, r *http.Request) {
	owner := r.PathValue("user")
	if owner == "" || settings().Users[owner] == nil || !(place{ofUser, owner}).sees(userOf(r)) {
		http.NotFound(w, r)
		return
	}
	serveUploadFile(w, r, place{ofUser, owner}, r.PathValue("file"))
}

// serveGroupUpload: a group's upload, to its members only (404 for anyone
// else, as for a file that isn't there, and for a group that isn't).
func serveGroupUpload(w http.ResponseWriter, r *http.Request) {
	p := placeOf(groupsDir + "/" + r.PathValue("group") + "/_assets")
	if p.kind != ofGroup || p.name != r.PathValue("group") || !p.sees(userOf(r)) {
		http.NotFound(w, r)
		return
	}
	serveUploadFile(w, r, p, r.PathValue("file"))
}

// serveTaskUpload: a task's file, to who sees the task (404 for anyone
// else, as for a file that isn't there).
func serveTaskUpload(w http.ResponseWriter, r *http.Request) {
	p := taskPlace(r.PathValue("task"))
	if !p.sees(userOf(r)) {
		http.NotFound(w, r)
		return
	}
	serveUploadFile(w, r, p, r.PathValue("file"))
}

// inlineTypes: the media types an upload is shown inline as, what a page's
// <img>, <audio>, <video> and <iframe> show (pictures, sound, film, PDF,
// text). Any other is a download, whatever the host's mime table calls it:
// an odd type there (image/svg+xml-compressed for .svgz, image/x-xbitmap …)
// is no picture a browser shows, but may be a document it reads its own way.
var inlineTypes = map[string]bool{}

func init() {
	for _, t := range strings.Fields(`image/png image/jpeg image/gif image/webp image/avif image/svg+xml
		image/bmp image/vnd.microsoft.icon image/x-icon image/tiff image/heic
		audio/mpeg audio/mp4 audio/ogg audio/opus audio/wav audio/x-wav audio/flac audio/aac audio/webm
		video/mp4 video/webm video/ogg video/quicktime video/x-matroska
		application/pdf text/plain`) {
		inlineTypes[t] = true
	}
}

// serveUploadFile: filename in the uploads of p (uploadPath: no other
// file, 404 for a name that is none there), inline only as one of inlineTypes.
func serveUploadFile(w http.ResponseWriter, r *http.Request, p place, filename string) {
	path, ok := uploadPath(p, filename)
	if !ok {
		http.NotFound(w, r)
		return
	}
	mimeType := mediaType(filename)
	inline := inlineTypes[mimeType]
	disposition := ""
	if inline {
		if mimeType == "text/plain" {
			mimeType += "; charset=utf-8"
		}
		w.Header().Set("Content-Type", mimeType)
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
	frameableBySelf(w.Header()) // a page may show it in an <iframe>
	servePath(w, r, path, filename, disposition)
}

// uploadPath: the file name is in the uploads of p (uploadDir), and ok
// false for a name that is no file of theirs: every door to an upload asks
// here, the routes that serve one and write one, an assistant's read_file
// and upload_file, a page's files taken along when it moves (shareUploads).
// A name is one plain part: no '/' nor '\' (a folder's end on Windows,
// which would lead out of the folder), not a dot file ("..", ".git"), no
// end in '.' or ' ' (which Windows drops: pagePart). And the file is the
// one the name says, as a page is (safePath): nothing on the way to it is
// a symlink, so a link made on the host, to another user's upload or out of
// the wiki, is no upload and nothing is written through it.
func uploadPath(p place, name string) (string, bool) {
	if !uploadNamed(name) {
		return "", false
	}
	path := filepath.Join(wikiRoot(), filepath.FromSlash(uploadRel(p, name)))
	if resolvePath(path) != path {
		return "", false
	}
	if st, err := os.Lstat(path); err == nil && st.Mode()&fs.ModeSymlink != 0 {
		return "", false // a link that leads nowhere: a write would go through it
	}
	return path, true
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
	page := r.FormValue("page")
	owner, ours := uploadPlace(userOf(r), page)
	// task: the file is for a task (its id; the dialog's, for a new one).
	if id := r.FormValue("task"); id != "" {
		var ok bool
		if owner, ok = taskUploadPlace(userOf(r), id); !ok {
			http.Error(w, "No such task", http.StatusNotFound)
			return
		}
	} else if !ours {
		http.Error(w, notYours(strings.Trim(page, "/"), userOf(r)), http.StatusNotFound)
		return
	}
	if changesNothing(userOf(r)) {
		http.Error(w, noChangeMsg(userOf(r)), http.StatusForbidden)
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
	dest, ok := uploadPath(owner, filename)
	if !ok {
		http.Error(w, "Bad Request", http.StatusBadRequest) // a link there, or no name an upload can have
		return
	}
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
// An assistant's upload with its token is marked as an assistant's, as
// upload_file's is (mcpMark).
func saveUpload(w http.ResponseWriter, r *http.Request, src io.ReadSeeker, owner place, filename string) bool {
	via := ""
	if byToken(r) {
		via = " " + assistantMark + commitTrailers(viaOf(r), "")
	}
	err := storeUpload(userOf(r), owner, filename, src, via)
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
func storeUpload(user asker, owner place, filename string, src io.ReadSeeker, via string) error {
	dest, ok := uploadPath(owner, filename)
	if !ok {
		return refuse(strconv.Quote(filename) + " can't be an upload's name there")
	}
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
	return notCommitted(gitCommit(user.Name, "Upload "+name+via, dest))
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
// its last part, if it is a name an upload can have (uploadNamed). "" for
// none.
func uploadName(name string) string {
	if name = baseName(strings.TrimSpace(name)); !uploadNamed(name) {
		return ""
	}
	return name
}

// uploadNamed: name is one an upload can have (uploadPath): one plain
// part, no '/' nor '\\' nor NUL, no line break or other control
// character (as a page's name: safePath), not a dot file ("..", ".git"
// and friends), no end in '.' or ' ' (pagePart).
func uploadNamed(name string) bool {
	return name != "" && !strings.ContainsAny(name, "/\\\x00") && !strings.ContainsFunc(name, mdtext.BadNameRune) &&
		!strings.HasPrefix(name, ".") && pagePart(name)
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
