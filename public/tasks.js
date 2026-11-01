// ── Zone Task App ────────────────────────────────────────────────────────────
// An ES module; zonewiki.js imports it on every page. initTaskApps() starts each
// task app (.tasks-app) on the page, and one added later (the hidden tasks
// that a new task from any page loads, actions.js).

import TasksCore from './tasks-core.js';
import { refreshDueBadge } from './js/chrome.js';
import { linkAutocomplete } from './js/completion.js';
import { markdownEditor, toggleCheckboxText } from './js/mdedit.js';
import { openLightbox } from './js/page.js';
import { HIGHLIGHT_CODE, renderFigures } from './js/figures.js';
import { copyText, keyPrefix, pasteUrlAsLink, putTask, wikilinkHref } from './js/util.js';
import { openDatePicker } from './js/datepicker.js';
import { initTaskMenu } from './js/taskmenu.js';
import { initTaskDnd } from './js/taskdnd.js';
import { initTaskTouch } from './js/tasktouch.js';
import { DATES, LOCALE, attachTagAc, commentStamp, commentWhen, dateOffset, dueDateClass, esc, formatDate, hashWordAtCaret, insertHashTag, isDueNow, noSpacesInTags, noticeFromResponse, placeToast, shortDate, showNotice, startsAfterDeadline, todayStr, urgencyClass } from './js/taskutil.js';

// Pure task logic lives in tasks-core.js.
const { dateToStr, addDays, isFocus, byOpenOrder, SORTS, placeInOrder, tagCounts, selectionTags, bulkTagChange, rankTags, sanitizeTagInput, parseFilter, matchesFilter, tagParamToQuery, parseQuickAdd } = TasksCore;


// Track which instance owns keyboard focus (only one at a time)
let activeTasksInstance = null;

// Registry of initialized instances. Each embed on the page holds its own
// task array, so after a write in one embed the others would show stale data
// until reload. Every instance registers a `refresh` callback here; a write
// in one instance refreshes the rest.
const tasksInstances = [];
function refreshOtherInstances(exceptId) {
  tasksInstances.forEach(inst => { if (inst.id !== exceptId) inst.refresh(); });
}

// A task list page can stay open for days (phones keep their tabs). Coming back
// to it — the tab shown again, or the page restored from the back/forward
// cache — reloads every embed, so tasks changed elsewhere appear and the
// day sections are computed for today, not the day the page was loaded.
function refreshStaleInstances() {
  tasksInstances.forEach(inst => inst.refreshIfIdle());
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshStaleInstances(); });
window.addEventListener('pageshow', e => { if (e.persisted) refreshStaleInstances(); });

// The edit modal of the last task app started: the editor's Make task and a
// new task from any page open it (`openEditModal`'s arguments).
let openEdit = null;
export function openTasksEdit(taskId, opts) {
  return openEdit?.(taskId, opts);
}

// ── Notes and comments, rendered as pages are ─────────────────────────────
// The server renders them (POST /api/render-notes, render.render_notes: the
// page renderer, so headings, lists, tables, code, wikilinks and task chips
// look as on a page). The HTML is kept per text for the page's life and
// shared by every task list app on it, so each text is fetched once.
const renderedHtml = { notes: new Map(), comments: new Map() };
// Until its HTML is here, a text shows as it is (line breaks kept).
const plainHtml = text => String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;').replace(/\n/g, '<br>');
// Math and diagrams are not kept rendered: renderFigures draws them in
// the live list after each draw (from its caches, so without a flash), and
// a library that failed to load is tried again then.
function highlightCode(html) {
  if (!window.hljs || !html.includes('<pre')) return html;
  const tpl = document.createElement('template');
  tpl.innerHTML = html;
  tpl.content.querySelectorAll(HIGHLIGHT_CODE).forEach(el => hljs.highlightElement(el));
  return tpl.innerHTML;
}
// The notes and comments of `taskList`, for renderTexts.
function textsOf(taskList) {
  return {
    notes: taskList.map(t => t?.description).filter(Boolean),
    comments: taskList.flatMap(t => (t?.comments || []).map(c => c.text)),
  };
}
// Fetch the HTML of the texts not rendered yet; true when there is new.
// Texts another task list app is fetching already are waited for, not asked
// again. A failed request caches nothing: the next render asks again.
const renderInFlight = { notes: new Map(), comments: new Map() };
async function renderTexts({ notes = [], comments = [] }) {
  const want = {}, waits = [];
  for (const [kind, texts] of Object.entries({ notes, comments })) {
    want[kind] = [];
    for (const t of new Set(texts)) {
      if (!t || renderedHtml[kind].has(t)) continue;
      const pending = renderInFlight[kind].get(t);
      if (pending) waits.push(pending);
      else want[kind].push(t);
    }
  }
  let fresh = false;
  if (want.notes.length || want.comments.length) {
    const req = (async () => {
      try {
        const res = await fetch('/api/render-notes', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(want),
        });
        if (!res.ok) return false;
        const out = await res.json();
        for (const kind in want) want[kind].forEach((t, i) => renderedHtml[kind].set(t, highlightCode(out[kind][i])));
        return true;
      } catch (_) {
        return false;
      }
    })();
    for (const kind in want) want[kind].forEach(t => renderInFlight[kind].set(t, req));
    try { fresh = await req; } finally {
      for (const kind in want) want[kind].forEach(t => renderInFlight[kind].delete(t));
    }
  }
  if (waits.length) fresh = (await Promise.all(waits)).some(Boolean) || fresh;
  return fresh;
}

// Start every task list app on the page that hasn't started yet (marked
// data-ready once it has). Returns how many it started.
export function initTaskApps() {
  const apps = [...document.querySelectorAll('.tasks-app:not([data-ready])')];
  apps.forEach(appEl => {
    initTasksInstance(appEl);
    appEl.dataset.ready = '1';
  });
  // Default: a lone instance is active
  if (!activeTasksInstance && tasksInstances.length === 1) activeTasksInstance = tasksInstances[0].id;
  return apps.length;
}

function initTasksInstance(appEl) {
const sfx = appEl.dataset.instance && appEl.dataset.instance !== '0'
  ? '-' + appEl.dataset.instance : '';
const instanceId = appEl.dataset.instance || '0';
// The app's state its modules share (js/task*.js), as st.name; what
// only one part of it uses stays a variable there.
const st = {};

const listEl = document.getElementById('tasks-list' + sfx);
const tabsEl = document.getElementById('tasks-tabs' + sfx);
const addBtn = document.getElementById('task-add-btn' + sfx);
const densityBtn = document.getElementById('task-density-btn' + sfx);
const sortSel = document.getElementById('task-sort' + sfx);
const planSwitch = document.getElementById('task-plan' + sfx);
const filterSearch = document.getElementById('task-filter-search' + sfx);
const completedLink = document.getElementById('task-completed-link' + sfx);
const modal = document.getElementById('task-edit-modal' + sfx);
const form = document.getElementById('task-edit-form' + sfx);
const cancelBtn = document.getElementById('task-edit-cancel' + sfx);
const deleteBtn = document.getElementById('task-edit-delete' + sfx);
const recTypeSelect = document.getElementById('task-edit-rec-type' + sfx);
const keepCb = document.getElementById('task-edit-keep' + sfx);
// With users only: the task is the user's alone.
const privateCb = document.getElementById('task-edit-private' + sfx);
const privateNote = document.getElementById('task-edit-private-note' + sfx);
let privateWas = false;   // as the modal opened: was the task private
const deleteModal = document.getElementById('task-delete-modal' + sfx);
const deleteConfirmBtn = document.getElementById('task-delete-confirm' + sfx);
const deleteCancelBtn = document.getElementById('task-delete-cancel' + sfx);
const deleteTitleEl = document.getElementById('task-delete-title' + sfx);
const deadlineModal = document.getElementById('task-deadline-modal' + sfx);
const deadlineTextEl = document.getElementById('task-deadline-text' + sfx);
const selBar = document.getElementById('task-selbar' + sfx);
// Hidden on the editor page, which only uses its edit modal (Make task).
const headless = appEl.dataset.headless === '1';

if (!listEl) return;

// What the app's modules (js/task*.js) use of it: its elements and
// functions, and as getters what is set further down.
const app = {
  st,
  get listEl() { return listEl; },
  setStart,
  updateSelectionClasses,
  get inSelection() { return inSelection; },
  enterSelectMode,
  setSelected,
  boardDivider,
  clearBoardDropOver,
  dropOnBucket,
  planOnDay,
  get sectionKey() { return sectionKey; },
  applySectionList,
  get OPEN_ORDER() { return OPEN_ORDER; },
  planSectionList,
  sectionDrop,
  get sfx() { return sfx; },
  get instanceId() { return instanceId; },
  get cancelBtn() { return cancelBtn; },
  get deleteModal() { return deleteModal; },
  get deleteConfirmBtn() { return deleteConfirmBtn; },
  get deleteCancelBtn() { return deleteCancelBtn; },
  get deleteTitleEl() { return deleteTitleEl; },
  get deadlineModal() { return deadlineModal; },
  get deadlineTextEl() { return deadlineTextEl; },
  unfoldFor,
  taskList,
  showUndo,
  updateTask,
  completeTasks,
  markWontDo,
  skipOccurrence,
  deleteWithUndo,
  filterTasks,
  runSelectionAction,
  boardBuckets,
  plainTitle,
  openEditModal,
  refreshOtherInstances,
};

st.tasks = [];
let completedLoaded = false;
st.activeView = 'focus';
st.draggedId = null;
st.selectedId  = null;
st.selectedIds = new Set();
st.anchorId    = null;
// Selection mode (⋯ → Select; meant for touch, which has no Shift or Ctrl):
// a tap or click on a card adds or drops it, nothing else on the card reacts.
st.selectMode  = false;
let completedLimit = 50;
let undoTimer  = null;
let undoToastEl = null;
let pendingUndoFn = null;
const pendingCompletions = new Map(); // taskId → timeoutId

// Plan day board: dragging a task onto a day section sets its start_date;
// dropping on "Unplanned" clears it. These list-level
// listeners are attached once and no-op outside the board view. Per-item
// dragstart is (re)wired each render by setupBoardDragAndDrop().
function boardDivider(el) {
  let li = el.closest && el.closest('li');
  while (li && !li.classList.contains('board-day-divider')) li = li.previousElementSibling;
  return li;
}
function clearBoardDropOver() {
  listEl.querySelectorAll('.board-drop-over').forEach(el => el.classList.remove('board-drop-over'));
}
listEl.addEventListener('dragover', e => {
  if (st.activeView !== 'plan' || !st.draggedId) return;
  const div = boardDivider(e.target);
  if (!div || !div.dataset.dropBucket) return;  // non-droppable section
  e.preventDefault();
  if (!div.classList.contains('board-drop-over')) { clearBoardDropOver(); div.classList.add('board-drop-over'); }
});
listEl.addEventListener('drop', e => {
  if (st.activeView !== 'plan' || !st.draggedId) return;
  const div = boardDivider(e.target);
  if (!div || !div.dataset.dropBucket) return;
  e.preventDefault();
  dropOnBucket(st.draggedId, div);
});
// Move a task to a board section: a day sets its start date, Unplanned
// clears it, and Later asks for a date. A drop on the task's own section
// does nothing, except on Later, where it picks another later date, and a
// carried-over task's on Today, which starts it today: committing to it
// again.
function dropOnBucket(id, div) {
  clearBoardDropOver();
  const el = listEl.querySelector(`.task-item[data-id="${CSS.escape(id)}"]`);
  const task = st.tasks.find(t => t.id === id);
  const recommit = div.dataset.dropDay === todayStr() && task && TasksCore.isCarried(task, todayStr());
  if (el && boardDivider(el) === div && div.dataset.dropBucket !== 'later' && !recommit) return;
  if (div.dataset.dropBucket === 'unplanned') updateTask(id, { start_date: null });
  else if (div.dataset.dropBucket === 'later') {
    // "Later" is a range, not a day — ask which date (min: beyond this week).
    openDatePicker('', d => { if (d) planOnDay(id, d); }, div, dateOffset(7));
  }
  else if (div.dataset.dropDay) planOnDay(id, div.dataset.dropDay);
}

// Returns whether the task now starts on `day`.
async function planOnDay(id, day) {
  const task = st.tasks.find(t => t.id === id);
  if (!task || !startsAfterDeadline(task, day)) return updateTask(id, { start_date: day });
  if (!await confirmDeadlineMove(task, day)) return null;
  const old = { start_date: task.start_date ?? null, due_date: task.due_date,
                recurrence: task.recurrence ?? null, history: oldHistory(id) };
  dismissUndo();
  if (!await updateTask(id, { start_date: day, due_date: day })) return null;
  showUndo(`Starts and due ${formatDate(day) === day ? shortDate(day) : formatDate(day)}`, () => updateTask(id, old));
  return true;
}

// A checkbox in a card's notes ("- [ ] milk"), as on a page: ticking it
// saves the notes with the date it was ticked (POST …/toggle-check, the
// Nth box as render_notes numbers them), with an undo toast; undo is a
// second toggle. Toggles are chained, so an undo never overtakes the one
// it reverts.
let noteCheckQueue = Promise.resolve();
function toggleNoteCheck(id, idx) {
  noteCheckQueue = noteCheckQueue.then(async () => {
    const res = await fetch(`/api/tasks/${encodeURIComponent(id)}/toggle-check`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ idx }),
    }).catch(() => null);
    if (!res?.ok) { showNotice('Could not save the checkbox'); render(); return; }
    const updated = await res.json();
    await renderTexts(textsOf([updated]));
    const i = st.tasks.findIndex(t => t.id === id);
    if (i !== -1) st.tasks[i] = updated;
    render();
    refreshOtherInstances(instanceId);
  });
  return noteCheckQueue;
}
listEl.addEventListener('change', e => {
  const cb = e.target.closest('.task-desc .task-list-item-checkbox');
  const item = cb?.closest('.task-item');
  if (!item) return;
  const id = item.dataset.id;
  const idx = [...item.querySelectorAll('.task-desc .task-list-item-checkbox')].indexOf(cb);
  toggleNoteCheck(id, idx);
  const text = (cb.closest('li')?.textContent || '').trim().split('\n')[0];
  const label = text.length > 40 ? text.slice(0, 39) + '…' : text;
  showUndo(`${cb.checked ? 'Checked' : 'Unchecked'} “${label}”`, () => toggleNoteCheck(id, idx));
});
// An image in the notes opens full size, as on a page.
listEl.addEventListener('click', e => {
  const img = e.target.closest('.task-desc img');
  if (img) openLightbox(img.src);
});

// Touch: the first tap on a card selects it (and expands it in compact
// view), a tap on the selected card opens the editor (double-click, the
// desktop gesture, is unreliable on phones). Mouse clicks keep selecting.
st.lastPointerType = 'mouse';
// Whether the press started on the one selected card: the compat mousedown
// that touch fires before the click selects it, so the click can't tell.
let pressedSelected = false;
listEl.addEventListener('pointerdown', e => {
  st.lastPointerType = e.pointerType;
  const pressed = e.target.closest('.task-item');
  pressedSelected = !!pressed && st.selectedIds.size === 1 && st.selectedId === pressed.dataset.id;
  // Under a finger a card is not natively draggable: phone browsers (Chrome
  // on Android, iOS Safari) start their own HTML5 drag on a long press, which
  // cancels the touch drag below mid-gesture, so the first drop was lost.
  // A mouse press turns it back on (hybrid devices).
  const card = e.target.closest('.task-item[data-drag]');
  if (card) card.draggable = e.pointerType !== 'touch';
}, true);
listEl.addEventListener('click', e => {
  const yearBtn = e.target.closest('[data-completed-year]');
  if (yearBtn) setCompletedYear(yearBtn.dataset.completedYear);
  const listBtn = e.target.closest('[data-header-tasks]');
  if (listBtn) { headerTasksMenu(listBtn); return; }
  const fold = e.target.closest('[data-fold]');
  if (fold) toggleFold(fold.dataset.fold);
});
// Selection mode: a tap or click anywhere on a card (its checkbox, a link,
// a date chip too) adds or drops the card, and does nothing else.
listEl.addEventListener('click', e => {
  if (!st.selectMode) return;
  const card = e.target.closest('.task-item');
  if (!card) return;
  e.preventDefault();
  e.stopPropagation();
  toggleSelection(card.dataset.id);
}, true);
listEl.addEventListener('click', e => {
  if (st.lastPointerType !== 'touch') return;
  const card = e.target.closest('.task-item');
  if (!card || e.target.closest('input, button, a, label, .task-clickable-date')) return;
  const id = card.dataset.id;
  if (pressedSelected) { openEditModal(id); return; }
  setSelected(id);
  if (st.compact) expandSelected(id);
});

Object.assign(app, initTaskTouch(app));
const { dropSide, DROP_CLASSES } = app;
// Claim keyboard focus when user interacts with this instance
// (A page with several task lists shows the selection bar of the active one.)
function claimInstance() {
  if (activeTasksInstance === instanceId) return;
  activeTasksInstance = instanceId;
  tasksInstances.forEach(inst => inst.syncSelBar());
}
appEl.addEventListener('pointerdown', claimInstance);
appEl.addEventListener('focusin', claimInstance);

