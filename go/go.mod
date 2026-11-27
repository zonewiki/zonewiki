module zonewiki

go 1.27.3

require (
	github.com/yuin/goldmark v1.8.6
	golang.org/x/text v0.43.0
)

// Zonewiki's own files (zonefiles.go at the repository's root: the browser
// code, the docs, the licenses), built into the binary. A module of its
// own, since go:embed reaches only below its package's folder.
require zonefiles v0.0.0

replace zonefiles => ../
