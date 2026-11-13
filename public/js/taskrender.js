// A task app's list, drawn (tasks.js initTasksInstance): render() draws the
// shown tab's cards (Focus, Open in sections or as the Plan board,
// Closed by year) from app.st and wires them; notes and comments come
// rendered as pages are (htmlOf). What it uses of the app comes in app
// (its state in app.st).
import TasksCore from '../tasks-core.js';
import { renderFigures } from './figures.js';
import { openDatePicker } from './datepicker.js';
import { commentCardHtml, commentStamp, importanceMark, subtaskCounts, dueDateClass, esc, formatDate, linkifyTitle, plainTitle, shortDate, startsAfterDeadline, todayStr, urgencyClass } from './taskutil.js';
import { plainHtml, renderedHtml, renderTexts } from './tasktexts.js';

const { byOpenOrder, SORTS } = TasksCore;

export function initTaskRender(app) {
const { st } = app;

// The HTML of a note or comment (renderedHtml). One not rendered yet (a
// task another task list app or the MCP just changed) shows as plain text, is
// fetched, and the list and the modal's comments redrawn when it is here.
let missingTexts = null;
function htmlOf(kind, text) {
  const html = renderedHtml[kind].get(text);
  if (html !== undefined) return html;
  if (!missingTexts) {
    missingTexts = { notes: [], comments: [] };
    queueMicrotask(async () => {
      const want = missingTexts;
      missingTexts = null;
      if (!await renderTexts(want)) return;
      render();
      if (app.modal.open && st.commentsTaskId) app.renderComments(st.tasks.find(t => t.id === st.commentsTaskId));
    });
  }
  missingTexts[kind].push(text);
  return plainHtml(text);
}

// The shown tab's tasks in its order. Open and Focus have the same two
// sections (SECTIONS): Unsorted (newTasks) above Sorted (orderedTasks);
// Focus is Open filtered. A due task has no section of its own: it keeps
// its place in the order, and its urgency colour marks it.
function inViewOrder(filtered) {
  let orderedTasks, newTasks = [];
  if (st.activeView === 'open' && app.activeSort()) {
    orderedTasks = [...filtered].reverse().sort(SORTS[app.activeSort()]);
  } else if (app.SECTIONS[st.activeView]) {
    // TasksCore.byOpenOrder, which the Plan board uses too. Reversed first,
    // so tasks created in the same second (created_at has no finer
    // resolution) keep newest-first.
    const sorted = [...filtered].reverse().sort(byOpenOrder);
    orderedTasks = sorted.filter(t => t.sort_order != null);
    newTasks     = sorted.filter(t => t.sort_order == null);
  } else {
    orderedTasks = app.sortTasks(filtered);
  }
  return { orderedTasks, newTasks };
}

// A tab with nothing to show says so (Focus offers the Plan board).
function renderEmpty() {
  if (st.selectedIds.size) app.clearSelection();
  if (st.activeView === 'focus') {
    app.listEl.innerHTML = '<li class="tasks-empty">Nothing planned for today. ' +
      '<button type="button" class="btn btn-sm" data-goto-tab="plan">Plan your day</button></li>';
    app.listEl.querySelector('[data-goto-tab]').addEventListener('click', () => app.switchTab('plan'));
  } else {
    app.listEl.innerHTML = '<li class="tasks-empty">No tasks here.</li>';
  }
}

// A task's card. draggable: it can be picked up (the tabs in the manual
// order); extraClass: more classes for its <li>.
function taskHTML(task, draggable = !!app.SECTIONS[st.activeView], extraClass = '') {
  // A task ticked but not yet done stays ticked when another completion
  // re-renders the list.
  const pending = app.pendingCompletions.has(task.id);
  const checked = task.closed_at || pending ? 'checked' : '';
  const closedClass = (task.closed_at ? 'task-closed' : '') + (task.wont_do ? ' task-wont-do' : '') +
    (pending ? ' task-completing' : '');
  const dueClass = dueDateClass(task);
  const urgClass = urgencyClass(task);
  // A separate list's tag first and marked (icon, tint, tooltip), as in
  // the filter and the modal.
  const isListTag = t => app.listSet.has(t.toLowerCase());
  const tags = [...(task.tags || []).filter(isListTag), ...(task.tags || []).filter(t => !isListTag(t))]
    .map(t => isListTag(t)
      ? `<span class="task-tag task-tag-list" title="${esc(app.listTooltip(t))}">#${esc(t)}</span>`
      : `<span class="task-tag">#${esc(t)}</span>`).join('');
  const recIcon = task.recurrence ? '<span class="task-recurring" title="Recurring">↻</span>'
    : task.keep ? `<span class="task-recurring task-keep" title="Keep: back to ${esc(app.tabLabel('open'))} when ticked off">⟲</span>` : '';
  // A keep task's last completion, e.g. when the milk was last bought.
  const lastDoneDisplay = task.keep && task.last_done && !task.closed_at
    ? `<span class="task-last-done" title="Last done">✓ ${esc(shortDate(task.last_done))}</span>` : '';
  // Comments: a count; hovering shows the latest, a click opens them.
  const comments = task.comments || [];
  const lastComment = comments[comments.length - 1];
  const commentsDisplay = lastComment
    ? `<button type="button" class="task-comments-btn" data-id="${esc(task.id)}" title="${esc(commentStamp(lastComment) + ': ' + lastComment.text)}" aria-label="${comments.length} comment${comments.length === 1 ? '' : 's'}">💬 ${comments.length}</button>` : '';
  // A planned task is either in Focus already (start today or earlier) or
  // planned for later; the card's left edge and its 📅 date say which, the
  // same on every tab.
  const planned = !task.closed_at && task.start_date
    ? (task.start_date <= todayStr() ? 'now' : 'later') : '';
  const focusClass = planned ? ` task-planned-${planned}` : '';
  // A past start date reads "since Sep 21" in the plain meta colour, never
  // as a warning, so only today's date is coloured amber.
  const focusMetaClass = planned === 'later' ? 'focus-later'
    : planned && task.start_date === todayStr() ? 'focus-now' : '';
  const dueDisplay = !task.closed_at
    ? `<span class="task-due task-clickable-date ${dueClass}${task.due_date ? '' : ' task-date-empty'}" data-id="${esc(task.id)}" data-field="due_date" title="${task.due_date ? 'Click to change due date' : 'Set due date'}">⏰${task.due_date ? ' ' + esc(formatDate(task.due_date)) : ''}</span>`
    : (task.due_date ? `<span class="task-due ${dueClass}">⏰ ${esc(formatDate(task.due_date))}</span>` : '');
  // A past start date means "available since", not late — so it reads
  // "since Sep 21" with no warning styling.
  const focusLabel = !task.start_date ? ''
    : (!task.closed_at && task.start_date < todayStr())
      ? `since ${shortDate(task.start_date)}` : formatDate(task.start_date);
  const focusDisplay = !task.closed_at
    ? `<span class="task-date-info task-focus-info task-clickable-date ${focusMetaClass}${task.start_date ? '' : ' task-date-empty'}" data-id="${esc(task.id)}" data-field="start_date" title="${task.start_date ? 'Click to change start date' : 'Set start date'}">📅${focusLabel ? ' ' + esc(focusLabel) : ''}</span>`
    : (task.start_date ? `<span class="task-date-info task-focus-info ${focusMetaClass}">📅 ${esc(formatDate(task.start_date))}</span>` : '');
  const closedDisplay = task.closed_at
    ? (task.wont_do
        ? `<span class="task-date-info task-wontdo-badge">✗ won't do</span>`
        : `<span class="task-date-info">done: ${esc(task.closed_at.slice(0, 10))}</span>`)
    : '';

  // Marks that there are notes, which a compact line hides; on every card
  // alike, so expanding a line changes nothing on it.
  // A task with subtasks (checklist items in its notes) says how many are
  // done instead: "☑ 1/3".
  const subtasks = subtaskCounts(task.description);
  const subtasksLabel = `${subtasks.done} of ${subtasks.total} subtask${subtasks.total === 1 ? '' : 's'} done`;
  const notesDisplay = subtasks.total
    ? `<button type="button" class="task-has-notes task-subtasks${subtasks.done === subtasks.total ? ' task-subtasks-done' : ''}" data-id="${esc(task.id)}" title="${subtasksLabel}" aria-label="${subtasksLabel}">☑ ${subtasks.done}/${subtasks.total}</button>`
    : task.description?.trim()
    ? `<button type="button" class="task-has-notes" data-id="${esc(task.id)}" title="Has a description" aria-label="Has a description">📝</button>` : '';
  const skipBtnHtml = (!task.closed_at && task.recurrence)
    ? `<button class="task-skip-btn" data-id="${esc(task.id)}" title="Skip this occurrence (series continues)">⏭</button>`
    : '';
  const tagsRight = tags ? `<span class="task-tags-right">${tags}</span>` : '';
  const privateDisplay = task.private
    ? '<span class="task-private" title="Private: only you see it" aria-label="Private">🔒</span>' : '';
  const hasRealMeta = !!(tags || task.due_date || task.start_date || task.recurrence ||
    task.keep || task.private || task.closed_at || commentsDisplay || notesDisplay);
  const hasMeta = !task.closed_at || dueDisplay || focusDisplay || closedDisplay || recIcon || privateDisplay || tagsRight || notesDisplay;
  const metaClass = (hasMeta && !hasRealMeta) ? 'task-meta task-meta--hover-only' : 'task-meta';
  const metaHtml = hasMeta ? `<div class="${metaClass}">${tagsRight}<span class="task-meta-right">${skipBtnHtml}${privateDisplay}${recIcon}${notesDisplay}${commentsDisplay}${lastDoneDisplay}${dueDisplay}${focusDisplay}${closedDisplay}</span></div>` : '';
  const descHtml = task.description ? `<div class="task-desc page-content">${htmlOf('notes', task.description)}</div>` : '';
  // The latest comments, newest first: shown only when a compact task is
  // expanded (full cards have the 💬 count; the modal has them all).
  const earlier = comments.length - 3;
  const commentsHtml = comments.length
    ? `<div class="task-card-comments">${comments.slice(-3).reverse().map(c =>
        commentCardHtml(commentStamp(c), htmlOf('comments', c.text))).join('')}` +
      (earlier > 0 ? `<button type="button" class="task-comments-more" data-id="${esc(task.id)}">${earlier} earlier…</button>` : '') + '</div>'
    : '';
  const dragAttr = draggable ? 'draggable="true" data-drag' : '';
  // Compact lines with details get the expand button (CSS hides it on
  // full cards, which show them).
  const detailsShown = st.compact && task.id === st.expandedId && st.detailsOpen;
  const expandBtn = descHtml || commentsHtml
    ? `<button type="button" class="task-expand-btn" data-id="${esc(task.id)}" aria-expanded="${detailsShown}" aria-label="Description and comments" title="Description and comments (l / h)">${app.EXPAND_ICON}</button>` : '';
  return `
    <li class="task-item ${closedClass} ${urgClass}${focusClass} ${extraClass}${st.compact && task.id === st.expandedId ? ' task-expanded' + (st.detailsOpen ? ' task-details' : '') : ''}" data-id="${esc(task.id)}" ${dragAttr}>
      <label class="task-checkbox">
        <input type="checkbox" ${checked} data-id="${esc(task.id)}">
      </label>
      <div class="task-content" data-id="${esc(task.id)}">
        ${importanceMark(task)}<span class="task-title" data-id="${esc(task.id)}">${linkifyTitle(task.title)}</span>
      </div>
      <div class="task-actions">
        ${expandBtn}<button type="button" class="task-menu-btn" data-id="${esc(task.id)}" aria-label="More actions" aria-haspopup="menu" title="More actions">${app.MORE_ICON}</button>
      </div>
      ${metaHtml}${descHtml}${commentsHtml}
    </li>`;
}

// The Plan board: one section per day, whose dividers double as drop
// targets.
function planHTML(filtered) {
  const filtering = app.userFiltering();
  const today = todayStr();
  return app.boardBuckets(filtered).map(s => {
    // A section with cards folds from its header (not while filtering:
    // a match must never hide in a folded section).
    const fid = app.foldId(s.key, today);
    const foldable = s.tasks.length && !filtering;
    const isFolded = foldable && st.folded.has(fid);
    const over = app.LIMIT && s.date && s.tasks.length >= app.LIMIT;
    const overCls = over ? ' board-day--over' : '';
    const overIcon = over
      ? `<span class="board-day-over-icon" title="Over the limit (${s.tasks.length}/${app.LIMIT})">⚠</span>`
      : '';
    const dropAttr = (s.droppable ? ` data-drop-bucket="${s.key}" data-drop-day="${s.date || ''}"` : '') +
      (s.ordered ? ' data-ordered="1"' : '');
    const emptyCls = (s.tasks.length ? '' : ' board-day--empty') + (isFolded ? ' board-day--folded' : '');
    // The deadlines on this day, wherever their cards sit, Unplanned's
    // overdue tasks (red) and Today's carried-over ones (amber; both shown
    // even folded): a click lists them (headerTasksMenu), the tooltip
    // names them.
    const listBtn = (list, which, title, cls = '') => list?.length
      ? `<button type="button" class="board-day-due${cls}" data-header-tasks="${esc(s.key)}" data-header-list="${which}"` +
        ` title="${esc(title + ': ' + list.map(t => plainTitle(t.title)).join(', '))}">${list.length} ${which}</button>`
      : '';
    const dueBtn = listBtn(s.carried, 'carried', 'Carried over, on Focus since an earlier day', ' board-day-carried') +
      listBtn(s.due, 'due', `Due ${s.label}`) +
      listBtn(s.overdue, 'overdue', 'Overdue', ' board-day-overdue');
    // The whole header folds the section (the label is its button, for
    // the keyboard and screen readers); only the due/overdue count does its own.
    const foldAttr = foldable ? ` data-fold="${esc(fid)}"` : '';
    const header = `<li class="board-day-divider${overCls}${emptyCls}${foldable ? ' board-day--foldable' : ''}"${dropAttr}${foldAttr}>` +
      `<span class="board-day-label">` + (foldable
        ? `<button type="button" class="board-day-fold" data-fold="${esc(fid)}" aria-expanded="${!isFolded}"` +
          ` title="${isFolded ? 'Show' : 'Hide'} these tasks">${esc(s.label)}</button>`
        : esc(s.label)) + `${dueBtn}</span>` +
      `${overIcon}<span class="board-day-count">${s.tasks.length}</span></li>`;
    // An empty section is just its header row, which is itself the drop
    // target (styled tall enough to hit). Nothing grows when a drag
    // starts, so the section you aim at never moves under the pointer.
    // A section with Unsorted tasks (listed first, in the manual order)
    // labels them and the Sorted ones below, as Open's sections do, one
    // level down: so you see what is still to be ranked, and moves follow
    // Open's rules (planSectionList). The labels are no sections
    // (.plan-subdivider, not .board-day-divider); Sorted takes drops, as
    // Open's heading does. No Unsorted task: no labels.
    const unsorted = s.ordered ? s.tasks.filter(t => t.sort_order == null).length : 0;
    const sub = (label, n) => `<li class="plan-subdivider" data-sub="${label.toLowerCase()}">` +
      `<span>${label}</span><span class="board-day-count">${n}</span></li>`;
    // A carried-over card's "since" badge is amber here (task-carried):
    // on Today it is a slipped plan, not just an available task. Unsorted
    // cards are hollow (task-inbox), as on Open and Focus.
    const body = isFolded ? '' : s.tasks.map((t, i) =>
      (unsorted && i === 0 ? sub('Unsorted', unsorted) : '') +
      (unsorted && i === unsorted ? sub('Sorted', s.tasks.length - unsorted) : '') +
      taskHTML(t, true, 'board-item' + (i < unsorted ? ' task-inbox' : '') +
        (TasksCore.isCarried(t, today) ? ' task-carried' : ''))).join('');
    return header + body;
  }).join('');
}

// Focus, Open and Closed: the list's HTML.
function listHTML(orderedTasks, newTasks) {
  let html = '';
  let truncated = false;
  if (st.activeView === 'focus') {
    const inFocus = orderedTasks.length + newTasks.length;
    if (app.LIMIT && inFocus >= app.LIMIT) {
      html += `<li class="focus-stale-bar focus-over-limit">` +
        `<span>⚠ ${inFocus} tasks in ${esc(app.tabLabel('focus'))} (limit: ${app.LIMIT})</span></li>`;
    }
  }
  // No tab is highlighted in Closed, so the list says where you are.
  if (st.activeView === 'closed') {
    // Earlier years (the yearly archives) are one click away.
    const shown = st.closedYear || app.thisYear;
    const years = st.closedYears.includes(shown) ? st.closedYears : [...st.closedYears, shown].sort().reverse();
    const yearBtns = years.length > 1
      ? `<span class="closed-years">${years.map(y =>
          `<button type="button" data-closed-year="${y}" aria-pressed="${y === shown}">${y}</button>`).join('')}</span>`
      : '';
    html += `<li class="task-inbox-divider task-inbox-divider--only closed-heading">Closed${yearBtns}</li>`;
  }
  // Limit what the Closed view shows
  let displayTasks = orderedTasks;
  if (st.activeView === 'closed' && orderedTasks.length > st.closedLimit) {
    displayTasks = orderedTasks.slice(0, st.closedLimit);
    truncated = true;
  }
  // Open sorted: by deadline the tasks with one, then those without, each
  // under a heading; newest first under one. Not drop targets: nothing
  // drags here.
  if (st.activeView === 'open' && app.activeSort()) {
    const heading = (label, n) => n ? `<li class="plan-subdivider sort-heading">` +
      `<span>${label}</span><span class="board-day-count">${n}</span></li>` : '';
    const group = (label, tasks) => heading(label, tasks.length) + tasks.map(t => taskHTML(t, false)).join('');
    html += app.activeSort() === 'due'
      ? group('By deadline', displayTasks.filter(t => t.due_date)) + group('No deadline', displayTasks.filter(t => !t.due_date))
      : group('Newest first', displayTasks);
    displayTasks = [];
  }
  // Open and Focus: both sections always have a heading, even when empty,
  // because the heading is where a task is dropped to enter an empty section.
  else if (app.SECTIONS[st.activeView]) {
    html += app.sectionHTML('unsorted', 'Unsorted', newTasks.length);
    html += newTasks.map(t => taskHTML(t, undefined, 'task-inbox')).join('');
    html += app.sectionHTML('sorted', 'Sorted', orderedTasks.length);
  }
  html += displayTasks.map(t => taskHTML(t)).join('');
  if (truncated) {
    html += `<li class="tasks-show-more"><button class="btn" id="tasks-show-more-btn">Show more (${orderedTasks.length - st.closedLimit} remaining)</button></li>`;
  }
  return html;
}

// What the cards of a fresh list answer to, and the selection carried over.
function wireListHandlers() {
  app.listEl.querySelectorAll('.task-menu-btn').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      app.openTaskMenu(btn.dataset.id, btn);
    });
  });
  // 💬: a compact line's toggles its details (the latest comments among
  // them); a full card shows them already, so there it opens the modal at
  // the comments.
  app.listEl.querySelectorAll('.task-comments-btn').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      if (!app.toggleDetails(btn.dataset.id)) app.openEditModal(btn.dataset.id, { focusComment: true });
    });
  });
  app.listEl.querySelectorAll('.task-expand-btn').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      app.toggleDetails(btn.dataset.id);
    });
  });
  // 📝: a compact line's toggles its details; a full card shows the notes,
  // so there it only selects the card.
  app.listEl.querySelectorAll('.task-has-notes').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      if (!app.toggleDetails(btn.dataset.id)) app.setSelected(btn.dataset.id);
    });
  });

  // "Show more" button
  const showMoreBtn = app.listEl.querySelector('#tasks-show-more-btn');
  if (showMoreBtn) {
    showMoreBtn.addEventListener('click', () => {
      st.closedLimit += 50;
      render();
    });
  }

  // Attach event listeners
  app.listEl.querySelectorAll('.task-checkbox input').forEach(cb => {
    cb.addEventListener('change', e => {
      const id = e.target.dataset.id;
      const completed = e.target.checked;
      const task = st.tasks.find(t => t.id === id);
      if (completed) {
        app.tickTask(id).then(ticked => { if (!ticked) e.target.checked = false; });
      } else if (!app.cancelPendingComplete(id)) {
        app.updateTask(id, { completed: false });
        if (task) app.showUndo('Task reopened', () => app.completeTasks([id], true));
      }
    });
  });

  app.listEl.querySelectorAll('.task-clickable-date').forEach(el => {
    el.addEventListener('click', e => {
      e.stopPropagation();
      const id = el.dataset.id;
      const field = el.dataset.field;
      const task = st.tasks.find(t => t.id === id);
      if (!task) return;
      openDatePicker(task[field] || '', d => {
        if (field === 'start_date' && startsAfterDeadline(task, d)) app.planOnDay(id, d);
        else app.updateTask(id, { [field]: d });
      }, el, todayStr());
    });
  });

  app.listEl.querySelectorAll('.task-comments-more').forEach(btn => {
    btn.addEventListener('click', e => { e.stopPropagation(); app.openEditModal(btn.dataset.id, { focusComment: true }); });
  });

  app.listEl.querySelectorAll('.task-skip-btn').forEach(btn => {
    btn.addEventListener('click', e => { e.stopPropagation(); app.skipOccurrence(btn.dataset.id); });
  });


  // Double-click anywhere on a card, title and notes included, opens the
  // modal; only its controls keep their own.
  app.listEl.querySelectorAll('.task-item').forEach(el => {
    el.addEventListener('dblclick', e => {
      // Touch: two taps select and open (the tap handler), never more.
      if (st.lastPointerType === 'touch' || st.selectMode) return;
      if (e.target.closest('input, button, a, label, .task-clickable-date')) return;
      window.getSelection()?.removeAllRanges();
      app.openEditModal(el.dataset.id);
    });
  });

  // Drag and drop
  st.touchDrop = null;
  if (st.activeView === 'plan') {
    app.setupBoardDragAndDrop();
  } else if (app.SECTIONS[st.activeView] && !app.activeSort()) {
    app.setupDragAndDrop();
  }

  // Restore or clear selection after re-render
  if (st.selectedIds.size) {
    const visSet = new Set(app.visibleItems().map(el => el.dataset.id));
    st.selectedIds = new Set([...st.selectedIds].filter(id => visSet.has(id)));
    if (!visSet.has(st.selectedId)) st.selectedId = st.selectedIds.size ? [...st.selectedIds][0] : null;
    if (!visSet.has(st.anchorId))   st.anchorId = st.selectedId;
    app.updateSelectionClasses();
    if (st.selectedId) {
      const el = app.listEl.querySelector(`.task-item[data-id="${CSS.escape(st.selectedId)}"]`);
      if (el) el.scrollIntoView({ block: 'nearest' });
    }
  }

  // Click to select (clicks on checkbox/button/link are handled separately):
  // Shift+click a range from the anchor, Ctrl/Cmd+click one more or less.
  // A right-click on a selected card keeps the selection for its menu.
  app.listEl.querySelectorAll('.task-item').forEach(el => {
    el.addEventListener('mousedown', e => {
      if (st.selectMode || e.target.closest('input, button, a, .task-checkbox')) return;   // selecting: the click toggles
      const id = el.dataset.id;
      if (e.button !== 0 && st.selectedIds.has(id)) return;
      if (e.shiftKey && st.selectedId) { e.preventDefault(); app.extendSelection(id); }
      else if (e.ctrlKey || e.metaKey) { e.preventDefault(); app.toggleSelection(id); }
      else {
        st.pressedCard = el;
        try { app.setSelected(id); } finally { st.pressedCard = null; }
      }
    });
  });
}

function render() {
  app.syncClosedLink();
  const filtered = app.filterTasks();
  const { orderedTasks, newTasks } = inViewOrder(filtered);
  if (st.activeView !== 'plan' && orderedTasks.length === 0 && newTasks.length === 0) {
    renderEmpty();
    return;
  }
  app.listEl.innerHTML = st.activeView === 'plan' ? planHTML(filtered) : listHTML(orderedTasks, newTasks);
  renderFigures(app.listEl);
  wireListHandlers();
}

return { htmlOf, render };
}
