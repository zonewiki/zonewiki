// A task app's list, drawn (tasks.js initTasksInstance): render() draws the
// shown tab's cards (Focus, Open in sections or as the Plan board,
// Closed by year) from app.st and wires them; notes and comments come
// rendered as pages are (htmlOf). What it uses of the app comes in app
// (its state in app.st).
import TasksCore from '../tasks-core.js';
import { renderFigures } from './figures.js';
import { openDatePicker } from './datepicker.js';
import { commentCardHtml, commentStamp, importanceMark, subtaskCounts, dueDateClass, esc, formatDate, linkifyTitle, people, plainTitle, shortDate, startsAfterDeadline, todayStr, urgencyClass } from './taskutil.js';
import { highlightIn, plainHtml, renderedHtml, renderTexts } from './tasktexts.js';

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

// The shown tab's tasks in its order. Open and Focus show the manual order
// as one list (ORDERED), Focus being Open filtered. A due task has no
// section of its own: it keeps its place in the order, and its urgency
// colour marks it. Updates: the latest news first (of many, the top is what
// just happened), a read task where its news put it.
function inViewOrder(filtered) {
  let orderedTasks;
  if (st.activeView === 'updates') {
    const at = t => app.newsOf(t)?.at || '';
    orderedTasks = [...filtered].reverse().sort((a, b) => at(b).localeCompare(at(a)));
  } else if (st.activeView === 'open' && app.activeSort()) {
    orderedTasks = [...filtered].reverse().sort(SORTS[app.activeSort()]);
  } else if (app.ORDERED[st.activeView]) {
    // TasksCore.byOpenOrder, which the Plan board uses too. Reversed first,
    // so tasks created in the same second (created_at has no finer
    // resolution) keep newest-first.
    orderedTasks = [...filtered].reverse().sort(byOpenOrder);
  } else {
    orderedTasks = app.sortTasks(filtered);
  }
  return orderedTasks;
}

// A tab with nothing to show says so (Focus offers the Plan board).
function renderEmpty() {
  if (st.selectedIds.size) app.clearSelection();
  if (st.activeView === 'updates') {
    app.listEl.innerHTML = '<li class="tasks-empty">No updates. A task someone gives you, a comment on one of yours ' +
      'or a mention shows here.</li>';
  } else if (st.today && st.activeView !== 'closed') {
    app.listEl.innerHTML = '<li class="tasks-empty">Nothing planned for today. ' +
      '<button type="button" class="btn btn-sm" data-goto-tab="plan">Plan your day</button></li>';
    app.listEl.querySelector('[data-goto-tab]').addEventListener('click', () => { st.cut = 'day'; app.switchTab('plan'); });
  } else {
    app.listEl.innerHTML = '<li class="tasks-empty">No tasks here.</li>';
  }
}

