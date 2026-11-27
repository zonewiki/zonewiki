import { copyText, hashTarget, localizeDates, putTask, showToast } from './util.js';
import { toggleCheckboxInTextarea } from './mdedit.js';
import { HIGHLIGHT_CODE, renderFigures } from './figures.js';

// ── Syntax highlighting, math and diagrams ────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  if (window.hljs) document.querySelectorAll(HIGHLIGHT_CODE).forEach(el => hljs.highlightElement(el));
  // What the reader was sent to stays where it is while figures above it render.
  const target = () => {
    const mark = document.querySelector('mark.search-hl.current');
    return mark || hashTarget(location.hash);
  };
  for (const root of document.querySelectorAll('article.page-content, .rail-toc')) renderFigures(root, target);
  scrollToRawAnchor();
});

// ── Anchors of a page's raw HTML ──────────────────────────────────────────────

// An id or name a page's raw HTML gives has "user-content-" in front
// (go/markdown.go defuseHTML: so it can't stand in for one of Zonewiki's
// elements), and so has a heading named as one of Zonewiki's ids (headingID),
// so for #note the browser finds no element and stays where it is, and for
// #modal it goes to the new note's dialog: this goes where hashTarget says
// instead, on opening the page and when a link changes the hash (fold.js has
// unfolded its section by then).
function scrollToRawAnchor() {
  let id = location.hash.slice(1);
  if (!id) return;
  try { id = decodeURIComponent(id); } catch (_) { /* as written */ }
  const el = hashTarget(location.hash);
  if (el && el.id !== id) requestAnimationFrame(() => el.scrollIntoView());
}
window.addEventListener('hashchange', scrollToRawAnchor);

// ── Dates ─────────────────────────────────────────────────────────────────────

// The task chips' dates in the locale setting (the server writes them in US English).
localizeDates();

// ── Heading links ─────────────────────────────────────────────────────────────

// On a page view, a link icon after the title and after each heading with an
// anchor (#, ##, ###; not a task list embed's), shown on hover: a click copies the
// link to the page, or to the section, and puts the section's anchor in the URL.
const CHECK_ICON = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7"/></svg>';
const LINK_ICON = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6.5 9.5a3 3 0 0 0 4.2 0l2-2a3 3 0 0 0-4.2-4.2l-.8.8"/><path d="M9.5 6.5a3 3 0 0 0-4.2 0l-2 2a3 3 0 0 0 4.2 4.2l.8-.8"/></svg>';

(function () {
  const article = document.querySelector('.page-grid article.page-content');
  const title = document.querySelector('.page-grid .page-header h1');
  if (!article || !title) return;
  const heads = [...article.querySelectorAll('h1[id], h2[id], h3[id]')].filter(h => !h.closest('.tasks-app'));
  for (const h of [title, ...heads]) {
    const a = document.createElement('a');
    a.className = 'heading-link';
    a.href = h === title ? location.pathname : '#' + h.id;
    a.title = h === title ? 'Copy link to page' : 'Copy link to section';
    a.setAttribute('aria-label', a.title);
    a.innerHTML = LINK_ICON;
    a.addEventListener('click', async e => {
      e.preventDefault();
      if (h !== title) history.replaceState(null, '', a.hash);
      if (!await copyText(a.href)) { showToast('Could not copy the link'); return; }
      showToast('Link copied');
      // A ✓ in the icon's place for a moment.
      a.innerHTML = CHECK_ICON;
      a.classList.add('copied');
      clearTimeout(a.copiedTimer);
      a.copiedTimer = setTimeout(() => { a.innerHTML = LINK_ICON; a.classList.remove('copied'); }, 1500);
    });
    h.append(a);
  }
}());


// ── Task list checkboxes ───────────────────────────────────────────────────────

