package wikiarchive

import (
	"archive/tar"
	"archive/zip"
	"bytes"
	"errors"
	"hash/crc32"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// zipEntry: one entry of a hand-made zip.
type zipEntry struct {
	name, body string
	mode       fs.FileMode // 0: a file (a name ending in / is a folder)
	says       int         // the size its header says, when it lies (0: the truth)
	unix       uint32      // the Unix mode its header says, raw (0: mode's)
}

func zipArchive(t *testing.T, entries ...zipEntry) []byte {
	t.Helper()
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	for _, e := range entries {
		h := &zip.FileHeader{Name: e.name, Method: zip.Store}
		if e.mode != 0 {
			h.SetMode(e.mode)
		}
		if e.unix != 0 {
			h.CreatorVersion, h.ExternalAttrs = 3<<8, e.unix<<16 // made on Unix, with this mode
		}
		if e.says != 0 {
			// Written raw, so the header says what it is told.
			h.CRC32 = crc32.ChecksumIEEE([]byte(e.body))
			h.CompressedSize64, h.UncompressedSize64 = uint64(len(e.body)), uint64(e.says)
			w, err := zw.CreateRaw(h)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := io.WriteString(w, e.body); err != nil {
				t.Fatal(err)
			}
			continue
		}
		w, err := zw.CreateHeader(h)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := io.WriteString(w, e.body); err != nil {
			t.Fatal(err)
		}
	}
	if err := zw.Close(); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}

// hostile: an archive a source refuses, as one of tar's entries and one of
// zip's; one of the two left out is a trap the other format hasn't.
type hostile struct {
	tar  []entry
	zip  []zipEntry
	want string // in the refusal; "" for any
}

func file(name string) (entry, zipEntry) {
	return entry{name: name, body: "x"}, zipEntry{name: name, body: "x"}
}

func badName(name string) hostile {
	te, ze := file(name)
	return hostile{tar: []entry{te}, zip: []zipEntry{ze}, want: "the name"}
}

func hostiles() map[string]hostile {
	many := hostile{want: "more than 10 entries"}
	for i := range 11 {
		te, ze := file(string(rune('a' + i)))
		many.tar, many.zip = append(many.tar, te), append(many.zip, ze)
	}
	big := strings.Repeat("x", 4096)
	return map[string]hostile{
		"a name that leads out":       badName("../x"),
		"a name that leads out later": badName("notes/../../x"),
		"a name from the root":        badName("/etc/passwd"),
		"a backslash":                 badName(`notes\..\x`),
		"a control character":         badName("a\x07b"),
		"a delete character":          badName("a\x7fb"),
		"an empty part":               badName("notes//x"),
		"a dot":                       badName("notes/./x"),
		"a symlink": {
			tar:  []entry{{name: "link", kind: tar.TypeSymlink, link: "/etc/passwd"}},
			zip:  []zipEntry{{name: "link", body: "/etc/passwd", mode: fs.ModeSymlink | 0o777}},
			want: "a link or a device"},
		"a symlink named as a folder": {
			zip:  []zipEntry{{name: "link/", unix: 0o120777}}, // S_IFLNK
			want: "a link or a device"},
		"a hard link": {
			tar:  []entry{{name: "a", body: "x"}, {name: "b", kind: tar.TypeLink, link: "a"}},
			want: "a link or a device"},
		"a device": {
			tar:  []entry{{name: "dev", kind: tar.TypeChar}},
			zip:  []zipEntry{{name: "dev", mode: fs.ModeDevice | fs.ModeCharDevice | 0o666}},
			want: "a link or a device"},
		"a pipe": {
			tar:  []entry{{name: "fifo", kind: tar.TypeFifo}},
			zip:  []zipEntry{{name: "fifo", mode: fs.ModeNamedPipe | 0o666}},
			want: "a link or a device"},
		"a file twice": {
			tar:  []entry{{name: "a", body: "x"}, {name: "a", body: "y"}},
			zip:  []zipEntry{{name: "a", body: "x"}, {name: "a", body: "y"}},
			want: "twice"},
		"a file, then a folder of its name": {
			tar:  []entry{{name: "a", body: "x"}, {name: "a/", kind: tar.TypeDir}},
			zip:  []zipEntry{{name: "a", body: "x"}, {name: "a/"}},
			want: "both a file and a folder"},
		"a folder, then a file of its name": {
			tar:  []entry{{name: "a/", kind: tar.TypeDir}, {name: "a", body: "x"}},
			zip:  []zipEntry{{name: "a/"}, {name: "a", body: "x"}},
			want: "both a file and a folder"},
		"a path through a file": {
			tar:  []entry{{name: "a", body: "x"}, {name: "a/b", body: "x"}},
			zip:  []zipEntry{{name: "a", body: "x"}, {name: "a/b", body: "x"}},
			want: "both a file and a folder"},
		"too many entries": many,
		"past the budget": {
			tar:  []entry{{name: "a", body: big}},
			zip:  []zipEntry{{name: "a", body: big}},
			want: ErrTooBig.Error()},
		"past the budget in two files": {
			tar:  []entry{{name: "a", body: big[:600]}, {name: "b", body: big[:600]}},
			zip:  []zipEntry{{name: "a", body: big[:600]}, {name: "b", body: big[:600]}},
			want: ErrTooBig.Error()},
		"a size its header understates": {
			zip:  []zipEntry{{name: "a", body: big, says: 10}},
			want: "can't be unpacked"},
		"a name that isn't UTF-8": {
			zip:  []zipEntry{{name: "caf\xe9.md", body: "x"}},
			want: "isn't UTF-8"},
	}
}

func everything(name []string, kind Kind) (Take, error) { return Take{Keep: true}, nil }

func TestUnpackRefuses(t *testing.T) {
	for name, h := range hostiles() {
		for _, source := range []string{"tar", "zip"} {
			var src Source
			switch {
			case source == "tar" && h.tar != nil:
				src = Tar(bytes.NewReader(archive(t, h.tar...)))
			case source == "zip" && h.zip != nil:
				b := zipArchive(t, h.zip...)
				src = Zip(bytes.NewReader(b), int64(len(b)))
			default:
				continue
			}
			parent := t.TempDir()
			dir := filepath.Join(parent, "in")
			n, err := Unpack(src, dir, Rules{Not: "not an archive of notes", Keep: everything, MaxBytes: 1024, MaxEntries: 10})
			if err == nil || !strings.Contains(err.Error(), h.want) {
				t.Errorf("%s, %s: err = %v, want one with %q", name, source, err, h.want)
				continue
			}
			if !errors.Is(err, ErrTooBig) && !strings.Contains(err.Error(), "can't be unpacked") && !strings.HasPrefix(err.Error(), "not an archive of notes: ") {
				t.Errorf("%s, %s: the refusal %q isn't in the caller's words", name, source, err)
			}
			if n > 1025 {
				t.Errorf("%s, %s: wrote %d bytes past the budget", name, source, n)
			}
			// Nothing was written outside dir, and no link anywhere.
			filepath.WalkDir(parent, func(path string, d fs.DirEntry, err error) error {
				if err == nil && path != parent && path != dir && !strings.HasPrefix(path, dir+string(filepath.Separator)) {
					t.Errorf("%s, %s: wrote %s", name, source, path)
				}
				if err == nil && d.Type()&^fs.ModeDir != 0 {
					t.Errorf("%s, %s: wrote %s, a %v", name, source, path, d.Type())
				}
				return nil
			})
		}
	}
}

func TestUnpackZipKeepsWhatItIsTold(t *testing.T) {
	// A vault's zip: its notes and files are kept, its settings, trash and
	// repository passed by, a link among them too.
	b := zipArchive(t,
		zipEntry{name: "vault/"},
		zipEntry{name: "vault/Home.md", body: "# Home\n"},
		zipEntry{name: "vault/Garden/Roses.md", body: "# Roses\n"},
		zipEntry{name: "vault/Garden/rose.png", body: "png"},
		zipEntry{name: "vault/.obsidian/app.json", body: "{}"},
		zipEntry{name: "vault/.obsidian/plugins", body: "/etc", mode: fs.ModeSymlink | 0o777},
		zipEntry{name: "vault/.trash/Old.md", body: "old"},
		zipEntry{name: "vault/.git/config", body: "[core]\n"},
	)
	dir := t.TempDir()
	keep := func(name []string, kind Kind) (Take, error) {
		for _, p := range name {
			if p == ".obsidian" || p == ".trash" || p == ".git" {
				return Take{}, nil
			}
		}
		return Take{Keep: true}, nil
	}
	n, err := Unpack(Zip(bytes.NewReader(b), int64(len(b))), dir, Rules{Not: "not a zip of notes", Keep: keep, MaxBytes: 1 << 20, MaxEntries: 20})
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]string{
		"vault/Home.md":         "# Home\n",
		"vault/Garden/Roses.md": "# Roses\n",
		"vault/Garden/rose.png": "png",
	}
	got := files(t, dir)
	if len(got) != len(want) || n != int64(len("# Home\n# Roses\npng")) {
		t.Errorf("unpacked %v (%d bytes), want %v", got, n, want)
	}
	for name, body := range want {
		if got[name] != body {
			t.Errorf("%s = %q, want %q", name, got[name], body)
		}
	}
	if _, err := os.Lstat(filepath.Join(dir, "vault", ".obsidian")); err == nil {
		t.Error(".obsidian/ was written")
	}
}

