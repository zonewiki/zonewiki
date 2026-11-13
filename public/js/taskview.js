// How a task app shows its list (tasks.js initTasksInstance), as this browser
// remembers it per list and tab: the Plan board's folded sections, the sort of
// Open and Plan, Open as the board or as one list; and a line's expansion. What it uses of the app comes in
// app (its state in app.st).

import TasksCore from '../tasks-core.js';
import { todayStr } from './taskutil.js';

const { SORTS } = TasksCore;

export function initTaskView(app) {
const { st } = app;

// Folded Plan sections: a section's header can hide its cards (its count
// and drop target stay). Remembered per task list in this browser, by section
// (`foldId`: a day by its distance from today, so "Tomorrow" stays folded
// as the days go by). A filter shows every section, and a task that has to
// be seen (a ?task= link, a new task, the due list) unfolds its own.
const foldKey = () => 'zone-tasks-folded:' + [location.pathname, app.appEl?.dataset.initList || '',
  app.appEl?.dataset.initTag || '', app.instanceId].join('|');
function loadFolded() {
  try { return new Set(JSON.parse(localStorage.getItem(foldKey()) || '[]')); } catch (_) { return new Set(); }
}
st.folded = loadFolded();
function saveFolded() {
  try { localStorage.setItem(foldKey(), JSON.stringify([...st.folded])); } catch (_) {}
}
function foldId(key, today = todayStr()) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(key)) return key;
  return 'day' + Math.round((new Date(key + 'T00:00:00') - new Date(today + 'T00:00:00')) / 86400000);
}
function toggleFold(id) {
  if (st.folded.has(id)) st.folded.delete(id); else st.folded.add(id);
  saveFolded();
  app.render();
}
// Unfold the Plan section task `id` sits in; true when that changed anything.
function unfoldFor(id) {
  const task = st.tasks.find(t => t.id === id);
  if (st.activeView !== 'plan' || !task) return false;
  const fid = foldId(TasksCore.bucketKey(task, todayStr()));
  if (!st.folded.delete(fid)) return false;
  saveFolded();
  app.render();
  return true;
}

// Open and Plan sorted by deadline or by creation (the Order dropdown; o
// cycles it) instead of the manual order (TasksCore.SORTS). Nothing reorders
// in them: ordering is done back in the manual order. Each tab's sort is
// remembered per task list in this browser.
const SORT_MODES = ['manual', 'due', 'created'];
const SORT_TITLES = { manual: 'the manual order', due: 'by deadline', created: 'newest first' };
const sortKey = () => 'zone-tasks-sort:' + [location.pathname, app.appEl?.dataset.initList || '',
  app.appEl?.dataset.initTag || '', app.instanceId].join('|');
let sortModes = {};   // view → 'due' | 'created'; missing: manual
try { sortModes = JSON.parse(localStorage.getItem(sortKey())) || {}; } catch (_) {}
// The shown tab's sort, when not the manual order.
const activeSort = () => (st.activeView === 'open' || st.activeView === 'plan') && SORTS[sortModes[st.activeView]]
  ? sortModes[st.activeView] : null;
function applySort() {
  if (!app.sortSel) return;
  app.sortSel.hidden = st.activeView !== 'open' && st.activeView !== 'plan';
  app.sortSel.value = activeSort() || 'manual';
}
function setSort(mode) {
  if (st.activeView !== 'open' && st.activeView !== 'plan') return;
  if (SORTS[mode]) sortModes[st.activeView] = mode; else delete sortModes[st.activeView];
  try { localStorage.setItem(sortKey(), JSON.stringify(sortModes)); } catch (_) {}
  applySort();
  app.render();
}
// o: Manual → Due date → Created → Manual.
function cycleSort() {
  const mode = activeSort() || 'manual';
  setSort(SORT_MODES[(SORT_MODES.indexOf(mode) + 1) % SORT_MODES.length]);
}
app.sortSel?.addEventListener('change', () => setSort(app.sortSel.value));

// The Open tab shows every open task, grouped by start date (Plan on: the
// board, view 'plan') or as one list (Plan off: view 'open', Unsorted ·
// Sorted, where the manual order is ranked across everything). Plan was a
// tab of its own until 2026-10; the views keep their names, so `view=plan`
// and `view=open` links still open the one they always did. The Plan
// switch (d) toggles. The Open tab opens what plan= says (the settings
// dialog's Plan; the board without it); a flip of the switch is remembered
// for this browser tab only (sessionStorage), as the active tab is, so a
// fresh load is back at the default.
const isOpenTab = view => view === 'open' || view === 'plan';
const groupKey = () => 'zone-tasks-group:' + [location.pathname, app.appEl?.dataset.initList || '',
  app.appEl?.dataset.initTag || '', app.instanceId].join('|');
const initPlan = app.appEl?.dataset.initPlan || '';   // [[tasks: plan=on|off]]
function openGroup() {
  let stored = null;
  try { stored = sessionStorage.getItem(groupKey()); } catch (_) {}
  if (stored === 'list') return 'open';
  if (stored === 'days') return 'plan';
  return initPlan === 'off' ? 'open' : 'plan';
}
function applyGroup() {
  if (!app.planSwitch) return;
  app.planSwitch.hidden = !isOpenTab(st.activeView);
  app.planSwitch.setAttribute('aria-checked', String(st.activeView === 'plan'));
}
function setGroup(group) {
  if (!isOpenTab(st.activeView)) return;
  try { sessionStorage.setItem(groupKey(), group); } catch (_) {}
  app.switchTab(group === 'list' ? 'open' : 'plan');
}
app.planSwitch?.addEventListener('click', () => setGroup(st.activeView === 'plan' ? 'list' : 'days'));
// The view a tab opens: Open its remembered grouping (unless already on
// one of them: a click on the active tab changes nothing).
const tabView = tab => tab === 'open' && !isOpenTab(st.activeView) ? openGroup() : tab;

