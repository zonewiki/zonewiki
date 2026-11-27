// Package mdimport turns a folder of markdown notes (an Obsidian vault for
// one) into pages and files for a wiki: what each page's text becomes and
// where each file goes, and a report of what changed on the way. It reads
// the folder and writes nothing, and knows nothing of users: Convert makes
// a Plan, and the server's importOp writes it (the import in
// docs/spec/editor.md).
package mdimport

// Options: what Convert needs to know of the wiki it plans for. The
// callbacks are the server's own rules, so a page's and a file's name is
// decided in one place.
type Options struct {
	// Folder: the slug of the folder the pages go into, which isn't there
	// yet ("my-vault", "private/alice/my-vault", "groups/dev/my-vault").
	Folder string
	// PagePart: whether a slug's part is one a page's path may have (the
	// server's pagePart); a part it refuses gets a "-" suffix.
	PagePart func(part string) bool
	// FileName: the name a file the notes link to gets in the place's
	// uploads, from the name it has (want, its base name in the vault):
	// one an upload can have, free in the place's _assets/ and not given
	// to another file of this import (freeUploadName, uploadNamed). ""
	// when there is none: the file is left out and reported.
	FileName func(want string) string
	// FileURL: the URL a link to an upload of that name takes in a page
	// (uploadURL: percent-encoded, the place's _assets/).
	FileURL func(name string) string
}

// Plan: what an import writes. Every page is new; Index is the folder's
// index page, which links every imported page and keeps the report.
type Plan struct {
	Pages  []Page
	Index  Page
	Files  []File
	Report Report
}

// Page: a page to write, by its full slug (under Options.Folder), and the
// text it is to hold.
type Page struct {
	Slug, Text string
}

// File: a file to copy into the place's uploads: Src its path in the
// folder converted (slash-separated, as fs.FS names it), Name its name in
// _assets/ (Options.FileName).
type File struct {
	Src, Name string
}

// Report: what changed on the way, shown before anything is written and
// kept on the folder's index page. Each list holds lines a person reads
// ("My Note (2024).md → my-vault/my-note-2024"), in the order of the
// folder's paths.
type Report struct {
	Obsidian  bool     // the folder is an Obsidian vault (it has .obsidian/): its own syntax was converted
	Renamed   []string // a note whose slug isn't its name (an empty part, a clash, a part refused)
	Ambiguous []string // a link with several targets, and the one chosen
	Missing   []string // a link to no note or file, left as written
	Lost      []string // what has no equivalent: block references, embedded notes, Dataview and Tasks-plugin syntax
	LeftOut   []string // files no note links to, files with no name an upload can have, .canvas, .base, Excalidraw
}
