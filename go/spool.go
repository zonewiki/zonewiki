// The mail folder (docs/spec/deployment.md, The mail folder): with
// ZONE_MAIL_SPOOL Zonewiki sends no mail itself. It writes each one as a
// file into .zone/outbox/, and whoever runs it sends what is there and
// takes the file away: a host whose Zonewiki has no network, or one with a
// relay of its own. A file is whole or not there (written beside the
// folder, then renamed into it), owner-only, and named so that the oldest
// sorts first. Nobody fetching them mustn't fill the wiki: with
// spoolMax files waiting, a new mail is dropped, and the log says so.
package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// spoolDirName: the mail folder, in .zone/.
const spoolDirName = "outbox"

// spoolMax: the mails that wait in the folder, at most.
const spoolMax = 500

// spooledMail: a mail as its file holds it. Who sends it writes the
// headers; these four fields are all Zonewiki says.
type spooledMail struct {
	To      string `json:"to"`      // one address
	Subject string `json:"subject"` // one line
	Body    string `json:"body"`    // plain text
	At      string `json:"at"`      // when it was written, UTC (RFC 3339)
}

// spoolMu: one mail is counted and written at a time, so the folder never
// holds more than spoolMax.
var spoolMu sync.Mutex

// spoolName: a mail's file name: the time to the nanosecond, UTC, and
// random characters ("20261125T193248.000000001Z-0123456789abcdef.json").
func spoolName(t time.Time) string {
	id := make([]byte, 8)
	_, _ = rand.Read(id)
	return t.UTC().Format("20060102T150405.000000000Z") + "-" + hex.EncodeToString(id) + ".json"
}

// spoolMail writes a mail into the mail folder.
func spoolMail(to, subject, body string) error {
	spoolMu.Lock()
	defer spoolMu.Unlock()
	dir := statePath(spoolDirName)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	waiting, err := os.ReadDir(dir)
	if err != nil {
		return err
	}
	if len(waiting) >= spoolMax {
		return fmt.Errorf("the mail folder %s holds %d mails that nobody fetched: dropped the mail to %s (%s)",
			filepath.Join(stateDirName, spoolDirName), len(waiting), to, oneLine(subject))
	}
	t := now()
	b, err := json.Marshal(spooledMail{To: to, Subject: strings.Join(strings.Fields(subject), " "),
		Body: strings.ReplaceAll(body, "\r", ""), At: t.UTC().Format(time.RFC3339)})
	if err != nil {
		return err
	}
	name := spoolName(t)
	// Beside the folder, not in it: whoever empties the folder never sees
	// half a mail, nor a file it doesn't know.
	tmp := statePath(spoolDirName + "-" + name + ".tmp")
	if err := os.WriteFile(tmp, append(b, '\n'), 0o600); err != nil {
		return err
	}
	if err := os.Rename(tmp, filepath.Join(dir, name)); err != nil {
		os.Remove(tmp)
		return err
	}
	return nil
}