// Every task is one line (title, then tags, markers and dates; notes and
// comments hidden). One task at a time is expanded: the selected one (a
// click, arrows, j/k; setSelected), or on touch the one tapped first (a tap
// on it then opens the modal), shows its whole title, wrapped, when the
// line cuts it off (titleCut); notes and comments stay hidden. Its details
// (notes, latest comments) open on request only
// (detailsOpen): 📝, 💬 or the expand button on the line, or → / l;
// ← / h closes them, Esc closes both. Opened, they stay open while the
// selection moves on (detailsMode): the next selected task shows its
// details too, until they are closed. Enter opens the modal. Another tab
// starts with nothing expanded (collapseAll).
st.expandedId = null;
st.detailsOpen = false;
st.detailsMode = false;
function collapseAll() {
  st.expandedId = null;
  st.detailsOpen = st.detailsMode = false;
}
// The expand button of a line with details: a chevron, turned up
// while they are open.
const EXPAND_ICON = '<svg viewBox="0 0 16 16" width="1em" height="1em" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">' +
  '<path d="M4 6l4 4 4-4"/></svg>';
// ⋯ on cards: three dots drawn, so they sit on the line's centre whatever
// the font (the ⋯ character sat low in some).
const MORE_ICON = '<svg viewBox="0 0 16 16" width="1em" height="1em" aria-hidden="true" fill="currentColor">' +
  '<circle cx="2.5" cy="8" r="1.1"/><circle cx="8" cy="8" r="1.1"/><circle cx="13.5" cy="8" r="1.1"/></svg>';
const cardEl = id => app.listEl?.querySelector(`.task-item[data-id="${CSS.escape(id)}"]`);
// Whether the line cuts the task's title off (selecting it then shows it
// whole). One whose title fits stays a line.
function titleCut(id) {
  const title = cardEl(id)?.querySelector('.task-content');
  return !!title && title.scrollWidth > title.clientWidth + 1;
}
// Whether the task has details its line hides: notes or comments.
const hasDetails = id => !!cardEl(id)?.querySelector('.task-desc, .task-card-comments');
function setExpanded(id, details = false) {
  st.expandedId = id;
  st.detailsOpen = !!id && details;
  app.listEl?.querySelectorAll('.task-item').forEach(el => {
    const on = el.dataset.id === id;
    if (!on && el.classList.contains('task-expanded') && st.pressedCard && el !== st.pressedCard) collapseLater(el);
    else {
      el.classList.toggle('task-expanded', on);
      el.classList.toggle('task-details', on && st.detailsOpen);
      el.querySelector('.task-expand-btn')?.setAttribute('aria-expanded', String(on && st.detailsOpen));
    }
  });
}
// 📝, 💬 or →/←: open or close a task's details, selecting it.
function toggleDetails(id, open = !(st.expandedId === id && st.detailsOpen)) {
  if (!hasDetails(id)) return false;
  st.detailsMode = open;
  if (st.selectedIds.size !== 1 || st.selectedId !== id) app.setSelected(id);
  // Closing keeps the whole title (a short one looks the same as its line).
  setExpanded(id, open);
  return true;
}
// A task just selected: its details while they are open on the
// way through (detailsMode), else its whole title if the line cuts it off.
function expandSelected(id) {
  if (st.detailsMode && hasDetails(id)) setExpanded(id, true);
  else if (titleCut(id)) setExpanded(id);
}
// A mouse press on another card leaves the expanded one open until a
// double-click is over (pressedCard: the card a mousedown is selecting) and
// then closes it with a short animation. Collapsing at once would pull a
// card below it up under the pointer, so a double-click's second click hit
// another card; a card above closes the same way, for one behaviour.
const COLLAPSE_DELAY_MS = 400;
st.pressedCard = null;
let collapsing = null;   // { el, timer }
function collapseLater(el) {
  if (collapsing?.el === el) return;   // already waiting (the press runs setExpanded twice, a double-click two presses)
  finishCollapse();
  collapsing = { el, timer: setTimeout(finishCollapse, COLLAPSE_DELAY_MS) };
}
function finishCollapse() {
  if (!collapsing) return;
  const { el, timer } = collapsing;
  collapsing = null;
  clearTimeout(timer);
  if (!el.isConnected || el.dataset.id === st.expandedId) return;
  const from = el.offsetHeight;
  el.classList.remove('task-expanded', 'task-details');
  el.querySelector('.task-expand-btn')?.setAttribute('aria-expanded', 'false');
  const to = el.offsetHeight;
  if (from > to && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
    el.animate([{ height: `${from}px`, overflow: 'hidden' }, { height: `${to}px`, overflow: 'hidden' }],
               { duration: 150, easing: 'ease-out' });
  }
}

return { foldId, toggleFold, unfoldFor, SORT_TITLES, activeSort, applySort, cycleSort, isOpenTab, groupKey, applyGroup, setGroup, tabView, collapseAll, EXPAND_ICON, MORE_ICON, setExpanded, toggleDetails, expandSelected };
}
