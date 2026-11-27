// Moving a whole wiki (Moving a wiki in docs/spec/deployment.md): zonewiki
// export writes it to one file, zonewiki import makes a wiki from such a
// file in an empty folder. The file is a clone of the wiki's git repository
// with its settings file beside it (internal/wikiarchive), so a wiki moves
// with its history and with its users, who sign in as before.
//
// An export reads the wiki as a git client would, so it works while Zonewiki
// runs. An import takes the file for what anyone may have made (a hosted
// Zonewiki imports its customers' uploads): it reads the history's objects and
// refs and the settings file, fetches the history into a repository of its
// own and checks the files out of that, so the archive's hooks, git config
// and work tree are never used.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"io/fs"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"syscall"
	"time"
	"zonewiki/internal/gitrepo"
	"zonewiki/internal/settingsfile"
	"zonewiki/internal/wikiarchive"
)

const exportUsage = `Usage: zonewiki export [--output FILE] [--work FOLDER]
  write the whole wiki in ZONE_DATA to one file, to move it to another Zonewiki (zonewiki import):
  its pages, uploads, tasks and history, and its settings file with the users.
  It works while Zonewiki runs. The file is as private as the wiki: it holds every user's
  private pages and the hashes of their passwords.`

const importUsage = `Usage: zonewiki import [--max-size SIZE] [--users-only] [--work FOLDER] FILE
  make the wiki in ZONE_DATA from a file zonewiki export wrote (-: read it from stdin).
  ZONE_DATA must be an empty folder: an import makes a whole wiki and never adds to one.
  The settings that are a host's (hosts, base-url, git-remote, trusted-proxies, storage-max)
  stay in the file; everyone signs in again, with the password they had.`

// transferTimeout: how long one git call of an export or an import may
// take: the clone, the fetch or the checkout of a whole wiki.
const transferTimeout = time.Hour

// importDirName: the folder in ZONE_DATA an import puts the wiki together
// in. It is there from the import's first step to its last, so a folder
// that holds it holds an import that didn't finish: Zonewiki doesn't start
// there (unfinishedImport), and the next import starts over.
const importDirName = ".zone-import"

// importedName: the file in .zone/ an import leaves, which says the folder
// is a wiki though it may have no .zone-format yet (an archive from before
// that file): newWikiError.
const importedName = "imported"

// transferGit runs git in dir, for as long as a whole wiki takes.
func transferGit(dir string, env []string, args ...string) gitResult {
	return gitrepo.Repo{Dir: dir}.Run(context.Background(), gitrepo.Call{Timeout: transferTimeout, Env: env}, args...)
}

// onInterrupt runs cleanup and exits when the command is stopped (^C, a
// kill); the returned function ends that, once the command is done.
func onInterrupt(cleanup func()) func() {
	ch, done := make(chan os.Signal, 1), make(chan struct{})
	signal.Notify(ch, os.Interrupt, syscall.SIGTERM)
	go func() {
		select {
		case <-ch:
			cleanup()
			os.Exit(130)
		case <-done:
		}
	}()
	return func() { signal.Stop(ch); close(done) }
}

