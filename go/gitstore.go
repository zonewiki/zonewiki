// Version history in git: setting the wiki's repository
// up, commits.
package main

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"
)

// A git that hangs (a stale lock, a slow disk) must not hold its request
// forever: after this long it counts as failed, which every caller handles.
const gitTimeout = 60 * time.Second

// git (2.47+) starts its automatic maintenance after a commit as a detached
// background process. In the container Leaf is PID 1 and never reaps that
// orphan, so every commit left a zombie process behind until the PID limit
// was reached and nothing could start any more. Not detached, git runs the
// (usually trivial) maintenance itself and reaps its own children.
var gitEnv = append(os.Environ(),
	"GIT_CONFIG_COUNT=2",
	"GIT_CONFIG_KEY_0=maintenance.autoDetach", "GIT_CONFIG_VALUE_0=false",
	"GIT_CONFIG_KEY_1=gc.autoDetach", "GIT_CONFIG_VALUE_1=false",
)

type gitResult struct {
	Code           int // 124: timed out
	Stdout, Stderr string
}

// runGit runs git in the wiki. A git that can't start or times out counts
// as failed, like a git that exits non-zero.
func runGit(args ...string) gitResult { return runGitFor(gitTimeout, args...) }

func runGitFor(timeout time.Duration, args ...string) gitResult {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, "git", args...)
	cmd.Dir, cmd.Env = cfg.Data, gitEnv
	// A process git started (a hook, an alias) may hold its output open
	// after git is killed: stop waiting for it soon after.
	cmd.WaitDelay = time.Second
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	err := cmd.Run()
	if ctx.Err() == context.DeadlineExceeded {
		return gitResult{124, "", fmt.Sprintf("git timed out after %ds", int(timeout.Seconds()))}
	}
	code := 0
	var exit *exec.ExitError
	if errors.As(err, &exit) {
		code = exit.ExitCode()
	} else if err != nil {
		return gitResult{1, "", err.Error()}
	}
	return gitResult{code, newlines(stdout.String()), newlines(stderr.String())}
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
		runGit("init")
		setCommitter()
		// Before the first add: .leaf/ holds the session key.
		excludeLeafState(cfg.Data)
		gitignore := filepath.Join(cfg.Data, ".gitignore")
		if _, err := os.Stat(gitignore); err != nil {
			_ = os.WriteFile(gitignore, nil, 0o666)
		}
		runGit("add", ".")
		runGit("commit", "--allow-empty", "-m", "Initial import")
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
	if runGit("remote", "get-url", "origin").Code == 0 {
		runGit("remote", "set-url", "origin", remote)
	} else {
		runGit("remote", "add", "origin", remote)
	}
}

// setCommitter: Leaf commits as "Leaf <leaf@localhost>".
func setCommitter() {
	runGit("config", "user.email", "leaf@localhost")
	runGit("config", "user.name", "Leaf")
}

// commitAuthor: who a commit is by, as the history shows it, with users: a
// user's name, or whoever committed by hand; "" for Leaf's own, and without
// users, so a wiki without them shows no names.
func commitAuthor(name string) string {
	if settings().Users == nil || name == appName {
		return ""
	}
	return name
}

// gitCommit stages paths (in the wiki) and commits them together, by user
// (the author; "": Leaf itself, or a wiki without users). The committer is
// always Leaf.
func gitCommit(user, message string, paths ...string) {
	if !cfg.GitEnabled {
		return
	}
	args := []string{"add", "--"}
	for _, p := range paths {
		rel, ok := wikiRel(p)
		if !ok {
			return
		}
		args = append(args, rel)
	}
	runGit(args...)
	if user != "" {
		runGit("commit", "--author", user+" <"+user+"@leaf>", "-m", message)
		return
	}
	runGit("commit", "-m", message)
}

// excludeLeafState keeps .leaf/ out of git (the session key, the caches,
// the query log), but for the settings file, which is the wiki's and is
// committed (its tokens are only hashes). An older Leaf excluded all of
// .leaf/ ("/.leaf/", a folder git then never looks into): that line goes.
func excludeLeafState(wiki string) {
	exclude := filepath.Join(wiki, ".git", "info", "exclude")
	if b, err := os.ReadFile(exclude); err == nil && strings.Contains(string(b), "/.leaf/\n") {
		lines := slices.DeleteFunc(strings.SplitAfter(string(b), "\n"), func(l string) bool { return l == "/.leaf/\n" })
		if err := os.WriteFile(exclude, []byte(strings.Join(lines, "")), 0o666); err != nil {
			logf("could not update %s: %v", exclude, err)
		}
	}
	excludeFromGit(wiki, "/.leaf/*")
	excludeFromGit(wiki, "!/.leaf/config")
}

