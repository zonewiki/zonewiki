// The order of a task app's list (tasks.js initTasksInstance): each tab's
// sort, the manual order (one list on Open and Today; a day's share of it
// on the Plan board), and a task moved in it by a drop or a key. What it
// uses of the app comes in app (its state in app.st).

import TasksCore from '../tasks-core.js';

const { placeInOrder, byOpenOrder } = TasksCore;

export function initTaskOrder(app) {
const { st } = app;

// ── Sorting ────────────────────────────────────────────────────────────────

// Per-tab sort comparators. Open and Today sort with TasksCore.byOpenOrder
// in render(), and Plan with TasksCore.boardBuckets.
const TAB_SORT = {
  closed: (a, b) => (b.closed_at || '').localeCompare(a.closed_at || ''),
};

// The tabs that show the manual order as one list, where it is edited: Open
// and Today (Open filtered). A new task is at its top; a move places the
// task among what is shown, and every other task keeps its place.
const ORDERED = { open: true, focus: true };

// Every open task in the manual order, the ones the view or filter hides
// too (not Updates' extras, which are in no order here): byOpenOrder, a task
// not yet placed (no sort_order) on top, newest first.
function manualOrderIds() {
  return [...st.tasks].filter(t => !t.closed_at && !t.extra).reverse().sort(byOpenOrder).map(t => t.id);
}
// The tasks the list shows, top to bottom (not Updates, which ranks nothing).
function shownIds() {
  return app.visibleItems().filter(el => !el.classList.contains('task-update')).map(el => el.dataset.id);
}
// Save the shown tasks `shown`, rearranged so that the tasks `moved` changed
// place (TasksCore.placeInOrder: every other task keeps its place, hidden
// ones too). The server is told the moves, not the order: each moved task
// with the task now before it and the next one that didn't move
// (TasksCore.orderMoves), so it places them in the order it has by then, a
// task not yet placed where it is shown, and a list that is stale here
// (someone ranked meanwhile) doesn't put its old order back.
function placeShown(shown, moved) {
  const old = manualOrderIds();
  const order = placeInOrder(old, shown, moved);
  if (order.join() === old.join()) return;
  const byId = new Map(st.tasks.map(t => [t.id, t]));
  order.forEach((id, i) => { byId.get(id).sort_order = i; });
  app.postReorder({ order, moves: TasksCore.orderMoves(order, moved) });
  app.render();
}

// Plan: every section (`data-ordered`) shows its share of the manual order,
// and moves in it place a task among the section's tasks as on Open.
function planSectionIds(div) {
  const ids = [];
  for (let el = div.nextElementSibling; el && !el.classList.contains('board-day-divider'); el = el.nextElementSibling) {
    if (el.classList.contains('task-item')) ids.push(el.dataset.id);
  }
  return ids;
}
// Drop `draggedId` on the task `key`, before or after it (`side`, by the
// pointer: dropSide).
function orderDrop(key, side) {
  const list = shownIds();
  if (!list.includes(st.draggedId) || !list.includes(key) || key === st.draggedId) return;
  list.splice(list.indexOf(st.draggedId), 1);
  list.splice(list.indexOf(key) + (side === 'after' ? 1 : 0), 0, st.draggedId);
  placeShown(list, [st.draggedId]);
}
// Ctrl+Shift+↑/↓: the selection moves one place among `list` (what the list
// shows, or a Plan section's tasks).
function orderKeyMove(direction, list = shownIds()) {
  const idxs = list.map((id, i) => st.selectedIds.has(id) ? i : -1).filter(i => i >= 0);
  if (!idxs.length) return;
  const group = idxs.map(i => list[i]);
  const rest = list.filter(id => !st.selectedIds.has(id));
  if (direction === 'up') {
    const above = list[idxs[0] - 1];
    if (above === undefined) return;
    rest.splice(rest.indexOf(above), 0, ...group);
  } else {
    const below = list[idxs[idxs.length - 1] + 1];
    if (below === undefined) return;
    rest.splice(rest.indexOf(below) + 1, 0, ...group);
  }
  placeShown(rest, group);
}

function sortTasks(tasksArr) {
  const fixed = TAB_SORT[st.activeView];
  return fixed ? [...tasksArr].sort(fixed) : [...tasksArr];
}

return { ORDERED, placeShown, planSectionIds, orderDrop, orderKeyMove, sortTasks };
}
