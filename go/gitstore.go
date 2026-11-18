// Version history in git: setting the wiki's repository
// up, commits.
package main

import (
	"context"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"
	"zonewiki/internal/gitrepo"
	"zonewiki/internal/mdtext"
)

// A git that hangs (a stale lock, a slow disk) must not hold its request
// forever: after this long it counts as failed, which every caller handles.
const gitTimeout = 60 * time.Second

// gitResult: how a git call ended (gitrepo.Result; Code 124: timed out).
type gitResult = gitrepo.Result

// wikiRepo: the wiki as a git repository.
func wikiRepo() gitrepo.Repo { return gitrepo.Repo{Dir: cfg.Data} }

// runGit runs git in the wiki. A git that can't start or times out counts
// as failed, like a git that exits non-zero.
func runGit(args ...string) gitResult { return runGitFor(gitTimeout, args...) }

func runGitFor(timeout time.Duration, args ...string) gitResult {
	return runGitIn(timeout, "", args...)
}

// runGitInput: runGit with input on git's stdin.
func runGitInput(input string, args ...string) gitResult { return runGitIn(gitTimeout, input, args...) }

func runGitIn(timeout time.Duration, input string, args ...string) gitResult {
	return runGitEnv(timeout, input, nil, args...)
}

// runGitEnv: runGitIn with more of git's environment (GIT_INDEX_FILE=…).
// Not under a request's context: a write goes through to its commit
// whether or not the client waits for the answer.
func runGitEnv(timeout time.Duration, input string, env []string, args ...string) gitResult {
	return wikiRepo().Run(context.Background(), gitrepo.Call{Timeout: timeout, Input: input, Env: env}, args...)
}

// wikiRel: a file's path relative to the wiki, with "/" (as git names it).
func wikiRel(path string) (string, bool) {
	rel, err := filepath.Rel(cfg.Data, path)
	return filepath.ToSlash(rel), err == nil
}

// gitSetup makes the wiki a repository (its files as the first commit), or
// gives an existing one a committer, and points origin at the git-remote setting.
func gitSetup() {
	if !cfg.GitEnabled {
		return
	}
	if _, err := os.Stat(filepath.Join(cfg.Data, ".git")); err != nil {
		if r := runGit("init"); r.Code != 0 {
			logf("git init failed, so there is no history: %s", strings.TrimSpace(r.Stderr))
			return
		}
		setCommitter()
		// Before the first add: .zone/ holds the session key.
		excludeState(cfg.Data)
		gitignore := filepath.Join(cfg.Data, ".gitignore")
		if _, err := os.Stat(gitignore); err != nil {
			if err := os.WriteFile(gitignore, nil, 0o666); err != nil {
				logf("could not create %s: %v", gitignore, err)
			}
		}
		for _, args := range [][]string{{"add", "."}, {"commit", "--allow-empty", "-m", "Initial import"}} {
			if r := runGit(args...); r.Code != 0 {
				logf("git %s failed: %s", args[0], strings.TrimSpace(r.Stderr))
			}
		}
	} else if strings.TrimSpace(runGit("config", "user.name").Stdout) == "" {
		setCommitter()
	}
	setGitRemote()
}

// setGitRemote points origin at git-remote (the settings file): at start, and when the setting changes.
func setGitRemote() {
	remote := settings().GitRemote
	if remote == "" || !cfg.GitEnabled {
		return
	}
	args := []string{"remote", "add", "origin", remote}
	if runGit("remote", "get-url", "origin").Code == 0 {
		args[1] = "set-url"
	}
	if r := runGit(args...); r.Code != 0 {
		logf("could not point origin at git-remote: %s", strings.TrimSpace(r.Stderr))
	}
}

// setCommitter: Zone commits as "Zone <zone@localhost>".
func setCommitter() {
	for _, kv := range [][2]string{{"user.email", "zone@localhost"}, {"user.name", "Zone"}} {
		if r := runGit("config", kv[0], kv[1]); r.Code != 0 {
			logf("git config %s failed: %s", kv[0], strings.TrimSpace(r.Stderr))
		}
	}
}

// commitAuthor: who a commit is by, as the history shows it, with users: a
// user's name, or whoever committed by hand; "" for Zone's own (also those
// from before Leaf was called Zone), and without users, so a wiki without
// them shows no names.
func commitAuthor(name string) string {
	if settings().Users == nil || name == appName {
		return ""
	}
	return name
}

var commitMu sync.Mutex