// The page's own checkboxes: a task list's task notes have theirs (tasks.js).
const IN_TASKS = '.tasks-app, .task-edit-modal';
document.addEventListener('change', e => {
  if (!e.target.matches('.task-list-item-checkbox') || e.target.closest(IN_TASKS)) return;
  const checkboxes = Array.from(document.querySelectorAll('.task-list-item-checkbox'))
    .filter(cb => !cb.closest(IN_TASKS));
  const idx = checkboxes.indexOf(e.target);
  if (idx === -1) return;

  const textarea = document.getElementById('editor-textarea');
  if (textarea) {
    // Editor: tick the box in the textarea where the server said it is in
    // the text the preview shows (js/mdedit.js); a text changed since waits
    // for the preview that follows it.
    const shown = document.getElementById('editor-preview')?.zoneMarks;
    if (!shown || shown.text !== textarea.value) { e.target.checked = !e.target.checked; return; }
    toggleCheckboxInTextarea(textarea, shown.marks[idx]);
    shown.text = textarea.value;   // a tick changes one character: the marks still hold
  } else {
    // Page view: persist the toggle, with an undo toast — a mis-tap on a
    // phone otherwise edits the file silently. Undo is a second toggle.
    const cb = e.target;
    const post = () => toggleCheckboxOnServer(idx).then(status => {
      if (status === 'ok') return;
      cb.checked = !cb.checked;
      if (status === 'changed') {
        showToast('The page changed: reload to tick this', () => window.location.reload(), 'Reload');
      } else {
        showToast('Could not save the checkbox');
      }
    });
    post();
    const text = (cb.closest('li')?.textContent || '').trim().split('\n')[0];
    const label = text.length > 40 ? text.slice(0, 39) + '…' : text;
    showToast(`${cb.checked ? 'Checked' : 'Unchecked'} “${label}”`, () => {
      cb.checked = !cb.checked;
      post();
    });
  }
});