func exportMain(args []string, getenv func(string) string, stdout, stderr io.Writer) int {
	if isHelp(firstOf(args)) {
		fmt.Fprintln(stdout, exportUsage)
		return 0
	}
	fl := flag.NewFlagSet("zonewiki export", flag.ContinueOnError)
	fl.SetOutput(stderr)
	output := fl.String("output", "", "the `file` to write (default: zonewiki-YYYY-MM-DD.tar.gz in this folder; -: stdout)")
	work := fl.String("work", "", "the `folder` to put the export together in (default: the file's; for stdout, the wiki's .zone/)")
	fl.Usage = func() { fmt.Fprintln(stderr, exportUsage); printFlags(fl) }
	if err := fl.Parse(args); err != nil || fl.NArg() != 0 {
		if err == nil {
			fl.Usage()
		}
		return 2
	}
	fail := func(format string, a ...any) int {
		fmt.Fprintf(stderr, "zonewiki export: "+format+"\n", a...)
		return 1
	}
	c, err := loadWiki(getenv)
	if err != nil {
		return fail("%v", err)
	}
	cfg = c
	if !cfg.GitEnabled {
		return fail("git isn't installed (not on PATH), and an export is a copy of the wiki's git repository.")
	}
	if !isDir(filepath.Join(cfg.Data, ".git")) {
		return fail("%s keeps no history (it is no git repository), so there is nothing to export but the folder: "+
			"copy it, with Zonewiki stopped.", cfg.Data)
	}
	out, toStdout := *output, *output == "-"
	if out == "" {
		out = "zonewiki-" + time.Now().Format("2006-01-02") + ".tar.gz"
	}
	said := stdout // what the command says: not into the archive
	if toStdout {
		said = stderr
		if f, ok := stdout.(*os.File); ok {
			if st, err := f.Stat(); err == nil && st.Mode()&os.ModeCharDevice != 0 {
				return fail("--output - writes the archive to stdout, which is a terminal: send it to a file (> wiki.tar.gz).")
			}
		}
	} else {
		if out, err = filepath.Abs(out); err != nil {
			return fail("%v", err)
		}
		if _, err := os.Lstat(out); err == nil {
			return fail("%s is there already: an export doesn't write over a file.", out)
		}
	}
	parent := *work
	if parent == "" && toStdout {
		parent = stateDirIn(cfg.Data)
		if err := os.MkdirAll(parent, 0o777); err != nil {
			return fail("%v", err)
		}
	} else if parent == "" {
		parent = filepath.Dir(out)
	}
	tmp, err := os.MkdirTemp(parent, ".zonewiki-export-")
	if err != nil {
		return fail("%v", err)
	}
	defer os.RemoveAll(tmp)
	defer onInterrupt(func() { os.RemoveAll(tmp) })()

	// --no-local: as a client fetches it, not a copy of the repository's
	// files, which Zonewiki packs and prunes while it runs. The clone's files
	// are plain ones (a link in the history is a file that names its
	// target), as an import's are.
	clone := filepath.Join(tmp, wikiarchive.Top)
	if r := transferGit(tmp, nil, "clone", "--quiet", "--no-local", "-c", "core.symlinks=false", cfg.Data, clone); r.Code != 0 {
		return fail("the wiki's repository couldn't be copied: %s", strings.TrimSpace(r.Stderr))
	}
	// origin would name the folder the wiki was in.
	if r := transferGit(clone, nil, "remote", "remove", "origin"); r.Code != 0 {
		return fail("the copy of the repository couldn't be finished: %s", strings.TrimSpace(r.Stderr))
	}
	excludeState(clone)
	hasConfig := false
	if b, err := os.ReadFile(configPath()); err == nil {
		// Zonewiki writes the file whole, by a rename: never half of it.
		if err := settingsfile.Write(filepath.Join(clone, stateDirName, "config"), string(b)); err != nil {
			return fail("%v", err)
		}
		hasConfig = true
	} else if !errors.Is(err, fs.ErrNotExist) {
		return fail("%s: %v", configName(), err)
	}
	var size int64
	if toStdout {
		err = wikiarchive.Write(stdout, clone)
	} else {
		size, err = writeArchiveFile(out, clone)
	}
	if err != nil {
		return fail("%v", err)
	}
	if pending := notInGit(); len(pending) > 0 {
		fmt.Fprintf(stderr, "zonewiki export: %s that aren't committed yet, and those changes aren't in the export: %s. "+
			"Zonewiki commits a page's text at Done or at its next change: open and save what should come along, then export again.\n",
			counted(len(pending), "file holds changes", "files hold changes"), shownNames(pending))
	}
	what := "its pages, uploads, tasks and history"
	private := "every user's private pages"
	if hasConfig {
		what += ", and its settings file with the users"
		private += " and the hashes of their passwords"
	}
	where := "to stdout"
	if !toStdout {
		where = fmt.Sprintf("to %s (%s)", out, formatSize(size))
	}
	fmt.Fprintf(said, "Exported the wiki %s: %s.\nKeep the file as private as the wiki: it holds %s.\n", where, what, private)
	return 0
}

