import { initTaskApps, openTasksEdit } from '../tasks.js';
import { closeOnBackdropClick, localDate, showToast } from './util.js';
import { openSearchModal } from './search.js';

// ── Quick-task modal ──────────────────────────────────────────────────────────

// A new task in the Tasks home, from any page (c t, the New task button).
// On the home page its own "+ New" (so the view's tags and dates apply);
// where a task list app is loaded (an embed, the editor's hidden one) its edit
// modal, with the tags and keep the home gives new tasks (the server puts
// them on <body>: data-tasks-tags, data-tasks-keep; none without a home);
// elsewhere the home page (or /tasks) opens with the modal.
// A page without a task list app loads a hidden one first (/api/tasks/headless,
// started by tasks.js's initTaskApps), so you stay on the page; a toast says
// where the task went. Should that fail, the home page opens instead.
export async function openHomeTask() {
  const homeAdd = document.querySelector('.tasks-app[data-home]:not([data-headless]) [id^="task-add-btn"]');
  if (homeAdd) { homeAdd.click(); return; }
  const { tasksTags = '', tasksKeep, tasksToday, tasksPrivate } = document.body.dataset;
  // The start date as the home's "+ New" gives it (today on Focus); without
  // a home, as /tasks's Focus does.
  const homeless = !('tasksTags' in document.body.dataset);
  const opts = { tags: tasksTags, keep: tasksKeep === '1', private: tasksPrivate === '1',
                 start_date: homeless || tasksToday === '1' ? localDate() : null };
  if (!document.querySelector('.tasks-app[data-ready]')) {
    try {
      await loadHeadlessTasks();
    } catch (err) {
      window.location.href = tasksHomeHref() + '?new=1';
      return;
    }
    opts.onCreated = task => {
      if (task) showToast(`Added “${task.title}” to your tasks`);
    };
  }
  openTasksEdit(null, opts);
}

// The hidden task app (/api/tasks/headless), added to the page and started.
let headlessTasks = null;
function loadHeadlessTasks() {
  headlessTasks ??= (async () => {
    const res = await fetch('/api/tasks/headless');
    if (!res.ok) throw new Error(res.status);
    const holder = document.createElement('div');
    holder.innerHTML = await res.text();
    document.body.append(...holder.childNodes);
    if (!initTaskApps()) throw new Error('no tasks');
  })().catch(err => { headlessTasks = null; throw err; });
  return headlessTasks;
}

// Where the Tasks module links go: the Tasks home page, else /tasks.
export const tasksHomeHref = () =>
  document.querySelector('.module-link[data-module="tasks"]')?.getAttribute('href') || '/tasks';

// ── New note modal ───────────────────────────────────────────────────────────────

const newNoteModal  = document.getElementById('modal');
const newNoteForm   = document.getElementById('new-note-form');
const newNoteTitle  = document.getElementById('new-note-title');
const newNoteCancel = document.getElementById('new-note-cancel');

newNoteCancel?.addEventListener('click', () => newNoteModal.close());
closeOnBackdropClick(newNoteModal);

export function openNewNote() {
  if (!newNoteModal) return;
  newNoteTitle.value = '';
  newNoteModal.showModal();
  newNoteTitle.focus();
}

newNoteForm?.addEventListener('submit', async e => {
  e.preventDefault();
  const title = newNoteTitle.value.trim() || 'Untitled';
  const data = new FormData();
  data.append('title', title);
  try {
    const res = await fetch('/api/scratch', { method: 'POST', body: data });
    const { slug } = await res.json();
    window.location.href = `/edit/${slug}`;
  } catch (_) {}
});

// ── Action bar ───────────────────────────────────────────────────────────────
// The phone's actions, as the desktop's top actions: a new note, a
// new task and Search (Ctrl+P). In the tasks the new task comes from its own
// "+ New" (so the view's tags and dates apply; on the Tasks home page the
// home embed's), elsewhere it goes to your tasks (openHomeTask).

const tasksAddButton = () =>
  document.querySelector('.tasks-app[data-home] [id^="task-add-btn"]')
  || document.querySelector('.tasks-app:not([data-headless]) [id^="task-add-btn"]');

