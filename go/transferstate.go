// What a wiki's users keep beside it, moving with the wiki (Moving a wiki
// in docs/spec/deployment.md): what each watches and wants mailed
// (.zone/watch/<user>.json, watch.go) and what each has seen of the tasks'
// news (.zone/seen/<user>.json, taskseen.go). An export copies the files of
// the users the settings file names; an import takes each for what anyone
// may have made (transfer.go): it reads what Zonewiki would have written
// itself out of it and writes that anew, for a user the imported settings
// have, and passes over the rest.
package main

import (
	"encoding/json"
	"maps"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"time"
	"zonewiki/internal/atomicfile"
	"zonewiki/internal/wikiarchive"
)

// The most an import takes of one user's file: watches, and looks at
// tasks. Zonewiki's own files stay far below (a look is taken out once a
// file names more than seenKept tasks).
const (
	importedWatches = 5000
	importedLooks   = 5000
)

// userFileName: the file of user in one of wikiarchive.UserFolders, ""
// for a name that is no file's.
func userFileName(user string) string {
	if user == "" || user != filepath.Base(user) || strings.HasPrefix(user, ".") {
		return ""
	}
	return user + ".json"
}

// exportUserFiles copies the files of users (the settings file's) into the
// clone an export writes, and says how many users have one. A file that
// isn't there is nobody's loss; Zonewiki writes each whole, by a rename, so
// one read while it runs is never half of it.
func exportUserFiles(clone string, users []string) (int, error) {
	have := map[string]bool{}
	for _, folder := range wikiarchive.UserFolders {
		for _, user := range users {
			name := userFileName(user)
			if name == "" {
				continue
			}
			raw, err := os.ReadFile(filepath.Join(statePath(folder), name))
			if err != nil || len(raw) == 0 || len(raw) > wikiarchive.UserFileMax {
				continue
			}
			if err := atomicfile.WriteOwnerOnly(filepath.Join(clone, stateDirName, folder, name), string(raw)); err != nil {
				return 0, err
			}
			have[user] = true
		}
	}
	return len(have), nil
}

// userFiles: what an import keeps of the archive's user files: folder →
// user → the file's text, as Zonewiki writes it.
type userFiles map[string]map[string]string

// users: how many users have a file.
func (f userFiles) users() int {
	names := map[string]bool{}
	for _, files := range f {
		for user := range files {
			names[user] = true
		}
	}
	return len(names)
}

// readUserFiles reads the user files an archive was unpacked with (src:
// its wiki/), those of users alone, each through its folder's reading
// (importedWatch, importedSeen). What can't be read, or holds nothing to
// keep, is passed over: a user without their watches is the import of a
// day ago, not a broken wiki.
func readUserFiles(src string, users []string) userFiles {
	read := map[string]func([]byte) string{"watch": importedWatch, "seen": importedSeen}
	out := userFiles{}
	for _, folder := range wikiarchive.UserFolders {
		for _, user := range users {
			name := userFileName(user)
			if name == "" {
				continue
			}
			path := filepath.Join(src, stateDirName, folder, name)
			if st, err := os.Lstat(path); err != nil || !st.Mode().IsRegular() || st.Size() > wikiarchive.UserFileMax {
				continue
			}
			raw, err := os.ReadFile(path)
			if err != nil {
				continue
			}
			if text := read[folder](raw); text != "" {
				if out[folder] == nil {
					out[folder] = map[string]string{}
				}
				out[folder][user] = text
			}
		}
	}
	return out
}

// write puts the files into the wiki at dir, each for its owner alone.
func (f userFiles) write(dir string) error {
	for folder, files := range f {
		for user, text := range files {
			if err := atomicfile.WriteOwnerOnly(filepath.Join(dir, stateDirName, folder, userFileName(user)), text); err != nil {
				return err
			}
		}
	}
	return nil
}

// watchKeyOK: key could be a watch: a page's slug, or a folder's path with
// a "/" after it. Not whether there is such a page: a watch of none tells
// nothing, as one of a page deleted since.
func watchKeyOK(key string) bool {
	slug := strings.TrimSuffix(key, "/")
	if slug == "" || len(key) > 500 {
		return false
	}
	for _, part := range strings.Split(slug, "/") {
		if part == "" || part == "." || part == ".." || strings.ContainsFunc(part, func(r rune) bool { return r < ' ' || r == '\\' || r == 0x7f }) {
			return false
		}
	}
	return true
}

var (
	lookAfterRE = regexp.MustCompile(`^[0-9a-f]{1,40}$`)
	lookCommRE  = regexp.MustCompile(`^[0-9A-Za-z_-]{1,64}$`)
)

// importedWatch: a watch file as an import writes it, from an archive's
// bytes: the watches that could be one, what was seen of those, and the
// kinds of mail Zonewiki knows, in its order, and the mentions unread, each
// of what could be a page, by what could be a user. "" for nothing to keep.
// (Which kinds a user may have is asked when a mail goes out: mails.)
func importedWatch(raw []byte) string {
	var in watched
	if json.Unmarshal(raw, &in) != nil {
		return ""
	}
	out := watched{Watch: map[string]int64{}, Seen: map[string]seenAt{}}
	for _, key := range slices.Sorted(maps.Keys(in.Watch)) {
		if at := in.Watch[key]; watchKeyOK(key) && at > 0 && len(out.Watch) < importedWatches {
			out.Watch[key] = at
		}
	}
	for _, slug := range slices.Sorted(maps.Keys(in.Seen)) {
		at := in.Seen[slug]
		if _, covered := out.covers(slug); !covered || !watchKeyOK(slug) || at.At < 0 || at.Sha != "" && !shaRE.MatchString(at.Sha) ||
			len(out.Seen) >= importedWatches {
			continue
		}
		out.Seen[slug] = at
	}
	for _, kind := range mailKinds {
		if slices.Contains(in.Mail, kind) {
			out.Mail = append(out.Mail, kind)
		}
	}
	for _, slug := range slices.Sorted(maps.Keys(in.Mentions)) {
		m := in.Mentions[slug]
		if !watchKeyOK(slug) || strings.HasSuffix(slug, "/") || m.At <= 0 || !userNameRE.MatchString(m.By) ||
			m.Sha != "" && !shaRE.MatchString(m.Sha) || len(out.Mentions) >= importedWatches {
			continue
		}
		if out.Mentions == nil {
			out.Mentions = map[string]pageMention{}
		}
		out.Mentions[slug] = m
	}
	if out.empty() {
		return ""
	}
	text, err := json.MarshalIndent(out, "", "  ")
	if err != nil {
		return ""
	}
	return string(text) + "\n"
}

// importedSeen: a seen file as an import writes it, from an archive's
// bytes: the looks at what could be a task, each as Zonewiki writes one.
func importedSeen(raw []byte) string {
	var in map[string]look
	if json.Unmarshal(raw, &in) != nil {
		return ""
	}
	out := map[string]look{}
	for _, id := range slices.Sorted(maps.Keys(in)) {
		at := in[id]
		if _, err := time.Parse("2006-01-02T15:04", at.At); err != nil || !taskIDRE.MatchString(id) || len(out) >= importedLooks ||
			at.After != "" && !lookAfterRE.MatchString(at.After) || at.Comment != "" && !lookCommRE.MatchString(at.Comment) {
			continue
		}
		out[id] = at
	}
	if len(out) == 0 {
		return ""
	}
	text, err := json.MarshalIndent(out, "", "  ")
	if err != nil {
		return ""
	}
	return string(text) + "\n"
}
