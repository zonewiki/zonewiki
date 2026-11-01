package mdtext

import (
	"reflect"
	"testing"
)

func TestToSlugCases(t *testing.T) {
	for in, want := range map[string]string{
		"hello world": "hello-world", "My Page": "my-page", "foo & bar": "foo-bar", "a  --  b": "a-b",
		"  my-page  ": "my-page", "journal/my entry": "journal/my-entry", "": "", "café": "caf",
	} {
		if got := ToSlug(in); got != want {
			t.Errorf("ToSlug(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestPageTitle(t *testing.T) {
	cases := []struct{ content, slug, want string }{
		{"# My Title\n\nBody.", "slug", "My Title"},
		{"\n# Title Here\n\nbody", "slug", "Title Here"},
		{"No heading here.", "my-page", "My Page"},
		{"# Title  \n\nbody", "slug", "Title"},
		{"## Section\n\nbody", "my-slug", "My Slug"},
		{"", "some-page", "Some Page"},
		// Code, frontmatter and lookalikes aren't headings.
		{"Intro\n\n```bash\n# install deps\n```\n", "my-page", "My Page"},
		{"```\n# nope\n```\n\n# Real Title\n", "x", "Real Title"},
		{"---\ntags: a\n---\n# Title\n", "x", "Title"},
		{"#hashtag\n", "tagged-page", "Tagged Page"},
		{"#\nnext line\n", "empty-h", "Empty H"},
	}
	for _, c := range cases {
		if got := PageTitle(c.content, c.slug); got != c.want {
			t.Errorf("PageTitle(%q, %q) = %q, want %q", c.content, c.slug, got, c.want)
		}
	}
}

func TestStripH1(t *testing.T) {
	for in, want := range map[string]string{
		"# Title\n\nBody text.\n":           "\nBody text.\n",
		"## Section\n\nBody.\n":             "## Section\n\nBody.\n",
		"# First\n\n# Second\n\nBody.\n":    "\n# Second\n\nBody.\n",
		"":                                  "",
		"Just text.\n":                      "Just text.\n",
		"---\ntags: a\n---\n\nBody only.\n": "---\ntags: a\n---\n\nBody only.\n",
		"---\ntags: a, b\n---\n\n# Title\n\nBody.\n": "---\ntags: a, b\n---\n\n\nBody.\n",
	} {
		if got := StripH1(in); got != want {
			t.Errorf("StripH1(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestParseFrontmatterCases(t *testing.T) {
	cases := []struct {
		content string
		want    Frontmatter
		body    string // "" for: don't check
	}{
		{"# Title\n\nBody.\n", Frontmatter{}, "# Title\n\nBody.\n"},
		{"---\ntags: work, urgent\ntldr: One line summary.\n---\n\n# Title\n\nBody.\n",
			Frontmatter{Tags: []string{"work", "urgent"}, Tldr: "One line summary."}, "# Title\n\nBody.\n"},
		{"---\ntags: [work, urgent]\n---\nBody.\n", Frontmatter{Tags: []string{"work", "urgent"}}, "Body.\n"},
		{"---\ntldr: \"Quoted summary\"\n---\nBody.\n", Frontmatter{Tldr: "Quoted summary"}, ""},
		{"---\ntags: a\n\n# Title\n\nBody.\n", Frontmatter{}, "---\ntags: a\n\n# Title\n\nBody.\n"},
		{"---\ncreated: 2026-01-01\ntags: a\n---\nBody.\n", Frontmatter{Tags: []string{"a"}}, ""},
		{"---\ntags:\ntldr:\n---\nBody.\n", Frontmatter{}, ""},
		{"---\nsources: https://a.example/x, [Doc, v2](https://b.example), [[people/alex|Alex, A.]]\n---\nBody.\n",
			Frontmatter{Sources: []string{"https://a.example/x", "[Doc, v2](https://b.example)", "[[people/alex|Alex, A.]]"}}, ""},
		{"---\ntags:\n  - work\n  - 'urgent'\nsources:\n- https://a.example/x?q=1:2\n- [[index]]\ntldr: Short.\n- stray item\n---\nBody.\n",
			Frontmatter{Tags: []string{"work", "urgent"}, Sources: []string{"https://a.example/x?q=1:2", "[[index]]"}, Tldr: "Short."}, ""},
		{"---\nsources: [[index]]\n---\nBody.\n", Frontmatter{Sources: []string{"[[index]]"}}, ""},
		{"---\ntldr: >-\n  One line\n  goes on.\n\n  Second: part.\ntags: a\n---\nBody.\n",
			Frontmatter{Tldr: "One line goes on.\nSecond: part.", Tags: []string{"a"}}, ""},
		{"---\ntldr: |\n  - first\n    more\n  - second\n---\nBody.\n", Frontmatter{Tldr: "- first\n  more\n- second"}, ""},
		{"---\ntldr: \"Starts here\n  and: ends here\"\ntags: a\n---\nBody.\n",
			Frontmatter{Tldr: "Starts here and: ends here", Tags: []string{"a"}}, ""},
		{"---\ntldr: >\n  Last\n---\nBody.\n", Frontmatter{Tldr: "Last"}, ""},
		{"---\ntldr: >\ntags: 'a'\n---\nBody.\n", Frontmatter{Tags: []string{"a"}}, ""},
		{"# Title\n\n---\ntags: a\n---\n", Frontmatter{}, "# Title\n\n---\ntags: a\n---\n"},
	}
	for _, c := range cases {
		meta, body := ParseFrontmatter(c.content)
		if !reflect.DeepEqual(meta, c.want) || c.body != "" && body != c.body {
			t.Errorf("ParseFrontmatter(%q) = %+v, %q; want %+v, %q", c.content, meta, body, c.want, c.body)
		}
	}
}

func TestSplitWikilink(t *testing.T) {
	for in, want := range map[string][3]string{
		"Garden":                       {"Garden", "", "Garden"},
		"Garden|the garden":            {"Garden", "", "the garden"},
		"Garden#Beds":                  {"Garden", "Beds", "Garden › Beds"},
		" Garden # Raised beds | beds": {"Garden", "Raised beds", "beds"},
		"#Beds":                        {"", "Beds", "Beds"},
	} {
		page, section, label := SplitWikilink(in)
		if [3]string{page, section, label} != want {
			t.Errorf("SplitWikilink(%q) = %q %q %q, want %q", in, page, section, label, want)
		}
	}
}

// Notes moved to the wiki (Move to wiki) or into a page (Append).
func TestPromotedSlug(t *testing.T) {
	for _, c := range [][4]string{
		{"", "Meeting with Alex", "", "meeting-with-alex"},
		{"conversations/", "Alex & Sam", "2026-07-08", "conversations/2026-07-08_alex-sam"},
		{"A/B", "x/y", "", "a/b/x-y"},
		{"people", "  ", "", ""},
	} {
		if got := PromotedSlug(c[0], c[1], c[2]); got != c[3] {
			t.Errorf("PromotedSlug(%q, %q, %q) = %q, want %q", c[0], c[1], c[2], got, c[3])
		}
	}
	for _, c := range [][3]string{{"a/b", "Title", "[[a/b|Title]]"}, {"b", "x | [y]", "[[b|x  y]]"}, {"b", "[]", "[[b|b]]"}} {
		if got := PageWikilink(c[0], c[1]); got != c[2] {
			t.Errorf("PageWikilink(%q, %q) = %q, want %q", c[0], c[1], got, c[2])
		}
	}
}

func TestNoteAsSection(t *testing.T) {
	note := "---\ntags: alex, 1on1\ntldr: All good\n---\n# Title\n\nIntro\n\n## Topics\n\n### Detail\n\n```\n# not a heading\n```\n"
	section, tags := NoteAsSection(note, "Alex 1:1 (2026-10-08)")
	if section != "## Alex 1:1 (2026-10-08)\n\n_All good_\n\nIntro\n\n### Topics\n\n#### Detail\n\n```\n# not a heading\n```\n" ||
		!reflect.DeepEqual(tags, []string{"alex", "1on1"}) {
		t.Errorf("note: %q %q", section, tags)
	}
	for in, want := range map[string]string{
		"# Only a title\n":                                           "## H\n",
		"---\ntldr: |\n  a\n  b\n---\n# T\n":                         "## H\n\n_a_\n_b_\n",
		"---\nsources: https://a.example, [[b]]\n---\n# T\n\nText\n": "## H\n\nText\n\nSources:\n\n- https://a.example\n- [[b]]\n",
	} {
		if section, tags := NoteAsSection(in, "H"); section != want || len(tags) != 0 {
			t.Errorf("NoteAsSection(%q) = %q %q, want %q", in, section, tags, want)
		}
	}
}

func TestAddFrontmatterTags(t *testing.T) {
	for _, c := range []struct {
		in   string
		tags []string
		want string
	}{
		{"# P\n", []string{"a"}, "---\ntags: a\n---\n# P\n"},
		{"---\ntags: [A, b]\n---\n# P\n", []string{"a", "c"}, "---\ntags: A, b, c\n---\n# P\n"},
		{"---\ntldr: x\n---\n# P\n", []string{"c"}, "---\ntags: c\ntldr: x\n---\n# P\n"},
		{"# P\n", nil, "# P\n"},
		{"---\ntags:\n  - a\n  - b\ntldr: x\n---\n# P\n", []string{"c"}, "---\ntags: a, b, c\ntldr: x\n---\n# P\n"},
	} {
		if got := AddFrontmatterTags(c.in, c.tags); got != c.want {
			t.Errorf("AddFrontmatterTags(%q, %q) = %q, want %q", c.in, c.tags, got, c.want)
		}
	}
}
