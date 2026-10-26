// Version history in git (src/gitstore.py): setting the wiki's repository
// up, commits.
package main

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
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
func runGit(args ...string) gitResult {
	ctx, cancel := context.WithTimeout(context.Background(), gitTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, "git", args...)
	cmd.Dir, cmd.Env = cfg.Data, gitEnv
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	err := cmd.Run()
	if ctx.Err() == context.DeadlineExceeded {
		return gitResult{124, "", fmt.Sprintf("git timed out after %ds", int(gitTimeout.Seconds()))}
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

// gitSetup makes the wiki a repository (its files as the first commit), or
// gives an existing one a committer, and points origin at LEAF_GIT_REMOTE.
func gitSetup() {
	if !cfg.GitEnabled {
		return
	}
	if _, err := os.Stat(filepath.Join(cfg.Data, ".git")); err != nil {
		runGit("init")
		runGit("config", "user.email", "leaf@localhost")
		runGit("config", "user.name", "Leaf")
		gitignore := filepath.Join(cfg.Data, ".gitignore")
		if _, err := os.Stat(gitignore); err != nil {
			_ = os.WriteFile(gitignore, nil, 0o666)
		}
		runGit("add", ".")
		runGit("commit", "--allow-empty", "-m", "Initial import")
	} else if pyStrip(runGit("config", "user.name").Stdout) == "" {
		runGit("config", "user.email", "leaf@localhost")
		runGit("config", "user.name", "Leaf")
	}
	if cfg.GitRemote != "" {
		if runGit("remote", "get-url", "origin").Code == 0 {
			runGit("remote", "set-url", "origin", cfg.GitRemote)
		} else {
			runGit("remote", "add", "origin", cfg.GitRemote)
		}
	}
}

// gitCommit stages paths (in the wiki) and commits them together.
func gitCommit(message string, paths ...string) {
	if !cfg.GitEnabled {
		return
	}
	args := []string{"add", "--"}
	for _, p := range paths {
		rel, err := filepath.Rel(cfg.Data, p)
		if err != nil {
			return
		}
		args = append(args, rel)
	}
	runGit(args...)
	runGit("commit", "-m", message)
}

// excludeFromGit lists entry (default the .leaf dir: cache, secret key) in
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
// and commits the move with the pages and deck files whose links changed.
func movePage(oldPath, newPath, slug, newSlug, message string) ([]string, error) {
	if err := os.MkdirAll(filepath.Dir(newPath), 0o777); err != nil {
		return nil, err
	}
	if err := os.Rename(oldPath, newPath); err != nil {
		return nil, err
	}
	changed := rewriteMovedLinks(map[string]string{slug: newSlug})
	gitCommit(message, append([]string{oldPath, newPath}, changed...)...)
	return changed, nil
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
// (YYYY-MM-DD HH:MM), message, lines added and deleted, and the file's path
// at that commit (relative to the wiki; another before a rename).
type GitEntry struct {
	Sha, Date, Rel, Time, Msg string
	Added, Deleted            int
	Path                      string
}

// gitLog: the commits of a file, newest first, across renames (--follow);
// max 0: all.
func gitLog(path string, max int) []GitEntry {
	if !cfg.GitEnabled {
		return nil
	}
	rel, err := filepath.Rel(cfg.Data, path)
	if err != nil {
		return nil
	}
	rel = filepath.ToSlash(rel)
	args := []string{"log", "--follow"}
	if max > 0 {
		args = append(args, "-n", strconv.Itoa(max))
	}
	args = append(args, "--numstat", "--date=format:%Y-%m-%d %H:%M",
		"--format=\x1e%H\x1f%as\x1f%ar\x1f%ad\x1f%s", "--", rel)
	r := runGit(args...)
	var entries []GitEntry
	records := strings.Split(r.Stdout, "\x1e")
	for _, record := range records[1:] {
		head, stats, _ := strings.Cut(record, "\n")
		parts := strings.SplitN(head, "\x1f", 5)
		if len(parts) != 5 {
			continue
		}
		e := GitEntry{Sha: parts[0], Date: parts[1], Rel: parts[2], Time: parts[3], Msg: parts[4], Path: rel}
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

// gitShow: a file as a commit has it (ok false when it hasn't).
func gitShow(sha, path string) (string, bool) {
	rel, err := filepath.Rel(cfg.Data, path)
	if err != nil {
		return "", false
	}
	r := runGit("show", sha+":"+filepath.ToSlash(rel))
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
		rel, err := filepath.Rel(cfg.Data, p)
		if err != nil {
			return "", false
		}
		if rel = filepath.ToSlash(rel); !seen[rel] {
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
		line := pyRstrip(raw)
		if line == "" {
			continue
		}
		if strings.Contains(line, "\t") {
			parts := strings.SplitN(line, "\t", 3)
			sha, parent, date = parts[0], "", ""
			if len(parts) > 1 {
				if ps := pySplit(parts[1]); len(ps) > 0 {
					parent = ps[0]
				}
			}
			if len(parts) > 2 {
				date = parts[2]
			}
		} else if parent != "" && strings.HasSuffix(line, ".md") && !strings.HasPrefix(line, ".") {
			slug := strings.TrimSuffix(line, ".md")
			if existing[slug] || seen[slug] || strings.HasPrefix(slug, "_assets") {
				continue
			}
			seen[slug] = true
			title := slugDisplayName(slug)
			if r2 := runGit("show", parent+":"+line); r2.Code == 0 {
				title = pageTitle(r2.Stdout, slug)
			}
			out = append(out, DeletedPage{slug, title, sha, date})
		}
	}
	return out
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
