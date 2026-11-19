// Files for assistants: the uploads in _assets/ (uploads.go), read and
// added over MCP (read_file, upload_file) and, for a file too big to pass
// through a tool call, over HTTP with the MCP token in place of a session
// (POST /api/upload, GET /_assets/…).
package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"unicode/utf8"
)

// What passes through a tool call: the whole file is in the assistant's
// context, as text or base64, so the tools take and give small files only.
// A bigger one goes over HTTP (fileRoutesHint).
const (
	toolUploadMax = 1 << 20   // upload_file
	toolImageMax  = 4 << 20   // read_file: an image, as the model sees it
	toolTextMax   = 200 << 10 // read_file: a text file; more is cut off
)

// fileRoutesHint: how an assistant with a shell moves a file of any size.
func fileRoutesHint(base string) string {
	return "With a shell, a file of any size goes over HTTP with your MCP token: upload with " +
		"`curl -H 'Authorization: Bearer <token>' -F file=@NAME " + base + "/api/upload` (add `-F page=SLUG` for a private page's, `-F task=ID` for a task's; " +
		"the answer is {\"url\": …}), download with `curl -H 'Authorization: Bearer <token>' -o NAME " + base + "<url>`."
}

// uploadFileTool: a file into the uploads, from its text or its bytes as
// base64; the answer is the markdown that links it. As the browser's upload
// (apiUpload), but it asks nobody: a taken name gets a number.
func uploadFileTool(args jsonObj, base string, user asker) (string, error) {
	name, err := strArg(args, "filename", true)
	if err != nil {
		return "", err
	}
	if name = uploadName(name); name == "" {
		return "", toolErr("filename must be a file's name, e.g. 'report.pdf'")
	}
	text, err := strArg(args, "text", false)
	if err != nil {
		return "", err
	}
	encoded, err := strArg(args, "content_base64", false)
	if err != nil {
		return "", err
	}
	if args.Has("text") == args.Has("content_base64") {
		return "", toolErr("Give the file as text (a text file) or as content_base64 (any file), one of the two")
	}
	data := []byte(text)
	if args.Has("content_base64") {
		clean := strings.Map(func(r rune) rune {
			if r == '\n' || r == '\r' || r == ' ' || r == '\t' {
				return -1
			}
			return r
		}, encoded)
		if data, err = base64.StdEncoding.DecodeString(clean); err != nil {
			if data, err = base64.RawStdEncoding.DecodeString(strings.TrimRight(clean, "=")); err != nil {
				return "", toolErr("content_base64 isn't base64")
			}
		}
	}
	if len(data) == 0 {
		return "", toolErr("The file is empty")
	}
	if limit := min(int64(toolUploadMax), settings().uploadMax()); int64(len(data)) > limit {
		return "", toolErr("Too large for this tool: at most " + formatSize(limit) + ". " + fileRoutesHint(base))
	}
	page, err := strArg(args, "page", false)
	if err != nil {
		return "", err
	}
	owner, ours := uploadPlace(user, page)
	// page names a task by its id: the file is the task's.
	if id, isTask, err := uploadTaskRef(user, page); err != nil {
		return "", err
	} else if isTask {
		var ok bool
		if owner, ok = taskUploadPlace(user, id); !ok {
			return "", toolErr("No task with this id")
		}
	} else if !ours {
		return "", toolErr("Nothing uploaded: " + notYours(strings.Trim(page, "/"), user))
	}
	name = freeUploadName(owner, name)
	if err := storeUpload(user, owner, name, bytes.NewReader(data), mcpMark(args)); err != nil {
		return "", err
	}
	link := "[" + name + "](" + uploadURL(owner, name) + ")"
	if strings.HasPrefix(mediaType(name), "image/") {
		link = "!" + link
	}
	where := "everyone who uses the wiki can open it"
	switch owner.kind {
	case ofUser:
		where = "private: only " + owner.name + " can open it"
	case ofGroup:
		where = "the group's: only who is in @" + owner.name + " can open it"
	case ofTask:
		where = "the task's: who sees the task can open it"
	}
	return "Uploaded " + name + " (" + formatSize(int64(len(data))) + "; " + where + "). " +
		"It shows on a page or in a task once linked there, as markdown:\n" + link, nil
}

// shortTaskRefRE: what upload_file's page takes for a task's id short of
// the whole one: the 8-character short id the tools show, or more of it.
var shortTaskRefRE = regexp.MustCompile(`^[0-9a-f]{8}[0-9a-f-]{0,28}$`)

// uploadTaskRef: the task upload_file's page names, if it names one: its
// whole id; the short id the tools show (or more of the id), a
// [[task:…]] reference or a /tasks?task=… link, when no page goes by the
// name, resolved among the tasks user sees (none, or several, refused:
// never a file for a task the assistant meant left with everyone's).
func uploadTaskRef(user asker, page string) (id string, isTask bool, err error) {
	ref := strings.ToLower(strings.TrimSpace(page))
	if uuid4RE.MatchString(ref) {
		return ref, true, nil
	}
	if !shortTaskRefRE.MatchString(ref) && !taskLinkRE.MatchString(ref) {
		return "", false, nil
	}
	if p := safePath(strings.Trim(page, "/")); p != "" && exists(p) {
		return "", false, nil // a page by that name: the file is for it
	}
	t, err := findTask(ref, user)
	if err != nil {
		return "", false, err
	}
	return t.ID, true, nil
}

