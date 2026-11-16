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

// What a reorder tells the server of the manual order `order` in which the
// tasks `moved` changed place: for each moved task still in it, in the
// order's sequence, the task now right before it (after: null when it is
// first; that may be a moved task placed just before) and the next task
// after it that didn't move (before: null when none follows).
function orderMoves(order, moved) {
  const movedSet = new Set(moved);
  const out = [];
  order.forEach((id, i) => {
    if (!movedSet.has(id)) return;
    const next = order.slice(i + 1).find(o => !movedSet.has(o));
    out.push({ id, after: i > 0 ? order[i - 1] : null, before: next ?? null });
  });
  return out;
}

// The tasks' single filter field: words starting with '#' filter by tag, the
// rest is a text search over title + description. Separate tag words must
// all match (`#a #b`); a pipe inside one word means any of them (`#a|b`).
// A word starting with '-#' excludes its tags (`-#mary`, `-#a|b`: neither).
// `!high` (or `!`) and `!low` filter by importance.
// With users (`people`: { me, users }, the signed-in user and the Set of
// users a task can be assigned to; without it '@' is text), words starting
// with '@' filter by assignee the same way: `@anna`, `@anna|bob`, `-@anna`,
// `@me` (whoever is signed in) and `@none` (assigned to no one).
// `list:shop` filters by the list a task is in (by its name; `list:a|b`
// either, `-list:shop` not in it, `list:inbox` in no list: the Inbox;
// `list:none` says the same).
// Returns { text, tagGroups, excludeTags, importance, assigneeGroups,
// excludeAssignees, listGroups, excludeLists, people } — every group must
// have at least one match, and nothing excluded may be present.
function parseFilter(q, people) {
  const words = (q || '').trim().split(/\s+/).filter(Boolean);
  const tagGroups = [];
  const excludeTags = [];
  const assigneeGroups = [];
  const excludeAssignees = [];
  const listGroups = [];
  const excludeLists = [];
  const fromGroups = [];
  const excludeFrom = [];
  const text = [];
  // from:anna (with users): the tasks anna made; from:me the user's own.
  const fromOf = w => w.replace(/^from:/i, '').split('|').map(n => n.replace(/^@/, '').toLowerCase()).filter(Boolean);
  const listsOf = w => w.replace(/^list:/i, '').split('|').map(n => n.replace(/^shared\//i, '').toLowerCase()).filter(Boolean);
  const partsOf = (w, sigil) => w.split('|').map(t => (t.startsWith(sigil) ? t.slice(1) : t).toLowerCase()).filter(Boolean);
  const tagsOf = w => partsOf(w, '#');
  const users = !!people?.users;
  let importance = '';
  let updated = false;   // is:updated: the tasks with news for the user
  let following = false; // is:following: the tasks the user follows
  for (const w of words) {
    if (/^is:updated$/i.test(w)) updated = true;
    else if (/^is:following$/i.test(w)) following = true;
    else if (importanceWord(w)) importance = importanceWord(w);
    else if (/^-list:./i.test(w)) excludeLists.push(...listsOf(w.slice(1)));
    else if (/^list:./i.test(w)) listGroups.push(listsOf(w));
    else if (users && /^-from:./i.test(w)) excludeFrom.push(...fromOf(w.slice(1)));
    else if (users && /^from:./i.test(w)) fromGroups.push(fromOf(w));
    else if (w.startsWith('-#')) excludeTags.push(...tagsOf(w.slice(1)));
    else if (w.startsWith('#')) {
      const group = tagsOf(w);
      if (group.length) tagGroups.push(group);
    } else if (users && w.startsWith('-@')) excludeAssignees.push(...partsOf(w.slice(1), '@'));
    else if (users && w.startsWith('@')) {
      const group = partsOf(w, '@');
      if (group.length) assigneeGroups.push(group);
    } else {
      text.push(w);
    }
  }
  return { text: text.join(' ').toLowerCase(), tagGroups, excludeTags, importance, assigneeGroups, excludeAssignees, listGroups, excludeLists, fromGroups, excludeFrom, updated, following, people };
}

// Whether a filter (parseFilter) leaves anything out.
function filterActive(f) {
  return !!(f.text || f.tagGroups.length || f.excludeTags.length || f.importance ||
    f.assigneeGroups?.length || f.excludeAssignees?.length || f.listGroups?.length || f.excludeLists?.length ||
    f.fromGroups?.length || f.excludeFrom?.length || f.updated || f.following);
}

// The task's assignee no longer has it: someone who can no longer be given
// tasks (disabled, removed, a viewer now), or who is none of those its list
// is for (people.members: a list for some's members by the list's ref; a
// list narrowed since, a group they left): they don't see the task. The
// name stays on it, so when they are back it is theirs as before.
function assigneeGone(t, people) {
  const a = (t.assignee || '').toLowerCase();
  if (!a || !people?.users) return false;
  const members = people.members?.[t.list || ''];
  return !people.users.has(a) || !!members && !members.includes(a);
}

// Whether the task's assignee is what a filter word names: a user, `me`
// (the signed-in user) or `none`. A task whose assignee no longer has it
// (assigneeGone) counts as no one's too, so it comes up where the team
// looks for what nobody has; it still matches the name, so it can be found
// and handed on.
function assigneeIs(t, word, people) {
  const a = (t.assignee || '').toLowerCase();
  // A private task has no assignee and is its user's own: theirs, not no one's.
  if (word === 'none') return !t.private && (!a || assigneeGone(t, people));
  if (word === 'me') return !!people?.me && (a === people.me || !!t.private);
  return a === word;
}

// My tasks, the home view the Tasks links open and count (mineFilter in
// go/tasklists.go; go/testdata/mine_cases.json runs through both): what is
// the user's. A task of their own (a private one: in their Inbox or a
// private list) unless its list is left out of My tasks, and a shared task
// assigned to them, wherever it is; an unassigned shared task is its
// list's. out: the task's list is left out (its setting home=off); who:
// { me, assigns (assignment counts for this user: not without users, nor
// for a viewer, where every list is the user's own) }.
function inMine(t, out, who) {
  if (who?.assigns && !t.private) return (t.assignee || '').toLowerCase() === who.me;
  return !out;
}

// What a shared task has for the user that they haven't seen (newsFor in
// go/tasklogic.go, which counts such tasks on the Tasks links;
// go/testdata/news_cases.json runs through both): { kind, by, at }, or
// null. The user has to do with a task they are assigned, that they made
// (created_by) or that they follow (followers: who follows is told what
// who made it is told); a mention reaches them whatever the task. What counts is
// someone else's and after the last thing the user did on the task
// (anything of theirs in its history, "seen" among it, which the app logs
// when they open the task: markSeen in js/taskdata.js; or a comment of
// theirs): it was given to them ('given': its card says "new"), a comment
// ('comment'), a mention of them ('mention'), it was closed or reopened
// ('closed', 'reopened'), and for who made it or follows it that it was
// given to someone else ('handed'). Not a date, a tag, the title or the order; no time takes
// it away. The latest is the news. History entries are in order, comments
// are, and between the two the minute decides: within one minute a comment
// of the user's own, or their look at the task ("seen"), counts as the
// later one. A look says which comment was the last one then (its `to`: a
// comment's id, '-' for none), and every comment after that one is news,
// whatever the minute; a look that doesn't say goes by the minute.
function newsFor(t, me) {
  if (!me || !t || t.private) return null;
  const minute = at => String(at || '').slice(0, 16);
  const history = t.history || [], comments = t.comments || [];
  const open = !t.closed_at;
  const has = (t.assignee || '').toLowerCase() === me;
  const made = (t.created_by || (history.find(e => e.event === 'created') || {}).by || '') === me ||
    (t.followers || []).includes(me);
  let mine = -1, myAct = '', sawIt = false, sawComment = '', lastAssigned = -1;
  history.forEach((e, i) => {
    if (e.by === me) { mine = i; myAct = minute(e.at); sawIt = e.event === 'seen'; sawComment = sawIt ? e.to || '' : ''; }
    if (e.event === 'assigned') lastAssigned = i;
  });
  let myComment = -1, said = '';
  comments.forEach((c, i) => { if (c.by === me) { myComment = i; said = minute(c.at); } });
  let news = null;
  const take = (kind, by, at) => { if (!news || at >= news.at) news = { kind, by, at }; };
  for (let i = mine + 1; i < history.length; i++) {
    const e = history[i], at = minute(e.at);
    if (!e.by || e.by === me || (said && at <= said)) continue;
    const to = (e.to || '').toLowerCase();
    if (e.event === 'assigned') {
      if (has && to === me && i === lastAssigned && open) take('given', e.by, at);
      else if (made && to && to !== me) take('handed', e.by, at);
    } else if (e.event === 'created') {
      if (has && lastAssigned < 0 && open) take('given', e.by, at);
    } else if (e.event === 'done' || e.event === 'wontdo') {
      if ((has || made) && !open) take('closed', e.by, at);
    } else if (e.event === 'reopened') {
      if ((has || made) && open) take('reopened', e.by, at);
    } else if (e.event === 'mentioned') {
      if (to === me) take('mention', e.by, at);
    }
  }
  if (has || made) {
    let from = myComment + 1, after = sawComment === '-';
    const saw = sawComment ? comments.findIndex(c => c.id && c.id === sawComment) : -1;
    if (saw >= 0) { from = Math.max(from, saw + 1); after = true; }
    for (const c of comments.slice(from)) {
      const at = minute(c.at);
      if (c.by && c.by !== me && (after || !myAct || at > myAct || (at === myAct && !sawIt))) take('comment', c.by, at);
    }
  }
  return news;
}

// A task someone else gave the user that they haven't opened: the news its
// card calls "new". Returns who gave it and when ({ at, by }), or null.
function givenToMe(t, me) {
  const news = newsFor(t, me);
  return news && news.kind === 'given' ? { at: news.at, by: news.by } : null;
}

// A task's importance: 'high', 'low', or '' for normal (what most are).
// As a word, in the filter and at the end of a new task's title: `!` or
// `!high`, `!low`.
function importanceWord(w) {
  const m = /^!(high|low)?$/i.exec(w);
  return m ? (m[1] || 'high').toLowerCase() : '';
}

function matchesFilter(t, f) {
  if (f.importance && (t.importance || '') !== f.importance) return false;
  if (f.updated && !newsFor(t, f.people?.me)) return false;
  if (f.following && !(f.people?.me && (t.followers || []).includes(f.people.me))) return false;
  if (f.text && !(t.title || '').toLowerCase().includes(f.text) &&
      !(t.description || '').toLowerCase().includes(f.text)) return false;
  const tags = (t.tags || []).map(tg => tg.toLowerCase());
  if ((f.excludeTags || []).some(tag => tags.includes(tag))) return false;
  const is = w => assigneeIs(t, w, f.people);
  if ((f.excludeAssignees || []).some(is)) return false;
  if (!(f.assigneeGroups || []).every(g => g.some(is))) return false;
  // Who made it (created_by): a private task is its user's own making.
  const maker = (t.created_by || (t.private ? f.people?.me : '') || '').toLowerCase();
  const from = n => (n === 'me' ? f.people?.me || '' : n) === maker && !!maker;
  if ((f.excludeFrom || []).some(from)) return false;
  if (!(f.fromGroups || []).every(g => g.some(from))) return false;
  const inList = n => n === 'inbox' || n === 'none' ? !t.list : (t.list || '').toLowerCase() === n;
  if ((f.excludeLists || []).some(inList)) return false;
  if (!(f.listGroups || []).every(g => g.some(inList))) return false;
  return f.tagGroups.every(g => g.some(tag => tags.includes(tag)));
}

// The tasks a tab shows, of `tasks`: those passing the filter f (parseFilter)
// that are the tab's kind. Focus: open and available now (isFocus);
// Closed: the closed (done or won't do); Open and the Plan board: every open task (one
// still waiting for a day is in the board's Unplanned section).
// me: in My tasks, the user: Focus then also holds every task with news
// for them (newsFor), whatever its dates: its Updates. Among them the ones
// My tasks holds for that alone (t.extra: what the user gave away, a closed
// one), which no other tab shows.
function tasksInView(tasks, f, view, today, me) {
  const filtering = filterActive(f);
  return tasks.filter(t => (!filtering || matchesFilter(t, f)) &&
    (view === 'closed' ? !!t.closed_at
      : view === 'focus' ? (!t.closed_at && !t.extra && isFocus(t, today)) || !!(me && newsFor(t, me))
      : !t.closed_at && !t.extra));
}

// The [[tasks: tag=…]] / ?tag= form ("a,b" = all, "a|b" = any) as filter words.
// sigil '@': the assignee= / ?assignee= form the same way (me, none, names).
function tagParamToQuery(raw, sigil = '#') {
  raw = (raw || '').trim();
  if (sigil === '@') raw = raw.toLowerCase().replace(/@/g, '');
  if (!raw) return '';
  if (raw.includes('|')) return sigil + raw.split('|').map(s => s.trim()).filter(Boolean).join('|');
  // A leading '-' excludes the tag (`tag=work,-mary`).
  return raw.split(',').map(s => s.trim()).filter(Boolean)
    .map(t => t.startsWith('-') ? '-' + sigil + t.slice(1) : sigil + t).join(' ');
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
  return !!t.start_date && t.start_date < today && !t.closed_at;
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

// The Open tab By person (with users): a section per assignee, as the board
// has one per day, and a drop on one assigns. Unassigned first (what no one
// has, a former assignee's included: assigneeIs none), then the user's own
// (the tasks assigned to them and their private ones), then everyone else
// who has tasks here, A–Z. Unassigned and the user's own are always there:
// they are where a task is let go and taken. Each names its overdue tasks
// (`overdue`), the most overdue first. `sort` as for boardBuckets.
// people: { me, users (a Set of who can be assigned) }.
function personBuckets(list, today, sort, people) {
  const by = SORTS[sort];
  const me = people?.me || '';
  const groups = new Map([['', []], [me, []]]);
  [...list].reverse().forEach(t => {
    const name = assigneeIs(t, 'none', people) ? '' : t.private ? me : (t.assignee || '').toLowerCase();
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(t);
  });
  const names = [...groups.keys()].filter(n => n && n !== me).sort((a, b) => a.localeCompare(b));
  return ['', ...(me ? [me] : []), ...names].map(name => {
    const tasks = groups.get(name).sort(by || byOpenOrder);
    return { key: 'p:' + name, person: name, label: !name ? 'Unassigned' : name === me ? 'You' : '@' + name,
      date: null, droppable: true, ordered: !by, tasks,
      overdue: tasks.filter(t => isOverdue(t, today)).sort(byDeadline) };
  });
}

// The Open tab By list (My tasks, All tasks): a section per list, and a drop
// on one moves the task there. The Inbox first, always (ref ''), then the
// lists that have tasks here in the order `lists` gives them ([{ ref,
// label }]: the user's own before the shared ones); listOf(t) is a task's
// list as the user names it, '' for the Inbox.
function listBuckets(list, today, sort, lists, listOf) {
  const by = SORTS[sort];
  const groups = new Map([['', []]]);
  [...list].reverse().forEach(t => {
    const ref = listOf(t) || '';
    if (!groups.has(ref)) groups.set(ref, []);
    groups.get(ref).push(t);
  });
  const known = lists.filter(l => l.ref && groups.has(l.ref));
  return [{ ref: '', label: 'Inbox' }, ...known].map(l => {
    const tasks = groups.get(l.ref).sort(by || byOpenOrder);
    return { key: 'l:' + l.ref, list: l.ref, label: l.label, date: null, droppable: true, ordered: !by, tasks,
      overdue: tasks.filter(t => isOverdue(t, today)).sort(byDeadline) };
  });
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

// The user a quick-add word assigns the task to: `@name` of a user who can
// be assigned, or `@me`; '' for any other word (bob@example.org, @home, a
// name that is no user), which stays in the title.
function quickAssignee(w, people) {
  const m = /^@([a-z0-9-]+)$/i.exec(w);
  if (!m || !people?.users) return '';
  const name = m[1].toLowerCase() === 'me' ? people.me : m[1].toLowerCase();
  return people.users.has(name) ? name : '';
}

// The shorthand at the end of a new task's title, word by word from its
// end: [{ field, value, index }], field one of 'tag', 'importance',
// 'assignee', 'due_date', 'start_date', index the word's place among the
// title's words. It ends at the first word that is none (or a second of a
// kind that is taken once), and never takes the first word: a title is
// never only shorthand.
function quickWords(words, today, people) {
  const found = [], has = {};
  for (let i = words.length - 1; i > 0; i--) {
    const w = words[i];
    let field = '', value = '';
    if (/^#\p{L}[\p{L}\p{N}_-]*$/u.test(w)) {
      field = 'tag'; value = w.slice(1).toLowerCase();
    } else if (!has.importance && importanceWord(w)) {
      field = 'importance'; value = importanceWord(w);
    } else if (!has.assignee && quickAssignee(w, people)) {
      field = 'assignee'; value = quickAssignee(w, people);
    } else if (/^due:/i.test(w) && !has.due_date && parseDateWord(w.slice(4), today)) {
      field = 'due_date'; value = parseDateWord(w.slice(4), today);
    } else if (!has.start_date && parseDateWord(w, today)) {
      field = 'start_date'; value = parseDateWord(w, today);
    } else {
      break;
    }
    has[field] = true;
    found.push({ field, value, index: i });
  }
  return found;
}
const titleWords = input => (input || '').trim().split(/\s+/).filter(Boolean);

// Returns { title, start_date, due_date, tags, importance, assignee, found }.
// Unrecognised input (or shorthand that would leave no title) comes back
// unchanged, found=false. `people` ({ me, users }, with users): `@name`
// assigns the task (quickAssignee); one name only, a second stays text.
function parseQuickAdd(input, today, people) {
  const words = titleWords(input);
  const out = { title: words.join(' '), start_date: null, due_date: null, tags: [], importance: '', assignee: '', found: false };
  const found = quickWords(words, today, people);
  if (!found.length) return out;
  for (const { field, value } of found) {
    if (field !== 'tag') out[field] = value;
    else if (!out.tags.includes(value)) out.tags.unshift(value);
  }
  out.title = words.slice(0, words.length - found.length).join(' ');
  out.found = true;
  return out;
}

// The title without the shorthand word that gives `field` (for 'tag': the
// tag `value`, each time it is there): what a field changed by hand leaves
// of a new task's title, so the title and the field never say two things.
// A title without such a word comes back as it is.
function dropQuickWord(input, today, people, field, value) {
  const words = titleWords(input);
  const drop = new Set(quickWords(words, today, people)
    .filter(f => f.field === field && (field !== 'tag' || f.value === value)).map(f => f.index));
  return drop.size ? words.filter((_, i) => !drop.has(i)).join(' ') : input;
}

const TasksCore = {
  dateToStr, addDays, isOverdue, isFocus, isCarried, byOpenOrder, byDeadline, byCreated, SORTS, placeInOrder, tagMatchScore, tagCounts, selectionTags, bulkTagChange,
  rankTags, sanitizeTagInput,
  parseFilter, matchesFilter, filterActive, assigneeIs, assigneeGone, inMine, newsFor, givenToMe, orderMoves, tasksInView, tagParamToQuery, importanceWord,
  bucketKey, fullDayLabel, boardBuckets, personBuckets, listBuckets, parseQuickAdd, dropQuickWord,
  dateSettings, localeDate, shortDate, longDayLabel, weekStart, weekdayNames, monthTitle,
};

export default TasksCore;
