//go:build !unix

package main

// lockFile: no lock where there is no flock; the settings commands then
// rely on their check that the file didn't change meanwhile alone.
func lockFile(path string) (unlock func(), err error) { return func() {}, nil }
