package wikiarchive

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// entry: one entry of a hand-made archive.
type entry struct {
	name, body string
	kind       byte // 0: a file
	link       string
}

func archive(t *testing.T, entries ...entry) []byte {
	t.Helper()
	var buf bytes.Buffer
	zw := gzip.NewWriter(&buf)
	tw := tar.NewWriter(zw)
	for _, e := range entries {
		h := &tar.Header{Name: e.name, Mode: 0o644, Size: int64(len(e.body)), Typeflag: tar.TypeReg, Linkname: e.link}
		if e.kind != 0 {
			h.Typeflag, h.Size = e.kind, 0
		}
		if err := tw.WriteHeader(h); err != nil {
			t.Fatal(err)
		}
		if h.Typeflag == tar.TypeReg {
			if _, err := tw.Write([]byte(e.body)); err != nil {
				t.Fatal(err)
			}
		}
	}
	if err := tw.Close(); err != nil {
		t.Fatal(err)
	}
	if err := zw.Close(); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}

// repo: the least an archive must hold to be one of a wiki.
func repo() []entry {
	return []entry{
		{name: "wiki/", kind: tar.TypeDir},
		{name: "wiki/.git/", kind: tar.TypeDir},
		{name: "wiki/.git/HEAD", body: "ref: refs/heads/main\n"},
		{name: "wiki/.git/objects/", kind: tar.TypeDir},
		{name: "wiki/.git/objects/ab/cdef", body: "object"},
		{name: "wiki/.git/refs/heads/main", body: "abcdef\n"},
	}
}

func files(t *testing.T, dir string) map[string]string {
	t.Helper()
	got := map[string]string{}
	err := filepath.WalkDir(dir, func(path string, d os.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return err
		}
		b, err := os.ReadFile(path)
		rel, _ := filepath.Rel(dir, path)
		got[filepath.ToSlash(rel)] = string(b)
		return err
	})
	if err != nil {
		t.Fatal(err)
	}
	return got
}

