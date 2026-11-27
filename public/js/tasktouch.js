// A task app's touch gestures (tasks.js initTasksInstance): a long press
// picks a card up and the finger carries it to a drop (touchDrop, set by
// the drag and drop of the view), a sideways swipe starts or unplans a
// task, a long press let go selects; and the right-click menu. What it uses
// of the app comes in app (its state in app.st).
import TasksCore from '../tasks-core.js';
import { dragChip, isDueNow, todayStr } from './taskutil.js';

const { isFocus } = TasksCore;

export function initTaskTouch(app) {
const { st } = app;

// Touch drag. Phones do not fire HTML5 drag events reliably, so a long press picks a
// card up and the finger carries it; releasing drops it through the same code
// as a mouse drop. `touchDrop` is set by setupBoardDragAndDrop /
// setupDragAndDrop on each render (null where the view has no dragging):
//   target(el) → the element under the finger that would take the drop, or null
//   drop(target) → perform the drop (draggedId is set while it runs)
// Moving before the press completes is a scroll, and cancels the pick-up —
// unless the move is mostly sideways on an open task: that is a swipe.
// Swipes only move a task in and out of Focus (swipeActions): right starts
// it today (Open and Plan), left clears its start date (Focus and Plan).
// A direction with nothing to do doesn't move the card. Both come with an
// undo toast (startOn). Drags work only where touchDrop is set.
// A long press let go without moving (10px) starts selection mode with
// that card; where nothing drags, the press
// only holds the card for that (holdCard), and moving drops the hold.
const LONG_PRESS_MS = 350;
st.touchDrop = null;
st.touchDrag = null;  // { item, x, y, x0, y0, timer, active, held, moved, swiping, swipe, ghost, target, raf }
// What a swipe does on this task in the current view: { right, left },
// each 'start' (start today), 'unplan' (clear the start date) or null.
function swipeActions(task) {
  if (!task || task.closed_at || st.ticked.has(task.id)) return { right: null, left: null };
  const today = todayStr();
  // Already in Focus: nothing to start. Due now: clearing the start date
  // would leave it in Focus (the deadline keeps it there).
  const start = isFocus(task, today) ? null : 'start';
  const unplan = task.start_date && !isDueNow(task, today) ? 'unplan' : null;
  // On Plan a carried-over task (started earlier, still open) starts today:
  // committing to it again, which takes its "since" badge away.
  const carried = TasksCore.isCarried(task, today) ? 'start' : null;
  if (st.activeView === 'closed') return { right: null, left: null };
  if (st.today) return { right: null, left: unplan };   // Today, however it is drawn
  if (st.activeView === 'open') return { right: start, left: null };
  if (st.activeView === 'plan') return { right: start || carried, left: unplan };
  return { right: null, left: null };
}
const SWIPE_LABELS = { start: '📅 Start today', unplan: '↩ Unplan' };
function setSwipe(dx) {
  const item = st.touchDrag.item;
  const action = dx > 0 ? st.touchDrag.swipe.right : st.touchDrag.swipe.left;
  if (!action) dx = 0;  // nothing to do this way: the card stays put
  const threshold = Math.min(110, item.offsetWidth * 0.3);
  item.style.transform = `translateX(${dx}px)`;
  if (action) {
    item.dataset.swipe = action;
    item.dataset.swipeSide = dx > 0 ? 'right' : 'left';
    item.dataset.swipeLabel = SWIPE_LABELS[action];
  } else {
    delete item.dataset.swipe;
  }
  item.classList.toggle('task-swipe-armed', !!action && Math.abs(dx) >= threshold);
}
// A drop on a card in a sorted list (Open, Focus) lands where the pointer
// is: above the card on its upper half, below it on the lower half. A line
// shows it while dragging (.task-drop-before / -after).
function dropSide(item, y) {
  const r = item.getBoundingClientRect();
  return y < r.top + r.height / 2 ? 'before' : 'after';
}
const DROP_CLASSES = ['task-drag-over', 'board-drop-over', 'task-drop-before', 'task-drop-after'];

// In the gap between two cards (the list's own) the target stays, so the
// line doesn't flicker as the finger crosses it.
function touchDropTarget(x, y) {
  const el = document.elementFromPoint(x, y);
  if (el === app.listEl) return st.touchDrag.target;
  return el && app.listEl.contains(el) && st.touchDrop ? st.touchDrop.target(el) : null;
}
function setTouchTarget(target) {
  // In a sorted list the side of the card under the finger matters too.
  const sided = target && (typeof st.touchDrop?.sided === 'function' ? st.touchDrop.sided(target) : st.touchDrop?.sided);
  const side = sided && target.classList.contains('task-item')
    ? dropSide(target, st.touchDrag.y) : null;
  if (target === st.touchDrag.target && side === st.touchDrag.side) return;
  st.touchDrag.target?.classList.remove(...DROP_CLASSES);
  st.touchDrag.target = target;
  st.touchDrag.side = side;
  if (!target) return;
  target.classList.add(side ? `task-drop-${side}`
    : target.classList.contains('board-day-divider') ? 'board-drop-over' : 'task-drag-over');
}
// A long press where nothing drags: select the card, so the press
// visibly took; letting go starts selection mode (endTouchDrag).
function holdCard() {
  const t = st.touchDrag;
  t.held = true;
  st.selectedId = st.anchorId = t.item.dataset.id;
  st.selectedIds = new Set([st.selectedId]);
  app.updateSelectionClasses();
  navigator.vibrate?.(15);
}
// The chip the finger carries sits above it, where the finger doesn't
// cover it (below it near the top of the screen), so the line where the
// card lands, at the finger, stays in sight.
function placeChip(t) {
  const r = t.ghost.getBoundingClientRect(), gap = 28, margin = 8;
  const above = t.y - gap - r.height;
  t.ghost.style.left = Math.max(margin, Math.min(t.x - 24, window.innerWidth - r.width - margin)) + 'px';
  t.ghost.style.top = (above >= margin ? above : t.y + gap) + 'px';
}
function startTouchDrag() {
  const t = st.touchDrag;
  t.active = true;
  st.draggedId = t.item.dataset.id;
  // Picking a card up selects it, as a mouse press does, so the long press
  // visibly took. Not setSelected(): its scrollIntoView would move the page
  // under the finger.
  st.selectedId = st.anchorId = st.draggedId;
  st.selectedIds = new Set([st.draggedId]);
  app.updateSelectionClasses();
  t.item.classList.add('task-dragging');
  t.ghost = dragChip(t.item);
  document.body.appendChild(t.ghost);
  placeChip(t);
  navigator.vibrate?.(15);
  // Near the top or bottom edge the page scrolls, so a drop target that is
  // off screen can still be reached.
  const edge = 70;
  const step = () => {
    if (!st.touchDrag?.active) return;
    const dy = st.touchDrag.y < edge ? -12 : st.touchDrag.y > window.innerHeight - edge ? 12 : 0;
    if (dy) {
      window.scrollBy(0, dy);
      setTouchTarget(touchDropTarget(st.touchDrag.x, st.touchDrag.y));
    }
    st.touchDrag.raf = requestAnimationFrame(step);
  };
  t.raf = requestAnimationFrame(step);
}
function endTouchDrag(drop) {
  const t = st.touchDrag;
  st.touchDrag = null;
  if (!t) return;
  clearTimeout(t.timer);
  if (t.swiping) {
    const armed = t.item.classList.contains('task-swipe-armed');
    const action = t.item.dataset.swipe;
    t.item.style.transform = '';
    t.item.classList.remove('task-swipe-armed');
    delete t.item.dataset.swipe;
    if (drop && armed && action === 'start') app.startOn([t.item.dataset.id], todayStr());
    if (drop && armed && action === 'unplan') app.startOn([t.item.dataset.id], null);
    return;
  }
  // Held and let go in place: no drop, selection mode.
  const select = drop && (t.held || (t.active && !t.moved));
  if (t.active) {
    cancelAnimationFrame(t.raf);
    t.ghost.remove();
    t.item.classList.remove('task-dragging');
    const target = t.target;
    target?.classList.remove(...DROP_CLASSES);
    if (drop && !select && target && st.touchDrop) st.touchDrop.drop(target, t.side);
    st.draggedId = null;
  }
  if (select) app.enterSelectMode(t.item.dataset.id);
}
app.listEl.addEventListener('touchstart', e => {
  endTouchDrag(false);
  if (e.touches.length !== 1 || st.selectMode) return;   // selecting: no drags, no swipes
  const item = e.target.closest('.task-item');
  // A date chip may start a swipe or drag (it sits on the first line, where
  // a thumb starts one); a mere tap on it still opens its date picker.
  // So may the card's list, a link in that line; a title's link is left alone.
  if (!item || e.target.closest('input, button, a:not(.task-tag-list)')) return;
  const canDrag = !!st.touchDrop && item.matches('[data-drag]');
  const swipe = swipeActions(st.tasks.find(t => t.id === item.dataset.id));
  const canSwipe = !!(swipe.right || swipe.left);
  const p = e.touches[0];
  st.touchDrag = { item, x: p.clientX, y: p.clientY, x0: p.clientX, y0: p.clientY,
                active: false, held: false, moved: false, swiping: false, canSwipe, swipe, target: null,
                timer: setTimeout(canDrag ? startTouchDrag : holdCard, LONG_PRESS_MS) };
}, { passive: true });
app.listEl.addEventListener('touchmove', e => {
  if (!st.touchDrag) return;
  const p = e.touches[0];
  const dx = p.clientX - st.touchDrag.x, dy = p.clientY - st.touchDrag.y;
  if (st.touchDrag.swiping) {
    e.preventDefault();
    setSwipe(dx);
    return;
  }
  if (st.touchDrag.held) {
    if (Math.hypot(dx, dy) > 10) endTouchDrag(false);  // moved after all: no selection mode
    return;
  }
  if (!st.touchDrag.active) {
    const sideways = Math.abs(dx) > 1.5 * Math.abs(dy);
    if (st.touchDrag.canSwipe && sideways && Math.abs(dx) > 10) {
      clearTimeout(st.touchDrag.timer);
      st.touchDrag.swiping = true;
      e.preventDefault();
      setSwipe(dx);
    } else if (Math.hypot(dx, dy) > 10) {
      // A vertical move (the page scrolls), or a sideways one on a card
      // with nothing to swipe: either way no long press.
      endTouchDrag(false);
    }
    return;
  }
  e.preventDefault();  // the finger is carrying a card, not scrolling the page
  if (Math.hypot(p.clientX - st.touchDrag.x0, p.clientY - st.touchDrag.y0) > 10) st.touchDrag.moved = true;
  st.touchDrag.x = p.clientX;
  st.touchDrag.y = p.clientY;
  placeChip(st.touchDrag);
  setTouchTarget(touchDropTarget(p.clientX, p.clientY));
}, { passive: false });
app.listEl.addEventListener('touchend', e => {
  // A finished drag or swipe must not also count as a tap (which opens the editor).
  if (st.touchDrag?.active || st.touchDrag?.held || st.touchDrag?.swiping) e.preventDefault();
  endTouchDrag(true);
});
app.listEl.addEventListener('touchcancel', () => endTouchDrag(false));
// The long press would otherwise open the browser's context menu.
// Right-click on a card (mouse): the ⋯ menu, at the pointer. A link in the
// card keeps the browser's own menu (open in a new tab …).
app.listEl.addEventListener('contextmenu', e => {
  if (st.touchDrag) { e.preventDefault(); return; }
  const card = e.target.closest('.task-item');
  if (!card || st.lastPointerType === 'touch' || e.target.closest('a, input, textarea')) return;
  e.preventDefault();
  if (!app.inSelection(card.dataset.id)) app.setSelected(card.dataset.id);
  app.openTaskMenu(card.dataset.id, card, { x: e.clientX, y: e.clientY });
});

return { dropSide, DROP_CLASSES };
}
