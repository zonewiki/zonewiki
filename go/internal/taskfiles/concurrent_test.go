package taskfiles

import (
	"fmt"
	"slices"
	"sync"
	"sync/atomic"
	"testing"
)

// Readers beside writers, as a team's requests are: what the store promises
// them (Lock, view), checked while both run, and under the race detector
// (./run.sh test runs go test -race): a reader sees every file of a write or
// none, so every task is there once, whatever moves between files meanwhile
// (a task closed, reopened, moved to another list); and a task handed out
// without a copy (lookAt) is only looked at.
func TestReadersBesideWriters(t *testing.T) {
	f := newStore(t, "anna", "bob")
	s := f.Store
	const perList, private, rounds = 12, 6, 150
	lists := []string{"shop", "site", ""}

	// The wiki: open tasks in two lists and no list, closed ones of this
	// month and the last, and each user's own.
	var shared, own []string
	seed := func(user string, tasks ...*Task) {
		t.Helper()
		s.Lock()
		defer s.Unlock()
		active, closed, err := s.ReadForWriteLocked(user)
		if err != nil {
			t.Fatal(err)
		}
		for _, x := range tasks {
			if x.ClosedAt == "" {
				active = append(active, x)
			} else {
				closed = append(closed, x)
			}
		}
		if err := s.WriteBothLocked(user, active, closed, "seed"); err != nil {
			t.Fatal(err)
		}
	}
	var tasks []*Task
	for _, list := range lists {
		for i := range perList {
			id := fmt.Sprintf("%s-%d", cmpOr(list, "none"), i)
			x := task(id, "", [...]string{"", "2026-06-01T09:00:00", "2026-05-10T09:00:00"}[i%3])
			x.List = list
			tasks, shared = append(tasks, x), append(shared, id)
		}
	}
	seed("anna", tasks...)
	for _, u := range []string{"anna", "bob"} {
		tasks = nil
		for i := range private {
			id := fmt.Sprintf("%s-%d", u, i)
			tasks = append(tasks, task(id, u, ""))
			if u == "anna" {
				own = append(own, id)
			}
		}
		seed(u, tasks...)
	}
	sees := map[string]int{"anna": len(shared) + private, "bob": len(shared) + private}

	var failed atomic.Value
	fail := func(format string, args ...any) { failed.CompareAndSwap(nil, fmt.Sprintf(format, args...)) }
	var writers, readers sync.WaitGroup
	done := make(chan struct{})

	// A write of one task, as updateTask does it: read for the write, change,
	// write both, commit.
	write := func(user, id string, change func(x *Task, active, closed []*Task) ([]*Task, []*Task)) {
		s.Lock()
		defer s.Unlock()
		active, closed, err := s.ReadTaskForWriteLocked(user, id)
		if err != nil {
			fail("read for write: %v", err)
			return
		}
		for _, x := range slices.Concat(active, closed) {
			if x.ID == id {
				active, closed = change(x, active, closed)
				if err := s.WriteBothLocked(user, active, closed, "change"); err != nil {
					fail("write: %v", err)
				}
				return
			}
		}
		fail("%s finds no task %s to write", user, id)
	}
	drop := func(tasks []*Task, id string) []*Task {
		return slices.DeleteFunc(tasks, func(x *Task) bool { return x.ID == id })
	}
	for w, user := range []string{"anna", "bob", "anna"} {
		writers.Add(1)
		go func() {
			defer writers.Done()
			for i := range rounds {
				id := shared[(i*7+w*11)%len(shared)]
				if user == "anna" && i%5 == 0 {
					id = own[i%len(own)]
				}
				write(user, id, func(x *Task, active, closed []*Task) ([]*Task, []*Task) {
					switch (i + w) % 4 {
					case 0: // its text
						x.Title = fmt.Sprintf("Changed %d by %s", i, user)
					case 1: // to another list: between two folders
						if x.Owner == "" {
							x.List = lists[(slices.Index(lists, x.List)+1)%len(lists)]
						}
					default: // closed, or open again: between two files
						if x.ClosedAt == "" {
							x.ClosedAt = "2026-06-01T11:00:00"
							return drop(active, x.ID), append(closed, x)
						}
						x.ClosedAt = ""
						return append(active, x), drop(closed, x.ID)
					}
					return active, closed
				})
			}
		}()
	}

	// A write of several tasks, as a batch does it: what holds them read,
	// each changed, written in one.
	writers.Add(1)
	go func() {
		defer writers.Done()
		for i := range rounds {
			set := map[string]bool{own[i%len(own)]: true}
			for k := range 4 {
				set[shared[(i*5+k*13)%len(shared)]] = true
			}
			func() {
				s.Lock()
				defer s.Unlock()
				active, closed, err := s.ReadTasksForWriteLocked("anna", set)
				if err != nil {
					fail("read for a batch: %v", err)
					return
				}
				n := 0
				for _, x := range slices.Concat(active, closed) {
					if set[x.ID] {
						x.Tags, n = []string{fmt.Sprintf("batch-%d", i)}, n+1
					}
				}
				if n != len(set) {
					fail("a batch of %d tasks finds %d", len(set), n)
				}
				if err := s.WriteBothLocked("anna", active, closed, "batch"); err != nil {
					fail("write of a batch: %v", err)
				}
			}()
		}
	}()

	// once: tasks has every task user sees, each once.
	once := func(what, user string, tasks []*Task) {
		ids := map[string]bool{}
		for _, x := range tasks {
			if ids[x.ID] {
				fail("%s shows %s the task %s twice", what, user, x.ID)
			}
			ids[x.ID] = true
		}
		if len(ids) != sees[user] {
			fail("%s shows %s %d tasks of %d", what, user, len(ids), sees[user])
		}
	}
	for r, user := range []string{"anna", "bob", "anna", "bob"} {
		readers.Add(1)
		go func() {
			defer readers.Done()
			for i := 0; ; i++ {
				select {
				case <-done:
					return
				default:
				}
				switch (i + r) % 6 {
				case 0:
					once("All", user, s.All(user))
				case 1:
					open, closed := s.Snapshot(user)
					once("Snapshot", user, append(open, closed...))
				case 2:
					// What a reader is handed is its own: changing it changes
					// nobody else's.
					for _, x := range s.Open(user) {
						x.Title, x.Tags = "a reader's", append(x.Tags, "mine")
					}
				case 3:
					page, total := s.ClosedPage(user, "2026", func(*Task) bool { return true }, 5, "")
					if n := s.CountClosedInYear(user, "2026", func(*Task) bool { return true }); len(page) > total || n < 0 {
						fail("ClosedPage: %d of %d", len(page), total)
					}
					s.RecentClosed(user, func(x *Task) bool { return x.List == "shop" })
				case 4:
					s.Stamp(user)
					s.Word(user, []Place{{Owner: "", Every: true}, {Owner: user, Every: true}})
					s.ClosedYears(user, func(*Task) bool { return true })
				case 5:
					// As the search does: every file looked through, nothing copied.
					for _, file := range s.Files(s.EveryOwner()) {
						Look(file.Path, file.Owner, func(x *Task) { _ = x.Title + x.List })
					}
					s.Every()
				}
			}
		}()
	}
	writers.Wait()
	close(done)
	readers.Wait()
	if msg := failed.Load(); msg != nil {
		t.Fatal(msg)
	}
	// And after all of it nothing is lost, doubled or a reader's.
	for _, user := range []string{"anna", "bob"} {
		all := s.All(user)
		once("All, at the end", user, all)
		for _, x := range all {
			if x.Title == "a reader's" || slices.Contains(x.Tags, "mine") {
				t.Fatalf("a reader's change of its copy is in the task %s", x.ID)
			}
		}
	}
	if msg := failed.Load(); msg != nil {
		t.Fatal(msg)
	}
}

func cmpOr(a, b string) string {
	if a != "" {
		return a
	}
	return b
}
