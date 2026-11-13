// A task app's menus and confirmations (tasks.js initTasksInstance): a
// card's ⋯ menu (every card action in one place, by tap too), a Plan
// header's menu of its due tasks, and the Delete and deadline dialogs.
// What it uses of the app comes in app (its state in app.st).
import { copyText } from './util.js';
import { openDatePicker } from './datepicker.js';
import { dateOffset, esc, formatDate, plainTitle, shortDate, showNotice, todayStr } from './taskutil.js';

export function initTaskMenu(app) {
const { st } = app;

// Every card action in one place, reachable by tap — the hover buttons,
// double-click and keyboard shortcuts don't exist on a phone. Renders as a
// popover at the button on desktop and as a bottom sheet on narrow screens.

st.taskMenu = null;

function closeTaskMenu() {
  if (!st.taskMenu) return;
  st.taskMenu.backdrop.remove();
  st.taskMenu.el.remove();
  document.removeEventListener('keydown', st.taskMenu.onKey, true);
  st.taskMenu.anchor?.focus({ preventScroll: true });
  st.taskMenu = null;
}

// `at` ({x, y}, a right-click): the menu opens there instead of at the
// anchor (the ⋯ button). On a card of a multi-selection the menu acts on
// every selected task (runSelectionAction), as the selection bar does.
function openTaskMenu(id, anchor, at) {
  closeTaskMenu();
  if (app.inSelection(id)) {
    const allDone = st.tasks.filter(t => st.selectedIds.has(t.id)).every(t => t.completed_at);
    const items = [
      ['tag', 'Tag…'],
      !allDone && ['today', 'Start today'],
      ['done', allDone ? 'Reopen' : 'Done'],
      ['delete', 'Delete…', 'danger'],
    ].filter(Boolean);
    showMenu(`${st.selectedIds.size} tasks`, items, action => app.runSelectionAction(action), anchor, at);
    return;
  }
  const task = st.tasks.find(t => t.id === id);
  if (!task) return;
  const items = task.completed_at ? [
    ['edit', 'Edit…'],
    ['reopen', 'Reopen'],
    ['copylink', 'Copy link'],
    ['select', 'Select several'],
    ['delete', 'Delete…', 'danger'],
  ] : [
    ['edit', 'Edit…'],
    ['today', 'Start today'],
    ['tomorrow', 'Start tomorrow'],
    ['pick', 'Pick start date…'],
    task.start_date && ['unplan', 'Unplan'],
    task.recurrence && ['skip', 'Skip this occurrence'],
    ['wontdo', "Won't do"],
    ['copylink', 'Copy link'],
    ['select', 'Select several'],
    ['delete', 'Delete…', 'danger'],
  ].filter(Boolean);
  showMenu(plainTitle(task.title), items, action => runTaskAction(task, action, anchor), anchor, at);
}

// A Plan header's "N due" (a day), "N overdue" (Unplanned) or "N carried"
// (Today): those tasks as a menu (a bottom sheet on phones); picking one
// selects its card, wherever it sits on the board, unfolding its section.
function headerTasksMenu(btn) {
  const section = app.boardBuckets(app.filterTasks()).find(s => s.key === btn.dataset.headerTasks);
  const which = btn.dataset.headerList || 'due';
  const list = section?.[which] || [];
  if (!list.length) return;
  const items = list.map(t => [t.id, esc(plainTitle(t.title))]);
  const title = { due: `Due ${section.label}`, overdue: 'Overdue', carried: 'Carried over' }[which];
  showMenu(title, items, id => { app.unfoldFor(id); app.setSelected(id); }, btn);
}

// The menu itself: `items` are [action, label, class?]; `run(action)`.
function showMenu(title, items, run, anchor, at) {
  const backdrop = document.createElement('div');
  backdrop.className = 'task-menu-backdrop';
  const el = document.createElement('div');
  el.className = 'task-menu';
  el.setAttribute('role', 'menu');
  el.innerHTML = `<div class="task-menu-title">${esc(title)}</div>` + items.map(([key, label, cls]) =>
    `<button type="button" role="menuitem" data-action="${key}"${cls ? ` class="${cls}"` : ''}>${label}</button>`
  ).join('');
  document.body.append(backdrop, el);

  // Desktop popover: under the button, right-aligned, flipped up if needed.
  // (The narrow-screen bottom sheet is pure CSS and ignores these.)
  const r = at ? { top: at.y, bottom: at.y, right: at.x } : anchor.getBoundingClientRect();
  const m = el.getBoundingClientRect();
  const top = r.bottom + 4 + m.height > window.innerHeight ? r.top - 4 - m.height : r.bottom + 4;
  el.style.top = `${Math.max(4, top)}px`;
  const left = at ? at.x : r.right - m.width;
  el.style.left = `${Math.max(4, Math.min(left, window.innerWidth - m.width - 4))}px`;

  const onKey = e => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeTaskMenu(); }
    else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault(); e.stopPropagation();
      const btns = [...el.querySelectorAll('button')];
      const i = btns.indexOf(document.activeElement);
      btns[(i + (e.key === 'ArrowDown' ? 1 : -1) + btns.length) % btns.length].focus();
    }
  };
  document.addEventListener('keydown', onKey, true);
  backdrop.addEventListener('click', closeTaskMenu);
  st.taskMenu = { el, backdrop, anchor, onKey };
  el.querySelector('button')?.focus({ preventScroll: true });

  el.addEventListener('click', e => {
    const action = e.target.closest('[data-action]')?.dataset.action;
    if (!action) return;
    closeTaskMenu();
    run(action);
  });
}

