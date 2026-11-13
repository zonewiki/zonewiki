// A task app's selection (tasks.js initTasksInstance): one card or several
// (Shift and Ctrl, selection mode on touch), the selection bar and what it
// does to every selected task at once (start, complete, delete with one
// undo), and the Tags dialog for several tasks. What it uses of the app
// comes in app (its state in app.st).
import TasksCore from '../tasks-core.js';
import { putTask } from './util.js';
import { attachTagAc, esc, formatDate, noSpacesInTags, noticeFromResponse, showNotice, startsAfterDeadline, todayStr } from './taskutil.js';

const { tagCounts, selectionTags, bulkTagChange, rankTags } = TasksCore;

export function initTaskSelect(app) {
const { st } = app;

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
      const isList = app.listSet.has(r.tag.toLowerCase());
      const tip = isList ? ` title="${esc(app.listTooltip(r.tag))}"` : '';
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
    : app.chipField(input, { label: t => '#' + t, listWord: t => app.listSet.has(t.toLowerCase()) });
  // A comma or space makes chips, as in the edit modal's Tags.
  if (chips) app.chipsOnSeparators(input, chips);
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
    const typed = app.cleanTag(input.value);
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
  app.dismissUndo();
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
    const msg = app.leftViewMessage(st.tasks[idx], u);
    if (app.inScope(u)) st.tasks[idx] = u;
    else st.tasks.splice(idx, 1);
    if (msg) gone.push({ msg, list: !app.inScope(u) && app.taskList(u) });
  }
  app.render();
  app.refreshOtherInstances(app.instanceId);
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
  app.showUndo(msg, () => batchUpdate(changed.map(u => ({ id: u.id, tags: before.get(u.id).tags || [] }))));
}

// ── Selection ──────────────────────────────────────────────────────────────
// One selected task (selectedId) or several (selectedIds): a click selects
// one, Shift+click or Shift+↑/↓ a range from the anchor, Ctrl/Cmd+click
// adds or drops one, Ctrl+A takes every task shown. While several are
// selected the selection bar (syncSelBar) and the ⋯ menu of a selected card
// act on all of them (runSelectionAction).

function updateSelectionClasses() {
  app.listEl.querySelectorAll('.task-item').forEach(el => {
    el.classList.toggle('task-selected', st.selectedIds.has(el.dataset.id));
  });
  // Several selected, none, or another one: the expanded task closes.
  if (st.expandedId && (st.selectedIds.size !== 1 || st.expandedId !== st.selectedId)) app.setExpanded(null);
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
  app.appEl.classList.add('tasks-selecting');
  st.selectedId = st.anchorId = id;
  st.selectedIds = new Set([id]);
  updateSelectionClasses();
  try { history.pushState({ ...history.state, tasksSelect: app.instanceId }, ''); } catch (_) {}
}
function exitSelectMode() {
  if (!st.selectMode) { clearSelection(); return; }
  st.selectMode = false;
  app.appEl.classList.remove('tasks-selecting');
  clearSelection();
  if (history.state?.tasksSelect === app.instanceId) history.back();
}
window.addEventListener('popstate', () => { if (st.selectMode) exitSelectMode(); });

