// ── Task app helpers ─────────────────────────────────────────────────────────
// What tasks.js's task apps use that holds no state of an app's own: how
// dates read and are classed, notices and the toast's place, escaping, tag
// input helpers (the tag autocomplete, # words at the caret), comment
// stamps. An ES module tasks.js imports.

import TasksCore from '../tasks-core.js';
import { wikilinkHref } from './mdtext.js';

const { dateToStr, sanitizeTagInput } = TasksCore;

// How dates read and the week starts: the locale setting (empty: US
// English) and date-format = iso (YYYY-MM-DD), see tasks-core.js.
export const DATES = TasksCore.dateSettings(document.body.dataset);
export const LOCALE = DATES.locale;

// Start a task on `day` from the board (and the quick ways below). A day
// after its deadline asks to move the deadline along; cancelling changes
// nothing. Undo restores both.
// A start date after the deadline is usually a slip, so setting one asks
// whether the deadline moves too (confirmDeadlineMove), on the board and
// everywhere else a start date is set (setStart, the 📅 picker, f/F, 1–7). The
// exception: an overdue task started today, the normal way to catch up on a
// missed deadline.
export function startsAfterDeadline(task, day) {
  if (!day || !task?.due_date || day <= task.due_date) return false;
  const today = todayStr();
  return !(task.due_date < today && day === today);
}

// A toast goes into an open modal dialog if there is one: in <body> it
// would sit under the dialog (top layer) and its Undo couldn't be clicked.
// It moves back to <body> when the dialog closes.
export function placeToast(el) {
  const dialog = document.querySelector('dialog[open]');
  (dialog || document.body).appendChild(el);
  dialog?.addEventListener('close', () => {
    if (el.isConnected) document.body.appendChild(el);
  }, { once: true });
}

// Plain auto-dismissing toast (no undo button) — for server-rejected writes.
export function showNotice(msg) {
  const el = document.createElement('div');
  el.className = 'raw-toast task-undo-toast';
  el.textContent = msg;
  placeToast(el);
  setTimeout(() => el.remove(), 4000);
}

export async function noticeFromResponse(res, fallback) {
  try {
    const body = await res.json();
    showNotice(body.error || fallback);
  } catch (_) {
    showNotice(fallback);
  }
}

export function todayStr() {
  return dateToStr(new Date());
}

export function isDueNow(t, today) {
  return !!t.due_date && t.due_date <= today;
}

export function formatDate(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr + 'T00:00:00');
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diff = Math.round((d - today) / 86400000);
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Tomorrow';
  if (diff === -1) return 'Yesterday';
  return dateStr;
}

// "Sep 21" (plus the year when it isn't this year), for "since …" labels.
export function shortDate(dateStr) {
  return TasksCore.shortDate(dateStr, todayStr(), DATES);
}

export function dueDateClass(task) {
  if (!task.due_date || task.completed_at) return '';
  const today = todayStr();
  if (task.due_date < today) return 'task-overdue';
  if (task.due_date === today) return 'task-due-today';
  return '';
}

export function urgencyClass(task) {
  if (!task.due_date || task.completed_at) return '';
  const diff = Math.round(
    (new Date(task.due_date + 'T00:00:00') - new Date(todayStr() + 'T00:00:00')) / 86400000
  );
  if (diff < 0)  return 'task-urg-overdue';
  if (diff === 0) return 'task-urg-today';
  if (diff <= 2)  return 'task-urg-soon';
  if (diff <= 7)  return 'task-urg-upcoming';
  return '';
}

