// ── Deck core — pure task logic, no DOM ──────────────────────────────────────
// Loaded before deck.js in the [[deck]] embed (browser: window.DeckCore).
// Also loadable from Node for tests (module.exports), which is why nothing
// here may touch the DOM, fetch, or per-instance state.

(function (root) {
  'use strict';

  function dateToStr(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }

  function addDays(dateStr, n) {
    const d = new Date(dateStr + 'T00:00:00');
    d.setDate(d.getDate() + n);
    return dateToStr(d);
  }

  // Two dates, one meaning each:
  //   due_date   — the deadline; only a past due date makes a task overdue.
  //   start_date — the day the task becomes available; a past one just means
  //                "available since", never overdue.
  // Client-only: the server has no say in which tasks a view shows.

  // A task is unplanned when it has no start date: nobody has decided when to
  // do it. A due date alone doesn't count — the deadline is the one day the
  // work should not start. The Open tab's "Unplanned only" filter and the
  // board's Unplanned section hold exactly these tasks.
  function isUnplanned(t) {
    return !t.start_date;
  }

  function isOverdue(t, today) {
    return !!t.due_date && t.due_date < today;
  }

  // Available now: started, or due today (or earlier). The Focus tab shows
  // exactly these — the board's Overdue and Today sections.
  function isFocus(t, today) {
    return (!!t.start_date && t.start_date <= today) || (!!t.due_date && t.due_date <= today);
  }

  // The deck's single filter field: words starting with '#' filter by tag, the
  // rest is a text search over title + description. Separate tag words must
  // all match (`#a #b`); a pipe inside one word means any of them (`#a|b`).
  // Returns { text, tagGroups } — every group must have at least one match.
  function parseFilter(q) {
    const words = (q || '').trim().split(/\s+/).filter(Boolean);
    const tagGroups = [];
    const text = [];
    for (const w of words) {
      if (w.startsWith('#')) {
        const group = w.split('|').map(t => t.replace(/^#/, '').toLowerCase()).filter(Boolean);
        if (group.length) tagGroups.push(group);
      } else {
        text.push(w);
      }
    }
    return { text: text.join(' ').toLowerCase(), tagGroups };
  }

  function matchesFilter(t, f) {
    if (f.text && !(t.title || '').toLowerCase().includes(f.text) &&
        !(t.description || '').toLowerCase().includes(f.text)) return false;
    const tags = (t.tags || []).map(tg => tg.toLowerCase());
    return f.tagGroups.every(g => g.some(tag => tags.includes(tag)));
  }

  // The [[deck tag=…]] / ?tag= form ("a,b" = all, "a|b" = any) as filter words.
  function tagParamToQuery(raw) {
    raw = (raw || '').trim();
    if (!raw) return '';
    if (raw.includes('|')) return '#' + raw.split('|').map(s => s.trim()).filter(Boolean).join('|');
    return raw.split(',').map(s => s.trim()).filter(Boolean).map(t => '#' + t).join(' ');
  }

  function tagMatchScore(q, tag) {
    const ql = q.toLowerCase(), tl = tag.toLowerCase();
    if (tl.startsWith(ql)) return 2;
    if (tl.includes(ql))   return 1;
    let qi = 0;
    for (let i = 0; i < tl.length && qi < ql.length; i++) {
      if (tl[i] === ql[qi]) qi++;
    }
    return qi === ql.length ? 0 : -1;
  }

  // Which board section a task belongs in: 'overdue' (missed deadline), an
  // exact YYYY-MM-DD for today..+6, 'later', or 'unplanned'. A start date in
  // the past lands on Today (it is available), not on Overdue.
  function bucketKey(t, today) {
    if (isOverdue(t, today)) return 'overdue';
    if (isFocus(t, today)) return today;
    if (!t.start_date) return 'unplanned';
    return t.start_date > addDays(today, 6) ? 'later' : t.start_date;
  }

  function fullDayLabel(d) {
    return new Date(d + 'T00:00:00').toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' });
  }

  // Group Plan-tab tasks into ordered day sections for the board. Date sections
  // (today..+6), Later, and Unplanned are droppable (dropping sets or clears the
  // start date); Overdue is display-only. Dropping on Later opens a date picker
  // in the UI layer.
  function boardBuckets(list, today) {
    const groups = {};
    list.forEach(t => {
      const key = bucketKey(t, today);
      (groups[key] = groups[key] || []).push(t);
    });
    // Within a day, deadlines first (soonest due), then undated, then by title.
    const byDate = (a, b) => (a.due_date || '9999').localeCompare(b.due_date || '9999') || (a.title || '').localeCompare(b.title || '');
    const byNewest = (a, b) => (b.created_at || '').localeCompare(a.created_at || '');
    // Droppable buckets respect manual sort_order; fall back to date/title for unordered tasks.
    const bySortOrder = (a, b) => {
      if (a.sort_order != null && b.sort_order != null) return a.sort_order - b.sort_order;
      if (a.sort_order != null) return -1;
      if (b.sort_order != null) return 1;
      return byDate(a, b);
    };
    const sections = [];
    if (groups['overdue']) sections.push({ key: 'overdue', label: 'Overdue', date: null, droppable: false, tasks: groups['overdue'].sort(byDate) });
    for (let i = 0; i < 7; i++) {
      const d = addDays(today, i);
      const label = i === 0 ? 'Today' : i === 1 ? 'Tomorrow' : fullDayLabel(d);
      sections.push({ key: d, label, date: d, droppable: true, tasks: (groups[d] || []).sort(bySortOrder) });
    }
    // Later and Unplanned are always present, even when empty: they are the
    // only drop targets for "start beyond this week" and "unplan", so hiding
    // them when empty would strand every task on a dated day.
    sections.push({ key: 'later', label: 'Later', date: null, droppable: true, tasks: (groups['later'] || []).sort(bySortOrder) });
    // Unplanned: nearest deadline first (they need a start date soonest), then
    // the undated rest in manual order.
    const byDeadline = (a, b) => (a.due_date && b.due_date) ? a.due_date.localeCompare(b.due_date)
      : a.due_date ? -1 : b.due_date ? 1 : bySortOrder(a, b);
    sections.push({ key: 'unplanned', label: 'Unplanned', date: null, droppable: true, tasks: (groups['unplanned'] || []).sort(byDeadline) });
    return sections;
  }

  const DeckCore = {
    dateToStr, addDays, isUnplanned, isOverdue, isFocus, tagMatchScore,
    parseFilter, matchesFilter, tagParamToQuery,
    bucketKey, fullDayLabel, boardBuckets,
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = DeckCore;
  else root.DeckCore = DeckCore;
})(typeof window !== 'undefined' ? window : globalThis);