const urlParams = new URLSearchParams(window.location.search);
const initTag = appEl?.dataset.initTag || urlParams.get('tag') || '';
// WIP limit: Focus warns and Plan flags a day at >= LIMIT tasks. Off unless
// set: [[tasks: limit=N]], or ?limit=N (the built-in /tasks page has no embed).
const LIMIT = parseInt(appEl?.dataset.initLimit || urlParams.get('limit'), 10) || null;
// Separate lists: a list is a tag that a page declares with
// [[tasks: list=<tag>]]. That embed (and /tasks?list=<tag>) shows the tasks
// carrying the tag, whose filter chip is locked there (new tasks get it;
// a task's own tags stay editable, so a task can move to another list).
// Elsewhere a list's tag is marked as a list but otherwise a tag: the main
// tasks and tag embeds show list tasks like any other.
const initList = cleanList(appEl?.dataset.initList);
let declaredLists = [];
try { declaredLists = JSON.parse(appEl?.dataset.lists || '[]'); } catch (_) {}
const listSet = new Set(declaredLists);
let listHomes = {};   // list name → its home page's title
try { listHomes = JSON.parse(appEl?.dataset.listHomes || '{}'); } catch (_) {}
// The tooltip of a list's tag (cards, filter and Tags chips): its page.
function listTooltip(name) {
  const n = String(name).replace(/^-?#/, '').toLowerCase();
  const home = listHomes[n];
  return home ? `List: ${home}` : 'List';
}
// The tabs' names: Open/Plan/Focus, or an embed's own (`labels=`, rendered
// into the tab buttons by tasks.py, so they are read back from there).
const tabLabel = view => tabsEl.querySelector(`[data-view="${view === 'plan' ? 'open' : view}"]`)?.textContent.trim() || view;
// [[tasks: keep=1]]: new tasks are keep tasks (a shopping list of staples).
const initKeep = appEl?.dataset.initKeep === '1';
// An embed on a page in the user's private folder: new tasks are private.
const initPrivate = appEl?.dataset.initPrivate === '1';
// Compact: one line per task (title, then tags, markers and dates; notes
// hidden; selecting or a tap expands a task). The default on every tab;
// the density button and v switch it, remembered per task list and tab in this
// browser.
const densityKey = view => 'zone-tasks-compact:' + [location.pathname, appEl?.dataset.initList || '',
  appEl?.dataset.initTag || '', instanceId, view].join('|');
function compactFor(view) {
  try {
    const stored = localStorage.getItem(densityKey(view));
    if (stored !== null) return stored === '1';
  } catch (_) {}
  return true;
}
// Folded Plan sections: a section's header can hide its cards (its count
// and drop target stay). Remembered per task list in this browser, by section
// (`foldId`: a day by its distance from today, so "Tomorrow" stays folded
// as the days go by). A filter shows every section, and a task that has to
// be seen (a ?task= link, a new task, the due list) unfolds its own.
const foldKey = () => 'zone-tasks-folded:' + [location.pathname, appEl?.dataset.initList || '',
  appEl?.dataset.initTag || '', instanceId].join('|');
function loadFolded() {
  try { return new Set(JSON.parse(localStorage.getItem(foldKey()) || '[]')); } catch (_) { return new Set(); }
}
let folded = loadFolded();
function saveFolded() {
  try { localStorage.setItem(foldKey(), JSON.stringify([...folded])); } catch (_) {}
}
function foldId(key, today = todayStr()) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(key)) return key;
  return 'day' + Math.round((new Date(key + 'T00:00:00') - new Date(today + 'T00:00:00')) / 86400000);
}
function toggleFold(id) {
  if (folded.has(id)) folded.delete(id); else folded.add(id);
  saveFolded();
  render();
}
// Unfold the Plan section task `id` sits in; true when that changed anything.
function unfoldFor(id) {
  const task = st.tasks.find(t => t.id === id);
  if (st.activeView !== 'plan' || !task) return false;
  const fid = foldId(TasksCore.bucketKey(task, todayStr()));
  if (!folded.delete(fid)) return false;
  saveFolded();
  render();
  return true;
}

// Open and Plan sorted by deadline or by creation (the Order dropdown; o
// cycles it) instead of the manual order (TasksCore.SORTS). Nothing reorders
// in them: ordering is done back in the manual order. Each tab's sort is
// remembered per task list in this browser.
const SORT_MODES = ['manual', 'due', 'created'];
const SORT_TITLES = { manual: 'the manual order', due: 'by deadline', created: 'newest first' };
const sortKey = () => 'zone-tasks-sort:' + [location.pathname, appEl?.dataset.initList || '',
  appEl?.dataset.initTag || '', instanceId].join('|');
let sortModes = {};   // view → 'due' | 'created'; missing: manual
try { sortModes = JSON.parse(localStorage.getItem(sortKey())) || {}; } catch (_) {}
// The shown tab's sort, when not the manual order.
const activeSort = () => (st.activeView === 'open' || st.activeView === 'plan') && SORTS[sortModes[st.activeView]]
  ? sortModes[st.activeView] : null;
function applySort() {
  if (!sortSel) return;
  sortSel.hidden = st.activeView !== 'open' && st.activeView !== 'plan';
  sortSel.value = activeSort() || 'manual';
}
function setSort(mode) {
  if (st.activeView !== 'open' && st.activeView !== 'plan') return;
  if (SORTS[mode]) sortModes[st.activeView] = mode; else delete sortModes[st.activeView];
  try { localStorage.setItem(sortKey(), JSON.stringify(sortModes)); } catch (_) {}
  applySort();
  render();
}
// o: Manual → Due date → Created → Manual.
function cycleSort() {
  const mode = activeSort() || 'manual';
  setSort(SORT_MODES[(SORT_MODES.indexOf(mode) + 1) % SORT_MODES.length]);
}
sortSel?.addEventListener('change', () => setSort(sortSel.value));

// The Open tab shows every open task, grouped by start date (Plan on: the
// board, view 'plan') or as one list (Plan off: view 'open', Unsorted ·
// Sorted, where the manual order is ranked across everything). Plan was a
// tab of its own until 2026-10; the views keep their names, so `view=plan`
// and `view=open` links still open the one they always did. The Plan
// switch (d) toggles; the choice is remembered per task list in this browser,
// and the Open tab opens it.
const isOpenTab = view => view === 'open' || view === 'plan';
const groupKey = () => 'zone-tasks-group:' + [location.pathname, appEl?.dataset.initList || '',
  appEl?.dataset.initTag || '', instanceId].join('|');
function openGroup() {
  try { return localStorage.getItem(groupKey()) === 'list' ? 'open' : 'plan'; } catch (_) { return 'plan'; }
}
function applyGroup() {
  if (!planSwitch) return;
  planSwitch.hidden = !isOpenTab(st.activeView);
  planSwitch.setAttribute('aria-checked', String(st.activeView === 'plan'));
}
function setGroup(group) {
  if (!isOpenTab(st.activeView)) return;
  try { localStorage.setItem(groupKey(), group); } catch (_) {}
  switchTab(group === 'list' ? 'open' : 'plan');
}
planSwitch?.addEventListener('click', () => setGroup(st.activeView === 'plan' ? 'list' : 'days'));
// The view a tab opens: Open its remembered grouping (unless already on
// one of them: a click on the active tab changes nothing).
const tabView = tab => tab === 'open' && !isOpenTab(st.activeView) ? openGroup() : tab;

st.compact = false;   // the shown tab's (applyCompact)
function applyCompact(view) {
  if (view) {
    st.compact = compactFor(view);
    st.expandedId = null;
    detailsOpen = detailsMode = false;
  }
  appEl?.classList.toggle('tasks-compact', st.compact);
  densityBtn?.setAttribute('aria-pressed', String(st.compact));
  if (densityBtn) {
    const label = st.compact ? 'Full cards' : 'One line per task';
    densityBtn.title = `${label} (v)`;
    densityBtn.setAttribute('aria-label', label);
  }
}
// In compact view one task at a time is expanded: the selected one (a
// click, arrows, j/k; setSelected), or on touch the one tapped first (a tap
// on it then opens the modal), shows its whole title, wrapped, when the
// line cuts it off (titleCut); notes and comments stay hidden. Its details
// (notes, latest comments: the rest of a full card) open on request only
// (detailsOpen): 📝, 💬 or the expand button on the line, or → / l;
// ← / h closes them, Esc closes both. Opened, they stay open while the
// selection moves on (detailsMode): the next selected task shows its
// details too, until they are closed. Enter opens the modal, as on full
// cards.
st.expandedId = null;
let detailsOpen = false;
let detailsMode = false;
// The expand button of a compact line with details: a chevron, turned up
// while they are open.
const EXPAND_ICON = '<svg viewBox="0 0 16 16" width="1em" height="1em" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">' +
  '<path d="M4 6l4 4 4-4"/></svg>';
// ⋯ on cards: three dots drawn, so they sit on the line's centre whatever
// the font (the ⋯ character sat low in some).
const MORE_ICON = '<svg viewBox="0 0 16 16" width="1em" height="1em" aria-hidden="true" fill="currentColor">' +
  '<circle cx="2.5" cy="8" r="1.1"/><circle cx="8" cy="8" r="1.1"/><circle cx="13.5" cy="8" r="1.1"/></svg>';
const cardEl = id => listEl?.querySelector(`.task-item[data-id="${CSS.escape(id)}"]`);
// Whether the line cuts the task's title off (selecting it then shows it
// whole). One whose title fits stays a line.
function titleCut(id) {
  const title = cardEl(id)?.querySelector('.task-content');
  return !!title && title.scrollWidth > title.clientWidth + 1;
}
// Whether the task has details a compact line hides: notes or comments.
const hasDetails = id => !!cardEl(id)?.querySelector('.task-desc, .task-card-comments');
function setExpanded(id, details = false) {
  st.expandedId = id;
  detailsOpen = !!id && details;
  listEl?.querySelectorAll('.task-item').forEach(el => {
    const on = el.dataset.id === id;
    if (!on && el.classList.contains('task-expanded') && st.pressedCard && el !== st.pressedCard) collapseLater(el);
    else {
      el.classList.toggle('task-expanded', on);
      el.classList.toggle('task-details', on && detailsOpen);
      el.querySelector('.task-expand-btn')?.setAttribute('aria-expanded', String(on && detailsOpen));
    }
  });
}
// 📝, 💬 or →/←: open or close a compact task's details, selecting it.
function toggleDetails(id, open = !(st.expandedId === id && detailsOpen)) {
  if (!st.compact || !hasDetails(id)) return false;
  detailsMode = open;
  if (st.selectedIds.size !== 1 || st.selectedId !== id) setSelected(id);
  // Closing keeps the whole title (a short one looks the same as its line).
  setExpanded(id, open);
  return true;
}
// A compact task just selected: its details while they are open on the
// way through (detailsMode), else its whole title if the line cuts it off.
function expandSelected(id) {
  if (detailsMode && hasDetails(id)) setExpanded(id, true);
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
function toggleCompact() {
  st.expandedId = null;
  detailsOpen = detailsMode = false;
  st.compact = !st.compact;
  try { localStorage.setItem(densityKey(st.activeView), st.compact ? '1' : '0'); } catch (_) {}
  applyCompact();
}
densityBtn?.addEventListener('click', toggleCompact);
function cleanList(name) {
  return String(name || '').trim().replace(/^#/, '').trim().toLowerCase()
    .replace(/\s+/g, '-').replace(/^-+|-+$/g, '');
}
const lowerTags = t => (t.tags || []).map(x => x.toLowerCase());
// The separate list a task belongs to (tasks.py task_list): the first A–Z
// of its tags that is a declared list.
function taskList(t) { return lowerTags(t).filter(x => listSet.has(x)).sort()[0] || null; }
function inScope(t) {
  return !initList || lowerTags(t).includes(initList);
}
// The scope of the server's completed counts, matching inScope.
function scopeQuery() {
  return initList ? `?list=${encodeURIComponent(initList)}` : '';
}
// ?view= / [[tasks: view=…]] pick the tab.
const urlView = urlParams.get('view') || '';   // explicit per-navigation override
const embedView = appEl?.dataset.initView || '';   // [[tasks: view=…]] — always wins when specified
// ?new=1 opens the new-task modal in one embed: the Tasks home if the
// page has it (as the New buttons do), else the first.
const newTarget = document.querySelector('.tasks-app[data-home]:not([data-headless])')
  || document.querySelector('.tasks-app:not([data-headless])');
const initNew = appEl?.dataset.initNew === '1' || (urlParams.has('new') && appEl === newTarget);
// The year Completed shows: '' is this year, an earlier one ('2025', from
// ?year= or the year switcher) comes from that year's archive.
const thisYear = String(new Date().getFullYear());
st.completedYear = /^\d{4}$/.test(urlParams.get('year') || '') && urlParams.get('year') !== thisYear
  ? urlParams.get('year') : '';
let completedYears = [];   // years Completed can show (/api/tasks/completed-years)

// Remember the active tab per page + embed instance for the current tab only
// (sessionStorage): a refresh lands on the last-clicked tab, but a fresh load
// in a new tab / next day starts empty so the embed's configured filter loads.
const tabStorageKey = `zone-tasks-tab:${location.pathname}#${instanceId}`;
function saveActiveTab(f) { try { sessionStorage.setItem(tabStorageKey, f); } catch (_) {} }
function loadActiveTab() { try { return sessionStorage.getItem(tabStorageKey); } catch (_) { return null; } }

// ── Undo toast ────────────────────────────────────────────────────────────

function dismissUndo() {
  if (undoTimer) { clearTimeout(undoTimer); undoTimer = null; }
  if (undoToastEl) { undoToastEl.remove(); undoToastEl = null; }
  pendingUndoFn = null;
}

// An action that writes first and arms its undo after the response calls
// dismissUndo() before the write: until its own undo is armed, Ctrl+Z (or
// the old toast's button) would otherwise undo the action before it.
function showUndo(msg, undoFn) {
  dismissUndo();
  pendingUndoFn = undoFn;
  const el = document.createElement('div');
  el.className = 'raw-toast task-undo-toast';
  const span = document.createElement('span');
  span.textContent = msg;
  const btn = document.createElement('button');
  btn.className = 'btn task-undo-btn';
  btn.textContent = 'Undo';
  btn.addEventListener('click', () => { const fn = pendingUndoFn; dismissUndo(); fn?.(); });
  el.appendChild(span);
  el.appendChild(btn);
  placeToast(el);
  undoToastEl = el;
  undoTimer = setTimeout(dismissUndo, 6000);
}

function triggerUndo() {
  if (!pendingUndoFn) return;
  const fn = pendingUndoFn;
  dismissUndo();
  fn();
}

// ── API ────────────────────────────────────────────────────────────────────

// /api/tasks returns only open tasks, so replacing `tasks` drops the
// completed ones: they count as not loaded again (tasksGen tells a completed
// load in flight that its merge target is gone).
let tasksGen = 0;
async function fetchTasks() {
  const res = await fetch('/api/tasks');
  const fetched = (await res.json()).filter(inScope);
  await renderTexts(textsOf(fetched));
  st.tasks = fetched;
  tasksGen++;
  completedLoaded = false;
  render();
  if (st.activeView === 'completed') fetchCompleted();
  refreshCompletedCount();
}

// Completed is not a tab: a link under the list opens it, labelled with the
// number of completed tasks. The count comes from the server because the
// completed tasks themselves are only loaded when the view is opened.
let completedCount = null;
async function refreshCompletedCount() {
  try {
    const res = await fetch('/api/tasks/completed-count' + scopeQuery());
    if (res.ok) { completedCount = (await res.json()).count; syncCompletedLink(); }
  } catch (_) {}
}

// Reload this instance from the server, honoring its current tab. Used to
// pull in changes made by another embed on the same page.
async function refresh() {
  await fetchTasks();
  if (st.activeView === 'completed') await fetchCompleted();
}

let completedLoading = null;
function fetchCompleted() {
  if (completedLoaded) return Promise.resolve();
  // One request at a time: the filter field asks for these on every render.
  return completedLoading ||= loadCompleted().finally(() => { completedLoading = null; });
}
async function loadCompleted() {
  let completed, gen;
  // The page load fetches open and completed tasks at the same time (the
  // filter field, restored by the browser on reload, asks for completed
  // matches). If fetchTasks replaced `tasks` meanwhile, merging into it would
  // be lost: fetch again. This race made the match count flip between
  // reloads.
  loadCompletedYears();
  do {
    gen = tasksGen;
    const res = await fetch('/api/tasks/completed' + (st.completedYear ? `?year=${st.completedYear}` : ''));
    completed = (await res.json()).filter(inScope);
    await renderTexts(textsOf(completed));
  } while (gen !== tasksGen);
  const existingIds = new Set(st.tasks.map(t => t.id));
  for (const t of completed) {
    if (!existingIds.has(t.id)) st.tasks.push(t);
  }
  completedLoaded = true;
  render();
}

async function loadCompletedYears() {
  try {
    const res = await fetch('/api/tasks/completed-years' + scopeQuery());
    if (res.ok) { completedYears = (await res.json()).years; if (st.activeView === 'completed') render(); }
  } catch (_) {}
}

// Show another year in Completed: drop the loaded completed tasks (the
// match count in the other views counts whatever is loaded) and fetch that
// year's. tasksGen tells a load in flight that its year is gone.
function setCompletedYear(year) {
  year = year === thisYear ? '' : year;
  if (year === st.completedYear) return;
  st.completedYear = year;
  st.tasks = st.tasks.filter(t => !t.completed_at);
  tasksGen++;
  completedLoaded = false;
  if (st.activeView === 'completed') fetchCompleted();
  render();
}

async function createTask(data) {
  const res = await fetch('/api/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (res.ok) {
    const task = await res.json();
    await renderTexts(textsOf([task]));
    const msg = leftViewMessage(null, task);
    if (inScope(task)) st.tasks.push(task);
    if (msg && !headless) showNotice(msg);
    render();
    refreshDueBadge(); refreshCompletedCount();
    refreshOtherInstances(instanceId);
    return task;
  }
  await noticeFromResponse(res, 'Could not create task');
  return null;
}

async function updateTask(id, data) {
  const res = await fetch(`/api/tasks/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (res.ok) {
    const updated = await res.json();
    await renderTexts(textsOf([updated]));
    const idx = st.tasks.findIndex(t => t.id === id);
    if (idx !== -1) {
      const before = st.tasks[idx];
      const msg = leftViewMessage(before, updated);
      if (inScope(updated)) st.tasks[idx] = updated;
      else st.tasks.splice(idx, 1);
      if (msg) offerUndoLeave(msg, [[id, editSnapshot(before)]]);
    }
    // Completion/skip/reopen of a recurring task spawns or removes an
    // instance server-side — re-fetch to pick it up.
    if (updated.recurrence && (data.completed || data.skip || 'wont_do' in data)) {
      await fetchTasks();
    } else {
      render();
    }
    refreshDueBadge(); refreshCompletedCount();
    refreshOtherInstances(instanceId);
    return updated;
  }
  await noticeFromResponse(res, 'Update rejected');
  return null;
}

// A task saved out of a list view, into another list or none; say where it went.
function listMovedNotice(task) {
  const name = taskList(task);
  return name ? `Saved to list “${name}”.` : 'Saved; it no longer shows here.';
}

// Why a saved task left this view, or null if it still shows: it went
// into a separate list (or out of this one), or it no longer passes the
// filter (e.g. #sam removed on Sam's page). `before` null: a new task.
function leftViewMessage(before, after) {
  if (!inScope(after)) return listMovedNotice(after);
  const f = parseFilter(filterQuery());
  if ((!before || matchesFilter(before, f)) && !matchesFilter(after, f)) {
    return 'Saved; it no longer shows here.';
  }
  return null;
}

// Everything an edit-modal save can change, as PUT fields: undoing a save
// restores the task as it was, not just the field that moved it. The old
// history goes too, so the undo leaves no trace.
function editSnapshot(t) {
  const rec = t.recurrence;
  return {
    title: t.title, description: t.description || '', tags: t.tags || [],
    due_date: t.due_date || null, start_date: t.start_date || null, keep: !!t.keep,
    rec_type: rec?.type || null, rec_interval: rec?.interval || 1, rec_from: rec?.from || 'due',
    last_done: t.last_done || null, sort_order: t.sort_order ?? null,
    recurrence: rec ?? null, history: t.history || [],
  };
}

// A save took tasks out of this view: say where they went, with an undo
// that PUTs each [id, fields] back. The tasks are no longer in `tasks`, so
// the tasks reloads to bring them back.
function offerUndoLeave(msg, restores) {
  if (headless) return;
  showUndo(msg, async () => {
    for (const [id, fields] of restores) {
      const res = await fetch(`/api/tasks/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(fields),
      });
      if (!res.ok) { await noticeFromResponse(res, 'Undo failed'); break; }
    }
    await fetchTasks();
    refreshDueBadge();
    refreshOtherInstances(instanceId);
  });
}