// String-based escape (mirrors he() in js/util.js); also escapes quotes, since esc
// output lands in attribute values. No DOM allocation — this runs many times
// per task per render.
export function esc(str) {
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// One dropdown for every place a tag is typed: the filter field (#tag), the
// edit modal's Tags field, a new task's title (#tag shorthand) and the bulk
// tag dialog. The best match (TasksCore.rankTags) is highlighted from the
// start, so Tab or Enter completes a half-typed tag to it; ↑/↓ pick another,
// Esc closes (and keeps what was typed). Each field supplies:
//   word()          the tag being typed at the caret, or null
//   suggest(word)   tags to offer, best first
//   accept(tag, w)  write the chosen tag into the field
//   onEnter()       optional: Enter also does this after completing
export function attachTagAc(input, { word, suggest, accept, onEnter }) {
  let el = null, idx = 0, tags = [];
  const hide = () => { el?.remove(); el = null; tags = []; };
  function highlight(i) {
    idx = i;
    el?.querySelectorAll('.tag-ac-item').forEach((it, j) => it.classList.toggle('selected', j === idx));
  }
  function choose(i) {
    const w = word();
    if (w && tags[i] != null) accept(tags[i], w);
    hide();
  }
  function show() {
    const w = document.activeElement === input ? word() : null;
    const next = w ? suggest(w) : [];
    hide();
    if (!next.length) return;
    tags = next;
    el = document.createElement('div');
    el.className = 'tag-ac-dropdown';
    tags.forEach((tag, i) => {
      const item = document.createElement('div');
      item.className = 'tag-ac-item';
      item.textContent = '#' + tag;
      item.addEventListener('mouseenter', () => highlight(i));
      item.addEventListener('mousedown', e => { e.preventDefault(); choose(i); });
      el.appendChild(item);
    });
    // Inside the field's parent (not <body>): in the edit modal the dropdown
    // must be in the dialog's top layer to be visible at all.
    // A chip field's dropdown hangs under the whole box, not the input.
    const anchor = input.closest('.chip-input') || input;
    const parent = anchor.parentElement;
    if (getComputedStyle(parent).position === 'static') parent.style.position = 'relative';
    el.style.top = anchor.offsetTop + anchor.offsetHeight + 'px';
    el.style.left = anchor.offsetLeft + 'px';
    el.style.width = anchor.offsetWidth + 'px';
    parent.appendChild(el);
    highlight(0);
  }
  // Capture phase: runs before the field's own key handlers, which must not
  // see a key the dropdown used (Enter submitting, Esc closing the dialog).
  input.addEventListener('keydown', e => {
    if (!el) return;
    const used = () => { e.preventDefault(); e.stopImmediatePropagation(); };
    if (e.key === 'ArrowDown') { used(); highlight((idx + 1) % tags.length); }
    else if (e.key === 'ArrowUp') { used(); highlight((idx - 1 + tags.length) % tags.length); }
    else if ((e.key === 'Tab' && !e.shiftKey) || e.key === 'Enter') {
      used(); choose(idx);
      if (e.key === 'Enter') onEnter?.();
    }
    else if (e.key === 'Escape') { used(); hide(); }
  }, true);
  input.addEventListener('input', show);
  input.addEventListener('click', show);
  input.addEventListener('blur', () => setTimeout(hide, 150));
  return { hide, isOpen: () => !!el };
}

// Keep spaces out of a tag-only field as it is typed (sanitizeTagInput).
// On `input`, not keydown: that also covers paste and phone keyboards,
// which often report no key for a space. Registered before attachTagAc's
// own input listener, so suggestions see the cleaned value.
export function noSpacesInTags(input, list) {
  input.addEventListener('input', e => {
    // Deleting never adds a space, and cleaning after it would undo the
    // deletion (Backspace on "work, " re-added the space after the comma).
    if (e.isComposing || e.inputType?.startsWith('delete')) return;
    const { value, caret } = sanitizeTagInput(input.value, input.selectionStart, list);
    if (value === input.value) return;
    input.value = value;
    input.setSelectionRange(caret, caret);
  });
}

// The word under the caret, if it starts with '#' (or '-#', an exclusion:
// the word then starts after the '-'). Inside an OR word (#a|b) only the
// part after the last pipe is being completed.
export function hashWordAtCaret(input) {
  const val = input.value;
  const caret = input.selectionStart ?? val.length;
  let start = val.lastIndexOf(' ', caret - 1) + 1;
  if (val.startsWith('-#', start) && caret > start) start++;
  let end = val.indexOf(' ', caret);
  if (end < 0) end = val.length;
  const w = val.slice(start, end);
  if (!w.startsWith('#')) return null;
  const pipe = w.lastIndexOf('|', caret - start - 1);
  const partStart = pipe >= 0 ? start + pipe + 1 : start;
  return { start, end, partStart, partial: val.slice(partStart, end).replace(/^#/, '') };
}

// Replace that word (or its OR part) with "#tag " and put the caret after it.
export function insertHashTag(input, tag, w) {
  const val = input.value;
  const after = val.slice(w.end);
  const insert = (w.partStart === w.start ? '#' : '') + tag + (after.startsWith(' ') ? '' : ' ');
  input.value = val.slice(0, w.partStart) + insert + after;
  const caret = w.partStart + insert.length;
  input.focus();
  input.setSelectionRange(caret, caret);
}

export function commentWhen(at) {
  return `${shortDate(at.slice(0, 10))} ${at.slice(11, 16)}`;
}

// When, and with users who: "12 Mar 09:30 · alice" (a comment, or a
// History entry).
export function commentStamp(c) {
  return commentWhen(c.at) + (c.by ? ` · ${c.by}` : '');
}
// A comment on a card: one of a single paragraph goes on the line of its
// time; any other (several paragraphs, a list, code …) under it, rendered
// as the notes are.
const ONE_PARAGRAPH = /^<p>((?:(?!<\/p>)[\s\S])*)<\/p>\s*$/;
export function commentCardHtml(stamp, html) {
  const when = `<span class="task-comment-when">${esc(stamp)}</span>`;
  const one = ONE_PARAGRAPH.exec(html);
  return one
    ? `<p class="task-card-comment">${when} ${one[1]}</p>`
    : `<div class="task-card-comment">${when}<div class="task-comment-body page-content">${html}</div></div>`;
}

// Relative-date helpers (used by card rendering and focus defaults).
export function dateOffset(days) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return dateToStr(d);
}

// ── Links in a task's title ────────────────────────────────────────────────

// A link to another site opens in a new tab (as go/markdown.go's
// external marks them in pages); a full URL to Zone itself stays.
const extAttrs = href => {
  try {
    const u = new URL(href, location.href);
    return /^https?:$/.test(u.protocol) && u.host !== location.host
      ? ' target="_blank" rel="noopener noreferrer"' : '';
  } catch (_) { return ''; }
};

// Links in a title: [[wikilinks]], [label](url) (a pasted URL with the
// name the dialog asked for) and bare URLs; the rest is text. A link to
// anything but the web, mail or this wiki (javascript: …) stays text.
const TITLE_LINK_RE = /\[\[([^\[\]]+)\]\]|\[([^\[\]]*)\]\(([^()\s]+)\)|(https?:\/\/[^\s<>"]+)/g;
const SAFE_HREF_RE = /^(?:https?:|mailto:|\/|#|(?![a-z][a-z0-9+.-]*:))/i;
export function linkifyTitle(text) {
  let out = '', last = 0;
  for (const m of text.matchAll(TITLE_LINK_RE)) {
    let href, label, cls = 'task-link';
    if (m[1] !== undefined) { ({ href, label } = wikilinkHref(m[1])); cls += ' wikilink'; }
    else if (m[3] !== undefined) { href = m[3]; label = m[2] || m[3]; }
    else { href = label = m[4]; }
    if (!SAFE_HREF_RE.test(href)) continue;
    out += esc(text.slice(last, m.index)) +
      `<a href="${esc(href)}" class="${cls}"${extAttrs(href)}>${esc(label)}</a>`;
    last = m.index + m[0].length;
  }
  return out + esc(text.slice(last));
}
// A title as text (menus, toasts, dialogs): its links reduced to their
// labels, as mdtext.plain_task_title does for the server.
export function plainTitle(text) {
  return String(text || '').replace(TITLE_LINK_RE, (all, wiki, label, url, bare) =>
    wiki !== undefined ? wikilinkHref(wiki).label : url !== undefined ? (label || url) : bare);
}
