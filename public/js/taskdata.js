// The task app's tasks, read and written (tasks.js initTasksInstance): the
// list fetched and kept in app.st.tasks, a task created, changed, completed,
// skipped or closed undone through /api/tasks, each change redrawn and told to the
// page's other task lists, and the Undo toast that takes one back. What it
// uses of the app comes in app (its state in app.st).

import TasksCore from '../tasks-core.js';
import { refreshDueBadge } from './chrome.js';
import { formatDate, loadPeople, noticeFromResponse, people, placeToast, plainTitle, setPeople, showNotice, startsAfterDeadline } from './taskutil.js';
import { keepRendered, renderTexts, textsOf } from './tasktexts.js';

const { matchesFilter, parseFilter } = TasksCore;

// The open tasks a page view brings along (go/tasksembed.go tasksInitial),
// with their notes' and comments' HTML: the task lists the page starts with
// draw from them, with no request. Each gets its own copy, as each would
// from /api/tasks. Only at the page's start (tasks.js forgets them once its
// lists have started): a list that starts later asks the server, which may
// know more by then.
let initialJSON;   // undefined: not looked for yet; '': none
function initialTasks() {
  if (initialJSON === undefined) {
    initialJSON = '';
    try {
      const data = JSON.parse(document.getElementById('tasks-initial').textContent);
      if (data.people) setPeople(data.people);
      if (Array.isArray(data.tasks)) {
        keepRendered(textsOf(data.tasks), data);
        initialJSON = JSON.stringify(data.tasks);
      }
    } catch (_) {}
  }
  return initialJSON ? JSON.parse(initialJSON) : null;
}
export function forgetInitialTasks() { initialJSON = ''; }

