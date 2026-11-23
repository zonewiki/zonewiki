// Package zonefiles is Zonewiki's own files, built into the zonewiki binary so
// that the binary is all a machine needs to run Zonewiki: the browser code
// (public/), the docs Zonewiki shows as Help and serves to assistants
// (README.md, docs/manual/, docs/spec/ and the screenshots they show), and
// the licenses (LICENSE, THIRD_PARTY_LICENSES; the vendored libraries' are
// in public/vendor/).
//
// It is at the repository's root because go:embed reaches only files below
// its package's folder; the server (go/) takes it as a module of its own
// (go/go.mod: replace zonefiles => ../), so it builds with a plain go build.
// The server serves a folder on disk instead when it finds one (a
// checkout's public/ and docs/), so these copies are for a binary on its
// own.
package zonefiles

import "embed"

// FS holds the files at their paths in the repository: public/style.css,
// docs/manual/README.md, LICENSE …
//
//go:embed public README.md docs/manual/*.md docs/spec/*.md docs/screenshots/*.png LICENSE THIRD_PARTY_LICENSES
var FS embed.FS
