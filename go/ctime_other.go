//go:build !linux

package main

import (
	"os"
	"time"
)

// changeTime: elsewhere the modification time stands in for it.
func changeTime(st os.FileInfo) time.Time { return st.ModTime() }
