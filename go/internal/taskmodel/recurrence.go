// A series' dates: the next occurrence of a recurrence, and a recurrence
// as a request or a snapshot gives it.
package taskmodel

import (
	"encoding/json"
	"time"
	"zonewiki/internal/jsonobj"
)

// DaysIn: the days of a month.
func DaysIn(year int, month time.Month) int {
	return time.Date(year, month+1, 0, 0, 0, 0, 0, time.UTC).Day()
}

// AddMonths adds months, clamping the day to the month's last (Jan 31 + 1
// month = Feb 28/29); day (when > 0) is the wanted day of month, so a
// series on the 31st returns to it after a shorter month.
func AddMonths(base time.Time, months, day int) time.Time {
	m := int(base.Month()) - 1 + months
	year := base.Year() + floorDiv(m, 12)
	month := time.Month(m - floorDiv(m, 12)*12 + 1)
	if day <= 0 {
		day = base.Day()
	}
	return time.Date(year, month, min(day, DaysIn(year, month)), 0, 0, 0, 0, time.Local)
}

func floorDiv(a, b int) int {
	q := a / b
	if a%b != 0 && (a < 0) != (b < 0) {
		q--
	}
	return q
}

// RecIntervalMax: the largest "every N", far beyond any real series and
// small enough that stepping never leaves the calendar.
const RecIntervalMax = 999

func step(base time.Time, rtype string, interval, day int) time.Time {
	switch rtype {
	case "weekly":
		return base.AddDate(0, 0, 7*interval)
	case "monthly":
		return AddMonths(base, interval, day)
	case "yearly":
		return AddMonths(base, 12*interval, day) // Feb 29 → Feb 28
	}
	return base.AddDate(0, 0, interval) // daily (and any unknown type)
}

// NextOccurrence steps a recurrence from base and catches up to >= today
// (> today with afterToday: a skipped occurrence isn't offered again).
// Monthly and yearly series remember their day of month in rec.Day
// (stepping from a clamped date would drift: Jan 31 → Feb 28 → Mar 28);
// from the completion day there is no anchor. Returns the series as the
// next occurrence carries it.
func NextOccurrence(rec *Recurrence, base time.Time, anchored bool, today time.Time, afterToday bool) (time.Time, *Recurrence) {
	next := *rec
	day := 0
	if (rec.Type == "monthly" || rec.Type == "yearly") && anchored {
		if day = rec.Day; day == 0 {
			day = base.Day()
		}
		next.Day = day
	}
	at := step(base, rec.Type, rec.Interval, day)
	for at.Before(today) || afterToday && at.Equal(today) {
		at = step(at, rec.Type, rec.Interval, day)
	}
	return at, &next
}

// CleanRecurrence: a recurrence from a request or a snapshot; nil when it
// has no type Zonewiki knows.
func CleanRecurrence(raw json.RawMessage) *Recurrence {
	rec, ok := jsonobj.As[jsonobj.Obj](raw)
	if !ok {
		return nil
	}
	rtype, _ := jsonobj.As[string](rec["type"])
	switch rtype {
	case "daily", "weekly", "monthly", "yearly":
	default:
		return nil
	}
	out := &Recurrence{Type: rtype, Interval: 1, From: "due"}
	if n, ok := jsonobj.As[int](rec["interval"]); ok && n >= 1 && n <= RecIntervalMax {
		out.Interval = n
	}
	if s, _ := jsonobj.As[string](rec["from"]); s == "completion" {
		out.From = "completion"
	}
	if n, ok := jsonobj.As[int](rec["day"]); ok && n >= 1 && n <= 31 {
		out.Day = n
	}
	if n, ok := jsonobj.As[int](rec["lead"]); ok && n >= 0 {
		out.Lead = &n
	}
	return out
}
