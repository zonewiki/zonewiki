// Importing pages in the browser (Importing pages in docs/spec/editor.md):
// a zip of a folder of markdown notes, an Obsidian vault for one, is
// uploaded (POST /import), unpacked into .zone/imports/<id>/ and its
// plan shown as a report; Import writes it (POST /import/apply, importOp),
// Cancel drops it (POST /import/cancel). The zip itself is deleted as
// soon as it is unpacked; what was unpacked waits for its uploader alone,
// an hour at most, and nothing of it is in git or shown by any route.
package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"html/template"
	"io"
	"io/fs"
	"mime/multipart"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"

	"zonewiki/internal/mdimport"
	"zonewiki/internal/mdtext"
	"zonewiki/internal/wikiarchive"
)

// importsDirName: the waiting imports, in .zone/.
const importsDirName = "imports"

// importWait: how long an import waits for Import or Cancel.
var importWait = time.Hour

// What an upload may unpack to: entries, kept or not, and bytes, as so
// many times upload-max (a zip's notes pack small). To be measured.
const (
	importMaxEntries = 20000
	importUnpackRate = 4
)

// importFormMax: the most the form's fields take besides the zip, in
// POST /import's body, whose cap is upload-max plus this (checks).
const importFormMax = 64 << 10

// importLeftOut: the folders of a vault an import never unpacks, with
// all in them (the converter leaves them out too: skippedDirs), and
// __MACOSX, the resource forks a Mac's zip puts beside the files.
var importLeftOut = map[string]bool{".obsidian": true, ".trash": true, ".git": true}

// waitingImport: an upload unpacked, waiting in .zone/imports/<id>/: its
// file import.json, beside the folder notes/ that holds what was unpacked.
type waitingImport struct {
	User     string    `json:"user"`     // who uploaded it, and the only one who sees it (uploadedBy)
	Folder   string    `json:"folder"`   // the folder the pages go into, checked at the upload, again at Import
	Name     string    `json:"name"`     // what was imported (the zip's one folder, else the zip): the index's title
	Root     string    `json:"root"`     // the notes' folder in notes/: "" or the zip's one folder
	Obsidian bool      `json:"obsidian"` // .obsidian/ was there, and left out
	At       time.Time `json:"at"`       // when it was uploaded
	id       string
}

// importsMu: the imports' folder lock, held to find, add and remove one,
// never while unpacking or writing; busy: those being imported now, which
// no sweep or Cancel removes meanwhile.
var importsMu struct {
	sync.Mutex
	busy map[string]bool
}

var waitingIDRE = regexp.MustCompile(`^[0-9a-f]{32}$`)

func importsDir() string { return statePath(importsDirName) }

func (wi waitingImport) dir() string { return filepath.Join(importsDir(), wi.id) }
func (wi waitingImport) notes() string {
	return filepath.Join(wi.dir(), "notes", filepath.FromSlash(wi.Root))
}

// clearImports: everything under .zone/imports/, at a start: what waited
// when Zonewiki stopped or crashed is gone.
func clearImports() {
	if err := os.RemoveAll(importsDir()); err != nil {
		logf("removing the waiting imports: %v", err)
	}
}

// readWaiting: the import with that id, if it is there, whole, and not
// past its hour.
func readWaiting(id string) (waitingImport, bool) {
	if !waitingIDRE.MatchString(id) {
		return waitingImport{}, false
	}
	b, err := os.ReadFile(filepath.Join(importsDir(), id, "import.json"))
	if err != nil {
		return waitingImport{}, false
	}
	var wi waitingImport
	if json.Unmarshal(b, &wi) != nil || now().Sub(wi.At) > importWait {
		return waitingImport{}, false
	}
	wi.id = id
	return wi, true
}

// sweepImports removes the imports past their hour, and folders without
// an import.json (an upload under way) older than that, but not those
// being imported. Every import route runs it first: there is no timer of
// its own.
func sweepImports() {
	importsMu.Lock()
	defer importsMu.Unlock()
	entries, _ := os.ReadDir(importsDir())
	for _, e := range entries {
		id := e.Name()
		if importsMu.busy[id] {
			continue
		}
		if _, ok := readWaiting(id); ok {
			continue
		}
		dir := filepath.Join(importsDir(), id)
		_, err := os.Stat(filepath.Join(dir, "import.json"))
		if info, ierr := e.Info(); errors.Is(err, os.ErrNotExist) && ierr == nil && now().Sub(info.ModTime()) <= importWait {
			continue // an upload being unpacked
		}
		_ = os.RemoveAll(dir)
	}
}

