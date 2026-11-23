// What a start does over every task file, and when it can be left out
// (Storage in docs/spec/tasks.md).
package main

import (
	"errors"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"
	"zonewiki/internal/taskfiles"
)

// setUpTasks: what a start does over the task files: closed tasks filed by
// month, tasks filed by list and files written in Zonewiki's format
// (ArchiveOldClosed), and with users a shared task in no list given the
// list "general" (fileInGeneral). All of that reads and encodes every task
// of the wiki's years, so it is done when something may need it, not at
// every start: when the task files are as a start left them (tasksChecked)
// only what a month's end leaves to file is done (taskFiles.Unfiled).
func setUpTasks() {
	checked := tasksChecked()
	if !checked || taskFiles.Unfiled() {
		taskFiles.ArchiveOldClosed()
	}
	ok := true
	if !checked {
		if err := fileInGeneral(); err != nil {
			ok = false
			logf("The shared tasks without a list couldn't be given the list #%s (the next start tries again): %v", generalListName, err)
		}
	}
	markTasksChecked(ok)
}

// fileInGeneral: with users a shared task is always in a list. One in none
// (a wiki whose users were turned on later, a file edited by hand) goes to
// the shared list "general", made for it and pinned for the wiki, and is
// filed in its folder, in one commit by Zonewiki. A task file that can't be
// read stops it before anything is written.
func fileInGeneral() error {
	if settings().Users == nil {
		return nil
	}
	taskFiles.Lock()
	defer taskFiles.Unlock()
	type taskFile struct {
		path    string
		tasks   []*Task
		changed bool
	}
	var files []*taskFile
	moved := 0
	for _, f := range taskFiles.Files([]string{""}) {
		tasks, err := taskfiles.ReadStrict(f.Path)
		if err != nil {
			return errors.New(f.Path + ": " + err.Error())
		}
		tf := &taskFile{path: f.Path, tasks: tasks}
		for _, t := range tasks {
			if t.List == "" {
				t.List, tf.changed, moved = generalListName, true, moved+1
			}
		}
		files = append(files, tf)
	}
	if moved == 0 {
		return nil
	}
	shared, err := taskFiles.ReadListsStrict("")
	if err != nil {
		return err
	}
	taskFiles.Hold()
	var written []string
	for _, f := range files {
		if f.changed {
			if err := taskfiles.Write(f.path, f.tasks); err != nil {
				return err
			}
			written = append(written, f.path)
		}
	}
	if shared.Find(generalListName) == nil {
		shared.Lists = append(shared.Lists, &taskfiles.List{Name: generalListName, Settings: map[string]string{}})
		if !slices.Contains(shared.Pinned, generalListName) {
			shared.Pinned, shared.PinsSet = append(shared.Pinned, generalListName), true
		}
		path, err := taskFiles.WriteListsOf("", shared)
		if err != nil {
			return err
		}
		written = append(written, path...)
	}
	written = append(written, taskFiles.FileByListLocked([]string{""})...)
	logf("lists: %d shared tasks without a list are in #%s", moved, generalListName)
	return commitLists(asker{}, "Put the shared tasks without a list in #"+generalListName, written)
}

// tasksCheckedPath: the mark that the task files are as a start of this
// Zonewiki leaves them, in Zonewiki's folder of the wiki (out of git).
func tasksCheckedPath() string { return statePath("tasks-checked") }

// tasksCheckedMark: what the mark says: the layout a start leaves (2: each
// list's tasks in its folder), whether the wiki has users (with users a
// shared task is always in a list, which a start sees to), and the commit
// the repository was at.
func tasksCheckedMark(head string) string {
	return "2 users=" + strconv.FormatBool(settings().Users != nil) + " head=" + head + "\n"
}

// taskDirs: the folders of the task files, as git names them: the wiki's
// and each user's that is there.
func taskDirs() []string {
	var dirs []string
	for _, o := range taskFiles.EveryOwner() {
		if dir := taskFiles.Dir(o); isDir(dir) {
			if rel, ok := wikiRel(dir); ok {
				dirs = append(dirs, rel)
			}
		}
	}
	return dirs
}

