// Who changed a page last, for the line under a search result: its last
// commit's author, and whether an assistant made it and which (Who changed
// a page last, in docs/spec/search.md). Kept in memory by the page's path,
// from one pass over the history, newest first, until every page is seen,
// and then from the commits since, which a search asks git for once the
// repository moved: never git per result, nor the history per search.
package main

import (
	"context"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"zonewiki/internal/gitrepo"
)

// lastCommit: what a page's last commit says of who made it.
type lastCommit struct {
	author    string // the commit's author as git has it (commitAuthor says whether it is shown)
	assistant string // the connection an assistant made it through (commitMark.Assistant), "" for none
	at        int64  // when it was committed (the committer's time, unix seconds)
}

// lastCommits: the pages' last commits by their paths in git, as of head.
var lastCommits struct {
	sync.RWMutex
	wiki     string // the wiki they are of (cfg.Data then)
	gen      int    // the scan filling them; a scan of an older one is dropped
	head     string // the commit they are as of; "" for an empty repository
	stamp    string // the reflog's stamp when head was asked (reflogStamp)
	pages    map[string]lastCommit
	done     chan struct{} // closed when the scan of gen ended; nil before one started
	deadline time.Time     // how long a search waits for that scan
	broken   bool          // a scan failed: none again in this run (it would fail again)

	moves sync.Mutex // one look at the repository's moves at a time
}

// lastCommitGits counts the git processes lastCommits started, and
// scannedCommits the commits their scans read (the tests' measures).
var lastCommitGits, scannedCommits atomic.Int64

// lastCommitWait: how long after the scan of the history started
// (scanLastCommits) a search waits for it before its results go without
// who changed them; the scan goes on, and later searches have it. A
// history of 5,000 commits takes some 40 ms, one of 100,000 two seconds
// (BenchmarkWhoChanged).
var lastCommitWait = time.Second

// scanTimeout: the most a scan of the history may take.
const scanTimeout = 10 * time.Minute

// lastCommitFormat: what the scan and the moves read of a commit: its
// author, its time and its trailers that say whether an assistant made it
// and which (markFormat, commitMarkOf).
const lastCommitFormat = "--format=\x1e%an\x1f%ct\x1f" + markFormat

// lastCommitsSoon starts the scan of the history if it hasn't started (in
// this wiki), without waiting for it: a search starts it before it ranks,
// so that the two overlap.
func lastCommitsSoon(rq *Req) {
	if !cfg.GitEnabled {
		return
	}
	lc := &lastCommits
	lc.RLock()
	started := lc.wiki == cfg.Data && (lc.done != nil || lc.broken)
	lc.RUnlock()
	if started {
		return
	}
	lc.Lock()
	defer lc.Unlock()
	if lc.wiki != cfg.Data {
		lc.wiki, lc.done, lc.broken = cfg.Data, nil, false
	}
	if lc.done == nil && !lc.broken {
		startScan(rq.pageFiles())
	}
}

// whoChanged: a function saying who made a page's last change (changedBy)
// to rq's user, with the pages' last commits brought up to date with the
// repository first; nil when they aren't known (no git, or the scan is not
// done in time).
func whoChanged(rq *Req) func(slug string, mtime time.Time) string {
	if !cfg.GitEnabled || !lastCommitsNow(rq) {
		return nil
	}
	who := rq.who()
	return func(slug string, mtime time.Time) string { return changedBy(who, slug, mtime) }
}

// changedBy: "by anna", "by anna through Claude" (an assistant whose
// connection gave its name, worded as on Changes: commitMark.Via), "by
// anna's assistant" (a token's, or one that gave none), "by an assistant":
// who made the last commit of the page, as who may be told; "" when that
// isn't known, when the file changed after it (text not committed, a
// checkout: whose isn't known), or when there is nobody to name and no
// assistant (Zonewiki's own commit, a wiki without users: commitAuthor). A
// public wiki's visitor learns no user's name, as in a page's history
// (pageLog), but whether an assistant made it, and which.
func changedBy(who asker, slug string, mtime time.Time) string {
	lastCommits.RLock()
	c, ok := lastCommits.pages[slug+".md"]
	lastCommits.RUnlock()
	// Git keeps whole seconds: a file written in the second it was
	// committed is as committed.
	if !ok || mtime.Unix() > c.at {
		return ""
	}
	name := commitAuthor(c.author)
	if who.Name == visitor && c.author != visitorAuthor {
		name = ""
	}
	if m := (commitMark{Assistant: c.assistant}); m.ByAssistant() {
		switch {
		case name == "":
			name = "an assistant"
		case m.Via() == "":
			name += "'s assistant"
		}
		if m.Via() != "" {
			name += " through " + m.Via()
		}
	}
	if name == "" {
		return ""
	}
	return "by " + name
}

