// Package settingsfile is the format of Zone's settings file (.zone/config):
// a small INI of "key = value" lines and [user NAME] sections, read
// strictly (an unknown key, a key given twice: an error naming its line) and
// edited a line at a time, keeping the comments and the order of the lines
// an edit doesn't change. What the keys mean is Zone's (configfile.go).
package settingsfile

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
)

// Keys: what a file may set. Top: the keys outside every section; User:
// those of a [user …] section; UserName: what a user's name must match.
type Keys struct {
	Top, User []string
	UserName  *regexp.Regexp
}

// File: the file as read, its lines (kept for rewriting) and what they set.
type File struct {
	Lines []string
	Top   map[string]Value // key → value, outside every section
	Users []User           // the [user …] sections, in order
	Read  string           // the text as read (a command's check that no one changed it since)
}

// Value: a key's value, and the line that sets it.
type Value struct {
	Value string
	Line  int // 1-based
}

// User: a [user NAME] section, its header's line and what it sets.
type User struct {
	Name   string
	Line   int
	Values map[string]Value
}

// Empty: a file with nothing in it (one that isn't there).
func Empty() *File { return &File{Top: map[string]Value{}} }

// User: name's section, nil if there is none.
func (f *File) User(name string) *User {
	for i := range f.Users {
		if f.Users[i].Name == name {
			return &f.Users[i]
		}
	}
	return nil
}

// LineError: what is wrong on line n of the file called name.
func LineError(name string, n int, format string, args ...any) error {
	return fmt.Errorf("%s line %d: %s", name, n, fmt.Sprintf(format, args...))
}

// sectionName: a line's section header's name ("[user alice]" → "user
// alice"), or false if the line isn't one.
func sectionName(line string) (string, bool) {
	t := strings.TrimSpace(line)
	if !strings.HasPrefix(t, "[") || !strings.HasSuffix(t, "]") {
		return "", false
	}
	return strings.Join(strings.Fields(t[1:len(t)-1]), " "), true
}

// isComment: a blank line, or one starting with "#" ("noshare = #tag" is a
// value, not a comment).
func isComment(line string) bool {
	t := strings.TrimSpace(line)
	return t == "" || strings.HasPrefix(t, "#")
}

// Parse reads text, the file called name (as its errors name it); an
// unknown key, a section that isn't a user's, a key given twice: an error
// naming its line, as a typo shouldn't leave Zone on a default.
func Parse(name, text string, keys Keys) (*File, error) {
	read := text
	text = strings.TrimPrefix(strings.ReplaceAll(text, "\r\n", "\n"), "\ufeff")
	f := &File{Lines: strings.Split(strings.TrimSuffix(text, "\n"), "\n"), Top: map[string]Value{}, Read: read}
	if text == "" {
		f.Lines = nil
	}
	lineError := func(n int, format string, args ...any) error { return LineError(name, n, format, args...) }
	values, known := f.Top, keys.Top
	for i, line := range f.Lines {
		n := i + 1
		if isComment(line) {
			continue
		}
		if section, ok := sectionName(line); ok {
			kind, user, _ := strings.Cut(section, " ")
			if kind != "user" {
				return nil, lineError(n, "[%s] isn't a section Zone knows. Users are [user NAME].", section)
			}
			if !keys.UserName.MatchString(user) {
				return nil, lineError(n, "%q isn't a user name. Use lowercase letters, digits and hyphens "+
					"(e.g. alice, mary-ann).", user)
			}
			if u := f.User(user); u != nil {
				return nil, lineError(n, "[user %s] is there twice (line %d).", user, u.Line)
			}
			f.Users = append(f.Users, User{Name: user, Line: n, Values: map[string]Value{}})
			values, known = f.Users[len(f.Users)-1].Values, keys.User
			continue
		}
		key, value, found := strings.Cut(line, "=")
		key = strings.TrimSpace(key)
		if !found || key == "" {
			return nil, lineError(n, "not a setting (key = value), a [user NAME] or a # comment.")
		}
		if !slices.Contains(known, key) {
			if len(f.Users) > 0 {
				return nil, lineError(n, "%q isn't a user's setting. A user has %s; the wiki's settings "+
					"go before the first [user …].", key, strings.Join(keys.User, " and "))
			}
			return nil, lineError(n, "%q isn't a setting Zone knows.", key)
		}
		if v, twice := values[key]; twice {
			return nil, lineError(n, "%s is set twice (line %d).", key, v.Line)
		}
		values[key] = Value{strings.TrimSpace(value), n}
	}
	return f, nil
}