async function deleteTask(id) {
  const res = await fetch(`/api/tasks/${id}`, { method: 'DELETE' });
  if (res.ok) {
    st.tasks = st.tasks.filter(t => t.id !== id);
    render();
    refreshDueBadge(); refreshCompletedCount();
    refreshOtherInstances(instanceId);
  }
}

async function deleteTasks(ids) {
  const res = await fetch('/api/tasks/batch-delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  });
  if (res.ok) {
    const idSet = new Set(ids);
    st.tasks = st.tasks.filter(t => !idSet.has(t.id));
    render();
    refreshDueBadge(); refreshCompletedCount();
    refreshOtherInstances(instanceId);
  }
}

function beginComplete(id, li) {
  const cb = li?.querySelector('.task-checkbox input');
  if (cb) cb.checked = true;
  if (li) li.classList.add('task-completing');
  if (pendingCompletions.has(id)) clearTimeout(pendingCompletions.get(id));
  const tid = setTimeout(() => {
    pendingCompletions.delete(id);
    // A re-render since the tick replaced the card: fade out the current one.
    li = listEl.querySelector(`.task-item[data-id="${CSS.escape(id)}"]`);
    if (li) li.classList.add('task-completing-out');
    setTimeout(async () => {
      const task = st.tasks.find(t => t.id === id);
      const before = task?.keep && keepSnapshot(task);
      if (before) dismissUndo();
      const done = await updateTask(id, { completed: true });
      // A keep task is not archived, so Completed can't reopen it: offer undo.
      if (done && before) showUndo(`“${plainTitle(task.title)}” done`, () => updateTask(id, before));
    }, 400);
  }, 4600);
  pendingCompletions.set(id, tid);
}

function cancelPendingComplete(id) {
  if (!pendingCompletions.has(id)) return false;
  clearTimeout(pendingCompletions.get(id));
  pendingCompletions.delete(id);
  const li = listEl.querySelector(`.task-item[data-id="${CSS.escape(id)}"]`);
  if (li) li.classList.remove('task-completing', 'task-completing-out');
  const cb = li?.querySelector('.task-checkbox input');
  if (cb) cb.checked = false;
  return true;
}

// What completing a keep task changes (mirrors _keep_done in tasks.py), so
// undo can put it back.
function keepSnapshot(t) {
  return { start_date: t.start_date || null, due_date: t.due_date || null,
           last_done: t.last_done || null, history: t.history || [] };
}

async function completeTasks(ids, completed) {
  const res = await fetch('/api/tasks/batch-complete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids, completed }),
  });
  if (res.ok) {
    // Re-fetch to capture any spawned recurring tasks
    await fetchTasks();
    refreshDueBadge(); refreshCompletedCount();
    refreshOtherInstances(instanceId);
  }
}


async function afterBatchWrite() {
  await fetchTasks();
  refreshDueBadge(); refreshCompletedCount();
  refreshOtherInstances(instanceId);
}

// ── Card actions (shared by buttons, the ⋯ menu and the keyboard) ────────

// Undo sends the task's old history back with the old fields (the server
// replaces what the undone change logged), so the log shows no trace of it.
// An undo that moves a date back also sends the old recurrence, so the
// series keeps its day of month (moving a date re-anchors it).
const oldHistory = id => st.tasks.find(t => t.id === id)?.history || [];

function markWontDo(id) {
  const history = oldHistory(id);
  updateTask(id, { wont_do: true });
  showUndo("Marked won't do", () => updateTask(id, { wont_do: false, history }));
}

function skipOccurrence(id) {
  const history = oldHistory(id);
  updateTask(id, { skip: true });
  showUndo('Occurrence skipped', () => updateTask(id, { wont_do: false, history }));
}

function setStart(id, startDate) {
  const task = st.tasks.find(t => t.id === id);
  if (startsAfterDeadline(task, startDate)) return planOnDay(id, startDate);
  const prev = task?.start_date ?? null;
  const recurrence = task?.recurrence ?? null;
  const history = oldHistory(id);
  updateTask(id, { start_date: startDate });
  const msg = startDate ? `Starts ${formatDate(startDate)}` : 'Start date cleared';
  showUndo(msg, () => updateTask(id, { start_date: prev, recurrence, history }));
}

// Confirm, delete, and offer undo that restores the full snapshots (ids and
// every field). beforeDelete runs after confirmation.
async function deleteWithUndo(ids, beforeDelete) {
  const count = ids.length;
  const title = count === 1 ? plainTitle(st.tasks.find(t => t.id === ids[0])?.title) : '';
  if (!await confirmDelete(title, count)) return;
  const snapshot = ids.map(id => st.tasks.find(t => t.id === id)).filter(Boolean);
  beforeDelete?.();
  dismissUndo();
  await (count === 1 ? deleteTask(ids[0]) : deleteTasks(ids));
  showUndo(count === 1 ? 'Task deleted' : `${count} tasks deleted`, async () => {
    await fetch('/api/tasks/restore', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tasks: snapshot }),
    });
    await afterBatchWrite();
  });
}

// ── ⋯ task menu ────────────────────────────────────────────────────────────
Object.assign(app, initTaskMenu(app));
const { openTaskMenu, headerTasksMenu, postReorder, confirmDelete, confirmDeadlineMove } = app;
function filterTasks(query = filterQuery()) {
  const today = todayStr();
  let filtered = st.tasks;

  // Filter field: text search plus '#tag' words (TasksCore.parseFilter).
  const f = parseFilter(query);
  if (f.text || f.tagGroups.length || f.excludeTags.length) filtered = filtered.filter(t => matchesFilter(t, f));

  // Tab filter
  if (st.activeView === 'focus') {
    filtered = filtered.filter(t => !t.completed_at && isFocus(t, today));
  } else if (st.activeView === 'plan') {
    // Plan is a day board: show all open tasks grouped by start date; tasks
    // without one go to the Unplanned section so they can be dragged into
    // the week (TasksCore.boardBuckets).
    filtered = filtered.filter(t => !t.completed_at);
  } else if (st.activeView === 'completed') {
    filtered = filtered.filter(t => !!t.completed_at);
  } else {
    // Open: every open task. Tasks still waiting for a day are the Plan
    // board's Unplanned section.
    filtered = filtered.filter(t => !t.completed_at);
  }

  return filtered;
}

// ── Sorting ────────────────────────────────────────────────────────────────

// Per-tab sort comparators. Open and Focus sort with TasksCore.byOpenOrder
// in render(), and Plan with TasksCore.boardBuckets.
const TAB_SORT = {
  completed: (a, b) => (b.completed_at || '').localeCompare(a.completed_at || ''),
};

// Open and Focus share one manual order, sort_order: Focus is Open
// filtered to what is in Focus, so both have the same two sections under a
// heading (sectionHTML): Unsorted (no sort_order, newest first) · Sorted
// (sort_order). A move works on the visible list with both headings as
// slots in it (sectionList): the side of the Sorted heading a task lands on
// decides its section. So dropping on that heading moves a task across it,
// and moves within Unsorted change nothing.
const SECTIONS = {
  open:  { field: 'sort_order', top: 'unsorted', bottom: 'sorted' },
  focus: { field: 'sort_order', top: 'unsorted', bottom: 'sorted' },
};
const sectionKey = zone => '#' + zone;  // a heading's slot (task ids never start with #)
// Every task in the manual order, including ones the view or filter hides.
function manualOrderIds(s) {
  return st.tasks.filter(t => !t.completed_at && t[s.field] != null)
    .sort((a, b) => a[s.field] - b[s.field]).map(t => t.id);
}
function sectionList() {
  const s = SECTIONS[st.activeView];
  const byId = new Map(st.tasks.map(t => [t.id, t]));
  const ids = visibleItems().map(el => el.dataset.id);
  const manual = ids.filter(id => byId.get(id)[s.field] != null);
  const fixed = ids.filter(id => byId.get(id)[s.field] == null);
  return [sectionKey(s.top), ...fixed, sectionKey(s.bottom), ...manual];
}
// Save a rearranged sectionList() in which the tasks `moved` changed place
// (TasksCore.placeInOrder: every other task keeps its place, hidden ones too).
function applySectionList(list, moved, s = SECTIONS[st.activeView]) {
  const cut = list.indexOf(sectionKey(s.bottom));
  saveOrder(s, placeInOrder(manualOrderIds(s), list.slice(cut + 1), moved));
}
// Save the manual order `order`; tasks of the old order missing from it
// become Unsorted.
function saveOrder(s, order) {
  const old = manualOrderIds(s);
  const kept = new Set(order);
  const removed = old.filter(id => !kept.has(id));
  if (!removed.length && order.join() === old.join()) return;
  const byId = new Map(st.tasks.map(t => [t.id, t]));
  order.forEach((id, i) => { byId.get(id)[s.field] = i; });
  removed.forEach(id => { byId.get(id)[s.field] = null; });
  postReorder({ order, updates: removed.map(id => ({ id, [s.field]: null })) });
  render();
}

// Plan: every section (`data-ordered`, in the manual order) shows Open's
// order, its Unsorted tasks first under their label, and moves in it follow
// Open's rules: planSectionList is the section as Open lists its sections,
// and applySectionList / sectionKeyMove do the rest.
const OPEN_ORDER = SECTIONS.open;
function planSectionIds(div) {
  const ids = [];
  for (let el = div.nextElementSibling; el && !el.classList.contains('board-day-divider'); el = el.nextElementSibling) {
    if (el.classList.contains('task-item')) ids.push(el.dataset.id);
  }
  return ids;
}
// A Plan section as Open lists its sections, the labels' slots included:
// [#unsorted, …its Unsorted ids, #sorted, …its Sorted ids]. What follows
// #sorted is the section's share of the manual order (applySectionList):
// a task moved there is ranked, one moved above it leaves the order.
function planSectionList(div) {
  const byId = new Map(st.tasks.map(t => [t.id, t]));
  const ids = planSectionIds(div);
  return [sectionKey('unsorted'), ...ids.filter(id => byId.get(id)?.sort_order == null),
          sectionKey('sorted'), ...ids.filter(id => byId.get(id)?.sort_order != null)];
}
// Drop `draggedId` on a task (`key`, `side`: before or after it, by the
// pointer, dropSide) or on a section heading (no side: it takes the
// heading's slot, so dropping on the Sorted heading puts it first there).
function sectionDrop(key, side) {
  const list = sectionList();
  const from = list.indexOf(st.draggedId), at = list.indexOf(key);
  if (from === -1 || at === -1 || from === at) return;
  list.splice(from, 1);
  const to = side ? list.indexOf(key) + (side === 'after' ? 1 : 0) : at;
  list.splice(to, 0, st.draggedId);
  applySectionList(list, [st.draggedId]);
}
// Ctrl+Shift+↑/↓: the selection moves one slot, the headings counting as
// slots, so the top task of Sorted crosses into Unsorted. Unsorted tasks
// can't move within it; moving down takes them straight across the heading.
function sectionKeyMove(direction, list = sectionList(), s = SECTIONS[st.activeView]) {
  const byId = new Map(st.tasks.map(t => [t.id, t]));
  const idxs = list.map((id, i) => st.selectedIds.has(id) ? i : -1).filter(i => i >= 0);
  if (!idxs.length) return;
  const group = idxs.map(i => list[i]);
  const rest = list.filter(id => !st.selectedIds.has(id));
  if (group.every(id => byId.get(id)[s.field] == null)) {
    if (direction === 'up') return;  // away from the manual order
    rest.splice(rest.indexOf(sectionKey(s.bottom)) + 1, 0, ...group);
  } else if (direction === 'up') {
    const above = list[idxs[0] - 1];
    if (above === undefined || above === sectionKey(s.top)) return;
    rest.splice(rest.indexOf(above), 0, ...group);
  } else {
    const below = list[idxs[idxs.length - 1] + 1];
    if (below === undefined) return;
    rest.splice(rest.indexOf(below) + 1, 0, ...group);
  }
  applySectionList(rest, group, s);
}

// A section heading on Open and Focus. It looks like a Plan-board day
// heading and, like one, takes drops: an empty section is only this row.
// On Focus it is the light label of the Open tab's board sections
// (.plan-subdivider), as Focus is a day's list, not a board; it still takes
// drops.
function sectionHTML(zone, label, count) {
  const empty = count ? '' : ' board-day--empty';
  if (st.activeView === 'focus') {
    return `<li class="plan-subdivider list-section${empty}" data-section="${zone}">` +
      `<span>${label}</span><span class="board-day-count">${count}</span></li>`;
  }
  return `<li class="board-day-divider list-section${empty}" data-section="${zone}">` +
    `<span class="board-day-label">${label}</span><span class="board-day-count">${count}</span></li>`;
}

function sortTasks(tasksArr) {
  const fixed = TAB_SORT[st.activeView];
  return fixed ? [...tasksArr].sort(fixed) : [...tasksArr];
}

// ── Bulk tag dialog ─────────────────────────────────────────────────────────
// The tags of several selected tasks at once (t, T, the selection bar's
// Tag…). Each tag the selection carries is a row with a three-state box:
// on every task (✓), on some of them (–, left as it is), on none (it goes).
// A click or tap cycles it, back to "some" only where it started so. The
// field below adds tags: chips, completed from every tag in use. T opens
// it for removing: the field then completes to the selection's own tags
// and turns the completed one off. Enter applies, taking a half-typed tag
// too. One request (batch-tag), and one undo puts back every task's tags.