function runTaskAction(task, action, anchor) {
  const id = task.id;
  switch (action) {
    case 'edit':     app.openEditModal(id); break;
    case 'today':    app.setStart(id, dateOffset(0)); break;
    case 'tomorrow': app.setStart(id, dateOffset(1)); break;
    case 'unplan':   app.setStart(id, null); break;
    case 'pick':
      openDatePicker(task.start_date || '', d => d !== undefined && app.setStart(id, d), anchor, todayStr());
      break;
    case 'skip':     app.skipOccurrence(id); break;
    case 'wontdo':   app.markWontDo(id); break;
    case 'reopen':
      app.updateTask(id, { completed: false });
      app.showUndo('Task reopened', () => app.completeTasks([id], true));
      break;
    case 'delete':   app.deleteWithUndo([id]); break;
    case 'select':   app.enterSelectMode(id); break;
    case 'copylink':
      copyText(location.origin + taskHref(task)).then(ok => showNotice(ok ? 'Link copied' : 'Could not copy the link'));
      break;
  }
}

// The link that opens this task in the tasks (render.task_link): an open
// one on Open, a done one on Completed (its archive's year when earlier),
// in its list when it has one.
function taskHref(task) {
  const params = new URLSearchParams({ task: task.id, view: task.completed_at ? 'completed' : 'open' });
  const list = app.taskList(task);
  if (list) params.set('list', list);
  if (task.completed_at && st.completedYear) params.set('year', st.completedYear);
  return `/tasks?${params}`;
}

async function postReorder(body) {
  await fetch('/api/tasks/reorder', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  app.refreshOtherInstances(app.instanceId);
}

// ── Delete confirmation dialog ─────────────────────────────────────────────

app.deleteCancelBtn?.addEventListener('click', () => app.deleteModal.close());

function confirmDelete(title, count = 1) {
  const labelEl = app.deleteModal.querySelector('.modal-label');
  if (labelEl) labelEl.textContent = count > 1 ? `Delete ${count} tasks?` : 'Delete task?';
  app.deleteTitleEl.textContent = count > 1 ? '' : title;
  return confirmModal(app.deleteModal, app.deleteConfirmBtn, app.deleteCancelBtn);
}

// A start date after the task's deadline: the task would start after it is
// due, usually a slip, so it asks whether the deadline moves along.
function confirmDeadlineMove(task, day) {
  const when = d => ({ Today: 'today', Tomorrow: 'tomorrow', Yesterday: 'yesterday' })[formatDate(d)] || shortDate(d);
  app.deadlineTextEl.textContent =
    `“${plainTitle(task.title)}” is due ${when(task.due_date)}. Starting it ${when(day)} moves the deadline to that day too.`;
  return confirmModal(app.deadlineModal,
    document.getElementById('task-deadline-confirm' + app.sfx),
    document.getElementById('task-deadline-cancel' + app.sfx));
}

// Show a confirmation <dialog>; resolves true for OK, false for Cancel/Esc.
function confirmModal(dialog, okBtn, cancelBtn) {
  return new Promise(resolve => {
    dialog.showModal();
    let resolved = false;
    const finish = ok => {
      if (resolved) return;
      resolved = true;
      okBtn.removeEventListener('click', onOk);
      cancelBtn.removeEventListener('click', onCancel);
      dialog.removeEventListener('close', onClose);
      dialog.close();
      resolve(ok);
    };
    const onOk     = () => finish(true);
    const onCancel = () => finish(false);
    const onClose  = () => finish(false);
    okBtn.addEventListener('click', onOk);
    cancelBtn.addEventListener('click', onCancel);
    dialog.addEventListener('close', onClose, { once: true });
  });
}

return { openTaskMenu, headerTasksMenu, showMenu, postReorder, confirmDelete, confirmDeadlineMove };
}
