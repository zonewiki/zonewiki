import { initTaskApps, openTasksEdit } from '../tasks.js';
import { closeOnBackdropClick, localDate, showToast } from './util.js';
import { openSearchModal } from './search.js';

// ── Quick-task modal ──────────────────────────────────────────────────────────

// A new task in the Inbox, starting today, from any page and the same on
// every one (c t, the New task button): it presses no list's "+ New", so
// what it makes doesn't depend on the page or the tab it was pressed on.
// Where a task list app is loaded (an embed, the Tasks page, the editor's
// hidden one) it is that app's edit modal that opens.
// A page without a task list app loads a hidden one first (/api/tasks/headless,
// started by tasks.js's initTaskApps), so you stay on the page; a toast says
// where the task went. Should that fail, where the Tasks link goes opens instead.
export async function openDefaultTask() {
  // Into the Inbox, starting today, as the "+ New" of My tasks' Today gives it.
  const opts = { list: '', tags: '', keep: false, start_date: localDate() };
  if (!document.querySelector('.tasks-app[data-ready]')) {
    try {
      await loadHeadlessTasks();
    } catch (err) {
      // My tasks, whose New gives the Inbox, not the list the link came back to.
      window.location.href = tasksLinkHref() === '/tasks' ? '/tasks?new=1' : '/tasks?mine=1&new=1';
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

// Where the Tasks module links go: the view /tasks showed last (the
// server's zone-tasks-place cookie), else My tasks (/tasks?mine=1), or
// /tasks in a wiki without lists.
export const tasksLinkHref = () =>
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
    const res = await fetch('/api/notes', { method: 'POST', body: data });
    if (res.status === 507) { alert(await res.text()); return; }   // the wiki is full
    const { slug } = await res.json();
    window.location.href = `/edit/${slug}`;
  } catch (_) {}
});

// ── Action bar ───────────────────────────────────────────────────────────────
// The phone's actions, as the desktop's top actions: a new note, a
// new task and Search (Ctrl+P). In the tasks the new task comes from its own
// "+ New" (so the view's tags and dates apply; on a page that shows My tasks
// or the Inbox that one's), elsewhere it goes to your Inbox (openDefaultTask).

const tasksAddButton = () =>
  document.querySelector('.tasks-app[data-home] [id^="task-add-btn"]')
  || document.querySelector('.tasks-app:not([data-headless]) [id^="task-add-btn"]');

document.querySelectorAll('.action-new-note, .notes-new').forEach(b => b.addEventListener('click', openNewNote));
document.querySelector('.action-task')?.addEventListener('click', () => {
  const add = document.body.dataset.module === 'tasks' && tasksAddButton();
  if (add) add.click(); else openDefaultTask();
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
// page, rendered for the new mode. It is switched by S and, while on, by the
// Sharing chip at the top right (top_actions.html; off, the top row keeps no
// room for it).
export const sharingOn = () => document.documentElement.classList.contains('sharing');
export function toggleSharing() {
  const on = !sharingOn();
  document.cookie = `zone-sharing=${on ? '1' : '0'}; path=/; SameSite=Lax; max-age=${on ? 31536000 : 0}`;
  location.reload();
}
document.querySelectorAll('.top-sharing').forEach(b => b.addEventListener('click', toggleSharing));
// The desktop's buttons at the top right (templates/partials/top_actions.html).
document.querySelector('.top-search')?.addEventListener('click', openSearchModal);
// The search pill names its shortcut; on a Mac that is ⌘P.
if (/Mac|iPhone|iPad/.test(navigator.platform)) {
  const key = document.querySelector('.top-search-key');
  if (key) key.textContent = '⌘P';
}