// tasksChecked: the task files are as a start of this Zonewiki left them, so
// this start needn't read them all to file tasks by month and list, write
// hand-edited files in Zonewiki's format and give a shared task its list. The
// mark says that a start did all of that, for a wiki with users or without
// as it is now, and names the commit the task files were last Zonewiki's at
// (it follows Zonewiki's own commits of them: noteTaskCommit); and git says
// that nothing else wrote a task file since: none differs from what is
// committed (a file edited by hand, a write whose commit failed), and no
// commit after the mark's touches one (a commit by hand, one pulled from
// elsewhere). Without git, or without the mark, they are read. What a
// month's end leaves to file is seen without that (taskFiles.Unfiled).
func tasksChecked() bool {
	mark, err := os.ReadFile(tasksCheckedPath())
	if err != nil || !cfg.GitEnabled || !isDir(filepath.Join(cfg.Data, ".git")) {
		return false
	}
	head, found := strings.CutPrefix(strings.TrimSpace(string(mark)), strings.TrimSpace(tasksCheckedMark("")))
	if !found || !shaRE.MatchString(head) {
		return false
	}
	dirs := taskDirs()
	if len(dirs) == 0 {
		return true
	}
	r := runGit(append([]string{"status", "--porcelain", "--untracked-files=all", "--"}, dirs...)...)
	if r.Code != 0 || strings.TrimSpace(r.Stdout) != "" {
		return false
	}
	// (An error: the mark's commit is no longer there.)
	r = runGit(append([]string{"log", "--format=%H", head + "..HEAD", "--"}, dirs...)...)
	return r.Code == 0 && strings.TrimSpace(r.Stdout) == ""
}

// tasksSetUp: the wiki whose task files this run set up (setUpTasks), so
// its mark may follow Zonewiki's commits; the commit it moves to next, and the
// timer that moves it.
var tasksSetUp struct {
	sync.Mutex
	wiki, commit string
	timer        *time.Timer
}

// taskMarkDelay: how long after a commit of task files the mark follows it
// (once for the commits of a burst).
var taskMarkDelay = 3 * time.Second

// noteTaskCommit: Zonewiki made the commit sha of these files (as git names
// them). When a task file is among them, the mark moves to that commit
// soon, so that the next start still takes the task files for Zonewiki's own:
// to Zonewiki's commit, not to wherever the repository is by then, so a commit
// by someone else after it is still seen. A Zonewiki that stops before that
// reads them all once at its next start.
func noteTaskCommit(changed []string, sha string) {
	touches := false
	for _, f := range changed {
		touches = touches || f == taskfiles.DirName || strings.HasPrefix(f, taskfiles.DirName+"/") ||
			strings.HasPrefix(f, privateDir+"/") && strings.Contains(f, "/"+taskfiles.DirName+"/")
	}
	if !touches {
		return
	}
	tasksSetUp.Lock()
	defer tasksSetUp.Unlock()
	wiki := cfg.Data
	if tasksSetUp.wiki != wiki {
		return
	}
	tasksSetUp.commit = sha
	if tasksSetUp.timer == nil {
		tasksSetUp.timer = time.AfterFunc(taskMarkDelay, func() {
			tasksSetUp.Lock()
			tasksSetUp.timer = nil
			sha, same := tasksSetUp.commit, tasksSetUp.wiki == wiki && cfg.Data == wiki
			tasksSetUp.Unlock()
			if same {
				writeTasksMark(sha)
			}
		})
	}
}

// markTasksChecked notes that this start left the task files as it leaves
// them (tasksChecked), at the commit the repository is at; not when it
// couldn't (ok false): the next start reads them again.
func markTasksChecked(ok bool) {
	if !ok {
		_ = os.Remove(tasksCheckedPath())
		return
	}
	tasksSetUp.Lock()
	tasksSetUp.wiki = cfg.Data
	tasksSetUp.Unlock()
	head := ""
	if cfg.GitEnabled {
		head = strings.TrimSpace(runGit("rev-parse", "HEAD").Stdout)
	}
	writeTasksMark(head)
}

func writeTasksMark(commit string) {
	_ = os.MkdirAll(filepath.Dir(tasksCheckedPath()), 0o777)
	_ = os.WriteFile(tasksCheckedPath(), []byte(tasksCheckedMark(commit)), 0o666)
}