// A task's card. draggable: it can be picked up (the tabs in the manual
// order); extraClass: more classes for its <li>.
function taskHTML(task, draggable = !!app.ORDERED[st.activeView], extraClass = '') {
  // A task ticked off here shows as it was, struck through (st.ticked), and
  // isn't picked up.
  const pending = st.ticked.has(task.id);
  if (pending) draggable = false;
  const checked = task.closed_at || pending ? 'checked' : '';
  const closedClass = (task.closed_at ? 'task-closed' : '') + (task.wont_do ? ' task-wont-do' : '') +
    (pending ? ' task-completing' : '');
  const dueClass = dueDateClass(task);
  const urgClass = urgencyClass(task);
  // The list the task is in, first and marked (icon, tint, tooltip), as in
  // the filter; not in a view of that one list, where every card would say it.
  // A link to the list (a tap follows it on the selected card only: tasks.js).
  const listRef = app.taskList(task);
  // nor By list, where its section does.
  const listChip = listRef && listRef !== app.initList && !(st.activeView === 'plan' && st.cut === 'list')
    ? `<a class="task-tag task-tag-list" href="/tasks?list=${encodeURIComponent(listRef)}" draggable="false" title="${esc(app.listTooltip(listRef))}">${app.listMembers(listRef) ? '👥 ' : ''}${esc(app.listName(listRef))}</a>` : '';
  const tags = listChip + (task.tags || []).map(t => `<span class="task-tag">#${esc(t)}</span>`).join('');
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
  const plannedClass = planned ? ` task-planned-${planned}` : '';
  // A past start date reads "since Sep 21" in the plain meta colour, never
  // as a warning, so only today's date is coloured amber.
  const startClass = planned === 'later' ? 'start-later'
    : planned && task.start_date === todayStr() ? 'start-now' : '';
  const dueDisplay = !task.closed_at
    ? `<span class="task-due task-clickable-date ${dueClass}${task.due_date ? '' : ' task-date-empty'}" data-id="${esc(task.id)}" data-field="due_date" title="${task.due_date ? 'Click to change due date' : 'Set due date'}">⏰${task.due_date ? ' ' + esc(formatDate(task.due_date)) : ''}</span>`
    : (task.due_date ? `<span class="task-due ${dueClass}">⏰ ${esc(formatDate(task.due_date))}</span>` : '');
  // A past start date means "available since", not late — so it reads
  // "since Sep 21" with no warning styling.
  const startLabel = !task.start_date ? ''
    : (!task.closed_at && task.start_date < todayStr())
      ? `since ${shortDate(task.start_date)}` : formatDate(task.start_date);
  const startDisplay = !task.closed_at
    ? `<span class="task-date-info task-start-info task-clickable-date ${startClass}${task.start_date ? '' : ' task-date-empty'}" data-id="${esc(task.id)}" data-field="start_date" title="${task.start_date ? 'Click to change start date' : 'Set start date'}">📅${startLabel ? ' ' + esc(startLabel) : ''}</span>`
    : (task.start_date ? `<span class="task-date-info task-start-info ${startClass}">📅 ${esc(formatDate(task.start_date))}</span>` : '');
  const closedDisplay = task.closed_at
    ? (task.wont_do
        ? `<span class="task-date-info task-wontdo-badge">✗ won't do</span>`
        : `<span class="task-date-info" title="${esc(task.closed_at.slice(0, 10))}">done: ${esc(shortDate(task.closed_at.slice(0, 10)))}</span>`)
    : '';

  // Marks that there are notes, which the line hides; open or not, so
  // expanding a line changes nothing on it.
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
  // Who has it, after the tags: your own name set apart, the name of
  // someone who can no longer be given tasks struck through.
  const assignee = task.assignee || '';
  const formerAssignee = TasksCore.assigneeGone(task, people);
  const leftList = formerAssignee && !!people.users?.has(assignee);   // none of the list's members now
  // What the task has for you that you haven't seen (TasksCore.newsFor): a
  // task someone gave you is marked new, any other news says what it is.
  // In Updates a task read since keeps its mark, quiet (app.newsOf), and one
  // not read yet has Mark read: the news seen without opening the task.
  const news = app.newsOf(task);
  const unread = !!news && !!TasksCore.newsFor(task, people.me);
  const NEWS = { comment: 'commented', mention: 'mentioned you', closed: 'closed it', reopened: 'reopened it', handed: 'gave it on' };
  const readCls = news && !unread ? ' task-news-read' : '';
  // In Updates the card says it all on a line of its own: everything since
  // you last looked, the oldest first ("bob commented twice · anna closed
  // it"), when the latest was, and under it what the latest comment among
  // them says, so most news is read without opening the task. Plain text,
  // a line or two (the task's dialog has the comment as written).
  const inUpdates = st.activeView === 'updates';
  const said = inUpdates && news ? (news.all || []).filter(n => n.kind === 'comment' && (n.text || '').trim()).pop() : null;
  const newsHtml = inUpdates && news
    ? `<div class="task-news${readCls}"><span class="task-news-what">${esc(TasksCore.newsText(news.all || [news]))}</span>` +
      `<span class="task-news-when">${esc(`${shortDate(news.at.slice(0, 10))} ${news.at.slice(11)}`)}</span>` +
      (said ? `<div class="task-news-comment" title="${esc(`${said.by}'s comment`)}">${esc(said.text.trim().replace(/\s+/g, ' ').slice(0, 400))}</div>` : '') + '</div>'
    : '';
  const newDisplay = !news || inUpdates ? ''
    : news.kind === 'given'
      ? `<span class="task-new${readCls}" title="${esc(`${news.by} gave you this task on ${shortDate(news.at.slice(0, 10))}`)}">new</span>`
      : `<span class="task-new task-update-mark${readCls}" title="${esc(`${shortDate(news.at.slice(0, 10))} ${news.at.slice(11)}${unread ? ': open the task to mark it read' : ''}`)}">${esc(news.by)} ${NEWS[news.kind]}</span>`;
  const readBtn = st.activeView === 'updates' && unread && app.appEl.dataset.writes === '1'
    ? `<button type="button" class="task-read-btn" data-id="${esc(task.id)}" title="Mark as read, without opening it">Mark read</button>` : '';
  // By person the section says who has it (not of a former assignee, whose
  // task is under Unassigned).
  const sectionSays = st.activeView === 'plan' && st.cut === 'person' && !formerAssignee;
  // In My tasks every shared task is yours: its card doesn't say so.
  const assigneeDisplay = !assignee || sectionSays || (app.initMine && assignee === people.me) ? newDisplay
    : `${newDisplay}<span class="task-assignee${assignee === people.me ? ' task-assignee-me' : ''}${formerAssignee ? ' task-assignee-former' : ''}" title="${
        esc(leftList ? `Assigned to ${assignee}, who doesn’t see this list anymore: it counts as no one’s`
          : formerAssignee ? `Was assigned to ${assignee}, who can no longer be given tasks`
          : assignee === people.me ? 'Assigned to you' : `Assigned to ${assignee}`)}">@${esc(assignee)}</span>`;
  const tagsRight = tags || assigneeDisplay || readBtn ? `<span class="task-tags-right">${tags}${assigneeDisplay}${readBtn}</span>` : '';
  const privateDisplay = task.private
    ? '<span class="task-private" title="Private: only you see it" aria-label="Private">🔒</span>' : '';
  const hasRealMeta = !!(tags || assigneeDisplay || task.due_date || task.start_date || task.recurrence ||
    task.keep || task.private || task.closed_at || commentsDisplay || notesDisplay);
  const hasMeta = !task.closed_at || dueDisplay || startDisplay || closedDisplay || recIcon || privateDisplay || tagsRight || notesDisplay;
  const metaClass = (hasMeta && !hasRealMeta) ? 'task-meta task-meta--hover-only' : 'task-meta';
  const metaHtml = hasMeta ? `<div class="${metaClass}">${tagsRight}<span class="task-meta-right">${skipBtnHtml}${privateDisplay}${recIcon}${notesDisplay}${commentsDisplay}${lastDoneDisplay}${dueDisplay}${startDisplay}${closedDisplay}</span></div>` : '';
  // A card's details (its description, its latest comments) are in it, shown
  // when it is opened. Their HTML is the server's (htmlOf), and in a long
  // list it isn't fetched for every task up front (taskdata.js lazyTexts):
  // a card whose texts aren't rendered yet has their places empty and marked
  // (data-lazy, not shown), and opening its details fetches them and draws
  // the list again (taskview.js setExpanded), so no card shows plain text.
  const detailsShown = task.id === st.expandedId && st.detailsOpen;
  const latest = comments.slice(-3).reverse();
  const have = (kind, text) => renderedHtml[kind].has(text);
  const descReady = !task.description || have('notes', task.description);
  const commentsReady = latest.every(c => have('comments', c.text));
  const lazy = ready => ready ? '' : ' data-lazy';
  const descHtml = task.description
    ? `<div class="task-desc page-content" data-rendered${lazy(descReady)}>${descReady || detailsShown ? htmlOf('notes', task.description) : ''}</div>` : '';
  // The latest comments, newest first: shown only while the task's details
  // are open (the line has the 💬 count; the modal has them all).
  const earlier = comments.length - 3;
  const commentsHtml = comments.length
    ? `<div class="task-card-comments"${lazy(commentsReady)}><div class="task-card-comments-label">Comments</div>${
        commentsReady || detailsShown ? latest.map(c => commentCardHtml(commentStamp(c), htmlOf('comments', c.text))).join('') : ''}` +
      (earlier > 0 ? `<button type="button" class="task-comments-more" data-id="${esc(task.id)}">${earlier} earlier…</button>` : '') + '</div>'
    : '';
  const dragAttr = draggable ? 'draggable="true" data-drag' : '';
  // Lines with details get the expand button.
  const expandBtn = descHtml || commentsHtml
    ? `<button type="button" class="task-expand-btn" data-id="${esc(task.id)}" aria-expanded="${detailsShown}" aria-label="Description and comments" title="Description and comments (l / h)">${app.EXPAND_ICON}</button>` : '';
  return `
    <li class="task-item ${closedClass} ${urgClass}${plannedClass} ${extraClass}${task.id === st.expandedId ? ' task-expanded' + (st.detailsOpen ? ' task-details' : '') : ''}" data-id="${esc(task.id)}" ${dragAttr}>
      <label class="task-checkbox">
        <input type="checkbox" ${checked} data-id="${esc(task.id)}">
      </label>
      <div class="task-content" data-id="${esc(task.id)}">
        ${importanceMark(task)}<span class="task-title" data-id="${esc(task.id)}">${linkifyTitle(task.title)}</span>
      </div>
      <div class="task-actions">
        ${expandBtn}<button type="button" class="task-menu-btn" data-id="${esc(task.id)}" aria-label="More actions" aria-haspopup="menu" title="More actions">${app.MORE_ICON}</button>
      </div>
      ${metaHtml}${newsHtml}${descHtml}${commentsHtml}
    </li>`;
}

// A long section draws its first DRAW cards and a row that shows the rest
// (st.drawAll: the sections asked for whole; st.mustDraw: tasks that have to
// be seen, a link's, a new one, which their section then shows whole): All
// tasks of a large wiki is thousands of cards, and a list is read from its
// top. The filter still searches every task, and counts are of all.
const DRAW = 200;
function windowed(key, tasks, draw = DRAW) {
  if (tasks.length <= draw || st.drawAll.has(key) || tasks.slice(draw).some(t => st.mustDraw.has(t.id))) return [tasks, ''];
  return [tasks.slice(0, draw), `<li class="tasks-show-more tasks-draw-more"><button type="button" class="btn" ` +
    `data-draw-more="${esc(key)}">Show ${tasks.length - draw} more</button></li>`];
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
    const count = live(s.tasks).length;
    const over = app.LIMIT && s.date && count >= app.LIMIT;
    const overCls = over ? ' board-day--over' : '';
    const overIcon = over
      ? `<span class="board-day-over-icon" title="Over the limit (${count}/${app.LIMIT})">⚠</span>`
      : '';
    const dropAttr = (s.droppable ? ` data-drop-bucket="${esc(s.key)}" data-drop-day="${s.date || ''}"` +
      (s.person !== undefined ? ` data-drop-person="${esc(s.person)}"` : '') +
      (s.list !== undefined ? ` data-drop-list="${esc(s.list)}"` : '') : '') +
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
    const dueBtn = listBtn(s.carried, 'carried', 'Carried over, on Today since an earlier day', ' board-day-carried') +
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
      `${overIcon}<span class="board-day-count">${count}</span></li>`;
    // An empty section is just its header row, which is itself the drop
    // target (styled tall enough to hit). Nothing grows when a drag
    // starts, so the section you aim at never moves under the pointer.
    // A section's tasks are its share of the manual order (where it is
    // `ordered`), placed among each other as on Open.
    // A carried-over card's "since" badge is amber here (task-carried):
    // on Today it is a slipped plan, not just an available task.
    const [shown, more] = isFolded ? [[], ''] : windowed('plan:' + s.key, s.tasks);
    const body = shown.map(t =>
      taskHTML(t, true, 'board-item' + (s.date && TasksCore.isCarried(t, today) ? ' task-carried' : ''))).join('') + more;
    return header + body;
  }).join('');
}