var (
	sharedUploadRE  = regexp.MustCompile(`^/_assets/([^/\\]+)$`)
	privateUploadRE = regexp.MustCompile(`^/(` + keptDirs + `)/([^/\\]+)/_assets/([^/\\]+)$`)
	taskUploadRE    = regexp.MustCompile(`^/` + taskFilesDir + `/([^/\\]+)/_assets/([^/\\]+)$`)
)

// uploadFile: the file a link's target names (as a page has it:
// "/_assets/My%20Deck.pdf", with or without the wiki's address before it),
// for user: a shared upload, one of their own private ones, or one of a
// group they are in. ok false: no such file, as for one that isn't theirs.
func uploadFile(target string, user asker) (path, name string, ok bool) {
	if u, err := url.Parse(strings.TrimSpace(target)); err == nil {
		target = u.Path // unescaped, without address, query and fragment
	}
	var owner place
	if m := sharedUploadRE.FindStringSubmatch(target); m != nil {
		name = m[1]
	} else if m := privateUploadRE.FindStringSubmatch(target); m != nil {
		// Its place as its path says it (the name as written: "Alice" is no one's folder here).
		if p := placeOf(m[1] + "/" + m[2] + "/_assets"); p.name == m[2] && p.theirs(user) {
			owner, name = p, m[3]
		}
	} else if m := taskUploadRE.FindStringSubmatch(target); m != nil {
		if p := taskPlace(m[1]); p.sees(user) {
			owner, name = p, m[2]
		}
	}
	if name == "" || strings.HasPrefix(name, ".") {
		return "", "", false
	}
	path = filepath.Join(uploadDir(owner), name)
	if st, err := os.Stat(path); err != nil || !st.Mode().IsRegular() {
		return "", "", false
	}
	return path, name, true
}

// toolImages: the image types a model is shown (SVG is text).
var toolImages = map[string]bool{"image/png": true, "image/jpeg": true, "image/gif": true, "image/webp": true}

// readFileTool: an upload for the assistant: an image as an image, a text
// file as its text; anything else (a PDF, an archive) is only named, with
// the way to fetch it.
func readFileTool(args jsonObj, base string, user asker) ([]toolContent, error) {
	target, err := strArg(args, "url", true)
	if err != nil {
		return nil, err
	}
	path, name, ok := uploadFile(target, user)
	if !ok {
		// Not naming it: one that isn't the user's is no file, like one that isn't there.
		return nil, toolErr("No such file: give a link's target as the page or task has it, e.g. '/_assets/photo.png'")
	}
	st, err := os.Stat(path)
	if err != nil {
		return nil, err
	}
	kind, size := mediaType(name), st.Size()
	about := name + " (" + kind + ", " + formatSize(size) + ")"
	if toolImages[kind] {
		if size > toolImageMax {
			return textContent(about + ": too large to show here (at most " + formatSize(toolImageMax) + "). " + fileRoutesHint(base)), nil
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return nil, err
		}
		return []toolContent{{Type: "text", Text: about},
			{Type: "image", Data: base64.StdEncoding.EncodeToString(data), MimeType: kind}}, nil
	}
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	head := make([]byte, toolTextMax+utf8.UTFMax)
	n, _ := f.Read(head)
	head = head[:n]
	cut := size > toolTextMax
	if cut {
		head = head[:toolTextMax]
		for len(head) > 0 && !utf8.Valid(head) { // not in the middle of a character
			head = head[:len(head)-1]
		}
	}
	if !utf8.Valid(head) || bytes.IndexByte(head, 0) >= 0 {
		return textContent(about + ": not text or an image, so it can't be shown here. " + fileRoutesHint(base)), nil
	}
	text := about + ":\n\n" + string(head)
	if cut {
		text += "\n\n[cut off after " + formatSize(toolTextMax) + ". " + fileRoutesHint(base) + "]"
	}
	return textContent(text), nil
}

// ---- the file routes with a token ----

// tokenForFiles: the request is an upload or the download of one, and
// brings a bearer token: an assistant's, not a browser's.
func tokenForFiles(r *http.Request) bool {
	if !strings.HasPrefix(r.Header.Get("Authorization"), "Bearer ") {
		return false
	}
	p := r.URL.Path
	switch r.Method {
	case http.MethodPost:
		return p == "/api/upload"
	case http.MethodGet, http.MethodHead:
		return sharedUploadRE.MatchString(p) || privateUploadRE.MatchString(p) || taskUploadRE.MatchString(p)
	}
	return false
}

type byTokenKey struct{}

// byToken: the request came with an MCP token, not a session.
func byToken(r *http.Request) bool {
	ok, _ := r.Context().Value(byTokenKey{}).(bool)
	return ok
}

// tokenRequest: r as its token's user, as /mcp takes the token (mcpEndpoint):
// a wrong one is 401 and counts against the client like a wrong password.
// ok false: answered.
func tokenRequest(w http.ResponseWriter, r *http.Request) (*http.Request, bool) {
	user, via := asker{}, ""
	ok, wait := mcpThrottle.try(clientKey(r), func() (ok bool) {
		user, via, ok = mcpAuthorized(r)
		return ok
	})
	if wait > 0 {
		w.Header().Set("Retry-After", itoa(int(wait.Seconds())+1))
		writeJSON(w, http.StatusTooManyRequests, map[string]string{"error": waitMessage(wait)})
		return r, false
	}
	if !ok {
		w.Header().Set("WWW-Authenticate", `Bearer error="invalid_token"`)
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "Unauthorized: wrong bearer token"})
		return r, false
	}
	r = withAsker(r, user, via)
	return r.WithContext(context.WithValue(r.Context(), byTokenKey{}, true)), true
}