document.querySelector('.action-new-note')?.addEventListener('click', openNewNote);
document.querySelector('.action-task')?.addEventListener('click', () => {
  const add = document.body.dataset.module === 'tasks' && tasksAddButton();
  if (add) add.click(); else openHomeTask();
});
document.querySelector('.action-search')?.addEventListener('click', openSearchModal);

// A tap on the tab bar acts on its touchend and cancels the browser's own
// click. Firefox for Android, as an installed app with its toolbar scrolled
// away, draws the bar lower than it lays it out: a touch on the strip under
// the labels lands below the bar's box, on the page there (a task list card, which
// got selected and scrolled into view). So a touch on the bar, or below its
// box where nothing covers the bar, goes to the tab at its x, before the page
// sees it. A long press or a move is left to the browser (its link menu, a
// scroll).
(function () {
  const bar = document.querySelector('.action-buttons');
  if (!bar) return;
  const tabAt = (target, x, y) => {
    if (!bar.contains(target)) {
      // Below the bar's box, and the bar not covered there (a menu's bottom
      // sheet, a dialog): the browser's hit test missed the bar.
      const r = bar.getBoundingClientRect();
      if (!r.height || y < r.bottom || x < r.left || x > r.right) return null;
      if (!bar.contains(document.elementFromPoint(x, r.bottom - 1))) return null;
    }
    let best = null, dist = Infinity;
    for (const item of bar.querySelectorAll('a, button')) {
      const b = item.getBoundingClientRect();
      const d = x < b.left ? b.left - x : x > b.right ? x - b.right : 0;
      if (d < dist) { best = item; dist = d; }
    }
    return best;
  };
  let tap = null;
  document.addEventListener('touchstart', e => {
    tap = null;
    if (e.touches.length !== 1) return;
    const p = e.touches[0];
    const item = tabAt(e.target, p.clientX, p.clientY);
    if (!item) return;
    e.stopPropagation();   // not the page's (a card's drag or swipe)
    tap = { item, x: p.clientX, y: p.clientY, t: e.timeStamp };
  }, { capture: true, passive: true });
  document.addEventListener('touchmove', e => {
    const p = e.touches[0];
    if (tap && Math.hypot(p.clientX - tap.x, p.clientY - tap.y) > 10) tap = null;
  }, { capture: true, passive: true });
  document.addEventListener('touchcancel', () => { tap = null; }, true);
  document.addEventListener('touchend', e => {
    const t = tap;
    tap = null;
    if (!t || !e.cancelable || e.timeStamp - t.t > 500) return;
    e.preventDefault();   // no compatibility mouse events, no click
    e.stopPropagation();
    t.item.click();
  }, true);
}());

// ── Sharing mode ─────────────────────────────────────────────────────────────
// For sharing the screen: everything that shows other pages around the one
// on screen is hidden (style.css, html.sharing): the trail of visited pages,
// backlinks, Related, Links to, linked tasks, the empty search modal's
// recent pages, the editor's page list before anything is typed, the
// status counts. And the server leaves out the pages not for sharing
// (noshare in the settings file). It is a cookie, so the server knows: it renders
// <html class="sharing"> from the first paint, and switching reloads the
// page, rendered for the new mode.
const sharingBtn = document.querySelector('.top-sharing');
export const sharingOn = () => document.documentElement.classList.contains('sharing');
function syncSharingButton() {
  const on = sharingOn();
  sharingBtn?.setAttribute('aria-pressed', String(on));
  if (sharingBtn) sharingBtn.title = on ? 'Sharing mode is on: click to show everything again (S)'
                                        : 'Sharing mode: hide what is around the page (S)';
}
export function toggleSharing() {
  const on = !sharingOn();
  document.cookie = `leaf-sharing=${on ? '1' : '0'}; path=/; SameSite=Lax; max-age=${on ? 31536000 : 0}`;
  location.reload();
}
sharingBtn?.addEventListener('click', toggleSharing);
syncSharingButton();
// The desktop's buttons at the top right (views.py top_actions_html).
document.querySelector('.top-new-note')?.addEventListener('click', openNewNote);
document.querySelector('.top-task')?.addEventListener('click', openHomeTask);
document.querySelector('.top-search')?.addEventListener('click', openSearchModal);
// The search pill names its shortcut; on a Mac that is ⌘P.
if (/Mac|iPhone|iPad/.test(navigator.platform)) {
  const key = document.querySelector('.top-search-key');
  if (key) key.textContent = '⌘P';
}