// Focus, Open and Closed: the list's HTML.
function listHTML(orderedTasks) {
  let html = '';
  let truncated = false;
  if (st.today && st.activeView === 'open') {
    const inFocus = live(orderedTasks).length;
    if (app.LIMIT && inFocus >= app.LIMIT) {
      html += `<li class="today-limit-bar today-over-limit">` +
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
  // The Closed view shows the page the server answered (js/taskdata.js
  // loadClosed), and says how many more there are.
  let displayTasks = orderedTasks;
  const moreClosed = st.activeView === 'closed' ? Math.max(0, (st.closedTotal || 0) - orderedTasks.length) : 0;
  if (st.activeView === 'closed' && moreClosed > 0 && st.closedLoaded) truncated = true;
  // Open sorted: by deadline the tasks with one, then those without, each
  // under a heading; newest first under one. Not drop targets: nothing
  // drags here.
  if (st.activeView === 'open' && app.activeSort()) {
    const heading = (label, tasks) => tasks.length ? `<li class="plan-subdivider sort-heading">` +
      `<span>${label}</span><span class="board-day-count">${live(tasks).length}</span></li>` : '';
    const group = (label, tasks) => {
      const [shown, more] = windowed('sort:' + label, tasks);
      return heading(label, tasks) + shown.map(t => taskHTML(t, false)).join('') + more;
    };
    html += app.activeSort() === 'due'
      ? group('By deadline', displayTasks.filter(t => t.due_date)) + group('No deadline', displayTasks.filter(t => !t.due_date))
      : group('Newest first', displayTasks);
    displayTasks = [];
  }
  // Open and Focus: the manual order, one list, a new task on top.
  else if (app.ORDERED[st.activeView]) {
    const [shown, more] = windowed(st.activeView + ':list', displayTasks);
    html += shown.map(t => taskHTML(t)).join('') + more;
    displayTasks = [];
  }
  // Updates: in no order of the user's, so nothing is dragged or ranked; a
  // task read since is dimmed (task-read).
  else if (st.activeView === 'updates') {
    const [shown, more] = windowed('updates', displayTasks);
    html += shown.map(t => taskHTML(t, false, 'task-update' + (TasksCore.newsFor(t, people.me) ? '' : ' task-read'))).join('') + more;
    displayTasks = [];
  }
  html += displayTasks.map(t => taskHTML(t)).join('');
  if (truncated) {
    html += `<li class="tasks-show-more"><button class="btn" id="tasks-show-more-btn">Show more (${moreClosed} remaining)</button></li>`;
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
  // 💬 toggles the line's details (the latest comments among them).
  app.listEl.querySelectorAll('.task-comments-btn').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      app.toggleDetails(btn.dataset.id);
    });
  });
  app.listEl.querySelectorAll('.task-expand-btn').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      app.toggleDetails(btn.dataset.id);
    });
  });
  // 📝 toggles the line's details.
  app.listEl.querySelectorAll('.task-has-notes').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      app.toggleDetails(btn.dataset.id);
    });
  });

  // "Show more" button
  app.listEl.querySelectorAll('[data-draw-more]').forEach(btn => btn.addEventListener('click', () => {
    st.drawAll.add(btn.dataset.drawMore);
    render();
  }));
  const showMoreBtn = app.listEl.querySelector('#tasks-show-more-btn');
  if (showMoreBtn) {
    showMoreBtn.addEventListener('click', () => {
      st.closedLimit += 50;
      app.closedStale();   // asks for the longer page
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
      } else if (st.ticked.has(id)) {
        app.untickTask(id);
      } else {
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
        if (field === 'start_date') app.startOn([id], d);
        else app.updateTask(id, { [field]: d });
      }, el, todayStr());
    });
  });

  app.listEl.querySelectorAll('.task-comments-more').forEach(btn => {
    btn.addEventListener('click', e => { e.stopPropagation(); app.openEditModal(btn.dataset.id, { focusComment: true }); });
  });

  // Updates' Mark read: the task's news seen, its card stays.
  app.listEl.querySelectorAll('.task-read-btn').forEach(btn => {
    btn.addEventListener('click', e => { e.stopPropagation(); app.markSeen(btn.dataset.id); });
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
  } else if (app.ORDERED[st.activeView] && !app.activeSort()) {
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
        // Selected now, expanded when the button comes up: a press that
        // starts a drag leaves the card a line (pressPending, released below).
        st.pressedCard = el;
        st.pressPending = id;
        try { app.setSelected(id); } finally { st.pressedCard = null; }
      }
    });
  });
}

