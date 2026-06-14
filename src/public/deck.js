// ── Leaf Task App ────────────────────────────────────────────────────────────

(function () {
  'use strict';

  // Find all deck app instances on the page and initialize each
  const allApps = document.querySelectorAll('.tasks-app');
  if (!allApps.length) return;

  // Track which instance owns keyboard focus (only one at a time)
  let activeDeckInstance = null;

  allApps.forEach(function(appEl) {
    initDeckInstance(appEl);
  });
  // Default: first instance is active
  if (!activeDeckInstance && allApps.length === 1) {
    activeDeckInstance = allApps[0].dataset.instance || '0';
  }

  function initDeckInstance(appEl) {
  const sfx = appEl.dataset.instance && appEl.dataset.instance !== '0'
    ? '-' + appEl.dataset.instance : '';
  const instanceId = appEl.dataset.instance || '0';

  const listEl = document.getElementById('tasks-list' + sfx);
  const tabsEl = document.getElementById('tasks-tabs' + sfx);
  const addBtn = document.getElementById('task-add-btn' + sfx);
  const sortSelect = document.getElementById('task-sort' + sfx);
  const filterTag = document.getElementById('task-filter-tag' + sfx);
  const filterSearch = document.getElementById('task-filter-search' + sfx);
  const modal = document.getElementById('task-edit-modal' + sfx);
  const form = document.getElementById('task-edit-form' + sfx);
  const cancelBtn = document.getElementById('task-edit-cancel' + sfx);
  const deleteBtn = document.getElementById('task-edit-delete' + sfx);
  const recTypeSelect = document.getElementById('task-edit-rec-type' + sfx);
  const recIntervalField = document.getElementById('task-rec-interval-field' + sfx);
  const deleteModal = document.getElementById('task-delete-modal' + sfx);
  const deleteConfirmBtn = document.getElementById('task-delete-confirm' + sfx);
  const deleteCancelBtn = document.getElementById('task-delete-cancel' + sfx);
  const deleteTitleEl = document.getElementById('task-delete-title' + sfx);

  if (!listEl) return;

  let tasks = [];
  let activeFilter = 'all';
  let draggedId = null;
  let selectedId  = null;
  let selectedIds = new Set();
  let anchorId    = null;
  let completedLimit = 50;
  let undoTimer  = null;
  let undoToastEl = null;
  let pendingUndoFn = null;

  // Claim keyboard focus when user interacts with this instance
  appEl.addEventListener('pointerdown', () => { activeDeckInstance = instanceId; });
  appEl.addEventListener('focusin', () => { activeDeckInstance = instanceId; });

  const urlParams = new URLSearchParams(window.location.search);
  const initTag = appEl?.dataset.initTag || urlParams.get('tag') || '';
  const initPage = appEl?.dataset.initPage || urlParams.get('page') || '';
  const initFilter = appEl?.dataset.initFilter || urlParams.get('filter') || '';
  const initNew = appEl?.dataset.initNew === '1' || urlParams.has('new');

  // ── Undo toast ────────────────────────────────────────────────────────────

  function dismissUndo() {
    if (undoTimer) { clearTimeout(undoTimer); undoTimer = null; }
    if (undoToastEl) { undoToastEl.remove(); undoToastEl = null; }
    pendingUndoFn = null;
  }

  function showUndo(msg, undoFn) {
    dismissUndo();
    pendingUndoFn = undoFn;
    const el = document.createElement('div');
    el.className = 'raw-toast task-undo-toast';
    const span = document.createElement('span');
    span.textContent = msg;
    const btn = document.createElement('button');
    btn.className = 'btn task-undo-btn';
    btn.textContent = 'Undo';
    btn.addEventListener('click', () => { const fn = pendingUndoFn; dismissUndo(); fn?.(); });
    el.appendChild(span);
    el.appendChild(btn);
    document.body.appendChild(el);
    undoToastEl = el;
    undoTimer = setTimeout(dismissUndo, 6000);
  }

  function triggerUndo() {
    if (!pendingUndoFn) return;
    const fn = pendingUndoFn;
    dismissUndo();
    fn();
  }

  // ── API ────────────────────────────────────────────────────────────────────

  async function fetchDeck() {
    const res = await fetch('/api/deck');
    tasks = await res.json();
    render();
  }

  async function createTask(data) {
    const res = await fetch('/api/deck', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (res.ok) {
      const task = await res.json();
      tasks.push(task);
      render();
      window.refreshDueBadge?.();
      return task;
    }
    return null;
  }

  async function updateTask(id, data) {
    const res = await fetch(`/api/deck/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (res.ok) {
      const updated = await res.json();
      const idx = tasks.findIndex(t => t.id === id);
      if (idx !== -1) tasks[idx] = updated;
      // If recurring spawned a new task, re-fetch
      if (data.completed && tasks[idx]?.recurrence) {
        await fetchDeck();
      } else {
        render();
      }
      window.refreshDueBadge?.();
      return updated;
    }
    return null;
  }

  async function deleteTask(id) {
    const res = await fetch(`/api/deck/${id}`, { method: 'DELETE' });
    if (res.ok) {
      tasks = tasks.filter(t => t.id !== id);
      render();
      window.refreshDueBadge?.();
    }
  }

  async function deleteTasks(ids) {
    const res = await fetch('/api/deck/batch-delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids }),
    });
    if (res.ok) {
      const idSet = new Set(ids);
      tasks = tasks.filter(t => !idSet.has(t.id));
      render();
      window.refreshDueBadge?.();
    }
  }

  async function completeTasks(ids, completed) {
    const res = await fetch('/api/deck/batch-complete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids, completed }),
    });
    if (res.ok) {
      // Re-fetch to capture any spawned recurring tasks
      await fetchDeck();
      window.refreshDueBadge?.();
    }
  }

  async function reorder(order) {
    await fetch('/api/deck/reorder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ order }),
    });
  }

  // ── Delete confirmation dialog ─────────────────────────────────────────────

  deleteCancelBtn?.addEventListener('click', () => deleteModal.close());

  function confirmDelete(title, count = 1) {
    return new Promise(resolve => {
      const labelEl = deleteModal.querySelector('.scratch-label');
      if (labelEl) labelEl.textContent = count > 1 ? `Delete ${count} tasks?` : 'Delete task?';
      deleteTitleEl.textContent = count > 1 ? '' : title;
      deleteModal.showModal();
      let resolved = false;
      const finish = ok => {
        if (resolved) return;
        resolved = true;
        deleteConfirmBtn.removeEventListener('click', onOk);
        deleteCancelBtn.removeEventListener('click', onCancel);
        deleteModal.removeEventListener('close', onClose);
        deleteModal.close();
        resolve(ok);
      };
      const onOk     = () => finish(true);
      const onCancel = () => finish(false);
      const onClose  = () => finish(false);
      deleteConfirmBtn.addEventListener('click', onOk);
      deleteCancelBtn.addEventListener('click', onCancel);
      deleteModal.addEventListener('close', onClose, { once: true });
    });
  }

  // ── Filtering ──────────────────────────────────────────────────────────────

  function todayStr() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  }
  function dateToStr(d) {
    return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  }

  function filterTasks() {
    const today = todayStr();
    let filtered = tasks;

    // Text search filter (title + description)
    const searchTerm = (filterSearch?.value || '').trim().toLowerCase();
    if (searchTerm) {
      filtered = filtered.filter(t =>
        t.title.toLowerCase().includes(searchTerm) ||
        (t.description || '').toLowerCase().includes(searchTerm)
      );
    }

    // Hide tasks that are deferred (absolute start_date or relative defer_days)
    // Task is visible when: today >= start_date AND today >= due_date - defer_days
    // Both conditions must pass (latest/most-restrictive wins)
    if (activeFilter !== 'completed' && activeFilter !== 'hidden') {
      filtered = filtered.filter(t => {
        if (t.completed_at) return true;
        let visible = true;
        if (t.start_date && t.start_date > today) visible = false;
        if (t.defer_days != null && t.due_date) {
          const due = new Date(t.due_date + 'T00:00:00');
          const showFrom = new Date(due - t.defer_days * 86400000);
          const showStr = dateToStr(showFrom);
          if (showStr > today) visible = false;
        }
        return visible;
      });
    }

    // Tab filter
    if (activeFilter === 'due') {
      filtered = filtered.filter(t => !t.completed_at && t.due_date);
    } else if (activeFilter === 'completed') {
      filtered = filtered.filter(t => !!t.completed_at);
    } else if (activeFilter === 'hidden') {
      filtered = filtered.filter(t => {
        if (t.completed_at) return false;
        if (t.start_date && t.start_date > today) return true;
        if (t.defer_days != null && t.due_date) {
          const due = new Date(t.due_date + 'T00:00:00');
          const showFrom = new Date(due - t.defer_days * 86400000);
          if (dateToStr(showFrom) > today) return true;
        }
        return false;
      });
    } else {
      // "all" shows non-completed by default
      filtered = filtered.filter(t => !t.completed_at);
    }

    // Tag filter — comma-separated, all specified tags must match (AND)
    const tagTerms = filterTag.value.split(',').map(s => s.trim().toLowerCase()).filter(Boolean);
    if (tagTerms.length) {
      filtered = filtered.filter(t =>
        tagTerms.every(term => t.tags && t.tags.some(tg => tg.toLowerCase() === term))
      );
    }

    // Page filter (set via URL param, stored in initPage)
    if (initPage) {
      filtered = filtered.filter(t => t.page === initPage);
    }

    return filtered;
  }

  // ── Sorting ────────────────────────────────────────────────────────────────

  // Per-tab fixed sort comparators; null = use the user's sort select
  const TAB_SORT = {
    due:       (a, b) => (a.due_date || '').localeCompare(b.due_date || ''),
    completed: (a, b) => (b.completed_at || '').localeCompare(a.completed_at || ''),
    hidden: (a, b) => (a.start_date || '').localeCompare(b.start_date || ''),
  };

  function sortTasks(tasksArr) {
    const fixed = TAB_SORT[activeFilter];
    if (fixed) return [...tasksArr].sort(fixed);

    const mode = sortSelect.value;
    const sorted = [...tasksArr];
    if (mode === 'manual') {
      sorted.sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0));
    } else if (mode === 'due_date') {
      sorted.sort((a, b) => {
        if (!a.due_date && !b.due_date) return 0;
        if (!a.due_date) return 1;
        if (!b.due_date) return -1;
        return a.due_date.localeCompare(b.due_date);
      });
    } else if (mode === 'created_at') {
      sorted.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
    }
    return sorted;
  }

  // ── Bulk tag modal ──────────────────────────────────────────────────────────

  function openBulkTagModal(ids, removing) {
    const count = ids.length;
    const label = removing
      ? `Remove tag from ${count} item${count > 1 ? 's' : ''}`
      : `Add tag to ${count} item${count > 1 ? 's' : ''}`;

    // Create a minimal inline dialog
    const dlg = document.createElement('dialog');
    dlg.className = 'scratch-modal';
    dlg.innerHTML = `
      <p class="scratch-label">${label}</p>
      <div class="task-field" style="position:relative">
        <input type="text" id="bulk-tag-input" autocomplete="off" spellcheck="false" placeholder="tag name…">
      </div>
      <div class="scratch-actions">
        <button type="button" class="btn btn-primary" id="bulk-tag-ok">${removing ? 'Remove' : 'Add'}</button>
        <button type="button" class="btn" id="bulk-tag-cancel">Cancel</button>
      </div>`;
    document.body.appendChild(dlg);

    const input = dlg.querySelector('#bulk-tag-input');
    const okBtn = dlg.querySelector('#bulk-tag-ok');
    const cancelBtn = dlg.querySelector('#bulk-tag-cancel');
    let acEl = null;
    let acIndex = -1;

    function hideAc() { acEl?.remove(); acEl = null; acIndex = -1; }

    function showAc(matches) {
      hideAc();
      if (!matches.length) return;
      acEl = document.createElement('div');
      acEl.className = 'tag-ac-dropdown';
      acEl.style.position = 'absolute';
      acEl.style.top = input.offsetTop + input.offsetHeight + 'px';
      acEl.style.left = '0';
      acEl.style.width = input.offsetWidth + 'px';
      matches.forEach(tag => {
        const item = document.createElement('div');
        item.className = 'tag-ac-item';
        item.textContent = '#' + tag;
        item.addEventListener('mousedown', e => {
          e.preventDefault();
          input.value = tag;
          hideAc();
        });
        acEl.appendChild(item);
      });
      input.parentElement.appendChild(acEl);
    }

    input.addEventListener('input', () => {
      const partial = input.value.trim();
      if (!partial) { hideAc(); return; }
      const allTags = [...new Set(tasks.flatMap(t => t.tags || []))].sort();
      const matches = allTags
        .map(tag => ({ tag, score: fuzzyScore(partial, tag) }))
        .filter(({ score }) => score >= 0)
        .sort((a, b) => b.score - a.score || a.tag.localeCompare(b.tag))
        .map(({ tag }) => tag)
        .slice(0, 8);
      showAc(matches);
    });

    input.addEventListener('keydown', ev => {
      if (acEl) {
        const items = acEl.querySelectorAll('.tag-ac-item');
        if (ev.key === 'ArrowDown') {
          ev.preventDefault();
          acIndex = (acIndex + 1) % items.length;
          items.forEach((el, i) => el.classList.toggle('selected', i === acIndex));
          return;
        } else if (ev.key === 'ArrowUp') {
          ev.preventDefault();
          acIndex = acIndex <= 0 ? items.length - 1 : acIndex - 1;
          items.forEach((el, i) => el.classList.toggle('selected', i === acIndex));
          return;
        } else if ((ev.key === 'Tab') && acIndex >= 0) {
          ev.preventDefault();
          input.value = items[acIndex].textContent.replace(/^#/, '');
          hideAc();
          return;
        } else if (ev.key === 'Escape') {
          hideAc();
          return;
        }
      }
      if (ev.key === 'Enter') { ev.preventDefault(); ev.stopPropagation(); submit(); }
      if (ev.key === 'Escape') { ev.stopPropagation(); close(); }
    });

    async function submit() {
      const tag = input.value.trim();
      if (!tag) return;
      close();
      try {
        const body = removing ? { ids, remove: [tag] } : { ids, add: [tag] };
        const res = await fetch('/api/deck/batch-tag', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (res.ok) {
          const updated = await res.json();
          for (const u of updated) {
            const idx = tasks.findIndex(t => t.id === u.id);
            if (idx !== -1) tasks[idx] = u;
          }
          render();
        }
      } catch (_) {}
    }

    function close() {
      hideAc();
      dlg.close();
      dlg.remove();
    }

    okBtn.addEventListener('click', submit);
    cancelBtn.addEventListener('click', close);
    dlg.addEventListener('click', ev => { if (ev.target === dlg) close(); });

    dlg.showModal();
    input.focus();
  }

  // ── Keyboard selection ─────────────────────────────────────────────────────

  function updateSelectionClasses() {
    listEl.querySelectorAll('.task-item').forEach(el => {
      el.classList.toggle('task-selected', selectedIds.has(el.dataset.id));
    });
  }

  function setSelected(id) {
    selectedId = id;
    anchorId   = id;
    selectedIds = new Set(id ? [id] : []);
    updateSelectionClasses();
    if (id) {
      const el = listEl.querySelector(`.task-item[data-id="${CSS.escape(id)}"]`);
      if (el) el.scrollIntoView({ block: 'nearest' });
    }
  }

  function extendSelection(id) {
    if (!id) return;
    selectedId = id;
    const items = visibleItems();
    const aIdx = anchorId ? items.findIndex(el => el.dataset.id === anchorId) : 0;
    const bIdx = items.findIndex(el => el.dataset.id === id);
    if (bIdx === -1) return;
    const [lo, hi] = aIdx <= bIdx ? [aIdx, bIdx] : [bIdx, aIdx];
    selectedIds = new Set(items.slice(lo, hi + 1).map(el => el.dataset.id));
    updateSelectionClasses();
    const el = listEl.querySelector(`.task-item[data-id="${CSS.escape(id)}"]`);
    if (el) el.scrollIntoView({ block: 'nearest' });
  }

  function visibleItems() {
    return Array.from(listEl.querySelectorAll('.task-item'));
  }

  // ── Rendering ──────────────────────────────────────────────────────────────

  function startInlineEdit(el) {
    const id = el.dataset.id;
    const task = tasks.find(t => t.id === id);
    if (!task) return;
    // Prevent double-activation
    if (el.querySelector('input')) return;

    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'task-inline-input';
    input.value = task.title;
    el.textContent = '';
    el.appendChild(input);
    input.focus();
    input.select();

    const commit = () => {
      const newTitle = input.value.trim();
      if (newTitle && newTitle !== task.title) {
        updateTask(id, { title: newTitle });
      } else {
        render();
      }
    };

    input.addEventListener('blur', commit);
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); input.removeEventListener('blur', commit); commit(); }
      if (e.key === 'Escape') { input.removeEventListener('blur', commit); render(); }
    });
  }

  function formatDate(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr + 'T00:00:00');
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const diff = Math.round((d - today) / 86400000);
    if (diff === 0) return 'Today';
    if (diff === 1) return 'Tomorrow';
    if (diff === -1) return 'Yesterday';
    return dateStr;
  }

  function dueDateClass(task) {
    if (!task.due_date || task.completed_at) return '';
    const today = todayStr();
    if (task.due_date < today) return 'task-overdue';
    if (task.due_date === today) return 'task-due-today';
    return '';
  }

  function urgencyClass(task) {
    if (!task.due_date || task.completed_at) return '';
    const diff = Math.round(
      (new Date(task.due_date + 'T00:00:00') - new Date(todayStr() + 'T00:00:00')) / 86400000
    );
    if (diff < 0)  return 'task-urg-overdue';
    if (diff === 0) return 'task-urg-today';
    if (diff <= 2)  return 'task-urg-soon';
    if (diff <= 7)  return 'task-urg-upcoming';
    return '';
  }

  function render() {
    sortSelect.style.display = TAB_SORT[activeFilter] ? 'none' : '';

    const filtered = filterTasks();

    // In "all" view split into ordered (sort_order set) and new (sort_order null)
    let orderedTasks, newTasks;
    if (activeFilter === 'all') {
      orderedTasks = sortTasks(filtered.filter(t => t.sort_order != null));
      newTasks   = filtered.filter(t => t.sort_order == null)
                             .sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
    } else {
      orderedTasks = sortTasks(filtered);
      newTasks   = [];
    }

    if (orderedTasks.length === 0 && newTasks.length === 0) {
      listEl.innerHTML = '<li class="tasks-empty">No tasks here.</li>';
      return;
    }

    const taskHTML = task => {
      const checked = task.completed_at ? 'checked' : '';
      const completedClass = task.completed_at ? 'task-completed' : '';
      const dueClass = dueDateClass(task);
      const urgClass = urgencyClass(task);
      const tags = (task.tags || []).map(t => `<span class="task-tag">#${esc(t)}</span>`).join('');
      const recIcon = task.recurrence ? '<span class="task-recurring" title="Recurring">↻</span>' : '';
      const dueDisplay = task.due_date ? `<span class="task-due ${dueClass}">due: ${formatDate(task.due_date)}</span>` : '';
      const startDisplay = task.start_date ? `<span class="task-date-info">hidden until ${formatDate(task.start_date)}</span>` : '';
      const deferDaysDisplay = task.defer_days != null ? `<span class="task-date-info">${task.defer_days === 0 ? 'hidden until due' : `hidden until ${task.defer_days}d before due`}</span>` : '';
      const completedDisplay = task.completed_at ? `<span class="task-date-info">done: ${task.completed_at.slice(0, 10)}</span>` : '';
      const pageDisplay = task.page ? `<a href="/page/${esc(task.page)}" class="task-page-link">📄 ${esc(task.page)}</a>` : '';

      // Blocked by: show full title, click to open modal
      let blockedDisplay = '';
      if (task.blocked_by && task.blocked_by.length) {
        const links = task.blocked_by.map(bid => {
          const blocker = tasks.find(t => t.id === bid);
          const title = blocker ? esc(blocker.title) : bid.slice(0, 6);
          return `<a href="#" class="dep-link" data-id="${bid}">${title}</a>`;
        }).join(' ');
        blockedDisplay = `<span class="task-blocked">🚫 blocked by ${links}</span>`;
      }

      // Blocking: show full title, click to open modal
      let blockingDisplay = '';
      const blocking = tasks.filter(t => t.blocked_by && t.blocked_by.includes(task.id) && !t.completed_at);
      if (blocking.length) {
        const links = blocking.map(t => {
          return `<a href="#" class="dep-link" data-id="${t.id}">${esc(t.title)}</a>`;
        }).join(' ');
        blockingDisplay = `<span class="task-blocking">⛓ blocking ${links}</span>`;
      }

      const hasMeta = dueDisplay || startDisplay || deferDaysDisplay || completedDisplay || recIcon || tags || pageDisplay || blockedDisplay || blockingDisplay;
      const metaHtml = hasMeta ? `<div class="task-meta">${startDisplay}${deferDaysDisplay}${dueDisplay}${completedDisplay}${recIcon}${tags}${pageDisplay}${blockedDisplay}${blockingDisplay}</div>` : '';
      const dragAttr = sortSelect.value === 'manual' ? 'draggable="true"' : '';
      return `
        <li class="task-item ${completedClass} ${urgClass}" data-id="${task.id}" ${dragAttr}>
          <label class="task-checkbox">
            <input type="checkbox" ${checked} data-id="${task.id}">
          </label>
          <div class="task-content" data-id="${task.id}">
            <span class="task-title" data-id="${task.id}">${esc(task.title)}</span>
            ${metaHtml}
            ${task.description ? `<div class="task-desc">${renderDesc(task.description)}</div>` : ''}
          </div>
          <button class="task-edit-btn" data-id="${task.id}" title="Edit">✎</button>
        </li>`;
    };

    let html = '';
    let truncated = false;
    if (newTasks.length > 0) {
      html += '<li class="task-inbox-divider task-inbox-divider--only">New</li>';
      html += newTasks.map(taskHTML).join('');
      if (orderedTasks.length > 0) html += '<li class="task-inbox-divider"></li>';
    }
    // Limit completed tab display
    let displayTasks = orderedTasks;
    if (activeFilter === 'completed' && orderedTasks.length > completedLimit) {
      displayTasks = orderedTasks.slice(0, completedLimit);
      truncated = true;
    }
    html += displayTasks.map(taskHTML).join('');
    if (truncated) {
      html += `<li class="tasks-show-more"><button class="btn" id="tasks-show-more-btn">Show more (${orderedTasks.length - completedLimit} remaining)</button></li>`;
    }
    listEl.innerHTML = html;

    // "Show more" button
    const showMoreBtn = listEl.querySelector('#tasks-show-more-btn');
    if (showMoreBtn) {
      showMoreBtn.addEventListener('click', () => {
        completedLimit += 50;
        render();
      });
    }

    // Attach event listeners
    listEl.querySelectorAll('input[type=checkbox]').forEach(cb => {
      cb.addEventListener('change', e => {
        const id = e.target.dataset.id;
        const completed = e.target.checked;
        const task = tasks.find(t => t.id === id);
        updateTask(id, { completed });
        if (task) {
          const label = completed ? 'completed' : 'reopened';
          showUndo(`Task ${label}`, () => completeTasks([id], !completed));
        }
      });
    });

    listEl.querySelectorAll('.task-edit-btn').forEach(btn => {
      btn.addEventListener('click', e => {
        openEditModal(e.currentTarget.dataset.id);
      });
    });

    // Dependency links open the linked task's edit modal
    listEl.querySelectorAll('.dep-link').forEach(link => {
      link.addEventListener('click', e => {
        e.preventDefault();
        e.stopPropagation();
        openEditModal(link.dataset.id);
      });
    });

    listEl.querySelectorAll('.task-title').forEach(el => {
      el.addEventListener('dblclick', e => {
        startInlineEdit(e.currentTarget);
      });
    });

    listEl.querySelectorAll('.task-content').forEach(el => {
      el.addEventListener('dblclick', e => {
        if (e.target.classList.contains('task-title')) return;
        openEditModal(e.currentTarget.dataset.id);
      });
    });

    // Drag and drop
    if (sortSelect.value === 'manual') {
      setupDragAndDrop();
    }

    // Restore or clear selection after re-render
    if (selectedIds.size) {
      const visSet = new Set(visibleItems().map(el => el.dataset.id));
      selectedIds = new Set([...selectedIds].filter(id => visSet.has(id)));
      if (!visSet.has(selectedId)) selectedId = selectedIds.size ? [...selectedIds][0] : null;
      if (!visSet.has(anchorId))   anchorId = selectedId;
      updateSelectionClasses();
      if (selectedId) {
        const el = listEl.querySelector(`.task-item[data-id="${CSS.escape(selectedId)}"]`);
        if (el) el.scrollIntoView({ block: 'nearest' });
      }
    }

    // Click to select (clicks on checkbox/button are handled separately)
    listEl.querySelectorAll('.task-item').forEach(el => {
      el.addEventListener('mousedown', e => {
        if (!e.target.closest('input, button')) setSelected(el.dataset.id);
      });
    });
  }

  function esc(str) {
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
  }

  // ── Inline markdown rendering for descriptions ─────────────────────────────

  function renderDesc(text) {
    if (!text) return '';
    let h = esc(text);
    // Wikilinks: [[page|label]] and [[page]]
    h = h.replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, (_, page, label) => {
      const slug = page.trim().toLowerCase().replace(/\s+/g, '-');
      return `<a href="/page/${slug}">${label}</a>`;
    });
    h = h.replace(/\[\[([^\]]+)\]\]/g, (_, page) => {
      const slug = page.trim().toLowerCase().replace(/\s+/g, '-');
      const label = page.trim();
      return `<a href="/page/${slug}">${esc(label)}</a>`;
    });
    // Markdown links: [label](url)
    h = h.replace(/\[([^\]]*)\]\(([^)]+)\)/g, '<a href="$2">$1</a>');
    // Bold
    h = h.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    // Italic
    h = h.replace(/\*(.+?)\*/g, '<em>$1</em>');
    // Inline code
    h = h.replace(/`([^`]+)`/g, '<code>$1</code>');
    // Line breaks
    h = h.replace(/\n/g, '<br>');
    return h;
  }

  // ── Wikilink autocomplete for notes textarea ───────────────────────────────

  let wikiPages = [];
  let noteAcEl = null;
  let noteAcIndex = 0;

  fetch('/api/pages').then(r => r.json()).then(p => { wikiPages = p; }).catch(() => {});

  function getNoteWikilinkQuery(ta) {
    const before = ta.value.slice(0, ta.selectionStart);
    const m = before.match(/\[\[([^\][\n]*)$/);
    return m ? m[1] : null;
  }

  function getNoteMdLinkQuery(ta) {
    const before = ta.value.slice(0, ta.selectionStart);
    const m = before.match(/\[[^\]]*\]\(([^()\n]*)$/);
    if (!m) return null;
    const typed = m[1];
    if (typed === '' || /^[/a-z0-9_-]/i.test(typed)) return typed;
    return null;
  }

  function showNoteAc(ta, matches, mode) {
    const container = ta.closest('dialog') || document.body;
    if (!noteAcEl) {
      noteAcEl = document.createElement('div');
      noteAcEl.className = 'wikilink-ac';
      container.appendChild(noteAcEl);
      noteAcIndex = 0;
    }
    noteAcEl.innerHTML = '';
    matches.forEach((page, i) => {
      const item = document.createElement('div');
      item.className = 'wikilink-ac-item' + (i === noteAcIndex ? ' selected' : '');
      item.textContent = page;
      item.addEventListener('mousedown', ev => {
        ev.preventDefault();
        insertNoteLink(ta, page, mode);
      });
      noteAcEl.appendChild(item);
    });
    if (noteAcIndex >= matches.length) noteAcIndex = 0;
    // Position relative to textarea within the dialog
    const taRect = ta.getBoundingClientRect();
    const containerRect = container.getBoundingClientRect();
    const coords = getNoteCaretCoords(ta);
    const lineH = parseFloat(window.getComputedStyle(ta).lineHeight) || 20;
    let top = taRect.top - containerRect.top + coords.top - ta.scrollTop + lineH;
    let left = taRect.left - containerRect.left + coords.left;
    noteAcEl.style.top = top + 'px';
    noteAcEl.style.left = left + 'px';
  }

  function hideNoteAc() {
    if (noteAcEl) { noteAcEl.remove(); noteAcEl = null; }
  }

  function noteAcMove(dir) {
    if (!noteAcEl) return;
    const items = noteAcEl.querySelectorAll('.wikilink-ac-item');
    items[noteAcIndex]?.classList.remove('selected');
    noteAcIndex = (noteAcIndex + dir + items.length) % items.length;
    items[noteAcIndex]?.classList.add('selected');
  }

  function insertNoteLink(ta, page, mode) {
    const pos = ta.selectionStart;
    const val = ta.value;
    if (mode === 'wikilink') {
      const bracketPos = val.lastIndexOf('[[', pos);
      ta.setSelectionRange(bracketPos, pos);
      document.execCommand('insertText', false, '[[' + page + ']]');
    } else {
      const parenPos = val.lastIndexOf('](', pos);
      ta.setSelectionRange(parenPos + 2, pos);
      document.execCommand('insertText', false, '/page/' + page + ')');
    }
    hideNoteAc();
  }

  function getNoteCaretCoords(el) {
    const computed = window.getComputedStyle(el);
    const mirror = document.createElement('div');
    Object.assign(mirror.style, {
      position: 'absolute', visibility: 'hidden', whiteSpace: 'pre-wrap',
      wordWrap: 'break-word', overflow: 'hidden',
      boxSizing: computed.boxSizing, width: computed.width,
      paddingTop: computed.paddingTop, paddingRight: computed.paddingRight,
      paddingBottom: computed.paddingBottom, paddingLeft: computed.paddingLeft,
      borderTopWidth: computed.borderTopWidth, borderLeftWidth: computed.borderLeftWidth,
      fontFamily: computed.fontFamily, fontSize: computed.fontSize,
      fontWeight: computed.fontWeight, lineHeight: computed.lineHeight,
      letterSpacing: computed.letterSpacing,
    });
    document.body.appendChild(mirror);
    mirror.textContent = el.value.slice(0, el.selectionStart);
    const span = document.createElement('span');
    span.textContent = '\u200b';
    mirror.appendChild(span);
    const coords = { top: span.offsetTop, left: span.offsetLeft };
    document.body.removeChild(mirror);
    return coords;
  }

  function setupNoteAc(ta) {
    ta.addEventListener('input', () => {
      const wq = getNoteWikilinkQuery(ta);
      if (wq !== null) {
        const q = wq.toLowerCase();
        const matches = wikiPages.filter(p => p.includes(q)).slice(0, 8);
        if (matches.length) { showNoteAc(ta, matches, 'wikilink'); return; }
      }
      const mq = getNoteMdLinkQuery(ta);
      if (mq !== null) {
        const q = mq.replace(/^\/page\//, '').toLowerCase();
        const matches = wikiPages.filter(p => p.includes(q)).slice(0, 8);
        if (matches.length) { showNoteAc(ta, matches, 'mdlink'); return; }
      }
      hideNoteAc();
    });
    ta.addEventListener('keydown', e => {
      if (!noteAcEl) return;
      if (e.key === 'ArrowDown') { e.preventDefault(); noteAcMove(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); noteAcMove(-1); }
      else if (e.key === 'Enter' || e.key === 'Tab') {
        const sel = noteAcEl.querySelector('.selected');
        if (sel) {
          e.preventDefault();
          const mode = getNoteWikilinkQuery(ta) !== null ? 'wikilink' : 'mdlink';
          insertNoteLink(ta, sel.textContent, mode);
        }
      } else if (e.key === 'Escape') { hideNoteAc(); }
    });
    ta.addEventListener('blur', () => setTimeout(hideNoteAc, 150));
  }

  // Attach autocomplete to title and notes fields
  const titleInput = document.getElementById('task-edit-title' + sfx);
  const descTextarea = document.getElementById('task-edit-desc' + sfx);
  if (titleInput) setupNoteAc(titleInput);
  if (descTextarea) setupNoteAc(descTextarea);

  // ── Tabs ───────────────────────────────────────────────────────────────────

  tabsEl.addEventListener('click', e => {
    const btn = e.target.closest('.tab');
    if (!btn) return;
    tabsEl.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
    activeFilter = btn.dataset.filter;
    render();
  });

  sortSelect.addEventListener('change', render);
  filterTag.addEventListener('input', render);
  if (filterSearch) filterSearch.addEventListener('input', render);

  // ── Tag autocomplete ───────────────────────────────────────────────────────

  let tagAcEl = null;
  let tagAcIndex = -1;

  function hideTagAc() {
    tagAcEl?.remove();
    tagAcEl = null;
    tagAcIndex = -1;
  }

  function fuzzyScore(q, tag) {
    const ql = q.toLowerCase(), tl = tag.toLowerCase();
    if (tl.startsWith(ql)) return 2;
    if (tl.includes(ql))   return 1;
    let qi = 0;
    for (let i = 0; i < tl.length && qi < ql.length; i++) {
      if (tl[i] === ql[qi]) qi++;
    }
    return qi === ql.length ? 0 : -1;
  }

  function selectTagAcItem(text) {
    const val = filterTag.value;
    const commaIdx = val.lastIndexOf(',');
    filterTag.value = (commaIdx >= 0 ? val.slice(0, commaIdx + 1) + ' ' : '') + text;
    render();
    filterTag.focus();
  }

  function showTagAc(matches) {
    hideTagAc();
    if (!matches.length) return;
    tagAcEl = document.createElement('div');
    tagAcEl.className = 'tag-ac-dropdown';
    matches.forEach((tag, i) => {
      const item = document.createElement('div');
      item.className = 'tag-ac-item';
      item.textContent = '#' + tag;
      item.addEventListener('mousedown', e => { e.preventDefault(); selectTagAcItem(tag); hideTagAc(); });
      tagAcEl.appendChild(item);
    });
    const rect = filterTag.getBoundingClientRect();
    tagAcEl.style.top  = (rect.bottom + window.scrollY) + 'px';
    tagAcEl.style.left = rect.left + 'px';
    tagAcEl.style.width = rect.width + 'px';
    document.body.appendChild(tagAcEl);
  }

  function updateTagAcHighlight() {
    if (!tagAcEl) return;
    tagAcEl.querySelectorAll('.tag-ac-item').forEach((el, i) => {
      el.classList.toggle('selected', i === tagAcIndex);
    });
  }

  filterTag.addEventListener('keydown', e => {
    if (!tagAcEl) return;
    const items = tagAcEl.querySelectorAll('.tag-ac-item');
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      tagAcIndex = (tagAcIndex + 1) % items.length;
      updateTagAcHighlight();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      tagAcIndex = tagAcIndex <= 0 ? items.length - 1 : tagAcIndex - 1;
      updateTagAcHighlight();
    } else if ((e.key === 'Enter' || e.key === 'Tab') && tagAcIndex >= 0) {
      e.preventDefault();
      selectTagAcItem(items[tagAcIndex].textContent.replace(/^#/, ''));
      hideTagAc();
    } else if (e.key === 'Escape') {
      hideTagAc();
    }
  });

  function computeTagMatches() {
    const val = filterTag.value;
    const commaIdx = val.lastIndexOf(',');
    const partial = (commaIdx >= 0 ? val.slice(commaIdx + 1) : val).trim().replace(/^#/, '');

    // Collect tags only from tasks visible under the current tab + committed tag filters.
    const committedVal = commaIdx >= 0 ? val.slice(0, commaIdx) : '';
    const origVal = filterTag.value;
    filterTag.value = committedVal;
    const visibleTasks = filterTasks();
    filterTag.value = origVal;

    const committed = committedVal.split(',').map(s => s.trim().toLowerCase()).filter(Boolean);
    const available = [...new Set(visibleTasks.flatMap(t => t.tags || []))].sort();

    if (!partial) {
      return available.filter(tag => !committed.includes(tag.toLowerCase())).slice(0, 12);
    }
    return available
      .map(tag => ({ tag, score: fuzzyScore(partial, tag) }))
      .filter(({ tag, score }) => score >= 0 && !committed.includes(tag.toLowerCase()))
      .sort((a, b) => b.score - a.score || a.tag.localeCompare(b.tag))
      .map(({ tag }) => tag)
      .slice(0, 8);
  }

  filterTag.addEventListener('input', () => {
    const val = filterTag.value;
    const commaIdx = val.lastIndexOf(',');
    const partial = (commaIdx >= 0 ? val.slice(commaIdx + 1) : val).trim();
    if (partial.startsWith('#')) {
      showTagAc(computeTagMatches());
    } else {
      hideTagAc();
    }
  });

  filterTag.addEventListener('blur', () => setTimeout(hideTagAc, 150));

  // ── Edit modal tag autocomplete ─────────────────────────────────────────────

  const editTagsInput = document.getElementById('task-edit-tags' + sfx);
  let editTagAcEl = null;
  let editTagAcIndex = -1;

  function hideEditTagAc() {
    editTagAcEl?.remove();
    editTagAcEl = null;
    editTagAcIndex = -1;
  }

  function selectEditTagAcItem(text) {
    const val = editTagsInput.value;
    const commaIdx = val.lastIndexOf(',');
    editTagsInput.value = (commaIdx >= 0 ? val.slice(0, commaIdx + 1) + ' ' : '') + text;
    editTagsInput.focus();
  }

  function showEditTagAc(matches) {
    hideEditTagAc();
    if (!matches.length) return;
    editTagAcEl = document.createElement('div');
    editTagAcEl.className = 'tag-ac-dropdown';
    matches.forEach((tag) => {
      const item = document.createElement('div');
      item.className = 'tag-ac-item';
      item.textContent = '#' + tag;
      item.addEventListener('mousedown', e => { e.preventDefault(); selectEditTagAcItem(tag); hideEditTagAc(); });
      editTagAcEl.appendChild(item);
    });
    // Position relative to the input's parent (inside the dialog)
    const parent = editTagsInput.parentElement;
    parent.style.position = 'relative';
    editTagAcEl.style.top = editTagsInput.offsetTop + editTagsInput.offsetHeight + 'px';
    editTagAcEl.style.left = '0';
    editTagAcEl.style.width = editTagsInput.offsetWidth + 'px';
    parent.appendChild(editTagAcEl);
  }

  if (editTagsInput) {
    editTagsInput.addEventListener('keydown', e => {
      if (!editTagAcEl) return;
      const items = editTagAcEl.querySelectorAll('.tag-ac-item');
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        editTagAcIndex = (editTagAcIndex + 1) % items.length;
        items.forEach((el, i) => el.classList.toggle('selected', i === editTagAcIndex));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        editTagAcIndex = editTagAcIndex <= 0 ? items.length - 1 : editTagAcIndex - 1;
        items.forEach((el, i) => el.classList.toggle('selected', i === editTagAcIndex));
      } else if ((e.key === 'Enter' || e.key === 'Tab') && editTagAcIndex >= 0) {
        e.preventDefault();
        selectEditTagAcItem(items[editTagAcIndex].textContent.replace(/^#/, ''));
        hideEditTagAc();
      } else if (e.key === 'Escape') {
        hideEditTagAc();
      }
    });

    editTagsInput.addEventListener('input', () => {
      const val = editTagsInput.value;
      const commaIdx = val.lastIndexOf(',');
      const partial = (commaIdx >= 0 ? val.slice(commaIdx + 1) : val).trim();
      if (!partial) { hideEditTagAc(); return; }

      // Collect all known tags from all tasks
      const committed = val.slice(0, commaIdx >= 0 ? commaIdx : 0)
        .split(',').map(s => s.trim().toLowerCase()).filter(Boolean);
      const allTags = [...new Set(tasks.flatMap(t => t.tags || []))].sort();
      const matches = allTags
        .map(tag => ({ tag, score: fuzzyScore(partial, tag) }))
        .filter(({ tag, score }) => score >= 0 && !committed.includes(tag.toLowerCase()))
        .sort((a, b) => b.score - a.score || a.tag.localeCompare(b.tag))
        .map(({ tag }) => tag)
        .slice(0, 8);

      showEditTagAc(matches);
    });

    editTagsInput.addEventListener('blur', () => setTimeout(hideEditTagAc, 150));
  }

  // ── Links picker ──────────────────────────────────────────────────────────

  const linkChipsEl = document.getElementById('link-chips' + sfx);
  const linkSearchEl = document.getElementById('link-search' + sfx);
  const linkTypeEl = document.getElementById('link-type' + sfx);
  let linkAcEl = null;
  let linkAcIndex = -1;
  // [{type: 'blocked_by'|'blocking', taskId: string}]
  let currentLinks = [];
  let originalBlockingIds = [];

  function getLinkIds(type) {
    return currentLinks.filter(l => l.type === type).map(l => l.taskId);
  }

  function addLinkChip(type, taskId, title) {
    if (!linkChipsEl) return;
    if (currentLinks.some(l => l.type === type && l.taskId === taskId)) return;
    currentLinks.push({ type, taskId });
    const chip = document.createElement('span');
    chip.className = 'link-chip';
    chip.dataset.id = taskId;
    chip.dataset.type = type;
    const typeLabel = type === 'blocked_by' ? 'blocked by' : 'blocking';
    chip.innerHTML = `<span class="link-chip-type">${typeLabel}</span><a href="#" class="link-chip-title" data-id="${taskId}">${esc(title)}</a><button type="button" class="chip-remove">×</button>`;
    chip.querySelector('.link-chip-title').addEventListener('click', e => {
      e.preventDefault();
      modal.close();
      openEditModal(taskId);
    });
    chip.querySelector('.chip-remove').addEventListener('click', () => {
      currentLinks = currentLinks.filter(l => !(l.type === type && l.taskId === taskId));
      chip.remove();
    });
    linkChipsEl.appendChild(chip);
  }

  function hideLinkAc() {
    linkAcEl?.remove();
    linkAcEl = null;
    linkAcIndex = -1;
  }

  function showLinkAc(matches) {
    hideLinkAc();
    if (!matches.length) return;
    linkAcEl = document.createElement('div');
    linkAcEl.className = 'tag-ac-dropdown';
    matches.forEach(t => {
      const item = document.createElement('div');
      item.className = 'tag-ac-item';
      const tagStr = (t.tags && t.tags.length) ? t.tags.join(', ') + ' · ' : '';
      item.innerHTML = `${esc(t.title)} <span class="blocker-ac-meta">${esc(tagStr)}${t.id.slice(0, 6)}</span>`;
      item.addEventListener('mousedown', e => {
        e.preventDefault();
        addLinkChip(linkTypeEl.value, t.id, t.title);
        linkSearchEl.value = '';
        hideLinkAc();
      });
      linkAcEl.appendChild(item);
    });
    const parent = linkSearchEl.parentElement;
    parent.style.position = 'relative';
    linkAcEl.style.top = linkSearchEl.offsetTop + linkSearchEl.offsetHeight + 'px';
    linkAcEl.style.left = '0';
    linkAcEl.style.width = '100%';
    parent.appendChild(linkAcEl);
  }

  if (linkSearchEl) {
    linkSearchEl.addEventListener('input', () => {
      const q = linkSearchEl.value.trim().toLowerCase();
      if (!q) { hideLinkAc(); return; }
      const currentType = linkTypeEl?.value || 'blocked_by';
      const editingId = document.getElementById('task-edit-id' + sfx)?.value || '';
      const matches = tasks
        .filter(t => !t.completed_at && t.id !== editingId)
        .filter(t => !currentLinks.some(l => l.type === currentType && l.taskId === t.id))
        .filter(t => t.title.toLowerCase().includes(q) || t.id.slice(0, 6).startsWith(q))
        .slice(0, 8);
      showLinkAc(matches);
    });

    // Re-filter when type changes (stale dropdown would show wrong exclusions)
    linkTypeEl?.addEventListener('change', () => { hideLinkAc(); linkSearchEl.value = ''; });

    linkSearchEl.addEventListener('keydown', e => {
      if (!linkAcEl) return;
      const items = linkAcEl.querySelectorAll('.tag-ac-item');
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        linkAcIndex = (linkAcIndex + 1) % items.length;
        items.forEach((el, i) => el.classList.toggle('selected', i === linkAcIndex));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        linkAcIndex = linkAcIndex <= 0 ? items.length - 1 : linkAcIndex - 1;
        items.forEach((el, i) => el.classList.toggle('selected', i === linkAcIndex));
      } else if ((e.key === 'Enter' || e.key === 'Tab') && linkAcIndex >= 0) {
        e.preventDefault();
        items[linkAcIndex].dispatchEvent(new MouseEvent('mousedown'));
      } else if (e.key === 'Escape') {
        hideLinkAc();
      }
    });

    linkSearchEl.addEventListener('blur', () => setTimeout(hideLinkAc, 150));
  }

  // ── Add task ───────────────────────────────────────────────────────────────

  addBtn.addEventListener('click', () => openEditModal(null));

  // ── Edit modal ─────────────────────────────────────────────────────────────

  function openEditModal(taskId, opts) {
    const task = taskId ? tasks.find(t => t.id === taskId) : null;
    const defaultTags = opts?.tags || '';
    document.getElementById('task-edit-id' + sfx).value = task ? task.id : '';
    document.getElementById('task-edit-title' + sfx).value = task ? task.title : '';
    document.getElementById('task-edit-desc' + sfx).value = task ? task.description || '' : '';
    document.getElementById('task-edit-start' + sfx).value = task ? task.start_date || '' : '';
    const deferDaysInput = document.getElementById('task-edit-defer-days' + sfx);
    const deferDueCheck = document.getElementById('task-edit-defer-due' + sfx);
    const deferDaysField = deferDaysInput?.closest('.task-field');
    const isDeferDue = task?.defer_days === 0;
    deferDaysInput.value = isDeferDue ? '' : (task?.defer_days ?? '');
    if (deferDaysField) deferDaysField.style.visibility = isDeferDue ? 'hidden' : '';
    if (deferDueCheck) deferDueCheck.checked = isDeferDue;
    document.getElementById('task-edit-due' + sfx).value = task ? task.due_date || '' : '';
    document.getElementById('task-edit-tags' + sfx).value = task ? (task.tags || []).join(', ') : (defaultTags || initTag || '');
    if (linkChipsEl) {
      linkChipsEl.innerHTML = '';
      currentLinks = [];
      if (task) {
        (task.blocked_by || []).forEach(bid => {
          const blocker = tasks.find(t => t.id === bid);
          addLinkChip('blocked_by', bid, blocker ? blocker.title : bid.slice(0, 8));
        });
        tasks.filter(t => t.blocked_by && t.blocked_by.includes(task.id)).forEach(t => {
          addLinkChip('blocking', t.id, t.title);
        });
      }
      originalBlockingIds = getLinkIds('blocking');
    }
    const createdEl = document.getElementById('task-edit-created' + sfx);
    if (task && task.created_at) {
      createdEl.textContent = `Created: ${task.created_at.slice(0, 16).replace('T', ' ')}`;
      createdEl.style.display = '';
    } else {
      createdEl.style.display = 'none';
    }
    const recType = task?.recurrence?.type || '';
    recTypeSelect.value = recType;
    document.getElementById('task-edit-rec-interval' + sfx).value = task?.recurrence?.interval || 1;
    recIntervalField.style.display = recType ? '' : 'none';
    // Show "move to new" only for existing tasks with a sort_order
    const newField = document.getElementById('task-edit-inbox-field' + sfx);
    const newCb = document.getElementById('task-edit-inbox' + sfx);
    if (task && task.sort_order != null) {
      newField.style.display = '';
      newCb.checked = false;
    } else {
      newField.style.display = 'none';
      newCb.checked = false;
    }
    deleteBtn.style.display = task ? '' : 'none';
    modal.showModal();
    document.getElementById('task-edit-title' + sfx).focus();
  }

  recTypeSelect.addEventListener('change', () => {
    recIntervalField.style.display = recTypeSelect.value ? '' : 'none';
  });

  // Hide-until-due checkbox
  const deferDueCheckEl = document.getElementById('task-edit-defer-due' + sfx);
  if (deferDueCheckEl) {
    deferDueCheckEl.addEventListener('change', () => {
      const ddi = document.getElementById('task-edit-defer-days' + sfx);
      const ddf = ddi?.closest('.task-field');
      if (deferDueCheckEl.checked) {
        ddi.value = '';
        if (ddf) ddf.style.visibility = 'hidden';
      } else {
        if (ddf) ddf.style.visibility = '';
      }
    });
  }

  // Quick due-date shortcuts
  function dateOffset(days) {
    const d = new Date();
    d.setDate(d.getDate() + days);
    return dateToStr(d);
  }
  function nextMonday() {
    const d = new Date();
    const day = d.getDay();
    const add = day === 0 ? 1 : 8 - day;
    d.setDate(d.getDate() + add);
    return dateToStr(d);
  }
  document.querySelectorAll('#task-edit-modal' + sfx + ' .task-date-shortcuts button').forEach(btn => {
    btn.addEventListener('click', () => {
      const dueInput = document.getElementById('task-edit-due' + sfx);
      const v = btn.dataset.due;
      if (v === 'today')         dueInput.value = dateOffset(0);
      else if (v === 'tomorrow') dueInput.value = dateOffset(1);
      else if (v === '+3')       dueInput.value = dateOffset(3);
      else if (v === 'next-week') dueInput.value = nextMonday();
      else if (v === 'clear')    dueInput.value = '';
    });
  });

  cancelBtn.addEventListener('click', () => { hideEditTagAc(); modal.close(); });
  modal.addEventListener('click', e => { if (e.target === modal) { hideEditTagAc(); modal.close(); } });

  form.addEventListener('submit', async e => {
    e.preventDefault();
    const id = document.getElementById('task-edit-id' + sfx).value;
    const data = {
      title: document.getElementById('task-edit-title' + sfx).value.trim(),
      description: document.getElementById('task-edit-desc' + sfx).value.trim(),
      start_date: document.getElementById('task-edit-start' + sfx).value || null,
      due_date: document.getElementById('task-edit-due' + sfx).value || null,
      defer_days: document.getElementById('task-edit-defer-due' + sfx)?.checked
        ? 0
        : (parseInt(document.getElementById('task-edit-defer-days' + sfx).value) ?? null) || null,
      tags: document.getElementById('task-edit-tags' + sfx).value.split(',').map(t => t.trim()).filter(Boolean),
      rec_type: recTypeSelect.value || null,
      rec_interval: parseInt(document.getElementById('task-edit-rec-interval' + sfx).value) || 1,
      blocked_by: getLinkIds('blocked_by'),
    };
    if (!data.title) return;
    // Move to new: reset sort_order to null
    if (id && document.getElementById('task-edit-inbox' + sfx).checked) {
      data.sort_order = null;
    }
    const newBlockingIds = getLinkIds('blocking');
    modal.close();
    let savedId = id;
    if (id) {
      await updateTask(id, data);
    } else {
      const created = await createTask(data);
      if (created) savedId = created.id;
    }
    // Reconcile "blocking" links — update other tasks' blocked_by
    if (savedId) {
      const addedBlocking = newBlockingIds.filter(tid => !originalBlockingIds.includes(tid));
      const removedBlocking = originalBlockingIds.filter(tid => !newBlockingIds.includes(tid));
      for (const tid of addedBlocking) {
        const t = tasks.find(x => x.id === tid);
        if (t && !(t.blocked_by || []).includes(savedId)) {
          await updateTask(tid, { blocked_by: [...(t.blocked_by || []), savedId] });
        }
      }
      for (const tid of removedBlocking) {
        const t = tasks.find(x => x.id === tid);
        if (t) {
          await updateTask(tid, { blocked_by: (t.blocked_by || []).filter(bid => bid !== savedId) });
        }
      }
    }
  });

  deleteBtn.addEventListener('click', async () => {
    const id = document.getElementById('task-edit-id' + sfx).value;
    if (!id) return;
    const task = tasks.find(t => t.id === id);
    modal.close();
    if (await confirmDelete(task?.title || '')) deleteTask(id);
  });

  // ── Drag and Drop ──────────────────────────────────────────────────────────

  function setupDragAndDrop() {
    const items = listEl.querySelectorAll('.task-item[draggable]');
    items.forEach(item => {
      item.addEventListener('dragstart', e => {
        draggedId = item.dataset.id;
        item.classList.add('task-dragging');
        e.dataTransfer.effectAllowed = 'move';
      });
      item.addEventListener('dragend', () => {
        item.classList.remove('task-dragging');
        draggedId = null;
        listEl.querySelectorAll('.task-drag-over').forEach(el => el.classList.remove('task-drag-over'));
      });
      item.addEventListener('dragover', e => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        if (item.dataset.id !== draggedId) {
          item.classList.add('task-drag-over');
        }
      });
      item.addEventListener('dragleave', () => {
        item.classList.remove('task-drag-over');
      });
      item.addEventListener('drop', e => {
        e.preventDefault();
        item.classList.remove('task-drag-over');
        if (!draggedId || item.dataset.id === draggedId) return;
        // Reorder in the filtered+sorted view
        const ids = Array.from(listEl.querySelectorAll('.task-item')).map(el => el.dataset.id);
        const fromIdx = ids.indexOf(draggedId);
        const toIdx = ids.indexOf(item.dataset.id);
        if (fromIdx === -1 || toIdx === -1) return;
        const draggedTask = tasks.find(tk => tk.id === draggedId);
        const wasNew = draggedTask && draggedTask.sort_order == null;
        ids.splice(fromIdx, 1);
        // When dragging from new into ordered, insert BEFORE target (adjust index).
        // Within-section reorder uses standard behavior (no adjustment).
        const insertIdx = (wasNew && fromIdx < toIdx) ? toIdx - 1 : toIdx;
        ids.splice(insertIdx, 0, draggedId);
        // Only assign sort_order to items that already had one, plus the dragged item
        ids.forEach((tid, i) => {
          const t = tasks.find(tk => tk.id === tid);
          if (t && (t.sort_order != null || t.id === draggedId)) t.sort_order = i;
        });
        // Send only the items that have a sort_order to the server
        const orderedIds = ids.filter(tid => {
          const t = tasks.find(tk => tk.id === tid);
          return t && t.sort_order != null;
        });
        reorder(orderedIds);
        render();
      });
    });
  }

  // ── Keyboard navigation ────────────────────────────────────────────────────

  function reorderSelected(direction) {
    if (sortSelect.value !== 'manual' || !selectedIds.size) return;
    const items = visibleItems();
    const ids = items.map(el => el.dataset.id);

    if (selectedIds.size === 1) {
      const idx = ids.indexOf(selectedId);
      if (idx === -1) return;
      const selTask = tasks.find(t => t.id === selectedId);

      if (direction === 'down') {
        if (idx === ids.length - 1) return;
        if (selTask && selTask.sort_order == null) {
          // Moving new item into ordered: place at top of ordered section
          ids.splice(idx, 1);
          const firstOrd = ids.findIndex(id => {
            const t = tasks.find(tk => tk.id === id);
            return t && t.sort_order != null;
          });
          ids.splice(firstOrd === -1 ? ids.length : firstOrd, 0, selectedId);
        } else {
          [ids[idx + 1], ids[idx]] = [ids[idx], ids[idx + 1]];
        }
      } else {
        if (idx === 0) return;
        [ids[idx - 1], ids[idx]] = [ids[idx], ids[idx - 1]];
      }
    } else {
      const sel = selectedIds;
      const selIdx = ids.map((id, i) => sel.has(id) ? i : -1).filter(i => i >= 0);
      const topIdx = selIdx[0], botIdx = selIdx[selIdx.length - 1];
      const rest = ids.filter(id => !sel.has(id));
      const group = selIdx.map(i => ids[i]);
      if (direction === 'up') {
        if (topIdx === 0) return;
        const insertAt = rest.indexOf(ids[topIdx - 1]);
        rest.splice(insertAt, 0, ...group);
      } else {
        if (botIdx === ids.length - 1) return;
        const insertAt = rest.indexOf(ids[botIdx + 1]) + 1;
        rest.splice(insertAt, 0, ...group);
      }
      ids.length = 0; ids.push(...rest);
    }
    ids.forEach((tid, i) => {
      const t = tasks.find(tk => tk.id === tid);
      if (t && (t.sort_order != null || selectedIds.has(tid))) t.sort_order = i;
    });
    const orderedIds = ids.filter(tid => {
      const t = tasks.find(tk => tk.id === tid);
      return t && t.sort_order != null;
    });
    reorder(orderedIds);
    render();
  }

  document.addEventListener('keydown', async e => {
    // Only the active instance handles keyboard events (multi-embed safety)
    if (allApps.length > 1 && activeDeckInstance !== instanceId) return;

    // Ctrl+Z undo — works everywhere except inside the edit modal
    if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey && pendingUndoFn) {
      if (modal.open || deleteModal.open) return;
      const focus = document.activeElement;
      if (focus && focus.closest('textarea, [contenteditable]')) return;
      e.preventDefault();
      triggerUndo();
      return;
    }

    if (modal.open || deleteModal.open || document.querySelector('dialog[open].scratch-modal')) return;
    const focus = document.activeElement;
    if (focus && focus.closest('input, textarea, select, [contenteditable]')) return;

    const items = visibleItems();
    const currentIdx = selectedId ? items.findIndex(el => el.dataset.id === selectedId) : -1;

    // 'n' opens new task modal
    if (e.key === 'n' && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      openEditModal(null);
      return;
    }

    // Tab/Shift+Tab cycles filter tabs
    if (e.key === 'Tab') {
      e.preventDefault();
      const tabs = Array.from(tabsEl.querySelectorAll('.tab'));
      const activeIdx = tabs.findIndex(t => t.classList.contains('active'));
      const next = e.shiftKey
        ? (activeIdx > 0 ? activeIdx - 1 : tabs.length - 1)
        : (activeIdx < tabs.length - 1 ? activeIdx + 1 : 0);
      tabs[next].click();
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
      if (currentIdx < items.length - 1) extendSelection(items[currentIdx + 1].dataset.id);
    } else if (e.shiftKey && (e.key === 'ArrowUp' || e.key === 'K')) {
      e.preventDefault();
      if (currentIdx > 0) extendSelection(items[currentIdx - 1].dataset.id);
    } else if (e.key === 'ArrowDown' || e.key === 'j') {
      e.preventDefault();
      setSelected(items[currentIdx < items.length - 1 ? currentIdx + 1 : 0].dataset.id);
    } else if (e.key === 'ArrowUp' || e.key === 'k') {
      e.preventDefault();
      setSelected(items[currentIdx > 0 ? currentIdx - 1 : items.length - 1].dataset.id);
    } else if (e.key === ' ' && selectedId) {
      e.preventDefault();
      if (selectedIds.size > 1) {
        // Bulk complete/uncomplete
        const ids = [...selectedIds];
        const completing = ids.some(id => { const t = tasks.find(tk => tk.id === id); return t && !t.completed_at; });
        const next = items.find((el, i) => i > currentIdx && !selectedIds.has(el.dataset.id))
                  || items.find(el => !selectedIds.has(el.dataset.id));
        setSelected(next?.dataset.id || null);
        await completeTasks(ids, completing);
        const label = completing ? `${ids.length} tasks completed` : `${ids.length} tasks reopened`;
        showUndo(label, () => completeTasks(ids, !completing));
      } else {
        const task = tasks.find(t => t.id === selectedId);
        if (!task) return;
        const completing = !task.completed_at;
        const willDisappear = completing && activeFilter !== 'completed';
        if (willDisappear) {
          const next = items.find((el, i) => i > currentIdx && !selectedIds.has(el.dataset.id))
                    || items.find(el => !selectedIds.has(el.dataset.id));
          setSelected(next?.dataset.id || null);
        }
        updateTask(task.id, { completed: completing });
        const label = completing ? 'Task completed' : 'Task reopened';
        showUndo(label, () => completeTasks([task.id], !completing));
      }
    } else if (e.key === 'Enter' && selectedId) {
      e.preventDefault();
      openEditModal(selectedId);
    } else if (e.key === 'Delete' && selectedIds.size) {
      e.preventDefault();
      const ids = [...selectedIds];
      const count = ids.length;
      const title = count === 1 ? (tasks.find(t => t.id === ids[0])?.title || '') : '';
      if (await confirmDelete(title, count)) {
        // Snapshot deleted tasks for undo
        const deleted = ids.map(id => tasks.find(t => t.id === id)).filter(Boolean);
        const next = items.find(el => !selectedIds.has(el.dataset.id) &&
                       items.indexOf(el) > currentIdx)
                  || items.find(el => !selectedIds.has(el.dataset.id));
        selectedId = next?.dataset.id || null;
        selectedIds = new Set(selectedId ? [selectedId] : []);
        anchorId = selectedId;
        await deleteTasks(ids);
        const label = count === 1 ? 'Task deleted' : `${count} tasks deleted`;
        showUndo(label, async () => {
          for (const t of deleted) {
            await createTask({
              title: t.title, description: t.description || '',
              tags: t.tags || [], due_date: t.due_date, start_date: t.start_date,
              defer_days: t.defer_days, rec_type: t.recurrence?.type,
              rec_interval: t.recurrence?.interval,
            });
          }
        });
      }
    } else if ((e.key === 't' || e.key === 'T') && selectedIds.size) {
      e.preventDefault();
      const ids = [...selectedIds];
      const removing = e.key === 'T';
      openBulkTagModal(ids, removing);
    } else if (e.key === 'Escape') {
      selectedId = null;
      selectedIds.clear();
      anchorId = null;
      listEl.querySelectorAll('.task-selected').forEach(el => el.classList.remove('task-selected'));
    }
  });

  // ── Expose refresh and modal for quick-add ────────────────────────────────

  window.refreshDeck = fetchDeck;
  window.openDeckEdit = openEditModal;

  // ── Init ───────────────────────────────────────────────────────────────────

  // Apply URL-param pre-filtering
  if (initTag && filterTag) filterTag.value = initTag;
  const initSearch = urlParams.get('search') || '';
  if (initSearch && filterSearch) filterSearch.value = initSearch;
  if (initFilter) {
    const tab = tabsEl?.querySelector(`[data-filter="${initFilter}"]`);
    if (tab) {
      tabsEl.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      activeFilter = initFilter;
    }
  }

  fetchDeck();

  // Auto-open new item modal if ?new=1 in URL
  if (initNew) {
    openEditModal(null);
    // Clean up URL
    const url = new URL(window.location);
    url.searchParams.delete('new');
    history.replaceState(null, '', url);
  }

  } // end initDeckInstance

}());