// Toggles are chained, so an undo can never overtake the toggle it reverts
// (the server flips whatever state the file has when the request arrives).
// Each sends its line's key, as this view has it: on a page changed
// since, the server finds the item by its line ('changed': not there once).
let checkboxQueue = Promise.resolve();
function toggleCheckboxOnServer(idx) {
  const slug = window.location.pathname.replace(/^\/page\//, '');
  const keys = document.querySelector('article.page-content[data-task-keys]')?.dataset.taskKeys.split(',');
  const data = new FormData();
  data.append('idx', idx);
  if (keys?.[idx]) data.append('key', keys[idx]);
  checkboxQueue = checkboxQueue
    .then(() => fetch(`/api/toggle-checkbox/${slug}`, { method: 'POST', body: data }))
    .then(r => r.status === 409 ? 'changed' : r.ok ? 'ok' : 'failed', () => 'failed');
  return checkboxQueue;
}


// ── Task references ([[task:…]] chips) ───────────────────────────────────────

// A chip's checkbox completes (or reopens) the task in the tasks, through the
// same API as the tasks, so repeating and keep tasks behave as there. The chip
// is then re-rendered by the server (/api/preview of the reference): a
// repeating task shows its next occurrence, a keep task its "✓ today".
async function rerenderTaskRef(chip, id) {
  try {
    const res = await fetch('/api/preview', {
      method: 'POST', headers: { 'Content-Type': 'text/plain' }, body: `[[task:${id}]]`,
    });
    const fresh = new DOMParser().parseFromString(await res.text(), 'text/html').querySelector('.task-ref');
    if (fresh) { localizeDates(fresh); chip.replaceWith(fresh); return fresh; }
  } catch (_) { /* keep the old chip */ }
  return chip;
}


document.addEventListener('change', async e => {
  const cb = e.target;
  if (!cb.matches('.task-ref-check')) return;
  let chip = cb.closest('.task-ref');
  const id = chip.dataset.taskId;
  const done = cb.checked;
  // The open task as it is now, for undo: completing a keep task changes
  // its dates instead of archiving it, and undo must restore them.
  let before = null;
  if (done) {
    try { before = (await (await fetch('/api/tasks')).json()).find(t => t.id === id) || null; } catch (_) { /* no undo data */ }
  }
  const res = await putTask(id, { completed: done }).catch(() => null);
  if (!res?.ok) {
    cb.checked = !done;
    showToast('Could not update the task');
    return;
  }
  const title = chip.querySelector('.task-ref-title')?.textContent || '';
  const label = title.length > 40 ? title.slice(0, 39) + '…' : title;
  chip = await rerenderTaskRef(chip, id);
  showToast(`${done ? 'Done' : 'Reopened'} “${label}”`, async () => {
    const undo = before?.keep
      ? { start_date: before.start_date, due_date: before.due_date, last_done: before.last_done,
          history: before.history }
      : { completed: !done, ...(before ? { history: before.history } : {}) };
    const r = await putTask(id, undo).catch(() => null);
    if (r?.ok) chip = await rerenderTaskRef(chip, id);
    else showToast('Could not undo');
  });
});

// ── Sortable tables ───────────────────────────────────────────────────────────

// Clicking a column header of a table in a page's article sorts its rows:
// ascending, descending, then back to the file's order. View only — the file
// is never touched. Tables with merged cells (raw HTML) are left alone. Click
// handling is delegated, so cloned presentation slides sort too.
const tableCollator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

function tableCellNumber(text) {
  const t = text.replace(/[\s%€$£]/g, '');
  if (!/^[-+]?(\d+|\d{1,3}(,\d{3})+)(\.\d+)?$/.test(t)) return null;
  return parseFloat(t.replace(/,/g, ''));
}

function sortTableBy(th) {
  const table = th.closest('table');
  const tbody = table.tBodies[0];
  const col = th.cellIndex;
  const next = { none: 'ascending', ascending: 'descending', descending: 'none' };
  const dir = next[th.getAttribute('aria-sort') || 'none'];
  table.querySelectorAll('thead th').forEach(h => h.setAttribute('aria-sort', 'none'));
  th.setAttribute('aria-sort', dir);

  const rows = Array.from(tbody.rows);
  const text = r => (r.cells[col]?.textContent || '').trim();
  // A column sorts as numbers only when every non-empty cell is one
  const numeric = rows.every(r => !text(r) || tableCellNumber(text(r)) !== null);
  const sign = dir === 'descending' ? -1 : 1;
  rows.sort((a, b) => {
    if (dir === 'none') return a.dataset.sortIdx - b.dataset.sortIdx;
    const ta = text(a), tb = text(b);
    if (!ta || !tb) return (!ta) - (!tb);  // empty cells last either way
    const c = numeric ? tableCellNumber(ta) - tableCellNumber(tb) : tableCollator.compare(ta, tb);
    return sign * c || a.dataset.sortIdx - b.dataset.sortIdx;
  });
  rows.forEach(r => tbody.appendChild(r));
}

document.querySelectorAll('article.page-content table').forEach(table => {
  if (!table.tHead || table.tBodies.length !== 1 || table.tBodies[0].rows.length < 2) return;
  if (table.querySelector('[colspan], [rowspan]')) return;
  table.classList.add('sortable-table');
  Array.from(table.tBodies[0].rows).forEach((r, i) => { r.dataset.sortIdx = i; });
  table.querySelectorAll('thead th').forEach(th => {
    th.tabIndex = 0;
    th.setAttribute('aria-sort', 'none');
  });
});

document.addEventListener('click', e => {
  const th = e.target.closest('table.sortable-table thead th');
  if (!th || e.target.closest('a') || getSelection().toString()) return;
  sortTableBy(th);
});
document.addEventListener('keydown', e => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const th = e.target.closest?.('table.sortable-table thead th');
  if (!th) return;
  e.preventDefault();
  sortTableBy(th);
});

// ── Image lightbox ────────────────────────────────────────────────────────────

// Open an image full size (presentation mode's slides too, js/present.js).
let lightboxOpen = null;
export function openLightbox(src) {
  lightboxOpen?.(src);
}

(function () {
  const lb = document.createElement('div');
  lb.id = 'img-lightbox';
  const lbImg = document.createElement('img');
  lb.appendChild(lbImg);
  document.body.appendChild(lb);

  function open(src) {
    lbImg.src = src;
    lb.classList.add('open');
  }
  function close() {
    lb.classList.remove('open');
    lbImg.src = '';
  }

  // Click backdrop to close; clicking the image itself does nothing (cursor: default)
  lb.addEventListener('click', e => { if (e.target === lb) close(); });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && lb.classList.contains('open')) {
      e.stopImmediatePropagation();
      close();
    }
  });

  // Attach to all page-content images (both on load and after dynamic content)
  function attachLightbox(root) {
    root.querySelectorAll('.page-content img').forEach(img => {
      if (img.dataset.lightbox) return;
      img.dataset.lightbox = '1';
      img.addEventListener('click', () => open(img.src));
    });
  }
  attachLightbox(document);

  lightboxOpen = open;
}());
