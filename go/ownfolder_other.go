//go:build !unix

package main

// ownFolder: elsewhere a file's owner and mode say nothing Zone reads.
func ownFolder(path string) bool { return true }
