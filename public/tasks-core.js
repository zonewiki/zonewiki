// ── Tasks core — pure task logic, no DOM ──────────────────────────────────────
// An ES module imported by tasks.js and the editor (Make task's quick-add
// parser), and by the Node tests (tests/test_tasks_core.py), which is why
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
// exactly these — on the board: Today (carried-over tasks included), and
// overdue tasks wherever their start date puts them.
function isFocus(t, today) {
  return (!!t.start_date && t.start_date <= today) || (!!t.due_date && t.due_date <= today);
}

// Open's order, also Focus's (Open filtered) and the order inside every
// Plan-board section: Unsorted (no sort_order) first, newest
// first, then Sorted in sort_order. Every view that reorders edits this one
// order (placeInOrder).
function byOpenOrder(a, b) {
  if (a.sort_order == null && b.sort_order == null) return (b.created_at || '').localeCompare(a.created_at || '');
  if (a.sort_order == null) return -1;
  if (b.sort_order == null) return 1;
  return a.sort_order - b.sort_order;
}

// Nearest deadline first, then Open's order; tasks without one last, in
// Open's order. The Due sort of Open and Plan, and Unplanned's overdue list.
function byDeadline(a, b) {
  if (a.due_date && b.due_date) return a.due_date.localeCompare(b.due_date) || byOpenOrder(a, b);
  return a.due_date ? -1 : b.due_date ? 1 : byOpenOrder(a, b);
}

// Newest first: the Created sort of Open and Plan. Tasks created in the
// same second (created_at has no finer resolution) keep the order they
// come in, so callers reverse the list first, as for Unsorted.
function byCreated(a, b) {
  return (b.created_at || '').localeCompare(a.created_at || '');
}

// The sorts Open and Plan can switch to instead of the manual order.
const SORTS = { due: byDeadline, created: byCreated };

// The manual order `old` (ids, by sort_order) after the tasks `moved` were
// placed among the visible sorted tasks `shown`, in the order shown. Every
// other task keeps its place, hidden ones too: on Focus most of the order is
// hidden (a shopping list shows today's items of the shop's order). A moved
// task goes right after the visible task now above it, or right before the
// one below it when it is first. `moved` may name tasks not yet in `old`.
function placeInOrder(old, shown, moved) {
  const movedSet = new Set(moved);
  const order = old.filter(id => !movedSet.has(id));
  shown.forEach((id, i) => {
    if (!movedSet.has(id)) return;
    if (i > 0) order.splice(order.indexOf(shown[i - 1]) + 1, 0, id);
    else {
      const below = shown.slice(1).find(b => !movedSet.has(b));
      order.splice(below ? order.indexOf(below) : 0, 0, id);
    }
  });
  return order;
}

// The tasks' single filter field: words starting with '#' filter by tag, the
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

// Carried over: started on an earlier day and still open. Such a task is
// on Today (it is in Focus, today's load), its card saying since when, and
// Today's header counts them (`carried`), so a plan that keeps slipping
// stays visible.
function isCarried(t, today) {
  return !!t.start_date && t.start_date < today && !t.completed_at;
}

// Which board section a task belongs in: an exact YYYY-MM-DD for
// today..+6 (a start in the past: today), 'later', or 'unplanned'. The
// start date decides, overdue or not (a missed deadline shows on the card,
// and Unplanned's header counts its overdue tasks); only a deadline today
// puts an unplanned task on Today.
function bucketKey(t, today) {
  if (t.start_date && t.start_date < today) return today;
  if (!t.start_date) return t.due_date === today ? today : 'unplanned';
  return t.start_date > addDays(today, 6) ? 'later' : t.start_date;
}

// ── Locale ─────────────────────────────────────────────────────────────────
// Dates read the way the locale setting says (<body data-locale>, a BCP 47 tag like
// de-AT); without one, US English with weeks from Monday, as before the
// setting. date-format = iso (<body data-date-format>) writes the dates
// themselves as YYYY-MM-DD; weekday and month names still follow the locale.
// Only dates: the UI's words stay English. `dates` below is dateSettings().

const DEFAULT_LOCALE = 'en-US';

// { locale, iso } from <body>'s dataset (or any object with those keys).
function dateSettings(data) {
  return { locale: (data && data.locale) || '', iso: !!data && data.dateFormat === 'iso' };
}

// A date in `locale` (default US English); `opts` as toLocaleDateString's.
function localeDate(d, locale, opts) {
  return new Date(d + 'T00:00:00').toLocaleDateString(locale || DEFAULT_LOCALE, opts);
}

// "Sep 21" / "21. Sept." (plus the year when it isn't today's year), or
// "2026-09-21" in ISO, for "since …" labels, history and task chips.
function shortDate(d, today, dates = {}) {
  if (dates.iso) return d.slice(0, 10);
  const opts = { month: 'short', day: 'numeric' };
  if (d.slice(0, 4) !== today.slice(0, 4)) opts.year = 'numeric';
  return localeDate(d, dates.locale, opts);
}