// gitCommit stages paths (in the wiki) and commits them, and only them,
// together, by user (the author; "": Zone itself, or a wiki without users).
// The committer is always Zone. One at a time (commitMu): another's add
// between this add and its commit would go into this commit, in this
// user's name. Only these paths go in (git commit -- paths): whatever else
// is staged (a git add by hand on the host) stays staged, out of this
// commit. A path that is neither on disk nor in git (the old name of a
// page never committed) has nothing to commit; nothing to commit at all
// (the files are as committed) is no error. A failure (a stale
// index.lock, a hook that refuses) is logged and returned: the files stay
// as written, in the Uncommitted list, and their drafts keep their
// authors, so a later commit still names them.
func gitCommit(user, message string, paths ...string) error {
	if !cfg.GitEnabled {
		return nil
	}
	commitMu.Lock()
	defer commitMu.Unlock()
	failed := func(what string, r gitResult) error {
		err := fmt.Errorf("git %s for %q failed: %s", what, message, strings.TrimSpace(r.Stderr+" "+r.Stdout))
		logf("%v", err)
		return err
	}
	var rels, candidates, missing []string
	for _, p := range paths {
		rel, ok := wikiRel(p)
		if !ok || rel == ".." || strings.HasPrefix(rel, "../") {
			err := fmt.Errorf("commit %q: %s is outside the wiki", message, p)
			logf("%v", err)
			return err
		}
		rels = append(rels, rel)
		if exists(p) {
			candidates = append(candidates, rel)
		} else {
			missing = append(missing, rel)
		}
	}
	if len(missing) > 0 {
		// A deletion: only of what git has (a file, or a folder's files).
		r := runGit(append([]string{"--literal-pathspecs", "ls-files", "-z", "--"}, missing...)...)
		tracked := strings.Split(r.Stdout, "\x00")
		for _, rel := range missing {
			if slices.ContainsFunc(tracked, func(f string) bool { return f == rel || strings.HasPrefix(f, rel+"/") }) {
				candidates = append(candidates, rel)
			}
		}
	}
	if len(candidates) > 0 {
		// What .gitignore leaves out (and git doesn't track) stays out: git
		// add would refuse it (exit 1, 0 when some are).
		// check-ignore takes names, not pathspecs, and refuses the literal
		// kind (package gitrepo's environment).
		r := runGitEnv(gitTimeout, strings.Join(candidates, "\x00")+"\x00", []string{"GIT_LITERAL_PATHSPECS=0"},
			"check-ignore", "-z", "--stdin")
		if r.Code > 1 {
			return failed("check-ignore", r)
		}
		ignored := strings.Split(r.Stdout, "\x00")
		candidates = slices.DeleteFunc(candidates, func(rel string) bool { return slices.Contains(ignored, rel) })
	}
	if len(candidates) == 0 {
		forgetDrafts(rels...)
		return nil
	}
	if r := runGit(append([]string{"--literal-pathspecs", "add", "-A", "--"}, candidates...)...); r.Code != 0 {
		return failed("add", r)
	}
	// What of them changed: the commit's paths.
	r := runGit(append([]string{"--literal-pathspecs", "diff", "--cached", "--no-renames", "--name-only", "-z", "--"}, candidates...)...)
	if r.Code != 0 {
		return failed("diff", r)
	}
	var changed []string
	for _, f := range strings.Split(r.Stdout, "\x00") {
		if f != "" {
			changed = append(changed, f)
		}
	}
	if len(changed) == 0 {
		forgetDrafts(rels...)
		return nil
	}
	args := []string{"--literal-pathspecs", "commit", "-m", message}
	if user == visitor {
		user = visitorAuthor // a public wiki's visitor has no name
	}
	if user != "" {
		args = append(args, "--author", user+" <"+user+"@zone>")
	}
	r = runGit(append(append(args, "--"), changed...)...)
	if r.Code != 0 {
		return failed("commit", r)
	}
	forgetDrafts(rels...) // committed: no one's draft now
	packSoon()
	// The commit it made ("[main 3b1b793] Update task"), for the mark of
	// the task files (tasksetup.go).
	if m := committedRE.FindStringSubmatch(r.Stdout); m != nil {
		noteTaskCommit(changed, m[1])
	}
	return nil
}

// Packing: a commit leaves loose objects (git's own packing after a commit
// is off: package gitrepo), and every packEvery commits Zone packs them in
// the background, one run at a time, beside whatever commits meanwhile; and
// once after the start, when an earlier run left many (packLoose: a Zone
// that is stopped while idle may never count packEvery commits in one run).
const (
	packEvery   = 50
	packLoose   = 4 * packEvery // loose objects, about what packEvery commits of a task leave
	packTimeout = 10 * time.Minute
	joinTimeout = 2 * time.Hour // the whole history, on half the processors, beside everything else
)

var packing struct {
	sync.Mutex
	commits int
	running bool
}

// packSoon counts a commit and starts a pack when it is time.
func packSoon() {
	packing.Lock()
	defer packing.Unlock()
	if packing.commits++; packing.commits < packEvery || packing.running {
		return
	}
	packing.commits, packing.running = 0, true
	go packNow(0)
}

// packAtStart packs what an earlier run left loose, in the background, when
// it is much: a few loose objects are no reason to pack at every start.
func packAtStart() {
	if !cfg.GitEnabled {
		return
	}
	packing.Lock()
	defer packing.Unlock()
	if !packing.running {
		packing.running = true
		go packNow(packLoose)
	}
}

// looseObjects: how many loose objects the wiki's repository has (git
// count-objects: "12 objects, 48 kilobytes"); 0 when git doesn't say.
func looseObjects() int {
	n, _ := strconv.Atoi(strings.Fields(runGit("count-objects").Stdout + " 0")[0])
	return n
}

// packingNow: a pack or a join runs.
func packingNow() bool {
	packing.Lock()
	defer packing.Unlock()
	return packing.running
}