// lastCommitsNow brings lastCommits up to date with the repository: the
// first time, a scan of the history (scanLastCommits, waited for until its
// deadline); after that, when the reflog moved, the commits since head
// (lastCommitsSince), or the scan again when head is no longer in the
// history (a reset, a rewrite). False when they aren't known.
func lastCommitsNow(rq *Req) bool {
	lc := &lastCommits
	lc.moves.Lock()
	defer lc.moves.Unlock()
	lastCommitsSoon(rq)
	lc.RLock()
	done, deadline := lc.done, lc.deadline
	lc.RUnlock()
	if done == nil || !waitUntil(done, deadline) {
		return false
	}
	stamp, stamped := reflogStamp(cfg.Data)
	lc.RLock()
	head, moved, broken := lc.head, !stamped || stamp != lc.stamp, lc.broken
	lc.RUnlock()
	if broken {
		return false
	}
	if !moved {
		return true
	}
	now := headOf(wikiRepo())
	switch {
	case now == head:
	case head != "" && now != "" && lastCommitGit("merge-base", "--is-ancestor", head, now).Code == 0:
		if !lastCommitsSince(head, now) {
			return false
		}
	default:
		lc.Lock()
		if lc.wiki == cfg.Data && !lc.broken {
			startScan(rq.pageFiles())
		}
		done, deadline = lc.done, lc.deadline
		lc.Unlock()
		return waitUntil(done, deadline)
	}
	lc.Lock()
	if lc.wiki == cfg.Data {
		lc.head, lc.stamp = now, stamp
	}
	lc.Unlock()
	return true
}

// waitUntil: done is closed by then.
func waitUntil(done chan struct{}, deadline time.Time) bool {
	select {
	case <-done:
		return true
	default:
	}
	t := time.NewTimer(time.Until(deadline))
	defer t.Stop()
	select {
	case <-done:
		return true
	case <-t.C:
		return false
	}
}

// startScan starts a scan of the history in the background, for these
// pages (the ones it looks for); under lastCommits' lock. Until it is done
// the pages' last commits are not known.
func startScan(pages []PageFile) {
	lc := &lastCommits
	lc.gen++
	gen, wiki, done := lc.gen, lc.wiki, make(chan struct{})
	lc.done, lc.deadline, lc.pages = done, time.Now().Add(lastCommitWait), nil
	want := make(map[string]bool, len(pages))
	for _, p := range pages {
		want[p.Slug+".md"] = true
	}
	// The repository by its folder, not by cfg: a test's next wiki may be
	// cfg's by the time the scan reads.
	repo := wikiRepo()
	go func() {
		defer close(done)
		stamp, _ := reflogStamp(repo.Dir)
		head, pages, err := scanLastCommits(repo, want)
		lc.Lock()
		defer lc.Unlock()
		if lc.gen != gen || lc.wiki != wiki {
			return // another wiki's (a test's), or another scan's
		}
		if err != nil {
			logf("could not read who changed the pages last (%v): search results don't say until Zonewiki starts again", err)
			lc.broken = true
			return
		}
		lc.head, lc.stamp, lc.pages = head, stamp, pages
	}()
}

