// Zone's web server: one process, its request checks and routes.
// Run: LEAF_DATA=~/leaf zonewiki [-addr :5000] [-public dir]. Also: zonewiki eval
// measures retrieval (eval.go), zonewiki health asks a running Zone whether
// it answers (the container's healthcheck), zonewiki user and zonewiki config edit
// and check the settings file (commands.go).
package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"mime"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// publicDir holds the browser code and styles (public/), served as is.
var publicDir string

func logf(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "Zone: "+format+"\n", args...)
}

func main() {
	if len(os.Args) > 1 {
		switch os.Args[1] {
		case "eval":
			os.Exit(evalMain(os.Args[2:], os.Stdout, os.Stderr))
		case "health":
			os.Exit(health("http://localhost:5000/api/health"))
		case "user":
			restore := echoOff()
			code := userMain(os.Args[2:], os.Getenv, os.Stdin, os.Stdout, os.Stderr)
			restore()
			os.Exit(code)
		case "config":
			restore := echoOff()
			code := configMain(os.Args[2:], os.Getenv, os.Stdin, os.Stdout, os.Stderr)
			restore()
			os.Exit(code)
		}
	}
	addr := flag.String("addr", ":5000", "the address to listen on (127.0.0.1:0: a free port)")
	public := flag.String("public", "", "the folder of the browser code (default: public/, found from the binary)")
	flag.StringVar(&docsDir, "docs", "", "the folder of README.md and docs/spec/, for leaf_help (default: found from -public)")
	semanticTest := flag.String("semantic-test", "", "the tests' embedder and index state, as JSON (embedtest.go)")
	flag.Parse()
	c, s, err := loadConfig(os.Getenv)
	if err != nil {
		fatal(err)
	}
	cfg = c
	startSettings(s)
	if publicDir = *public; publicDir == "" {
		publicDir = findPublic()
	}
	for _, p := range strayPrivate() {
		logf("%s is in %s/ but no user's folder there: no one sees it (a removed user's?). "+
			"Move it to a user's folder (%s/<name>/) or out of %s/.", p, privateDir, privateDir, privateDir)
	}
	sessionKey = secretKey(cfg.Data)
	gitSetup()
	excludeLeafState(cfg.Data) // the secret key, the caches; not the settings file
	commitSettings("Change settings")
	ensureHomePage()
	cleanLeftDrafts()
	initTasks()
	// The semantic index's worker reads tasks: after the task files are set up.
	if *semanticTest != "" {
		if semantic, err = semanticForTest(*semanticTest); err != nil {
			fatal(fmt.Errorf("-semantic-test: %w", err))
		}
	} else if semantic = semanticFromEnv(os.Getenv); semantic != nil {
		semantic.start()
	}

	// All interfaces by default, so the container's network bridge can
	// forward connections; exposure is the port mapping's (compose.yaml:
	// LEAF_BIND, 127.0.0.1 = localhost only), the Host check the
	// DNS-rebinding defence.
	ln, err := net.Listen("tcp", *addr)
	if err != nil {
		fatal(err)
	}
	srv := &http.Server{
		Handler: newHandler(),
		// A slow or idle client doesn't hold a connection for long.
		ReadHeaderTimeout: 60 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
	fmt.Printf("Zone %s (%s) listening on %s\n", version, cfg.Build, ln.Addr())
	// One process: the task app's lock, the caches, the login throttle and the
	// semantic-search worker live in its memory. At most 100 connections,
	// those beyond the limit wait.
	if err := srv.Serve(limitListener(ln, 100)); err != nil {
		fatal(err)
	}
}

// fatal says what stops Zone from starting (or serving), and exits.
func fatal(err error) {
	fmt.Fprintln(os.Stderr, "Zone:", err)
	os.Exit(1)
}

// health: whether Zone answers at url within 5 seconds, its settings file
// taken (0), or not (1), for the container's healthcheck (compose.yaml),
// which has no curl.
func health(url string) int {
	resp, err := (&http.Client{Timeout: 5 * time.Second}).Get(url)
	if err != nil {
		fmt.Fprintln(os.Stderr, "Zone:", err)
		return 1
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		var answer struct{ Error string }
		if json.NewDecoder(io.LimitReader(resp.Body, 64<<10)).Decode(&answer) == nil && answer.Error != "" {
			fmt.Fprintln(os.Stderr, "Zone:", answer.Error)
		} else {
			fmt.Fprintln(os.Stderr, "Zone:", resp.Status)
		}
		return 1
	}
	return 0
}

// findPublic finds public/ from the binary: next to it (the image) or in
// the checkout it was built in (go/zonewiki → public/).
func findPublic() string {
	exe, err := os.Executable()
	if err != nil {
		return "public"
	}
	dir := filepath.Dir(exe)
	for _, p := range []string{filepath.Join(dir, "public"), filepath.Join(dir, "..", "public")} {
		if isDir(p) {
			return p
		}
	}
	return filepath.Join(dir, "public")
}

// ensureHomePage gives a wiki without index.md (a new one) its home page,
// committed: "/" opens it, and it can't be renamed or deleted.
func ensureHomePage() {
	path := filepath.Join(cfg.Data, "index.md")
	if _, err := os.Stat(path); errors.Is(err, os.ErrNotExist) {
		if os.WriteFile(path, []byte("# Home\n\n"), 0o666) == nil {
			gitCommit("", "Create index", path)
		}
	}
}

func newHandler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /{$}", func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, "/page/index", http.StatusFound)
	})
	mux.HandleFunc("GET /login", login)
	mux.HandleFunc("POST /login", login)
	mux.HandleFunc("POST /logout", logout)
	mux.HandleFunc("GET /api/version", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"version": version, "build": cfg.Build})
	})
	mux.HandleFunc("GET /api/health", apiHealth)
	mux.HandleFunc("POST /api/upload", apiUpload)
	mux.HandleFunc("GET /public/{path...}", servePublic)
	mux.HandleFunc("GET /_assets/{path...}", serveUpload)
	mux.HandleFunc("GET /private/{user}/_assets/{file}", servePrivateUpload)
	mux.HandleFunc("/mcp", mcpEndpoint)
	pageRoutes(mux)
	historyRoutes(mux)
	tasksRoutes(mux)
	return checks(mux)
}