function openBulkTagModal(ids, removing = false) {
  const idSet = new Set(ids);
  const chosen = st.tasks.filter(t => idSet.has(t.id));
  if (!chosen.length) return;
  const count = chosen.length;
  const rows = selectionTags(chosen);
  const initial = Object.fromEntries(rows.map(r => [r.tag, r.count === count ? 'all' : 'some']));
  const states = { ...initial };

  const dlg = document.createElement('dialog');
  dlg.className = 'modal bulk-tag-modal';
  dlg.innerHTML = `
    <p class="modal-label">Tags of ${count} task${count > 1 ? 's' : ''}</p>
    <ul class="bulk-tag-list"${rows.length ? '' : ' hidden'}></ul>
    <div class="task-field">
      <input type="text" id="bulk-tag-input" autocomplete="off" spellcheck="false"
             aria-label="${removing ? 'Tag to remove' : 'Tags to add'}"
             placeholder="${removing ? 'Tag to remove…' : 'Add tags…'}">
    </div>
    <div class="modal-actions">
      <button type="button" class="btn btn-primary" id="bulk-tag-ok">Apply</button>
      <button type="button" class="btn" id="bulk-tag-cancel">Cancel</button>
    </div>`;
  document.body.appendChild(dlg);

  const list = dlg.querySelector('.bulk-tag-list');
  const ARIA = { all: 'true', some: 'mixed', none: 'false' };
  function drawRows() {
    list.innerHTML = rows.map(r => {
      const isList = listSet.has(r.tag.toLowerCase());
      const tip = isList ? ` title="${esc(listTooltip(r.tag))}"` : '';
      const part = initial[r.tag] === 'some' ? `<span class="bulk-tag-count">${r.count} of ${count}</span>` : '';
      return `<li><button type="button" role="checkbox" class="bulk-tag-row" data-tag="${esc(r.tag)}" aria-checked="${ARIA[states[r.tag]]}">` +
        `<span class="bulk-tag-box" aria-hidden="true"></span>` +
        `<span class="task-tag${isList ? ' task-tag-list' : ''}"${tip}>#${esc(r.tag)}</span>${part}</button></li>`;
    }).join('');
  }
  drawRows();
  // all → none → some (if it started so) → all …
  const nextState = tag => states[tag] === 'all' ? 'none'
    : states[tag] === 'none' && initial[tag] === 'some' ? 'some' : 'all';
  list.addEventListener('click', e => {
    const row = e.target.closest('.bulk-tag-row');
    if (!row) return;
    const tag = row.dataset.tag;
    states[tag] = nextState(tag);
    row.setAttribute('aria-checked', ARIA[states[tag]]);
  });

  const input = dlg.querySelector('#bulk-tag-input');
  const chips = removing ? null
    : chipField(input, { label: t => '#' + t, listWord: t => listSet.has(t.toLowerCase()) });
  // A comma or space makes chips, as in the edit modal's Tags.
  if (chips) chipsOnSeparators(input, chips);
  else noSpacesInTags(input, false);
  const rowOf = tag => rows.find(r => r.tag.toLowerCase() === tag.toLowerCase());
  // Removing: turn the tag's row off.
  function removeTag(tag) {
    const row = rowOf(tag);
    if (!row) return;
    states[row.tag] = 'none';
    list.querySelector(`[data-tag="${CSS.escape(row.tag)}"]`)?.setAttribute('aria-checked', 'false');
  }
  attachTagAc(input, {
    word: () => (input.value.trim() ? { partial: input.value.trim() } : null),
    suggest: w => {
      if (removing) {
        return rankTags(w.partial, Object.fromEntries(
          rows.filter(r => states[r.tag] !== 'none').map(r => [r.tag, r.count])));
      }
      // Adding: any tag in use, but not one every selected task has already.
      const have = new Set([...chips.words(), ...rows.filter(r => states[r.tag] === 'all').map(r => r.tag)]
        .map(t => t.toLowerCase()));
      const counts = tagCounts(st.tasks);
      for (const tag of Object.keys(counts)) if (have.has(tag.toLowerCase())) delete counts[tag];
      return rankTags(w.partial, counts);
    },
    accept: tag => {
      if (removing) removeTag(tag); else chips.add(tag);
      input.value = '';
    },
    onEnter: () => submit(),
  });
  input.addEventListener('keydown', ev => {
    if (ev.key === 'Enter') { ev.preventDefault(); ev.stopPropagation(); submit(); }
  });

  function submit() {
    const typed = cleanTag(input.value);
    if (typed && removing) removeTag(typed);
    const added = removing ? [] : [...chips.words(), typed].filter(Boolean);
    // Only the rows changed: a tag every task has already needn't be sent.
    const changedRows = Object.fromEntries(Object.entries(states).filter(([t, st]) => st !== initial[t]));
    const { add, remove } = bulkTagChange(changedRows, added);
    close();
    if (add.length || remove.length) bulkTag(ids, add, remove);
  }

  function close() {
    if (dlg.open) dlg.close();   // the tag dropdown lives inside dlg and goes with it
  }
  dlg.addEventListener('close', () => dlg.remove());

  dlg.querySelector('#bulk-tag-ok').addEventListener('click', submit);
  dlg.querySelector('#bulk-tag-cancel').addEventListener('click', close);
  dlg.addEventListener('click', ev => { if (ev.target === dlg) close(); });

  dlg.showModal();
  // On a phone with tags to tick, the keyboard waits until the field is tapped.
  if (!(st.lastPointerType === 'touch' && rows.length)) input.focus();
  else dlg.querySelector('.bulk-tag-row')?.focus({ preventScroll: true });
}

// Add and remove tags on the tasks `ids` (batch-tag), with one undo that
// puts back each changed task's tags (batch-update). Tasks the change takes
// out of this view (another list, the filter) say where they went.
async function bulkTag(ids, add, remove) {
  const idSet = new Set(ids);
  const before = new Map(st.tasks.filter(t => idSet.has(t.id)).map(t => [t.id, t]));
  dismissUndo();
  let res;
  try {
    res = await fetch('/api/tasks/batch-tag', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids, add, remove }),
    });
  } catch (_) { showNotice('Could not change the tags'); return; }
  if (!res.ok) { await noticeFromResponse(res, 'Could not change the tags'); return; }
  const updated = await res.json();
  const sameTags = (a, b) => (a || []).join('\n') === (b || []).join('\n');
  const changed = updated.filter(u => !sameTags(before.get(u.id)?.tags, u.tags));
  const gone = [];
  for (const u of updated) {
    const idx = st.tasks.findIndex(t => t.id === u.id);
    if (idx === -1) continue;
    const msg = leftViewMessage(st.tasks[idx], u);
    if (inScope(u)) st.tasks[idx] = u;
    else st.tasks.splice(idx, 1);
    if (msg) gone.push({ msg, list: !inScope(u) && taskList(u) });
  }
  render();
  refreshOtherInstances(instanceId);
  if (!changed.length) return;
  const n = changed.length;
  const what = `${n} task${n > 1 ? 's' : ''}`;
  let msg = add.length === 1 && !remove.length ? `#${add[0]} added to ${what}`
    : remove.length === 1 && !add.length ? `#${remove[0]} removed from ${what}`
    : `Tags changed on ${what}`;
  if (gone.length) {
    const lists = new Set(gone.map(g => g.list));
    const one = lists.size === 1 && [...lists][0];   // all went into one list
    msg = gone.length === 1 ? gone[0].msg
      : one ? `${gone.length} tasks saved to list “${one}”.` : `${gone.length} tasks no longer show here.`;
  }
  showUndo(msg, () => batchUpdate(changed.map(u => ({ id: u.id, tags: before.get(u.id).tags || [] }))));
}

// ── Selection ──────────────────────────────────────────────────────────────
// One selected task (selectedId) or several (selectedIds): a click selects
// one, Shift+click or Shift+↑/↓ a range from the anchor, Ctrl/Cmd+click
// adds or drops one, Ctrl+A takes every task shown. While several are
// selected the selection bar (syncSelBar) and the ⋯ menu of a selected card
// act on all of them (runSelectionAction).

function updateSelectionClasses() {
  listEl.querySelectorAll('.task-item').forEach(el => {
    el.classList.toggle('task-selected', st.selectedIds.has(el.dataset.id));
  });
  // Several selected, none, or another one: the expanded task closes.
  if (st.expandedId && (st.selectedIds.size !== 1 || st.expandedId !== st.selectedId)) setExpanded(null);
  syncSelBar();
}

// Whether `id` is one of several selected tasks.
const inSelection = id => st.selectedIds.size > 1 && st.selectedIds.has(id);

function toggleSelection(id) {
  if (st.selectedIds.has(id)) {
    st.selectedIds.delete(id);
    if (st.selectedId === id) st.selectedId = st.selectedIds.size ? [...st.selectedIds].at(-1) : null;
  } else {
    st.selectedIds.add(id);
    st.selectedId = id;
  }
  st.anchorId = id;   // a Shift+click next extends from here
  updateSelectionClasses();
}

function selectAllVisible() {
  const ids = visibleItems().map(el => el.dataset.id);
  if (!ids.length) return;
  st.selectedIds = new Set(ids);
  if (!st.selectedIds.has(st.selectedId)) st.selectedId = ids[0];
  st.anchorId = st.selectedId;
  updateSelectionClasses();
}

function clearSelection() {
  st.selectedId = st.anchorId = null;
  st.selectedIds = new Set();
  updateSelectionClasses();
}

// Selection mode starts with the card whose menu it came from. It ends with
// ✕, Esc, another tab, Done or Delete, or the browser's back (a phone's
// back gesture): entering adds a history entry, so back leaves the mode,
// not the page.
function enterSelectMode(id) {
  st.selectMode = true;
  appEl.classList.add('tasks-selecting');
  st.selectedId = st.anchorId = id;
  st.selectedIds = new Set([id]);
  updateSelectionClasses();
  try { history.pushState({ ...history.state, tasksSelect: instanceId }, ''); } catch (_) {}
}
function exitSelectMode() {
  if (!st.selectMode) { clearSelection(); return; }
  st.selectMode = false;
  appEl.classList.remove('tasks-selecting');
  clearSelection();
  if (history.state?.tasksSelect === instanceId) history.back();
}
window.addEventListener('popstate', () => { if (st.selectMode) exitSelectMode(); });

// The selection bar: while several tasks are selected (on a page with
// several task lists, only the active one's), how many, and what to do to all.
// Today goes when every selected task is completed, and Done says Reopen.
function syncSelBar() {
  if (!selBar) return;
  const show = (st.selectMode || st.selectedIds.size > 1) &&
    (tasksInstances.length < 2 || activeTasksInstance === instanceId);
  selBar.hidden = !show;
  if (!show) return;
  const allDone = st.tasks.filter(t => st.selectedIds.has(t.id)).every(t => t.completed_at);
  selBar.querySelector('.tasks-selbar-count').textContent =
    st.selectedIds.size ? `${st.selectedIds.size} selected` : 'Tap tasks to select';
  selBar.querySelectorAll('.tasks-selbar-actions .btn').forEach(b => { b.disabled = !st.selectedIds.size; });
  selBar.querySelector('[data-sel="today"]').hidden = allDone;
  const done = selBar.querySelector('[data-sel="done"]');
  done.textContent = allDone ? 'Reopen' : 'Done';
  done.title = allDone ? 'Reopen the selected tasks (Space)' : 'Complete the selected tasks (Space)';
  selBar.querySelector('[data-sel="all"]').disabled =
    visibleItems().every(el => st.selectedIds.has(el.dataset.id));
}
selBar?.addEventListener('click', e => {
  const action = e.target.closest('[data-sel]')?.dataset.sel;
  if (action) runSelectionAction(action);
});

// What the selection bar and a selected card's ⋯ menu do to every selected task.
function runSelectionAction(action) {
  const ids = [...st.selectedIds];
  switch (action) {
    case 'all':    selectAllVisible(); break;
    case 'clear':  exitSelectMode(); break;
    case 'tag':    if (ids.length) openBulkTagModal(ids); break;
    case 'today':  startToday(ids); break;
    case 'done':   if (ids.length) { exitSelectMode(); completeMany(ids); } break;
    case 'delete': if (ids.length) deleteWithUndo(ids, exitSelectMode); break;
  }
}

// Fields of several tasks in one request (each task its own; batch-update),
// then the tasks reloads. False if the server refused.
async function batchUpdate(changes) {
  const res = await fetch('/api/tasks/batch-update', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tasks: changes }),
  });
  if (!res.ok) { await noticeFromResponse(res, 'Update rejected'); return false; }
  await afterBatchWrite();
  return true;
}

// Start the open tasks among `ids` today (those not starting today
// already), with one undo that puts back each one's start date and history.
// Today is never after a deadline that hasn't passed, and an overdue task
// started today is the way to catch up, so no deadline question comes up.
function startToday(ids) {
  return startOn(ids, todayStr());
}

// Start the open tasks among `ids` on `day` (null: unplan them), with one
// undo. A task whose deadline `day` passes asks whether the deadline moves
// too, one by one after the others (planOnDay), as everywhere a start date
// is set. One task alone goes through setStart, which does the same.
async function startOn(ids, day) {
  const idSet = new Set(ids);
  const before = st.tasks.filter(t => idSet.has(t.id) && !t.completed_at && (t.start_date ?? null) !== day);
  if (before.length === 1) return setStart(before[0].id, day);
  const asks = before.filter(t => startsAfterDeadline(t, day));
  const plain = before.filter(t => !asks.includes(t));
  if (plain.length) {
    dismissUndo();
    if (!await batchUpdate(plain.map(t => ({ id: t.id, start_date: day })))) return;
    const n = plain.length;
    const msg = n === 1 ? (day ? `Starts ${formatDate(day)}` : 'Start date cleared')
      : day ? `${n} tasks start ${/^(Today|Tomorrow)$/.test(formatDate(day)) ? formatDate(day).toLowerCase() : formatDate(day)}`
      : `${n} tasks unplanned`;
    showUndo(msg, () =>
      batchUpdate(plain.map(t => ({ id: t.id, start_date: t.start_date ?? null,
                                    recurrence: t.recurrence ?? null, history: t.history || [] }))));
  }
  for (const t of asks) await planOnDay(t.id, day);
}

// Complete the tasks `ids`, or reopen them if every one is completed, with
// an undo. Keep tasks stay open when completed; undo restores their fields
// instead of reopening them from Completed.
async function completeMany(ids) {
  const completing = ids.some(id => { const t = st.tasks.find(tk => tk.id === id); return t && !t.completed_at; });
  const kept = completing
    ? ids.map(id => st.tasks.find(t => t.id === id)).filter(t => t?.keep && !t.completed_at)
         .map(t => [t.id, keepSnapshot(t)])
    : [];
  dismissUndo();
  await completeTasks(ids, completing);
  const label = completing ? `${ids.length} tasks completed` : `${ids.length} tasks reopened`;
  showUndo(label, async () => {
    const keptIds = new Set(kept.map(([id]) => id));
    const others = ids.filter(id => !keptIds.has(id));
    if (kept.length) await Promise.all(kept.map(([id, before]) => putTask(id, before)));
    if (others.length) await completeTasks(others, !completing);
    else await afterBatchWrite();
  });
}

function setSelected(id) {
  st.selectedId = id;
  st.anchorId   = id;
  st.selectedIds = new Set(id ? [id] : []);
  updateSelectionClasses();
  // Compact: the selected task shows its whole title. On touch the tap
  // handler does it, since the compat mousedown selects without the click.
  // Selecting the expanded task again keeps its details as they are.
  if (st.compact && id && st.lastPointerType !== 'touch' && id !== st.expandedId) expandSelected(id);
  // A pressed card is in view; scrolling an expanded one into view would
  // move it from under the pointer.
  if (id && !st.pressedCard) {
    const el = listEl.querySelector(`.task-item[data-id="${CSS.escape(id)}"]`);
    if (el) el.scrollIntoView({ block: 'nearest' });
  }
}

function extendSelection(id) {
  if (!id) return;
  st.selectedId = id;
  const items = visibleItems();
  const aIdx = st.anchorId ? items.findIndex(el => el.dataset.id === st.anchorId) : 0;
  const bIdx = items.findIndex(el => el.dataset.id === id);
  if (bIdx === -1) return;
  const [lo, hi] = aIdx <= bIdx ? [aIdx, bIdx] : [bIdx, aIdx];
  st.selectedIds = new Set(items.slice(lo, hi + 1).map(el => el.dataset.id));
  updateSelectionClasses();
  const el = listEl.querySelector(`.task-item[data-id="${CSS.escape(id)}"]`);
  if (el) el.scrollIntoView({ block: 'nearest' });
}

function visibleItems() {
  return Array.from(listEl.querySelectorAll('.task-item'));
}

// ── Rendering ──────────────────────────────────────────────────────────────

// Day-board grouping lives in TasksCore; bind it to the current date here.
function boardBuckets(list) {
  return TasksCore.boardBuckets(list, todayStr(), activeSort(), DATES);
}

// The HTML of a note or comment (renderedHtml). One not rendered yet (a
// task another task list app or the MCP just changed) shows as plain text, is
// fetched, and the list and the modal's comments redrawn when it is here.
let missingTexts = null;
function htmlOf(kind, text) {
  const html = renderedHtml[kind].get(text);
  if (html !== undefined) return html;
  if (!missingTexts) {
    missingTexts = { notes: [], comments: [] };
    queueMicrotask(async () => {
      const want = missingTexts;
      missingTexts = null;
      if (!await renderTexts(want)) return;
      render();
      if (modal.open && st.commentsTaskId) renderComments(st.tasks.find(t => t.id === st.commentsTaskId));
    });
  }
  missingTexts[kind].push(text);
  return plainHtml(text);
}

