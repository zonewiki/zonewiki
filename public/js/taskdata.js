// The task app's tasks, read and written (tasks.js initTasksInstance): the
// list fetched and kept in app.st.tasks, a task created, changed, completed,
// skipped or closed undone through /api/tasks, each change redrawn and told to the
// page's other task lists, and the Undo toast that takes one back. What it
// uses of the app comes in app (its state in app.st).

import TasksCore from '../tasks-core.js';
import { pageScopes, refreshDueBadge } from './chrome.js';
import { formatDate, loadPeople, noticeFromResponse, people, placeToast, plainTitle, setPeople, showNotice, startsAfterDeadline } from './taskutil.js';
import { keepRendered, renderTexts, textsOf } from './tasktexts.js';

const { matchesFilter, parseFilter } = TasksCore;

// The open tasks a page view brings along (go/tasksembed.go tasksInitial),
// with their notes' and comments' HTML: the task lists the page starts with
// draw from them, with no request. Each gets its own copy, as each would
// from /api/tasks. Only at the page's start (tasks.js forgets them once its
// lists have started): a list that starts later asks the server, which may
// know more by then. The block is a <script> of JSON, the first child of the
// first list's own element: never looked up by an id, which a page's raw HTML
// could give one of its elements first (with "rendered" notes of its own,
// which would go into the cards as HTML), and raw HTML makes no <script>
// (go/markdown.go defuseHTML).
const INITIAL = '.tasks-app > script.tasks-initial[type="application/json"]';
let initialJSON;   // undefined: not looked for yet; '': none
let initialOrder = null;   // with them: the user's own order of My tasks (ids)
function initialTasks() {
  if (initialJSON === undefined) {
    initialJSON = '';
    try {
      const data = JSON.parse(document.querySelector(INITIAL).textContent);
      if (data.people) setPeople(data.people);
      if (data.word) document.body.dataset.tasksWord = data.word;
      if (Array.isArray(data.order)) initialOrder = data.order;
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
// The notes' and comments' HTML of a list's tasks, before it is drawn, so no
// card shows plain text. Not of a long list (over LAZY_FROM tasks): there
// each card's texts are fetched when its details are opened
// (taskview.js setExpanded), and the list is there without every
// description of the wiki rendered and sent. (The page does the same with
// the tasks it brings along: tasksInitial in go/tasksembed.go.)
const LAZY_FROM = 150;
const lazyTexts = list => list.length > LAZY_FROM ? Promise.resolve(false) : renderTexts(textsOf(list));
let tasksGen = 0;
let wrote = 0;   // the answers to this list's writes of one task so far
let started = false;
async function fetchTasks() {
  // The first time, the tasks the page came with. With users, who can be
  // assigned comes with them (or is asked for once): the cards and the
  // filter's @me and @none go by it.
  const initial = !started && initialTasks();
  started = true;
  // The word of the tasks held (the Zone-Tasks header; js/chrome.js compares
  // it with the server's every minute).
  // A list asks for the tasks of its scope (the server's rule is the one
  // inScope has); My tasks for its own order with them (app.ownsOrder).
  // held=: what the page's lists hold, so the word is of all of them.
  const query = [app.scopeQuery().slice(1), app.ownsOrder ? 'with=order' : '',
    pageScopes() ? 'held=' + encodeURIComponent(pageScopes()) : ''].filter(Boolean).join('&');
  const load = () => fetch('/api/tasks' + (query ? '?' + query : '')).then(async res => {
    const word = res.headers.get('Zone-Tasks');
    if (word) document.body.dataset.tasksWord = word;
    const data = await res.json();
    if (!app.ownsOrder) return data;
    app.setOwnOrder(data.order || []);
    return data.tasks;
  });
  if (initial && app.ownsOrder) app.setOwnOrder(initialOrder || []);
  // An answer that a write of this list overtook (updateTask's, answered
  // while the tasks were on their way: `wrote`) is from before it, and
  // would put the task back as it was: asked again.
  let fetched, at, first = true;
  do {
    at = wrote;
    const [all] = await Promise.all([(first && initial) || load(), loadPeople()]);
    first = false;
    if (app.initUpdates) {
      // Updates: the tasks with news, open or closed, each with the news it
      // came with (st.news); and those shown before that have none anymore
      // (read since: the server no longer answers them), as they are held, so
      // a card stays until the page is left.
      fetched = all.filter(t => TasksCore.newsFor(t, people.me));
      for (const t of fetched) st.news.set(t.id, app.newsOf(t));
      const have = new Set(fetched.map(t => t.id));
      fetched.push(...st.tasks.filter(t => st.news.has(t.id) && !have.has(t.id)));
    } else {
      fetched = all.filter(t => app.inScope(t) && !t.closed_at).map(app.own);
    }
    await lazyTexts(fetched);
  } while (at !== wrote);
  // A ticked task that is open again (reopened somewhere else) is as the
  // server says. (A keep task stays open when ticked.)
  for (const [id, was] of st.ticked) {
    if (!was.keep && !tickSaves.has(id) && fetched.some(t => t.id === id)) st.ticked.delete(id);
  }
  // The ticked tasks are closed, which the server didn't answer: they stay
  // held as they are, for what acts on their cards (the menu, Space, the
  // dialog).
  const have = new Set(fetched.map(t => t.id));
  fetched.push(...st.tasks.filter(t => t.closed_at && st.ticked.has(t.id) && !have.has(t.id)));
  st.tasks = fetched;
  st.loadedAt = Date.now();
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
  if (app.initUpdates) return;   // no Closed view
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
// What the Closed view asks the server for: the year, this list's scope,
// the filter field's words, how many it shows (st.closedLimit) and the
// task a link names, which is among them however far down. The server
// answers that page and how many there are in all (st.closedTotal): a
// year of a team's closed tasks is far more than the view shows.
function closedQuery() {
  const q = new URLSearchParams(app.scopeQuery().slice(1));
  if (st.closedYear) q.set('year', st.closedYear);
  if (app.filterQuery()) q.set('q', app.filterQuery());
  q.set('limit', st.closedLimit);
  const linked = new URLSearchParams(location.search).get('task');
  if (linked) q.set('task', linked);
  return q.toString();
}
async function loadClosed() {
  let closed, gen, asked, total = 0;
  // Only for the Closed view. If fetchTasks replaced `tasks` meanwhile (a
  // reload of the list while these were on their way), or the filter or
  // the number shown is another one by now, fetch again.
  loadClosedYears();
  do {
    gen = tasksGen;
    asked = closedQuery();
    const res = await fetch('/api/tasks/closed?' + asked);
    const page = await res.json();
    closed = (page.tasks || []).filter(app.inScope);
    total = page.total || 0;
    await lazyTexts(closed);
  } while (gen !== tasksGen || asked !== closedQuery());
  // The page in the place of the closed tasks held.
  st.tasks = st.tasks.filter(t => !t.closed_at);
  const existingIds = new Set(st.tasks.map(t => t.id));
  for (const t of closed) {
    if (!existingIds.has(t.id)) st.tasks.push(app.own(t));
  }
  st.closedAsked = asked;
  st.closedTotal = total;
  st.closedLoaded = true;
  app.render();
}
// The Closed view shows what it asked for: when the filter, or how many it
// shows, is another one by now, it asks again (a moment after the last
// key).
let closedSoon = null;
function closedStale() {
  if (st.activeView !== 'closed' || !st.closedLoaded || st.closedAsked === closedQuery()) return;
  clearTimeout(closedSoon);
  closedSoon = setTimeout(() => {
    if (st.activeView !== 'closed' || st.closedAsked === closedQuery()) return;
    st.closedLoaded = false;
    fetchClosed();
  }, 200);
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
    if (app.inScope(task)) st.tasks.push(app.own(task));
    if (msg && !app.headless) showNotice(msg);
    app.render();
    refreshDueBadge(); refreshClosedCount();
    app.refreshOtherInstances(app.instanceId);
    return task;
  }
  await noticeFromResponse(res, 'Could not create task');
  return null;
}

// A task someone gave the user, which they now open (its dialog, its
// details): the server is told, once, and logs "seen" in its history. So
// it is no longer new to them (TasksCore.givenToMe: its card's mark, the
// Tasks links' count), and whoever gave it sees that it arrived.
const seenSent = new Set();
function markSeen(id) {
  const task = st.tasks.find(t => t.id === id);
  if (!task || !TasksCore.newsFor(task, people.me)) return null;
  // Once for one piece of news: a second look at the same news says nothing.
  const news = TasksCore.newsFor(task, people.me), key = [id, news.kind, news.by, news.at].join('|');
  if (seenSent.has(key)) return null;
  seenSent.add(key);
  return updateTask(id, { seen: true });   // the task as it is now, for who shows its history
}
// The tasks with news for the user among those this list holds, and all of
// them seen at once (Updates' Mark all read): each as the server answers
// it, in the place of the one held, so its card stays, read.
const updatesNow = () => st.tasks.filter(t => TasksCore.newsFor(t, people.me));
async function markAllSeen() {
  const ids = updatesNow().map(t => t.id);
  if (!ids.length) return;
  const seen = await Promise.all(ids.map(id => fetch(`/api/tasks/${id}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ seen: true }) })
    .then(res => res.ok ? res.json() : null).catch(() => null)));
  for (const t of seen) {
    const i = t ? st.tasks.findIndex(o => o.id === t.id) : -1;
    if (i !== -1) st.tasks[i] = app.own(t);
  }
  await renderTexts(textsOf(seen.filter(Boolean)));
  app.render();
  refreshDueBadge();
  app.refreshOtherInstances(app.instanceId);
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
    wrote++;
    const idx = st.tasks.findIndex(t => t.id === id);
    if (idx !== -1) {
      const before = st.tasks[idx];
      const msg = leftViewMessage(before, updated);
      if (app.inScope(updated)) st.tasks[idx] = app.own(updated);
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

// Follow the task, or no longer: the user is told of its news from now on
// (a comment, closed, reopened, given on) without it being theirs to do.
// Saved at once, with an undo; the task as it is then, or null.
async function setFollow(id, follow) {
  const updated = await updateTask(id, { follow });
  if (updated) {
    showUndo(follow ? `You follow “${plainTitle(updated.title)}”: its news is under Updates` : `You no longer follow “${plainTitle(updated.title)}”`,
      () => updateTask(id, { follow: !follow }));
  }
  return updated;
}

// A task saved out of a list view, into another list or none; say where it went.
function listMovedNotice(task) {
  const ref = app.taskList(task);
  return ref ? `Saved to list “${app.listName(ref)}”.` : 'Saved; it no longer shows here.';
}

// Why a saved task left this view, or null if it still shows: it went
// into another list (or out of this one), or it no longer passes the
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
// history goes too, so the undo leaves no trace. Not the list of a task
// that was private: a save that shared it can't be taken back (a shared
// task stays shared).
function editSnapshot(t) {
  const rec = t.recurrence;
  return {
    title: t.title, description: t.description || '', tags: t.tags || [],
    ...(t.private ? {} : { list: app.taskList(t) || '' }),
    ...(people.users ? { assignee: t.assignee || null } : {}),
    due_date: t.due_date || null, start_date: t.start_date || null, keep: !!t.keep,
    rec_type: rec?.type || null, rec_interval: rec?.interval || 1, rec_from: rec?.from || 'due',
    // (Not its place in the manual order: no save of the dialog changes it,
    // and the number here is this list's own count since its last move, not
    // the server's.)
    last_done: t.last_done || null,
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

// ── Ticked tasks ──────────────────────────────────────────────────────────

// A task ticked off (its box, Space, Done on a selection) is done at once on
// the server, and its card stays where it was, ticked and struck through
// (st.ticked: id → the task as it was before), so the list doesn't move
// under the next tick and unticking takes it back. The cards go together:
// when the view shows something else (another tab, grouping, sort or
// filter: tickedHere, from render()), or TICKED_FOR after the last tick or
// untick, once nothing is in the user's hands (app.busy). The views draw
// shownTasks(): the tasks held, a ticked one as it was.
const TICKED_FOR = 10 * 60 * 1000;
let tickedTimer = null;
let tickedIn = '';
const tickSaves = new Map();   // id → the tick's write, while it is on its way

function shownTasks() {
  if (!st.ticked.size) return st.tasks;
  const held = new Set(st.tasks.map(t => t.id));
  return [...st.tasks.map(t => st.ticked.get(t.id) || t),
          ...[...st.ticked.values()].filter(t => !held.has(t.id))];
}
// The ticked tasks stay TICKED_FOR from now.
function holdTicked() {
  clearTimeout(tickedTimer);
  tickedTimer = st.ticked.size ? setTimeout(letTickedGo, TICKED_FOR) : null;
}
function letTickedGo() {
  if (app.busy()) { tickedTimer = setTimeout(letTickedGo, 5000); return; }
  app.listEl.querySelectorAll('.task-item.task-completing').forEach(li => li.classList.add('task-completing-out'));
  tickedTimer = setTimeout(() => { dropTicked(); app.render(); }, 400);
}
function dropTicked() {
  clearTimeout(tickedTimer);
  tickedTimer = null;
  st.ticked.clear();
}
function tickedHere() {
  const key = [st.activeView, st.today, st.cut, app.activeSort(), app.filterQuery()].join('\n');
  if (key === tickedIn) return;
  tickedIn = key;
  if (st.ticked.size) dropTicked();
}

// Several completed at once (completeMany): the closed tasks, as the server
// has them, are held beside the open ones, as one ticked alone is (its PUT
// answers it). They are the latest closed; one that isn't among them (others
// closed more meanwhile) doesn't stay.
async function holdClosed(ids) {
  ids = ids.filter(id => st.ticked.has(id) && !st.tasks.some(t => t.id === id));
  if (!ids.length) return;
  const q = new URLSearchParams(app.scopeQuery().slice(1));
  q.set('limit', ids.length + 50);
  let closed = [];
  try {
    const res = await fetch('/api/tasks/closed?' + q);
    if (res.ok) closed = (await res.json()).tasks || [];
  } catch (_) {}
  const byId = new Map(closed.map(t => [t.id, t]));
  for (const id of ids) {
    if (!st.ticked.has(id) || st.tasks.some(t => t.id === id)) continue;
    if (byId.has(id)) st.tasks.push(app.own(byId.get(id)));
    else st.ticked.delete(id);
  }
  app.render();
}

// The task's box ticked (a click, Space), once the user has said so if
// subtasks are still open. False when they cancelled, or it wasn't saved.
// Updates holds closed tasks as they are, so there the card is the closed
// task's at once.
async function tickTask(id) {
  const task = st.tasks.find(t => t.id === id);
  if (!task || st.ticked.has(id)) return !!task;
  if (!(await app.confirmOpenSubtasks([task]))) return false;
  if (!app.initUpdates) {
    st.ticked.set(id, { ...task });
    holdTicked();
    const li = app.listEl.querySelector(`.task-item[data-id="${CSS.escape(id)}"]`);
    const cb = li?.querySelector('.task-checkbox input');
    if (cb) cb.checked = true;
    if (li) li.classList.add('task-completing');
  }
  const save = updateTask(id, { completed: true });
  tickSaves.set(id, save);
  const done = await save;
  tickSaves.delete(id);
  if (!done && st.ticked.delete(id)) { holdTicked(); app.render(); }
  return !!done;
}

// A ticked task unticked: open again as it was, with no trace in its
// history (a keep task gets its dates back; a repeating one's next
// occurrence goes, which the list is asked again for).
async function untickTask(id) {
  const before = st.ticked.get(id);
  if (!before) return;
  st.ticked.delete(id);
  holdTicked();
  await tickSaves.get(id);
  const fields = before.keep ? keepSnapshot(before) : { completed: false, history: before.history || [] };
  if (!await updateTask(id, fields)) { st.ticked.set(id, before); holdTicked(); app.render(); }
  else if (before.recurrence) await fetchTasks();
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
  return res.ok;
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

// One task's start date (null: none), with an undo; a day past its deadline
// asks first (planOnDay). Whether it was written. What sets a start date
// quickly calls startOn (js/taskselect.js), which comes here for one task.
async function setStart(id, startDate) {
  const task = st.tasks.find(t => t.id === id);
  if (startsAfterDeadline(task, startDate)) return !!await app.planOnDay(id, startDate);
  const prev = task?.start_date ?? null;
  const recurrence = task?.recurrence ?? null;
  const history = oldHistory(id);
  dismissUndo();
  if (!await updateTask(id, { start_date: startDate })) return false;
  const msg = startDate ? `Starts ${formatDate(startDate)}` : 'Start date cleared';
  showUndo(msg, () => updateTask(id, { start_date: prev, recurrence, history }));
  return true;
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

return { dismissUndo, showUndo, triggerUndo, fetchTasks, refresh, fetchClosed, setClosedYear, createTask, updateTask, setFollow, markSeen, updatesNow, markAllSeen, closedStale, leftViewMessage, tickTask, untickTask, shownTasks, tickedHere, holdTicked, holdClosed, keepSnapshot, completeTasks, afterBatchWrite, oldHistory, markWontDo, wontDoMany, skipOccurrence, setStart };
}
