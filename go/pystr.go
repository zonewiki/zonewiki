// Python's string functions where Go's differ: Python's whitespace is
// Unicode's plus the separators U+001C–U+001F (str.strip, str.split).
package main

import (
	"strings"
	"unicode"
)

func pyIsSpace(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f }

func pyStrip(s string) string { return strings.TrimFunc(s, pyIsSpace) }

func pySplit(s string) []string { return strings.FieldsFunc(s, pyIsSpace) }