function render() {
  syncCompletedLink();

  const filtered = filterTasks();


  // Open and Focus have the same two sections (SECTIONS): Unsorted
  // (newTasks) above Sorted (orderedTasks); Focus is Open filtered. A due
  // task has no section of its own: it keeps its place in the order, and
  // its urgency colour marks it.
  let orderedTasks, newTasks = [];
  if (st.activeView === 'open' && activeSort()) {
    orderedTasks = [...filtered].reverse().sort(SORTS[activeSort()]);
  } else if (SECTIONS[st.activeView]) {
    // TasksCore.byOpenOrder, which the Plan board uses too. Reversed first,
    // so tasks created in the same second (created_at has no finer
    // resolution) keep newest-first.
    const sorted = [...filtered].reverse().sort(byOpenOrder);
    orderedTasks = sorted.filter(t => t.sort_order != null);
    newTasks     = sorted.filter(t => t.sort_order == null);
  } else {
    orderedTasks = sortTasks(filtered);
  }

  if (st.activeView !== 'plan' &&
      orderedTasks.length === 0 && newTasks.length === 0) {
    if (st.selectedIds.size) clearSelection();
    if (st.activeView === 'focus') {
      listEl.innerHTML = '<li class="tasks-empty">Nothing planned for today. ' +
        '<button type="button" class="btn btn-sm" data-goto-tab="plan">Plan your day</button></li>';
      listEl.querySelector('[data-goto-tab]').addEventListener('click', () => switchTab('plan'));
    } else {
      listEl.innerHTML = '<li class="tasks-empty">No tasks here.</li>';
    }
    return;
  }

  const taskHTML = (task, draggable = !!SECTIONS[st.activeView], extraClass = '') => {
    // A task ticked but not yet done stays ticked when another completion
    // re-renders the list.
    const pending = pendingCompletions.has(task.id);
    const checked = task.completed_at || pending ? 'checked' : '';
    const completedClass = (task.completed_at ? 'task-completed' : '') + (task.wont_do ? ' task-wont-do' : '') +
      (pending ? ' task-completing' : '');
    const dueClass = dueDateClass(task);
    const urgClass = urgencyClass(task);
    // A separate list's tag first and marked (icon, tint, tooltip), as in
    // the filter and the modal.
    const isListTag = t => listSet.has(t.toLowerCase());
    const tags = [...(task.tags || []).filter(isListTag), ...(task.tags || []).filter(t => !isListTag(t))]
      .map(t => isListTag(t)
        ? `<span class="task-tag task-tag-list" title="${esc(listTooltip(t))}">#${esc(t)}</span>`
        : `<span class="task-tag">#${esc(t)}</span>`).join('');
    const recIcon = task.recurrence ? '<span class="task-recurring" title="Recurring">↻</span>'
      : task.keep ? `<span class="task-recurring task-keep" title="Keep: back to ${esc(tabLabel('open'))} when ticked off">⟲</span>` : '';
    // A keep task's last completion, e.g. when the milk was last bought.
    const lastDoneDisplay = task.keep && task.last_done && !task.completed_at
      ? `<span class="task-last-done" title="Last done">✓ ${shortDate(task.last_done)}</span>` : '';
    // Comments: a count; hovering shows the latest, a click opens them.
    const comments = task.comments || [];
    const lastComment = comments[comments.length - 1];
    const commentsDisplay = lastComment
      ? `<button type="button" class="task-comments-btn" data-id="${task.id}" title="${esc(commentStamp(lastComment) + ': ' + lastComment.text)}" aria-label="${comments.length} comment${comments.length === 1 ? '' : 's'}">💬 ${comments.length}</button>` : '';
    // A planned task is either in Focus already (start today or earlier) or
    // planned for later; the card's left edge and its 📅 date say which, the
    // same on every tab.
    const planned = !task.completed_at && task.start_date
      ? (task.start_date <= todayStr() ? 'now' : 'later') : '';
    const focusClass = planned ? ` task-planned-${planned}` : '';
    // A past start date reads "since Sep 21" in the plain meta colour, never
    // as a warning, so only today's date is coloured amber.
    const focusMetaClass = planned === 'later' ? 'focus-later'
      : planned && task.start_date === todayStr() ? 'focus-now' : '';
    const dueDisplay = !task.completed_at
      ? `<span class="task-due task-clickable-date ${dueClass}${task.due_date ? '' : ' task-date-empty'}" data-id="${task.id}" data-field="due_date" title="${task.due_date ? 'Click to change due date' : 'Set due date'}">⏰${task.due_date ? ' ' + formatDate(task.due_date) : ''}</span>`
      : (task.due_date ? `<span class="task-due ${dueClass}">⏰ ${formatDate(task.due_date)}</span>` : '');
    // A past start date means "available since", not late — so it reads
    // "since Sep 21" with no warning styling.
    const focusLabel = !task.start_date ? ''
      : (!task.completed_at && task.start_date < todayStr())
        ? `since ${shortDate(task.start_date)}` : formatDate(task.start_date);
    const focusDisplay = !task.completed_at
      ? `<span class="task-date-info task-focus-info task-clickable-date ${focusMetaClass}${task.start_date ? '' : ' task-date-empty'}" data-id="${task.id}" data-field="start_date" title="${task.start_date ? 'Click to change start date' : 'Set start date'}">📅${focusLabel ? ' ' + focusLabel : ''}</span>`
      : (task.start_date ? `<span class="task-date-info task-focus-info ${focusMetaClass}">📅 ${formatDate(task.start_date)}</span>` : '');
    const completedDisplay = task.completed_at
      ? (task.wont_do
          ? `<span class="task-date-info task-wontdo-badge">✗ won't do</span>`
          : `<span class="task-date-info">done: ${task.completed_at.slice(0, 10)}</span>`)
      : '';

    // Marks that there are notes, which a compact line hides; on every card
    // alike, so expanding a line changes nothing on it.
    const notesDisplay = task.description?.trim()
      ? `<button type="button" class="task-has-notes" data-id="${task.id}" title="Has notes" aria-label="Has notes">📝</button>` : '';
    const skipBtnHtml = (!task.completed_at && task.recurrence)
      ? `<button class="task-skip-btn" data-id="${task.id}" title="Skip this occurrence (series continues)">⏭</button>`
      : '';
    const tagsRight = tags ? `<span class="task-tags-right">${tags}</span>` : '';
    const privateDisplay = task.private
      ? '<span class="task-private" title="Private: only you see it" aria-label="Private">🔒</span>' : '';
    const hasRealMeta = !!(tags || task.due_date || task.start_date || task.recurrence ||
      task.keep || task.private || task.completed_at || commentsDisplay || notesDisplay);
    const hasMeta = !task.completed_at || dueDisplay || focusDisplay || completedDisplay || recIcon || privateDisplay || tagsRight || notesDisplay;
    const metaClass = (hasMeta && !hasRealMeta) ? 'task-meta task-meta--hover-only' : 'task-meta';
    const metaHtml = hasMeta ? `<div class="${metaClass}">${tagsRight}<span class="task-meta-right">${skipBtnHtml}${privateDisplay}${recIcon}${notesDisplay}${commentsDisplay}${lastDoneDisplay}${dueDisplay}${focusDisplay}${completedDisplay}</span></div>` : '';
    const descHtml = task.description ? `<div class="task-desc page-content">${htmlOf('notes', task.description)}</div>` : '';
    // The latest comments, newest first: shown only when a compact task is
    // expanded (full cards have the 💬 count; the modal has them all).
    const earlier = comments.length - 3;
    const commentsHtml = comments.length
      ? `<div class="task-card-comments">${comments.slice(-3).reverse().map(c =>
          `<p><span class="task-comment-when">${esc(commentStamp(c))}</span> ${htmlOf('comments', c.text)}</p>`).join('')}` +
        (earlier > 0 ? `<button type="button" class="task-comments-more" data-id="${task.id}">${earlier} earlier…</button>` : '') + '</div>'
      : '';
    const dragAttr = draggable ? 'draggable="true" data-drag' : '';
    // Compact lines with details get the expand button (CSS hides it on
    // full cards, which show them).
    const detailsShown = st.compact && task.id === st.expandedId && detailsOpen;
    const expandBtn = descHtml || commentsHtml
      ? `<button type="button" class="task-expand-btn" data-id="${task.id}" aria-expanded="${detailsShown}" aria-label="Notes and comments" title="Notes and comments (l / h)">${EXPAND_ICON}</button>` : '';
    return `
      <li class="task-item ${completedClass} ${urgClass}${focusClass} ${extraClass}${st.compact && task.id === st.expandedId ? ' task-expanded' + (detailsOpen ? ' task-details' : '') : ''}" data-id="${task.id}" ${dragAttr}>
        <label class="task-checkbox">
          <input type="checkbox" ${checked} data-id="${task.id}">
        </label>
        <div class="task-content" data-id="${task.id}">
          <span class="task-title" data-id="${task.id}">${linkifyTitle(task.title)}</span>
        </div>
        <div class="task-actions">
          ${expandBtn}<button type="button" class="task-menu-btn" data-id="${task.id}" aria-label="More actions" aria-haspopup="menu" title="More actions">${MORE_ICON}</button>
        </div>
        ${metaHtml}${descHtml}${commentsHtml}
      </li>`;
  };

  let html = '';
  let truncated = false;

  // Upcoming day board: one section per day, dividers double as drop targets.
  if (st.activeView === 'plan') {
    const filtering = userFiltering();
    const today = todayStr();
    html = boardBuckets(filtered).map(s => {
      // A section with cards folds from its header (not while filtering:
      // a match must never hide in a folded section).
      const fid = foldId(s.key, today);
      const foldable = s.tasks.length && !filtering;
      const isFolded = foldable && folded.has(fid);
      const over = LIMIT && s.date && s.tasks.length >= LIMIT;
      const overCls = over ? ' board-day--over' : '';
      const overIcon = over
        ? `<span class="board-day-over-icon" title="Over the limit (${s.tasks.length}/${LIMIT})">⚠</span>`
        : '';
      const dropAttr = (s.droppable ? ` data-drop-bucket="${s.key}" data-drop-day="${s.date || ''}"` : '') +
        (s.ordered ? ' data-ordered="1"' : '');
      const emptyCls = (s.tasks.length ? '' : ' board-day--empty') + (isFolded ? ' board-day--folded' : '');
      // The deadlines on this day, wherever their cards sit, Unplanned's
      // overdue tasks (red) and Today's carried-over ones (amber; both shown
      // even folded): a click lists them (headerTasksMenu), the tooltip
      // names them.
      const listBtn = (list, which, title, cls = '') => list?.length
        ? `<button type="button" class="board-day-due${cls}" data-header-tasks="${esc(s.key)}" data-header-list="${which}"` +
          ` title="${esc(title + ': ' + list.map(t => plainTitle(t.title)).join(', '))}">${list.length} ${which}</button>`
        : '';
      const dueBtn = listBtn(s.carried, 'carried', 'Carried over, on Focus since an earlier day', ' board-day-carried') +
        listBtn(s.due, 'due', `Due ${s.label}`) +
        listBtn(s.overdue, 'overdue', 'Overdue', ' board-day-overdue');
      // The whole header folds the section (the label is its button, for
      // the keyboard and screen readers); only the due/overdue count does its own.
      const foldAttr = foldable ? ` data-fold="${esc(fid)}"` : '';
      const header = `<li class="board-day-divider${overCls}${emptyCls}${foldable ? ' board-day--foldable' : ''}"${dropAttr}${foldAttr}>` +
        `<span class="board-day-label">` + (foldable
          ? `<button type="button" class="board-day-fold" data-fold="${esc(fid)}" aria-expanded="${!isFolded}"` +
            ` title="${isFolded ? 'Show' : 'Hide'} these tasks">${esc(s.label)}</button>`
          : esc(s.label)) + `${dueBtn}</span>` +
        `${overIcon}<span class="board-day-count">${s.tasks.length}</span></li>`;
      // An empty section is just its header row, which is itself the drop
      // target (styled tall enough to hit). Nothing grows when a drag
      // starts, so the section you aim at never moves under the pointer.
      // A section with Unsorted tasks (listed first, in the manual order)
      // labels them and the Sorted ones below, as Open's sections do, one
      // level down: so you see what is still to be ranked, and moves follow
      // Open's rules (planSectionList). The labels are no sections
      // (.plan-subdivider, not .board-day-divider); Sorted takes drops, as
      // Open's heading does. No Unsorted task: no labels.
      const unsorted = s.ordered ? s.tasks.filter(t => t.sort_order == null).length : 0;
      const sub = (label, n) => `<li class="plan-subdivider" data-sub="${label.toLowerCase()}">` +
        `<span>${label}</span><span class="board-day-count">${n}</span></li>`;
      // A carried-over card's "since" badge is amber here (task-carried):
      // on Today it is a slipped plan, not just an available task.
      const body = isFolded ? '' : s.tasks.map((t, i) =>
        (unsorted && i === 0 ? sub('Unsorted', unsorted) : '') +
        (unsorted && i === unsorted ? sub('Sorted', s.tasks.length - unsorted) : '') +
        taskHTML(t, true, 'board-item' + (TasksCore.isCarried(t, today) ? ' task-carried' : ''))).join('');
      return header + body;
    }).join('');
    listEl.innerHTML = html;
    renderFigures(listEl);
    wireListHandlers();
    return;
  }

  if (st.activeView === 'focus') {
    const inFocus = orderedTasks.length + newTasks.length;
    if (LIMIT && inFocus >= LIMIT) {
      html += `<li class="focus-stale-bar focus-over-limit">` +
        `<span>⚠ ${inFocus} tasks in ${esc(tabLabel('focus'))} (limit: ${LIMIT})</span></li>`;
    }
  }
  // No tab is highlighted in Completed, so the list says where you are.
  if (st.activeView === 'completed') {
    // Earlier years (the yearly archives) are one click away.
    const shown = st.completedYear || thisYear;
    const years = completedYears.includes(shown) ? completedYears : [...completedYears, shown].sort().reverse();
    const yearBtns = years.length > 1
      ? `<span class="completed-years">${years.map(y =>
          `<button type="button" data-completed-year="${y}" aria-pressed="${y === shown}">${y}</button>`).join('')}</span>`
      : '';
    html += `<li class="task-inbox-divider task-inbox-divider--only completed-heading">Completed${yearBtns}</li>`;
  }
  // Limit completed tab display
  let displayTasks = orderedTasks;
  if (st.activeView === 'completed' && orderedTasks.length > completedLimit) {
    displayTasks = orderedTasks.slice(0, completedLimit);
    truncated = true;
  }
  // Open sorted: by deadline the tasks with one, then those without, each
  // under a heading; newest first under one. Not drop targets: nothing
  // drags here.
  if (st.activeView === 'open' && activeSort()) {
    const heading = (label, n) => n ? `<li class="board-day-divider sort-heading">` +
      `<span class="board-day-label">${label}</span><span class="board-day-count">${n}</span></li>` : '';
    const group = (label, tasks) => heading(label, tasks.length) + tasks.map(t => taskHTML(t, false)).join('');
    html += activeSort() === 'due'
      ? group('By deadline', displayTasks.filter(t => t.due_date)) + group('No deadline', displayTasks.filter(t => !t.due_date))
      : group('Newest first', displayTasks);
    displayTasks = [];
  }
  // Open and Focus: both sections always have a heading, even when empty,
  // because the heading is where a task is dropped to enter an empty section.
  else if (SECTIONS[st.activeView]) {
    html += sectionHTML('unsorted', 'Unsorted', newTasks.length);
    html += newTasks.map(t => taskHTML(t, undefined, 'task-inbox')).join('');
    html += sectionHTML('sorted', 'Sorted', orderedTasks.length);
  }
  html += displayTasks.map(t => taskHTML(t)).join('');
  if (truncated) {
    html += `<li class="tasks-show-more"><button class="btn" id="tasks-show-more-btn">Show more (${orderedTasks.length - completedLimit} remaining)</button></li>`;
  }
  listEl.innerHTML = html;
  renderFigures(listEl);
  wireListHandlers();

  function wireListHandlers() {
  listEl.querySelectorAll('.task-menu-btn').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      openTaskMenu(btn.dataset.id, btn);
    });
  });
  // 💬: a compact line's toggles its details (the latest comments among
  // them); a full card shows them already, so there it opens the modal at
  // the comments.
  listEl.querySelectorAll('.task-comments-btn').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      if (!toggleDetails(btn.dataset.id)) openEditModal(btn.dataset.id, { focusComment: true });
    });
  });
  listEl.querySelectorAll('.task-expand-btn').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      toggleDetails(btn.dataset.id);
    });
  });
  // 📝: a compact line's toggles its details; a full card shows the notes,
  // so there it only selects the card.
  listEl.querySelectorAll('.task-has-notes').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      if (!toggleDetails(btn.dataset.id)) setSelected(btn.dataset.id);
    });
  });

  // "Show more" button
  const showMoreBtn = listEl.querySelector('#tasks-show-more-btn');
  if (showMoreBtn) {
    showMoreBtn.addEventListener('click', () => {
      completedLimit += 50;
      render();
    });
  }

  // Attach event listeners
  listEl.querySelectorAll('.task-checkbox input').forEach(cb => {
    cb.addEventListener('change', e => {
      const id = e.target.dataset.id;
      const completed = e.target.checked;
      const task = st.tasks.find(t => t.id === id);
      if (completed) {
        beginComplete(id, e.target.closest('.task-item'));
      } else if (!cancelPendingComplete(id)) {
        updateTask(id, { completed: false });
        if (task) showUndo('Task reopened', () => completeTasks([id], true));
      }
    });
  });

  listEl.querySelectorAll('.task-clickable-date').forEach(el => {
    el.addEventListener('click', e => {
      e.stopPropagation();
      const id = el.dataset.id;
      const field = el.dataset.field;
      const task = st.tasks.find(t => t.id === id);
      if (!task) return;
      openDatePicker(task[field] || '', d => {
        if (field === 'start_date' && startsAfterDeadline(task, d)) planOnDay(id, d);
        else updateTask(id, { [field]: d });
      }, el, todayStr());
    });
  });

  listEl.querySelectorAll('.task-comments-more').forEach(btn => {
    btn.addEventListener('click', e => { e.stopPropagation(); openEditModal(btn.dataset.id, { focusComment: true }); });
  });

  listEl.querySelectorAll('.task-skip-btn').forEach(btn => {
    btn.addEventListener('click', e => { e.stopPropagation(); skipOccurrence(btn.dataset.id); });
  });


  // Double-click anywhere on a card, title and notes included, opens the
  // modal; only its controls keep their own.
  listEl.querySelectorAll('.task-item').forEach(el => {
    el.addEventListener('dblclick', e => {
      // Touch: two taps select and open (the tap handler), never more.
      if (st.lastPointerType === 'touch' || st.selectMode) return;
      if (e.target.closest('input, button, a, label, .task-clickable-date')) return;
      window.getSelection()?.removeAllRanges();
      openEditModal(el.dataset.id);
    });
  });

  // Drag and drop
  st.touchDrop = null;
  if (st.activeView === 'plan') {
    setupBoardDragAndDrop();
  } else if (SECTIONS[st.activeView] && !activeSort()) {
    setupDragAndDrop();
  }

  // Restore or clear selection after re-render
  if (st.selectedIds.size) {
    const visSet = new Set(visibleItems().map(el => el.dataset.id));
    st.selectedIds = new Set([...st.selectedIds].filter(id => visSet.has(id)));
    if (!visSet.has(st.selectedId)) st.selectedId = st.selectedIds.size ? [...st.selectedIds][0] : null;
    if (!visSet.has(st.anchorId))   st.anchorId = st.selectedId;
    updateSelectionClasses();
    if (st.selectedId) {
      const el = listEl.querySelector(`.task-item[data-id="${CSS.escape(st.selectedId)}"]`);
      if (el) el.scrollIntoView({ block: 'nearest' });
    }
  }

  // Click to select (clicks on checkbox/button/link are handled separately):
  // Shift+click a range from the anchor, Ctrl/Cmd+click one more or less.
  // A right-click on a selected card keeps the selection for its menu.
  listEl.querySelectorAll('.task-item').forEach(el => {
    el.addEventListener('mousedown', e => {
      if (st.selectMode || e.target.closest('input, button, a, .task-checkbox')) return;   // selecting: the click toggles
      const id = el.dataset.id;
      if (e.button !== 0 && st.selectedIds.has(id)) return;
      if (e.shiftKey && st.selectedId) { e.preventDefault(); extendSelection(id); }
      else if (e.ctrlKey || e.metaKey) { e.preventDefault(); toggleSelection(id); }
      else {
        st.pressedCard = el;
        try { setSelected(id); } finally { st.pressedCard = null; }
      }
    });
  });
  }  // end wireListHandlers
}

