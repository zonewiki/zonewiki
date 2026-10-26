// Semantic search (src/embed.py). Only the chunking is ported so far (PORT.md
// step 6 brings the vectors): pages cut into sections, which the keyword
// search indexes too (retrieval.go's section index).
package main

import (
	"crypto/sha1"
	"encoding/hex"
	"regexp"
	"strings"
)

// maxChunkChars: sections longer than this are split at paragraphs. Small
// models only read the first ~128–512 tokens of an input, so a long
// section's tail would be lost.
const maxChunkChars = 800

var (
	chunkHeadingRE = regexp.MustCompile(`^(#{1,6})` + pySpace + `+(.*?)` + pySpace + `*#*` + pySpace + `*$`)
	chunkLinkRE    = regexp.MustCompile(`(!?\[[^\]]*\])\([^)]*\)`)
	paragraphRE    = regexp.MustCompile(`\n` + pySpace + `*\n`)
)

// splitLong: text cut at blank lines into pieces of at most maxChars
// characters (a single longer paragraph is cut at whitespace).
func splitLong(text string, maxChars int) []string {
	var pieces []string
	cur := ""
	for _, para := range paragraphRE.Split(text, -1) {
		p := []rune(pyStrip(para))
		if len(p) == 0 {
			continue
		}
		for len(p) > maxChars {
			cut := -1
			for i := maxChars - 1; i >= 0; i-- {
				if p[i] == ' ' {
					cut = i
					break
				}
			}
			if cut <= maxChars/2 {
				cut = maxChars
			}
			if cur != "" {
				pieces = append(pieces, cur)
				cur = ""
			}
			pieces = append(pieces, pyStrip(string(p[:cut])))
			p = []rune(pyStrip(string(p[cut:])))
		}
		para := string(p)
		if cur != "" && runeLen(cur)+2+len(p) > maxChars {
			pieces = append(pieces, cur)
			cur = ""
		}
		if cur != "" {
			cur += "\n\n" + para
		} else {
			cur = para
		}
	}
	if cur != "" {
		pieces = append(pieces, cur)
	}
	return pieces
}

// chunkSection is one of chunkSections' pieces: before the first heading,
// the heading is "" and the level 0. Path is the heading after those it
// sits under ("Problems › Windows"): a section headed "Windows" or "Next
// steps" says little on its own.
type chunkSection struct {
	Heading, Body string
	Level         int
	Path          string
}

type headingLevel struct {
	level int
	text  string
}

// chunkSections: a page body cut into sections at its headings (outside
// code fences), long sections further at paragraphs; link targets dropped,
// their text kept.
func chunkSections(text string, maxChars int) []chunkSection {
	sections := [][]string{{}}
	for _, l := range fencedLines(strings.Split(text, "\n")) {
		if !l.inFence && chunkHeadingRE.MatchString(l.line) {
			sections = append(sections, []string{})
		}
		sections[len(sections)-1] = append(sections[len(sections)-1], l.line)
	}
	var chunks []chunkSection
	var parents []headingLevel // the open headings above
	for _, lines := range sections {
		heading, level := "", 0
		if len(lines) > 0 {
			if m := chunkHeadingRE.FindStringSubmatch(lines[0]); m != nil {
				heading, level = m[2], len(m[1])
				lines = lines[1:]
				var kept []headingLevel
				for _, p := range parents {
					if p.level < level {
						kept = append(kept, p)
					}
				}
				parents = append(kept, headingLevel{level, heading})
			}
		}
		path := ""
		if heading != "" {
			names := make([]string, len(parents))
			for i, p := range parents {
				names[i] = p.text
			}
			path = strings.Join(names, " › ")
		}
		body := pyStrip(chunkLinkRE.ReplaceAllString(strings.Join(lines, "\n"), "${1}"))
		if body == "" {
			continue
		}
		for _, piece := range splitLong(body, maxChars) {
			chunks = append(chunks, chunkSection{heading, piece, level, path})
		}
	}
	return chunks
}

// chunkText: a section as it is embedded and indexed, its heading path
// then its text.
func chunkText(path, body string) string {
	if path != "" {
		return path + "\n" + body
	}
	return body
}

// chunkKey: a chunk's identity, the hash its vector is stored under.
func chunkKey(title, chunk string) string {
	if chunk != "" {
		return sha1Hex(title + "\n" + chunk)
	}
	return sha1Hex(title)
}

func sha1Hex(s string) string {
	h := sha1.Sum([]byte(s))
	return hex.EncodeToString(h[:])
}