func TestUnpackSaysWhatIsNoZip(t *testing.T) {
	b := []byte("# Home\n\nJust some notes.\n")
	_, err := Unpack(Zip(bytes.NewReader(b), int64(len(b))), t.TempDir(), Rules{Not: "not a zip of notes", Keep: everything})
	if err == nil || !strings.HasPrefix(err.Error(), "not a zip of notes (a zip): ") {
		t.Errorf("err = %v", err)
	}
}

func TestUnpackTarTakesWhatZipRefuses(t *testing.T) {
	// A tar's names are bytes, as git's are: one that isn't UTF-8 is no
	// refusal there, so zonewiki import reads what it read before.
	dir := t.TempDir()
	if _, err := Unpack(Tar(bytes.NewReader(archive(t, entry{name: "caf\xe9", body: "x"}))), dir, Rules{Not: "not a test", Keep: everything}); err != nil {
		t.Fatal(err)
	}
}

func TestReadKeepsTheSizeAfterTheSettingsFile(t *testing.T) {
	// The settings file has a limit of its own, but what it takes counts:
	// past the size with it, the next file of the history is too much, not
	// one of any size.
	entries := append(repo(),
		entry{name: "wiki/.zone/config", body: strings.Repeat("#", 2048)},
		entry{name: "wiki/.git/objects/pack/p.pack", body: strings.Repeat("x", 4096)},
	)
	if _, err := Read(bytes.NewReader(archive(t, entries...)), t.TempDir(), 1024); !errors.Is(err, ErrTooBig) {
		t.Errorf("err = %v, want ErrTooBig", err)
	}
}
