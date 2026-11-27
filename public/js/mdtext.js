// ── Text functions the browser shares with the server ───────────────────────
// Each one here does in the browser what a function of the server does (named
// at each): a slug, a heading's id, a wikilink's parts, which lines are
// checkboxes, a tick. No DOM and no imports, so Node runs them:
// tests/test_text_parity.py holds each against the server's answers on the
// same texts (go/testdata/golden.json.gz, which go/golden_test.go holds the
// Go side to). Change one side and that test fails until the other follows.

// ── Slugs, headings, wikilinks ───────────────────────────────────────────────

// mdtext.ToSlug: each part between "/" lowercase, runs of anything but
// [a-z0-9_] a hyphen.
export function toSlug(name) {
  return name.trim().split('/')
    .map(p => p.trim().toLowerCase().replace(/[^a-z0-9_]+/g, '-').replace(/^-+|-+$/g, ''))
    .filter(Boolean)
    .join('/');
}

// cleanList (go/tasksyntax.go): a task list's name as stored and matched:
// trimmed, lowercase, one word (spaces a hyphen), no leading "#"; '' for no
// list. One copy for the task app and its settings dialog; the server's is
// held to the same answers (tests/test_text_parity.py).
export function cleanList(name) {
  return String(name ?? '').trim().replace(/^#+/, '').trim().toLowerCase()
    .replace(/[\s\v\p{Z}\u0085]+/gu, '-').replace(/^-+|-+$/g, '');
}

// mdtext.HeadingID, a heading's id as rendered: letters, digits, _ and -
// kept, lowercase, spaces → hyphens.
export const headingId = text => text.replace(/[^\p{L}\p{N}_\- ]+/gu, '').trim().toLowerCase().replace(/ /g, '-');

// mdtext.SplitWikilink: a wikilink's inside as { page, section, label }:
// `Page`, `Page|Label`, `Page#Section` (that heading), `#Section` (a heading
// on this page). The label is the page, "Page › Section" or the section
// alone, unless given.
export function splitWikilink(inner) {
  const bar = inner.indexOf('|');
  const target = bar < 0 ? inner : inner.slice(0, bar);
  const hash = target.indexOf('#');
  const page = (hash < 0 ? target : target.slice(0, hash)).trim();
  const section = hash < 0 ? '' : target.slice(hash + 1).trim();
  const label = bar >= 0 ? inner.slice(bar + 1).trim()
    : page && section ? `${page} › ${section}` : section || page;
  return { page, section, label };
}

// A wikilink's inside → { href, label }, as processWikilinks (go/render.go)
// links it. The section's anchor is its text's id: the server first takes
// inline markdown out of it (mdtext.SectionAnchor), which a link written
// here seldom has.
export function wikilinkHref(inner) {
  const { page, section, label } = splitWikilink(inner);
  const anchor = section ? '#' + headingId(section) : '';
  return { href: page ? `/page/${toSlug(page)}${anchor}` : anchor || '#', label };
}

// ── Search ───────────────────────────────────────────────────────────────────

// The words of a search query, textsearch.ParseQuery's Text: phrases lose
// their quotes; -word, -"phrase", #tag, -#tag and the date filters (edited:,
// after:, before:) are filters on content, which a title can't be checked
// against, so the title filter leaves them out. Of the query's first 500
// characters, as the server takes it (cutQuery).
export function queryWords(raw) {
  return [...cutQuery(raw).matchAll(/(-?)(?:"([^"]+)"|(\S+))/g)]
    .filter(m => !m[1] && !(m[3] && m[3].startsWith('#') && m[3].replace(/^#+|#+$/g, '') !== '')
                 && !(m[3] && /^(edited|after|before):\d{4}(-\d\d(-\d\d)?)?$/i.test(m[3]))
                 && !(m[3] && whereFilter(m[3])))
    .map(m => m[2] ?? m[3]).join(' ').trim();
}

// The is: and in: filters (kindFilterRE, placeFilterRE in
// go/internal/textsearch): is:page, is:task, is:note, and a task's
// is:open, is:done; in:everyone, in:private or a group's name.
const KIND_FILTER_RE = /^is:(page|task|note|open|done)$/i;
const STATUSES = ['open', 'done'];
const PLACE_FILTER_RE = /^in:([a-z0-9][a-z0-9-]{0,39})$/i;

// whereFilter: the filter a part of a query is: op 'is' (a kind), 'status'
// (is:open, is:done) or 'in' (a place), and its value; null for none.
function whereFilter(part) {
  const m = part.match(KIND_FILTER_RE) || part.match(PLACE_FILTER_RE);
  if (!m) return null;
  const value = m[1].toLowerCase(), op = part.slice(0, 2).toLowerCase();
  return { op: op === 'is' && STATUSES.includes(value) ? 'status' : op, value };
}

// queryWhere: the kinds, places and tasks' statuses a query keeps and
// leaves out (is:, in:, -is:, -in:), as textsearch's ParseQuery reads them.
export function queryWhere(raw) {
  const w = { kinds: [], exKinds: [], places: [], exPlaces: [], statuses: [], exStatuses: [] };
  const lists = { is: ['kinds', 'exKinds'], in: ['places', 'exPlaces'], status: ['statuses', 'exStatuses'] };
  for (const m of cutQuery(raw).matchAll(/(-?)(?:"([^"]+)"|(\S+))/g)) {
    const f = m[3] && whereFilter(m[3]);
    if (!f) continue;
    const list = w[lists[f.op][m[1] ? 1 : 0]];
    if (!list.includes(f.value)) list.push(f.value);
  }
  return w;
}

// acceptsWhere: a thing of the kind ('page', 'task', 'note') kept in the
// place ('everyone', 'private', a group's name), a task of the status
// ('open', 'done'; '' for a page), passes w (queryWhere), as
// Query.AcceptsWhere says.
export function acceptsWhere(w, kind, place, status = '') {
  return (!w.kinds.length || w.kinds.includes(kind)) && !w.exKinds.includes(kind)
    && (!w.places.length || w.places.includes(place)) && !(place && w.exPlaces.includes(place))
    && (!w.statuses.length || w.statuses.includes(status)) && !(status && w.exStatuses.includes(status));
}

// withFilter: raw with its is: kinds (op 'is') or in: places (op 'in')
// replaced by op:value, at the end; value '' only drops them. A task's
// status (is:open) stays. The search modal's
// chips and Where menu write the query this way.
export function withFilter(raw, op, value) {
  const kept = [...raw.matchAll(/-?(?:"[^"]+"|\S+)/g)].map(m => m[0])
    .filter(part => { const f = whereFilter(part); return !f || f.op !== op; });
  if (value) kept.push(`${op}:${value}`);
  return kept.join(' ');
}

// textsearch.MaxQueryChars: how long a query is; a longer one is cut there,
// at its last space before that when it has one (textsearch's cutQuery).
const MAX_QUERY_CHARS = 500;

function cutQuery(raw) {
  const chars = [...raw];
  if (chars.length <= MAX_QUERY_CHARS) return raw;
  const cut = chars.slice(0, MAX_QUERY_CHARS);
  for (let j = cut.length - 1; j > 0; j--) {
    if (/\p{White_Space}/u.test(cut[j])) return cut.slice(0, j).join('');
  }
  return cut.join('');
}

// ── Display math and code fences ─────────────────────────────────────────────

// A $$ line as go/math.go reads it: one that opens display math (only $$,
// up to three spaces before it), or one that is display math by itself
// ($$tex$$). The math runs to a line ending in $$ (mathFenceEnd).
export function mathFence(line) {
  if (/^( {4}| {0,3}\t)/.test(line)) return { opens: false, whole: false };
  const t = line.trim();
  return { opens: t === '$$', whole: t.length > 4 && t.startsWith('$$') && t.endsWith('$$') };
}
export const mathFenceEnd = line => line.trimEnd().endsWith('$$');

// A line (trimmed) that opens a code fence: a backtick after a ``` is inline
// code (```x``` y), no fence, as mdtext.FencedLines reads it.
export function opensCodeFence(t) {
  return t.startsWith('~~~') || (t.startsWith('```') && !t.replace(/^`+/, '').includes('`'));
}

// ── Checkboxes ───────────────────────────────────────────────────────────────

// Character offsets of the mark inside each task-list checkbox ("[ ]"/"[x]"),
// counted by lines: only list items whose text starts with the box; fenced
// code, display math and (in a page, `frontmatter`) a leading frontmatter
// block are skipped. A card's count of subtasks is by it (js/taskutil.js).
// What is ticked goes by the boxes the server's parser finds
// (go/checkboxes.go), which also leaves out a line in a comment, raw HTML,
// indented code or a fence in a quote: the previews have their marks.
const TASK_ITEM_RE = /^([ \t]*(?:>[ \t]?)*[ \t]*(?:[-*+]|\d{1,9}[.)])[ \t]+\[)([ xX])\](?=[ \t]+\S)/;

export function taskCheckboxOffsets(text, frontmatter = true) {
  const lines = text.split('\n');
  let start = 0;
  if (frontmatter && lines.length && lines[0].trim() === '---') {
    const close = lines.findIndex((l, i) => i > 0 && l.trim() === '---');
    if (close !== -1) start = close + 1;
  }
  const offsets = [];
  let pos = 0, fence = null;
  lines.forEach((line, i) => {
    const t = line.trim();
    const run = t.length - t.replace(/^(`+|~+)/, '').length;  // leading fence chars
    const math = fence ? {} : mathFence(line);
    if (fence === '$$') {
      if (mathFenceEnd(line)) fence = null;
    } else if (fence) {
      // Closing fence: same char, at least as long, nothing else on the line
      if (t[0] === fence[0] && run >= fence.length && run === t.length) fence = null;
    } else if (opensCodeFence(t)) {
      fence = t.slice(0, run);
    } else if (math.opens) {
      fence = '$$';
    } else if (math.whole) {
      // display math by itself: no checkbox
    } else if (i >= start) {
      const m = TASK_ITEM_RE.exec(line);
      if (m) offsets.push(pos + m[1].length);
    }
    pos += line.length + 1;
  });
  return offsets;
}

// Now as a tick's stamp: YYYY-MM-DDTHH:MM:SS in local time, as the clock
// shows it.
export function localStamp(d = new Date()) {
  const two = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())}T${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`;
}

// The text with the checkbox whose mark is at `at` ticked or unticked,
// recording when as the server does (mdtext.ToggleCheckbox): ticking adds
// <!--d:YYYY-MM-DDTHH:MM:SS--> (or the d: of an item's <!--t:…-->), shown
// as a date chip; unticking takes it out. `now`: the stamp (localStamp).
const TASK_META_RE = /<!--t:(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\s+d:\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)?-->/;
const TASK_DONE_RE = /<!--d:\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d-->/;
export function toggleCheckboxText(text, at, now = localStamp()) {
  if (at === undefined || (text[at] !== ' ' && text[at] !== 'x' && text[at] !== 'X')) return text;
  const checking = text[at] === ' ';
  const start = text.lastIndexOf('\n', at) + 1;
  let end = text.indexOf('\n', at);
  if (end === -1) end = text.length;
  let line = text.slice(start, at) + (checking ? 'x' : ' ') + text.slice(at + 1, end);
  const meta = line.match(TASK_META_RE);
  if (checking) {
    if (meta) line = line.replace(TASK_META_RE, `<!--t:${meta[1]} d:${now}-->`);
    else if (TASK_DONE_RE.test(line)) line = line.replace(TASK_DONE_RE, `<!--d:${now}-->`);
    else line = `${line.trimEnd()} <!--d:${now}-->`;
  } else if (meta) {
    line = line.replace(TASK_META_RE, `<!--t:${meta[1]}-->`);
  } else {
    line = line.replace(TASK_DONE_RE, '').trimEnd();
  }
  return text.slice(0, start) + line + text.slice(end);
}