// A link to another site opens in a new tab (as render.py _is_external
// marks them in pages); a full URL to Zone itself stays.
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
function linkifyTitle(text) {
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
function plainTitle(text) {
  return String(text || '').replace(TITLE_LINK_RE, (all, wiki, label, url, bare) =>
    wiki !== undefined ? wikilinkHref(wiki).label : url !== undefined ? (label || url) : bare);
}


// Page completion in the title ([[ and [text](, linkAutocomplete in
// js/completion.js, shared with the editor).
const titleInput = document.getElementById('task-edit-title' + sfx);
const descTextarea = document.getElementById('task-edit-desc' + sfx);
if (titleInput) {
  // A private task's title may link private pages, a shared one's not.
  linkAutocomplete(titleInput, { page: () => privateCb?.checked ? 'private/task' : '' });
  // Pasting a URL asks for its name, as in the notes and the editor
  // (pasteUrlAsLink): [name](url), shown as a link on the card.
  titleInput.addEventListener('paste', e => pasteUrlAsLink(titleInput, e));
}

// The notes are edited as a page is (js/mdedit.js): the formatting bar,
// completion, snippets, lists, file paste and drop. Tab stays the key to the
// next field (it still expands a snippet). Edit | Preview (or F2) switches
// the field for the notes as the card will show them, in the field's place
// and at its height, so the modal keeps its layout; it opens in Edit.
const notesPreview = document.getElementById('task-edit-preview' + sfx);
const notesBar = document.getElementById('task-edit-format' + sfx);
const notesModeBtns = [...modal.querySelectorAll('[data-notes-mode]')];
async function fetchNotesPreview() {
  const text = descTextarea.value.trim();
  if (!text) { notesPreview.innerHTML = '<p class="task-notes-empty">Nothing to preview</p>'; return; }
  try {
    const res = await fetch('/api/render-notes', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ notes: [text] }),
    });
    if (res.ok && descTextarea.value.trim() === text) {
      notesPreview.innerHTML = highlightCode((await res.json()).notes[0]);
      renderFigures(notesPreview);
    }
  } catch (_) { notesPreview.textContent = 'Could not render the preview'; }
}
const previewing = () => !!notesPreview && !notesPreview.hidden;
function setNotesPreview(show, focus = true) {
  if (!notesPreview) return;
  // The bar's and the field's space together, so nothing below moves.
  if (show) {
    const top = (notesBar || descTextarea).getBoundingClientRect().top;
    notesPreview.style.height = `${descTextarea.getBoundingClientRect().bottom - top}px`;
  }
  notesPreview.hidden = !show;
  descTextarea.hidden = show;
  if (notesBar) notesBar.hidden = show;
  notesModeBtns.forEach(b => b.setAttribute('aria-pressed', String((b.dataset.notesMode === 'preview') === show)));
  // Focus follows: the hidden field would drop it out of the modal, whose
  // keys (F2 back, Esc) then wouldn't reach it. Arrows scroll the preview.
  if (show) { notesPreview.innerHTML = ''; fetchNotesPreview(); if (focus) notesPreview.focus({ preventScroll: true }); }
  else if (focus) descTextarea.focus();
}
notesModeBtns.forEach(b => b.addEventListener('click', () => setNotesPreview(b.dataset.notesMode === 'preview')));
// Ticking a box in the preview ticks the item in the text, recording when
// as on the card (the field is hidden, so not through its undo; unticking
// undoes it); the preview redraws with the date chip, keeping the focus.
notesPreview?.addEventListener('change', async e => {
  if (!e.target.matches('.task-list-item-checkbox')) return;
  const idx = [...notesPreview.querySelectorAll('.task-list-item-checkbox')].indexOf(e.target);
  descTextarea.value = toggleCheckboxText(descTextarea.value, idx, false);
  await fetchNotesPreview();
  notesPreview.focus({ preventScroll: true });
});
if (descTextarea) {
  // A private task's files go where a private page's do (the user's folder).
  const privateDir = appEl?.dataset.privateFolder;
  markdownEditor(descTextarea, { bar: notesBar, attachInput: document.getElementById('task-edit-attach' + sfx),
                                 tabIndents: false, page: () => privateCb?.checked ? privateDir + '/task' : '' });
}

// ── Tabs ───────────────────────────────────────────────────────────────────
// Segmented control: one button per view, data-view = the view's name.

const tabButtons = () => Array.from(tabsEl.querySelectorAll('[data-view]'));
const VIEWS = ['open', 'plan', 'focus', 'completed'];
// The tab to return to from Completed (the link's "back" state).
let lastTab = 'focus';

// While the filter field is in use, the link counts the completed tasks it
// matches ("✓ 2 matches in Completed →"), so a search that comes up empty
// here still shows where the task went. That needs the completed tasks,
// which are otherwise only loaded when the view is opened. The unfiltered
// total is never shown while filtering: it would read as the number of
// completed tasks the filter leaves.
function syncCompletedLink() {
  if (!completedLink) return;
  let matches = null;
  const f = parseFilter(filterQuery());
  const filtering = st.activeView !== 'completed' && (f.text || f.tagGroups.length || f.excludeTags.length);
  if (filtering) {
    if (!completedLoaded) { fetchCompleted(); }
    else {
      matches = st.tasks.filter(t => t.completed_at && matchesFilter(t, f)).length;
    }
  }
  completedLink.classList.toggle('has-matches', matches > 0);
  completedLink.textContent = st.activeView === 'completed' ? `← Back to ${tabLabel(lastTab)}`
    : matches ? `✓ ${matches} ${matches === 1 ? 'match' : 'matches'} in Completed →`
    : matches === 0 ? '✓ No matches in Completed →'
    : filtering ? '✓ Completed →'
    : `✓ Completed${completedCount != null ? ` (${completedCount})` : ''} →`;
}

// Mark the view's button active (no side effects — used during init too).
function markActiveTab(view) {
  const tab = view === 'plan' ? 'open' : view;   // Open grouped by day
  tabButtons().forEach(b => {
    const on = b.dataset.view === tab;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  });
}

function switchTab(view) {
  if (!VIEWS.includes(view)) return;
  if (st.selectMode) exitSelectMode();
  if (view !== 'completed') lastTab = view;
  st.activeView = view;
  applyCompact(view);
  applySort();
  applyGroup();
  // Back from an earlier year: the other views count this year's matches.
  if (view !== 'completed' && st.completedYear) setCompletedYear('');
  markActiveTab(view);   // Completed has no tab: none is marked
  syncCompletedLink();
  // Keyboard focus follows the active view (Tab-cycling from a focused tab
  // would otherwise leave the focus ring on the previous one).
  if (tabsEl.contains(document.activeElement)) {
    tabsEl.querySelector(`[data-view="${view === 'plan' ? 'open' : view}"]`)?.focus();
  }
  saveActiveTab(st.activeView);
  if (st.activeView === 'completed') fetchCompleted();
  render();
}

tabsEl.addEventListener('click', e => {
  const btn = e.target.closest('[data-view]');
  if (btn) switchTab(tabView(btn.dataset.view));
});
completedLink?.addEventListener('click', () => {
  switchTab(st.activeView === 'completed' ? lastTab : 'completed');
  if (st.activeView === 'completed') tabsEl.scrollIntoView({ block: 'nearest' });
});

// The filter: its chips (completed tag words) plus the text typed after them.
// The embed's own filter words (list= and tag=) are locked: the page says
// what it shows. A URL ?tag= (e.g. g t) gives ordinary chips.
const embedFilterWords = [
  ...(initList ? ['#' + initList] : []),
  ...tagParamToQuery(appEl?.dataset.initTag || '').split(' ').filter(Boolean),
];
// A chip that is just a separate list's tag (#groceries) is marked as a list.
const filterChips = filterSearch && chipField(filterSearch, {
  onChange: () => render(), locked: embedFilterWords,
  listWord: w => w.startsWith('#') && listSet.has(w.slice(1).toLowerCase()) });
function filterQuery() {
  return [...(filterChips?.words() || []), filterSearch?.value || ''].join(' ').trim();
}
// Whether the viewer filters: words beyond the embed's locked ones (a
// list= tasks always has its list's tag in the field, and isn't filtered).
function userFiltering() {
  const locked = new Set(embedFilterWords.map(w => w.toLowerCase()));
  return (filterChips?.words() || []).some(w => !locked.has(w.toLowerCase())) || !!filterSearch?.value.trim();
}

if (filterSearch) {
  // Debounced — render() rebuilds the whole list, too heavy per keystroke.
  let searchTimer = null;
  filterSearch.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(render, 150);
  });
  // Esc leaves the field (an open tag dropdown takes the first Esc).
  filterSearch.addEventListener('keydown', e => {
    if (e.key === 'Escape') { e.preventDefault(); filterSearch.blur(); }
  });
}

// ── Tag chips ──────────────────────────────────────────────────────────────
// A completed tag becomes a chip in front of the field's text: it can't be
// edited, only removed (its × or Backspace at the start of the field). The
// input is wrapped in a .chip-input box that looks like the field. Used by
// the filter field (chips are filter words: #a, -#b, #a|b) and the edit
// modal's Tags field (chips are tag names, shown as #name). `locked` words
// (an embed's filter words, case ignored) are always there and have no ×.
// `listWord(w)` marks a separate list's tag: such chips come first, with a
// list icon, a tint and a tooltip naming the list's page.
function chipField(input, { label = w => w, onChange, locked = [], listWord = () => false } = {}) {
  const lockedSet = new Set(locked.map(w => w.toLowerCase()));
  const isLocked = w => lockedSet.has(w.toLowerCase());
  const box = document.createElement('div');
  box.className = 'chip-input';
  input.parentNode.insertBefore(box, input);
  box.appendChild(input);
  let words = [];
  function draw() {
    box.querySelectorAll('.tag-chip').forEach(c => c.remove());
    words.forEach((w, i) => {
      const chip = document.createElement('span');
      chip.className = 'tag-chip';
      const text = document.createElement('span');
      text.className = 'tag-chip-label';
      text.textContent = label(w);
      chip.appendChild(text);
      const isList = listWord(w);
      if (isList) {
        chip.classList.add('tag-chip-list');
        chip.title = listTooltip(w);
      }
      if (isLocked(w)) {
        chip.classList.add('tag-chip-locked');
        if (!isList) chip.title = 'Set by this page';
        box.insertBefore(chip, input);
        return;
      }
      const x = document.createElement('button');
      x.type = 'button';
      x.className = 'tag-chip-x';
      x.tabIndex = -1;
      x.textContent = '×';
      x.setAttribute('aria-label', `Remove ${label(w)}`);
      x.addEventListener('mousedown', e => e.preventDefault());   // keep the caret in the field
      x.addEventListener('click', e => { e.stopPropagation(); remove(i); input.focus(); });
      chip.appendChild(x);
      box.insertBefore(chip, input);
    });
  }
  function set(list, silent) {
    const seen = new Set();
    words = list.filter(w => w && !seen.has(w.toLowerCase()) && seen.add(w.toLowerCase()));
    words.unshift(...locked.filter(w => !seen.has(w.toLowerCase())));
    words = [...words.filter(w => listWord(w)), ...words.filter(w => !listWord(w))];
    draw();
    if (!silent) onChange?.();
  }
  function remove(i) {
    if (i < 0 || isLocked(words[i])) return;
    words.splice(i, 1); draw(); onChange?.();
  }
  input.addEventListener('keydown', e => {
    if (e.key === 'Backspace' && !e.defaultPrevented &&
        input.selectionStart === 0 && input.selectionEnd === 0) {
      const i = words.findLastIndex(w => !isLocked(w));
      if (i === -1) return;
      e.preventDefault();
      remove(i);
    }
  });
  box.addEventListener('mousedown', e => {
    if (e.target === box) { e.preventDefault(); input.focus(); }
  });
  return { words: () => [...words], add: w => set([...words, w]), set };
}

// ── Tag autocomplete ───────────────────────────────────────────────────────