// packNow packs the wiki's repository (packSoon; packAtStart: only with
// atLeast loose objects). The pack is made beside whatever commits; the
// loose objects it made superfluous are removed while no commit runs
// (commitMu): removing them takes their folders along, and a git add that
// is writing into one just then fails. When the packs have become many or
// heavy (Repo.TimeToJoin) they are made one, joinPacks.
func packNow(atLeast int) {
	defer func() {
		packing.Lock()
		packing.running = false
		packing.Unlock()
	}()
	if atLeast > 0 && looseObjects() < atLeast {
		return
	}
	repo := wikiRepo()
	r := repo.Pack(context.Background(), packTimeout)
	if r.Code == 0 {
		commitMu.Lock()
		r = repo.Prune(context.Background(), gitTimeout)
		commitMu.Unlock()
	}
	if r.Code == 0 && repo.TimeToJoin() {
		r = joinPacks(repo)
	}
	if r.Code != 0 {
		logf("git: packing failed (the repository stays as it is, only larger): %s", strings.TrimSpace(r.Stderr+" "+r.Stdout))
	}
}

// joinPacks makes the repository's packs one. The pack is written beside
// whatever commits (Repo.Join: it reads the whole history, minutes in a
// repository of some hundred thousand commits), and no commit waits for
// it; only the packs it stands for, and what is loose and packed now, are
// removed while no commit runs, which reads no object. (Until 2026-11-16
// all of it ran under commitMu: every save of the wiki waited for the
// join, as long as the history made it.)
func joinPacks(repo gitrepo.Repo) gitrepo.Result {
	old, r := repo.Join(context.Background(), joinTimeout)
	if r.Code != 0 || len(old) == 0 {
		return r
	}
	commitMu.Lock()
	defer commitMu.Unlock()
	repo.Drop(old)
	return repo.Prune(context.Background(), gitTimeout)
}

// excludeState keeps Zone's folder .zone/ out of git, all of it: the
// session key, the caches, the query log, and the settings file, which
// holds the users and their password hashes (docs/spec/users.md: account
// data stays out of git).
func excludeState(wiki string) { excludeFromGit(wiki, "/"+stateDirName+"/*") }

// usersInHistory: a user who made a commit in the wiki's history (Zone
// commits as "name <name@zone>" for a user: gitCommit), "" if none did.
// A wiki with such commits but no settings file lost its users (a clone
// of its git remote, which holds no settings file): missingUsers.
func usersInHistory() string {
	if !cfg.GitEnabled || !isDir(filepath.Join(cfg.Data, ".git")) {
		return ""
	}
	return strings.TrimSpace(runGit("log", "-1", "--author=@zone>", "--format=%an").Stdout)
}

// missingUsers: why Zone won't start, if its wiki had users but its
// settings file is gone: else it would run without a login. An empty file
// says no login on purpose.
func missingUsers() error {
	if exists(configPath()) {
		return nil
	}
	if user := usersInHistory(); user != "" {
		return fmt.Errorf("this wiki had users (%s made changes in its history), but its settings file %s, "+
			"which holds them, isn't there: Zone doesn't keep it in git, so a clone of the git remote has none. "+
			"Restore %s from a backup, or add the users again (zonewiki user add NAME). "+
			"To run this wiki without a login on purpose, create an empty %s.",
			user, configName(), configName(), configName())
	}
	return nil
}

// excludeFromGit lists entry (e.g. /.zone/*: cache, secret key) in
// .git/info/exclude, not .gitignore, which would itself show up as an
// uncommitted change.
func excludeFromGit(wiki, entry string) {
	if !isDir(filepath.Join(wiki, ".git")) {
		return
	}
	info := filepath.Join(wiki, ".git", "info")
	exclude := filepath.Join(info, "exclude")
	b, err := os.ReadFile(exclude)
	if err != nil && !os.IsNotExist(err) {
		logf("could not update %s: %v", exclude, err)
		return
	}
	text := string(b)
	for _, line := range strings.Split(text, "\n") {
		if line == entry {
			return
		}
	}
	if text != "" && !strings.HasSuffix(text, "\n") {
		text += "\n"
	}
	_ = os.Mkdir(info, 0o777)
	if err := os.WriteFile(exclude, []byte(text+entry+"\n"), 0o666); err != nil {
		logf("could not update %s: %v", exclude, err)
	}
}

// noDeltaForUploads tells the wiki's repository (its own
// .git/info/attributes, which is in no commit) not to look for differences
// between uploads when it packs: gitrepo.NoDeltaAttributes.
func noDeltaForUploads(wiki string) {
	if !isDir(filepath.Join(wiki, ".git")) {
		return
	}
	info := filepath.Join(wiki, ".git", "info")
	path := filepath.Join(info, "attributes")
	b, err := os.ReadFile(path)
	if err != nil && !os.IsNotExist(err) {
		logf("could not update %s: %v", path, err)
		return
	}
	text, have := string(b), strings.Split(string(b), "\n")
	var add []string
	for _, line := range gitrepo.NoDeltaAttributes("_assets", privateDir+"/*/_assets", groupsDir+"/*/_assets", taskFilesDir+"/*/_assets") {
		if !slices.Contains(have, line) {
			add = append(add, line)
		}
	}
	if len(add) == 0 {
		return
	}
	if text != "" && !strings.HasSuffix(text, "\n") {
		text += "\n"
	}
	_ = os.Mkdir(info, 0o777)
	if err := os.WriteFile(path, []byte(text+strings.Join(add, "\n")+"\n"), 0o666); err != nil {
		logf("could not update %s: %v", path, err)
	}
}

var shaRE = regexp.MustCompile(`^[0-9a-f]{7,40}$`)

