//go:build unix

package main

import (
	"os"
	"syscall"
)

// ownFolder: path (a folder or a file) is whoever runs Zone's or root's,
// and nobody else may write to it (its group may: a user's own group, with
// a umask of 002). What it links to is what counts.
func ownFolder(path string) bool {
	st, err := os.Stat(path)
	if err != nil {
		return false
	}
	sys, ok := st.Sys().(*syscall.Stat_t)
	if !ok {
		return false
	}
	return (sys.Uid == 0 || int(sys.Uid) == os.Geteuid()) && st.Mode().Perm()&0o002 == 0
}
