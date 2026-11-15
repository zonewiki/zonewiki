// The order of a task app's list (tasks.js initTasksInstance): each tab's
// sort, the manual order with its sections (Unsorted above Sorted, on Open
// and Focus; a day's on the Plan board), and a task moved in it by a drop or
// a key. What it uses of the app comes in app (its state in app.st).

import TasksCore from '../tasks-core.js';

const { placeInOrder } = TasksCore;

export function initTaskOrder(app) {
const { st } = app;

// ── Sorting ────────────────────────────────────────────────────────────────

// Per-tab sort comparators. Open and Focus sort with TasksCore.byOpenOrder
// in render(), and Plan with TasksCore.boardBuckets.
const TAB_SORT = {
  closed: (a, b) => (b.closed_at || '').localeCompare(a.closed_at || ''),
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
  return st.tasks.filter(t => !t.closed_at && t[s.field] != null)
    .sort((a, b) => a[s.field] - b[s.field]).map(t => t.id);
}
function sectionList() {
  const s = SECTIONS[st.activeView];
  const byId = new Map(st.tasks.map(t => [t.id, t]));
  const ids = app.visibleItems().map(el => el.dataset.id);
  const manual = ids.filter(id => byId.get(id)[s.field] != null);
  const fixed = ids.filter(id => byId.get(id)[s.field] == null);
  return [sectionKey(s.top), ...fixed, sectionKey(s.bottom), ...manual];
}
// Save a rearranged sectionList() in which the tasks `moved` changed place
// (TasksCore.placeInOrder: every other task keeps its place, hidden ones too).
function applySectionList(list, moved, s = SECTIONS[st.activeView]) {
  const cut = list.indexOf(sectionKey(s.bottom));
  saveOrder(s, placeInOrder(manualOrderIds(s), list.slice(cut + 1), moved), moved);
}
// Save the manual order `order`, in which the tasks `moved` changed place;
// tasks of the old order missing from it become Unsorted. The server is
// told the moves, not the order: each moved task with the task now before
// it and the next one that didn't move (TasksCore.orderMoves), so it places
// them in the order it has by then, and a list that is stale here (someone
// ranked meanwhile) doesn't put its old order back.
function saveOrder(s, order, moved = []) {
  const old = manualOrderIds(s);
  const kept = new Set(order);
  const removed = old.filter(id => !kept.has(id));
  if (!removed.length && order.join() === old.join()) return;
  const byId = new Map(st.tasks.map(t => [t.id, t]));
  order.forEach((id, i) => { byId.get(id)[s.field] = i; });
  removed.forEach(id => { byId.get(id)[s.field] = null; });
  app.postReorder({ order, moves: TasksCore.orderMoves(order, moved), updates: removed.map(id => ({ id, [s.field]: null })) });
  app.render();
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

// A section heading on Open and Focus: the light label of a Plan section's
// Unsorted / Sorted (.plan-subdivider), so the two tabs look the same and the
// labels never read as a day heading. It takes drops: an empty section is
// only this row.
function sectionHTML(zone, label, count) {
  const empty = count ? '' : ' board-day--empty';
  return `<li class="plan-subdivider list-section${empty}" data-section="${zone}">` +
    `<span>${label}</span><span class="board-day-count">${count}</span></li>`;
}

function sortTasks(tasksArr) {
  const fixed = TAB_SORT[st.activeView];
  return fixed ? [...tasksArr].sort(fixed) : [...tasksArr];
}

return { SECTIONS, sectionKey, applySectionList, OPEN_ORDER, planSectionList, sectionDrop, sectionKeyMove, sectionHTML, sortTasks };
}