// writeArchiveFile writes the archive of dir as the new file at path, for
// its owner alone, and returns its size. The file is there whole or not at
// all, and one that appeared meanwhile isn't written over.
func writeArchiveFile(path, dir string) (int64, error) {
	tmp, err := os.CreateTemp(filepath.Dir(path), "."+filepath.Base(path)+".*") // mode 0600
	if err != nil {
		return 0, err
	}
	defer os.Remove(tmp.Name())
	if err = wikiarchive.Write(tmp, dir); err == nil {
		err = tmp.Sync()
	}
	st, serr := tmp.Stat()
	if cerr := tmp.Close(); err == nil {
		err = cerr
	}
	if err == nil {
		err = serr
	}
	if err != nil {
		return 0, err
	}
	// A link fails where the file is there by now; a filesystem without
	// links gets a rename.
	if err := os.Link(tmp.Name(), path); errors.Is(err, fs.ErrExist) {
		return 0, fmt.Errorf("%s is there already: an export doesn't write over a file.", path)
	} else if err != nil {
		if err := os.Rename(tmp.Name(), path); err != nil {
			return 0, err
		}
	}
	return st.Size(), nil
}

// notInGit: the wiki's files with changes git doesn't hold yet (an
// auto-save not committed, an edit on disk, a new file): a clone, and so
// an export, is without them. Read without taking git's locks, which a
// running Zonewiki's commits need.
func notInGit() []string {
	r := transferGit(cfg.Data, []string{"GIT_OPTIONAL_LOCKS=0"}, "status", "--porcelain", "--untracked-files=all")
	var names []string
	for _, line := range strings.Split(r.Stdout, "\n") {
		if len(line) > 3 {
			names = append(names, strings.TrimSpace(line[3:]))
		}
	}
	return names
}

func counted(n int, one, many string) string {
	if n == 1 {
		return "1 " + one
	}
	return strconv.Itoa(n) + " " + many
}

// shownNames: the first three of names, and how many more there are.
func shownNames(names []string) string {
	shown := strings.Join(names[:min(len(names), 3)], ", ")
	if len(names) > 3 {
		shown += fmt.Sprintf(" and %d more", len(names)-3)
	}
	return shown
}