// committedRE: the commit git commit says it made, in its first line.
var committedRE = regexp.MustCompile(`^\[[^\]\n]* ([0-9a-f]{7,40})\]`)

// movePage moves a page's file, points every link to it at the new slug,
// and commits the move (by user) with the pages and tasks files whose links
// changed.
func movePage(user asker, oldPath, newPath, slug, newSlug, message string) ([]string, error) {
	// The page's, and those of the pages whose links follow.
	if err := commitOthersDrafts(user); err != nil {
		return nil, err
	}
	if err := os.MkdirAll(filepath.Dir(newPath), 0o777); err != nil {
		return nil, err
	}
	if err := os.Rename(oldPath, newPath); err != nil {
		return nil, err
	}
	changed, linkErr := rewriteMovedLinks(map[string]string{slug: newSlug})
	files := append([]string{oldPath, newPath}, shareUploads(slug, newSlug, newPath)...)
	if err := notCommitted(gitCommit(user.Name, message, append(files, changed...)...)); err != nil {
		return changed, err
	}
	return changed, linkErr
}

// moveFolder moves every page in a folder (and its subfolders) to
// newFolder, as movePage does for one, all in one commit: returns the
// (slug, new slug) moves, A–Z, and the files whose links changed. The
// caller checks that no target exists. Other files in the folder (images…)
// stay; folders left empty go.
func moveFolder(user asker, folder, newFolder, message string) ([][2]string, []string, error) {
	if err := commitOthersDrafts(user); err != nil {
		return nil, nil, err
	}
	var moves [][2]string
	var touched []string
	moved := map[string]string{}
	for _, p := range sortedPages() {
		if !strings.HasPrefix(p.Slug, folder+"/") {
			continue
		}
		newSlug := newFolder + p.Slug[len(folder):]
		newPath := filepath.Join(cfg.Data, filepath.FromSlash(newSlug+".md"))
		err := os.MkdirAll(filepath.Dir(newPath), 0o777)
		if err == nil {
			err = os.Rename(p.Path, newPath)
		}
		if err != nil {
			// Nothing moved, as the refusals promise: those moved go back.
			for i := len(touched) - 2; i >= 0; i -= 2 {
				if back := os.Rename(touched[i+1], touched[i]); back != nil {
					logf("moving %s back after a failed folder move: %v", touched[i+1], back)
				}
			}
			return nil, nil, err
		}
		moves = append(moves, [2]string{p.Slug, newSlug})
		moved[p.Slug] = newSlug
		touched = append(touched, p.Path, newPath)
	}
	// All the files are in place before any link is rewritten, so each
	// page's links are re-based from where it moved to.
	changed, linkErr := rewriteMovedLinks(moved)
	for _, m := range moves {
		touched = append(touched, shareUploads(m[0], m[1], filepath.Join(cfg.Data, filepath.FromSlash(m[1]+".md")))...)
	}
	var dirs []string
	_ = filepath.WalkDir(filepath.Join(cfg.Data, filepath.FromSlash(folder)), func(p string, d fs.DirEntry, err error) error {
		if err == nil && d.IsDir() {
			dirs = append(dirs, p)
		}
		return nil
	})
	for i := len(dirs) - 1; i >= 0; i-- { // the deepest first
		_ = os.Remove(dirs[i]) // fails when not empty
	}
	commit := append([]string{}, touched...)
	var still []string
	for _, p := range changed {
		if !slices.Contains(touched, p) {
			commit = append(commit, p)
		}
		if exists(p) {
			still = append(still, p)
		}
	}
	if err := notCommitted(gitCommit(user.Name, message, commit...)); err != nil {
		return moves, still, err
	}
	return moves, still, linkErr
}

var renameBracesRE = regexp.MustCompile(`\{([^{}]*) => ([^{}]*)\}`)

// numstatPath: the path after a commit from a --numstat path, which for a
// rename reads "old => new" or "dir/{old => new}.md".
func numstatPath(p string) string {
	if !strings.Contains(p, " => ") {
		return p
	}
	if strings.Contains(p, "{") {
		return strings.ReplaceAll(renameBracesRE.ReplaceAllString(p, "$2"), "//", "/")
	}
	_, after, _ := strings.Cut(p, " => ")
	return after
}

// viaFormat: a commit's Assistant trailer in a git log format: its value
// alone, on one line (the first, should a commit have several).
const viaFormat = "%(trailers:key=" + viaTrailerKey + ",valueonly,separator=%x20)"

// GitEntry is a commit of a page: its sha, day, relative date, time
// (YYYY-MM-DD HH:MM), message, author (commitAuthor), lines added and
// deleted, and the file's path at that commit (relative to the wiki;
// another before a rename).
type GitEntry struct {
	Sha, Date, Rel, Time, Msg, Author string
	Via                               string // which assistant wrote it (viaOfCommit), "" for none or not said
	Added, Deleted                    int
	Path                              string
}

