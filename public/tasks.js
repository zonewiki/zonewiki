// ── Zone Task App ────────────────────────────────────────────────────────────
// An ES module; zonewiki.js imports it on every page. initTaskApps() starts each
// task app (.tasks-app) on the page, and one added later (the hidden tasks
// that a new task from any page loads, actions.js).
//
// A task app is put together here (initTasksInstance): its elements, the
// state its parts share (app.st), what the embed and the address say it
// shows, and the list's own listeners. What it does is in its parts,
// js/task*.js, each an initTask…(app) that is handed `app`, uses the others
// through it (app.render(), app.updateTask(…)) and returns its own functions,
// which are added to `app`. In the order below: a part's top-level code runs
// when it is started, so one that calls another while starting comes after
// it. tests/test_task_app_wiring.py checks that every app.name a part uses
// is one that is put there, and by one place only.

import TasksCore from './tasks-core.js';
import { linkAutocomplete } from './js/completion.js';
import { openLightbox } from './js/page.js';
import { pasteUrlAsLink } from './js/util.js';
import { people } from './js/taskutil.js';
import { openDatePicker } from './js/datepicker.js';
import { forgetInitialTasks, initTaskData } from './js/taskdata.js';
import { initTaskView } from './js/taskview.js';
import { initTaskOrder } from './js/taskorder.js';
import { initTaskChips } from './js/taskchips.js';
import { initTaskTabs } from './js/tasktabs.js';
import { initTaskKeys } from './js/taskkeys.js';
import { initTaskRender } from './js/taskrender.js';
import { initTaskModal } from './js/taskmodal.js';
import { initTaskSelect } from './js/taskselect.js';
import { initTaskMenu } from './js/taskmenu.js';
import { initTaskDnd } from './js/taskdnd.js';
import { initTaskTouch } from './js/tasktouch.js';
import { initTaskSettings } from './js/tasksettings.js';
import { dateOffset, DATES, formatDate, shortDate, showNotice, startsAfterDeadline, todayStr } from './js/taskutil.js';
import { renderTexts, textsOf } from './js/tasktexts.js';

// Pure task logic lives in tasks-core.js.
const { parseFilter, tasksInView, tagParamToQuery } = TasksCore;


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

// Start every task list app on the page that hasn't started yet (marked
// data-ready once it has). Returns how many it started.
export function initTaskApps() {
  const apps = [...document.querySelectorAll('.tasks-app:not([data-ready])')];
  apps.forEach(appEl => {
    initTasksInstance(appEl);
    appEl.dataset.ready = '1';
  });
  // The tasks the page came with were for these.
  forgetInitialTasks();
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
const sortSel = document.getElementById('task-sort' + sfx);
const planSwitch = document.getElementById('task-plan' + sfx);
const filterSearch = document.getElementById('task-filter-search' + sfx);
const closedLink = document.getElementById('task-closed-link' + sfx);
const modal = document.getElementById('task-edit-modal' + sfx);
const form = document.getElementById('task-edit-form' + sfx);
const cancelBtn = document.getElementById('task-edit-cancel' + sfx);
const wontDoBtn = document.getElementById('task-edit-wontdo' + sfx);
const recTypeSelect = document.getElementById('task-edit-rec-type' + sfx);
const keepCb = document.getElementById('task-edit-keep' + sfx);
// The list the task is in (a private list's tasks are the user's alone),
// and the line that says who sees it.
const listSelect = document.getElementById('task-edit-list' + sfx);
const listNote = document.getElementById('task-edit-list-note' + sfx);
const deadlineModal = document.getElementById('task-deadline-modal' + sfx);
const deadlineTextEl = document.getElementById('task-deadline-text' + sfx);
const selBar = document.getElementById('task-selbar' + sfx);
// Hidden on the editor page, which only uses its edit modal (Make task).
const headless = appEl.dataset.headless === '1';

if (!listEl) return;

// What the app's modules (js/task*.js) use of it: its elements and
// functions, and as getters what is set further down or changes.
const app = {
  st, appEl,
  listEl, sfx, instanceId, cancelBtn, deadlineModal, deadlineTextEl, selBar, tasksInstances,
  addBtn, modal, form, wontDoBtn, recTypeSelect, keepCb, listSelect, listNote,

  // its functions
  boardDivider, clearBoardDropOver, dropOnBucket, planOnDay,
  taskList,
  filterTasks, boardBuckets,
  refreshOtherInstances, listTooltip, inScope, listName, editPrivate, newTaskList,

  flashTask,

  forgetSession,
  scopeQuery, headless,
  sortSel, planSwitch,
  filterSearch,
  tabsEl, closedLink, saveActiveTab,
  // what is set further down, or changes
  get activeTasksInstance() { return activeTasksInstance; },
  get listSet() { return listSet; },
  get initKeep() { return initKeep; },
  get privateLists() { return privateLists; },
  get listChoices() { return listChoices; },
  get titleInput() { return titleInput; },
  get descTextarea() { return descTextarea; },
  get pendingCompletions() { return pendingCompletions; },
  get LIMIT() { return LIMIT; },
  get tabLabel() { return tabLabel; },
  get thisYear() { return thisYear; },
  get initList() { return initList; },
  get noList() { return noList; },
  get defaultList() { return defaultList; },
};

st.tasks = [];
st.closedLoaded = false;   // the closed tasks are among st.tasks
st.activeView = 'focus';
st.draggedId = null;
st.selectedId  = null;
st.selectedIds = new Set();
st.anchorId    = null;
// Selection mode (⋯ → Select; meant for touch, which has no Shift or Ctrl):
// a tap or click on a card adds or drops it, nothing else on the card reacts.
st.selectMode  = false;
st.closedLimit = 50;
st.pendingUndo = null;   // what Undo (the toast's button, Ctrl+Z) would run
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

// Touch: the first tap on a card selects it (and expands it),
// a tap on the selected card opens the editor (double-click, the
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
  const yearBtn = e.target.closest('[data-closed-year]');
  if (yearBtn) setClosedYear(yearBtn.dataset.closedYear);
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
  expandSelected(id);
});