// Editing the file (`zonewiki user`, `zonewiki config`): a line at a time,
// keeping the comments and the order of the lines an edit doesn't change;
// Write puts it on disk whole.

// sectionRange: the lines of a section (user "": the top, before the first
// section), from its header (or 0) to the next header; -1 if it isn't there.
func (f *File) sectionRange(user string) (start, end int) {
	start = -1
	if user == "" {
		start = 0
	}
	for i, line := range f.Lines {
		name, ok := sectionName(line)
		if !ok {
			continue
		}
		if start >= 0 {
			return start, i
		}
		if name == "user "+user {
			start = i
		}
	}
	if start < 0 {
		return -1, -1
	}
	return start, len(f.Lines)
}

// Set gives key a value in user's section (""; the top), replacing its line
// or adding one after the section's last setting; a new section goes at
// the end.
func (f *File) Set(user, key, value string) {
	line := key + " = " + value
	start, end := f.sectionRange(user)
	if start < 0 {
		if len(f.Lines) > 0 && strings.TrimSpace(f.Lines[len(f.Lines)-1]) != "" {
			f.Lines = append(f.Lines, "")
		}
		f.Lines = append(f.Lines, "[user "+user+"]", line)
		return
	}
	at := start
	if user != "" {
		at++
	}
	for i := start; i < end; i++ {
		if isComment(f.Lines[i]) {
			continue
		}
		if k, _, _ := strings.Cut(f.Lines[i], "="); strings.TrimSpace(k) == key {
			f.Lines[i] = line
			return
		}
		if _, header := sectionName(f.Lines[i]); !header {
			at = i + 1
		}
	}
	if user == "" && at == 0 {
		// Below the comments the file starts with.
		for at < end && strings.HasPrefix(strings.TrimSpace(f.Lines[at]), "#") {
			at++
		}
	}
	if at < len(f.Lines) {
		if _, header := sectionName(f.Lines[at]); header {
			f.Lines = slices.Insert(f.Lines, at, "") // the wiki's settings, then the users
		}
	}
	f.Lines = slices.Insert(f.Lines, at, line)
}

// Unset takes key's line out of user's section ("": the top).
func (f *File) Unset(user, key string) {
	start, end := f.sectionRange(user)
	for i := max(start, 0); start >= 0 && i < end; i++ {
		if k, _, found := strings.Cut(f.Lines[i], "="); found && !isComment(f.Lines[i]) && strings.TrimSpace(k) == key {
			f.Lines = slices.Delete(f.Lines, i, i+1)
			return
		}
	}
}

// RemoveUser takes user's section out, with the comments right above it
// (they are about it; not those the file starts with), but not those right
// above the next section.
func (f *File) RemoveUser(user string) {
	start, end := f.sectionRange(user)
	if start < 0 {
		return
	}
	above := start
	for above > 0 && strings.HasPrefix(strings.TrimSpace(f.Lines[above-1]), "#") {
		above--
	}
	if above > 0 {
		start = above
	}
	if end < len(f.Lines) {
		for end > start+1 && strings.HasPrefix(strings.TrimSpace(f.Lines[end-1]), "#") {
			end--
		}
	}
	for end > start+1 && strings.TrimSpace(f.Lines[end-1]) == "" {
		end--
	}
	// One blank line between what was before and after it, not two.
	for start > 0 && strings.TrimSpace(f.Lines[start-1]) == "" {
		start--
	}
	f.Lines = slices.Delete(f.Lines, start, end)
	if start > 0 && start < len(f.Lines) && strings.TrimSpace(f.Lines[start]) != "" {
		f.Lines = slices.Insert(f.Lines, start, "")
	}
}

// Text: the file's text as it is now.
func (f *File) Text() string {
	if len(f.Lines) == 0 {
		return ""
	}
	return strings.Join(f.Lines, "\n") + "\n"
}

// Write writes text as the file at path, owner-only (it holds password
// hashes and tokens): whole, to a temporary file next to it renamed over
// it, so no one reads half a file.
func Write(path, text string) error {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o777); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, ".config-*")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if err = tmp.Chmod(0o600); err == nil {
		_, err = tmp.WriteString(text)
	}
	if err == nil {
		err = tmp.Sync() // on disk before it replaces the file
	}
	if cerr := tmp.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		return err
	}
	return os.Rename(tmp.Name(), path)
}
