// A task app's drag and drop with the mouse (tasks.js initTasksInstance):
// the Plan board's days and sections, the manual order of Open and Focus.
// Each render wires the cards (setupBoardDragAndDrop, setupDragAndDrop) and
// sets st.touchDrop, through which js/tasktouch.js drops a card carried by
// a finger. What it uses of the app comes in app (its state in app.st).

export function initTaskDnd(app) {
const { st } = app;

// Plan board: make cards draggable; the list-level dragover/drop handlers
// (attached once at init) re-plan the task to the section it's dropped on.
// A drop on a card in a section (`data-ordered`) lands above or below it,
// as on Open: on another day it also starts the task there. A drop on a
// section's Sorted label puts it first among the sorted ones, as Open's
// Sorted heading does. Elsewhere a drop on a card is a drop on its section.
const orderedCard = item => !!app.boardDivider(item)?.dataset.ordered;
function setupBoardDragAndDrop() {
  async function executeBoardItemDrop(target, side) {
    const label = !target.classList.contains('task-item');   // a Sorted label
    const targetId = label ? null : target.dataset.id;
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
    if (!targetDiv.dataset.ordered || (!same && !targetDiv.dataset.dropDay)) {
      if (!same) app.dropOnBucket(id, targetDiv);
      return;
    }
    const list = app.planSectionList(targetDiv).filter(x => x !== id);
    list.splice(label ? list.indexOf(app.sectionKey('sorted')) + 1
      : list.indexOf(targetId) + (side === 'after' ? 1 : 0), 0, id);
    if (!same && !await app.planOnDay(id, targetDiv.dataset.dropDay)) return;
    app.applySectionList(list, [id], app.OPEN_ORDER);
  }
  const SORTED_LABEL = '.plan-subdivider[data-sub="sorted"]';

  st.touchDrop = {
    sided: t => t.classList.contains('task-item') && orderedCard(t),
    target: el => {
      const item = el.closest('.task-item');
      if (item) return item.dataset.id === st.draggedId ? null : item;
      const label = el.closest(SORTED_LABEL);
      if (label) return label;
      const div = app.boardDivider(el);
      return div && div.dataset.dropBucket ? div : null;
    },
    drop: (target, side) => target.classList.contains('task-item') || target.matches(SORTED_LABEL)
      ? executeBoardItemDrop(target, side) : app.dropOnBucket(st.draggedId, target),
  };

  app.listEl.querySelectorAll(SORTED_LABEL).forEach(label => {
    label.addEventListener('dragover', e => {
      if (!st.draggedId) return;
      e.preventDefault();
      e.stopPropagation();  // not the section's highlight: this is a place in it
      app.clearBoardDropOver();
      label.classList.add('task-drag-over');
    });
    label.addEventListener('dragleave', () => label.classList.remove('task-drag-over'));
    label.addEventListener('drop', e => {
      e.preventDefault();
      e.stopPropagation();
      label.classList.remove('task-drag-over');
      executeBoardItemDrop(label);
    });
  });

  app.listEl.querySelectorAll('.task-item[data-drag]').forEach(item => {
    item.addEventListener('dragstart', e => {
      // Touch has its own long-press drag (see touchDrop); selecting, nothing drags.
      if (st.lastPointerType === 'touch' || st.selectMode) { e.preventDefault(); return; }
      st.draggedId = item.dataset.id;
      item.classList.add('task-dragging');
      e.dataTransfer.effectAllowed = 'move';
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

const DIVIDER_TARGETS = '.list-section';
function setupDragAndDrop() {
  function executeDrop(targetId, side) {
    if (!st.draggedId || targetId === st.draggedId) return;
    app.sectionDrop(targetId, side);
  }
  const dropOnDivider = div => executeDrop(app.sectionKey(div.dataset.section));
  st.touchDrop = {
    sided: true,
    target: el => {
      const item = el.closest('.task-item[data-drag]');
      if (item) return item.dataset.id === st.draggedId ? null : item;
      return el.closest(DIVIDER_TARGETS);
    },
    drop: (target, side) => target.classList.contains('task-item')
      ? executeDrop(target.dataset.id, side) : dropOnDivider(target),
  };

  const items = app.listEl.querySelectorAll('.task-item[data-drag]');
  const clearDropOver = () => app.listEl.querySelectorAll(app.DROP_CLASSES.map(c => '.' + c).join(', '))
    .forEach(el => el.classList.remove(...app.DROP_CLASSES));
  items.forEach(item => {
    item.addEventListener('dragstart', e => {
      if (st.lastPointerType === 'touch' || st.selectMode) { e.preventDefault(); return; }
      st.draggedId = item.dataset.id;
      item.classList.add('task-dragging');
      e.dataTransfer.effectAllowed = 'move';
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

  // Section dividers are also drop targets (dropOnDivider).
  app.listEl.querySelectorAll(DIVIDER_TARGETS).forEach(div => {
    const overCls = div.classList.contains('board-day-divider') ? 'board-drop-over' : 'task-drag-over';
    div.addEventListener('dragover', e => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      div.classList.add(overCls);
    });
    div.addEventListener('dragleave', () => div.classList.remove(overCls));
    div.addEventListener('drop', e => {
      e.preventDefault();
      div.classList.remove(overCls);
      dropOnDivider(div);
    });
  });
}

// ── Keyboard navigation ────────────────────────────────────────────────────

return { setupBoardDragAndDrop, setupDragAndDrop };
}
