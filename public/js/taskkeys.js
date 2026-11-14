// A task app's keys (tasks.js initTasksInstance): moving the selection,
// acting on the selected tasks, switching tabs and views, Ctrl+Z for the
// Undo toast. Only the app that was last used answers (activeTasksInstance).
// What it uses of the app comes in app (its state in app.st).

import { dateOffset, people, showNotice, startsAfterDeadline } from './taskutil.js';
import { keyPrefix } from './util.js';

export function initTaskKeys(app) {
const { st } = app;

// ── Keyboard navigation ────────────────────────────────────────────────────
function reorderSelected(direction) {
  if (!st.selectedIds.size) return;
  if (app.activeSort()) showNotice(`Sorted ${app.SORT_TITLES[app.activeSort()]}: press o until Manual to reorder`);
  else if (app.SECTIONS[st.activeView]) app.sectionKeyMove(direction);
  else if (st.activeView === 'plan') planKeyMove(direction);
}
// Plan: Ctrl+Shift+↑/↓ moves the selection one place within its day or
// Later, never out of it (moving to another day is 1–7 and dragging).
function planKeyMove(direction) {
  const els = [...st.selectedIds].map(id => app.listEl.querySelector(`.task-item[data-id="${CSS.escape(id)}"]`));
  if (els.some(el => !el)) return;
  const div = app.boardDivider(els[0]);
  if (!div?.dataset.ordered || els.some(el => app.boardDivider(el) !== div)) return;
  app.sectionKeyMove(direction, app.planSectionList(div), app.OPEN_ORDER);
}

document.addEventListener('keydown', async e => {
  // Only the active instance handles keyboard events (multi-embed safety)
  if (app.tasksInstances.length > 1 && app.activeTasksInstance !== app.instanceId) return;
  if (app.headless) return;
  // The second key of g … / c … is the page's (the t of c t, g t)
  if (keyPrefix.key) return;
  // The ⋯ menu owns the keyboard while open (its buttons take Enter/Space)
  if (st.taskMenu) return;

  // Ctrl+Z undo — works everywhere except inside the edit modal
  if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey && st.pendingUndo) {
    if (app.modal.open || app.deadlineModal.open) return;
    const focus = document.activeElement;
    if (focus && focus.closest('textarea, [contenteditable]')) return;
    e.preventDefault();
    app.triggerUndo();
    return;
  }

  if (app.modal.open || document.querySelector('dialog[open].modal, dialog[open].task-settings-app.modal')) return;
  const focus = document.activeElement;

  // '/' focuses the filter field; '#' focuses it and starts a tag word
  if (e.key === '/' && !e.ctrlKey && !e.metaKey && !focus?.closest('input, textarea, select, [contenteditable]')) {
    e.preventDefault();
    app.filterSearch?.focus();
    return;
  }
  // ('@' with users: an assignee; AltGr, which types it on many keyboards,
  // reports Ctrl+Alt on Windows.)
  const sigil = e.key === '#' || e.key === '@' && people.users ? e.key : '';
  if (sigil && !(e.ctrlKey && !e.altKey) && !e.metaKey && !focus?.closest('input, textarea, select, [contenteditable]')) {
    e.preventDefault();
    if (app.filterSearch) {
      const v = app.filterSearch.value.trimEnd();
      app.filterSearch.value = v ? v + ' ' + sigil : sigil;
      app.filterSearch.focus();
      app.filterSearch.setSelectionRange(app.filterSearch.value.length, app.filterSearch.value.length);
      app.filterSearch.dispatchEvent(new Event('input'));
    }
    return;
  }

  if (focus && focus.closest('input, textarea, select, [contenteditable]')) return;

  const items = app.visibleItems();
  const currentIdx = st.selectedId ? items.findIndex(el => el.dataset.id === st.selectedId) : -1;

  // Ctrl/Cmd+A with a task selected: every task shown (with the filter, the
  // ones it leaves). Without a selection it stays the page's select all.
  if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === 'a' && st.selectedId) {
    e.preventDefault();
    app.selectAllVisible();
    return;
  }

  // The menu key or Shift+F10: the selected task's ⋯ menu (with a mouse
  // there is no ⋯ button; right-click opens it too)
  if ((e.key === 'ContextMenu' || (e.key === 'F10' && e.shiftKey)) && st.selectedId) {
    const el = app.listEl.querySelector(`.task-item[data-id="${CSS.escape(st.selectedId)}"]`);
    if (el) { e.preventDefault(); app.openTaskMenu(st.selectedId, el); }
    return;
  }

  // 'd' toggles the Open tab's Plan switch (by start date / one list)
  if (e.key === 'd' && !e.ctrlKey && !e.metaKey && !e.altKey && app.isOpenTab(st.activeView)) {
    e.preventDefault();
    app.setGroup(st.activeView === 'open' ? 'days' : 'list');
    return;
  }

  // 'o' cycles the Open tab's order: manual, by deadline, newest first
  if (e.key === 'o' && !e.ctrlKey && !e.metaKey && !e.altKey && (st.activeView === 'open' || st.activeView === 'plan')) {
    e.preventDefault();
    app.cycleSort();
    return;
  }

  // 'n' opens new task modal
  if (e.key === 'n' && !e.ctrlKey && !e.metaKey && !e.altKey) {
    e.preventDefault();
    app.openEditModal(null);
    return;
  }

  // 'i' — the importance of the selected tasks, in turn: high, low, normal
  // (several: all get what follows the first one's)
  if (e.key === 'i' && !e.ctrlKey && !e.metaKey && !e.altKey && st.selectedId) {
    e.preventDefault();
    const ids = st.selectedIds.size ? [...st.selectedIds] : [st.selectedId];
    const first = st.tasks.find(t => t.id === st.selectedId);
    if (first) app.setImportance(ids, { '': 'high', high: 'low', low: 'normal' }[first.importance || '']);
    return;
  }

  // 'f' / 'F' — start selected task today / tomorrow
  if ((e.key === 'f' || e.key === 'F') && !e.ctrlKey && !e.metaKey && !e.altKey && st.selectedId) {
    e.preventDefault();
    const task = st.tasks.find(t => t.id === st.selectedId);
    if (task && !task.closed_at) {
      const target = e.key === 'f' ? dateOffset(0) : dateOffset(1);
      if (task.start_date !== target && startsAfterDeadline(task, target)) app.planOnDay(task.id, target);
      else app.updateTask(task.id, { start_date: task.start_date === target ? null : target });
    }
    return;
  }

  // 1–7 start the selected tasks on that day of the Plan board (1 today,
  // 2 tomorrow … 7 six days ahead), 0 unplans them; on every tab
  if (/^[0-7]$/.test(e.key) && !e.ctrlKey && !e.metaKey && !e.altKey && st.selectedIds.size) {
    e.preventDefault();
    const n = Number(e.key);
    app.startOn([...st.selectedIds], n ? dateOffset(n - 1) : null);
    return;
  }

  // Tab/Shift+Tab cycles the views
  if (e.key === 'Tab') {
    e.preventDefault();
    // Only the tabs: Closed (reached by its link) is not in the cycle,
    // and from there Tab goes to the first tab, Shift+Tab to the last.
    const views = app.tabButtons().map(b => b.dataset.view);
    const i = views.indexOf(st.activeView === 'plan' ? 'open' : st.activeView);
    const n = views.length;
    app.switchTab(app.tabView(views[i < 0 ? (e.shiftKey ? n - 1 : 0) : e.shiftKey ? (i - 1 + n) % n : (i + 1) % n]));
    return;
  }

  if (!items.length) return;

  // Ctrl+Shift+j/k / Ctrl+Shift+↑↓ — reorder selected (manual sort mode)
  if (e.ctrlKey && e.shiftKey && (e.key === 'ArrowDown' || e.key === 'J')) {
    e.preventDefault();
    reorderSelected('down');
  } else if (e.ctrlKey && e.shiftKey && (e.key === 'ArrowUp' || e.key === 'K')) {
    e.preventDefault();
    reorderSelected('up');
  // Shift+j/k / Shift+↑↓ — extend selection range
  } else if (e.shiftKey && (e.key === 'ArrowDown' || e.key === 'J')) {
    e.preventDefault();
    if (currentIdx < items.length - 1) app.extendSelection(items[currentIdx + 1].dataset.id);
  } else if (e.shiftKey && (e.key === 'ArrowUp' || e.key === 'K')) {
    e.preventDefault();
    if (currentIdx > 0) app.extendSelection(items[currentIdx - 1].dataset.id);
  } else if (e.key === 'ArrowDown' || e.key === 'j') {
    e.preventDefault();
    app.setSelected(items[currentIdx < items.length - 1 ? currentIdx + 1 : 0].dataset.id);
  } else if (e.key === 'ArrowUp' || e.key === 'k') {
    e.preventDefault();
    app.setSelected(items[currentIdx > 0 ? currentIdx - 1 : items.length - 1].dataset.id);
  } else if (e.key === ' ' && st.selectedId) {
    e.preventDefault();
    if (st.selectedIds.size > 1) {
      // Bulk complete/uncomplete; the selection moves on to the next task.
      const ids = [...st.selectedIds];
      if (!(await app.mayComplete(ids))) return;
      const next = items.find((el, i) => i > currentIdx && !st.selectedIds.has(el.dataset.id))
                || items.find(el => !st.selectedIds.has(el.dataset.id));
      app.setSelected(next?.dataset.id || null);
      await app.completeMany(ids);
    } else {
      const task = st.tasks.find(t => t.id === st.selectedId);
      if (!task) return;
      const completing = !task.closed_at;
      if (completing) {
        if (app.pendingCompletions.has(task.id)) {
          app.cancelPendingComplete(task.id);
        } else {
          app.tickTask(task.id);
        }
      } else {
        app.updateTask(task.id, { completed: false });
        app.showUndo('Task reopened', () => app.completeTasks([task.id], true));
      }
    }
  } else if (['ArrowRight', 'l', 'ArrowLeft', 'h'].includes(e.key) && !e.ctrlKey && !e.metaKey && !e.altKey &&
             st.selectedIds.size === 1) {
    // → / l open the selected line's details, ← / h close them (vim's hjkl).
    if (app.toggleDetails(st.selectedId, e.key === 'ArrowRight' || e.key === 'l')) e.preventDefault();
  } else if (e.key === 'Enter' && st.selectedId) {
    e.preventDefault();
    app.openEditModal(st.selectedId);
  } else if (e.key === 'Delete' && st.selectedIds.size) {
    // Delete closes the selected open tasks undone (won't do); a task is
    // never deleted. Closed ones stay as they are.
    e.preventDefault();
    const ids = [...st.selectedIds].filter(id => !st.tasks.find(t => t.id === id)?.closed_at);
    if (!ids.length) return;
    // Move the selection to the next surviving card
    const next = items.find(el => !st.selectedIds.has(el.dataset.id) &&
                   items.indexOf(el) > currentIdx)
              || items.find(el => !st.selectedIds.has(el.dataset.id));
    st.selectedId = next?.dataset.id || null;
    st.selectedIds = new Set(st.selectedId ? [st.selectedId] : []);
    st.anchorId = st.selectedId;
    await app.wontDoMany(ids);
  } else if ((e.key === 't' || e.key === 'T') && st.selectedIds.size) {
    e.preventDefault();
    const ids = [...st.selectedIds];
    const removing = e.key === 'T';
    app.openBulkTagModal(ids, removing);
  } else if (e.key === 'm' && !e.ctrlKey && !e.metaKey && !e.altKey && app.listSet.size && st.selectedIds.size) {
    // m: move the selected tasks to a list (the selection bar's List…).
    e.preventDefault();
    app.openBulkListModal([...st.selectedIds]);
  } else if (e.key === 'a' && !e.ctrlKey && !e.metaKey && !e.altKey && people.users && st.selectedIds.size) {
    e.preventDefault();
    app.openBulkAssignModal([...st.selectedIds]);
  } else if (e.key === 'Escape' && (st.expandedId || st.detailsMode) && !st.selectMode) {
    st.detailsMode = false;
    app.setExpanded(null);   // collapse first; the next Esc clears the selection
  } else if (e.key === 'Escape') {
    app.exitSelectMode();
  }
});

}