// Filter field: tags of the tasks the *other* tag words leave visible in
// this tab, minus those already used. Free text is ignored: it is usually
// still being typed ("some text #ta…") and would otherwise hide every tag.
if (filterSearch) attachTagAc(filterSearch, {
  word: () => hashWordAtCaret(filterSearch),
  suggest: w => {
    const val = filterSearch.value;
    const rest = [...filterChips.words(), val.slice(0, w.start) + val.slice(w.end)].join(' ');
    const otherTags = rest.split(/\s+/).filter(word => /^-?#/.test(word)).join(' ');
    const restFilter = parseFilter(rest);
    const used = [...restFilter.tagGroups.flat(), ...restFilter.excludeTags];
    const counts = tagCounts(filterTasks(otherTags));
    for (const tag of Object.keys(counts)) if (used.includes(tag.toLowerCase())) delete counts[tag];
    return rankTags(w.partial, counts, w.partial ? 8 : 12);
  },
  // The completed word (with its '-' and any OR parts) becomes a chip.
  accept: (tag, w) => {
    const val = filterSearch.value;
    const start = val[w.start - 1] === '-' ? w.start - 1 : w.start;
    const chip = val.slice(start, w.partStart) + (w.partStart === w.start ? '#' : '') + tag;
    filterSearch.value = (val.slice(0, start) + val.slice(w.end).replace(/^ +/, '')).replace(/ {2,}/g, ' ');
    filterSearch.focus();
    filterSearch.setSelectionRange(start, start);
    filterChips.add(chip);
  },
});
// A new task starts with the tags the filter requires (single-tag words;
// an OR word doesn't say which one), so it shows up in the current view.
function filterDefaultTags() {
  return parseFilter(filterQuery()).tagGroups.filter(g => g.length === 1).map(g => g[0]);
}

// Edit modal Tags field: each tag is a chip; the input holds the one being
// typed. Completing it (Tab/Enter on a suggestion), typing a comma or a
// space, or Enter on a tag no suggestion matches makes it a chip (Enter on
// an empty field saves); Backspace at the start removes the last chip.
const editTagsInput = document.getElementById('task-edit-tags' + sfx);
const cleanTag = t => t.trim().replace(/^#+/, '').replace(/\s+/g, '-');
// A chip field of tags: a comma or a space (typed or pasted: "#a #b, c")
// commits everything before it as chips. Tags are one word each, so a
// space ends one rather than becoming part of it.
function chipsOnSeparators(input, chips) {
  input.addEventListener('input', () => {
    const parts = input.value.split(/[\s,]+/);
    if (parts.length < 2) return;
    const last = parts.pop();
    chips.set([...chips.words(), ...parts.map(cleanTag).filter(Boolean)], true);
    input.value = last;
  });
}
const editChips = editTagsInput && chipField(editTagsInput, {
  label: t => '#' + t, listWord: t => listSet.has(t.toLowerCase()) });
if (editTagsInput) {
  chipsOnSeparators(editTagsInput, editChips);
  // Enter with no suggestion open (the dropdown takes it first when one
  // is): a typed tag becomes a chip rather than saving the task.
  editTagsInput.addEventListener('keydown', e => {
    if (e.key !== 'Enter' || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.isComposing) return;
    const typed = cleanTag(editTagsInput.value);
    if (!typed) return;
    e.preventDefault();
    editChips.add(typed);
    editTagsInput.value = '';
  });
}
// Every tag in the field: the chips plus a half-typed one (Save takes it too).
function editTags() {
  return [...editChips.words(), cleanTag(editTagsInput.value)].filter(Boolean);
}
const editTagAc = editTagsInput && attachTagAc(editTagsInput, {
  word: () => {
    const partial = editTagsInput.value.trim();
    return partial ? { partial } : null;
  },
  suggest: w => {
    const committed = editChips.words().map(t => t.toLowerCase());
    const counts = tagCounts(st.tasks);
    for (const tag of Object.keys(counts)) if (committed.includes(tag.toLowerCase())) delete counts[tag];
    return rankTags(w.partial, counts);
  },
  accept: tag => {
    editChips.add(tag);
    editTagsInput.value = '';
    editTagsInput.focus();
  },
});

// A new task's title: the #tag shorthand (TasksCore.parseQuickAdd) gets the
// same suggestions, from every tag in use.
if (titleInput) attachTagAc(titleInput, {
  word: () => document.getElementById('task-edit-id' + sfx).value ? null : hashWordAtCaret(titleInput),
  suggest: w => rankTags(w.partial, tagCounts(st.tasks), w.partial ? 8 : 12),
  accept: (tag, w) => { insertHashTag(titleInput, tag, w); syncQuickPreview(); },
});

// ── Add task ───────────────────────────────────────────────────────────────

addBtn.addEventListener('click', () => openEditModal(null));

// ── Edit modal ─────────────────────────────────────────────────────────────

// Quick-add shorthand (TasksCore.parseQuickAdd) — new tasks only: a title
// ending in "tomorrow #work due:fri" fills those fields on save. The line
// under the title shows what was recognised, so nothing is a surprise.
const quickPreview = document.getElementById('task-edit-quick' + sfx);
function quickParse() {
  if (document.getElementById('task-edit-id' + sfx).value) return null;   // editing
  const q = parseQuickAdd(titleInput.value, todayStr());
  return q.found ? q : null;
}
function quickText(q) {
  return [
    q.start_date && `📅 start ${formatDate(q.start_date)}`,
    q.due_date && `⏰ due ${formatDate(q.due_date)}`,
    ...q.tags.map(t => '#' + t),
  ].filter(Boolean).join(' · ');
}

// Batch mode (the editor's Make task with several lines): the modal makes
// one task per title. The titles are listed instead of the title field,
// and the fields apply to all of them; shorthand on a line adds to them.
let batchTitles = null;
const titleField = titleInput.closest('.task-field');
const batchField = document.getElementById('task-edit-batch' + sfx);
function setBatch(titles) {
  batchTitles = titles?.length ? titles : null;
  titleField.style.display = batchTitles ? 'none' : '';
  titleInput.required = !batchTitles;
  if (!batchField) return;
  batchField.style.display = batchTitles ? '' : 'none';
  if (!batchTitles) return;
  document.getElementById('task-edit-batch-label' + sfx).textContent = `${batchTitles.length} tasks`;
  const list = document.getElementById('task-edit-batch-list' + sfx);
  list.replaceChildren(...batchTitles.map(t => {
    const q = parseQuickAdd(t, todayStr());
    const li = document.createElement('li');
    li.textContent = q.title;
    if (q.found) {
      const extra = document.createElement('span');
      extra.className = 'task-quick-preview';
      extra.textContent = ' ' + quickText(q);
      li.append(extra);
    }
    return li;
  }));
}

function syncQuickPreview() {
  const q = !batchTitles && quickParse();
  quickPreview.hidden = !q;
  if (!q) return;
  quickPreview.textContent = quickText(q);
}

// A private task is shared by unticking the Private box (the note says what
// that does); a shared task stays shared, so its box is disabled.
function syncPrivateNote() {
  if (!privateCb) return;
  const share = privateWas && !privateCb.checked;
  privateNote.hidden = !share;
  if (share) privateNote.textContent = 'Everyone who uses the wiki will see and change this task.';
}
privateCb?.addEventListener('change', syncPrivateNote);
titleInput.addEventListener('input', syncQuickPreview);

// The modal's History section: the task's logged events, newest first
// (tasks.py _log), with its creation as the oldest. Closed on every open;
// hidden for a new task. Tasks from before the log existed get their
// creation from created_at.
const historyDetails = document.getElementById('task-edit-history' + sfx);
const HISTORY_TEXT = {
  created: () => 'Created', done: () => 'Done', reopened: () => 'Reopened',
  wontdo: () => "Won't do", skipped: () => 'Skipped',
  private: () => 'Made private', shared: () => 'Made shared',
  start: e => `Start ${historyDate(e.from)} → ${historyDate(e.to)}`,
  due: e => `Due ${historyDate(e.from)} → ${historyDate(e.to)}`,
  repeat: e => `Repeats: next ${historyDate(e.to)}`,
};
function historyDate(d) { return d ? shortDate(d) : '—'; }
function renderHistory(task) {
  const entries = (task?.history || []).filter(e => HISTORY_TEXT[e.event]);
  if (task?.created_at && !entries.some(e => e.event === 'created')) {
    entries.unshift({ at: task.created_at.slice(0, 16), event: 'created' });
  }
  historyDetails.hidden = !entries.length;
  historyDetails.open = false;
  document.getElementById('task-edit-history-count' + sfx).textContent = entries.length || '';
  document.getElementById('task-edit-history-list' + sfx).innerHTML = entries.slice().reverse().map(e =>
    `<li><span class="task-history-at">${esc(commentStamp(e))}</span> ` +
    `${esc(HISTORY_TEXT[e.event](e))}</li>`).join('');
}

// The modal's Comments: short status lines ("called Bob, waiting"), newest
// first. They are saved at once through their own routes (not with Save),
// so a comment is never lost to Cancel. Hidden for a new task. Enter in
// the field adds one; ✎ edits one in place (Enter saves, Esc cancels), ×
// deletes it with undo.
const commentsBox = document.getElementById('task-edit-comments' + sfx);
const commentInput = document.getElementById('task-edit-comment' + sfx);
const commentList = document.getElementById('task-edit-comment-list' + sfx);
st.commentsTaskId = null;
function renderComments(task) {
  st.commentsTaskId = task?.id || null;
  commentsBox.hidden = !task;
  if (!task) return;
  const comments = task.comments || [];
  document.getElementById('task-edit-comments-count' + sfx).textContent = comments.length || '';
  commentList.innerHTML = comments.slice().reverse().map(c =>
    `<li data-comment="${esc(c.id)}"><span class="task-history-at">${esc(commentStamp(c))}</span>` +
    `<span class="task-comment-text">${htmlOf('comments', c.text)}</span>` +
    `<button type="button" class="task-comment-btn" data-comment-action="edit" title="Edit" aria-label="Edit comment">✎</button>` +
    `<button type="button" class="task-comment-btn" data-comment-action="delete" title="Delete" aria-label="Delete comment">×</button>` +
    '</li>').join('');
  renderFigures(commentList);
}
// A comment route answered with the task: take it in and redraw.
async function commentWrite(url, method, body) {
  const res = await fetch(url, {
    method, headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) { await noticeFromResponse(res, 'Comment not saved'); return null; }
  const updated = await res.json();
  await renderTexts(textsOf([updated]));
  const idx = st.tasks.findIndex(t => t.id === updated.id);
  if (idx !== -1) st.tasks[idx] = updated;
  if (st.commentsTaskId === updated.id && modal.open) renderComments(updated);
  render();
  refreshOtherInstances(instanceId);
  return updated;
}
const commentsUrl = id => `/api/tasks/${encodeURIComponent(id)}/comments`;
async function addComment() {
  const text = commentInput.value.trim();
  if (!text || !st.commentsTaskId) return;
  commentInput.disabled = true;
  const ok = await commentWrite(commentsUrl(st.commentsTaskId), 'POST', { text });
  commentInput.disabled = false;
  if (ok) commentInput.value = '';
  commentInput.focus();
}
commentInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); addComment(); }
});
// Pasting a URL asks for its name, as in the description (pasteUrlAsLink).
commentInput.addEventListener('paste', e => pasteUrlAsLink(commentInput, e));
document.getElementById('task-edit-comment-add' + sfx).addEventListener('click', addComment);
commentList.addEventListener('click', e => {
  const btn = e.target.closest('[data-comment-action]');
  const li = btn?.closest('[data-comment]');
  if (!li || !st.commentsTaskId) return;
  const taskId = st.commentsTaskId, cid = li.dataset.comment;
  const task = st.tasks.find(t => t.id === taskId);
  const comment = task?.comments?.find(c => c.id === cid);
  if (!comment) return;
  if (btn.dataset.commentAction === 'delete') {
    const before = task.comments;
    dismissUndo();
    commentWrite(`${commentsUrl(taskId)}/${encodeURIComponent(cid)}`, 'DELETE').then(ok => {
      if (ok) showUndo('Comment deleted', () => updateTask(taskId, { comments: before }).then(t => {
        if (t && st.commentsTaskId === taskId && modal.open) renderComments(t);
      }));
    });
    return;
  }
  // Edit in place.
  const textEl = li.querySelector('.task-comment-text');
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'task-comment-edit';
  input.addEventListener('paste', e => pasteUrlAsLink(input, e));
  input.value = comment.text;
  input.setAttribute('aria-label', 'Edit comment');
  textEl.replaceWith(input);
  li.querySelectorAll('.task-comment-btn').forEach(b => { b.hidden = true; });
  input.focus();
  let done = false;
  const finish = async save => {
    if (done) return;
    done = true;
    const text = input.value.trim();
    if (save && text && text !== comment.text) {
      if (await commentWrite(`${commentsUrl(taskId)}/${encodeURIComponent(cid)}`, 'PUT', { text })) return;
    }
    renderComments(st.tasks.find(t => t.id === taskId));
  };
  input.addEventListener('keydown', ev => {
    if (ev.key === 'Enter' && !ev.isComposing) { ev.preventDefault(); finish(true); }
    // Esc ends the edit, not the modal.
    else if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); finish(false); }
  });
  input.addEventListener('blur', () => finish(true));
});

// opts (new tasks): `tags` (comma-separated; given, even empty, it
// replaces the filter's tags), `keep` (instead of the embed's keep=), and from the editor's Make
// task a starting `title` and `description`, a `start_date` (null: none,
// instead of Focus's today) and `onCreated(task)`, called once it is saved.
// `titles` instead of `title` makes one task per title (batch mode), and
// onCreated gets them all (a null where creating failed, then it stops).
let onCreated = null;
function openEditModal(taskId, opts) {
  const task = taskId ? st.tasks.find(t => t.id === taskId) : null;
  const defaultTags = opts?.tags || '';
  const givenTags = !!opts && 'tags' in opts;
  onCreated = !task && opts?.onCreated || null;
  setBatch(!task && opts?.titles);
  document.getElementById('task-edit-id' + sfx).value = task ? task.id : '';
  document.getElementById('task-edit-title' + sfx).value = task ? task.title : opts?.title || '';
  document.getElementById('task-edit-desc' + sfx).value = task ? task.description || '' : opts?.description || '';
  setNotesPreview(false, false);
  document.getElementById('task-edit-due' + sfx).value = task ? task.due_date || '' : '';
  document.getElementById('task-edit-start' + sfx).value = task ? task.start_date || ''
    : opts && 'start_date' in opts ? opts.start_date || '' : (st.activeView === 'focus' ? dateOffset(0) : '');
  const startTags = task ? task.tags || []
    : givenTags ? defaultTags.split(',').map(cleanTag).filter(Boolean) : filterDefaultTags();
  editChips.set(startTags, true);
  editTagsInput.value = '';
  renderHistory(task);
  renderComments(task);
  commentInput.value = '';
  titleInput.placeholder = task ? '' : 'e.g. Call Bob tomorrow #work due:fri';
  syncQuickPreview();
  keepCb.checked = task ? !!task.keep : opts && 'keep' in opts ? !!opts.keep : initKeep;
  if (privateCb) {
    privateCb.checked = task ? !!task.private : opts && 'private' in opts ? !!opts.private : initPrivate;
    privateWas = privateCb.checked;
    privateCb.disabled = !!task && !task.private;   // shared stays shared
    privateNote.hidden = true;
  }
  const recType = task?.recurrence?.type || '';
  recTypeSelect.value = recType;
  document.getElementById('task-edit-rec-interval' + sfx).value = task?.recurrence?.interval || 1;
  const recFromCb = document.getElementById('task-edit-rec-from' + sfx);
  if (recFromCb) recFromCb.checked = task?.recurrence?.from === 'completion';
  syncRecFields();
  deleteBtn.style.display = task ? '' : 'none';
  syncDates('open');
  modal.showModal();
  (task && opts?.focusComment ? commentInput : batchTitles ? editTagsInput : titleInput).focus();
}

// Every N / From done are only meaningful with a repeat type — disable them
// (rather than hide, which would shift the row) when Repeat is "—".
function syncRecFields() {
  // Sub-controls are only active when Repeat is both enabled and set.
  const on = !recTypeSelect.disabled && !!recTypeSelect.value;
  const subHint = on ? '' : (recTypeSelect.disabled ? recTypeSelect.closest('.task-field').title : 'Select a repeat type first');
  const intervalEl = document.getElementById('task-edit-rec-interval' + sfx);
  intervalEl.disabled = !on;
  intervalEl.closest('.task-field').title = subHint;
  const rf = document.getElementById('task-edit-rec-from' + sfx);
  if (rf) {
    rf.disabled = !on;
    rf.closest('.task-field').title = on ? 'Repeat from the completion date instead of the due (or start) date' : subHint;
  }
}
recTypeSelect.addEventListener('change', syncRecFields);

// ── Modal date triggers (custom calendar popup, no native <input type=date>) ──
// Each date field is a hidden <input> holding the value plus a visible button
// showing it; clicking the button opens the same in-page calendar the cards use.
function refreshDateTriggers() {
  modal.querySelectorAll('.date-trigger').forEach(btn => {
    const input = document.getElementById(btn.dataset.dateTrigger);
    btn.textContent = input && input.value ? formatDate(input.value) : (btn.dataset.placeholder || 'Set date');
    btn.classList.toggle('date-trigger--set', !!(input && input.value));
  });
}
modal.querySelectorAll('.date-trigger').forEach(btn => {
  btn.addEventListener('click', () => {
    const input = document.getElementById(btn.dataset.dateTrigger);
    openDatePicker(input.value || '', d => {
      input.value = d || '';
      syncDates(btn.dataset.dateTrigger === 'task-edit-due' + sfx ? 'due' : 'start');
    }, btn, todayStr(), modal);
  });
});

// "Days before due" is not stored: it is just another way to enter the start
// date, shown as the gap between the two dates (and that gap is what a
// recurring task carries to its next instance). `changed` says which field
// the user touched: typing a number ('lead') sets start = due − N; any other
// change recomputes the number from the two dates. Moving the due date never
// moves the start date — like the inline ⏰ edit, one edit changes one date.
// Also gates Repeat on having at least one date.
function syncDates(changed) {
  const leadEl  = document.getElementById('task-edit-lead' + sfx);
  const dueEl   = document.getElementById('task-edit-due' + sfx);
  const startEl = document.getElementById('task-edit-start' + sfx);
  const lead = leadEl.value.trim();
  if (changed === 'lead' && dueEl.value && lead !== '') {
    startEl.value = addDays(dueEl.value, -Math.max(0, parseInt(lead, 10) || 0));
  }
  if (changed !== 'lead') {
    const gap = (dueEl.value && startEl.value)
      ? Math.round((new Date(dueEl.value + 'T00:00:00') - new Date(startEl.value + 'T00:00:00')) / 86400000)
      : null;
    leadEl.value = gap != null && gap >= 0 ? gap : '';
  }
  // Only meaningful relative to a due date.
  leadEl.disabled = !dueEl.value;
  syncDateWarning(changed);
  refreshDateTriggers();
  // Repeat requires at least one date to be meaningful, and a keep task
  // never repeats (Keep and Repeat both say what happens after done).
  const noDate = !dueEl.value && !startEl.value;
  recTypeSelect.disabled = noDate || keepCb.checked;
  recTypeSelect.closest('.task-field').title = keepCb.checked ? 'A keep task does not repeat'
    : noDate ? 'Set a due or start date first' : '';
  syncRecFields();
}
keepCb.addEventListener('change', () => syncDates('keep'));

// Start after Due: a hint under the dates with a one-click fix that moves
// the date not just changed (the deadline, or the start when Due was moved
// before it). It doesn't block saving: a late task may well start later.
const dateWarn = document.getElementById('task-edit-date-warn' + sfx);
const dateWarnFix = document.getElementById('task-edit-date-warn-fix' + sfx);
let dateWarnMove = null;   // 'due' | 'start'
function syncDateWarning(changed) {
  if (!dateWarn) return;
  const due = document.getElementById('task-edit-due' + sfx).value;
  const start = document.getElementById('task-edit-start' + sfx).value;
  const late = !!(due && start && start > due);
  dateWarn.hidden = !late;
  if (!late) return;
  if (changed === 'open') dateWarnMove = null;
  if (changed === 'due' || changed === 'start') dateWarnMove = changed === 'due' ? 'start' : 'due';
  if (!dateWarnMove) dateWarnMove = 'due';
  document.getElementById('task-edit-date-warn-text' + sfx).textContent =
    `Starts after its deadline (${formatDate(due)}).`;
  dateWarnFix.textContent = dateWarnMove === 'due'
    ? `Move deadline to ${formatDate(start)}` : `Move start to ${formatDate(due)}`;
}
dateWarnFix?.addEventListener('click', () => {
  const due = document.getElementById('task-edit-due' + sfx);
  const start = document.getElementById('task-edit-start' + sfx);
  if (dateWarnMove === 'due') due.value = start.value; else start.value = due.value;
  syncDates(dateWarnMove);
});
document.getElementById('task-edit-lead' + sfx).addEventListener('input', () => syncDates('lead'));