// gitLog: the commits of a file, newest first, across renames (--follow);
// max 0: all.
func gitLog(path string, max int) []GitEntry {
	if !cfg.GitEnabled {
		return nil
	}
	rel, ok := wikiRel(path)
	if !ok {
		return nil
	}
	args := []string{"log", "--follow"}
	if max > 0 {
		args = append(args, "-n", strconv.Itoa(max))
	}
	args = append(args, "--numstat", "--date=format:%Y-%m-%d %H:%M",
		"--format=\x1e%H\x1f%as\x1f%ar\x1f%ad\x1f%an\x1f"+viaFormat+"\x1f%s", "--", rel)
	r := runGit(args...)
	var entries []GitEntry
	records := strings.Split(r.Stdout, "\x1e")
	for _, record := range records[1:] {
		head, stats, _ := strings.Cut(record, "\n")
		parts := strings.SplitN(head, "\x1f", 7)
		if len(parts) != 7 {
			continue
		}
		e := GitEntry{Sha: parts[0], Date: parts[1], Rel: parts[2], Time: parts[3], Author: commitAuthor(parts[4]),
			Msg: parts[6], Via: viaOfCommit(parts[6], parts[5]), Path: rel}
		for _, line := range mdtext.SplitLines(stats) {
			cols := strings.SplitN(mdtext.LineBody(line), "\t", 3)
			if len(cols) == 3 {
				if n, err := strconv.Atoi(cols[0]); err == nil {
					e.Added += n
				}
				if n, err := strconv.Atoi(cols[1]); err == nil {
					e.Deleted += n
				}
				e.Path = numstatPath(cols[2])
			}
		}
		entries = append(entries, e)
	}
	return entries
}

// LogEntry is a commit of the wiki: sha, time (YYYY-MM-DD HH:MM), how long
// ago, message, author (commitAuthor) and the files it changed (relative
// to the wiki) and what it did to each (Kinds, a letter a file: A added, M
// changed, D deleted; a rename is a D and an A); Hidden: how many more it
// changed that see didn't take.
type LogEntry struct {
	Sha, Time, Rel, Msg, Author string
	Via                         string // which assistant wrote it (viaOfCommit), "" for none or not said
	Files                       []string
	Kinds                       string
	Hidden                      int
}

// gitLogAll: the wiki's commits, newest first, at most limit; since a day
// (YYYY-MM-DD, from its start), under a folder ("" for either: all). Of
// each commit only the files see takes (nil: all), and a commit with none
// of them is left out, as is one keep refuses (nil: none; it is given the
// commit with the files see took); git is asked for more until limit are found, twice
// as many each time (each call walks the history from the start again, so
// a fixed batch would cost quadratic time on a long history where see
// and keep take few commits).
func gitLogAll(since, under string, limit int, see func(path string) bool, keep func(LogEntry) bool) []LogEntry {
	if !cfg.GitEnabled {
		return nil
	}
	batch := limit
	if see != nil || keep != nil {
		batch = max(2*limit, 50)
	}
	var entries []LogEntry
	for skip := 0; len(entries) < limit; skip, batch = skip+batch, 2*batch {
		args := []string{"log", "-n", strconv.Itoa(batch), "--skip", strconv.Itoa(skip), "--name-status", "--no-renames",
			"--date=format:%Y-%m-%d %H:%M", "--format=\x1e%H\x1f%ad\x1f%ar\x1f%an\x1f" + viaFormat + "\x1f%s"}
		if since != "" {
			args = append(args, "--since="+since+" 00:00")
		}
		if under != "" {
			args = append(args, "--", under+"/")
		}
		records := strings.Split(runGit(args...).Stdout, "\x1e")[1:]
		for _, record := range records {
			head, names, _ := strings.Cut(record, "\n")
			parts := strings.SplitN(head, "\x1f", 6)
			if len(parts) != 6 {
				continue
			}
			e := LogEntry{Sha: parts[0], Time: parts[1], Rel: parts[2], Author: commitAuthor(parts[3]), Msg: parts[5],
				Via: viaOfCommit(parts[5], parts[4])}
			for _, line := range mdtext.SplitLines(names) {
				kind, n, _ := strings.Cut(mdtext.LineBody(line), "\t")
				switch {
				case strings.TrimSpace(n) == "" || kind == "":
				case see == nil || see(n):
					e.Files = append(e.Files, n)
					e.Kinds += kind[:1]
				default:
					e.Hidden++
				}
			}
			if (see == nil || len(e.Files) > 0) && (keep == nil || keep(e)) {
				entries = append(entries, e)
			}
		}
		if len(records) < batch {
			break
		}
	}
	return entries[:min(limit, len(entries))]
}

// fileChange: a file a commit changed (relative to the wiki) and how: 'A'
// added, 'M' changed, 'D' deleted.
type fileChange struct {
	Path string
	Kind byte
}

// commitOf: the commit a version names (a sha or its start), whole, and
// its message's first line; false for none.
func commitOf(version string) (sha, subject string, ok bool) {
	if !shaRE.MatchString(version) {
		return "", "", false
	}
	r := runGit("log", "-n", "1", "--format=%H%x1f%s", version+"^{commit}", "--")
	sha, subject, found := strings.Cut(strings.TrimRight(r.Stdout, "\n"), "\x1f")
	return sha, subject, r.Code == 0 && found && shaRE.MatchString(sha)
}

// commitChanges: every file the commit changed, with how (a rename is a
// deletion and an addition; a merge changed none).
func commitChanges(sha string) []fileChange {
	r := runGit("diff-tree", "--root", "--no-commit-id", "-r", "--name-status", "--no-renames", "-z", sha)
	if r.Code != 0 {
		return nil
	}
	var out []fileChange
	parts := strings.Split(r.Stdout, "\x00")
	for i := 0; i+1 < len(parts); i += 2 {
		if parts[i] != "" && parts[i+1] != "" {
			out = append(out, fileChange{Path: parts[i+1], Kind: parts[i][0]})
		}
	}
	return out
}

