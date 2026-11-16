// Zone's web server: one process, its request checks and routes.
// Run: ZONE_DATA=~/zonewiki zonewiki [--addr :5000] [--public dir]. Also: zonewiki eval
// measures retrieval (eval.go), zonewiki health asks a running Zone whether
// it answers (the container's healthcheck), zonewiki user and zonewiki config edit
// and check the settings file (commands.go).
package main

import (
	"bytes"
	"compress/gzip"
	"context"
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
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// publicDir holds the browser code and styles (public/), served as is.
var publicDir string

func logf(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "Zone: "+format+"\n", args...)
}

const usage = `Usage: zonewiki [--addr ADDRESS] [--public FOLDER] [--docs FOLDER]
  serve the wiki in ZONE_DATA (http://localhost:5000; --addr unix:PATH: on a Unix socket,
  for a reverse proxy on this machine)

Commands:
  user COMMAND               the wiki's users (add, passwd, token, remove, list)
  group COMMAND              the wiki's groups made of groups (includes, list)
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
  ZONE_PUBLIC                view: anyone reads the shared pages without signing in; edit: and edits
                             them (needs users; no tasks, notes or private pages for visitors)
  ZONE_SIGNUP                1: people may make an account at /signup (needs mail, users, base-url)
  ZONE_SMTP_HOST, _PORT      the mail server Zone sends sign-up mail through (port default 587)
  ZONE_SMTP_USER, _PASSWORD  signing in to it (only over TLS, or to this machine)
  ZONE_SMTP_TLS              starttls (default), tls (465) or none
  ZONE_MAIL_FROM             the address Zone's mail comes from
  ZONE_BUILD                 the build shown next to the version
  ZONE_IDLE_EXIT             15m: exit after that long without a request, for a Zone systemd starts
                             on its socket (--addr fd:3) when the next one comes

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
	addr := flag.String("addr", ":5000", "the `address` to listen on (127.0.0.1:0: a free port; unix:PATH: a Unix socket; fd:3: the socket systemd hands over)")
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
			os.Exit(health(healthClient(os.Args[2:]), healthURL(os.Args[2:])))
		case "user":
			restore := echoOff()
			code := userMain(os.Args[2:], environ, os.Stdin, os.Stdout, os.Stderr)
			restore()
			os.Exit(code)
		case "group":
			os.Exit(groupMain(os.Args[2:], environ, os.Stdout, os.Stderr))
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
	for name := range reservedNames {
		if s.Users[name] != nil {
			logf("The user %q can't be given tasks: the name is a word for a task's assignee (me: whoever is signed in, none: no one).", name)
		}
	}
	if publicDir = *public; publicDir == "" {
		publicDir = findPublic()
	}
	if needsSetup() {
		logf("other devices can reach this wiki and it has no login: Zone asks for a password in the browser " +
			"before it shows anything (/setup). To run it without a login on purpose: zonewiki config set no-login yes")
	}
	if why := signupOffReason(s); why != "" {
		logf("%s", why)
	} else if signupOn(s) {
		logf("sign-up is on: anyone%s can make an account at %s/signup, as %s", signupWho(s), s.BaseURL, s.SignupRole)
	}
	if cfg.Public != "" && s.Users[visitorAuthor] != nil {
		logf("The user %q has the name a public wiki's visitors commit under (ZONE_PUBLIC): the history "+
			"can't tell their changes from a visitor's. Give them another name.", visitorAuthor)
	}
	switch {
	case cfg.Public != "" && s.Users == nil:
		logf("ZONE_PUBLIC=%s does nothing yet: a public wiki needs users, to tell its visitors from "+
			"(zonewiki user add NAME). Until then the wiki is as its settings file says.", cfg.Public)
	case cfg.Public == publicView:
		logf("this wiki is public: anyone reads its shared pages without signing in (ZONE_PUBLIC=view)")
	case cfg.Public == publicEdit:
		logf("this wiki is public: anyone reads and edits its shared pages without signing in (ZONE_PUBLIC=edit)")
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
	noDeltaForUploads(cfg.Data)
	ensureHomePage()
	cleanLeftDrafts()
	setUpTasks()  // the task files as this Zone keeps them (tasksetup.go)
	packAtStart() // the repository's loose objects, in the background (gitstore.go)
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
	// DNS-rebinding defence. Or a Unix socket, for a reverse proxy.
	ln, err := listen(*addr)
	if err != nil {
		fatal(err)
	}
	srv := &http.Server{
		Handler: countRequests(newHandler()),
		// A slow or idle client doesn't hold a connection for long: the
		// headers come in 10 s, and a body or a response that stands
		// still for a minute fails (stalls.go).
		ReadHeaderTimeout: 10 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
	fmt.Printf("Zone %s (%s) listening on %s\n", version, cfg.Build, listenName(ln))
	switch {
	case cfg.IdleExit == 0:
	case !handedSocket:
		logf("ZONE_IDLE_EXIT is set, but Zone opened its own socket (--addr %s): nothing would start it again, so it stays. "+
			"It is for a Zone that systemd starts on a socket it holds (--addr fd:3).", *addr)
	default:
		idle.last.Store(now().UnixNano())
		go exitWhenIdle(srv, cfg.IdleExit)
	}
	// One process: the task app's lock, the caches, the login throttle and the
	// semantic-search worker live in its memory. At most 100 connections,
	// those beyond the limit wait.
	err = srv.Serve(limitListener(ln, 100))
	if errors.Is(err, http.ErrServerClosed) {
		select {} // exitWhenIdle is ending Zone, once what still runs is answered
	}
	fatal(err)
}

// socketPrefix: an --addr that names a Unix socket's path, not a TCP address.
const socketPrefix = "unix:"

// onSocket: Zone listens on a Unix socket. Its peers have no address: each
// is the reverse proxy the socket is for (clientAddr).
var onSocket bool

// listen: the listener of --addr, a TCP address or unix:PATH. A socket is
// for a reverse proxy on the same machine, with no port for anyone else: who
// may connect is who may reach the path, so the socket itself is open to all
// (0666, as a port on localhost is to every local user) and the folder it is
// in decides. A socket file a Zone that was killed left is replaced; one
// something still listens on, or a file that is no socket, is refused.
func listen(addr string) (net.Listener, error) {
	if fd, handed := strings.CutPrefix(addr, fdPrefix); handed {
		return handedListener(fd)
	}
	path, socket := strings.CutPrefix(addr, socketPrefix)
	if !socket {
		return net.Listen("tcp", addr)
	}
	if path == "" {
		return nil, errors.New("--addr unix: takes the socket's path, as unix:/run/zone/zone.sock")
	}
	if st, err := os.Lstat(path); err == nil {
		if st.Mode()&os.ModeSocket == 0 {
			return nil, fmt.Errorf("--addr %s: %s is there and is no socket", addr, path)
		}
		if c, err := net.DialTimeout("unix", path, time.Second); err == nil {
			c.Close()
			return nil, fmt.Errorf("--addr %s: something listens on %s already", addr, path)
		}
		if err := os.Remove(path); err != nil {
			return nil, err
		}
	}
	ln, err := net.Listen("unix", path)
	if err != nil {
		return nil, err
	}
	if err := os.Chmod(path, 0o666); err != nil {
		ln.Close()
		return nil, err
	}
	onSocket = true
	return ln, nil
}

// fdPrefix: an --addr that names a listening socket Zone was started with,
// by its file descriptor: fd:3 is the first one systemd hands a service it
// starts for a socket unit (socket activation), also into a container.
const fdPrefix = "fd:"

// handedSocket: Zone listens on a socket it was handed (--addr fd:N), not
// one it opened: whoever holds it starts Zone again when a connection
// comes, so Zone may exit while nobody asks (ZONE_IDLE_EXIT).
var handedSocket bool

// handedListener: the listener on file descriptor fd, which must be a
// listening socket. A Unix socket's peers are the proxy's, as with
// unix:PATH; its file is the holder's, and stays when Zone closes it.
func handedListener(fd string) (net.Listener, error) {
	n, err := strconv.Atoi(fd)
	if err != nil || n < 3 {
		return nil, fmt.Errorf("--addr fd:%s: it takes the number of a file descriptor Zone was started with, as fd:3", fd)
	}
	f := os.NewFile(uintptr(n), "socket "+fd)
	if f == nil {
		return nil, fmt.Errorf("--addr fd:%s: Zone was started without that file descriptor", fd)
	}
	defer f.Close() // the listener has its own copy
	ln, err := net.FileListener(f)
	if err != nil {
		return nil, fmt.Errorf("--addr fd:%s: not a listening socket Zone was started with (%v)", fd, err)
	}
	handedSocket = true
	onSocket = ln.Addr().Network() == "unix"
	return ln, nil
}

// idle: when the last request came (or ended), and how many run now, for
// exitWhenIdle. The healthcheck isn't one: whoever watches Zone would keep
// it up for ever.
var idle struct {
	last atomic.Int64 // UnixNano
	busy atomic.Int64
}

// countRequests keeps idle up to date around next.
func countRequests(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/health" {
			next.ServeHTTP(w, r)
			return
		}
		idle.busy.Add(1)
		idle.last.Store(now().UnixNano())
		defer func() {
			idle.last.Store(now().UnixNano())
			idle.busy.Add(-1)
		}()
		next.ServeHTTP(w, r)
	})
}

// idleLongEnough: nothing was asked for after, nothing runs, and nothing
// that lives only in memory would be lost that someone counts on: a client
// who guessed a password wrong is remembered by the throttles for a day,
// and an exit would hand a guesser five new tries each time.
// Nor while the repository is packed: a join of its packs stopped halfway
// is its work lost, and done again from the start the next time.
func idleLongEnough(after time.Duration) bool {
	return idle.busy.Load() == 0 && now().Sub(time.Unix(0, idle.last.Load())) >= after &&
		!loginThrottle.remembers() && !mcpThrottle.remembers() && !packingNow()
}

// exitWhenIdle ends Zone, with exit code 0, once idleLongEnough: requests
// that came meanwhile are answered first, and connections that come after
// wait in the socket, which its holder starts Zone on again. Everything
// Zone keeps is in the wiki's files (the search index too), so the next
// start loses nothing but what the caches held, and what an editor's save
// is merged from: a tab left open over a sleep saves as after any restart
// (a conflict to settle by hand, if the page changed meanwhile). Who wrote
// the drafts not yet committed is the one thing only memory has: they are
// committed in their authors' names first (commitDrafts).
func exitWhenIdle(srv *http.Server, after time.Duration) {
	for {
		time.Sleep(min(after/4, 15*time.Second))
		if !idleLongEnough(after) {
			continue
		}
		logf("no request for %s: exiting; the next one starts Zone again", after)
		ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
		srv.Shutdown(ctx)
		cancel()
		commitDrafts()
		os.Exit(0)
	}
}

// listenName: where ln listens, as --addr takes it.
func listenName(ln net.Listener) string {
	if ln.Addr().Network() == "unix" {
		return socketPrefix + ln.Addr().String()
	}
	return ln.Addr().String()
}

// fatal says what stops Zone from starting (or serving), and exits.
func fatal(err error) {
	fmt.Fprintln(os.Stderr, "Zone:", err)
	os.Exit(1)
}

const healthUsage = `Usage: zonewiki health [ADDR]
  whether the Zone listening on ADDR (its --addr, default :5000; unix:PATH for a
  socket) answers on localhost and took its settings file: exit code 0, or 1`

// healthURL: /api/health on localhost at the port of zonewiki health's
// argument, the --addr Zone listens on (":8080"; default :5000). For a
// socket (unix:PATH) there is no port: healthClient connects to it.
func healthURL(args []string) string {
	port := "5000"
	if len(args) > 0 && strings.HasPrefix(args[0], socketPrefix) {
		return "http://localhost/api/health"
	}
	if len(args) > 0 {
		if _, p, err := net.SplitHostPort(args[0]); err == nil && p != "" {
			port = p
		}
	}
	return "http://localhost:" + port + "/api/health"
}

// healthClient: what zonewiki health asks with: within 5 seconds, and over
// the socket when its argument names one (unix:PATH).
func healthClient(args []string) *http.Client {
	client := &http.Client{Timeout: 5 * time.Second}
	if len(args) > 0 {
		if path, ok := strings.CutPrefix(args[0], socketPrefix); ok {
			client.Transport = &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
				return (&net.Dialer{}).DialContext(ctx, "unix", path)
			}}
		}
	}
	return client
}

// health: whether Zone answers at url within the client's 5 seconds, its
// settings file taken (0), or not (1), for the container's healthcheck
// (compose.yaml), which has no curl.
func health(client *http.Client, url string) int {
	resp, err := client.Get(url)
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
	signupRoutes(mux)
	assistantRoutes(mux)
	passwordRoutes(mux)
	oauthRoutes(mux)
	return noStalls(checks(visitorGate(mux, gzipTasks(viewerGate(mux)))))
}

// visitorRoutes: what a visitor of a public wiki (ZONE_PUBLIC; access.go)
// reaches, by route: the shared pages, their history, search and uploads.
// Everything else answers a visitor as it answers anyone not signed in
// where the wiki isn't public: with the way to sign in. So a new route is
// closed to visitors until it is listed here (tests/test_public.py probes
// each route). No route of tasks or notes is here: a visitor sees neither.
// What needs no login anyway (authExempt: /login, /signup, /public/ …)
// never comes this far.
var visitorRoutes = map[string]bool{
	"GET /{$}": true, "GET /page/{slug...}": true, "GET /api/page-preview/{slug...}": true,
	"GET /pages": true, "GET /api/pages-list": true, "GET /api/pages": true,
	"GET /search": true, "GET /api/search": true,
	"GET /history/{slug...}": true, "GET /revision/{rest...}": true, "GET /diff/{rest...}": true,
	"GET /_assets/{path...}": true, "GET /api/version": true,
}

// visitorEdits: what a visitor reaches besides, where the wiki is public to
// edit (ZONE_PUBLIC=edit): the editor, with its saves and its preview, a new
// page, a checkbox ticked on a page. Not uploads, renames, deletes or
// restores (spam, and what is lost), and nothing of tasks or notes.
var visitorEdits = map[string]bool{
	"GET /edit/{slug...}": true, "POST /edit/{slug...}": true, "POST /api/save/{slug...}": true,
	"GET /new": true, "POST /new": true, "POST /api/preview": true,
	"POST /api/toggle-checkbox/{slug...}": true,
}

// visitorGate lets a visitor through to the routes that are a visitor's
// (visitorRoutes, and visitorEdits where visitors edit); any other sends
// them to sign in, before the route runs.
func visitorGate(mux *http.ServeMux, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if userOf(r) == visitor {
			_, pattern := mux.Handler(r)
			if !visitorRoutes[pattern] && !(publicMode() == publicEdit && visitorEdits[pattern]) {
				toLogin(w, r)
				return
			}
		}
		next.ServeHTTP(w, r)
	})
}

// toLogin sends a request without a session to the login page, which comes
// back to what was asked.
func toLogin(w http.ResponseWriter, r *http.Request) {
	next := r.URL.Path
	if next == "/oauth/authorize" && r.Method == "GET" {
		next = r.URL.RequestURI() // what the client asks is in the query
	}
	http.Redirect(w, r, "/login?next="+url.QueryEscape(next), http.StatusFound)
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
	"POST /password": true, // their own password
	// a connection of their own, whose writing tools are refused; the other two carry no session
	"POST /oauth/authorize": true, "POST /oauth/token": true, "POST /oauth/register": true,
	"POST /assistant/connections/{id}/revoke": true,
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
		if crossSite(r) && !oauthOpen(r.URL.Path) {
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
		if tokenForFiles(r) {
			// An assistant's upload or download, with its MCP token in
			// place of a session (mcpfiles.go).
			var ok bool
			if r, ok = tokenRequest(w, r); !ok {
				return
			}
		} else if settings().authOn() && !authExempt(r) {
			user, ok, renew := signedIn(r)
			switch {
			case ok:
				if renew {
					renewSession(w, r)
				}
				r = withUser(r, user)
			case publicMode() != "":
				// A public wiki: a visitor, for the routes visitorGate lets
				// them reach.
				r = withUser(r, visitor)
			default:
				toLogin(w, r)
				return
			}
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
// asks without one. So do an MCP client's OAuth calls (oauthOpen), but not
// /oauth/authorize, where its user allows it.
func authExempt(r *http.Request) bool {
	switch p := r.URL.Path; {
	case p == "/login", p == "/logout", p == "/mcp", p == "/api/health", p == "/setup", p == "/signup":
		return true
	case strings.HasPrefix(p, "/welcome/"):
		return true // an invite or reset link: the person has no password yet (links.go)
	case oauthOpen(p):
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
	b = append(b, '\n')
	w.Header().Set("Content-Type", "application/json")
	if len(b) >= gzipFrom && takesGzip(w) {
		var buf bytes.Buffer
		zw, _ := gzip.NewWriterLevel(&buf, gzip.BestSpeed)
		_, _ = zw.Write(b)
		_ = zw.Close()
		b = buf.Bytes()
		w.Header().Set("Content-Encoding", "gzip")
		w.Header().Add("Vary", "Accept-Encoding")
	}
	w.WriteHeader(status)
	_, _ = w.Write(b)
}

// The tasks' answers go gzipped to a browser that takes it: a list of a
// team's tasks is megabytes of JSON that compress to a tenth (All tasks at
// a team's size: 3.3 MB, measured 2026-11-15). Only what GET /api/tasks…
// answers: tasks, counts, the closed ones. It holds the user's text and
// nothing secret beside it (no token, no form's field), which is what
// compressing the two together would give away (BREACH); pages, which hold
// a session's forms, go as they are.
const gzipFrom = 1024 // bytes: below that the header costs what it saves

// gzipTasks marks the answers of GET /api/tasks… as ones writeJSON may
// gzip, for a request that takes it.
func gzipTasks(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet && (r.URL.Path == "/api/tasks" || strings.HasPrefix(r.URL.Path, "/api/tasks/")) && acceptsGzip(r) {
			w = &gzipOK{w}
		}
		next.ServeHTTP(w, r)
	})
}

// gzipOK: a response writeJSON may gzip (takesGzip).
type gzipOK struct{ http.ResponseWriter }

func (g *gzipOK) Unwrap() http.ResponseWriter { return g.ResponseWriter }

// takesGzip: w is, or wraps, a response marked by gzipTasks.
func takesGzip(w http.ResponseWriter) bool {
	for {
		if _, ok := w.(*gzipOK); ok {
			return true
		}
		u, ok := w.(interface{ Unwrap() http.ResponseWriter })
		if !ok {
			return false
		}
		w = u.Unwrap()
	}
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