// The Plan board's days: "Wed 21", or "Wed 2026-10-21" in ISO.
function fullDayLabel(d, dates = {}) {
  if (dates.iso) return `${localeDate(d, dates.locale, { weekday: 'short' })} ${d}`;
  return localeDate(d, dates.locale, { weekday: 'short', day: 'numeric' });
}

// "Mon, Oct 5", or "Mon 2026-10-05" in ISO: the date picker's tooltips.
function longDayLabel(d, dates = {}) {
  if (dates.iso) return fullDayLabel(d, dates);
  return localeDate(d, dates.locale, { weekday: 'short', month: 'short', day: 'numeric' });
}

// The day a week starts on in `locale`, as getDay() counts (0 = Sunday,
// 1 = Monday): Intl's week info where the engine has it, else Monday.
function weekStart(locale) {
  if (!locale) return 1;
  try {
    const loc = new Intl.Locale(locale);
    const info = loc.getWeekInfo ? loc.getWeekInfo() : loc.weekInfo;
    if (info && info.firstDay) return info.firstDay % 7;
  } catch (e) { /* not a locale this engine knows: Monday */ }
  return 1;
}

// The calendar's column heads, from the week's first day: two letters each
// ("Mo", "Di"), a short form's trailing dot dropped.
function weekdayNames(locale, start) {
  // 2023-01-01 was a Sunday, so day i of that week has getDay() === i.
  return Array.from({ length: 7 }, (_, i) =>
    localeDate(addDays('2023-01-01', (start + i) % 7), locale, { weekday: 'short' })
      .replace(/\.$/, '').slice(0, 2));
}

// "October 2026", the calendar's title.
function monthTitle(year, month, locale) {
  return new Date(year, month, 1).toLocaleDateString(locale || DEFAULT_LOCALE, { month: 'long', year: 'numeric' });
}

// Group Plan-tab tasks into ordered day sections for the board. Date sections
// (today..+6), Later, and Unplanned are droppable (dropping sets or clears the
// start date). Dropping on Later opens a date picker in the UI layer.
// Unplanned also lists its overdue tasks (`overdue`) and Today its
// carried-over ones (`carried`), the longest-slipping first, for their
// headers. `sort` ('due' or
// 'created', see SORTS) sorts every section by it instead, and then none is
// `ordered`: the manual order is only edited where it is shown. Days past
// tomorrow are labelled as `dates` says (dateSettings).
function boardBuckets(list, today, sort, dates) {
  const by = SORTS[sort];
  const groups = {};
  // Reversed first, so tasks created in the same second (created_at has no
  // finer resolution) keep newest-first too, as on Open.
  [...list].reverse().forEach(t => {
    const key = bucketKey(t, today);
    (groups[key] = groups[key] || []).push(t);
  });
  // Days and Later are in Open's order (byOpenOrder), and reordering them
  // (`ordered`) edits that order, as on Open.
  const sections = [];
  // Each day also names the tasks due on it, wherever they sit (`due`): the
  // board places cards by start date, so a deadline is otherwise invisible
  // until its day.
  for (let i = 0; i < 7; i++) {
    const d = addDays(today, i);
    const label = i === 0 ? 'Today' : i === 1 ? 'Tomorrow' : fullDayLabel(d, dates);
    const due = list.filter(t => t.due_date === d).sort(byOpenOrder);
    const tasks = (groups[d] || []).sort(by || byOpenOrder);
    const carried = i === 0
      ? tasks.filter(t => isCarried(t, today)).sort((a, b) => a.start_date.localeCompare(b.start_date) || byOpenOrder(a, b))
      : undefined;
    sections.push({ key: d, label, date: d, droppable: true, ordered: !by, tasks, due, carried });
  }
  // Later and Unplanned are always present, even when empty: they are the
  // only drop targets for "start beyond this week" and "unplan", so hiding
  // them when empty would strand every task on a dated day.
  sections.push({ key: 'later', label: 'Later', date: null, droppable: true, ordered: !by, tasks: (groups['later'] || []).sort(by || byOpenOrder) });
  // Unplanned: in Open's order like a day, its Unsorted tasks first (under
  // their own label on the board, as in every section), and reorderable:
  // it is the backlog, ranked while planning. Its overdue tasks are named in its
  // header (`overdue`), the most overdue first.
  // Unplanned goes first: it is where planning starts (cards are dragged
  // from it onto the days), so it sits next to them, not past Later.
  const unplanned = (groups['unplanned'] || []).sort(by || byOpenOrder);
  sections.unshift({ key: 'unplanned', label: 'Unplanned', date: null, droppable: true, ordered: !by, tasks: unplanned,
    overdue: unplanned.filter(t => isOverdue(t, today)).sort(byDeadline) });
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

const TasksCore = {
  dateToStr, addDays, isOverdue, isFocus, isCarried, byOpenOrder, byDeadline, byCreated, SORTS, placeInOrder, tagMatchScore, tagCounts, selectionTags, bulkTagChange,
  rankTags, sanitizeTagInput,
  parseFilter, matchesFilter, tagParamToQuery,
  bucketKey, fullDayLabel, boardBuckets, parseQuickAdd,
  dateSettings, localeDate, shortDate, longDayLabel, weekStart, weekdayNames, monthTitle,
};

export default TasksCore;