export function initTaskData(app) {
const { st } = app;

// ── Undo toast ────────────────────────────────────────────────────────────

let undoTimer = null;
let undoToastEl = null;

function dismissUndo() {
  if (undoTimer) { clearTimeout(undoTimer); undoTimer = null; }
  if (undoToastEl) { undoToastEl.remove(); undoToastEl = null; }
  st.pendingUndo = null;
}

// An action that writes first and arms its undo after the response calls
// dismissUndo() before the write: until its own undo is armed, Ctrl+Z (or
// the old toast's button) would otherwise undo the action before it.
function showUndo(msg, undoFn) {
  dismissUndo();
  st.pendingUndo = undoFn;
  const el = document.createElement('div');
  el.className = 'raw-toast task-undo-toast';
  const span = document.createElement('span');
  span.textContent = msg;
  const btn = document.createElement('button');
  btn.className = 'btn task-undo-btn';
  btn.textContent = 'Undo';
  btn.addEventListener('click', () => { const fn = st.pendingUndo; dismissUndo(); fn?.(); });
  el.appendChild(span);
  el.appendChild(btn);
  placeToast(el);
  undoToastEl = el;
  undoTimer = setTimeout(dismissUndo, 6000);
}

function triggerUndo() {
  if (!st.pendingUndo) return;
  const fn = st.pendingUndo;
  dismissUndo();
  fn();
}

// ── API ────────────────────────────────────────────────────────────────────

// /api/tasks returns only open tasks, so replacing `tasks` drops the
// closed ones: they count as not loaded again (tasksGen tells a closed
// load in flight that its merge target is gone).
let tasksGen = 0;
let started = false;
async function fetchTasks() {
  // The first time, the tasks the page came with. With users, who can be
  // assigned comes with them (or is asked for once): the cards and the
  // filter's @me and @none go by it.
  const initial = !started && initialTasks();
  started = true;
  const [all] = await Promise.all([initial || fetch('/api/tasks').then(res => res.json()), loadPeople()]);
  const fetched = all.filter(app.inScope);
  await renderTexts(textsOf(fetched));
  st.tasks = fetched;
  tasksGen++;
  st.closedLoaded = false;
  app.render();
  // The list shows its tasks (data-loaded; on Closed once those are there
  // too). The browser tests wait for it before they act; nothing else
  // reads it.
  const loaded = () => { app.appEl.dataset.loaded = '1'; };
  if (st.activeView === 'closed') fetchClosed().then(loaded, loaded);
  else loaded();
  refreshClosedCount();
}

// Closed is not a tab: a link under the list opens it, labelled with the
// number of closed tasks. The count comes from the server because the
// closed tasks themselves are only loaded when the view is opened.
st.closedCount = null;
async function refreshClosedCount() {
  try {
    const res = await fetch('/api/tasks/closed-count' + app.scopeQuery());
    if (res.ok) { st.closedCount = (await res.json()).count; app.syncClosedLink(); }
  } catch (_) {}
}

// Reload this instance from the server, honoring its current tab. Used to
// pull in changes made by another embed on the same page.
async function refresh() {
  await fetchTasks();
  if (st.activeView === 'closed') await fetchClosed();
}

let closedLoading = null;
function fetchClosed() {
  if (st.closedLoaded) return Promise.resolve();
  // One request at a time.
  return closedLoading ||= loadClosed().finally(() => { closedLoading = null; });
}
async function loadClosed() {
  let closed, gen;
  // Only for the Closed view. If fetchTasks replaced `tasks` meanwhile (a
  // reload of the list while these were on their way), fetch again: what
  // was asked for may be another year's by now.
  loadClosedYears();
  do {
    gen = tasksGen;
    const res = await fetch('/api/tasks/closed' + (st.closedYear ? `?year=${st.closedYear}` : ''));
    closed = (await res.json()).filter(app.inScope);
    await renderTexts(textsOf(closed));
  } while (gen !== tasksGen);
  const existingIds = new Set(st.tasks.map(t => t.id));
  for (const t of closed) {
    if (!existingIds.has(t.id)) st.tasks.push(t);
  }
  st.closedLoaded = true;
  app.render();
}

async function loadClosedYears() {
  try {
    const res = await fetch('/api/tasks/closed-years' + app.scopeQuery());
    if (res.ok) { st.closedYears = (await res.json()).years; if (st.activeView === 'closed') app.render(); }
  } catch (_) {}
}

// Show another year in Closed: drop the loaded closed tasks (the
// match count in the other views counts whatever is loaded) and fetch that
// year's. tasksGen tells a load in flight that its year is gone.
function setClosedYear(year) {
  year = year === app.thisYear ? '' : year;
  if (year === st.closedYear) return;
  st.closedYear = year;
  st.tasks = st.tasks.filter(t => !t.closed_at);
  tasksGen++;
  st.closedLoaded = false;
  if (st.activeView === 'closed') fetchClosed();
  app.render();
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
    if (app.inScope(task)) st.tasks.push(task);
    if (msg && !app.headless) showNotice(msg);
    app.render();
    refreshDueBadge(); refreshClosedCount();
    app.refreshOtherInstances(app.instanceId);
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
      if (app.inScope(updated)) st.tasks[idx] = updated;
      else st.tasks.splice(idx, 1);
      if (msg) offerUndoLeave(msg, [[id, editSnapshot(before)]]);
    }
    // Completion/skip/reopen of a recurring task spawns or removes an
    // instance server-side — re-fetch to pick it up.
    if (updated.recurrence && (data.completed || data.skip || 'wont_do' in data)) {
      await fetchTasks();
    } else {
      app.render();
    }
    refreshDueBadge(); refreshClosedCount();
    app.refreshOtherInstances(app.instanceId);
    return updated;
  }
  await noticeFromResponse(res, 'Update rejected');
  return null;
}

// A task saved out of a list view, into another list or none; say where it went.
function listMovedNotice(task) {
  const name = app.taskList(task);
  return name ? `Saved to list “${name}”.` : 'Saved; it no longer shows here.';
}

// Why a saved task left this view, or null if it still shows: it went
// into a separate list (or out of this one), or it no longer passes the
// filter (e.g. #sam removed on Sam's page). `before` null: a new task.
function leftViewMessage(before, after) {
  if (!app.inScope(after)) return listMovedNotice(after);
  const f = parseFilter(app.filterQuery(), people);
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
    ...(people.users ? { assignee: t.assignee || null } : {}),
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
  if (app.headless) return;
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
    app.refreshOtherInstances(app.instanceId);
  });
}

