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

  // A task with an open (non-completed) blocker is not actionable: it is
  // excluded from Focus and dimmed elsewhere. Completing the blocker
  // surfaces it again automatically. byId is a Map of id → task.
  function isBlocked(t, byId) {
    return (t.blocked_by || []).some(bid => {
      const blocker = byId.get(bid);
      return blocker && !blocker.completed_at;
    });
  }

  // A task is deferred (hidden from Backlog/Focus/Plan) until its start_date,
  // or until defer_days before its due_date — the most restrictive wins.
  // An explicit plan overrides deferral: focus_date <= today makes the task
  // visible even when start_date/defer_days would still hide it.
  // Mirror of _is_deferred() in deck.py; keep the two in sync.
  function isDeferred(t, today) {
    if (t.completed_at) return false;
    if (t.focus_date && t.focus_date <= today) return false;
    if (t.start_date && t.start_date > today) return true;
    if (t.defer_days != null && t.due_date) {
      if (addDays(t.due_date, -t.defer_days) > today) return true;
    }
    return false;
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

  // Board grouping is plan-first: a task's planned date (focus_date) wins over
  // its deadline, so dragging a due-dated task to another day visibly moves it.
  function planDate(t) { return t.focus_date || t.due_date || ''; }

  // Which bucket a date falls in: 'overdue', an exact YYYY-MM-DD for
  // today..+6, or 'later'.
  function dayBucketKey(d, today) {
    if (!d) return 'later';
    if (d < today) return 'overdue';
    if (d > addDays(today, 6)) return 'later';
    return d;
  }

  function fullDayLabel(d) {
    return new Date(d + 'T00:00:00').toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' });
  }

  // Group Plan-tab tasks into ordered day sections for the board. Date sections
  // (today..+6), Later, and Someday are droppable (re-plan targets); Overdue is
  // display-only. Dropping on Later opens a date picker in the UI layer.
  function boardBuckets(list, today) {
    const groups = {};
    list.forEach(t => {
      const key = (!t.due_date && !t.focus_date) ? 'someday' : dayBucketKey(planDate(t), today);
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
    if (groups['later']) sections.push({ key: 'later', label: 'Later', date: null, droppable: true, tasks: groups['later'].sort(bySortOrder) });
    if (groups['someday']) sections.push({ key: 'someday', label: 'Someday', date: null, droppable: true, tasks: groups['someday'].sort(bySortOrder) });
    return sections;
  }

  const DeckCore = {
    dateToStr, addDays, isBlocked, isDeferred, tagMatchScore,
    planDate, dayBucketKey, fullDayLabel, boardBuckets,
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = DeckCore;
  else root.DeckCore = DeckCore;
})(typeof window !== 'undefined' ? window : globalThis);