// lastCommitsOf: the n latest commits that changed the file (relative to
// the wiki), newest first; not across renames.
func lastCommitsOf(rel string, n int) []string {
	return strings.Fields(runGit("log", "-n", strconv.Itoa(n), "--format=%H", "--", rel).Stdout)
}

// latestCommits: for each file changed in the wiki's n latest commits,
// those commits, newest first.
func latestCommits(n int) map[string][]string {
	out := map[string][]string{}
	for _, record := range strings.Split(runGit("log", "-n", strconv.Itoa(n), "--name-only", "--no-renames", "--format=%x1e%H").Stdout, "\x1e")[1:] {
		sha, names, _ := strings.Cut(record, "\n")
		for _, name := range mdtext.SplitLines(names) {
			if name = mdtext.LineBody(name); strings.TrimSpace(name) != "" {
				out[name] = append(out[name], sha)
			}
		}
	}
	return out
}

// isUncommitted: the file differs from its last commit (or was never
// committed): edits auto-saved by the editor, or made on disk.
func isUncommitted(path string) bool {
	if !cfg.GitEnabled {
		return false
	}
	rel, ok := wikiRel(path)
	if !ok {
		return false
	}
	r := runGit("status", "--porcelain", "--", rel)
	return r.Code == 0 && strings.TrimSpace(r.Stdout) != ""
}

// A pattern search (grepPages) gets far less time than other git calls: a
// pattern that backtracks badly is killed, instead of a whole minute of work.
var grepTimeout = 10 * time.Second

// grepPerl: whether this git has Perl-style patterns (grep -P, built with
// PCRE); else grepPages uses extended regexes (-E). Found out on the first
// search.
var grepPerl struct {
	sync.Mutex
	known, perl bool
}

// GrepHit is a line of a page that matched.
type GrepHit struct {
	Slug   string
	Number int
	Line   string
}

// grepPages: every line of every page matching a regex, in file order. git
// matches (git grep --no-index, so pages not yet committed count too) in
// its own process, which a timeout can kill. Without git, the pattern is
// matched as plain text. Only the pages in slugs (nil: every page). A
// pattern git refuses (bad syntax) or that takes too long is a refusal.
func grepPages(pattern string, ignoreCase bool, slugs map[string]bool) ([]GrepHit, error) {
	if slugs == nil {
		slugs = slugSet()
	}
	var hits []GrepHit
	if !cfg.GitEnabled {
		needle := pattern
		if ignoreCase {
			needle = strings.ToLower(pattern)
		}
		for _, p := range sortedPages() {
			if !slugs[p.Slug] {
				continue
			}
			text, err := readText(p.Path)
			if err != nil {
				continue
			}
			for n, line := range mdtext.SplitLines(strings.ToValidUTF8(text, "\uFFFD")) {
				line = mdtext.LineBody(line)
				hay := line
				if ignoreCase {
					hay = strings.ToLower(line)
				}
				if strings.Contains(hay, needle) {
					hits = append(hits, GrepHit{p.Slug, n + 1, line})
				}
			}
		}
		return hits, nil
	}
	grepPerl.Lock()
	tries := []bool{true, false}
	if grepPerl.known {
		tries = []bool{grepPerl.perl}
	}
	grepPerl.Unlock()
	var r gitResult
	for _, perl := range tries {
		args := []string{"grep", "--no-index", "--no-color", "-I", "-n", "-z", "-E"}
		if perl {
			args[len(args)-1] = "-P"
		}
		if ignoreCase {
			args = append(args, "-i")
		}
		// "*.md" is a pattern (package gitrepo takes paths literally): git's, since Zone wrote it.
		r = runGitEnv(grepTimeout, "", []string{"GIT_LITERAL_PATHSPECS=0"}, append(args, "-e", pattern, "--", "*.md")...)
		if r.Code == gitrepo.TimedOut {
			return nil, refuse(fmt.Sprintf("the search took longer than %ds; try a simpler pattern", int(grepTimeout.Seconds())))
		}
		if perl && r.Code != 0 && r.Code != 1 && strings.Contains(r.Stderr, "PCRE") {
			continue // this git has no -P: extended regexes instead
		}
		grepPerl.Lock()
		grepPerl.known, grepPerl.perl = true, perl
		grepPerl.Unlock()
		break
	}
	if r.Code != 0 && r.Code != 1 {
		msg := strings.TrimPrefix(strings.TrimSpace(r.Stderr), "fatal: ")
		if msg == "" {
			msg = fmt.Sprintf("git grep failed (%d)", r.Code)
		}
		return nil, refuse(msg)
	}
	for _, row := range mdtext.SplitLines(strings.ToValidUTF8(r.Stdout, "\uFFFD")) {
		parts := strings.SplitN(mdtext.LineBody(row), "\x00", 3)
		if len(parts) != 3 {
			continue
		}
		n, err := strconv.Atoi(parts[1])
		slug, ok := strings.CutSuffix(filepath.ToSlash(parts[0]), ".md")
		if err == nil && ok && slugs[slug] { // pages only: not _assets/, _tasks/, …
			hits = append(hits, GrepHit{slug, n, parts[2]})
		}
	}
	return hits, nil
}

