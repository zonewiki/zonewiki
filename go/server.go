// Zone's web server: one process, its request checks and routes.
// Run: ZONE_DATA=~/zonewiki zonewiki [--addr :5000] [--public dir]. Also: zonewiki eval
// measures retrieval (eval.go), zonewiki health asks a running Zone whether
// it answers (the container's healthcheck), zonewiki user and zonewiki config edit
// and check the settings file (commands.go).
package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
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

const usage = `Usage: zonewiki [--addr ADDRESS] [--public FOLDER] [--docs FOLDER]
  serve the wiki in ZONE_DATA (http://localhost:5000)

Commands:
  user COMMAND               the wiki's users (add, passwd, token, remove, list)
  config COMMAND             the wiki's settings file .zone/config (check, set, unset, passwd, token)
  eval [--k N] [--new] FILE  measure search_wiki's retrieval against the questions in FILE
  health [ADDR]              whether a Zone running here answers (the container's healthcheck)
  help                       this text; zonewiki COMMAND --help a command's

Environment:
  ZONE_DATA                  the wiki's folder (required; it must exist)
  ZONE_ALLOW_REMOTE          1: answer to every host name, not only localhost (unless hosts is set)
  ZONE_BIND                  the address the container's port is published on (compose sets it): not
                             localhost's, and no login yet, Zone asks for a password in the browser first
  ZONE_HOST_DATA             the wiki's folder as the host sees it, for the MCP tools' file: lines
  ZONE_SEMANTIC_SEARCH       on: semantic search besides keywords (needs onnxruntime)
  ZONE_MODEL_DIR             where the semantic model's files are
  ZONE_BUILD                 the build shown next to the version

Flags:`

// printUsage: the usage text, then the flags.
func printUsage() {
	fmt.Fprintln(flag.CommandLine.Output(), usage)
	printFlags(flag.CommandLine)
}

// printFlags: fs's flags as --name VALUE and their usage, in a column with
// the commands' (flag.PrintDefaults would show them with one dash, which
// works too).
func printFlags(fs *flag.FlagSet) {
	fs.VisitAll(func(f *flag.Flag) {
		value, text := flag.UnquoteUsage(f)
		name := "--" + f.Name
		if value != "" {
			name += " " + strings.ToUpper(value)
		}
		if f.DefValue != "" && f.DefValue != "false" {
			text += " (default " + f.DefValue + ")"
		}
		fmt.Fprintf(fs.Output(), "  %-26s %s\n", name, text)
	})
}

// isHelp: whether arg asks for a command's usage.
func isHelp(arg string) bool {
	return arg == "-h" || arg == "-help" || arg == "--help" || arg == "help"
}

func main() {
	addr := flag.String("addr", ":5000", "the `address` to listen on (127.0.0.1:0: a free port)")
	public := flag.String("public", "", "the `folder` of the browser code (default: public/, found from the binary)")
	flag.StringVar(&docsDir, "docs", "", "the `folder` of README.md and docs/spec/, for zone_help (default: found from --public)")
	semanticTest := flag.String("semantic-test", "", "the tests' embedder and index state, as `JSON` (embedtest.go)")
	flag.Usage = printUsage
	if len(os.Args) > 1 {
		switch os.Args[1] {
		case "help":
			flag.CommandLine.SetOutput(os.Stdout)
			printUsage()
			os.Exit(0)
		case "eval":
			os.Exit(evalMain(os.Args[2:], os.Stdout, os.Stderr))
		case "health":
			if len(os.Args) > 2 && isHelp(os.Args[2]) {
				fmt.Println(healthUsage)
				os.Exit(0)
			}
			os.Exit(health(healthURL(os.Args[2:])))
		case "user":
			restore := echoOff()
			code := userMain(os.Args[2:], environ, os.Stdin, os.Stdout, os.Stderr)
			restore()
			os.Exit(code)
		case "config":
			restore := echoOff()
			code := configMain(os.Args[2:], environ, os.Stdin, os.Stdout, os.Stderr)
			restore()
			os.Exit(code)
		}
	}
	flag.Parse()
	c, s, err := loadConfig(environ)
	if err != nil {
		fatal(err)
	}
	cfg = c
	startSettings(s)
	if publicDir = *public; publicDir == "" {
		publicDir = findPublic()
	}
	if needsSetup() {
		logf("other devices can reach this wiki and it has no login: Zone asks for a password in the browser " +
			"before it shows anything (/setup). To run it without a login on purpose: zonewiki config set no-login yes")
	}
	for _, p := range strayPrivate() {
		logf("%s is in %s/ but no user's folder there: no one sees it (a removed user's?). "+
			"Move it to a user's folder (%s/<name>/) or out of %s/.", p, privateDir, privateDir, privateDir)
	}
	sessionKey = secretKey(cfg.Data)
	gitSetup()
	if err := missingUsers(); err != nil {
		fatal(err)
	}
	excludeState(cfg.Data) // .zone/: the settings file, the secret key, the caches
	ensureHomePage()
	cleanLeftDrafts()
	initTasks()
	// The semantic index's worker reads tasks: after the task files are set up.
	if *semanticTest != "" {
		if semantic, err = semanticForTest(*semanticTest); err != nil {
			fatal(fmt.Errorf("--semantic-test: %w", err))
		}
	} else if semantic = semanticFromEnv(environ); semantic != nil {
		semantic.start()
	}

	// All interfaces by default, so the container's network bridge can
	// forward connections; exposure is the port mapping's (compose.yaml:
	// ZONE_BIND, 127.0.0.1 = localhost only), the Host check the
	// DNS-rebinding defence.
	ln, err := net.Listen("tcp", *addr)
	if err != nil {
		fatal(err)
	}
	srv := &http.Server{
		Handler: newHandler(),
		// A slow or idle client doesn't hold a connection for long: the
		// headers come in 10 s, and a body or a response that stands
		// still for a minute fails (stalls.go).
		ReadHeaderTimeout: 10 * time.Second,
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

const healthUsage = `Usage: zonewiki health [ADDR]
  whether the Zone listening on ADDR (its --addr, default :5000) answers on localhost
  and took its settings file: exit code 0, or 1`

// healthURL: /api/health on localhost at the port of zonewiki health's
// argument, the --addr Zone listens on (":8080"; default :5000).
func healthURL(args []string) string {
	port := "5000"
	if len(args) > 0 {
		if _, p, err := net.SplitHostPort(args[0]); err == nil && p != "" {
			port = p
		}
	}
	return "http://localhost:" + port + "/api/health"
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
	mux.HandleFunc("GET /setup", setupRoute)
	mux.HandleFunc("POST /setup", setupRoute)
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
	adminRoutes(mux)
	assistantRoutes(mux)
	return noStalls(checks(viewerGate(mux)))
}

// viewerWrites: the routes taking a POST (or another write method) that a
// viewer may use: none of them changes the wiki. Every other write is
// refused to a viewer before it runs (viewerGate), so a new route is
// closed to them until it is listed here (tests/test_roles.py probes each).
// /mcp refuses a viewer's writing tools itself (callMCPTool).
var viewerWrites = map[string]bool{
	"POST /login": true, "POST /logout": true, "POST /welcome/{token}": true, "/mcp": true,
	"POST /api/preview": true, "POST /api/render-notes": true, // render only
	"POST /assistant/token": true, "POST /assistant/revoke": true, // their own MCP token, whose writing tools are refused
}

// viewerGate refuses a viewer's write (POST, PUT, PATCH, DELETE) to a
// route not in viewerWrites: 403, before the route runs.
func viewerGate(mux *http.ServeMux) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case "POST", "PUT", "PATCH", "DELETE":
			if settings().isViewer(userOf(r)) {
				if _, pattern := mux.Handler(r); pattern != "" && !viewerWrites[pattern] {
					http.Error(w, readOnlyMsg, http.StatusForbidden)
					return
				}
			}
		}
		mux.ServeHTTP(w, r)
	})
}

