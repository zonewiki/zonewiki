// Version history in git: setting the wiki's repository
// up, commits.
package main

import (
	"bufio"
	"bytes"
	"context"
	"fmt"
	"io"
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
func wikiRepo() gitrepo.Repo { return gitrepo.Repo{Dir: cfg.Data, Env: zoneEnv()} }

// zoneEnv: with the wiki's timezone set, git writes a commit's time and
// reads "local" as that zone's (history's times, --date=format-local).
func zoneEnv() []string {
	if s := settings(); s != nil && s.Zone != nil {
		return []string{"TZ=" + s.Timezone}
	}
	return nil
}

// runGit runs git in the wiki. A git that can't start or times out counts
// as failed, like a git that exits non-zero.
func runGit(args ...string) gitResult { return runGitFor(gitTimeout, args...) }

func runGitFor(timeout time.Duration, args ...string) gitResult {
	return runGitIn(timeout, "", args...)
}

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
// It says in the log which it did: made the repository, found one, or
// found no git to keep a history with. The error: the folder is inside
// another repository that doesn't ignore it (outerRepo), so Zonewiki doesn't
// start.
func gitSetup() error {
	if !cfg.GitEnabled {
		logf("git isn't installed (not on PATH): the wiki keeps no history, so no versions or undo")
		return nil
	}
	if _, err := os.Stat(filepath.Join(cfg.Data, ".git")); err != nil {
		if err := wikiFolderError(); err != nil {
			return err
		}
		if r := runGit("init"); r.Code != 0 {
			logf("git init failed, so there is no history: %s", strings.TrimSpace(r.Stderr))
			return nil
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
		made := true
		for _, args := range [][]string{{"add", "."}, {"commit", "--allow-empty", "-m", "Initial import"}} {
			if r := runGit(args...); r.Code != 0 {
				logf("git %s failed: %s", args[0], strings.TrimSpace(r.Stderr))
				made = false
			}
		}
		if made {
			logf("made %s a git repository: the wiki's history starts here, with its files as they are", cfg.Data)
		}
	} else {
		logf("found a git repository in %s: the wiki's history goes on from it", cfg.Data)
		if strings.TrimSpace(runGit("config", "user.name").Stdout) == "" {
			setCommitter()
		}
	}
	setGitRemote()
	return nil
}

// wikiFolderError: why Zonewiki doesn't use the wiki's folder (outerRepo),
// nil if it does. The start asks it first, before anything is written
// there, so a folder refused is left as it was; gitSetup asks it again
// before it makes the repository.
func wikiFolderError() error {
	if !cfg.GitEnabled {
		return nil
	}
	if _, err := os.Stat(filepath.Join(cfg.Data, ".git")); err == nil {
		return nil // the wiki's repository already, wherever it lies
	}
	return outerRepo()
}

// outerRepo: an error when the wiki's folder, not a repository yet, lies
// inside another one's working tree, which doesn't ignore it. A repository
// of the wiki's own there would hide its pages from the outer one (git
// takes a folder with a .git for another project: its add records a
// pointer, not the files), and it is most often a mistake: a ZONE_DATA
// that names a folder of a checkout. Where the outer repository ignores
// the folder (a home folder kept in git for its dotfiles, ignoring the
// rest), the wiki's own repository is what was meant. A folder that is a
// repository already is the wiki's, wherever it lies (gitSetup).
func outerRepo() error {
	r := runGit("rev-parse", "--show-toplevel")
	top := strings.TrimSpace(r.Stdout)
	if r.Code != 0 || top == "" {
		return nil // in no repository, or in one git won't read (another owner's): as before
	}
	if real, err := filepath.EvalSymlinks(top); err == nil {
		top = real
	}
	rel, err := filepath.Rel(top, cfg.Data)
	if err != nil || rel == "." {
		return nil
	}
	// check-ignore takes no literal pathspecs (Zonewiki's default for git), so
	// the folder's name is one with its glob characters escaped.
	spec := strings.NewReplacer(`\`, `\\`, "*", `\*`, "?", `\?`, "[", `\[`).Replace(filepath.ToSlash(rel)) + "/"
	ignored := gitrepo.Repo{Dir: top}.Run(context.Background(),
		gitrepo.Call{Timeout: gitTimeout, Env: []string{"GIT_LITERAL_PATHSPECS=0"}}, "check-ignore", "-q", "--", spec)
	if ignored.Code == 0 {
		return nil
	}
	return fmt.Errorf("%s is inside the git repository %s, which doesn't ignore it. "+
		"Zonewiki keeps the wiki's history in a repository of its own, which would hide the wiki's files from that one. "+
		"Use a folder outside it, or add the folder to that repository's .gitignore (%s/), then start Zonewiki again.",
		cfg.Data, top, filepath.ToSlash(rel))
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

// setCommitter: Zonewiki commits as "Zonewiki <zonewiki@localhost>".
func setCommitter() {
	for _, kv := range [][2]string{{"user.email", "zonewiki@localhost"}, {"user.name", "Zonewiki"}} {
		if r := runGit("config", kv[0], kv[1]); r.Code != 0 {
			logf("git config %s failed: %s", kv[0], strings.TrimSpace(r.Stderr))
		}
	}
}

// commitAuthor: who a commit is by, as the history shows it, with users: a
// user's name, or whoever committed by hand; "" for Zonewiki's own (and
// for "Zone", its name before 2026-11-23, in an older wiki's history), and
// without users, so a wiki without them shows no names.
func commitAuthor(name string) string {
	if settings().Users == nil || name == appName || name == "Zone" {
		return ""
	}
	return name
}

var commitMu sync.Mutex

// gitCommit stages paths (in the wiki) and commits them, and only them,
// together, by user (the author; "": Zonewiki itself, or a wiki without users).
// The committer is always Zonewiki. One at a time (commitMu): another's add
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
		// No history: a write still counts as one (usage.go), by its paths.
		var rels []string
		for _, p := range paths {
			if rel, ok := wikiRel(p); ok {
				rels = append(rels, rel)
			}
		}
		usageCommit(message, rels)
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
	usageCommit(message, changed)
	packSoon()
	// The commit it made ("[main 3b1b793] Update task"), for the mark of
	// the task files (tasksetup.go).
	if m := committedRE.FindStringSubmatch(r.Stdout); m != nil {
		noteTaskCommit(changed, m[1])
	}
	return nil
}

// Packing: a commit leaves loose objects (git's own packing after a commit
// is off: package gitrepo), and every packEvery commits Zonewiki packs them in
// the background, one run at a time, beside whatever commits meanwhile; and
// once after the start, when an earlier run left many (packLoose: a Zonewiki
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
	repo := wikiRepo()
	if atLeast > 0 && looseObjects() < atLeast {
		if !repo.HasGraph() { // a wiki from before Zonewiki wrote it
			writeGraph(repo)
		}
		return
	}
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
	writeGraph(repo)
}

// writeGraph adds the commits since the last pack to the commit-graph,
// which a log of one path asks (Repo.WriteGraph); without it git reads the
// history as before, only slower.
func writeGraph(repo gitrepo.Repo) {
	if r := repo.WriteGraph(context.Background(), packTimeout); r.Code != 0 {
		logf("git: writing the commit-graph failed (the history is read without it, more slowly): %s",
			strings.TrimSpace(r.Stderr+" "+r.Stdout))
	}
}

// joinPacks makes the repository's packs one. The pack is written beside
// whatever commits (Repo.Join: it reads the whole history, minutes in a
// repository of some hundred thousand commits), and no commit waits for
// it; only the packs it stands for, and what is loose and packed now, are
// removed while no commit runs, which reads no object: under commitMu all
// of it, every save of the wiki would wait for the join, as long as the
// history makes it.
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

// excludeState keeps Zonewiki's folder .zone/ out of git, all of it: the
// session key, the caches, the query log, and the settings file, which
// holds the users and their password hashes (docs/spec/users.md: account
// data stays out of git).
func excludeState(wiki string) { excludeFromGit(wiki, "/"+stateDirName+"/*") }

// usersInHistory: a user who made a commit in the wiki's history (Zonewiki
// commits as "name <name@zone>" for a user: gitCommit), "" if none did.
// A wiki with such commits but no settings file lost its users (a clone
// of its git remote, which holds no settings file): missingUsers.
func usersInHistory() string {
	if !cfg.GitEnabled || !isDir(filepath.Join(cfg.Data, ".git")) {
		return ""
	}
	return strings.TrimSpace(runGit("log", "-1", "--author=@zone>", "--format=%an").Stdout)
}

// missingUsers: why Zonewiki won't start, if its wiki had users but its
// settings file is gone: else it would run without a login. An empty file
// says no login on purpose.
func missingUsers() error {
	if exists(configPath()) {
		return nil
	}
	if user := usersInHistory(); user != "" {
		return fmt.Errorf("this wiki had users (%s made changes in its history), but its settings file %s, "+
			"which holds them, isn't there: Zonewiki doesn't keep it in git, so a clone of the git remote has none. "+
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
	watchesMoved(map[string]string{slug: newSlug}, "", "")
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
	watchesMoved(moved, folder, newFolder)
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

// logRecord: a commit in git log's output with -z and a format that starts
// with \x1e: the format's text (head), and what git says of its files
// (fields: --name-status's letter and name, --numstat's counts and name,
// --name-only's names), each a field of its own. With -z a name is the
// file's own, whatever it holds; without it git quotes a name with a byte
// outside ASCII, a quote, a backslash or a control character
// ("private/alice/K\303\274ndigung.pdf"), which read as a path is another
// one: it named no private folder, and Changes showed it to everyone.
type logRecord struct {
	head   string
	fields []string
}

// logRecords: the commits of git log -z's output, as logRecord says. A
// commit starts after a NUL (or at the start), which no message holds, so
// a \x1e in a subject (a tag's name in "Rename tag #…") starts none.
func logRecords(out string) []logRecord {
	var records []logRecord
	if !strings.HasPrefix(out, "\x1e") {
		return nil
	}
	for _, record := range strings.Split(out[1:], "\x00\x1e") {
		records = append(records, logRecordOf(record))
	}
	return records
}

// logRecordOf: one commit of git log -z's output, without the \x1e that
// starts it.
func logRecordOf(record string) logRecord {
	head, rest, _ := strings.Cut(record, "\x00")
	rest = strings.TrimPrefix(rest, "\n")
	var fields []string
	if rest != "" {
		fields = strings.Split(strings.TrimSuffix(rest, "\x00"), "\x00")
	}
	return logRecord{head, fields}
}

// eachLogRecord streams git log -z's output (a format that starts with
// \x1e) to each, a commit at a time, until each returns true (it has what
// it wanted) or the output ends; as logRecords, a commit starts only after
// a NUL. False when it ended before each had enough.
func eachLogRecord(out io.Reader, each func(logRecord) bool) bool {
	sc := bufio.NewScanner(out)
	sc.Buffer(make([]byte, 0, 64*1024), logRecordMax)
	sc.Split(func(data []byte, atEOF bool) (int, []byte, error) {
		if i := bytes.Index(data, []byte("\x00\x1e")); i >= 0 {
			return i + 2, data[:i+1], nil // the NUL ends its file names
		}
		if atEOF && len(data) > 0 {
			return len(data), data, nil
		}
		return 0, nil, nil
	})
	for first := true; sc.Scan(); first = false {
		record := sc.Text()
		if first {
			var ok bool
			if record, ok = strings.CutPrefix(record, "\x1e"); !ok {
				return false // not a log of records
			}
		}
		if each(logRecordOf(record)) {
			return true
		}
	}
	return false
}

// logRecordMax: the longest commit eachLogRecord reads (its file names: an
// import of a large wiki is one commit of every page).
const logRecordMax = 256 << 20

// GitEntry is a commit of a page: its sha, day, relative date, time
// (YYYY-MM-DD HH:MM), message, author (commitAuthor), lines added and
// deleted, and the file's path at that commit (relative to the wiki;
// another before a rename).
type GitEntry struct {
	Sha, Date, Rel, Time, Msg, Author string
	commitMark                        // whether an assistant wrote it, which, and where its text is from
	Added, Deleted                    int
	Path                              string
	created                           bool // the commit made the file (gitLog asks whether by a rename)
}

// followMost: how many renames a file's history (gitLog) follows back.
const followMost = 100

// gitLog: the commits of a file, newest first, across renames; max 0: all.
// As git log --follow has them, but without it: --follow walks the whole
// history and compares every commit's files (2 s for a page at 200,000
// commits), where a log of one path asks the commit-graph's filters which
// commits touched it (0.1 s; WriteGraph in package gitrepo). So the path's
// own log is read, and where a commit created the file (--raw's A) git is
// asked whether that commit renamed another to it (renamedTo); the newest
// such commit ends what is the file's, and its history goes on under the
// old name from the commit before. A creation that is no rename (a page
// deleted and made again) goes on under the same name, as --follow does.
func gitLog(path string, max int) []GitEntry {
	if !cfg.GitEnabled {
		return nil
	}
	rel, ok := wikiRel(path)
	if !ok {
		return nil
	}
	var entries []GitEntry
	from, name := "HEAD", rel
	for hops := 0; ; hops++ {
		args := []string{"log"}
		if max > 0 {
			args = append(args, "-n", strconv.Itoa(max-len(entries)))
		}
		args = append(args, "-z", "--raw", "--numstat", "--date=format-local:%Y-%m-%d %H:%M",
			"--format=\x1e%H\x1f%as\x1f%ar\x1f%ad\x1f%an\x1f"+markFormat+"\x1f%s", from, "--", name)
		got := parseFileLog(runGit(args...).Stdout, name)
		cut, old := -1, ""
		for i := range got {
			if !got[i].created {
				continue
			}
			if added, deleted, src, ok := renamedTo(got[i].Sha, name); ok {
				got[i].Added, got[i].Deleted = added, deleted
				cut, old = i, src
				break
			}
		}
		if cut < 0 {
			return append(entries, got...)
		}
		entries = append(entries, got[:cut+1]...)
		if (max > 0 && len(entries) >= max) || hops == followMost {
			return entries
		}
		from, name = got[cut].Sha+"^", old
	}
}

// parseFileLog: gitLog's records of the file rel (git log -z --raw
// --numstat with gitLog's format).
func parseFileLog(out, rel string) []GitEntry {
	var entries []GitEntry
	for _, record := range logRecords(out) {
		parts := strings.SplitN(record.head, "\x1f", 8)
		if len(parts) != 8 {
			continue
		}
		e := GitEntry{Sha: parts[0], Date: parts[1], Rel: parts[2], Time: parts[3], Author: commitAuthor(parts[4]),
			Msg: parts[7], commitMark: commitMarkOf(parts[5], parts[6]), Path: rel}
		// --raw: ":100644 100644 <sha> <sha> M" and the name, a field each
		// (A: the commit made the file); --numstat: "added\tdeleted\tname".
		// A log of one path: no rename in it.
		for i := 0; i < len(record.fields); i++ {
			field := record.fields[i]
			if strings.HasPrefix(field, ":") {
				f := strings.Fields(field)
				e.created = e.created || (len(f) > 0 && strings.HasPrefix(f[len(f)-1], "A"))
				i++ // its name
				continue
			}
			if cols := strings.SplitN(field, "\t", 3); len(cols) == 3 {
				if n, err := strconv.Atoi(cols[0]); err == nil {
					e.Added += n
				}
				if n, err := strconv.Atoi(cols[1]); err == nil {
					e.Deleted += n
				}
			}
		}
		entries = append(entries, e)
	}
	return entries
}

// renamedTo: whether commit sha made rel by renaming another file (git's
// rename detection, as --follow uses it, on that one commit), which, and the
// lines the rename added and deleted.
func renamedTo(sha, rel string) (added, deleted int, old string, ok bool) {
	r := runGit("diff-tree", "-M", "-r", "--numstat", "-z", sha)
	if r.Code != 0 {
		return 0, 0, "", false
	}
	// -z: "added\tdeleted\tpath\0", a rename "added\tdeleted\t\0old\0new\0".
	fields := strings.Split(r.Stdout, "\x00")
	for i := 0; i < len(fields); i++ {
		cols := strings.SplitN(fields[i], "\t", 3)
		if len(cols) != 3 || cols[2] != "" || i+2 >= len(fields) {
			continue
		}
		src, dst := fields[i+1], fields[i+2]
		i += 2
		if dst == rel {
			added, _ = strconv.Atoi(cols[0])
			deleted, _ = strconv.Atoi(cols[1])
			return added, deleted, src, true
		}
	}
	return 0, 0, "", false
}

// LogEntry is a commit of the wiki: sha, time (YYYY-MM-DD HH:MM), how long
// ago, message, author (commitAuthor) and the files it changed (relative
// to the wiki) and what it did to each (Kinds, a letter a file: A added, M
// changed, D deleted; a rename is a D and an A); Hidden: how many more it
// changed that see didn't take, hidden their names (for hideNames).
type LogEntry struct {
	Sha, Time, Rel, Msg, Author string
	At                          int64 // when it was committed (unix seconds)
	commitMark                        // whether an assistant wrote it, which, and where its text is from
	taskMark                          // which tasks it says it changed
	Files                       []string
	Kinds                       string
	Hidden                      int
	hidden                      []string
}

// gitLogAll: the wiki's commits, newest first, at most limit; since a day
// (YYYY-MM-DD, from its start; "" for all), of these paths (pathspecs: a
// file, or a folder with a "/" after it; none for all). Of
// each commit only the files see takes (nil: all), and a commit with none
// of them is left out, as is one keep refuses (nil: none; it is given the
// commit with the files see took); git is asked for more until limit are found, twice
// as many each time (each call walks the history from the start again, so
// a fixed batch would cost quadratic time on a long history where see
// and keep take few commits).
func gitLogAll(since string, paths []string, limit int, see func(path string) bool, keep func(LogEntry) bool) []LogEntry {
	if !cfg.GitEnabled {
		return nil
	}
	batch := limit
	if see != nil || keep != nil {
		batch = max(2*limit, 50)
	}
	var entries []LogEntry
	for skip := 0; len(entries) < limit; skip, batch = skip+batch, 2*batch {
		args := []string{"log", "-z", "-n", strconv.Itoa(batch), "--skip", strconv.Itoa(skip), "--name-status", "--no-renames",
			"--date=format-local:%Y-%m-%d %H:%M", "--format=\x1e%H\x1f%ad\x1f%ar\x1f%an\x1f%ct\x1f" + markFormat + "\x1f" + tasksFormat + "\x1f%s"}
		if since != "" {
			args = append(args, "--since="+since+" 00:00")
		}
		if len(paths) > 0 {
			args = append(append(args, "--"), paths...)
		}
		records := logRecords(runGit(args...).Stdout)
		for _, record := range records {
			parts := strings.SplitN(record.head, "\x1f", 10)
			if len(parts) != 10 {
				continue
			}
			e := LogEntry{Sha: parts[0], Time: parts[1], Rel: parts[2], Author: commitAuthor(parts[3]), Msg: parts[9],
				commitMark: commitMarkOf(parts[5], parts[6]), taskMark: taskMarkOf(parts[7], parts[8])}
			e.At, _ = strconv.ParseInt(parts[4], 10, 64)
			for i := 0; i+1 < len(record.fields); i += 2 {
				kind, n := record.fields[i], record.fields[i+1]
				switch {
				case n == "" || kind == "":
				case see == nil || see(n):
					e.Files = append(e.Files, n)
					e.Kinds += kind[:1]
				default:
					e.Hidden++
					e.hidden = append(e.hidden, n)
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
// what it says it did (commitWhat: its first line, an assistant's without
// its mark); false for none.
func commitOf(version string) (sha, what string, ok bool) {
	if !shaRE.MatchString(version) {
		return "", "", false
	}
	r := runGit("log", "-n", "1", "--format=%H\x1f"+markFormat+"\x1f%s", version+"^{commit}", "--")
	parts := strings.SplitN(strings.TrimRight(r.Stdout, "\n"), "\x1f", 4)
	if r.Code != 0 || len(parts) != 4 || !shaRE.MatchString(parts[0]) {
		return "", "", false
	}
	return parts[0], commitWhat(parts[3], commitMarkOf(parts[1], parts[2])), true
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

// recentCommits: the wiki's latest commits as Changes asks them whether
// something was changed again (undoOffers): files, for each file they
// changed, those of them that did, newest first; tasks, the tasks each
// names (its taskMark), by sha.
type recentCommits struct {
	files map[string][]string
	tasks map[string]taskMark
}

// latestCommits: the wiki's n latest commits, as recentCommits.
func latestCommits(n int) recentCommits {
	out := recentCommits{files: map[string][]string{}, tasks: map[string]taskMark{}}
	r := runGit("log", "-z", "-n", strconv.Itoa(n), "--name-only", "--no-renames", "--format=%x1e%H\x1f"+tasksFormat)
	for _, record := range logRecords(r.Stdout) {
		sha, rest, _ := strings.Cut(record.head, "\x1f")
		ids, more, _ := strings.Cut(rest, "\x1f")
		out.tasks[sha] = taskMarkOf(ids, more)
		for _, name := range record.fields {
			if name != "" {
				out.files[name] = append(out.files[name], sha)
			}
		}
	}
	return out
}

// newestFirst: where each of the wiki's n latest commits stands among
// them, newest first, children before their parents (--topo-order: commits
// of one second are in their order too).
func newestFirst(n int) map[string]int {
	at := map[string]int{}
	for i, sha := range strings.Fields(runGit("rev-list", "--topo-order", "-n", strconv.Itoa(n), "HEAD", "--").Stdout) {
		at[sha] = i
	}
	return at
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

// globEscapeRE: what a pathspec with glob magic reads as a wildcard, or as
// its escape, in a folder's name.
var globEscapeRE = regexp.MustCompile(`[*?\[\]\\]`)

// GrepHit is a line of a page that matched.
type GrepHit struct {
	Slug   string
	Number int
	Line   string
}

// grepPages: every line of every page user sees matching a regex, in file
// order. git matches (git grep --no-index, so pages not yet committed count
// too) in its own process, which a timeout can kill. Without git, the
// pattern is matched as plain text. A pattern git refuses (bad syntax)
// or that takes too long is a refusal.
//
// The regex never reads a file user may not see: git is given only the
// folders of seenFolders, Zonewiki's own (_…, .…) left out below them too.
// Hits it doesn't return still tell: a pattern that gives up on a line
// holding a word (PCRE's match limit, or the timeout) is an error only
// where that word is, so a search over everything, its hits filtered
// after, read other users' pages a guess at a time.
func grepPages(user asker, pattern string, ignoreCase bool) ([]GrepHit, error) {
	slugs := visibleSlugs(user)
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
	// The pathspecs are patterns (package gitrepo takes paths literally):
	// git's, since Zonewiki wrote them, a folder's name escaped.
	specs := []string{":(exclude,glob)**/_*/**", ":(exclude,glob)**/.*/**"}
	for _, folder := range seenFolders(user) {
		if folder == "" {
			specs = append(specs, ":(glob)*.md")
		} else {
			specs = append(specs, ":(glob)"+globEscapeRE.ReplaceAllString(folder, `\$0`)+"/**/*.md")
		}
	}
	var r gitResult
	for _, perl := range tries {
		args := []string{"grep", "--no-index", "--no-color", "-I", "-n", "-z", "-E"}
		if perl {
			args[len(args)-1] = "-P"
		}
		if ignoreCase {
			args = append(args, "-i")
		}
		r = runGitEnv(grepTimeout, "", []string{"GIT_LITERAL_PATHSPECS=0"}, append(append(args, "-e", pattern, "--"), specs...)...)
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
		// What is wrong with the pattern itself is the asker's to hear
		// ("-e option, '(': missing closing parenthesis"); anything else
		// (the regex giving up on a line) only that it failed.
		stderr := strings.TrimSpace(r.Stderr)
		if msg, ok := strings.CutPrefix(stderr, "fatal: -e option, "); ok {
			return nil, refuse(msg)
		}
		logf("git grep failed (%d): %s", r.Code, stderr)
		return nil, refuse("the search failed: the pattern is too hard to match; try a simpler one")
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

// gitBlob: the file rel (relative to the wiki) as a commit has it, byte
// for byte (ok false when it hasn't): an upload put back as it was. Not
// gitShow, which reads text and makes its line ends "\n", which would
// change a picture's bytes.
func gitBlob(sha, rel string) ([]byte, bool) {
	var buf bytes.Buffer
	failed := false
	r := wikiRepo().Stream(context.Background(), gitrepo.Call{Timeout: gitTimeout}, func(out io.Reader) bool {
		_, err := io.Copy(&buf, out)
		failed = err != nil
		return false
	}, "cat-file", "blob", sha+":"+rel)
	return buf.Bytes(), r.Code == 0 && !failed
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
			// Its title from the page safePath opens: not through a symlink,
			// whose target may be a page the list's reader doesn't see.
			if p := safePath(f.Slug); p == "" {
				f.Title = mdtext.SlugDisplayName(f.Slug)
			} else if text, err := readText(p); err == nil {
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
type DeletedPage struct {
	Slug, Title, Sha, Date string
	parent                 string // the commit before the deletion, which has the page's last text
}

// deletions: every page's latest deletion in the history, newest first
// (Sha the deleting commit, Title not yet known), as read up to the commit
// head of the wiki's repository (wiki: cfg.Data then); and the titles found
// so far, by parent and slug. listDeletedPages
// reads the history once and after that only what was committed since: a
// look for deletions reads every commit of it, which takes seconds in a
// long one (3 s at 200,000 commits), and the commit-graph's filters can't
// help a log of every path.
var deletions struct {
	sync.Mutex
	wiki, head string
	pages      []DeletedPage
	titles     map[string]string
}

// listDeletedPages: the deleted pages (not on disk now), newest deletion
// first.
func listDeletedPages() []DeletedPage {
	if !cfg.GitEnabled {
		return nil
	}
	head := strings.TrimSpace(runGit("rev-parse", "--verify", "-q", "HEAD").Stdout)
	if head == "" {
		return nil
	}
	deletions.Lock()
	defer deletions.Unlock()
	if deletions.wiki != cfg.Data {
		deletions.wiki, deletions.head, deletions.pages, deletions.titles = cfg.Data, "", nil, nil
	}
	if head != deletions.head {
		since := head
		if deletions.head != "" && runGit("merge-base", "--is-ancestor", deletions.head, head).Code == 0 {
			since = deletions.head + ".." + head
		}
		newer, ok := readDeletions(since)
		if !ok {
			return nil
		}
		if since == head { // the first look, or a history that was rewritten
			deletions.pages = nil
		}
		taken := map[string]bool{}
		for _, p := range newer {
			taken[p.Slug] = true
		}
		deletions.pages = append(newer, slices.DeleteFunc(deletions.pages, func(p DeletedPage) bool { return taken[p.Slug] })...)
		deletions.head = head
	}
	existing := slugSet()
	var out []DeletedPage
	for _, p := range deletions.pages {
		if !existing[p.Slug] {
			out = append(out, p)
		}
	}
	addDeletedTitles(out)
	return out
}

// readDeletions: the pages deleted in the commits of revs (git log's
// range), each slug's latest deletion only, newest first, with the commit
// before it (DeletedPage.parent: its tree has the last text).
func readDeletions(revs string) ([]DeletedPage, bool) {
	r := runGit("log", "--diff-filter=D", "--format=%x1e%H\t%P\t%as", "--name-only", "-z", revs)
	if r.Code != 0 {
		return nil, false
	}
	seen := map[string]bool{}
	var out []DeletedPage
	for _, record := range logRecords(r.Stdout) {
		sha, parent, date := deletionCommit(record.head)
		if parent == "" {
			continue
		}
		for _, name := range record.fields {
			if !strings.HasSuffix(name, ".md") || strings.HasPrefix(name, ".") {
				continue
			}
			slug := strings.TrimSuffix(name, ".md")
			if seen[slug] || strings.HasPrefix(slug, "_assets") {
				continue
			}
			seen[slug] = true
			out = append(out, DeletedPage{Slug: slug, Sha: sha, Date: date, parent: parent})
		}
	}
	return out, true
}

// addDeletedTitles gives each page its title as its last version has it
// (deletedTitle), those not known yet asked of git in one call; under
// deletions' lock.
func addDeletedTitles(pages []DeletedPage) {
	if deletions.titles == nil {
		deletions.titles = map[string]string{}
	}
	var ask []string
	for _, p := range pages {
		// A name with a line break can't be asked in a batch (a line a name).
		if _, ok := deletions.titles[p.parent+":"+p.Slug]; !ok && !strings.ContainsAny(p.Slug, "\n\r") {
			ask = append(ask, p.parent+":"+p.Slug+".md")
		}
	}
	if len(ask) > 0 {
		// cat-file --batch: "<sha> blob <size>\n<content>\n" an object, "<name> missing\n" for none.
		out := runGitIn(gitTimeout, strings.Join(ask, "\n")+"\n", "cat-file", "--batch").Stdout
		for _, name := range ask {
			key := strings.TrimSuffix(name, ".md")
			line, rest, _ := strings.Cut(out, "\n")
			f := strings.Fields(line)
			size, err := 0, error(nil)
			if len(f) == 3 && f[1] == "blob" {
				size, err = strconv.Atoi(f[2])
			}
			if len(f) != 3 || f[1] != "blob" || err != nil || size > len(rest) {
				deletions.titles[key] = ""
				out = rest
				continue
			}
			_, slug, _ := strings.Cut(key, ":")
			deletions.titles[key] = mdtext.PageTitle(rest[:size], slug)
			out = strings.TrimPrefix(rest[size:], "\n")
		}
	}
	for i := range pages {
		if t := deletions.titles[pages[i].parent+":"+pages[i].Slug]; t != "" {
			pages[i].Title = t
		} else {
			pages[i].Title = mdtext.SlugDisplayName(pages[i].Slug)
		}
	}
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

// versionLabel: what a commit did to the page, in words: Zonewiki's own commit
// messages read alike in a list, so they are named by kind; others as written.
func versionLabel(e GitEntry, entries []GitEntry) string {
	msg := commitWhat(e.Msg, e.commitMark)
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