// excludeFromGit lists entry (e.g. /.leaf/*: cache, secret key) in
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

var shaRE = regexp.MustCompile(`^[0-9a-f]{7,40}$`)

// movePage moves a page's file, points every link to it at the new slug,
// and commits the move (by user) with the pages and deck files whose links
// changed.
func movePage(user, oldPath, newPath, slug, newSlug, message string) ([]string, error) {
	if err := os.MkdirAll(filepath.Dir(newPath), 0o777); err != nil {
		return nil, err
	}
	if err := os.Rename(oldPath, newPath); err != nil {
		return nil, err
	}
	changed := rewriteMovedLinks(map[string]string{slug: newSlug})
	gitCommit(user, message, append([]string{oldPath, newPath}, changed...)...)
	return changed, nil
}

// moveFolder moves every page in a folder (and its subfolders) to
// newFolder, as movePage does for one, all in one commit: returns the
// (slug, new slug) moves, A–Z, and the files whose links changed. The
// caller checks that no target exists. Other files in the folder (images…)
// stay; folders left empty go.
func moveFolder(user, folder, newFolder, message string) ([][2]string, []string, error) {
	var moves [][2]string
	var touched []string
	moved := map[string]string{}
	for _, p := range sortedPages() {
		if !strings.HasPrefix(p.Slug, folder+"/") {
			continue
		}
		newSlug := newFolder + p.Slug[len(folder):]
		newPath := filepath.Join(cfg.Data, filepath.FromSlash(newSlug+".md"))
		if err := os.MkdirAll(filepath.Dir(newPath), 0o777); err != nil {
			return nil, nil, err
		}
		if err := os.Rename(p.Path, newPath); err != nil {
			return nil, nil, err
		}
		moves = append(moves, [2]string{p.Slug, newSlug})
		moved[p.Slug] = newSlug
		touched = append(touched, p.Path, newPath)
	}
	// All the files are in place before any link is rewritten, so each
	// page's links are re-based from where it moved to.
	changed := rewriteMovedLinks(moved)
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
	gitCommit(user, message, commit...)
	return moves, still, nil
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

// GitEntry is a commit of a page: its sha, day, relative date, time
// (YYYY-MM-DD HH:MM), message, author (commitAuthor), lines added and
// deleted, and the file's path at that commit (relative to the wiki;
// another before a rename).
type GitEntry struct {
	Sha, Date, Rel, Time, Msg, Author string
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
		"--format=\x1e%H\x1f%as\x1f%ar\x1f%ad\x1f%an\x1f%s", "--", rel)
	r := runGit(args...)
	var entries []GitEntry
	records := strings.Split(r.Stdout, "\x1e")
	for _, record := range records[1:] {
		head, stats, _ := strings.Cut(record, "\n")
		parts := strings.SplitN(head, "\x1f", 6)
		if len(parts) != 6 {
			continue
		}
		e := GitEntry{Sha: parts[0], Date: parts[1], Rel: parts[2], Time: parts[3], Author: commitAuthor(parts[4]),
			Msg: parts[5], Path: rel}
		for _, line := range splitLines(stats) {
			cols := strings.SplitN(lineBody(line), "\t", 3)
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

// LogEntry is a commit of the wiki: sha, time (YYYY-MM-DD HH:MM), message,
// author (commitAuthor) and the files it changed (relative to the wiki).
type LogEntry struct {
	Sha, Time, Msg, Author string
	Files                  []string
}

// gitLogAll: the wiki's commits, newest first, at most limit; since a day
// (YYYY-MM-DD, from its start), under a folder ("" for either: all). Of
// each commit only the files see takes (nil: all), and a commit with none
// of them is left out; git is asked for more until limit are found.
func gitLogAll(since, under string, limit int, see func(path string) bool) []LogEntry {
	if !cfg.GitEnabled {
		return nil
	}
	batch := limit
	if see != nil {
		batch = max(2*limit, 50)
	}
	var entries []LogEntry
	for skip := 0; len(entries) < limit; skip += batch {
		args := []string{"log", "-n", strconv.Itoa(batch), "--skip", strconv.Itoa(skip), "--name-only",
			"--date=format:%Y-%m-%d %H:%M", "--format=\x1e%H\x1f%ad\x1f%an\x1f%s"}
		if since != "" {
			args = append(args, "--since="+since+" 00:00")
		}
		if under != "" {
			args = append(args, "--", under+"/")
		}
		records := strings.Split(runGit(args...).Stdout, "\x1e")[1:]
		for _, record := range records {
			head, names, _ := strings.Cut(record, "\n")
			parts := strings.SplitN(head, "\x1f", 4)
			if len(parts) != 4 {
				continue
			}
			e := LogEntry{Sha: parts[0], Time: parts[1], Author: commitAuthor(parts[2]), Msg: parts[3]}
			for _, n := range splitLines(names) {
				if n = lineBody(n); strings.TrimSpace(n) != "" && (see == nil || see(n)) {
					e.Files = append(e.Files, n)
				}
			}
			if see == nil || len(e.Files) > 0 {
				entries = append(entries, e)
			}
		}
		if len(records) < batch {
			break
		}
	}
	return entries[:min(limit, len(entries))]
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

// grepError: a grepPages pattern git refused (bad syntax) or that took too long.
type grepError struct{ msg string }

func (e *grepError) Error() string { return e.msg }

// grepPages: every line of every page matching a regex, in file order. git
// matches (git grep --no-index, so pages not yet committed count too) in
// its own process, which a timeout can kill. Without git, the pattern is
// matched as plain text. Only the pages in slugs (nil: every page).
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
			for n, line := range splitLines(strings.ToValidUTF8(text, "\uFFFD")) {
				line = lineBody(line)
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
		r = runGitFor(grepTimeout, append(args, "-e", pattern, "--", "*.md")...)
		if r.Code == 124 {
			return nil, &grepError{fmt.Sprintf("the search took longer than %ds; try a simpler pattern", int(grepTimeout.Seconds()))}
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
		return nil, &grepError{msg}
	}
	for _, row := range splitLines(strings.ToValidUTF8(r.Stdout, "\uFFFD")) {
		parts := strings.SplitN(lineBody(row), "\x00", 3)
		if len(parts) != 3 {
			continue
		}
		n, err := strconv.Atoi(parts[1])
		slug, ok := strings.CutSuffix(filepath.ToSlash(parts[0]), ".md")
		if err == nil && ok && slugs[slug] { // pages only: not _assets/, _deck/, …
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
// an umlaut).
func gitUncommittedFiles() []UncommittedFile {
	if !cfg.GitEnabled {
		return nil
	}
	r := runGit("status", "--porcelain", "-z", "--untracked-files=all")
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
				f.Title = pageTitle(text, f.Slug)
			} else {
				f.Title = slugDisplayName(f.Slug)
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
	for _, raw := range splitLines(r.Stdout) {
		line := trimRightSpace(raw)
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
		return pageTitle(r.Stdout, slug)
	}
	return slugDisplayName(slug)
}

var (
	editedRE   = regexp.MustCompile(`^(?:Update|Save) \S+$`)
	createdRE  = regexp.MustCompile(`^Create \S+$`)
	deletedRE  = regexp.MustCompile(`^Delete \S+$`)
	renamedRE  = regexp.MustCompile(`^Rename (\S+) to (\S+)$`)
	promotedRE = regexp.MustCompile(`^Promote (\S+) to (\S+)$`)
	appendedRE = regexp.MustCompile(`^Append (\S+) to (\S+)$`)
	restoredRE = regexp.MustCompile(`^Restore \S+ to ([0-9a-f]{7,40})$`)
)

// versionLabel: what a commit did to the page, in words: Leaf's own commit
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
			return "Moved from Scratch"
		}
		return "Link updated (a scratch note moved to the wiki)"
	}
	if m := appendedRE.FindStringSubmatch(msg); m != nil {
		switch e.Path {
		case m[2] + ".md":
			return "Scratch note appended"
		case m[1] + ".md":
			return "Appended to “" + m[2] + "”"
		}
		return "Link updated (a scratch note appended to a page)"
	}
	if m := restoredRE.FindStringSubmatch(msg); m != nil {
		for _, old := range entries {
			if strings.HasPrefix(old.Sha, m[1]) {
				return "Restored the version of " + old.Time
			}
		}
		return "Restored an older version"
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