// apiHealth: Zone answers, and took its settings file's last change (503
// with the error if not: Zone runs on the settings from before), for
// zonewiki health. It needs no login, and the error may name users: the error
// itself only to whoever may read the wiki, everyone else is pointed to
// the log.
func apiHealth(w http.ResponseWriter, r *http.Request) {
	if msg := settingsError(); msg != "" {
		if _, ok, _ := signedIn(r); settings().authOn() && !ok {
			msg = configName() + " has an error, or a change Zone doesn't take while it runs: see Zone's log " +
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
		beginRequest()
		defer endRequest()
		if !hostAllowed(r.Host) {
			http.Error(w, "Bad Request", http.StatusBadRequest)
			return
		}
		if dotSegments(r.URL.Path) {
			// "/page/../x": refused (400), never resolved; Go's
			// router would redirect to the cleaned path instead.
			http.Error(w, "Bad Request", http.StatusBadRequest)
			return
		}
		if crossSite(r) {
			http.Error(w, "Cross-site request refused", http.StatusForbidden)
			return
		}
		limit := int64(bodyMax)
		if r.URL.Path == "/api/upload" {
			limit = settings().uploadMax()
		}
		if r.ContentLength > limit {
			tooLargeError(w, limit)
			return
		}
		r.Body = http.MaxBytesReader(w, r.Body, limit)
		if needsSetup() && !setupExempt(r) {
			// Reachable from other devices, and no login yet: nothing is
			// shown or changed before one is set (setup.go).
			if r.Method == "GET" && !strings.HasPrefix(r.URL.Path, "/api/") {
				http.Redirect(w, r, "/setup", http.StatusFound)
			} else {
				http.Error(w, setupFirstMsg, http.StatusForbidden)
			}
			return
		}
		if settings().authOn() && !authExempt(r) {
			user, ok, renew := signedIn(r)
			if !ok {
				http.Redirect(w, r, "/login?next="+url.QueryEscape(r.URL.Path), http.StatusFound)
				return
			}
			if renew {
				renewSession(w, r)
			}
			r = withUser(r, user)
		}
		next.ServeHTTP(w, r)
	})
}

// A request's body is at most bodyMax (a page, a task, an MCP call), an
// upload's upload-max (100 MB unless set; Settings.uploadMax): a bigger one
// gets 413 before it is read, or, sent without a length, a read error at
// the limit (tooLarge).
const bodyMax = 16 << 20

// tooLarge: err is a body read past its limit.
func tooLarge(err error) bool {
	var e *http.MaxBytesError
	return errors.As(err, &e)
}

// tooLargeError answers a body over limit with 413.
func tooLargeError(w http.ResponseWriter, limit int64) {
	http.Error(w, "Too large: at most "+formatSize(limit), http.StatusRequestEntityTooLarge)
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
// MCP client can't log in), a welcome link its own token; the browser code and the home-screen app's
// files may be fetched without the session cookie, and the healthcheck
// asks without one.
func authExempt(r *http.Request) bool {
	switch p := r.URL.Path; {
	case p == "/login", p == "/logout", p == "/mcp", p == "/api/health", p == "/setup":
		return true
	case strings.HasPrefix(p, "/welcome/"):
		return true // an invite or reset link: the person has no password yet (links.go)
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
