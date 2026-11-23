package main

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
	"zonewiki/internal/settingsfile"
	"zonewiki/internal/taskmodel"
)

// How Zonewiki's requests grow with the wiki: a page view walks the wiki's
// folders and looks at every page's links (for the backlinks) and every
// open task (for the linked tasks), and the tasks API reads the task files
// whole. The numbers that came out are in docs/spec/deployment.md (How big
// a wiki can be); to measure again:
//
//	go test -run '^$' -bench . -benchtime 20x
//
// benchWiki: a wiki of n pages in folders of 100, each linking two others,
// with open and closed tasks, a tenth of the open ones linking a page.
func benchWiki(b *testing.B, pages, open, closed int) http.Handler {
	b.Helper()
	testWiki(b, nil)
	for i := range pages {
		dir := filepath.Join(cfg.Data, fmt.Sprintf("f%03d", i/100))
		_ = os.MkdirAll(dir, 0o777)
		text := fmt.Sprintf("---\ntags: [bench, t%d]\n---\n# Page %d\n\nSome text about page %d, with a link to [[f%03d/p%d]] "+
			"and one to [another](/page/f%03d/p%d).\n\n## Details\n\n- [ ] an item\n- a point\n\nMore text here, long enough to be a paragraph "+
			"that a reader would read and a search would index.\n", i%50, i, i, ((i+1)%pages)/100, (i+1)%pages, ((i*7)%pages)/100, (i*7)%pages)
		if err := os.WriteFile(filepath.Join(dir, fmt.Sprintf("p%d.md", i)), []byte(text), 0o666); err != nil {
			b.Fatal(err)
		}
	}
	_ = os.WriteFile(filepath.Join(cfg.Data, "index.md"), []byte("# Home\n\n[[f000/p0]]\n"), 0o666)
	mk := func(n int, done bool) []*Task {
		out := make([]*Task, n)
		for i := range out {
			t := &Task{ID: fmt.Sprintf("%08d-0000-4000-8000-%012d", i, n), Title: fmt.Sprintf("Task %d", i),
				Description: "Notes of the task, a line or two.", Tags: []string{"bench"}, CreatedAt: "2026-01-02T10:00:00"}
			if i%10 == 0 {
				t.Title += fmt.Sprintf(" for [[f%03d/p%d]]", (i%pages)/100, i%pages)
			}
			if done {
				t.ClosedAt = "2026-02-03T10:00:00"
			}
			out[i] = t
		}
		return out
	}
	for path, tasks := range map[string][]*Task{taskFiles.OpenPath(""): mk(open, false), taskFiles.ClosedPath(""): mk(closed, true)} {
		text, err := taskmodel.Encode(tasks)
		if err != nil {
			b.Fatal(err)
		}
		_ = os.MkdirAll(filepath.Dir(path), 0o777)
		if err := os.WriteFile(path, []byte(text), 0o666); err != nil {
			b.Fatal(err)
		}
	}
	return newHandler()
}

func benchGet(b *testing.B, h http.Handler, path string) {
	b.Helper()
	get := func() {
		w := httptest.NewRecorder()
		r := httptest.NewRequest("GET", path, nil)
		r.Host = "localhost:5000"
		h.ServeHTTP(w, r)
		if w.Code != http.StatusOK {
			b.Fatalf("GET %s: %d %s", path, w.Code, w.Body.String())
		}
	}
	get() // the first view fills the per-page caches
	b.ResetTimer()
	for range b.N {
		get()
	}
}

func BenchmarkPageView(b *testing.B) {
	for _, pages := range []int{1000, 5000, 20000} {
		b.Run(fmt.Sprintf("pages=%d", pages), func(b *testing.B) {
			benchGet(b, benchWiki(b, pages, 500, 2000), "/page/f000/p7")
		})
	}
}

func BenchmarkPageViewTasks(b *testing.B) {
	for _, open := range []int{500, 5000} {
		b.Run(fmt.Sprintf("open=%d", open), func(b *testing.B) {
			benchGet(b, benchWiki(b, 1000, open, 4*open), "/page/f000/p7")
		})
	}
}

func BenchmarkTasksAPI(b *testing.B) {
	for _, open := range []int{500, 5000} {
		b.Run(fmt.Sprintf("open=%d", open), func(b *testing.B) {
			benchGet(b, benchWiki(b, 100, open, 4*open), "/api/tasks")
		})
	}
}

func BenchmarkPageList(b *testing.B) {
	for _, pages := range []int{1000, 20000} {
		b.Run(fmt.Sprintf("pages=%d", pages), func(b *testing.B) {
			benchGet(b, benchWiki(b, pages, 500, 2000), "/api/pages-list")
		})
	}
}