// A press that ends without a drag (dragstart clears pressPending) expands
// the card it selected, as a selection does (expandSelected): the whole
// title, or its details while they are open on the way.
st.pressPending = null;
document.addEventListener('mouseup', () => {
  const id = st.pressPending;
  st.pressPending = null;
  if (!id || st.selectedIds.size !== 1 || st.selectedId !== id || id === st.expandedId) return;
  st.pressedCard = app.listEl?.querySelector(`.task-item[data-id="${CSS.escape(id)}"]`) || null;
  if (!st.pressedCard) return;
  try { app.expandSelected(id); } finally { st.pressedCard = null; }
}, true);

// The tasks still to do among `tasks`: what a section or a limit counts
// (not one ticked off that still shows).
const live = tasks => st.ticked.size ? tasks.filter(t => !st.ticked.has(t.id)) : tasks;

function render() {
  app.tickedHere();   // another tab, grouping, sort or filter: the ticked tasks go
  app.closedStale();   // the Closed view: another filter by now, asked for again
  app.applyGroup();   // who can be assigned may have arrived since
  app.syncClosedLink();
  syncReadAll();
  app.syncFilterShown();   // a phone shows the filter field while it filters
  let filtered = app.filterTasks();
  // Today as a board: the tasks to do now in their sections.
  if (st.today && st.activeView === 'plan') filtered = filtered.filter(t => TasksCore.isFocus(t, todayStr()));
  const orderedTasks = inViewOrder(filtered);
  const nothing = st.activeView === 'plan' ? st.today && !filtered.length : orderedTasks.length === 0;
  if (nothing) {
    renderEmpty();
    return;
  }
  app.listEl.innerHTML = st.activeView === 'plan' ? planHTML(filtered) : listHTML(orderedTasks);
  highlightIn(app.listEl);
  renderFigures(app.listEl);
  wireListHandlers();
}

// Updates' Mark all read, beside the page's heading (templates/tasks_page.html):
// there while a task has news, for who may write tasks.
const readAllBtn = app.initUpdates && app.appEl.dataset.writes === '1' ? document.getElementById('updates-read-btn') : null;
readAllBtn?.addEventListener('click', () => app.markAllSeen());
function syncReadAll() {
  if (readAllBtn) readAllBtn.hidden = !app.updatesNow().length;
}

return { htmlOf, render };
}