// gitShow: a file as a commit has it (ok false when it hasn't).
func gitShow(sha, path string) (string, bool) {
	rel, ok := wikiRel(path)
	if !ok {
		return "", false
	}
	r := runGit("show", sha+":"+rel)
	return r.Stdout, r.Code == 0
}

// relativeTime: "2 days ago".
func relativeTime(t time.Time) string {
	seconds := int(now().Sub(t).Seconds())
	unit := func(n int, name string) string {
		if n != 1 {
			name += "s"
		}
		return strconv.Itoa(n) + " " + name + " ago"
	}
	switch {
	case seconds < 60:
		return "just now"
	case seconds < 3600:
		return unit(seconds/60, "minute")
	case seconds < 86400:
		return unit(seconds/3600, "hour")
	case seconds < 2592000:
		return unit(seconds/86400, "day")
	case seconds < 31536000:
		return unit(seconds/2592000, "month")
	}
	return unit(seconds/31536000, "year")
}

// UncommittedFile is a file with uncommitted changes (modified, added,
// deleted, untracked): its path in the wiki, and for a page its slug and
// title; mtime and rel are empty for a deleted file.
type UncommittedFile struct {
	Slug, Title, Filepath, Mtime, Rel string
}

// gitUncommittedFiles: -z, so paths come unquoted (a name with a space or
// an umlaut). Only under the given paths (relative to the wiki), if any.
func gitUncommittedFiles(under ...string) []UncommittedFile {
	if !cfg.GitEnabled {
		return nil
	}
	args := []string{"status", "--porcelain", "-z", "--untracked-files=all"}
	if len(under) > 0 {
		args = append(append(args, "--"), under...)
	}
	r := runGit(args...)
	fields := strings.Split(r.Stdout, "\x00")
	var files []UncommittedFile
	for i := 0; i < len(fields); i++ {
		entry := fields[i]
		if len(entry) < 3 {
			continue
		}
		status, fp := entry[:2], entry[3:]
		if status[0] == 'R' || status[0] == 'C' {
			i++ // a rename's or copy's old path
		}
		if strings.TrimSpace(status) == "" || fp == "" || strings.HasSuffix(fp, "/") {
			continue
		}
		f := UncommittedFile{Filepath: fp, Title: fp}
		if st, err := os.Stat(filepath.Join(cfg.Data, fp)); err == nil {
			f.Mtime, f.Rel = st.ModTime().Format("2006-01-02"), relativeTime(st.ModTime())
		}
		files = append(files, f)
	}
	return files
}

// commitMessage: the message for committing a file from the uncommitted list.
func commitMessage(fp string) string {
	if strings.HasSuffix(fp, ".md") {
		return "Save " + strings.TrimSuffix(fp, ".md")
	}
	return "Update " + fp
}

// gitDiff: what a commit changed in a file; also: its name before the
// commit, when the commit renamed it, so the rename is seen as one.
func gitDiff(sha string, paths ...string) (string, bool) {
	args := []string{"show", "--format=", "--patch", "-M", sha, "--"}
	seen := map[string]bool{}
	for _, p := range paths {
		rel, ok := wikiRel(p)
		if !ok {
			return "", false
		}
		if !seen[rel] {
			seen[rel] = true
			args = append(args, rel)
		}
	}
	r := runGit(args...)
	return strings.TrimSpace(r.Stdout), r.Code == 0
}

// uncommittedEntries: the uncommitted files, a page's with its slug and title.
func uncommittedEntries() []UncommittedFile {
	files := gitUncommittedFiles()
	for i, f := range files {
		if strings.HasSuffix(f.Filepath, ".md") && !strings.HasPrefix(f.Filepath, "_assets") {
			f.Slug = strings.TrimSuffix(f.Filepath, ".md")
			if text, err := readText(filepath.Join(cfg.Data, f.Filepath)); err == nil {
				f.Title = mdtext.PageTitle(text, f.Slug)
			} else {
				f.Title = mdtext.SlugDisplayName(f.Slug)
			}
			files[i] = f
		}
	}
	return files
}

// DeletedPage is a page in git history but not on disk.
type DeletedPage struct{ Slug, Title, Sha, Date string }

// listDeletedPages: the deleted pages, newest deletion first.
func listDeletedPages() []DeletedPage {
	if !cfg.GitEnabled {
		return nil
	}
	r := runGit("log", "--diff-filter=D", "--format=%H\t%P\t%as", "--name-only")
	if r.Code != 0 {
		return nil
	}
	existing := slugSet()
	seen := map[string]bool{}
	var out []DeletedPage
	var sha, parent, date string
	for _, raw := range mdtext.SplitLines(r.Stdout) {
		line := mdtext.TrimRightSpace(raw)
		if line == "" {
			continue
		}
		if strings.Contains(line, "\t") {
			sha, parent, date = deletionCommit(line)
		} else if parent != "" && strings.HasSuffix(line, ".md") && !strings.HasPrefix(line, ".") {
			slug := strings.TrimSuffix(line, ".md")
			if existing[slug] || seen[slug] || strings.HasPrefix(slug, "_assets") {
				continue
			}
			seen[slug] = true
			out = append(out, DeletedPage{slug, deletedTitle(parent, slug), sha, date})
		}
	}
	return out
}

