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
// against, so the title filter leaves them out.
export function queryWords(raw) {
  return [...raw.matchAll(/(-?)(?:"([^"]+)"|(\S+))/g)]
    .filter(m => !m[1] && !(m[3] && m[3].startsWith('#') && m[3].replace(/^#+|#+$/g, '') !== '')
                 && !(m[3] && /^(edited|after|before):\d{4}(-\d\d(-\d\d)?)?$/i.test(m[3])))
    .map(m => m[2] ?? m[3]).join(' ').trim();
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
// in rendered order: mdtext.TaskItemLines (and its taskItemRE). Only list
// items whose text starts with the box produce a checkbox; fenced code,
// display math and (in a page, `frontmatter`) a leading frontmatter block
// are skipped. So the Nth rendered checkbox is the Nth offset here.
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

// The text with its Nth checkbox ticked or unticked, recording when as the
// server does (mdtext.ToggleNthCheckbox): ticking adds
// <!--d:YYYY-MM-DDTHH:MM:SS--> (or the d: of an item's <!--t:…-->), shown
// as a date chip; unticking takes it out. `now`: the stamp (localStamp).
const TASK_META_RE = /<!--t:(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\s+d:\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)?-->/;
const TASK_DONE_RE = /<!--d:\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d-->/;
export function toggleCheckboxText(text, idx, frontmatter = true, now = localStamp()) {
  const at = taskCheckboxOffsets(text, frontmatter)[idx];
  if (at === undefined) return text;
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