func importMain(args []string, getenv func(string) string, stdin io.Reader, stdout, stderr io.Writer) int {
	if isHelp(firstOf(args)) {
		fmt.Fprintln(stdout, importUsage)
		return 0
	}
	fl := flag.NewFlagSet("zonewiki import", flag.ContinueOnError)
	fl.SetOutput(stderr)
	maxSize := fl.String("max-size", "", "the most the wiki may take on disk, its history and its files, as a `size` (1GB); more is refused")
	usersOnly := fl.Bool("users-only", false, "of the settings file, take the users, the groups, locale, date-format, timezone and noshare only (a host that sets the rest itself)")
	work := fl.String("work", "", "the `folder` to unpack the archive in (default: in ZONE_DATA)")
	fl.Usage = func() { fmt.Fprintln(stderr, importUsage); printFlags(fl) }
	if err := fl.Parse(args); err != nil || fl.NArg() != 1 {
		if err == nil {
			fl.Usage()
		}
		return 2
	}
	fail := func(format string, a ...any) int {
		fmt.Fprintf(stderr, "zonewiki import: "+format+"\n", a...)
		return 1
	}
	var max int64
	if *maxSize != "" {
		n, err := parseSize(*maxSize)
		if err != nil {
			return fail("--max-size %s isn't a size (500MB, 1GB).", *maxSize)
		}
		max = n
	}
	// A ZONE_DATA that isn't there is made: an import is how a wiki comes
	// to be here. Its folder must be there, as for any wiki.
	if d := getenv("ZONE_DATA"); d != "" {
		if _, err := os.Stat(d); errors.Is(err, fs.ErrNotExist) {
			if err := os.Mkdir(d, 0o777); err != nil {
				return fail("%v", err)
			}
		}
	}
	c, err := loadWiki(getenv)
	if err != nil {
		return fail("%v", err)
	}
	cfg = c
	if !cfg.GitEnabled {
		return fail("git isn't installed (not on PATH), and an import reads the wiki's history with it.")
	}
	in := stdin
	if file := fl.Arg(0); file != "-" {
		f, err := os.Open(file)
		if err != nil {
			return fail("%v", err)
		}
		defer f.Close()
		in = f
	}

	// Only into an empty folder; one an import didn't finish in is emptied:
	// all it holds is that import's.
	stage := filepath.Join(cfg.Data, importDirName)
	entries, err := os.ReadDir(cfg.Data)
	if err != nil {
		return fail("%v", err)
	}
	var others []string
	unfinished := false
	for _, e := range entries {
		if e.Name() == importDirName {
			unfinished = true
		} else {
			others = append(others, e.Name())
		}
	}
	if len(others) > 0 && !unfinished {
		return fail("%s isn't empty (it holds %s). An import makes a whole wiki and never adds to one: "+
			"empty the folder, or set ZONE_DATA to a new one.", cfg.Data, shownNames(others))
	}
	if unfinished {
		fmt.Fprintf(stderr, "zonewiki import: an import didn't finish in %s: starting over.\n", cfg.Data)
		for _, name := range append(others, importDirName) {
			if err := os.RemoveAll(filepath.Join(cfg.Data, name)); err != nil {
				return fail("%v", err)
			}
		}
	}
	if err := os.Mkdir(stage, 0o700); err != nil {
		return fail("%v", err)
	}
	// Until the wiki is moved into place, a failure leaves the folder empty
	// again; after that, the stage marks what is left as unfinished.
	moving := false
	giveUp := func() {
		if !moving {
			os.RemoveAll(stage)
		}
	}
	defer giveUp()
	unpacked := filepath.Join(stage, "archive")
	if *work != "" {
		if unpacked, err = os.MkdirTemp(*work, ".zonewiki-import-"); err != nil {
			return fail("%v", err)
		}
		defer os.RemoveAll(unpacked)
	} else if err := os.Mkdir(unpacked, 0o700); err != nil {
		return fail("%v", err)
	}
	defer onInterrupt(func() { giveUp(); os.RemoveAll(unpacked) })()

	tooBig := func() int {
		return fail("the wiki in the archive takes more than %s (--max-size).", formatSize(max))
	}
	found, err := wikiarchive.Read(in, unpacked, max)
	if errors.Is(err, wikiarchive.ErrTooBig) && max > 0 {
		return tooBig()
	} else if err != nil {
		return fail("%v", err)
	}
	src := filepath.Join(unpacked, wikiarchive.Top)
	branch, err := archiveBranch(filepath.Join(src, ".git", "HEAD"))
	if err != nil {
		return fail("%v", err)
	}
	var config string
	var users, left []string
	if found.Config {
		b, err := os.ReadFile(filepath.Join(src, stateDirName, "config"))
		if err != nil {
			return fail("%v", err)
		}
		if config, users, left, err = importedSettings(string(b), *usersOnly); err != nil {
			return fail("the archive's settings file: %v", err)
		}
	}

	// The history, fetched into a repository made here: what git runs and
	// reads is this one's config, never the archive's.
	wiki := filepath.Join(stage, wikiarchive.Top)
	if err := os.Mkdir(wiki, 0o777); err != nil {
		return fail("%v", err)
	}
	history := func(r gitResult) int {
		return fail("the archive's history can't be read: %s", strings.TrimSpace(r.Stderr))
	}
	for _, step := range [][]string{
		{"init", "--quiet"},
		// A link in the history is checked out as a file that names its
		// target: nothing in the wiki's folder points out of it.
		{"config", "core.symlinks", "false"},
		{"check-ref-format", branch},
		{"-c", "transfer.fsckObjects=true", "fetch", "--quiet", "--no-tags", "--update-head-ok", filepath.Join(src, ".git"),
			"+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*"},
		{"rev-parse", "--verify", "--quiet", branch + "^{commit}"},
		{"symbolic-ref", "HEAD", branch},
	} {
		if r := transferGit(wiki, nil, step...); r.Code != 0 {
			if step[0] == "rev-parse" {
				return fail("the archive's repository has no commit on its branch %s.", strings.TrimPrefix(branch, "refs/heads/"))
			}
			return history(r)
		}
	}
	if max > 0 {
		files, err := checkedOutSize(wiki)
		if err != nil {
			return fail("%v", err)
		}
		if found.Bytes+files > max {
			return tooBig()
		}
	}
	if r := transferGit(wiki, nil, "reset", "--hard", "--quiet"); r.Code != 0 {
		return history(r)
	}
	if *work == "" {
		_ = os.RemoveAll(unpacked) // its room, before the wiki's own files take theirs
	}

	format, err := readFormatFile(filepath.Join(wiki, formatFileName))
	if err != nil {
		return fail("the archive's %s can't be read (%v).", formatFileName, err)
	}
	if format > wikiFormat {
		return fail("the wiki in the archive is in format %d (%s), which a newer Zonewiki wrote; this one (%s) knows format %d "+
			"and would misread it. Import it with the newer Zonewiki.", format, formatFileName, version, wikiFormat)
	}
	if found.Config {
		if err := settingsfile.Write(filepath.Join(wiki, stateDirName, "config"), config); err != nil {
			return fail("%v", err)
		}
	}
	excludeState(wiki)
	noDeltaForUploads(wiki)
	note := fmt.Sprintf("Imported by Zonewiki %s on %s.\n", version, time.Now().UTC().Format(time.RFC3339))
	if err := os.MkdirAll(filepath.Join(wiki, stateDirName), 0o777); err != nil {
		return fail("%v", err)
	}
	if err := os.WriteFile(filepath.Join(wiki, stateDirName, importedName), []byte(note), 0o666); err != nil {
		return fail("%v", err)
	}
	pages := countPages(wiki)

	// Into place: the repository last, and the stage after it, so a folder
	// without the stage holds the whole wiki.
	moving = true
	moved, err := os.ReadDir(wiki)
	if err != nil {
		return fail("%v", err)
	}
	names := []string{}
	for _, e := range moved {
		if e.Name() != ".git" {
			names = append(names, e.Name())
		}
	}
	for _, name := range append(names, ".git") {
		if err := os.Rename(filepath.Join(wiki, name), filepath.Join(cfg.Data, name)); err != nil {
			return fail("%v. Run the import again.", err)
		}
	}
	if err := os.RemoveAll(stage); err != nil {
		return fail("%v. Run the import again.", err)
	}

	said := fmt.Sprintf("Imported the wiki into %s: %s", cfg.Data, counted(pages, "page", "pages"))
	if format > 0 {
		said += fmt.Sprintf(", in format %d", format)
	}
	switch {
	case len(users) > 0:
		said += fmt.Sprintf(", with %s (%s).", counted(len(users), "user", "users"), shownNames(users))
	case found.Config:
		said += ", with its settings and no users."
	default:
		said += ". The archive held no settings file, so no users."
	}
	fmt.Fprintln(stdout, said)
	if len(left) > 0 {
		fmt.Fprintf(stdout, "Not taken from its settings file, as this host has its own: %s (zonewiki config set KEY VALUE).\n",
			strings.Join(left, ", "))
	}
	if len(users) > 0 {
		fmt.Fprintln(stdout, "Everyone signs in again, with the password they had. An assistant that was connected by "+
			"signing in connects again; an MCP token works as before, at this Zonewiki's address.")
	}
	fmt.Fprintln(stdout, "Start Zonewiki on it now.")
	return 0
}