// BenchmarkWhoChanged: what saying who changed a page last costs a search
// (lastchange.go): the scan of the history that the first search starts
// (a history whose pages weren't all changed since its first commit, so
// read to its start), the commits since that a search after a commit asks
// git for, and a search when nothing moved; and git's log alone, which is
// most of the scan. Also the memory the pages' last commits take.
func BenchmarkWhoChanged(b *testing.B) {
	for _, size := range [][2]int{{1000, 5000}, {20000, 100000}} {
		pages, commits := size[0], size[1]
		b.Run(fmt.Sprintf("pages=%d,commits=%d", pages, commits), func(b *testing.B) {
			benchHistory(b, pages, commits)
			rq := func() *Req { return &Req{user: asUser("alice")} }
			old := lastCommitWait
			lastCommitWait = time.Hour
			b.Cleanup(func() { lastCommitWait = old })
			b.Run("scan", func(b *testing.B) {
				var before, after runtime.MemStats
				for range b.N {
					lastCommits.Lock()
					lastCommits.wiki, lastCommits.pages = "", nil
					lastCommits.Unlock()
					runtime.GC()
					runtime.ReadMemStats(&before)
					if whoChanged(rq()) == nil {
						b.Fatal("not known")
					}
					runtime.GC()
					runtime.ReadMemStats(&after)
				}
				b.ReportMetric(float64(after.HeapAlloc-min(before.HeapAlloc, after.HeapAlloc))/float64(pages), "heap-bytes/page")
			})
			b.Run("git-log-alone", func(b *testing.B) { // what of the scan is git's
				for range b.N {
					runGit("log", "-z", "--name-status", "--no-renames", lastCommitFormat, "HEAD", "--")
				}
			})
			b.Run("after-a-commit", func(b *testing.B) {
				for i := range b.N {
					b.StopTimer()
					path := filepath.Join(cfg.Data, fmt.Sprintf("f%d/page-%d.md", i%50, i))
					_ = os.WriteFile(path, []byte(fmt.Sprintf("# Page\n\nChange %d\n", i)), 0o666)
					if err := gitCommit("alice", "Update", path); err != nil {
						b.Fatal(err)
					}
					b.StartTimer()
					if whoChanged(rq()) == nil {
						b.Fatal("not known")
					}
				}
			})
			b.Run("nothing-moved", func(b *testing.B) {
				for range b.N {
					if whoChanged(rq()) == nil {
						b.Fatal("not known")
					}
				}
			})
		})
	}
}

// benchHistory: a wiki in git of n pages, all made by one commit, and then
// commits more of a page each (by alice, bob or carol, every fifth by an
// assistant), most of them to a fifth of the pages; its files as old as
// that, and the users.
func benchHistory(b *testing.B, pages, commits int) {
	b.Helper()
	if _, err := exec.LookPath("git"); err != nil {
		b.Skip("no git")
	}
	testWiki(b, nil)
	if err := settingsfile.Write(configPath(), "[user alice]\n[user bob]\n[user carol]\n"); err != nil {
		b.Fatal(err)
	}
	if f, err := readConfigFile(configPath()); err == nil {
		if s, err := loadSettings(f); err == nil {
			current.Store(s)
		}
	}
	cfg.GitEnabled = true
	if r := runGit("init", "-q", "-b", "main"); r.Code != 0 {
		b.Fatal(r.Stderr)
	}
	name := func(i int) string { return fmt.Sprintf("f%d/page-%d.md", i%50, i) }
	var stream strings.Builder
	at := time.Now().Add(-time.Duration(commits+1) * time.Minute).Unix()
	data := func(s string) { fmt.Fprintf(&stream, "data %d\n%s\n", len(s), s) }
	fmt.Fprintf(&stream, "commit refs/heads/main\nauthor Zonewiki <zonewiki@localhost> %d +0000\ncommitter Zonewiki <zonewiki@localhost> %d +0000\n", at, at)
	data("Initial import")
	for i := range pages {
		fmt.Fprintf(&stream, "M 100644 inline %s\n", name(i))
		data(fmt.Sprintf("# Page %d\n\nSome text.\n", i))
	}
	users := []string{"alice", "bob", "carol"}
	for c := range commits {
		at += 60
		page := (c * 7919) % (pages / 5)
		if c%5 == 4 {
			page = (c * 104729) % pages
		}
		msg := "Update " + name(page)
		if c%5 == 0 {
			msg += " (MCP)" + commitTrailers("Claude", "")
		}
		u := users[c%3]
		fmt.Fprintf(&stream, "commit refs/heads/main\nauthor %s <%s@zone> %d +0000\ncommitter Zonewiki <zonewiki@localhost> %d +0000\n", u, u, at, at)
		data(msg)
		fmt.Fprintf(&stream, "M 100644 inline %s\n", name(page))
		data(fmt.Sprintf("# Page %d\n\nChange %d.\n", page, c))
	}
	if r := runGitIn(gitTimeout, stream.String(), "fast-import", "--quiet"); r.Code != 0 {
		b.Fatal(r.Stderr)
	}
	if r := runGit("checkout", "-q", "-f", "main"); r.Code != 0 {
		b.Fatal(r.Stderr)
	}
	setCommitter()
	past := time.Unix(at-3600*24*365, 0)
	for i := range pages {
		_ = os.Chtimes(filepath.Join(cfg.Data, name(i)), past, past)
	}
}