// The selection bar: while several tasks are selected (on a page with
// several task lists, only the active one's), how many, and what to do to all.
// Today goes when every selected task is completed, and Done says Reopen.
function syncSelBar() {
  if (!app.selBar) return;
  const show = (st.selectMode || st.selectedIds.size > 1) &&
    (app.tasksInstances.length < 2 || app.activeTasksInstance === app.instanceId);
  app.selBar.hidden = !show;
  if (!show) return;
  const allDone = st.tasks.filter(t => st.selectedIds.has(t.id)).every(t => t.completed_at);
  app.selBar.querySelector('.tasks-selbar-count').textContent =
    st.selectedIds.size ? `${st.selectedIds.size} selected` : 'Tap tasks to select';
  app.selBar.querySelectorAll('.tasks-selbar-actions .btn').forEach(b => { b.disabled = !st.selectedIds.size; });
  app.selBar.querySelector('[data-sel="today"]').hidden = allDone;
  const done = app.selBar.querySelector('[data-sel="done"]');
  done.textContent = allDone ? 'Reopen' : 'Done';
  done.title = allDone ? 'Reopen the selected tasks (Space)' : 'Complete the selected tasks (Space)';
  app.selBar.querySelector('[data-sel="all"]').disabled =
    visibleItems().every(el => st.selectedIds.has(el.dataset.id));
}
app.selBar?.addEventListener('click', e => {
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
    case 'done':
      if (ids.length) mayComplete(ids).then(ok => { if (ok) { exitSelectMode(); completeMany(ids); } });
      break;
    case 'delete': if (ids.length) app.deleteWithUndo(ids, exitSelectMode); break;
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
  await app.afterBatchWrite();
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
  if (before.length === 1) return app.setStart(before[0].id, day);
  const asks = before.filter(t => startsAfterDeadline(t, day));
  const plain = before.filter(t => !asks.includes(t));
  if (plain.length) {
    app.dismissUndo();
    if (!await batchUpdate(plain.map(t => ({ id: t.id, start_date: day })))) return;
    const n = plain.length;
    const msg = n === 1 ? (day ? `Starts ${formatDate(day)}` : 'Start date cleared')
      : day ? `${n} tasks start ${/^(Today|Tomorrow)$/.test(formatDate(day)) ? formatDate(day).toLowerCase() : formatDate(day)}`
      : `${n} tasks unplanned`;
    app.showUndo(msg, () =>
      batchUpdate(plain.map(t => ({ id: t.id, start_date: t.start_date ?? null,
                                    recurrence: t.recurrence ?? null, history: t.history || [] }))));
  }
  for (const t of asks) await app.planOnDay(t.id, day);
}

// Before completeMany, and before the selection moves on: some of `ids`
// have subtasks still open, so it asks (confirmOpenSubtasks; reopening
// asks nothing). False when the user cancelled.
function mayComplete(ids) {
  return app.confirmOpenSubtasks(ids.map(id => st.tasks.find(t => t.id === id)));
}

// Complete the tasks `ids`, or reopen them if every one is completed, with
// an undo. Keep tasks stay open when completed; undo restores their fields
// instead of reopening them from Completed.
async function completeMany(ids) {
  const completing = ids.some(id => { const t = st.tasks.find(tk => tk.id === id); return t && !t.completed_at; });
  const kept = completing
    ? ids.map(id => st.tasks.find(t => t.id === id)).filter(t => t?.keep && !t.completed_at)
         .map(t => [t.id, app.keepSnapshot(t)])
    : [];
  app.dismissUndo();
  await app.completeTasks(ids, completing);
  const label = completing ? `${ids.length} tasks completed` : `${ids.length} tasks reopened`;
  app.showUndo(label, async () => {
    const keptIds = new Set(kept.map(([id]) => id));
    const others = ids.filter(id => !keptIds.has(id));
    if (kept.length) await Promise.all(kept.map(([id, before]) => putTask(id, before)));
    if (others.length) await app.completeTasks(others, !completing);
    else await app.afterBatchWrite();
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
  if (st.compact && id && st.lastPointerType !== 'touch' && id !== st.expandedId) app.expandSelected(id);
  // A pressed card is in view; scrolling an expanded one into view would
  // move it from under the pointer.
  if (id && !st.pressedCard) {
    const el = app.listEl.querySelector(`.task-item[data-id="${CSS.escape(id)}"]`);
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
  const el = app.listEl.querySelector(`.task-item[data-id="${CSS.escape(id)}"]`);
  if (el) el.scrollIntoView({ block: 'nearest' });
}

function visibleItems() {
  return Array.from(app.listEl.querySelectorAll('.task-item'));
}

return { openBulkTagModal, updateSelectionClasses, inSelection, toggleSelection, selectAllVisible, clearSelection, enterSelectMode, exitSelectMode, syncSelBar, mayComplete, runSelectionAction, startOn, completeMany, setSelected, extendSelection, visibleItems };
}