// deletionCommit: a commit as "%H\t%P\t%as" reads: its sha, its first
// parent ("" for none) and its day.
func deletionCommit(line string) (sha, parent, date string) {
	parts := strings.SplitN(line, "\t", 3)
	if len(parts) > 1 {
		if ps := strings.Fields(parts[1]); len(ps) > 0 {
			parent = ps[0]
		}
	}
	if len(parts) > 2 {
		date = parts[2]
	}
	return parts[0], parent, date
}

// lastDeletion: the commit before a page's latest deletion (which has its
// last text) and the day it was deleted; ok false when git has none.
func lastDeletion(slug string) (parent, date string, ok bool) {
	if !cfg.GitEnabled {
		return "", "", false
	}
	r := runGit("log", "-1", "--format=%H\t%P\t%as", "--diff-filter=D", "--", slug+".md")
	out := strings.TrimSpace(r.Stdout)
	if r.Code != 0 || out == "" {
		return "", "", false
	}
	_, parent, date = deletionCommit(out)
	return parent, date, parent != ""
}

// deletedTitle: a deleted page's title as its last version (at parent) has it.
func deletedTitle(parent, slug string) string {
	if r := runGit("show", parent+":"+slug+".md"); r.Code == 0 {
		return mdtext.PageTitle(r.Stdout, slug)
	}
	return mdtext.SlugDisplayName(slug)
}

var (
	editedRE   = regexp.MustCompile(`^(?:Update|Save) \S+$`)
	createdRE  = regexp.MustCompile(`^Create \S+$`)
	deletedRE  = regexp.MustCompile(`^Delete \S+$`)
	renamedRE  = regexp.MustCompile(`^Rename (\S+) to (\S+)$`)
	promotedRE = regexp.MustCompile(`^Promote (\S+) to (\S+)$`)
	appendedRE = regexp.MustCompile(`^Append (\S+) to (\S+)$`)
	restoredRE = regexp.MustCompile(`^Restore \S+ to ([0-9a-f]{7,40})$`)
	undoneRE   = regexp.MustCompile(`^Undo ([0-9a-f]{7,40}): `)
	undoneNRE  = regexp.MustCompile(`^Undo (\d+) changes: `)
)

// versionLabel: what a commit did to the page, in words: Zone's own commit
// messages read alike in a list, so they are named by kind; others as written.
func versionLabel(e GitEntry, entries []GitEntry) string {
	msg := e.Msg
	switch {
	case editedRE.MatchString(msg):
		return "Edited"
	case createdRE.MatchString(msg):
		return "Created"
	case deletedRE.MatchString(msg):
		return "Deleted"
	case msg == "Initial import":
		return "Imported"
	}
	if m := renamedRE.FindStringSubmatch(msg); m != nil {
		if e.Path == m[2]+".md" {
			return "Renamed from “" + m[1] + "”"
		}
		return "Link updated (“" + m[1] + "” renamed)"
	}
	if m := promotedRE.FindStringSubmatch(msg); m != nil {
		if e.Path == m[2]+".md" {
			return "Moved from Notes"
		}
		return "Link updated (a note moved to the wiki)"
	}
	if m := appendedRE.FindStringSubmatch(msg); m != nil {
		switch e.Path {
		case m[2] + ".md":
			return "Note appended"
		case m[1] + ".md":
			return "Appended to “" + m[2] + "”"
		}
		return "Link updated (a note appended to a page)"
	}
	if m := restoredRE.FindStringSubmatch(msg); m != nil {
		for _, old := range entries {
			if strings.HasPrefix(old.Sha, m[1]) {
				return "Restored the version of " + old.Time
			}
		}
		return "Restored an older version"
	}
	if m := undoneRE.FindStringSubmatch(msg); m != nil {
		for _, old := range entries {
			if strings.HasPrefix(old.Sha, m[1]) {
				return "Undid the change of " + old.Time
			}
		}
		return "Undid a change"
	}
	if m := undoneNRE.FindStringSubmatch(msg); m != nil {
		return "Undid " + m[1] + " changes"
	}
	return msg
}

// versionIndex: the entry of sha (a prefix), -1 when none.
func versionIndex(entries []GitEntry, sha string) int {
	for i, e := range entries {
		if strings.HasPrefix(e.Sha, sha) {
			return i
		}
	}
	return -1
}

// versionPath: the file's path at a commit: before a rename it had another name.
func versionPath(path string, entries []GitEntry, sha string) string {
	if i := versionIndex(entries, sha); i >= 0 {
		return filepath.Join(cfg.Data, filepath.FromSlash(entries[i].Path))
	}
	return path
}

// versionDiff: what the version sha of a page (path, its entries) changed.
func versionDiff(path string, entries []GitEntry, sha string) (string, bool) {
	paths := []string{versionPath(path, entries, sha)}
	if i := versionIndex(entries, sha); i >= 0 && i+1 < len(entries) {
		paths = append(paths, filepath.Join(cfg.Data, filepath.FromSlash(entries[i+1].Path)))
	}
	return gitDiff(sha, paths...)
}

// isCurrent: the newest commit is what the page holds now (no edits on
// disk since, not deleted).
func isCurrent(path string, entries []GitEntry) bool {
	if len(entries) == 0 || !exists(path) {
		return false
	}
	old, ok := gitShow(entries[0].Sha, versionPath(path, entries, entries[0].Sha))
	text, err := readText(path)
	return ok && err == nil && old == text
}
