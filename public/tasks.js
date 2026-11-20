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
import { cleanList } from './js/mdtext.js';
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
// Someone else changed a task (js/chrome.js asks every minute): the lists
// show it without the tab having been away. A long list of every task (All
// tasks or a tag's view, of a team) at most every EVERY_MS: there some task
// changes every minute, and such a list is all of them, loaded again each
// time.
const EVERY_MS = 5 * 60 * 1000;
document.addEventListener('zone:tasks-changed', () => {
  tasksInstances.forEach(inst => {
    const wait = inst.holdsEvery() ? inst.loadedAt() + EVERY_MS - Date.now() : 0;
    if (wait <= 0) inst.refreshIfIdle();
    // Told once (the page then has the server's word): it loads when its time has come.
    else if (!inst.later) inst.later = setTimeout(() => { inst.later = null; inst.refreshIfIdle(); }, wait);
  });
});
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
const cutEl = document.getElementById('task-cut' + sfx);
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
  boardDivider, clearBoardDropOver, dropOnBucket, dropWrite, planOnDay,
  taskList,
  filterTasks, boardBuckets,
  refreshOtherInstances, listTooltip, listMembers, listGroup, assignableIn, widens, narrows, mayNarrow, inScope, listName, editPrivate, newTaskList,

  flashTask,

  forgetSession,
  scopeQuery, headless,
  sortSel, cutEl,
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
  get inboxView() { return inboxView; },
  get inboxPrivate() { return inboxPrivate; },
  get initMine() { return initMine; },
  isPrivateList,
  get ownsOrder() { return !!initMine; },
  own, setOwnOrder,
};