// apiHealth: Zone answers, and took its settings file's last change (503
// with the error if not: Zone runs on the settings from before), for leaf
// health. It needs no login, and the error may name users: the error
// itself only to whoever may read the wiki, everyone else is pointed to
// the log.
func apiHealth(w http.ResponseWriter, r *http.Request) {
	if msg := settingsError(); msg != "" {
		if _, ok, _ := signedIn(r); settings().authOn() && !ok {
			msg = configName + " has an error, or a change Zone doesn't take while it runs: see Zone's log " +
				"(./run.sh logs), or run zonewiki config check"
		}
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": msg})
		return
	}
	writeJSON(w, http.StatusOK, map[string]bool{"ok": true})
}

// checks runs the request checks before every route (Host, Origin, the
// login gate) and gives every response the page policy.
func checks(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w = &policyWriter{ResponseWriter: w}
		reloadSettings()
		if !hostAllowed(r.Host) {
			http.Error(w, "Bad Request", http.StatusBadRequest)
			return
		}
		if dotSegments(r.URL.Path) {
			// "/page/../x": Flask's routes refused such a slug (400); Go's
			// router would redirect to the cleaned path instead.
			http.Error(w, "Bad Request", http.StatusBadRequest)
			return
		}
		if crossSite(r) {
			http.Error(w, "Cross-site request refused", http.StatusForbidden)
			return
		}
		if settings().authOn() && !authExempt(r) {
			user, ok, renew := signedIn(r)
			if !ok {
				http.Redirect(w, r, "/login?next="+url.QueryEscape(r.URL.Path), http.StatusFound)
				return
			}
			if renew {
				setSession(w, r, user)
			}
			r = withUser(r, user)
		}
		next.ServeHTTP(w, r)
	})
}

// dotSegments: a path with a "." or ".." segment.
func dotSegments(p string) bool {
	for _, part := range strings.Split(p, "/") {
		if part == "." || part == ".." {
			return true
		}
	}
	return false
}

// authExempt: what needs no login. /mcp checks its own bearer token (an
// MCP client can't log in); the browser code and the home-screen app's
// files may be fetched without the session cookie, and the healthcheck
// asks without one.
func authExempt(r *http.Request) bool {
	switch p := r.URL.Path; {
	case p == "/login", p == "/logout", p == "/mcp", p == "/api/health":
		return true
	case strings.HasPrefix(p, "/public/"):
		return r.Method == "GET" || r.Method == "HEAD"
	}
	return false
}

// policyWriter gives a response the page policy's headers as it starts.
type policyWriter struct {
	http.ResponseWriter
	started bool
}

func (p *policyWriter) WriteHeader(status int) {
	if !p.started {
		p.started = true
		contentPolicy(p.Header())
	}
	p.ResponseWriter.WriteHeader(status)
}

func (p *policyWriter) Write(b []byte) (int, error) {
	if !p.started {
		p.WriteHeader(http.StatusOK)
	}
	return p.ResponseWriter.Write(b)
}

