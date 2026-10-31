// Folders Zone has renamed: at start-up, one still under its old name moves
// to its new one, all of them in one commit.
package main

import (
	"os"
	"path/filepath"
)

// folderMove: a folder's old and new path, relative to the wiki, with "/".
type folderMove struct{ from, to string }

// renamedFolders: where a folder of an older Zone may be: the tasks'
// (_deck/, now _tasks/), in the wiki and in each private folder.
func renamedFolders() []folderMove {
	moves := []folderMove{{legacyTasksDirName, tasksDirName}}
	dirs, _ := os.ReadDir(filepath.Join(cfg.Data, privateDir))
	for _, d := range dirs {
		if d.IsDir() {
			p := privateDir + "/" + d.Name() + "/"
			moves = append(moves, folderMove{p + legacyTasksDirName, p + tasksDirName})
		}
	}
	return moves
}

// migrateNames moves the folders still under an old name and commits the
// moves. Run again, it finds nothing to move. Zone's own folder first: .leaf/
// → .zone/, its settings file the commit's only tracked file (excludeState
// keeps the rest of both out of git, before and after).
func migrateNames() {
	moveFolders([]folderMove{{legacyStateDirName, stateDirName}}, "Move "+legacyStateDirName+"/ to "+stateDirName+"/: Leaf is called Zone")
	excludeState(cfg.Data)
	moveFolders(renamedFolders(), "Move "+legacyTasksDirName+"/ to "+tasksDirName+"/: Deck is called Tasks")
}

// moveFolders does each move whose old folder is there and commits them
// together; returns the moves done. A new folder already there (and not
// empty) keeps the move from happening, as does a failed rename: logged,
// and Zone goes on with the old folder where it can (tasksDir, stateDirIn).
func moveFolders(moves []folderMove, message string) []folderMove {
	var done []folderMove
	var commit []string
	for _, m := range moves {
		from := filepath.Join(cfg.Data, filepath.FromSlash(m.from))
		to := filepath.Join(cfg.Data, filepath.FromSlash(m.to))
		if !isDir(from) {
			continue
		}
		if isDir(to) {
			_ = os.Remove(to) // an empty one goes: the move fills it
		}
		if exists(to) {
			logf("%s/ and %s/ are both there: move what %s/ still holds into %s/, then remove %s/.",
				m.from, m.to, m.from, m.to, m.from)
			continue
		}
		// git add of a path it never knew fails, and takes the others with it.
		tracked := cfg.GitEnabled && runGit("ls-files", "--", m.from).Stdout != ""
		if err := os.Rename(from, to); err != nil {
			logf("could not move %s/ to %s/ (%v): Zone goes on with %s/", m.from, m.to, err, m.from)
			continue
		}
		logf("moved %s/ to %s/", m.from, m.to)
		done = append(done, m)
		if tracked {
			commit = append(commit, from)
		}
		commit = append(commit, to)
	}
	if len(commit) > 0 {
		gitCommit("", message, commit...)
	}
	return done
}
