// ── Deck core — pure task logic, no DOM ──────────────────────────────────────
// An ES module imported by deck.js and the editor (Make task's quick-add
// parser), and by the Node tests (tests/test_deck_core.py), which is why
// nothing here may touch the DOM, fetch, or per-instance state.

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

function isOverdue(t, today) {
  return !!t.due_date && t.due_date < today;
}

// Available now: started, or due today (or earlier). The Focus tab shows
// exactly these — the board's Overdue, Carried over and Today sections.
function isFocus(t, today) {
  return (!!t.start_date && t.start_date <= today) || (!!t.due_date && t.due_date <= today);
}

// Open's order, also Focus's (Open filtered) and the order inside every
// Plan-board section but Overdue: Unsorted (no sort_order) first, newest
// first, then Sorted in sort_order. Plan only moves tasks between days.
function byOpenOrder(a, b) {
  if (a.sort_order == null && b.sort_order == null) return (b.created_at || '').localeCompare(a.created_at || '');
  if (a.sort_order == null) return -1;
  if (b.sort_order == null) return 1;
  return a.sort_order - b.sort_order;
}

// The deck's single filter field: words starting with '#' filter by tag, the
// rest is a text search over title + description. Separate tag words must
// all match (`#a #b`); a pipe inside one word means any of them (`#a|b`).
// A word starting with '-#' excludes its tags (`-#mary`, `-#a|b`: neither).
// Returns { text, tagGroups, excludeTags } — every group must have at least
// one match, and no excluded tag may be present.
function parseFilter(q) {
  const words = (q || '').trim().split(/\s+/).filter(Boolean);
  const tagGroups = [];
  const excludeTags = [];
  const text = [];
  const tagsOf = w => w.split('|').map(t => t.replace(/^#/, '').toLowerCase()).filter(Boolean);
  for (const w of words) {
    if (w.startsWith('-#')) excludeTags.push(...tagsOf(w.slice(1)));
    else if (w.startsWith('#')) {
      const group = tagsOf(w);
      if (group.length) tagGroups.push(group);
    } else {
      text.push(w);
    }
  }
  return { text: text.join(' ').toLowerCase(), tagGroups, excludeTags };
}

function matchesFilter(t, f) {
  if (f.text && !(t.title || '').toLowerCase().includes(f.text) &&
      !(t.description || '').toLowerCase().includes(f.text)) return false;
  const tags = (t.tags || []).map(tg => tg.toLowerCase());
  if ((f.excludeTags || []).some(tag => tags.includes(tag))) return false;
  return f.tagGroups.every(g => g.some(tag => tags.includes(tag)));
}

// The [[deck tag=…]] / ?tag= form ("a,b" = all, "a|b" = any) as filter words.
function tagParamToQuery(raw) {
  raw = (raw || '').trim();
  if (!raw) return '';
  if (raw.includes('|')) return '#' + raw.split('|').map(s => s.trim()).filter(Boolean).join('|');
  // A leading '-' excludes the tag (`tag=work,-mary`).
  return raw.split(',').map(s => s.trim()).filter(Boolean)
    .map(t => t.startsWith('-') ? '-#' + t.slice(1) : '#' + t).join(' ');
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

// Tag fields never hold a space: tags are one word each (the filter field
// splits on spaces). Applied on every input event of the edit modal's Tags
// field (list=true, comma-separated) and the bulk tag dialog (list=false):
// whitespace inside a tag becomes "-", whitespace after a comma or at the
// start goes, commas are followed by one space, and a finished tag loses a
// trailing "-". Returns the new value and where the caret should go.
function sanitizeTagInput(value, caret, list) {
  const clean = v => {
    if (!list) return v.trimStart().replace(/\s+/g, '-');
    const segs = v.split(',');
    return segs.map((seg, i) => {
      const t = seg.trimStart().replace(/\s+/g, '-');
      return i < segs.length - 1 ? t.replace(/-+$/, '') : t;
    }).join(', ');
  };
  const out = clean(value);
  const at = caret == null ? out.length : Math.min(clean(value.slice(0, caret)).length, out.length);
  return { value: out, caret: at };
}

// How many tasks carry each tag — the tie-breaker for suggestions.
function tagCounts(tasks) {
  const counts = {};
  for (const t of tasks) for (const tag of t.tags || []) counts[tag] = (counts[tag] || 0) + 1;
  return counts;
}

// The tags a selection of tasks carries (the bulk tag dialog): each with
// how many of the tasks have it, case ignored (the first spelling seen),
// most common first, then A–Z.
function selectionTags(tasks) {
  const byLower = new Map();
  for (const t of tasks) {
    const seen = new Set();
    for (const tag of t.tags || []) {
      const key = tag.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      const row = byLower.get(key) || byLower.set(key, { tag, count: 0 }).get(key);
      row.count++;
    }
  }
  return [...byLower.values()].sort((a, b) => b.count - a.count || a.tag.localeCompare(b.tag));
}

// What the bulk tag dialog sends to batch-tag. `states` maps each of the
// selection's tags to 'all' (every task gets it), 'none' (it goes) or
// 'some' (left as it is); `added` are tags typed in. A tag both typed and
// set to none goes.
function bulkTagChange(states, added) {
  const remove = Object.keys(states).filter(t => states[t] === 'none');
  const gone = new Set(remove.map(t => t.toLowerCase()));
  const add = [];
  const seen = new Set();
  for (const tag of [...Object.keys(states).filter(t => states[t] === 'all'), ...added]) {
    const key = tag.toLowerCase();
    if (gone.has(key) || seen.has(key)) continue;
    seen.add(key);
    add.push(tag);
  }
  return { add, remove };
}

// Tag suggestions, best first — every tag field completes to the first one
// on Tab/Enter. Exact match, then prefix, substring and in-order letters
// (tagMatchScore); within a level the most-used tag wins, then A–Z. An empty
// partial lists the most-used tags.
function rankTags(partial, counts, limit = 8) {
  const q = (partial || '').toLowerCase();
  return Object.keys(counts)
    .map(tag => ({ tag, score: !q ? 0 : tag.toLowerCase() === q ? 3 : tagMatchScore(q, tag) }))
    .filter(x => x.score >= 0)
    .sort((a, b) => b.score - a.score || counts[b.tag] - counts[a.tag] || a.tag.localeCompare(b.tag))
    .slice(0, limit)
    .map(x => x.tag);
}

// Which board section a task belongs in: 'overdue' (missed deadline),
// 'carried' (started on an earlier day, still open), an exact YYYY-MM-DD for
// today..+6, 'later', or 'unplanned'. A start date in the past is not
// overdue (the task is available), but it is not today's plan either: the
// board shows it apart, so a plan that keeps slipping is visible.
function bucketKey(t, today) {
  if (isOverdue(t, today)) return 'overdue';
  if (t.start_date && t.start_date < today) return 'carried';
  if (isFocus(t, today)) return today;
  if (!t.start_date) return 'unplanned';
  return t.start_date > addDays(today, 6) ? 'later' : t.start_date;
}

function fullDayLabel(d) {
  return new Date(d + 'T00:00:00').toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' });
}

// Group Plan-tab tasks into ordered day sections for the board. Date sections
// (today..+6), Later, and Unplanned are droppable (dropping sets or clears the
// start date); Overdue and Carried over are display-only. Dropping on Later opens a date picker
// in the UI layer.
function boardBuckets(list, today) {
  const groups = {};
  // Reversed first, so tasks created in the same second (created_at has no
  // finer resolution) keep newest-first too, as on Open.
  [...list].reverse().forEach(t => {
    const key = bucketKey(t, today);
    (groups[key] = groups[key] || []).push(t);
  });
  // Overdue: the most overdue first, then by title.
  const byDate = (a, b) => (a.due_date || '9999').localeCompare(b.due_date || '9999') || (a.title || '').localeCompare(b.title || '');
  // Days and Later are in Open's order (byOpenOrder). The board decides
  // which day a task is on, never where in the day: that is Open's order,
  // and on the day itself Focus's.
  const sections = [];
  if (groups['overdue']) sections.push({ key: 'overdue', label: 'Overdue', date: null, droppable: false, tasks: groups['overdue'].sort(byDate) });
  // Carried over: the longest-slipping first, then Open's order. Display-only
  // like Overdue (its tasks are in Focus already); dropping one on Today
  // commits to it again.
  const byStart = (a, b) => a.start_date.localeCompare(b.start_date) || byOpenOrder(a, b);
  if (groups['carried']) sections.push({ key: 'carried', label: 'Carried over', date: null, droppable: false, tasks: groups['carried'].sort(byStart) });
  // Each day also names the tasks due on it, wherever they sit (`due`): the
  // board places cards by start date, so a deadline is otherwise invisible
  // until its day.
  for (let i = 0; i < 7; i++) {
    const d = addDays(today, i);
    const label = i === 0 ? 'Today' : i === 1 ? 'Tomorrow' : fullDayLabel(d);
    const due = list.filter(t => t.due_date === d).sort(byOpenOrder);
    sections.push({ key: d, label, date: d, droppable: true, tasks: (groups[d] || []).sort(byOpenOrder), due });
  }
  // Later and Unplanned are always present, even when empty: they are the
  // only drop targets for "start beyond this week" and "unplan", so hiding
  // them when empty would strand every task on a dated day.
  sections.push({ key: 'later', label: 'Later', date: null, droppable: true, tasks: (groups['later'] || []).sort(byOpenOrder) });
  // Unplanned: nearest deadline first (they need a start date soonest), then
  // the rest in Open's order.
  const byDeadline = (a, b) => (a.due_date && b.due_date) ? a.due_date.localeCompare(b.due_date)
    : a.due_date ? -1 : b.due_date ? 1 : byOpenOrder(a, b);
  // Unplanned goes first: it is where planning starts (cards are dragged
  // from it onto the days), so it sits next to them, not past Later.
  sections.unshift({ key: 'unplanned', label: 'Unplanned', date: null, droppable: true, tasks: (groups['unplanned'] || []).sort(byDeadline) });
  return sections;
}

// ── Quick-add shorthand ────────────────────────────────────────────────────
// A new task's title may end in shorthand that fills its fields:
//   Call Bob tomorrow #work due:fri
// Only trailing words are read (a date word inside a sentence stays text),
// and the edit modal previews what was recognised before saving.
//   start:  today · tomorrow/tmrw · mon…sun (the next one, never today) ·
//           +3 / +3d / +2w · 2026-10-03
//   due:    due:<any of the above>
//   tags:   #tag (starting with a letter, so "Fix #3" keeps its number)
const WEEKDAYS = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];

function parseDateWord(word, today) {
  const w = word.toLowerCase();
  if (w === 'today') return today;
  if (w === 'tomorrow' || w === 'tmrw') return addDays(today, 1);
  let m = w.match(/^\+(\d{1,3})([dw]?)$/);
  if (m) return addDays(today, Number(m[1]) * (m[2] === 'w' ? 7 : 1));
  m = w.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (m) {
    const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
    return dateToStr(d) === w ? w : null;   // rejects 2026-02-31
  }
  if (w.length >= 3) {
    const wd = WEEKDAYS.findIndex(name => name.startsWith(w));
    if (wd >= 0) {
      const cur = new Date(today + 'T00:00:00').getDay();
      return addDays(today, ((wd - cur + 6) % 7) + 1);   // 1..7 days ahead
    }
  }
  return null;
}

// Returns { title, start_date, due_date, tags, found }. Unrecognised input
// (or shorthand that would leave no title) comes back unchanged, found=false.
function parseQuickAdd(input, today) {
  const words = (input || '').trim().split(/\s+/).filter(Boolean);
  const out = { title: words.join(' '), start_date: null, due_date: null, tags: [], found: false };
  let end = words.length;
  while (end > 1) {
    const w = words[end - 1];
    if (/^#\p{L}[\p{L}\p{N}_-]*$/u.test(w)) {
      const tag = w.slice(1).toLowerCase();
      if (!out.tags.includes(tag)) out.tags.unshift(tag);
    } else if (/^due:/i.test(w) && !out.due_date && parseDateWord(w.slice(4), today)) {
      out.due_date = parseDateWord(w.slice(4), today);
    } else if (!out.start_date && parseDateWord(w, today)) {
      out.start_date = parseDateWord(w, today);
    } else {
      break;
    }
    end--;
  }
  if (end === words.length) return out;
  out.title = words.slice(0, end).join(' ');
  out.found = true;
  return out;
}

const DeckCore = {
  dateToStr, addDays, isOverdue, isFocus, byOpenOrder, tagMatchScore, tagCounts, selectionTags, bulkTagChange,
  rankTags, sanitizeTagInput,
  parseFilter, matchesFilter, tagParamToQuery,
  bucketKey, fullDayLabel, boardBuckets, parseQuickAdd,
};

export default DeckCore;