// headRE: what .git/HEAD says when a branch is checked out.
var headRE = regexp.MustCompile(`^ref: (refs/heads/[^\s]+)\s*$`)

// archiveBranch: the branch the archive's repository had checked out, as a
// ref: the one whose files become the wiki's.
func archiveBranch(head string) (string, error) {
	b, err := os.ReadFile(head)
	if err != nil {
		return "", err
	}
	m := headRE.FindStringSubmatch(string(b))
	if m == nil || len(b) > 1024 {
		return "", errors.New("the archive's repository has no branch checked out (its HEAD names none)")
	}
	return m[1], nil
}

// checkedOutSize: how many bytes the files of the commit at HEAD take once
// they are checked out, asked before they are: a history compresses, so a
// small one can hold files far bigger than itself.
func checkedOutSize(wiki string) (int64, error) {
	r := transferGit(wiki, nil, "ls-tree", "-r", "-l", "HEAD")
	if r.Code != 0 {
		return 0, fmt.Errorf("the archive's history can't be read: %s", strings.TrimSpace(r.Stderr))
	}
	var total int64
	for _, line := range strings.Split(r.Stdout, "\n") {
		// "<mode> blob <sha> <size>\t<path>"; a submodule's line has "-".
		meta, _, _ := strings.Cut(line, "\t")
		if f := strings.Fields(meta); len(f) == 4 {
			if n, err := strconv.ParseInt(f[3], 10, 64); err == nil {
				total += n
			}
		}
	}
	return total, nil
}