// dropImportsOf removes the user's waiting imports but keep: one waits
// per user, the last uploaded.
func dropImportsOf(user asker, keep string) {
	importsMu.Lock()
	defer importsMu.Unlock()
	entries, _ := os.ReadDir(importsDir())
	for _, e := range entries {
		if wi, ok := readWaiting(e.Name()); ok && wi.id != keep && !importsMu.busy[wi.id] && uploadedBy(user, wi.User) {
			_ = os.RemoveAll(wi.dir())
		}
	}
}

// userImport: the import id names, if it waits and is user's; another's
// answers as one that isn't there.
func userImport(user asker, id string) (waitingImport, bool) {
	wi, ok := readWaiting(id)
	if !ok || !uploadedBy(user, wi.User) {
		return waitingImport{}, false
	}
	return wi, true
}

func importRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /import", importForm)
	mux.HandleFunc("POST /import", importUpload) // writes no page: only into .zone/imports/
	mux.HandleFunc("POST /import/apply", writesPages(importApply))
	mux.HandleFunc("POST /import/cancel", importCancel)
}

// importView: /import (import.html): the form, or an upload's report.
type importView struct {
	view
	Groups         []string // the groups the user is in, whose folders the pages can go into
	For            string   // what For says: "", "private", "group:<name>"
	Folder         string   // the folder typed
	Error          string
	MaxUpload      string // upload-max, for the form's hint
	Report         *importReport
	BreadcrumbHTML template.HTML
}

// importReport: what an upload would import, before anything is written.
type importReport struct {
	ID, Folder, Name string
	Obsidian         bool
	Pages, Files     int
	Sections         []importSection
}

// importSection: one kind of the report's lines, rendered (they are
// markdown, names in code spans).
type importSection struct {
	Title string
	HTML  template.HTML
}

// importFor: what For says, from the form or the query (as /new takes
// it: for=private, for=group:<name>, private=1), "" for anything the
// user can't choose.
func importFor(user asker, who, private string) string {
	if private == "1" && who == "" {
		who = "private"
	}
	if who == "private" && privateFolder(user) != "" {
		return who
	}
	if g, ok := strings.CutPrefix(who, "group:"); ok && newFromRE.MatchString(who) && inGroup(user, g) {
		return who
	}
	return ""
}

// importFolder: the folder an import goes into: the one typed, else one
// named after the zip, as a slug, in the place For says (as /new puts a
// page there), "" when there is no name to make one of.
func importFolder(user asker, who, typed, zip string) string {
	folder := mdtext.ToSlug(typed)
	if folder == "" {
		folder = mdtext.ToSlug(strings.TrimSuffix(baseName(zip), filepath.Ext(zip)))
	}
	if folder == "" {
		folder = "imported-notes"
	}
	switch g, isGroup := strings.CutPrefix(who, "group:"); {
	case who == "private":
		if own := privateFolder(user); !strings.HasPrefix(folder+"/", own+"/") {
			folder = own + "/" + folder
		}
	case isGroup:
		if !strings.HasPrefix(folder+"/", groupsDir+"/"+g+"/") {
			folder = groupsDir + "/" + g + "/" + folder
		}
	}
	return folder
}

// showImport renders /import: v's form or report, its error with status.
func showImport(w http.ResponseWriter, rq *Req, status int, v *importView) {
	if v.Report == nil {
		v.Groups = groupsFor(rq.user)
	}
	v.MaxUpload = formatSize(settings().uploadMax())
	v.BreadcrumbHTML = breadcrumb(rq, []crumb{{"Import pages", ""}}, "/import", false, "Import pages", "")
	page(w, rq, status, "import.html", "Import pages", shell{}, v)
}

// importFailed: the form again, with what refused the upload or the
// import.
func importFailed(w http.ResponseWriter, r *http.Request, v *importView, err error) {
	pe, ok := refusalOf(err)
	if !ok {
		serverError(w, r, err)
		return
	}
	v.Error = pe.msg
	showImport(w, newReq(r), pe.kind.status(), v)
}