func TestWriteThenRead(t *testing.T) {
	src := t.TempDir()
	for name, body := range map[string]string{
		".git/HEAD":                    "ref: refs/heads/main\n",
		".git/packed-refs":             "# pack-refs\n",
		".git/objects/ab/cdef":         "object",
		".git/objects/info/alternates": "/somewhere/else\n",
		".git/refs/heads/main":         "abcdef\n",
		".git/refs/tags/v1":            "abcdef\n",
		".git/config":                  "[core]\n\tfsmonitor = /bin/evil\n",
		".git/hooks/post-checkout":     "#!/bin/sh\ntouch /tmp/ran\n",
		".git/info/exclude":            "/.zone/*\n",
		".git/index":                   "index",
		".zone/config":                 "locale = de-AT\n",
		".zone/secret_key":             "never exported, never read",
		"index.md":                     "# Home\n",
		"garden/roses.md":              "# Roses\n",
	} {
		path := filepath.Join(src, filepath.FromSlash(name))
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	var buf bytes.Buffer
	if err := Write(&buf, src); err != nil {
		t.Fatal(err)
	}
	dest := t.TempDir()
	found, err := Read(bytes.NewReader(buf.Bytes()), dest, 0)
	if err != nil {
		t.Fatal(err)
	}
	// The history and the settings file, and nothing that says what git
	// runs or where else it reads.
	want := map[string]string{
		"wiki/.git/HEAD":            "ref: refs/heads/main\n",
		"wiki/.git/packed-refs":     "# pack-refs\n",
		"wiki/.git/objects/ab/cdef": "object",
		"wiki/.git/refs/heads/main": "abcdef\n",
		"wiki/.git/refs/tags/v1":    "abcdef\n",
		"wiki/.zone/config":         "locale = de-AT\n",
	}
	got := files(t, dest)
	if len(got) != len(want) {
		t.Errorf("read %v, want %v", got, want)
	}
	for name, body := range want {
		if got[name] != body {
			t.Errorf("%s = %q, want %q", name, got[name], body)
		}
	}
	if !found.Config || found.Bytes == 0 {
		t.Errorf("found = %+v", found)
	}
	if st, err := os.Stat(filepath.Join(dest, "wiki", ".zone", "config")); err != nil || st.Mode().Perm()&0o077 != 0 {
		t.Errorf("the settings file is readable by others: %v %v", st.Mode(), err)
	}
}

func TestWriteRefusesWhatAReaderWould(t *testing.T) {
	src := t.TempDir()
	if err := os.Symlink("/etc/passwd", filepath.Join(src, "link.md")); err != nil {
		t.Skip(err)
	}
	if err := Write(&bytes.Buffer{}, src); err == nil || !strings.Contains(err.Error(), "neither a file nor a folder") {
		t.Errorf("a link was written: %v", err)
	}
}

func TestReadRefuses(t *testing.T) {
	with := func(more ...entry) []entry { return append(repo(), more...) }
	for name, c := range map[string]struct {
		entries []entry
		want    string
	}{
		"a name that leads out":      {with(entry{name: "wiki/../../etc/cron.d/x", body: "x"}), "the name"},
		"a name from the root":       {with(entry{name: "/etc/passwd", body: "x"}), "the name"},
		"a backslash":                {with(entry{name: `wiki\..\x`, body: "x"}), "the name"},
		"a control character":        {with(entry{name: "wiki/a\x07b", body: "x"}), "the name"},
		"an empty part":              {with(entry{name: "wiki//.git/HEAD", body: "x"}), "the name"},
		"something beside wiki/":     {with(entry{name: "other/file", body: "x"}), "beside wiki/"},
		"HEAD as a link":             {[]entry{{name: "wiki/.git/objects/", kind: tar.TypeDir}, {name: "wiki/.git/HEAD", kind: tar.TypeSymlink, link: "/etc/passwd"}}, "a link or a device"},
		"an object as a hard link":   {with(entry{name: "wiki/.git/objects/cd/ef", kind: tar.TypeLink, link: "wiki/.git/HEAD"}), "a link or a device"},
		"a ref as a device":          {with(entry{name: "wiki/.git/refs/heads/dev", kind: tar.TypeChar}), "a link or a device"},
		"the settings as a link":     {with(entry{name: "wiki/.zone/config", kind: tar.TypeSymlink, link: "/etc/shadow"}), "a link or a device"},
		"the settings as a folder":   {with(entry{name: "wiki/.zone/config/", kind: tar.TypeDir}), "is a folder"},
		"a file twice":               {with(entry{name: "wiki/.git/HEAD", body: "ref: refs/heads/other\n"}), "twice"},
		"no repository":              {[]entry{{name: "wiki/index.md", body: "# Home\n"}}, "no git repository"},
		"a repository without HEAD":  {[]entry{{name: "wiki/.git/objects/ab/cd", body: "x"}}, "no git repository"},
		"settings past their size":   {with(entry{name: "wiki/.zone/config", body: strings.Repeat("x", ConfigMax+1)}), ErrTooBig.Error()},
		"a path through a file":      {with(entry{name: "wiki/.git/objects/ab/cdef/x", body: "x"}), ""},
		"a second top folder's file": {with(entry{name: "wikis", body: "x"}), "beside wiki/"},
	} {
		dir := t.TempDir()
		_, err := Read(bytes.NewReader(archive(t, c.entries...)), dir, 0)
		if err == nil || !strings.Contains(err.Error(), c.want) {
			t.Errorf("%s: err = %v, want one with %q", name, err, c.want)
		}
		// Nothing was written outside the folder's wiki/.
		for path := range files(t, dir) {
			if !strings.HasPrefix(path, "wiki/") {
				t.Errorf("%s: wrote %s", name, path)
			}
		}
	}
}

func TestReadLeavesTheRestInTheArchive(t *testing.T) {
	// What isn't the history or the settings file is passed by, whatever it
	// is: an older export's work tree may hold a link.
	dir := t.TempDir()
	entries := append(repo(),
		entry{name: "wiki/link.md", kind: tar.TypeSymlink, link: "/etc/passwd"},
		entry{name: "wiki/.git/hooks/post-checkout", body: "#!/bin/sh\n"},
		entry{name: "wiki/.git/config", body: "[core]\n"},
		entry{name: "wiki/.git/objects/info/alternates", body: "/elsewhere\n"},
		entry{name: "wiki/.git/objects/info/http-alternates", body: "http://elsewhere\n"},
		entry{name: "wiki/.zone/secret_key", body: "key"},
		entry{name: "wiki/.zone/config.d/x", body: "x"},
	)
	if _, err := Read(bytes.NewReader(archive(t, entries...)), dir, 0); err != nil {
		t.Fatal(err)
	}
	got := files(t, dir)
	if len(got) != 3 {
		t.Errorf("read %v", got)
	}
	if _, err := os.Lstat(filepath.Join(dir, "wiki", "link.md")); err == nil {
		t.Error("the work tree's link was written")
	}
}

func TestReadTakesTarsOwnSpelling(t *testing.T) {
	// tar -C folder -czf out . names its entries ./wiki/…
	var entries []entry
	for _, e := range repo() {
		e.name = "./" + e.name
		entries = append(entries, e)
	}
	entries = append([]entry{{name: "./", kind: tar.TypeDir}}, entries...)
	dir := t.TempDir()
	if _, err := Read(bytes.NewReader(archive(t, entries...)), dir, 0); err != nil {
		t.Fatal(err)
	}
	if got := files(t, dir); got["wiki/.git/HEAD"] == "" {
		t.Errorf("read %v", got)
	}
}

func TestReadStopsAtTheSize(t *testing.T) {
	big := append(repo(), entry{name: "wiki/.git/objects/pack/p.pack", body: strings.Repeat("x", 4096)})
	if _, err := Read(bytes.NewReader(archive(t, big...)), t.TempDir(), 1024); !errors.Is(err, ErrTooBig) {
		t.Errorf("err = %v, want ErrTooBig", err)
	}
	found, err := Read(bytes.NewReader(archive(t, big...)), t.TempDir(), 1<<20)
	if err != nil || found.Bytes < 4096 {
		t.Errorf("found = %+v, err = %v", found, err)
	}
	// The work tree isn't written, so it doesn't count.
	work := append(repo(), entry{name: "wiki/big.md", body: strings.Repeat("x", 4096)})
	if _, err := Read(bytes.NewReader(archive(t, work...)), t.TempDir(), 1024); err != nil {
		t.Errorf("err = %v", err)
	}
}

func TestReadSaysWhatIsNoArchive(t *testing.T) {
	if _, err := Read(strings.NewReader("# Home\n"), t.TempDir(), 0); err == nil || !strings.Contains(err.Error(), "gzipped tar") {
		t.Errorf("err = %v", err)
	}
	whole := archive(t, repo()...)
	if _, err := Read(bytes.NewReader(whole[:len(whole)/2]), t.TempDir(), 0); err == nil {
		t.Error("half an archive was read")
	}
}
