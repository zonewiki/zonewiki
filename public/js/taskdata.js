// The task app's tasks, read and written (tasks.js initTasksInstance): the
// list fetched and kept in app.st.tasks, a task created, changed, completed,
// skipped or deleted through /api/tasks, each change redrawn and told to the
// page's other task lists, and the Undo toast that takes one back. What it
// uses of the app comes in app (its state in app.st).

import TasksCore from '../tasks-core.js';
import { refreshDueBadge } from './chrome.js';
import { formatDate, noticeFromResponse, placeToast, plainTitle, showNotice, startsAfterDeadline } from './taskutil.js';
import { renderTexts, textsOf } from './tasktexts.js';

const { matchesFilter, parseFilter } = TasksCore;

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
// completed ones: they count as not loaded again (tasksGen tells a completed
// load in flight that its merge target is gone).
let tasksGen = 0;
async function fetchTasks() {
  const res = await fetch('/api/tasks');
  const fetched = (await res.json()).filter(app.inScope);
  await renderTexts(textsOf(fetched));
  st.tasks = fetched;
  tasksGen++;
  st.completedLoaded = false;
  app.render();
  if (st.activeView === 'completed') fetchCompleted();
  refreshCompletedCount();
}

// Completed is not a tab: a link under the list opens it, labelled with the
// number of completed tasks. The count comes from the server because the
// completed tasks themselves are only loaded when the view is opened.
st.completedCount = null;
async function refreshCompletedCount() {
  try {
    const res = await fetch('/api/tasks/completed-count' + app.scopeQuery());
    if (res.ok) { st.completedCount = (await res.json()).count; app.syncCompletedLink(); }
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
  if (st.completedLoaded) return Promise.resolve();
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
    completed = (await res.json()).filter(app.inScope);
    await renderTexts(textsOf(completed));
  } while (gen !== tasksGen);
  const existingIds = new Set(st.tasks.map(t => t.id));
  for (const t of completed) {
    if (!existingIds.has(t.id)) st.tasks.push(t);
  }
  st.completedLoaded = true;
  app.render();
}

async function loadCompletedYears() {
  try {
    const res = await fetch('/api/tasks/completed-years' + app.scopeQuery());
    if (res.ok) { st.completedYears = (await res.json()).years; if (st.activeView === 'completed') app.render(); }
  } catch (_) {}
}

// Show another year in Completed: drop the loaded completed tasks (the
// match count in the other views counts whatever is loaded) and fetch that
// year's. tasksGen tells a load in flight that its year is gone.
function setCompletedYear(year) {
  year = year === app.thisYear ? '' : year;
  if (year === st.completedYear) return;
  st.completedYear = year;
  st.tasks = st.tasks.filter(t => !t.completed_at);
  tasksGen++;
  st.completedLoaded = false;
  if (st.activeView === 'completed') fetchCompleted();
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
    refreshDueBadge(); refreshCompletedCount();
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
    refreshDueBadge(); refreshCompletedCount();
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
  const f = parseFilter(app.filterQuery());
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

async function deleteTask(id) {
  const res = await fetch(`/api/tasks/${id}`, { method: 'DELETE' });
  if (res.ok) {
    st.tasks = st.tasks.filter(t => t.id !== id);
    app.render();
    refreshDueBadge(); refreshCompletedCount();
    app.refreshOtherInstances(app.instanceId);
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
    app.render();
    refreshDueBadge(); refreshCompletedCount();
    app.refreshOtherInstances(app.instanceId);
  }
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
      // A keep task is not archived, so Completed can't reopen it: offer undo.
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
    app.refreshOtherInstances(app.instanceId);
  }
}


async function afterBatchWrite() {
  await fetchTasks();
  refreshDueBadge(); refreshCompletedCount();
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

// Confirm, delete, and offer undo that restores the full snapshots (ids and
// every field). beforeDelete runs after confirmation.
async function deleteWithUndo(ids, beforeDelete) {
  const count = ids.length;
  const title = count === 1 ? plainTitle(st.tasks.find(t => t.id === ids[0])?.title) : '';
  if (!await app.confirmDelete(title, count)) return;
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

return { dismissUndo, showUndo, triggerUndo, fetchTasks, refresh, fetchCompleted, setCompletedYear, createTask, updateTask, leftViewMessage, tickTask, cancelPendingComplete, keepSnapshot, completeTasks, afterBatchWrite, oldHistory, markWontDo, skipOccurrence, setStart, deleteWithUndo };
}
