// Storage: the settings upload-max (the biggest upload) and storage-max
// (how much the wiki may hold), their sizes ("25MB"), and what the wiki
// holds as storage-max counts it.
package main

import (
	"errors"
	"fmt"
	"io/fs"
	"math"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"
)

// defaultUploadMax: an upload's cap without upload-max.
const defaultUploadMax = 100 << 20

var sizeRE = regexp.MustCompile(`(?i)^([0-9]+(?:\.[0-9]+)?) *(|b|bytes?|kb|mb|gb|tb)$`)

// parseSize: a size as the settings file writes it: bytes, or a number with
// KB, MB, GB or TB (1024 apart, as Zone's caps have always counted: 100 MB is
// 100 × 1024 × 1024 bytes); "1.5GB" too. At least 1 byte.
func parseSize(s string) (int64, error) {
	m := sizeRE.FindStringSubmatch(strings.TrimSpace(s))
	if m == nil {
		return 0, errors.New("not a size")
	}
	n, err := strconv.ParseFloat(m[1], 64)
	if err != nil {
		return 0, err
	}
	shift := map[string]uint{"kb": 10, "mb": 20, "gb": 30, "tb": 40}[strings.ToLower(m[2])]
	n *= float64(int64(1) << shift)
	if n < 1 || n > math.MaxInt64/2 {
		return 0, errors.New("not a size")
	}
	return int64(n), nil
}

// formatSize: n bytes for a message: "100 MB", "1.5 GB", "512 bytes".
func formatSize(n int64) string {
	units := []string{"KB", "MB", "GB", "TB"}
	if n < 1<<10 {
		return fmt.Sprintf("%d bytes", n)
	}
	v, unit := float64(n)/(1<<10), units[0]
	for _, u := range units[1:] {
		if v < 1<<10 {
			break
		}
		v, unit = v/(1<<10), u
	}
	if v == math.Trunc(v) {
		return fmt.Sprintf("%.0f %s", v, unit)
	}
	return fmt.Sprintf("%.1f %s", v, unit)
}

// uploadMax: the biggest upload, upload-max or 100 MB.
func (s *Settings) uploadMax() int64 {
	if s.UploadMax > 0 {
		return s.UploadMax
	}
	return defaultUploadMax
}

// used: what the wiki holds, in bytes, as storage-max counts it: every
// file in the data directory but .git/ and Zone's own folder (.zone/: the
// settings, the session key, the search's cache), which the user doesn't
// control. Walked only when a write asks (checkRoom), and then at most
// once a minute (a walk takes milliseconds: 20,000 files in 10 ms); a write
// that adds a file adds it meanwhile (added), so a burst of uploads can't
// slip past the limit.
var used struct {
	sync.Mutex
	bytes int64
	at    time.Time
}

// usageEvery: how old the figure may be; usageRecheck: how soon a figure
// that says full may be walked again (fresh), so a client asking over and
// over in a full wiki doesn't make a walk each time.
var usageEvery, usageRecheck = time.Minute, 5 * time.Second

// usedBytes: what the wiki holds, a figure at most usageEvery old (plus
// what was added since); fresh: at most usageRecheck old.
func usedBytes(fresh bool) int64 {
	used.Lock()
	defer used.Unlock()
	age := time.Since(used.at)
	if used.at.IsZero() || age > usageEvery || fresh && age > usageRecheck {
		used.bytes, used.at = walkUsage(), time.Now()
	}
	return used.bytes
}

// forgetUsage: the wiki holds less (a page deleted): the next check walks
// it, so the room counts at once.
func forgetUsage() {
	used.Lock()
	used.at = time.Time{}
	used.Unlock()
}

// added: a write added n bytes (an upload, a new page).
func added(n int64) {
	used.Lock()
	used.bytes += n
	used.Unlock()
}

func walkUsage() int64 {
	state := filepath.Base(stateDirIn(cfg.Data))
	var total int64
	_ = filepath.WalkDir(cfg.Data, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil // gone meanwhile, or unreadable: not counted
		}
		if d.IsDir() {
			if filepath.Dir(p) == filepath.Clean(cfg.Data) && (d.Name() == ".git" || d.Name() == state) {
				return filepath.SkipDir
			}
			return nil
		}
		if d.Type().IsRegular() {
			if info, err := d.Info(); err == nil {
				total += info.Size()
			}
		}
		return nil
	})
	return total
}

// checkRoom: refused (refusedFull) when adding n bytes would take the wiki
// past storage-max. Only what makes something new asks: an upload, a new
// page or note; an edit to a page never does, so typing is never refused.
// What adds nothing (an upload replacing a bigger file) always fits. A
// figure that says full is walked again first (at most every usageRecheck),
// so a file deleted on disk counts within seconds; one deleted through
// Zone at once (forgetUsage).
func checkRoom(n int64) error {
	max := settings().StorageMax
	if max == 0 || n <= 0 {
		return nil
	}
	holds := usedBytes(false)
	if holds+n > max {
		holds = usedBytes(true)
	}
	if holds+n <= max {
		return nil
	}
	return refuseAs(refusedFull, fmt.Sprintf("The wiki is full: %s of %s used. Delete pages or files to make room.",
		formatSize(holds), formatSize(max)))
}
