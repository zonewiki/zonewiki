import { putTask, showToast } from './util.js';
import { toggleCheckboxInTextarea } from './mdedit.js';

// ── Syntax highlighting ───────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  if (!window.hljs) return;
  document.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
});

// ── Task list checkboxes ───────────────────────────────────────────────────────

// The page's own checkboxes: a deck's task notes have theirs (deck.js).
const IN_DECK = '.tasks-app, .task-edit-modal';
document.addEventListener('change', e => {
  if (!e.target.matches('.task-list-item-checkbox') || e.target.closest(IN_DECK)) return;
  const checkboxes = Array.from(document.querySelectorAll('.task-list-item-checkbox'))
    .filter(cb => !cb.closest(IN_DECK));
  const idx = checkboxes.indexOf(e.target);
  if (idx === -1) return;

  const textarea = document.getElementById('editor-textarea');
  if (textarea) {
    // Editor: toggle the Nth checkbox in the textarea (js/mdedit.js).
    toggleCheckboxInTextarea(textarea, idx);
  } else {
    // Page view: persist the toggle, with an undo toast — a mis-tap on a
    // phone otherwise edits the file silently. Undo is a second toggle.
    const cb = e.target;
    const post = () => toggleCheckboxOnServer(idx).then(ok => {
      if (!ok) { cb.checked = !cb.checked; showToast('Could not save the checkbox'); }
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
let checkboxQueue = Promise.resolve();
function toggleCheckboxOnServer(idx) {
  const slug = window.location.pathname.replace(/^\/page\//, '');
  const data = new FormData();
  data.append('idx', idx);
  checkboxQueue = checkboxQueue
    .then(() => fetch(`/api/toggle-checkbox/${slug}`, { method: 'POST', body: data }))
    .then(r => r.ok, () => false);
  return checkboxQueue;
}


// ── Task references ([[task:…]] chips) ───────────────────────────────────────

// A chip's checkbox completes (or reopens) the task in the deck, through the
// same API as the deck, so repeating and keep tasks behave as there. The chip
// is then re-rendered by the server (/api/preview of the reference): a
// repeating task shows its next occurrence, a keep task its "✓ today".
async function rerenderTaskRef(chip, id) {
  // The lists the page declares (its list embeds), so the fresh chip links to
  // the task in the same list as the server-rendered one (link_list).
  const lists = [...document.querySelectorAll('.tasks-app[data-init-list]')]
    .map(el => el.dataset.initList).filter(Boolean);
  const query = lists.length ? `?lists=${encodeURIComponent(lists.join(','))}` : '';
  try {
    const res = await fetch(`/api/preview${query}`, {
      method: 'POST', headers: { 'Content-Type': 'text/plain' }, body: `[[task:${id}]]`,
    });
    const fresh = new DOMParser().parseFromString(await res.text(), 'text/html').querySelector('.task-ref');
    if (fresh) { chip.replaceWith(fresh); return fresh; }
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
    try { before = (await (await fetch('/api/deck')).json()).find(t => t.id === id) || null; } catch (_) { /* no undo data */ }
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