// scanLastCommits: the last commit of each of the pages wanted, from the
// history newest first, read until every one of them is seen (a page never
// committed: to the history's start); and the commit it is as of. A page
// whose last change in the history deleted it (a file on disk that git no
// longer has) has none.
func scanLastCommits(repo gitrepo.Repo, want map[string]bool) (string, map[string]lastCommit, error) {
	pages := map[string]lastCommit{}
	head := headOf(repo)
	if head == "" || len(want) == 0 {
		return head, pages, nil // no commit yet, or no page
	}
	names := map[string]string{}
	lastCommitGits.Add(1)
	res := repo.Stream(context.Background(), gitrepo.Call{Timeout: scanTimeout}, func(out io.Reader) bool {
		return eachLogRecord(out, func(r logRecord) bool {
			scannedCommits.Add(1)
			c, ok := lastCommitOf(r.head, names)
			for i := 0; ok && i+1 < len(r.fields); i += 2 {
				name := r.fields[i+1]
				if !want[name] {
					continue
				}
				delete(want, name) // seen: older commits of it are no news
				if r.fields[i] != "D" {
					pages[name] = c
				}
			}
			return len(want) == 0
		})
	}, "log", "-z", "--name-status", "--no-renames", lastCommitFormat, head, "--")
	if res.Code != 0 {
		return "", nil, fmt.Errorf("git log: %s", strings.TrimSpace(res.Stderr))
	}
	return head, pages, nil
}

// lastCommitsSince takes the commits from head to now into lastCommits:
// newest first, a page's first is its last; a page deleted is no longer
// there. False when git fails.
func lastCommitsSince(head, now string) bool {
	r := lastCommitGit("log", "-z", "--name-status", "--no-renames", lastCommitFormat, head+".."+now, "--")
	if r.Code != 0 {
		return false
	}
	type change struct {
		lastCommit
		gone bool
	}
	changes := map[string]change{}
	names := map[string]string{}
	for _, record := range logRecords(r.Stdout) {
		c, ok := lastCommitOf(record.head, names)
		for i := 0; ok && i+1 < len(record.fields); i += 2 {
			name := record.fields[i+1]
			if _, seen := changes[name]; seen || !strings.HasSuffix(name, ".md") {
				continue
			}
			changes[name] = change{c, record.fields[i] == "D"}
		}
	}
	lc := &lastCommits
	lc.Lock()
	defer lc.Unlock()
	if lc.wiki != cfg.Data || lc.pages == nil {
		return false
	}
	for name, c := range changes {
		if c.gone {
			delete(lc.pages, name)
		} else {
			lc.pages[name] = c.lastCommit
		}
	}
	return true
}

// lastCommitOf: a commit as lastCommitFormat gives it. names keeps one
// copy of each author's and assistant's name for all of a scan's pages.
func lastCommitOf(head string, names map[string]string) (lastCommit, bool) {
	parts := strings.SplitN(head, "\x1f", 4)
	if len(parts) != 4 {
		return lastCommit{}, false
	}
	mark := commitMarkOf(parts[2], parts[3])
	at, err := strconv.ParseInt(parts[1], 10, 64)
	if err != nil {
		return lastCommit{}, false
	}
	one := func(s string) string {
		if n, ok := names[s]; ok {
			return n
		}
		s = strings.Clone(s)
		names[s] = s
		return s
	}
	return lastCommit{author: one(parts[0]), assistant: one(mark.Assistant), at: at}, true
}

// headOf: the commit a repository is at; "" for none yet.
func headOf(repo gitrepo.Repo) string {
	lastCommitGits.Add(1)
	r := repo.Run(context.Background(), gitrepo.Call{Timeout: gitTimeout}, "rev-parse", "-q", "--verify", "HEAD^{commit}")
	return strings.TrimSpace(r.Stdout)
}

// lastCommitGit: runGit, counted.
func lastCommitGit(args ...string) gitResult {
	lastCommitGits.Add(1)
	return runGit(args...)
}

// reflogStamp: the time and size of the reflog of the wiki in that folder,
// which every commit, reset, checkout and pull changes, Zonewiki's and a
// terminal's alike; false without one (git is asked each time then).
func reflogStamp(wiki string) (string, bool) {
	st, err := os.Stat(filepath.Join(wiki, ".git", "logs", "HEAD"))
	if err != nil {
		return "", false
	}
	return strconv.FormatInt(st.ModTime().UnixNano(), 10) + "/" + strconv.FormatInt(st.Size(), 10), true
}