// importedSettings: the settings file an import writes, from the archive's
// text: without the settings that are a host's (hostKeys), and with
// usersOnly without any but the users, the groups and usersOnlyKeys. Also
// the users' names and the keys left out. An error for a file Zonewiki
// wouldn't start with, as it stands after that.
func importedSettings(text string, usersOnly bool) (out string, users, left []string, err error) {
	f, err := parseConfig(text)
	if err != nil {
		return "", nil, nil, err
	}
	for _, key := range configKeys {
		if _, set := f.Top[key]; !set {
			continue
		}
		if slices.Contains(hostKeys, key) || usersOnly && !slices.Contains(usersOnlyKeys, key) {
			f.Unset("", key)
			left = append(left, key)
		}
	}
	if usersOnly {
		// A user's address counts as not confirmed on the host a wiki moves
		// to, which answers for the mail it sends: its owner confirms it
		// there (email.go).
		for _, u := range f.Users {
			if email := u.Values["email"].Value; email != "" {
				f.Unset(u.Name, "email")
				f.Set(u.Name, "email-pending", email)
			}
		}
	}
	out = f.Text()
	if f, err = parseConfig(out); err == nil {
		_, err = loadSettings(f)
	}
	if err != nil {
		return "", nil, nil, err
	}
	for _, u := range f.Users {
		users = append(users, u.Name)
	}
	return out, users, left, nil
}

// countPages: the pages in the wiki at dir, for what an import says: its
// .md files, outside the folders Zonewiki keeps for itself (_assets/, _tasks/,
// hidden ones).
func countPages(dir string) int {
	n := 0
	_ = filepath.WalkDir(dir, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		name := d.Name()
		if d.IsDir() && path != dir && (strings.HasPrefix(name, "_") || strings.HasPrefix(name, ".")) {
			return filepath.SkipDir
		}
		if !d.IsDir() && strings.HasSuffix(name, ".md") {
			n++
		}
		return nil
	})
	return n
}

// unfinishedImport: why Zonewiki doesn't start in a folder an import didn't
// finish in: it holds part of a wiki.
func unfinishedImport() error {
	if _, err := os.Lstat(filepath.Join(cfg.Data, importDirName)); err != nil {
		return nil
	}
	return fmt.Errorf("an import didn't finish in %s (it holds %s/): the wiki there isn't whole. "+
		"Run zonewiki import again, which starts over", cfg.Data, importDirName)
}