Object.assign(app, initTaskTouch(app));

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
// [[tasks: assignee=…]] / ?assignee= (with users): whose tasks.
const initAssignee = document.body.dataset.users === '1'
  ? appEl?.dataset.initAssignee || urlParams.get('assignee') || '' : '';
// WIP limit: Focus warns and Plan flags a day at >= LIMIT tasks. Off unless
// set: [[tasks: limit=N]], or ?limit=N (the built-in /tasks page has no embed).
const LIMIT = parseInt(appEl?.dataset.initLimit || urlParams.get('limit'), 10) || null;
// Lists: a list is a place a task is in (its `list`; one, or none). A view
// of a list ([[tasks: list=<name>]], /tasks?list=) shows its tasks, and a
// new task there goes into it; My tasks (data-init-mine, with the pinned
// lists in data-init-lists) shows what TasksCore.inMine says, and a new
// task there goes into the first pinned list. Elsewhere (all tasks, a
// tag's embed) every task shows, with its list as a chip. The lists come as refs: a list's
// name, or shared/<name> for the shared one beside the user's own list of
// that name; data-lists has those the user sees, with which are private
// (theirs alone, and so are the tasks in them).
const initList = cleanList(appEl?.dataset.initList);
const initLists = (appEl?.dataset.initLists || '').split('|').filter(Boolean);
// My tasks: 'assign' where assignment counts for the user, else 'lists'.
const initMine = appEl?.dataset.initMine || '';
let listChoices = [];
try { listChoices = JSON.parse(appEl?.dataset.lists || '[]'); } catch (_) {}
const listSet = new Set(listChoices.map(l => l.ref));
const privateLists = new Set(listChoices.filter(l => l.private).map(l => l.ref));
// A list made while the page is open (New list… in the dialog that moves
// tasks to a list, js/taskselect.js, which says so to every task app of the
// page): one more list the user sees, in the List field and the dialogs;
// My tasks shows its tasks too when it was pinned.
document.addEventListener('zone:list-made', e => {
  const { ref, private: isPrivate, pinned } = e.detail || {};
  if (!ref || listSet.has(ref)) return;
  listChoices.push({ ref, private: !!isPrivate });
  listChoices.sort((a, b) => listName(a.ref).localeCompare(listName(b.ref)) || a.ref.localeCompare(b.ref));
  listSet.add(ref);
  if (isPrivate) privateLists.add(ref);
  if (pinned && initMine) initLists.push(ref);
});
// The user's default list: where a new task goes in a view of no one list.
const defaultList = appEl?.dataset.defaultList || '';
// A list's name, as its chip and the dialogs show it.
function listName(ref) { return String(ref).replace(/^shared\//, ''); }
// The tooltip of a list's chip (cards, the filter).
function listTooltip(ref) {
  const r = String(ref).replace(/^-?list:/, '').toLowerCase();
  if (r === 'none' && !listSet.has('none')) return 'In no list';
  return privateLists.has(r) ? 'List (private: only you see it)' : 'List';
}
// The tabs' names: Open/Plan/Focus, or an embed's own (`labels=`, rendered
// into the tab buttons by templates/partials/tasks_embed.html, so they are
// read back from there).
const tabLabel = view => tabsEl.querySelector(`[data-view="${view === 'plan' ? 'open' : view}"]`)?.textContent.trim() || view;
// [[tasks: keep=1]]: new tasks are keep tasks (a shopping list of staples).
const initKeep = appEl?.dataset.initKeep === '1';
Object.assign(app, initTaskView(app));
const { toggleFold, unfoldFor, activeSort, applySort, groupKey, applyGroup, expandSelected } = app;

function cleanList(name) {
  return String(name || '').trim().replace(/^#/, '').trim().toLowerCase()
    .replace(/\s+/g, '-').replace(/^-+|-+$/g, '');
}
// The list a task is in, as a ref (taskList in go/tasklists.go): its `list`
// names it, and `private` says which of two lists of one name; null for
// none (and for a list this user doesn't see).
function taskList(t) {
  if (!t.list) return null;
  const ref = !t.private && listSet.has('shared/' + t.list) ? 'shared/' + t.list : t.list;
  return listSet.has(ref) && privateLists.has(ref) === !!t.private ? ref : null;
}
// list=none (no list is called so): the view of the tasks in no list, as
// the filter word list:none finds them (isNoList in go/tasklists.go).
const noList = initList === 'none' && !listSet.has('none');
function inScope(t) {
  if (initMine) {
    return TasksCore.inMine(t, initLists.includes(taskList(t)),
      { me: people.me, users: people.users, assigns: initMine === 'assign' });
  }
  if (initLists.length) return initLists.includes(taskList(t));
  if (noList) return !t.list;
  return !initList || taskList(t) === initList;
}
// The scope of the server's closed counts, matching inScope.
function scopeQuery() {
  return initMine ? '?mine=1'
    : initLists.length ? `?lists=${encodeURIComponent(initLists.join('|'))}`
    : initList ? `?list=${encodeURIComponent(initList)}` : '';
}
// The list a new task starts in here: the view's own (none in the view of
// the tasks in no list), else the default list.
function newTaskList() { return noList ? '' : initList || defaultList; }
// The edit modal's List field names a private list: the task is (or will
// be) the user's alone.
function editPrivate() { return privateLists.has(listSelect?.value || ''); }
// ?view= / [[tasks: view=…]] pick the tab.
const urlView = urlParams.get('view') || '';   // explicit per-navigation override
const embedView = appEl?.dataset.initView || '';   // [[tasks: view=…]] — always wins when specified
// ?new=1 opens the new-task modal in one embed: a pinned one if the page
// has it (as the New buttons do), else the first.
const newTarget = document.querySelector('.tasks-app[data-pinned]:not([data-headless])')
  || document.querySelector('.tasks-app:not([data-headless])');
const initNew = appEl?.dataset.initNew === '1' || (urlParams.has('new') && appEl === newTarget);
// The year Closed shows: '' is this year, an earlier one ('2025', from
// ?year= or the year switcher) comes from that year's archive.
const thisYear = String(new Date().getFullYear());
st.closedYear = /^\d{4}$/.test(urlParams.get('year') || '') && urlParams.get('year') !== thisYear
  ? urlParams.get('year') : '';
st.closedYears = [];   // years Closed can show (/api/tasks/closed-years)

// Remember the active tab per page + embed instance for the current tab only
// (sessionStorage): a reload lands on the tab it left, but a fresh load
// in a new tab / next day starts empty so the embed's configured filter loads.
const tabStorageKey = `zone-tasks-tab:${location.pathname}#${instanceId}`;
// The tab shown, whoever chose it (the user, a link's ?view=, the embed): what
// a reload comes back to. The tab the user chose is what a plain [[tasks:]]
// opens on; a link's tab isn't remembered for that.
const shownStorageKey = `zone-tasks-shown:${location.pathname}#${instanceId}`;
function saveShownTab(f) { try { sessionStorage.setItem(shownStorageKey, f); } catch (_) {} }
function loadShownTab() { try { return sessionStorage.getItem(shownStorageKey); } catch (_) { return null; } }
function saveActiveTab(f) { saveShownTab(f); try { sessionStorage.setItem(tabStorageKey, f); } catch (_) {} }
function loadActiveTab() { try { return sessionStorage.getItem(tabStorageKey); } catch (_) { return null; } }
// The settings dialog saved: the tab and the Plan switch this browser tab
// remembered give way to the new defaults.
function forgetSession() {
  try {
    sessionStorage.removeItem(tabStorageKey); sessionStorage.removeItem(shownStorageKey);
    sessionStorage.removeItem(groupKey());
  } catch (_) {}
}

Object.assign(app, initTaskData(app));
const { dismissUndo, showUndo, fetchTasks, refresh, fetchClosed, setClosedYear, updateTask, oldHistory } = app;

Object.assign(app, initTaskMenu(app));
const { headerTasksMenu, confirmDeadlineMove } = app;

// ── Filtering ──────────────────────────────────────────────────────────────

// `query` defaults to the filter field; the tag autocomplete passes the
// field minus the word being typed.
function filterTasks(query = filterQuery()) {
  return tasksInView(st.tasks, parseFilter(query, people), st.activeView, todayStr());
}

Object.assign(app, initTaskOrder(app));

Object.assign(app, initTaskSelect(app));
const { toggleSelection, syncSelBar, setSelected } = app;

// ── Rendering ──────────────────────────────────────────────────────────────
// Day-board grouping lives in TasksCore; bind it to the current date here.
function boardBuckets(list) {
  return TasksCore.boardBuckets(list, todayStr(), activeSort(), DATES);
}

Object.assign(app, initTaskRender(app));
const { render } = app;
// Page completion in the title ([[ and [text](, linkAutocomplete in
// js/completion.js, shared with the editor).
const titleInput = document.getElementById('task-edit-title' + sfx);
const descTextarea = document.getElementById('task-edit-desc' + sfx);
if (titleInput) {
  // A private task's title may link private pages, a shared one's not.
  linkAutocomplete(titleInput, { page: () => editPrivate() ? 'private/task' : '' });
  // Pasting a URL asks for its name, as in the notes and the editor
  // (pasteUrlAsLink): [name](url), shown as a link on the card.
  titleInput.addEventListener('paste', e => pasteUrlAsLink(titleInput, e));
}

Object.assign(app, initTaskChips(app));

Object.assign(app, initTaskTabs(app));
const { VIEWS, syncClosedLink, markActiveTab, filterChips, filterQuery } = app;

Object.assign(app, initTaskModal(app));
initTaskSettings(app);
const { openEditModal } = app;

// ── Drag and Drop ──────────────────────────────────────────────────────────
Object.assign(app, initTaskDnd(app));

initTaskKeys(app);

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
  filterChips.set([...tagParamToQuery(initTag).split(' '), ...tagParamToQuery(initAssignee, '@').split(' ')].filter(Boolean), true);
  filterSearch.value = initSearch || '';
}
// Precedence when a page is opened: URL ?view= > embed view > tab remembered
// this session > Focus default. So a link and a [[tasks: view=…]] embed open
// on their tab, and a plain [[tasks:]] on the one last shown in this browser
// tab. A reload (F5) is not opening the page: it stays on the tab it showed,
// whatever the URL or the embed say, so the tab it showed comes first. A
// name that isn't one of VIEWS is passed over.
const isReload = performance.getEntriesByType?.('navigation')[0]?.type === 'reload';
st.activeView = [isReload && loadShownTab(), urlView, embedView, loadActiveTab()].find(v => VIEWS.includes(v)) || 'focus';
saveShownTab(st.activeView);
if (st.activeView !== 'closed') st.lastTab = st.activeView;
applySort();
applyGroup();
markActiveTab(st.activeView);
syncClosedLink();

// ?task=<id> (search results, a page's "Linked tasks"): show exactly that
// task — the link sets its tab, and here it is selected, scrolled into view
// and briefly highlighted. By id, not by title: a title can be shared, or
// contain a #word the filter field would read as a tag.
// Not after a reload on another tab than the link's: the user has moved on.
const initTaskId = isReload && VIEWS.includes(urlView) && st.activeView !== urlView ? '' : urlParams.get('task') || '';
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
  if (st.activeView === 'closed') await fetchClosed();
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
