package main

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"zonewiki/internal/taskmodel"
)

// How Zone's requests grow with the wiki: a page view walks the wiki's
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