// The task's box ticked (a click, Space): it completes after its grace
// period, once the user has said so if subtasks are still open. False when
// they cancelled.
async function tickTask(id) {
  const task = st.tasks.find(t => t.id === id);
  if (task && !(await app.confirmOpenSubtasks([task]))) return false;
  beginComplete(id, app.listEl.querySelector(`.task-item[data-id="${CSS.escape(id)}"]`));
  return true;
}

function beginComplete(id, li) {
  const cb = li?.querySelector('.task-checkbox input');
  if (cb) cb.checked = true;
  if (li) li.classList.add('task-completing');
  if (app.pendingCompletions.has(id)) clearTimeout(app.pendingCompletions.get(id));
  const tid = setTimeout(() => {
    app.pendingCompletions.delete(id);
    // A re-render since the tick replaced the card: fade out the current one.
    li = app.listEl.querySelector(`.task-item[data-id="${CSS.escape(id)}"]`);
    if (li) li.classList.add('task-completing-out');
    setTimeout(async () => {
      const task = st.tasks.find(t => t.id === id);
      const before = task?.keep && keepSnapshot(task);
      if (before) dismissUndo();
      const done = await updateTask(id, { completed: true });
      // A keep task is not archived, so Closed can't reopen it: offer undo.
      if (done && before) showUndo(`“${plainTitle(task.title)}” done`, () => updateTask(id, before));
    }, 400);
  }, 4600);
  app.pendingCompletions.set(id, tid);
}

function cancelPendingComplete(id) {
  if (!app.pendingCompletions.has(id)) return false;
  clearTimeout(app.pendingCompletions.get(id));
  app.pendingCompletions.delete(id);
  const li = app.listEl.querySelector(`.task-item[data-id="${CSS.escape(id)}"]`);
  if (li) li.classList.remove('task-completing', 'task-completing-out');
  const cb = li?.querySelector('.task-checkbox input');
  if (cb) cb.checked = false;
  return true;
}

// What completing a keep task changes (mirrors keepDone in go/taskstore.go), so
// undo can put it back.
function keepSnapshot(t) {
  return { start_date: t.start_date || null, due_date: t.due_date || null,
           last_done: t.last_done || null, history: t.history || [] };
}

// Complete the tasks `ids` (wontDo: close them undone instead), or reopen them.
async function completeTasks(ids, completed, wontDo = false) {
  const res = await fetch('/api/tasks/batch-complete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(wontDo ? { ids, completed, wont_do: true } : { ids, completed }),
  });
  if (res.ok) {
    // Re-fetch to capture any spawned recurring tasks
    await fetchTasks();
    refreshDueBadge(); refreshClosedCount();
    app.refreshOtherInstances(app.instanceId);
  }
}


async function afterBatchWrite() {
  await fetchTasks();
  refreshDueBadge(); refreshClosedCount();
  app.refreshOtherInstances(app.instanceId);
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
  if (startsAfterDeadline(task, startDate)) return app.planOnDay(id, startDate);
  const prev = task?.start_date ?? null;
  const recurrence = task?.recurrence ?? null;
  const history = oldHistory(id);
  updateTask(id, { start_date: startDate });
  const msg = startDate ? `Starts ${formatDate(startDate)}` : 'Start date cleared';
  showUndo(msg, () => updateTask(id, { start_date: prev, recurrence, history }));
}

// Close the open tasks among `ids` undone (won't do), in one change with
// one undo, which reopens them. One task alone goes through markWontDo.
async function wontDoMany(ids) {
  const open = ids.filter(id => { const t = st.tasks.find(tk => tk.id === id); return t && !t.closed_at; });
  if (open.length === 1) return markWontDo(open[0]);
  if (!open.length) return;
  dismissUndo();
  await completeTasks(open, true, true);
  showUndo(`${open.length} tasks marked won't do`, () => completeTasks(open, false));
}

return { dismissUndo, showUndo, triggerUndo, fetchTasks, refresh, fetchClosed, setClosedYear, createTask, updateTask, leftViewMessage, tickTask, cancelPendingComplete, keepSnapshot, completeTasks, afterBatchWrite, oldHistory, markWontDo, wontDoMany, skipOccurrence, setStart };
}
