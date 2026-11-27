// A task app's drag and drop with the mouse (tasks.js initTasksInstance):
// the Plan board's days and sections, the manual order of Open and Focus.
// Each render wires the cards (setupBoardDragAndDrop, setupDragAndDrop) and
// sets st.touchDrop, through which js/tasktouch.js drops a card carried by
// a finger. What it uses of the app comes in app (its state in app.st).
import { dragChip } from './taskutil.js';

export function initTaskDnd(app) {
const { st } = app;

// The browser's drag image is the chip below and right of the pointer
// (transparent padding puts it there), not the card under it: the browser
// takes a picture of it, off screen, as the drag starts, so it may go then.
function carryChip(e, item) {
  const image = document.createElement('div');
  image.className = 'task-drag-image';
  image.appendChild(dragChip(item));
  document.body.appendChild(image);
  e.dataTransfer.setDragImage(image, 0, 0);
  setTimeout(() => image.remove());
}

// Plan board: make cards draggable; the list-level dragover/drop handlers
// (attached once at init) re-plan the task to the section it's dropped on.
// A drop on a card in a section (`data-ordered`) lands above or below it,
// as on Open: on another day it also starts the task there (another
// person's section assigns it, another list's moves it: dropWrite).
// Elsewhere a drop on a card is a drop on its section.
const orderedCard = item => !!app.boardDivider(item)?.dataset.ordered;
function setupBoardDragAndDrop() {
  async function executeBoardItemDrop(target, side) {
    const targetId = target.dataset.id;
    if (!st.draggedId || targetId === st.draggedId) return;
    const id = st.draggedId;
    const draggedEl = app.listEl.querySelector(`.task-item[data-id="${CSS.escape(id)}"]`);
    const draggedDiv = draggedEl && app.boardDivider(draggedEl);
    const targetDiv = app.boardDivider(target);
    app.clearBoardDropOver();
    if (!targetDiv?.dataset.dropBucket) return;
    const same = draggedDiv === targetDiv;
    // Later is a range: a task from elsewhere asks for its date first, as
    // on the heading, and lands where Open's order puts it.
    const writes = targetDiv.dataset.dropDay || targetDiv.dataset.dropPerson !== undefined || targetDiv.dataset.dropList !== undefined;
    if (!targetDiv.dataset.ordered || (!same && !writes)) {
      if (!same) app.dropOnBucket(id, targetDiv);
      return;
    }
    const list = app.planSectionIds(targetDiv).filter(x => x !== id);
    list.splice(list.indexOf(targetId) + (side === 'after' ? 1 : 0), 0, id);
    if (!same && !await app.dropWrite(id, targetDiv)) return;
    app.placeShown(list, [id]);
  }

  st.touchDrop = {
    sided: t => t.classList.contains('task-item') && orderedCard(t),
    target: el => {
      const item = el.closest('.task-item');
      if (item) return item.dataset.id === st.draggedId ? null : item;
      const div = app.boardDivider(el);
      return div && div.dataset.dropBucket ? div : null;
    },
    drop: (target, side) => target.classList.contains('task-item')
      ? executeBoardItemDrop(target, side) : app.dropOnBucket(st.draggedId, target),
  };

  app.listEl.querySelectorAll('.task-item[data-drag]').forEach(item => {
    item.addEventListener('dragstart', e => {
      // Touch has its own long-press drag (see touchDrop); selecting, nothing drags.
      if (st.lastPointerType === 'touch' || st.selectMode) { e.preventDefault(); return; }
      st.pressPending = null;   // dragged: the press doesn't expand it
      st.draggedId = item.dataset.id;
      item.classList.add('task-dragging');
      e.dataTransfer.effectAllowed = 'move';
      carryChip(e, item);
    });
    item.addEventListener('dragend', () => {
      item.classList.remove('task-dragging');
      st.draggedId = null;
      app.clearBoardDropOver();
      app.listEl.querySelectorAll('.task-drag-over, .task-drop-before, .task-drop-after')
        .forEach(el => el.classList.remove('task-drag-over', 'task-drop-before', 'task-drop-after'));
    });
    item.addEventListener('dragover', e => {
      if (!st.draggedId) return;
      e.preventDefault();
      e.stopPropagation();  // suppress list-level bucket-highlight when over a task item
      e.dataTransfer.dropEffect = 'move';
      if (item.dataset.id === st.draggedId) return;
      if (!orderedCard(item)) { item.classList.add('task-drag-over'); return; }
      app.clearBoardDropOver();
      const side = app.dropSide(item, e.clientY);
      item.classList.toggle('task-drop-before', side === 'before');
      item.classList.toggle('task-drop-after', side === 'after');
    });
    item.addEventListener('dragleave', () => item.classList.remove('task-drag-over', 'task-drop-before', 'task-drop-after'));
    item.addEventListener('drop', e => {
      e.preventDefault();
      e.stopPropagation();  // prevent list-level handler from also firing
      item.classList.remove('task-drag-over', 'task-drop-before', 'task-drop-after');
      executeBoardItemDrop(item, app.dropSide(item, e.clientY));
    });
  });
}

// Open and Today: a card dropped on a card lands above or below it.
function setupDragAndDrop() {
  function executeDrop(targetId, side) {
    if (!st.draggedId || targetId === st.draggedId) return;
    app.orderDrop(targetId, side);
  }
  st.touchDrop = {
    sided: true,
    target: el => {
      const item = el.closest('.task-item[data-drag]');
      return item && item.dataset.id !== st.draggedId ? item : null;
    },
    drop: (target, side) => executeDrop(target.dataset.id, side),
  };

  const items = app.listEl.querySelectorAll('.task-item[data-drag]');
  const clearDropOver = () => app.listEl.querySelectorAll(app.DROP_CLASSES.map(c => '.' + c).join(', '))
    .forEach(el => el.classList.remove(...app.DROP_CLASSES));
  items.forEach(item => {
    item.addEventListener('dragstart', e => {
      if (st.lastPointerType === 'touch' || st.selectMode) { e.preventDefault(); return; }
      st.pressPending = null;   // dragged: the press doesn't expand it
      st.draggedId = item.dataset.id;
      item.classList.add('task-dragging');
      e.dataTransfer.effectAllowed = 'move';
      carryChip(e, item);
    });
    item.addEventListener('dragend', () => {
      item.classList.remove('task-dragging');
      st.draggedId = null;
      clearDropOver();
    });
    item.addEventListener('dragover', e => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      if (item.dataset.id === st.draggedId) return;
      const side = app.dropSide(item, e.clientY);
      item.classList.toggle('task-drop-before', side === 'before');
      item.classList.toggle('task-drop-after', side === 'after');
    });
    item.addEventListener('dragleave', () => {
      item.classList.remove('task-drop-before', 'task-drop-after');
    });
    item.addEventListener('drop', e => {
      e.preventDefault();
      item.classList.remove('task-drop-before', 'task-drop-after');
      executeDrop(item.dataset.id, app.dropSide(item, e.clientY));
    });
  });
}

return { setupBoardDragAndDrop, setupDragAndDrop };
}
