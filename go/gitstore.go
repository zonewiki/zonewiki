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
	return gitResult{code, stdout.String(), stderr.String()}
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
