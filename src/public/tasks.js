// ── Leaf Task App ────────────────────────────────────────────────────────────

(function () {
  'use strict';

  const listEl = document.getElementById('tasks-list');
  const tabsEl = document.getElementById('tasks-tabs');
  const addBtn = document.getElementById('task-add-btn');
  const sortSelect = document.getElementById('task-sort');
  const filterTag = document.getElementById('task-filter-tag');
  const filterSearch = document.getElementById('task-filter-search');
  const modal = document.getElementById('task-edit-modal');
  const form = document.getElementById('task-edit-form');
  const cancelBtn = document.getElementById('task-edit-cancel');
  const deleteBtn = document.getElementById('task-edit-delete');
  const recTypeSelect = document.getElementById('task-edit-rec-type');
  const recIntervalField = document.getElementById('task-rec-interval-field');
  const deleteModal = document.getElementById('task-delete-modal');
  const deleteConfirmBtn = document.getElementById('task-delete-confirm');
  const deleteCancelBtn = document.getElementById('task-delete-cancel');
  const deleteTitleEl = document.getElementById('task-delete-title');

  if (!listEl) return;

  let tasks = [];
  let activeFilter = 'all';
  let draggedId = null;
  let selectedId  = null;
  let selectedIds = new Set();
  let anchorId    = null;
  let completedLimit = 50;

  // ── API ────────────────────────────────────────────────────────────────────

  async function fetchTasks() {
    const res = await fetch('/api/tasks');
    tasks = await res.json();
    render();
  }

  async function createTask(data) {
    const res = await fetch('/api/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (res.ok) {
      const task = await res.json();
      tasks.push(task);
      render();
      window.refreshDueBadge?.();
    }
  }

  async function updateTask(id, data) {
    const res = await fetch(`/api/tasks/${id}`, {
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
        await fetchTasks();
      } else {
        render();
      }
      window.refreshDueBadge?.();
    }
  }

  async function deleteTask(id) {
    const res = await fetch(`/api/tasks/${id}`, { method: 'DELETE' });
    if (res.ok) {
      tasks = tasks.filter(t => t.id !== id);
      render();
      window.refreshDueBadge?.();
    }
  }

  async function deleteTasks(ids) {
    const res = await fetch('/api/tasks/batch-delete', {
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

  async function reorder(order) {
    await fetch('/api/tasks/reorder', {
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
    return new Date().toISOString().slice(0, 10);
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

    // Hide tasks whose start_date hasn't arrived yet (unless viewing completed or scheduled)
    if (activeFilter !== 'completed' && activeFilter !== 'scheduled') {
      filtered = filtered.filter(t => !t.start_date || t.start_date <= today || t.completed_at);
    }

    // Tab filter
    if (activeFilter === 'due') {
      filtered = filtered.filter(t => !t.completed_at && t.due_date);
    } else if (activeFilter === 'completed') {
      filtered = filtered.filter(t => !!t.completed_at);
    } else if (activeFilter === 'scheduled') {
      filtered = filtered.filter(t => !t.completed_at && t.start_date && t.start_date > today);
    } else {
      // "all" shows non-completed by default
      filtered = filtered.filter(t => !t.completed_at);
    }

    // Tag filter — comma-separated, all specified tags must match (AND)
    const tagTerms = filterTag.value.split(',').map(s => s.trim().toLowerCase()).filter(Boolean);
    if (tagTerms.length) {
      filtered = filtered.filter(t =>
        tagTerms.every(term => t.tags && t.tags.some(tg => tg.toLowerCase().includes(term)))
      );
    }

    return filtered;
  }

  // ── Sorting ────────────────────────────────────────────────────────────────

  // Per-tab fixed sort comparators; null = use the user's sort select
  const TAB_SORT = {
    due:       (a, b) => (a.due_date || '').localeCompare(b.due_date || ''),
    completed: (a, b) => (b.completed_at || '').localeCompare(a.completed_at || ''),
    scheduled: (a, b) => (a.start_date || '').localeCompare(b.start_date || ''),
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

    // In "all" view split into ordered (sort_order set) and inbox (sort_order null)
    let orderedTasks, inboxTasks;
    if (activeFilter === 'all') {
      orderedTasks = sortTasks(filtered.filter(t => t.sort_order != null));
      inboxTasks   = filtered.filter(t => t.sort_order == null)
                             .sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
    } else {
      orderedTasks = sortTasks(filtered);
      inboxTasks   = [];
    }

    if (orderedTasks.length === 0 && inboxTasks.length === 0) {
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
      const startDisplay = task.start_date ? `<span class="task-date-info">defer: ${formatDate(task.start_date)}</span>` : '';
      const completedDisplay = task.completed_at ? `<span class="task-date-info">done: ${task.completed_at.slice(0, 10)}</span>` : '';
      const hasMeta = dueDisplay || startDisplay || completedDisplay || recIcon || tags;
      const metaHtml = hasMeta ? `<div class="task-meta">${startDisplay}${dueDisplay}${completedDisplay}${recIcon}${tags}</div>` : '';
      const dragAttr = sortSelect.value === 'manual' ? 'draggable="true"' : '';
      return `
        <li class="task-item ${completedClass} ${urgClass}" data-id="${task.id}" ${dragAttr}>
          <label class="task-checkbox">
            <input type="checkbox" ${checked} data-id="${task.id}">
          </label>
          <div class="task-content" data-id="${task.id}">
            <span class="task-title" data-id="${task.id}">${esc(task.title)}</span>
            ${metaHtml}
            ${task.description ? `<p class="task-desc">${esc(task.description)}</p>` : ''}
          </div>
          <button class="task-edit-btn" data-id="${task.id}" title="Edit">✎</button>
        </li>`;
    };

    let html = '';
    let truncated = false;
    if (inboxTasks.length > 0) {
      html += '<li class="task-inbox-divider task-inbox-divider--only">Inbox</li>';
      html += inboxTasks.map(taskHTML).join('');
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
    const showMoreBtn = document.getElementById('tasks-show-more-btn');
    if (showMoreBtn) {
      showMoreBtn.addEventListener('click', () => {
        completedLimit += 50;
        render();
      });
    }

    // Attach event listeners
    listEl.querySelectorAll('input[type=checkbox]').forEach(cb => {
      cb.addEventListener('change', e => {
        updateTask(e.target.dataset.id, { completed: e.target.checked });
      });
    });

    listEl.querySelectorAll('.task-edit-btn').forEach(btn => {
      btn.addEventListener('click', e => {
        openEditModal(e.currentTarget.dataset.id);
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

  filterTag.addEventListener('input', () => {
    const val = filterTag.value;
    const commaIdx = val.lastIndexOf(',');
    const partial = (commaIdx >= 0 ? val.slice(commaIdx + 1) : val).trim();
    if (!partial) { hideTagAc(); return; }

    // Collect tags only from tasks visible under the current tab + committed tag filters.
    // Temporarily swap filterTag.value so filterTasks() sees only committed terms.
    const committedVal = commaIdx >= 0 ? val.slice(0, commaIdx) : '';
    filterTag.value = committedVal;
    const visibleTasks = filterTasks();
    filterTag.value = val;

    const committed = committedVal.split(',').map(s => s.trim().toLowerCase()).filter(Boolean);
    const available = [...new Set(visibleTasks.flatMap(t => t.tags || []))].sort();

    const matches = available
      .map(tag => ({ tag, score: fuzzyScore(partial, tag) }))
      .filter(({ tag, score }) => score >= 0 && !committed.includes(tag.toLowerCase()))
      .sort((a, b) => b.score - a.score || a.tag.localeCompare(b.tag))
      .map(({ tag }) => tag)
      .slice(0, 8);

    showTagAc(matches);
  });

  filterTag.addEventListener('blur', () => setTimeout(hideTagAc, 150));

  // ── Add task ───────────────────────────────────────────────────────────────

  addBtn.addEventListener('click', () => openEditModal(null));

  // ── Edit modal ─────────────────────────────────────────────────────────────

  function openEditModal(taskId) {
    const task = taskId ? tasks.find(t => t.id === taskId) : null;
    document.getElementById('task-edit-id').value = task ? task.id : '';
    document.getElementById('task-edit-title').value = task ? task.title : '';
    document.getElementById('task-edit-desc').value = task ? task.description || '' : '';
    document.getElementById('task-edit-start').value = task ? task.start_date || '' : '';
    document.getElementById('task-edit-due').value = task ? task.due_date || '' : '';
    document.getElementById('task-edit-tags').value = task ? (task.tags || []).join(', ') : '';
    const createdEl = document.getElementById('task-edit-created');
    if (task && task.created_at) {
      createdEl.textContent = `Created: ${task.created_at.slice(0, 16).replace('T', ' ')}`;
      createdEl.style.display = '';
    } else {
      createdEl.style.display = 'none';
    }
    const recType = task?.recurrence?.type || '';
    recTypeSelect.value = recType;
    document.getElementById('task-edit-rec-interval').value = task?.recurrence?.interval || 1;
    recIntervalField.style.display = recType ? '' : 'none';
    // Show "move to inbox" only for existing tasks with a sort_order
    const inboxField = document.getElementById('task-edit-inbox-field');
    const inboxCb = document.getElementById('task-edit-inbox');
    if (task && task.sort_order != null) {
      inboxField.style.display = '';
      inboxCb.checked = false;
    } else {
      inboxField.style.display = 'none';
      inboxCb.checked = false;
    }
    deleteBtn.style.display = task ? '' : 'none';
    modal.showModal();
    document.getElementById('task-edit-title').focus();
  }

  recTypeSelect.addEventListener('change', () => {
    recIntervalField.style.display = recTypeSelect.value ? '' : 'none';
  });

  cancelBtn.addEventListener('click', () => modal.close());
  modal.addEventListener('click', e => { if (e.target === modal) modal.close(); });

  form.addEventListener('submit', async e => {
    e.preventDefault();
    const id = document.getElementById('task-edit-id').value;
    const data = {
      title: document.getElementById('task-edit-title').value.trim(),
      description: document.getElementById('task-edit-desc').value.trim(),
      start_date: document.getElementById('task-edit-start').value || null,
      due_date: document.getElementById('task-edit-due').value || null,
      tags: document.getElementById('task-edit-tags').value.split(',').map(t => t.trim()).filter(Boolean),
      rec_type: recTypeSelect.value || null,
      rec_interval: parseInt(document.getElementById('task-edit-rec-interval').value) || 1,
    };
    if (!data.title) return;
    // Move to inbox: reset sort_order to null
    if (id && document.getElementById('task-edit-inbox').checked) {
      data.sort_order = null;
    }
    modal.close();
    if (id) {
      await updateTask(id, data);
    } else {
      await createTask(data);
    }
  });

  deleteBtn.addEventListener('click', async () => {
    const id = document.getElementById('task-edit-id').value;
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
        ids.splice(fromIdx, 1);
        ids.splice(toIdx, 0, draggedId);
        // Only assign sort_order to items that already had one, plus the dragged item
        const draggedTask = tasks.find(tk => tk.id === draggedId);
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
      if (direction === 'up' && idx === 0) return;
      if (direction === 'down' && idx === ids.length - 1) return;
      if (direction === 'up') [ids[idx - 1], ids[idx]] = [ids[idx], ids[idx - 1]];
      else [ids[idx + 1], ids[idx]] = [ids[idx], ids[idx + 1]];
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
    if (modal.open || deleteModal.open) return;
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
      const task = tasks.find(t => t.id === selectedId);
      if (!task) return;
      const willDisappear = !task.completed_at && activeFilter !== 'completed';
      if (willDisappear) {
        const next = items.find((el, i) => i > currentIdx && !selectedIds.has(el.dataset.id))
                  || items.find(el => !selectedIds.has(el.dataset.id));
        setSelected(next?.dataset.id || null);
      }
      updateTask(task.id, { completed: !task.completed_at });
    } else if (e.key === 'Enter' && selectedId) {
      e.preventDefault();
      openEditModal(selectedId);
    } else if (e.key === 'Delete' && selectedIds.size) {
      e.preventDefault();
      const ids = [...selectedIds];
      const count = ids.length;
      const title = count === 1 ? (tasks.find(t => t.id === ids[0])?.title || '') : '';
      if (await confirmDelete(title, count)) {
        const next = items.find(el => !selectedIds.has(el.dataset.id) &&
                       items.indexOf(el) > currentIdx)
                  || items.find(el => !selectedIds.has(el.dataset.id));
        selectedId = next?.dataset.id || null;
        selectedIds = new Set(selectedId ? [selectedId] : []);
        anchorId = selectedId;
        await deleteTasks(ids);
      }
    } else if (e.key === 'Escape') {
      selectedId = null;
      selectedIds.clear();
      anchorId = null;
      listEl.querySelectorAll('.task-selected').forEach(el => el.classList.remove('task-selected'));
    }
  });

  // ── Expose refresh and modal for quick-add ────────────────────────────────

  window.refreshTasks = fetchTasks;

  // ── Init ───────────────────────────────────────────────────────────────────

  fetchTasks();

}());
