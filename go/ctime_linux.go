package main

import (
	"os"
	"syscall"
	"time"
)

// changeTime is a file's ctime (st_ctime on Linux, which has no
// st_birthtime): what the page list calls "created".
func changeTime(st os.FileInfo) time.Time {
	if s, ok := st.Sys().(*syscall.Stat_t); ok {
		return time.Unix(s.Ctim.Sec, s.Ctim.Nsec)
	}
	return st.ModTime()
}