func (p *policyWriter) Unwrap() http.ResponseWriter { return p.ResponseWriter }

// writeJSON answers with v as JSON.
func writeJSON(w http.ResponseWriter, status int, v any) {
	b, err := json.Marshal(v)
	if err != nil {
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(append(b, '\n'))
}

// isLocalPath: url is a same-origin path. "//host" and "/\host" start with
// "/" but browsers resolve them to another origin, so they don't count.
func isLocalPath(u string) bool {
	return strings.HasPrefix(u, "/") && !strings.HasPrefix(u, "//") && !strings.HasPrefix(u, "/\\")
}

func login(w http.ResponseWriter, r *http.Request) {
	s := settings()
	if !s.authOn() {
		http.Redirect(w, r, "/", http.StatusFound)
		return
	}
	errorText, status := "", http.StatusOK
	if r.Method == "POST" {
		key := clientKey(r)
		if wait := loginThrottle.wait(key); wait > 0 {
			// Refused unchecked, even the right password: guessing doesn't
			// go on in the meantime.
			errorText, status = waitMessage(wait), http.StatusTooManyRequests
		} else if user, ok := checkLogin(r); ok {
			loginThrottle.succeeded(key)
			setSession(w, r, user)
			next := r.URL.Query().Get("next")
			if !r.URL.Query().Has("next") || !isLocalPath(next) {
				next = "/"
			}
			http.Redirect(w, r, next, http.StatusFound)
			return
		} else {
			loginThrottle.failed(key)
			errorText = "Incorrect password."
			if s.Users != nil {
				errorText = "Incorrect user name or password."
			}
		}
	}
	page(w, newReq(r), status, "login.html", "Sign in", shell{noActions: true}, M{
		"error": errorText, "next_url": r.URL.Query().Get("next"), "badge_in_form": true,
		"users": s.Users != nil, "username": r.PostFormValue("username")})
}

// checkLogin: the login form's password is right, for the user it names
// (with users) or the wiki's password (without).
func checkLogin(r *http.Request) (user string, ok bool) {
	s := settings()
	if s.Users == nil {
		return "", checkPassword(r.PostFormValue("password"), s.PasswordHash)
	}
	if u := authenticate(r.PostFormValue("username"), r.PostFormValue("password")); u != nil {
		return u.Name, true
	}
	return "", false
}

func logout(w http.ResponseWriter, r *http.Request) {
	clearSession(w, r)
	http.Redirect(w, r, "/login", http.StatusFound)
}

// servePublic serves the browser code (public/): files only, nothing
// outside the folder.
func servePublic(w http.ResponseWriter, r *http.Request) {
	serveFile(w, r, publicDir, r.PathValue("path"), "")
}

// serveFile serves name from dir (a file, never a folder or anything
// outside dir); a disposition ("attachment") makes it a download.
func serveFile(w http.ResponseWriter, r *http.Request, dir, name, disposition string) {
	for _, part := range strings.Split(name, "/") {
		if part == "" || part == "." || part == ".." || strings.ContainsRune(part, 0) {
			http.NotFound(w, r)
			return
		}
	}
	f, err := os.Open(filepath.Join(dir, filepath.FromSlash(name)))
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
// none), so uploads of them are shown inline, as Python's table has them.
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
func saveUpload(w http.ResponseWriter, r *http.Request, src io.Reader, owner, filename string) bool {
	dest := filepath.Join(uploadDir(owner), filename)
	uploadMu.Lock()
	defer uploadMu.Unlock()
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
	gitCommit(userOf(r), "Upload "+name, dest)
	return true
}

// baseName is a path's last part, as Python's PurePosixPath.name.
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

// suffix is a file name's extension, as Python's PurePath.suffix: "" for
// ".bashrc", "." for "name.".
func suffix(name string) string {
	i := strings.LastIndex(name, ".")
	if i <= 0 || strings.Trim(name, ".") == "" {
		return ""
	}
	return name[i:]
}

// limitListener accepts at most n connections at once; more wait.
func limitListener(l net.Listener, n int) net.Listener {
	return &limited{Listener: l, sem: make(chan struct{}, n)}
}

type limited struct {
	net.Listener
	sem chan struct{}
}

func (l *limited) Accept() (net.Conn, error) {
	l.sem <- struct{}{}
	c, err := l.Listener.Accept()
	if err != nil {
		<-l.sem
		return nil, err
	}
	return &limitedConn{Conn: c, release: func() { <-l.sem }}, nil
}

type limitedConn struct {
	net.Conn
	once    sync.Once
	release func()
}

func (c *limitedConn) Close() error {
	err := c.Conn.Close()
	c.once.Do(c.release)
	return err
}