// importForm: GET /import, the form (Import pages in the search list).
func importForm(w http.ResponseWriter, r *http.Request) {
	sweepImports()
	user := userOf(r)
	if changesNothing(user) {
		http.Error(w, noChangeMsg(user), http.StatusForbidden)
		return
	}
	q := r.URL.Query()
	showImport(w, newReq(r), http.StatusOK, &importView{For: importFor(user, q.Get("for"), q.Get("private"))})
}

// importUpload: POST /import, the zip and where its pages go: unpacked
// into a waiting folder of its own, converted, and answered with the
// report and Import and Cancel. Whatever refuses it (a folder that is
// there or not the user's, a zip too big or not a zip of notes, a wiki
// without room) answers the form again with why, its folder removed
// first; the zip is deleted as soon as it is unpacked, before the report.
func importUpload(w http.ResponseWriter, r *http.Request) {
	sweepImports()
	user := userOf(r)
	if changesNothing(user) {
		http.Error(w, noChangeMsg(user), http.StatusForbidden)
		return
	}
	v := &importView{}
	mr, err := r.MultipartReader()
	if err != nil {
		badRequestPage(w)
		return
	}
	id, err := newImportID()
	if err != nil {
		serverError(w, r, err)
		return
	}
	wi := waitingImport{User: user.Name, id: id}
	if err := os.MkdirAll(wi.dir(), 0o700); err != nil {
		serverError(w, r, err)
		return
	}
	kept := false
	defer func() {
		if !kept {
			_ = os.RemoveAll(wi.dir())
		}
	}()
	zip := filepath.Join(wi.dir(), "upload.zip")
	zipName, size, fields, err := readImportForm(mr, zip)
	if err != nil {
		if errors.Is(err, errImportTooBig) {
			importFailed(w, r, v, refuseAs(refusedTooLarge, "The zip is too large: an upload may be at most "+
				formatSize(settings().uploadMax())+" (upload-max in the wiki's settings)."))
			return
		}
		badRequestPage(w)
		return
	}
	v.For, v.Folder = importFor(user, fields["for"], fields["private"]), fields["folder"]
	if zipName == "" || size == 0 {
		importFailed(w, r, v, refuse("Choose the zip file to import."))
		return
	}
	wi.Folder = importFolder(user, v.For, v.Folder, zipName)
	opts, err := importOptions(user, wi.Folder)
	if err != nil {
		importFailed(w, r, v, err)
		return
	}
	dropImportsOf(user, id) // the new one takes its place
	err = unpackImport(&wi, zip, size, strings.TrimSuffix(baseName(zipName), filepath.Ext(zipName)))
	if rmErr := os.Remove(zip); rmErr != nil && !errors.Is(rmErr, os.ErrNotExist) && err == nil {
		err = rmErr
	}
	if err != nil {
		importFailed(w, r, v, err)
		return
	}
	plan, err := convertImport(wi, opts)
	if err != nil {
		importFailed(w, r, v, err)
		return
	}
	if err := checkRoom(planSize(plan, os.DirFS(wi.notes()))); err != nil {
		importFailed(w, r, v, err)
		return
	}
	wi.At = now()
	b, _ := json.Marshal(wi)
	if err := os.WriteFile(filepath.Join(wi.dir(), "import.json"), b, 0o600); err != nil {
		serverError(w, r, err)
		return
	}
	kept = true
	dropImportsOf(user, id) // one uploaded meanwhile
	showImport(w, newReq(r), http.StatusOK, &importView{Report: reportOf(newReq(r), wi, plan)})
}

func newImportID() (string, error) {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
}

// errImportTooBig: the zip is over upload-max.
var errImportTooBig = errors.New("the zip is too big")