cancelBtn.addEventListener('click', () => { editTagAc?.hide(); modal.close(); });
// Closed without saving (Cancel, Esc): no callback later by mistake.
// Focus would stay on a field of the closed dialog, and the task app's keys
// (j/k, Enter, Esc …) ignore keys typed into a field. Esc's cancel comes
// at once (close only after the key), so the next key already works.
const blurInside = () => { if (modal.contains(document.activeElement)) document.activeElement.blur(); };
modal.addEventListener('cancel', blurInside);
modal.addEventListener('close', () => { onCreated = null; blurInside(); });

modal.addEventListener('keydown', e => { if ((e.key === 'Enter' || e.key === 's') && (e.ctrlKey || e.metaKey)) { e.preventDefault(); e.stopPropagation(); form.requestSubmit(); } }, true);
// F2: Edit | Preview of the notes, as in the editor (not the editor's,
// on its page).
modal.addEventListener('keydown', e => {
  if (e.key !== 'F2' || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
  e.preventDefault(); e.stopPropagation();
  setNotesPreview(!previewing());
}, true);

form.addEventListener('submit', async e => {
  e.preventDefault();
  const id = document.getElementById('task-edit-id' + sfx).value;
  const data = {
    title: document.getElementById('task-edit-title' + sfx).value.trim(),
    description: document.getElementById('task-edit-desc' + sfx).value.trim(),
    due_date: document.getElementById('task-edit-due' + sfx).value || null,
    start_date: document.getElementById('task-edit-start' + sfx).value || null,
    tags: editTags(),
    rec_type: keepCb.checked ? null : recTypeSelect.value || null,
    rec_interval: parseInt(document.getElementById('task-edit-rec-interval' + sfx).value) || 1,
    rec_from: document.getElementById('task-edit-rec-from' + sfx)?.checked ? 'completion' : 'due',
    keep: keepCb.checked,
    ...(privateCb ? { private: privateCb.checked } : {}),
  };
  if (batchTitles && !id) {
    const titles = batchTitles, created = onCreated;
    onCreated = null;
    modal.close();
    const today = todayStr(), made = [];
    for (const title of titles) {
      const q = parseQuickAdd(title, today);
      const task = await createTask({
        ...data, title: q.title,
        start_date: q.start_date || data.start_date, due_date: q.due_date || data.due_date,
        tags: [...new Set([...data.tags, ...q.tags])],
      });
      made.push(task);
      if (!task) break;
    }
    created?.(made);
    return;
  }
  // Shorthand in a new task's title wins over the fields (the preview said so).
  const quick = !id && quickParse();
  if (quick) {
    data.title = quick.title;
    if (quick.start_date) data.start_date = quick.start_date;
    if (quick.due_date) data.due_date = quick.due_date;
    data.tags = [...new Set([...data.tags, ...quick.tags])];
  }
  if (!data.title) { document.getElementById('task-edit-title' + sfx).focus(); return; }
  const created = onCreated;
  onCreated = null;
  modal.close();
  if (id) {
    await updateTask(id, data);
  } else if (created) {
    const task = await createTask(data);
    if (task) created(task);
  } else {
    // Select the new task so it is easy to find wherever the view sorts it
    // (on Focus it lands at the end, possibly below the fold). A view that
    // doesn't show it (another list, a filter) leaves the selection alone.
    const task = await createTask(data);
    if (task) unfoldFor(task.id);
    const el = task && listEl.querySelector(`.task-item[data-id="${CSS.escape(task.id)}"]`);
    if (el) {
      setSelected(task.id);
      flashTask(el);
    }
  }
});

deleteBtn.addEventListener('click', () => {
  const id = document.getElementById('task-edit-id' + sfx).value;
  if (!id) return;
  modal.close();
  deleteWithUndo([id]);
});

// ── Drag and Drop ──────────────────────────────────────────────────────────

Object.assign(app, initTaskDnd(app));
const { setupBoardDragAndDrop, setupDragAndDrop } = app;
function reorderSelected(direction) {
  if (!st.selectedIds.size) return;
  if (activeSort()) showNotice(`Sorted ${SORT_TITLES[activeSort()]}: press o until Manual to reorder`);
  else if (SECTIONS[st.activeView]) sectionKeyMove(direction);
  else if (st.activeView === 'plan') planKeyMove(direction);
}
// Plan: Ctrl+Shift+↑/↓ moves the selection one place within its day or
// Later, never out of it (moving to another day is 1–7 and dragging).
function planKeyMove(direction) {
  const els = [...st.selectedIds].map(id => listEl.querySelector(`.task-item[data-id="${CSS.escape(id)}"]`));
  if (els.some(el => !el)) return;
  const div = boardDivider(els[0]);
  if (!div?.dataset.ordered || els.some(el => boardDivider(el) !== div)) return;
  sectionKeyMove(direction, planSectionList(div), OPEN_ORDER);
}

document.addEventListener('keydown', async e => {
  // Only the active instance handles keyboard events (multi-embed safety)
  if (tasksInstances.length > 1 && activeTasksInstance !== instanceId) return;
  if (headless) return;
  // The second key of g … / c … is the page's (the t of c t, g t)
  if (keyPrefix.key) return;
  // The ⋯ menu owns the keyboard while open (its buttons take Enter/Space)
  if (st.taskMenu) return;

  // Ctrl+Z undo — works everywhere except inside the edit modal
  if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey && pendingUndoFn) {
    if (modal.open || deleteModal.open || deadlineModal.open) return;
    const focus = document.activeElement;
    if (focus && focus.closest('textarea, [contenteditable]')) return;
    e.preventDefault();
    triggerUndo();
    return;
  }

  if (modal.open || deleteModal.open || document.querySelector('dialog[open].modal')) return;
  const focus = document.activeElement;

  // '/' focuses the filter field; '#' focuses it and starts a tag word
  if (e.key === '/' && !e.ctrlKey && !e.metaKey && !focus?.closest('input, textarea, select, [contenteditable]')) {
    e.preventDefault();
    filterSearch?.focus();
    return;
  }
  if (e.key === '#' && !e.ctrlKey && !e.metaKey && !focus?.closest('input, textarea, select, [contenteditable]')) {
    e.preventDefault();
    if (filterSearch) {
      const v = filterSearch.value.trimEnd();
      filterSearch.value = v ? v + ' #' : '#';
      filterSearch.focus();
      filterSearch.setSelectionRange(filterSearch.value.length, filterSearch.value.length);
      filterSearch.dispatchEvent(new Event('input'));
    }
    return;
  }

  if (focus && focus.closest('input, textarea, select, [contenteditable]')) return;

  const items = visibleItems();
  const currentIdx = st.selectedId ? items.findIndex(el => el.dataset.id === st.selectedId) : -1;

  // Ctrl/Cmd+A with a task selected: every task shown (with the filter, the
  // ones it leaves). Without a selection it stays the page's select all.
  if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === 'a' && st.selectedId) {
    e.preventDefault();
    selectAllVisible();
    return;
  }

  // The menu key or Shift+F10: the selected task's ⋯ menu (with a mouse
  // there is no ⋯ button; right-click opens it too)
  if ((e.key === 'ContextMenu' || (e.key === 'F10' && e.shiftKey)) && st.selectedId) {
    const el = listEl.querySelector(`.task-item[data-id="${CSS.escape(st.selectedId)}"]`);
    if (el) { e.preventDefault(); openTaskMenu(st.selectedId, el); }
    return;
  }

  // 'v' switches between full cards and one line per task
  if (e.key === 'v' && !e.ctrlKey && !e.metaKey && !e.altKey) {
    e.preventDefault();
    toggleCompact();
    return;
  }

  // 'd' toggles the Open tab's Plan switch (by start date / one list)
  if (e.key === 'd' && !e.ctrlKey && !e.metaKey && !e.altKey && isOpenTab(st.activeView)) {
    e.preventDefault();
    setGroup(st.activeView === 'open' ? 'days' : 'list');
    return;
  }

  // 'o' cycles the Open tab's order: manual, by deadline, newest first
  if (e.key === 'o' && !e.ctrlKey && !e.metaKey && !e.altKey && (st.activeView === 'open' || st.activeView === 'plan')) {
    e.preventDefault();
    cycleSort();
    return;
  }

  // 'n' opens new task modal
  if (e.key === 'n' && !e.ctrlKey && !e.metaKey && !e.altKey) {
    e.preventDefault();
    openEditModal(null);
    return;
  }

  // 'f' / 'F' — start selected task today / tomorrow
  if ((e.key === 'f' || e.key === 'F') && !e.ctrlKey && !e.metaKey && !e.altKey && st.selectedId) {
    e.preventDefault();
    const task = st.tasks.find(t => t.id === st.selectedId);
    if (task && !task.completed_at) {
      const target = e.key === 'f' ? dateOffset(0) : dateOffset(1);
      if (task.start_date !== target && startsAfterDeadline(task, target)) planOnDay(task.id, target);
      else updateTask(task.id, { start_date: task.start_date === target ? null : target });
    }
    return;
  }

  // 1–7 start the selected tasks on that day of the Plan board (1 today,
  // 2 tomorrow … 7 six days ahead), 0 unplans them; on every tab
  if (/^[0-7]$/.test(e.key) && !e.ctrlKey && !e.metaKey && !e.altKey && st.selectedIds.size) {
    e.preventDefault();
    const n = Number(e.key);
    startOn([...st.selectedIds], n ? dateOffset(n - 1) : null);
    return;
  }

  // Tab/Shift+Tab cycles the views
  if (e.key === 'Tab') {
    e.preventDefault();
    // Only the tabs: Completed (reached by its link) is not in the cycle,
    // and from there Tab goes to the first tab, Shift+Tab to the last.
    const views = tabButtons().map(b => b.dataset.view);
    const i = views.indexOf(st.activeView === 'plan' ? 'open' : st.activeView);
    const n = views.length;
    switchTab(tabView(views[i < 0 ? (e.shiftKey ? n - 1 : 0) : e.shiftKey ? (i - 1 + n) % n : (i + 1) % n]));
    return;
  }

  if (!items.length) return;

  // Ctrl+Shift+j/k / Ctrl+Shift+↑↓ — reorder selected (manual sort mode)
  if (e.ctrlKey && e.shiftKey && (e.key === 'ArrowDown' || e.key === 'J')) {
    e.preventDefault();
    reorderSelected('down');
  } else if (e.ctrlKey && e.shiftKey && (e.key === 'ArrowUp' || e.key === 'K')) {
    e.preventDefault();
    reorderSelected('up');
  // Shift+j/k / Shift+↑↓ — extend selection range
  } else if (e.shiftKey && (e.key === 'ArrowDown' || e.key === 'J')) {
    e.preventDefault();
    if (currentIdx < items.length - 1) extendSelection(items[currentIdx + 1].dataset.id);
  } else if (e.shiftKey && (e.key === 'ArrowUp' || e.key === 'K')) {
    e.preventDefault();
    if (currentIdx > 0) extendSelection(items[currentIdx - 1].dataset.id);
  } else if (e.key === 'ArrowDown' || e.key === 'j') {
    e.preventDefault();
    setSelected(items[currentIdx < items.length - 1 ? currentIdx + 1 : 0].dataset.id);
  } else if (e.key === 'ArrowUp' || e.key === 'k') {
    e.preventDefault();
    setSelected(items[currentIdx > 0 ? currentIdx - 1 : items.length - 1].dataset.id);
  } else if (e.key === ' ' && st.selectedId) {
    e.preventDefault();
    if (st.selectedIds.size > 1) {
      // Bulk complete/uncomplete; the selection moves on to the next task.
      const ids = [...st.selectedIds];
      const next = items.find((el, i) => i > currentIdx && !st.selectedIds.has(el.dataset.id))
                || items.find(el => !st.selectedIds.has(el.dataset.id));
      setSelected(next?.dataset.id || null);
      await completeMany(ids);
    } else {
      const task = st.tasks.find(t => t.id === st.selectedId);
      if (!task) return;
      const completing = !task.completed_at;
      if (completing) {
        if (pendingCompletions.has(task.id)) {
          cancelPendingComplete(task.id);
        } else {
          const li = listEl.querySelector(`.task-item[data-id="${CSS.escape(task.id)}"]`);
          beginComplete(task.id, li);
        }
      } else {
        updateTask(task.id, { completed: false });
        showUndo('Task reopened', () => completeTasks([task.id], true));
      }
    }
  } else if (['ArrowRight', 'l', 'ArrowLeft', 'h'].includes(e.key) && !e.ctrlKey && !e.metaKey && !e.altKey &&
             st.selectedIds.size === 1 && st.compact) {
    // → / l open the selected line's details, ← / h close them (vim's hjkl).
    if (toggleDetails(st.selectedId, e.key === 'ArrowRight' || e.key === 'l')) e.preventDefault();
  } else if (e.key === 'Enter' && st.selectedId) {
    e.preventDefault();
    openEditModal(st.selectedId);
  } else if (e.key === 'Delete' && st.selectedIds.size) {
    e.preventDefault();
    await deleteWithUndo([...st.selectedIds], () => {
      // Move the selection to the next surviving card
      const next = items.find(el => !st.selectedIds.has(el.dataset.id) &&
                     items.indexOf(el) > currentIdx)
                || items.find(el => !st.selectedIds.has(el.dataset.id));
      st.selectedId = next?.dataset.id || null;
      st.selectedIds = new Set(st.selectedId ? [st.selectedId] : []);
      st.anchorId = st.selectedId;
    });
  } else if ((e.key === 't' || e.key === 'T') && st.selectedIds.size) {
    e.preventDefault();
    const ids = [...st.selectedIds];
    const removing = e.key === 'T';
    openBulkTagModal(ids, removing);
  } else if (e.key === 'Escape' && (st.expandedId || detailsMode) && !st.selectMode) {
    detailsMode = false;
    setExpanded(null);   // collapse first; the next Esc clears the selection
  } else if (e.key === 'Escape') {
    exitSelectMode();
  }
});

// ── Expose refresh and modal for quick-add ────────────────────────────────

// Register this instance so writes in sibling embeds can refresh it.
// Background refresh re-renders the list, so it waits while the user is in
// the middle of something the re-render would destroy: a drag or swipe, a
// completion's grace period, the ⋯ menu or the editor.
function refreshIfIdle() {
  const busy = st.draggedId || st.touchDrag || pendingCompletions.size || st.taskMenu || modal.open || deadlineModal.open;
  if (!busy) refresh();
}
tasksInstances.push({ id: instanceId, refresh, refreshIfIdle, syncSelBar, openEdit: openEditModal });

openEdit = openEditModal;

// ── Init ───────────────────────────────────────────────────────────────────

// Apply URL-param pre-filtering
// [[tasks: tag=…]] / ?tag= and ?search= all land in the one filter field.
const initSearch = urlParams.get('search') || '';
// ?tag= / [[tasks: tag=…]] arrive as chips, ?search= as text.
if (filterSearch) {
  filterChips.set(tagParamToQuery(initTag).split(' ').filter(Boolean), true);
  filterSearch.value = initSearch || '';
}
// Precedence: URL ?view= > embed view (always wins when specified) >
// tab remembered this session > Focus default. So a [[tasks: view=…]] embed
// always opens on its configured tab; a plain [[tasks:]] remembers the tab the
// user last clicked across refreshes within the same browser tab. A name that
// isn't one of VIEWS is passed over.
st.activeView = [urlView, embedView, loadActiveTab()].find(v => VIEWS.includes(v)) || 'focus';
if (st.activeView !== 'completed') lastTab = st.activeView;
applyCompact(st.activeView);
applySort();
applyGroup();
markActiveTab(st.activeView);
syncCompletedLink();

// ?task=<id> (search results, a page's "Linked tasks"): show exactly that
// task — the link sets its tab, and here it is selected, scrolled into view
// and briefly highlighted. By id, not by title: a title can be shared, or
// contain a #word the filter field would read as a tag.
const initTaskId = urlParams.get('task') || '';
function revealTask(id) {
  if (!st.tasks.some(t => t.id === id)) {
    // Deleted (or in another list). One notice per page.
    if (instanceId === '0') showNotice('That task is no longer in your tasks.');
    return;
  }
  unfoldFor(id);
  setSelected(id);
  const el = listEl.querySelector(`.task-item[data-id="${CSS.escape(id)}"]`);
  if (!el) return;
  el.scrollIntoView({ block: 'center' });
  flashTask(el);
}

function flashTask(el) {
  el.classList.add('task-flash');
  setTimeout(() => el.classList.remove('task-flash'), 2000);
}

fetchTasks().then(async () => {
  if (st.activeView === 'completed') await fetchCompleted();
  if (initTaskId) revealTask(initTaskId);
});

// Auto-open new item modal if ?new=1 in URL
if (initNew) {
  openEditModal(null);
  // Clean up URL
  const url = new URL(window.location);
  url.searchParams.delete('new');
  history.replaceState(null, '', url);
}

} // end initTasksInstance

initTaskApps();