st.tasks = [];
st.closedLoaded = false;   // the closed tasks are among st.tasks
st.activeView = 'open';   // how the tab shown draws its tasks: 'open', 'plan', 'closed' (tasktabs.js)
st.today = true;           // the Today tab: the Open tab's tasks filtered
st.draggedId = null;
st.selectedId  = null;
st.selectedIds = new Set();
st.anchorId    = null;
// Selection mode (⋯ → Select; meant for touch, which has no Shift or Ctrl):
// a tap or click on a card adds or drops it, nothing else on the card reacts.
st.selectMode  = false;
st.closedLimit = 50;
st.drawAll = new Set();    // the long sections shown whole (taskrender.js windowed)
st.mustDraw = new Set();   // tasks that have to be drawn: a link's, a new one
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
  if (refusesDrop(st.tasks.find(t => t.id === st.draggedId), div)) return;   // not this task
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
// What a section stands for other than a day (By person, By list): the
// user a drop there assigns the task to ('' no one), the list it moves it
// to ('' the Inbox); undefined on the board by day.
const dropPerson = div => div.dataset.dropPerson;
const dropList = div => div.dataset.dropList;
// Why the section doesn't take the task, '' when it does: a private task
// has no assignee, and a shared task stays shared.
function refusesDrop(task, div) {
  if (!task) return '';
  if (dropPerson(div) !== undefined && task.private && dropPerson(div) !== people.me) {
    return 'A private task is yours alone: move it to a shared list to assign it';
  }
  if (dropList(div) !== undefined && !task.private && isPrivateList(dropList(div))) return 'A shared task stays shared';
  return '';
}
// Write what a drop on the section means for the task: a day starts it
// there, a person's section assigns it, a list's moves it (a private task
// into a shared list only after asking: everyone sees it from then on).
// Whether it was written.
async function dropWrite(id, div) {
  const task = st.tasks.find(t => t.id === id);
  if (!task) return false;
  const why = refusesDrop(task, div);
  if (why) { showNotice(why); return false; }
  if (dropPerson(div) !== undefined) {
    return task.private ? true : app.assignTasks([task], dropPerson(div));
  }
  if (dropList(div) !== undefined) {
    const ref = dropList(div), toPrivate = isPrivateList(ref);
    if (task.private && !toPrivate && !await app.confirmShare(task, listName(ref))) return false;
    if (widens(task, ref) && !await app.confirmShare(task, listName(ref), listName(taskList(task)))) return false;
    return app.moveTasks([task], ref, toPrivate, false);
  }
  return app.startOn([id], div.dataset.dropDay);
}
// Move a task to a board section: a day sets its start date, Unplanned
// clears it, and Later asks for a date; a person's section assigns it, a
// list's moves it there (dropWrite). A drop on the task's own section
// does nothing, except on Later, where it picks another later date, and a
// carried-over task's on Today, which starts it today: committing to it
// again.
function dropOnBucket(id, div) {
  clearBoardDropOver();
  const el = listEl.querySelector(`.task-item[data-id="${CSS.escape(id)}"]`);
  const task = st.tasks.find(t => t.id === id);
  const recommit = div.dataset.dropDay === todayStr() && task && TasksCore.isCarried(task, todayStr());
  if (el && boardDivider(el) === div && div.dataset.dropBucket !== 'later' && !recommit) return;
  if (dropPerson(div) !== undefined || dropList(div) !== undefined) dropWrite(id, div);
  else if (div.dataset.dropBucket === 'unplanned') app.startOn([id], null);
  else if (div.dataset.dropBucket === 'later') {
    // "Later" is a range, not a day — ask which date (min: beyond this week).
    openDatePicker('', d => { if (d) app.startOn([id], d); }, div, dateOffset(7));
  }
  else if (div.dataset.dropDay) app.startOn([id], div.dataset.dropDay);
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
  showUndo(`Starts and due ${formatDate(day)}`, () => updateTask(id, old));
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
    if (i !== -1) st.tasks[i] = own(updated);
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
// WIP limit: Today warns and Plan flags a day at >= LIMIT tasks. Off unless
// the view shown has one: a list's, My tasks' or All tasks' limit.
const LIMIT = parseInt(appEl?.dataset.initLimit, 10) || null;
// Lists: a list is a place a task is in (its `list`; one, or none). A view
// of a list ([[tasks: list=<name>]], /tasks?list=) shows its tasks, and a
// new task there goes into it; My tasks (data-init-mine, with the lists
// left out of it in data-init-out) shows what TasksCore.inMine says.
// Elsewhere (all tasks, a tag's embed) every task shows, with its list as
// a chip. A task in no list is in the Inbox, where a new task goes unless
// its view has a list: with users (data-inbox-private) its maker's own. The lists come as refs: a list's
// name, or shared/<name> for the shared one beside the user's own list of
// that name; data-lists has those the user sees, with which are private
// (theirs alone, and so are the tasks in them).
const initList = cleanList(appEl?.dataset.initList);
// My tasks: 'assign' where assignment counts for the user, else 'lists';
// and the lists left out of it.
const initMine = appEl?.dataset.initMine || '';
const initOut = (appEl?.dataset.initOut || '').split('|').filter(Boolean);
// My tasks has a manual order of its own, the user's (mineOrder in
// go/tasklists.go): a list's order (a task's sort_order) is the list's, for
// everyone who looks at it, and ranking across lists in My tasks moves
// nothing there. The view lays its order over its own copies of the tasks
// (own: a task's place in it as its sort_order, none: not placed there
// yet, on top, newest first), so the
// rest of the app orders as it does everywhere; a move saves the order as
// the user's (postReorder) and writes no task.
let ownOrder = null;
function setOwnOrder(ids) { ownOrder = new Map(ids.map((id, i) => [id, i])); }
function own(t) {
  if (initMine && ownOrder && t) t.sort_order = ownOrder.has(t.id) ? ownOrder.get(t.id) : null;
  return t;
}
// With users a task in no list is in its user's Inbox: private.
const inboxPrivate = appEl?.dataset.inboxPrivate === '1';
let listChoices = [];
try { listChoices = JSON.parse(appEl?.dataset.lists || '[]'); } catch (_) {}
const listSet = new Set(listChoices.map(l => l.ref));
// The members of the groups' lists, where the rules ask who has a task
// (TasksCore.assigneeGone): the page's lists are every task app's.
people.members = { ...people.members, ...Object.fromEntries(listChoices.filter(l => l.members).map(l => [l.ref, l.members])) };
const privateLists = new Set(listChoices.filter(l => l.private).map(l => l.ref));
// A list made while the page is open (New list… in the dialog that moves
// tasks to a list, js/taskselect.js, which says so to every task app of the
// page): one more list the user sees, in the List field and the dialogs.
document.addEventListener('zone:list-made', e => {
  const { ref, private: isPrivate } = e.detail || {};
  if (!ref || listSet.has(ref)) return;
  listChoices.push({ ref, private: !!isPrivate });
  listChoices.sort((a, b) => listName(a.ref).localeCompare(listName(b.ref)) || a.ref.localeCompare(b.ref));
  listSet.add(ref);
  if (isPrivate) privateLists.add(ref);
});
// A group's list: its members, who its tasks can be given to (a Set of
// names); null for a list that is everyone's, where that is whoever can be
// given tasks (people.users).
function listMembers(ref) {
  const members = listChoices.find(l => l.ref === ref)?.members;
  return members ? new Set(members) : null;
}
// The group whose list ref is ('' for any other).
function listGroup(ref) { return listChoices.find(l => l.ref === ref)?.group || ''; }
// The task would leave a group's list for a list that is everyone's: more
// people see it than now (the dialogs say so, a drop asks).
function widens(task, ref) {
  return !task.private && !!listMembers(taskList(task) || '') && !isPrivateList(ref) && !listMembers(ref);
}
// The task would go to a list that some who see it now are none of: fewer
// people see it than now. Their names (null: none lose it); the dialogs say
// so, and only who made the task or an admin moves it there (applyList).
function narrows(task, ref) {
  const to = listMembers(ref);
  if (task.private || isPrivateList(ref) || !to || (taskList(task) || '') === ref) return null;
  const now = listMembers(taskList(task) || '') || people.users || new Set();
  const lost = [...now].filter(name => !to.has(name));
  return lost.length ? lost : null;
}
function mayNarrow(task) { return document.body.dataset.admin === '1' || !task.created_by || task.created_by === people.me; }
// Who a task in the list ref can be given to.
function assignableIn(ref) { return listMembers(ref) || people.users || new Set(); }
// A list's name, as its chip and the dialogs show it.
function listName(ref) { return String(ref).replace(/^shared\//, ''); }
// The tooltip of a list's chip (cards, the filter).
function listTooltip(ref) {
  const r = String(ref).replace(/^-?list:/, '').toLowerCase();
  if ((r === 'inbox' || r === 'none') && !listSet.has(r)) return inboxPrivate ? 'In your Inbox: only you see it' : 'In the Inbox: in no list';
  if (listGroup(r)) return `The group ${listGroup(r)}'s list: only its members (${[...(listMembers(r) || [])].join(', ')}) see it and its tasks`;
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
const { initCut } = app;

// The list a task is in, as a ref (taskList in go/tasklists.go): its `list`
// names it, and `private` says which of two lists of one name; null for
// none (and for a list this user doesn't see).
function taskList(t) {
  if (!t.list) return null;
  const ref = !t.private && listSet.has('shared/' + t.list) ? 'shared/' + t.list : t.list;
  return listSet.has(ref) && privateLists.has(ref) === !!t.private ? ref : null;
}
// list=inbox (no list is called so): the Inbox, the view of the tasks in
// no list, as the filter word list:inbox finds them (isInbox in
// go/tasklists.go).
const inboxView = initList === 'inbox' && !listSet.has('inbox');
function inScope(t) {
  if (initMine) {
    return TasksCore.inMine(t, initOut.includes(taskList(t)), { me: people.me, assigns: initMine === 'assign' });
  }
  if (inboxView) return !t.list;
  return !initList || taskList(t) === initList;
}
// The scope of the server's closed counts, matching inScope.
function scopeQuery() {
  return initMine ? '?mine=1' : initList ? `?list=${encodeURIComponent(initList)}` : '';
}
// The list a new task starts in here: the view's own, else none: the Inbox.
function newTaskList() { return inboxView ? '' : initList; }
// The edit modal's List field names a private list, or with users the
// Inbox: the task is (or will be) the user's alone.
function isPrivateList(ref) { return ref ? privateLists.has(ref) : inboxPrivate; }
function editPrivate() { return isPrivateList(listSelect?.value || ''); }
// ?view= / [[tasks: view=…]] pick the tab, by the views' names: today,
// open, closed, and with open group= how it shows its tasks (none, day,
// person, list; without it as the view's own group= says). The app keeps a
// tab by a word (namedView: 'focus' Today, 'open' the Open tab as one list,
// 'plan' as the board, 'closed'; tasktabs.js); any other name is none.
const urlViewName = urlParams.get('view') || '';   // explicit per-navigation override
const urlGroup = urlParams.get('group') || '';
const embedViewName = appEl?.dataset.initView || '';   // [[tasks: view=…]] — always wins when specified
// ?new=1 opens the new-task modal in one embed: My tasks' or the Inbox's
// if the page has it (data-home, as the phone's Task tab does), else the first.
const newTarget = document.querySelector('.tasks-app[data-home]:not([data-headless])')
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
  // Today is Open with a filter (tasktabs.js): 'focus' is the word for it.
  return tasksInView(st.tasks, parseFilter(query, people), app.shown(), todayStr(), initMine ? people.me : '');
}

Object.assign(app, initTaskOrder(app));

Object.assign(app, initTaskSelect(app));
const { toggleSelection, syncSelBar, setSelected } = app;

// ── Rendering ──────────────────────────────────────────────────────────────
// Day-board grouping lives in TasksCore; bind it to the current date here.
// What the board's sections stand for is st.cut (js/taskview.js): days,
// people or lists.
function boardBuckets(list) {
  if (st.cut === 'person') return TasksCore.personBuckets(list, todayStr(), activeSort(), people);
  if (st.cut === 'list') {
    // The user's own lists before the shared ones, each A–Z.
    const lists = [...listChoices].sort((a, b) => b.private - a.private
      || listName(a.ref).localeCompare(listName(b.ref)) || a.ref.localeCompare(b.ref))
      .map(l => ({ ref: l.ref, label: (l.private ? '🔒 ' : '') + listName(l.ref) + (l.ref.startsWith('shared/') ? ' (shared)' : '') }));
    return TasksCore.listBuckets(list, todayStr(), activeSort(), lists, taskList)
      .map(b => b.list ? b : { ...b, label: (inboxPrivate ? '🔒 ' : '') + 'Inbox' });
  }
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
// holdsEvery: the list holds every task the user sees (no list's, not My
// tasks), and they are many (what a long list is: LAZY_FROM in
// js/taskdata.js); loadedAt: when it last asked for its tasks.
tasksInstances.push({ id: instanceId, refresh, refreshIfIdle, syncSelBar, openEdit: openEditModal,
  holdsEvery: () => !scopeQuery() && st.tasks.length > 150, loadedAt: () => st.loadedAt || 0 });

openEdit = openEditModal;

// ── Init ───────────────────────────────────────────────────────────────────

// Apply URL-param pre-filtering
// [[tasks: tag=…]] / ?tag= and ?search= all land in the one filter field.
const initSearch = urlParams.get('search') || '';
// ?tag= / [[tasks: tag=…]] arrive as chips, ?search= as text.
if (filterSearch) {
  // ?from=me (with users): the tasks that user made, as the word from:me.
  const initFrom = document.body.dataset.users === '1' && /^[a-z0-9-]+$/i.test(urlParams.get('from') || '') ? ['from:' + urlParams.get('from').toLowerCase()] : [];
  filterChips.set([...tagParamToQuery(initTag).split(' '), ...tagParamToQuery(initAssignee, '@').split(' '), ...initFrom].filter(Boolean), true);
  filterSearch.value = initSearch || '';
}
// Precedence when a page is opened: URL ?view= > embed view > tab remembered
// this session > Focus default. So a link and a [[tasks: view=…]] embed open
// on their tab, and a plain [[tasks:]] on the one last shown in this browser
// tab. A reload (F5) is not opening the page: it stays on the tab it showed,
// whatever the URL or the embed say, so the tab it showed comes first. A
// name that isn't one of VIEWS is passed over.
const isReload = performance.getEntriesByType?.('navigation')[0]?.type === 'reload';
// What nothing else says: the view's own default (data-default-view: today
// for what is the user's to do, open for a place they look into).
const namedView = (name, group) => name === 'today' ? 'focus' : name === 'closed' ? 'closed' : name !== 'open' ? ''
  : group === 'none' ? 'open' : ['day', 'person', 'list'].includes(group) ? 'plan' : app.openView();
const urlView = namedView(urlViewName, urlGroup);
const embedView = namedView(embedViewName, appEl?.dataset.initGroup || '');
const asked = [isReload && loadShownTab(), urlView, embedView, loadActiveTab(),
  namedView(appEl?.dataset.defaultView || '', appEl?.dataset.initGroup || '')].find(v => VIEWS.includes(v)) || 'focus';
// The board's sections on the Open tab: what a link's ?view=open&group=
// says, else what the view or this browser tab says.
const byLink = !(isReload && VIEWS.includes(loadShownTab()));
initCut(urlView === 'plan' && ['day', 'person', 'list'].includes(urlGroup) && byLink, urlGroup);
// Today is the Open tab's tasks filtered (st.today), drawn as its own
// grouping says: a link's ?view=today&group=, else this browser tab's or
// the view's.
st.today = asked === 'focus';
st.activeView = !st.today ? asked
  : urlView === 'focus' && byLink && ['none', 'person', 'list'].includes(urlGroup) ? app.todayView(urlGroup) : app.todayView();
saveShownTab(app.shown());
if (st.activeView !== 'closed') st.lastTab = app.shown();
applySort();
applyGroup();
markActiveTab(st.activeView);
syncClosedLink();

// ?task=<id> (search results, a page's "Linked tasks"): show exactly that
// task — the link sets its tab, and here it is selected, scrolled into view
// and briefly highlighted. By id, not by title: a title can be shared, or
// contain a #word the filter field would read as a tag.
// Not after a reload on another tab than the link's: the user has moved on.
const initTaskId = isReload && VIEWS.includes(urlView) && app.shown() !== urlView ? '' : urlParams.get('task') || '';
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