// readImportForm reads POST /import's form as it comes: the zip (field
// file) into the file zip, at most upload-max of it, and the other
// fields (folder, for, private), a few KB each; returns the zip's name
// as the browser gave it and its size.
func readImportForm(mr *multipart.Reader, zip string) (name string, size int64, fields map[string]string, err error) {
	fields = map[string]string{}
	max := settings().uploadMax()
	for {
		part, err := mr.NextPart()
		if errors.Is(err, io.EOF) {
			return name, size, fields, nil
		}
		if err != nil {
			if tooLarge(err) {
				err = errImportTooBig
			}
			return "", 0, nil, err
		}
		switch field := part.FormName(); field {
		case "file":
			if name != "" {
				return "", 0, nil, errors.New("two files")
			}
			name = part.FileName()
			f, err := os.OpenFile(zip, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
			if err != nil {
				return "", 0, nil, err
			}
			size, err = io.Copy(f, io.LimitReader(part, max+1))
			if cerr := f.Close(); err == nil {
				err = cerr
			}
			if tooLarge(err) || err == nil && size > max {
				err = errImportTooBig
			}
			if err != nil {
				return "", 0, nil, err
			}
		case "folder", "for", "private":
			b, err := io.ReadAll(io.LimitReader(part, 4<<10))
			if err != nil {
				if tooLarge(err) {
					err = errImportTooBig
				}
				return "", 0, nil, err
			}
			fields[field] = string(b)
		}
		part.Close()
	}
}

// unpackImport unpacks the zip into wi's notes/ (wikiarchive.Unpack,
// every name checked): the notes and files, not the folders a vault
// keeps for itself (importLeftOut, though whether .obsidian/ was there is
// noted) nor links; at most importMaxEntries entries and importUnpackRate
// times upload-max, or what the wiki has room for when that is less (what
// waits isn't counted in storage-max, so it is held to it here). A zip
// holding one folder (a vault zipped as its folder) is that folder's
// notes, named after it; else named zipName.
func unpackImport(wi *waitingImport, zip string, size int64, zipName string) error {
	f, err := os.Open(zip)
	if err != nil {
		return err
	}
	defer f.Close()
	max, full := settings().uploadMax()*importUnpackRate, false
	if s := settings(); s.StorageMax > 0 {
		if room := s.StorageMax - usedBytes(true); room < max {
			max, full = room, true
		}
	}
	if max <= 0 {
		return checkRoom(1) // the wiki is full: its words
	}
	obsidian := map[string]bool{}
	notes := filepath.Join(wi.dir(), "notes")
	if err := os.Mkdir(notes, 0o700); err != nil {
		return err
	}
	_, err = wikiarchive.Unpack(wikiarchive.Zip(f, size), notes, wikiarchive.Rules{
		Not: "not a zip of notes",
		Keep: func(name []string, kind wikiarchive.Kind) (wikiarchive.Take, error) {
			if name[0] == "__MACOSX" || kind == wikiarchive.Other {
				return wikiarchive.Take{}, nil
			}
			for i, part := range name {
				if importLeftOut[part] {
					if part == ".obsidian" {
						obsidian[strings.Join(name[:i], "/")] = true
					}
					return wikiarchive.Take{}, nil
				}
			}
			return wikiarchive.Take{Keep: true, Private: true}, nil
		},
		MaxBytes:   max,
		MaxEntries: importMaxEntries,
	})
	switch {
	case errors.Is(err, wikiarchive.ErrTooBig) && full:
		return refuseAs(refusedFull, "The wiki has no room for what the zip holds: it has room for "+formatSize(max)+" more.")
	case errors.Is(err, wikiarchive.ErrTooBig):
		return refuseAs(refusedTooLarge, "The zip holds too much: it may unpack to at most "+formatSize(max)+".")
	case err != nil:
		// Unpack's words: what is wrong with the archive (a name, a
		// link, not a zip at all, cut off).
		return refuse("Nothing was imported: " + err.Error() + ".")
	}
	wi.Name = zipName
	if entries, err := os.ReadDir(notes); err == nil && len(entries) == 1 && entries[0].IsDir() {
		wi.Root, wi.Name = entries[0].Name(), entries[0].Name()
	}
	wi.Obsidian = obsidian[wi.Root]
	return nil
}

// convertImport: the plan of wi's notes, made with opts (importOptions).
func convertImport(wi waitingImport, opts mdimport.Options) (*mdimport.Plan, error) {
	opts.Name, opts.Obsidian = wi.Name, wi.Obsidian
	plan, err := mdimport.Convert(os.DirFS(wi.notes()), opts)
	if errors.Is(err, mdimport.ErrNoNotes) {
		return nil, refuse("The zip holds no markdown notes (.md files): there is nothing to import.")
	}
	return plan, err
}

// planSize: the bytes a plan writes, its pages and its files, as
// importOp counts them for room.
func planSize(plan *mdimport.Plan, src fs.FS) int64 {
	n := int64(len(plan.Index.Text))
	for _, p := range plan.Pages {
		n += int64(len(p.Text))
	}
	for _, f := range plan.Files {
		if st, err := fs.Stat(src, f.Src); err == nil {
			n += st.Size()
		}
	}
	return n
}

// reportOf: the report of what wi's plan imports.
func reportOf(rq *Req, wi waitingImport, plan *mdimport.Plan) *importReport {
	rep := &importReport{ID: wi.id, Folder: wi.Folder, Name: wi.Name, Obsidian: plan.Report.Obsidian,
		Pages: len(plan.Pages), Files: len(plan.Files)}
	for _, sec := range []struct {
		title string
		lines []string
	}{
		{"Renamed", plan.Report.Renamed},
		{"Links with several targets", plan.Report.Ambiguous},
		{"Links to nothing", plan.Report.Missing},
		{"Lost on the way", plan.Report.Lost},
		{"Left out", plan.Report.LeftOut},
	} {
		if len(sec.lines) == 0 {
			continue
		}
		var b strings.Builder
		for _, l := range sec.lines {
			b.WriteString("- " + l + "\n")
		}
		rep.Sections = append(rep.Sections, importSection{Title: sec.title, HTML: htmlOf(renderMd(rq, b.String(), RenderOpts{}))})
	}
	return rep
}

// importApply: POST /import/apply, Import on the report: the user's
// waiting import (id) written into its folder (importOp, one commit),
// converted again so a file's name is free now; the folder's index
// opens. The waiting folder goes once the commit is made; one that
// failed is left, for Import again. Another's, or one past its hour,
// answers 404.
func importApply(w http.ResponseWriter, r *http.Request) {
	id := r.PostFormValue("id")
	sweepImports()
	user := userOf(r)
	importsMu.Lock()
	wi, ok := userImport(user, id)
	if ok && importsMu.busy[id] {
		ok = false
	}
	if ok {
		if importsMu.busy == nil {
			importsMu.busy = map[string]bool{}
		}
		importsMu.busy[id] = true
	}
	importsMu.Unlock()
	if !ok {
		http.NotFound(w, r)
		return
	}
	defer func() {
		importsMu.Lock()
		delete(importsMu.busy, id)
		importsMu.Unlock()
	}()
	v := &importView{For: "", Folder: wi.Folder}
	opts, err := importOptions(user, wi.Folder)
	if err != nil {
		importFailed(w, r, v, err)
		return
	}
	plan, err := convertImport(wi, opts)
	if err != nil {
		importFailed(w, r, v, err)
		return
	}
	if _, err := importOp(user, wi.Folder, plan, os.DirFS(wi.notes()), wi.Name, ""); err != nil {
		if pe, ok := refusalOf(err); ok {
			v.Report, v.Error = reportOf(newReq(r), wi, plan), pe.msg
			showImport(w, newReq(r), pe.kind.status(), v)
			return
		}
		serverError(w, r, err)
		return
	}
	_ = os.RemoveAll(wi.dir())
	http.Redirect(w, r, pathURL("page", plan.Index.Slug), http.StatusSeeOther)
}

// importCancel: POST /import/cancel, Cancel on the report: the user's
// waiting import removed at once; back to the form.
func importCancel(w http.ResponseWriter, r *http.Request) {
	id := r.PostFormValue("id")
	sweepImports()
	importsMu.Lock()
	wi, ok := userImport(userOf(r), id)
	if ok && !importsMu.busy[id] {
		_ = os.RemoveAll(wi.dir())
	}
	busy := importsMu.busy[id]
	importsMu.Unlock()
	switch {
	case !ok:
		http.NotFound(w, r)
	case busy:
		http.Error(w, "It is being imported now", http.StatusConflict)
	default:
		http.Redirect(w, r, "/import", http.StatusSeeOther)
	}
}
