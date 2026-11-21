// Zone's own files: the browser code (public/) and the docs Help and
// zone_help show (README.md, docs/), from a folder on disk or the copies
// built into the binary (package zonefiles, at the repository's root). A
// folder wins: a checkout serves its files as they are edited, and a
// binary on its own serves what it was built with.
package main

import (
	"fmt"
	"io"
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"strings"
	"sync"
	"time"
	"zonefiles"
)

// builtInPublic: the browser code built into the binary.
var builtInPublic = func() fs.FS {
	sub, err := fs.Sub(zonefiles.FS, "public")
	if err != nil {
		panic(err) // only for a malformed name
	}
	return sub
}()

// publicFiles: the browser code, from the folder publicDir (--public, or
// public/ found from the binary: findPublic), else the binary's copy.
func publicFiles() fs.FS {
	if publicDir != "" {
		return os.DirFS(publicDir)
	}
	return builtInPublic
}

// findPublic finds public/ from the binary: next to it, or in the checkout
// it was built in (go/zonewiki → public/); "" when there is none, for the
// copy built into it. A folder counts when it holds Zone's entry module
// (zonewiki.js), so a downloaded binary doesn't take some other folder for
// it (macOS's ~/Public is ../public to one in ~/Downloads).
func findPublic() string {
	exe, err := os.Executable()
	if err != nil {
		return ""
	}
	return publicNear(filepath.Dir(exe))
}

// publicNear: Zone's public/ in dir or beside it (findPublic), or "". Only
// a folder that is whoever runs Zone's, or root's, and that others can't
// write to (ownFolder): the browser runs what is in it as Zone's own code,
// so a binary started in a shared place (/tmp) doesn't take a public/
// someone else put beside it. --public names any folder.
func publicNear(dir string) string {
	for _, p := range []string{filepath.Join(dir, "public"), filepath.Join(dir, "..", "public")} {
		entry := filepath.Join(p, "zonewiki.js")
		if !exists(entry) {
			continue
		}
		if !ownFolder(p) || !ownFolder(entry) {
			logf("not using the browser code in %s (it is another user's, or others can write to it): "+
				"the binary's own copy is served. --public %s uses it all the same.", p, p)
			continue
		}
		return p
	}
	return ""
}

// docsRoot: Zone's docs, README.md and docs/ at their paths in the
// repository: from the folder --docs names (nil when they aren't there:
// Help says it isn't installed), else from beside publicDir's folder (a
// checkout), else the binary's copy.
func docsRoot() fs.FS {
	if docsDir != "" {
		if isDir(filepath.Join(docsDir, "docs", "spec")) {
			return os.DirFS(docsDir)
		}
		return nil
	}
	if publicDir != "" {
		if root := filepath.Dir(publicDir); isDir(filepath.Join(root, "docs", "spec")) {
			return os.DirFS(root)
		}
	}
	return zonefiles.FS
}

// readDoc: a file of the docs (a path in docsRoot), its line ends as "\n".
func readDoc(name string) (string, error) {
	root := docsRoot()
	if root == nil {
		return "", fs.ErrNotExist
	}
	b, err := fs.ReadFile(root, name)
	return newlines(string(b)), err
}

// fsExists: name is in files.
func fsExists(files fs.FS, name string) bool {
	_, err := fs.Stat(files, name)
	return err == nil
}

// modTime: a file's time; the binary's for the files built into it, which
// have none of their own, so a browser keeps them until Zone is updated.
func modTime(st fs.FileInfo) time.Time {
	if t := st.ModTime(); !t.IsZero() {
		return t
	}
	return builtTime()
}

// builtTime: when the binary's file was written (the time Zone started if
// that can't be told).
var builtTime = sync.OnceValue(func() time.Time {
	if exe, err := os.Executable(); err == nil {
		if st, err := os.Stat(exe); err == nil {
			return st.ModTime()
		}
	}
	return time.Now()
})

const licensesUsage = `Usage: zonewiki licenses
  print the licenses of what this binary holds: Zone's own (LICENSE, the AGPL),
  Go's, the Go modules' and the model's (THIRD_PARTY_LICENSES), and those of the
  libraries in its browser code (public/vendor/)`

// licensesMain: zonewiki licenses: the license files built into the binary,
// each under its path, so a binary passed on alone still carries them.
func licensesMain(args []string, stdout, stderr io.Writer) int {
	if len(args) > 0 {
		if isHelp(args[0]) {
			fmt.Fprintln(stdout, licensesUsage)
			return 0
		}
		fmt.Fprintln(stderr, licensesUsage)
		return 2
	}
	for i, name := range licenseFiles() {
		text, err := fs.ReadFile(zonefiles.FS, name)
		if err != nil {
			fmt.Fprintln(stderr, "zonewiki licenses:", err)
			return 1
		}
		if i > 0 {
			fmt.Fprintln(stdout)
		}
		fmt.Fprintf(stdout, "==> %s <==\n\n%s", name, text)
	}
	return 0
}

// licenseFiles: the license files built into the binary: Zone's, the
// notices of what is compiled in, then each vendored library's (LICENSE,
// THIRD_PARTY_LICENSES, COPYING…).
func licenseFiles() []string {
	names := []string{"LICENSE", "THIRD_PARTY_LICENSES"}
	vendored, _ := fs.Glob(zonefiles.FS, "public/vendor/*/*")
	for _, name := range vendored {
		if base := path.Base(name); base == "LICENSE" || base == "THIRD_PARTY_LICENSES" || strings.HasPrefix(base, "COPYING") {
			names = append(names, name)
		}
	}
	return names
}
