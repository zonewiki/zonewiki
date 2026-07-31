// ── Leaf Task App ────────────────────────────────────────────────────────────

(function () {
  'use strict';

  // Pure task logic lives in deck-core.js (loaded before this file).
  const { dateToStr, addDays, isBlocked, isDeferred, tagMatchScore } = window.DeckCore;

  // Find all deck app instances on the page and initialize each
  const allApps = document.querySelectorAll('.tasks-app');
  if (!allApps.length) return;

  // Track which instance owns keyboard focus (only one at a time)
  let activeDeckInstance = null;

  // Registry of initialized instances. Each embed on the page holds its own
  // task array, so after a write in one embed the others would show stale data
  // until reload. Every instance registers a `refresh` callback here; a write
  // in one instance refreshes the rest.
  const deckInstances = [];
  function refreshOtherInstances(exceptId) {
    deckInstances.forEach(inst => { if (inst.id !== exceptId) inst.refresh(); });
  }
  function refreshAllInstances() {
    deckInstances.forEach(inst => inst.refresh());
  }

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
  const deleteModal = document.getElementById('task-delete-modal' + sfx);
  const deleteConfirmBtn = document.getElementById('task-delete-confirm' + sfx);
  const deleteCancelBtn = document.getElementById('task-delete-cancel' + sfx);
  const deleteTitleEl = document.getElementById('task-delete-title' + sfx);

  if (!listEl) return;

  let tasks = [];
  let completedLoaded = false;
  let activeFilter = 'open';
  let draggedId = null;
  let selectedId  = null;
  let selectedIds = new Set();
  let anchorId    = null;
  let completedLimit = 50;
  let undoTimer  = null;
  let undoToastEl = null;
  let pendingUndoFn = null;
  const pendingCompletions = new Map(); // taskId → timeoutId
  // Day sections with >= this many tasks are flagged; [[deck capacity=N]]
  // overrides the default per embed.
  const CAPACITY = parseInt(appEl.dataset.initCapacity, 10) || 5;
  const STALE_DAYS = 3;  // focus plans older than this are styled stale

  // Upcoming day board: dragging a task onto a day section re-plans it (sets
  // focus_date); dropping on "Someday" clears the plan. These list-level
  // listeners are attached once and no-op outside the board view. Per-item
  // dragstart is (re)wired each render by setupBoardDragAndDrop().
  function boardDivider(el) {
    let li = el.closest && el.closest('li');
    while (li && !li.classList.contains('board-day-divider')) li = li.previousElementSibling;
    return li;
  }
  function clearBoardDropOver() {
    listEl.querySelectorAll('.board-drop-over').forEach(el => el.classList.remove('board-drop-over'));
  }
  listEl.addEventListener('dragover', e => {
    if (activeFilter !== 'due' || !draggedId) return;
    const div = boardDivider(e.target);
    if (!div || !div.dataset.dropBucket) return;  // non-droppable section
    e.preventDefault();
    if (!div.classList.contains('board-drop-over')) { clearBoardDropOver(); div.classList.add('board-drop-over'); }
  });
  listEl.addEventListener('drop', e => {
    if (activeFilter !== 'due' || !draggedId) return;
    const div = boardDivider(e.target);
    if (!div || !div.dataset.dropBucket) return;
    e.preventDefault();
    const id = draggedId;
    clearBoardDropOver();
    if (div.dataset.dropBucket === 'someday') updateTask(id, { focus_date: null });
    else if (div.dataset.dropBucket === 'later') {
      // "Later" is a range, not a day — ask which date (min: beyond this week).
      openDatePicker('', d => { if (d) updateTask(id, { focus_date: d }); }, div, dateOffset(7));
    }
    else if (div.dataset.dropDay) updateTask(id, { focus_date: div.dataset.dropDay });
  });

  // Claim keyboard focus when user interacts with this instance
  appEl.addEventListener('pointerdown', () => { activeDeckInstance = instanceId; });
  appEl.addEventListener('focusin', () => { activeDeckInstance = instanceId; });

  const urlParams = new URLSearchParams(window.location.search);
  const initTag = appEl?.dataset.initTag || urlParams.get('tag') || '';
  const initPage = appEl?.dataset.initPage || urlParams.get('page') || '';
  const urlFilter = urlParams.get('filter') || '';       // explicit per-navigation override
  const embedFilter = appEl?.dataset.initFilter || '';   // [[deck filter=…]] — always wins when specified
  const initNew = appEl?.dataset.initNew === '1' || urlParams.has('new');

  // Remember the active tab per page + embed instance for the current tab only
  // (sessionStorage): a refresh lands on the last-clicked tab, but a fresh load
  // in a new tab / next day starts empty so the embed's configured filter loads.
  const tabStorageKey = `leaf-deck-tab:${location.pathname}#${instanceId}`;
  function saveActiveTab(f) { try { sessionStorage.setItem(tabStorageKey, f); } catch (_) {} }
  function loadActiveTab() { try { return sessionStorage.getItem(tabStorageKey); } catch (_) { return null; } }

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

  // Plain auto-dismissing toast (no undo button) — for server-rejected writes.
  function showNotice(msg) {
    const el = document.createElement('div');
    el.className = 'raw-toast task-undo-toast';
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 4000);
  }

  async function noticeFromResponse(res, fallback) {
    try {
      const body = await res.json();
      showNotice(body.error || fallback);
    } catch (_) {
      showNotice(fallback);
    }
  }

  // ── API ────────────────────────────────────────────────────────────────────

  async function fetchDeck() {
    const res = await fetch('/api/deck');
    tasks = await res.json();
    render();
  }

  // Reload this instance from the server, honoring its current tab. Used to
  // pull in changes made by another embed on the same page.
  async function refresh() {
    completedLoaded = false;
    await fetchDeck();
    if (activeFilter === 'completed') await fetchCompleted();
  }

  async function fetchCompleted() {
    if (completedLoaded) return;
    const res = await fetch('/api/deck/completed');
    const completed = await res.json();
    const existingIds = new Set(tasks.map(t => t.id));
    for (const t of completed) {
      if (!existingIds.has(t.id)) tasks.push(t);
    }
    completedLoaded = true;
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
      refreshOtherInstances(instanceId);
      return task;
    }
    await noticeFromResponse(res, 'Could not create task');
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
      // Completion/skip/reopen of a recurring task spawns or removes an
      // instance server-side — re-fetch to pick it up.
      if (updated.recurrence && (data.completed || data.skip || 'wont_do' in data)) {
        await fetchDeck();
      } else {
        render();
      }
      window.refreshDueBadge?.();
      refreshOtherInstances(instanceId);
      return updated;
    }
    await noticeFromResponse(res, 'Update rejected');
    return null;
  }

  async function deleteTask(id) {
    const res = await fetch(`/api/deck/${id}`, { method: 'DELETE' });
    if (res.ok) {
      tasks = tasks.filter(t => t.id !== id);
      render();
      window.refreshDueBadge?.();
      refreshOtherInstances(instanceId);
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
      refreshOtherInstances(instanceId);
    }
  }

  function beginComplete(id, li) {
    const cb = li?.querySelector('input[type=checkbox]');
    if (cb) cb.checked = true;
    if (li) li.classList.add('task-completing');
    if (pendingCompletions.has(id)) clearTimeout(pendingCompletions.get(id));
    const tid = setTimeout(() => {
      pendingCompletions.delete(id);
      if (li) li.classList.add('task-completing-out');
      setTimeout(() => updateTask(id, { completed: true }), 400);
    }, 4600);
    pendingCompletions.set(id, tid);
  }

  function cancelPendingComplete(id) {
    if (!pendingCompletions.has(id)) return false;
    clearTimeout(pendingCompletions.get(id));
    pendingCompletions.delete(id);
    const li = listEl.querySelector(`.task-item[data-id="${CSS.escape(id)}"]`);
    if (li) li.classList.remove('task-completing', 'task-completing-out');
    const cb = li?.querySelector('input[type=checkbox]');
    if (cb) cb.checked = false;
    return true;
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
      refreshOtherInstances(instanceId);
    }
  }

  // Bare PUT without the per-call render of updateTask — for batched writes
  // that end with a single fetchDeck().
  function putTask(id, data) {
    return fetch(`/api/deck/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  }

  // Clear focus_date on the stale plans the Focus bar counts (same filtered
  // view, so tag/page filters are respected), with undo restoring the dates.
  async function clearStalePlans() {
    const cutoff = dateOffset(-STALE_DAYS);
    const stale = filterTasks().filter(t => t.focus_date && t.focus_date <= cutoff);
    if (!stale.length) return;
    const snapshot = stale.map(t => ({ id: t.id, focus_date: t.focus_date }));
    await Promise.all(stale.map(t => putTask(t.id, { focus_date: null })));
    await fetchDeck();
    refreshOtherInstances(instanceId);
    showUndo(`${stale.length} stale plan${stale.length > 1 ? 's' : ''} cleared`, async () => {
      await Promise.all(snapshot.map(s => putTask(s.id, { focus_date: s.focus_date })));
      await fetchDeck();
      refreshOtherInstances(instanceId);
    });
  }

  async function postReorder(body) {
    await fetch('/api/deck/reorder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    refreshOtherInstances(instanceId);
  }

  async function reorder(order, field = 'sort_order') { return postReorder({ order, field }); }
  async function reorderUpdates(updates, field = 'sort_order') { return postReorder({ updates, field }); }

  // True when the visible task list is a narrowed view (tag/search/page
  // filter) of the full Backlog rather than every open task. Manual reorders
  // done under a narrowed view must not renumber the whole visible list —
  // tasks hidden by the filter would keep their old sort_order and collide
  // with the freshly-renumbered ones once the filter is cleared.
  function hasNarrowingFilter() {
    return !!(filterTag.value.trim() || filterSearch?.value.trim() || initPage);
  }

  // Assign sort_order values to `groupIds` (already spliced into `ids` at
  // `insertIdx`, contiguously and in order) so they land between the nearest
  // already-ordered neighbors. Only the group's own tasks are touched — every
  // other task keeps its existing sort_order, so the move stays correctly
  // placed relative to filtered-out tasks once the filter is cleared.
  function interpolateOrder(ids, insertIdx, groupIds, byId, field = 'sort_order') {
    const groupSet = new Set(groupIds);
    let prevVal = null;
    for (let i = insertIdx - 1; i >= 0; i--) {
      const t = byId.get(ids[i]);
      if (t && !groupSet.has(ids[i]) && t[field] != null) { prevVal = t[field]; break; }
    }
    let nextVal = null;
    for (let i = insertIdx + groupIds.length; i < ids.length; i++) {
      const t = byId.get(ids[i]);
      if (t && !groupSet.has(ids[i]) && t[field] != null) { nextVal = t[field]; break; }
    }
    const lo = prevVal != null ? prevVal : (nextVal != null ? nextVal - 1 : 0);
    const hi = nextVal != null ? nextVal : (prevVal != null ? prevVal + 1 : lo + 1);
    const n = groupIds.length;
    groupIds.forEach((id, i) => {
      const t = byId.get(id);
      if (t) t[field] = lo + (hi - lo) * (i + 1) / (n + 1);
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
    return dateToStr(new Date());
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

    // Hide tasks that opted into hiding and whose plan date is still ahead
    if (activeFilter !== 'completed' && activeFilter !== 'hidden') {
      filtered = filtered.filter(t => !isDeferred(t, today));
    }

    // Tab filter
    if (activeFilter === 'focus') {
      const today = todayStr();
      const byId = new Map(tasks.map(t => [t.id, t]));
      filtered = filtered.filter(t => !t.completed_at && !isBlocked(t, byId) && (
        (t.focus_date && t.focus_date <= today) || (t.due_date && t.due_date <= today)
      ));
    } else if (activeFilter === 'due') {
      // Upcoming is a day board: show all open tasks grouped by day. Scheduled
      // tasks (due_date/focus_date) land on their day; unplanned ones go to the
      // Someday section so they can be dragged into the week.
      filtered = filtered.filter(t => !t.completed_at);
    } else if (activeFilter === 'completed') {
      filtered = filtered.filter(t => !!t.completed_at);
    } else if (activeFilter === 'hidden') {
      filtered = filtered.filter(t => isDeferred(t, today));
    } else {
      // "open" (Backlog) shows non-completed by default
      filtered = filtered.filter(t => !t.completed_at);
    }

    // Tag filter. Comma-separated = AND (all must match) — the narrowing
    // default; pipe-separated = OR (any match). e.g. "a,b" needs both tags,
    // "a|b" needs either.
    const rawTag = filterTag.value.trim().toLowerCase();
    if (rawTag) {
      const orMode = rawTag.includes('|');
      const terms = rawTag.split(orMode ? '|' : ',').map(s => s.trim()).filter(Boolean);
      if (terms.length) {
        filtered = filtered.filter(t => {
          const tags = (t.tags || []).map(tg => tg.toLowerCase());
          return orMode
            ? terms.some(term => tags.includes(term))
            : terms.every(term => tags.includes(term));
        });
      }
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
    focus: (a, b) => {
      // Full manual ordering via focus_order (independent of backlog sort_order)
      if (a.focus_order != null && b.focus_order != null) return a.focus_order - b.focus_order;
      if (a.focus_order != null) return -1;
      if (b.focus_order != null) return 1;
      // Fallback for tasks without a focus_order
      if (a.due_date && b.due_date) return a.due_date.localeCompare(b.due_date);
      if (a.due_date) return -1;
      if (b.due_date) return 1;
      if (a.focus_date && b.focus_date) return a.focus_date.localeCompare(b.focus_date);
      // Final fallback: inherit backlog/plan priority so tasks land in Focus in the
      // same relative order they were given in the Backlog or Plan tab.
      if (a.sort_order != null && b.sort_order != null) return a.sort_order - b.sort_order;
      if (a.sort_order != null) return -1;
      if (b.sort_order != null) return 1;
      return 0;
    },
    due:       (a, b) => (a.due_date || a.focus_date || '').localeCompare(b.due_date || b.focus_date || ''),
    completed: (a, b) => (b.completed_at || '').localeCompare(a.completed_at || ''),
    // Deferred tab: soonest reveal first — the date each task is hidden until.
    hidden: (a, b) => (a.focus_date || a.due_date || '').localeCompare(b.focus_date || b.due_date || ''),
  };

  function sortTasks(tasksArr) {
    const fixed = TAB_SORT[activeFilter];
    if (fixed) return [...tasksArr].sort(fixed);

    const mode = sortSelect.value;
    const sorted = [...tasksArr];
    if (mode === 'manual') {
      sorted.sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0));
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
        .map(tag => ({ tag, score: tagMatchScore(partial, tag) }))
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
          refreshOtherInstances(instanceId);
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

  // Day-board grouping lives in DeckCore; bind it to the current date here.
  function boardBuckets(list) {
    return window.DeckCore.boardBuckets(list, todayStr());
  }

  // Custom in-page calendar popup, anchored at `anchor`. Calls cb with the
  // chosen YYYY-MM-DD, or null if the user clicks Clear. Dismissing without a
  // choice (Escape / outside click) never calls cb. We build our own calendar
  // instead of <input type=date> because Firefox's native picker fires `change`
  // on month navigation, committing a date before the user picks a day.
  // `min` (YYYY-MM-DD) restricts the earliest selectable date.
  // `parent` is where the popup is appended — defaults to document.body, but
  // callers inside a <dialog> (the edit modal) must pass the dialog element so
  // the popup renders in the same top layer instead of behind the modal.
  function openDatePicker(current, cb, anchor, min, parent = document.body) {
    // Only one picker at a time.
    document.querySelectorAll('.deck-datepicker').forEach(el => el.remove());

    const WEEKDAYS = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'];
    const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
      'July', 'August', 'September', 'October', 'November', 'December'];
    const today = todayStr();
    // View month starts on the current value, else today.
    const anchorDate = new Date((current || today) + 'T00:00:00');
    let viewY = anchorDate.getFullYear();
    let viewM = anchorDate.getMonth();

    const pop = document.createElement('div');
    pop.className = 'deck-datepicker';
    const r = anchor ? anchor.getBoundingClientRect() : { left: 20, bottom: 20 };
    // The edit modal centers itself with transform: translate(), which makes
    // position:fixed resolve against the modal's box rather than the viewport.
    // Offset the anchor's viewport coords by the parent's rect so the popup
    // still lands directly under the trigger. (For document.body, pr is 0,0.)
    const pr = (parent && parent !== document.body)
      ? parent.getBoundingClientRect() : { left: 0, top: 0 };
    pop.style.cssText = `position:fixed;left:${Math.round(r.left - pr.left)}px;top:${Math.round(r.bottom - pr.top + 4)}px;z-index:1000;`;
    parent.appendChild(pop);

    let done = false;
    function cleanup() {
      if (done) return;
      done = true;
      pop.remove();
      document.removeEventListener('keydown', onKey, true);
      document.removeEventListener('mousedown', onOutside, true);
    }
    function commit(val) { const c = cb; cleanup(); c(val); }
    function onKey(e) { if (e.key === 'Escape') { e.stopPropagation(); cleanup(); } }
    function onOutside(e) { if (!pop.contains(e.target) && e.target !== anchor) cleanup(); }

    function draw() {
      const first = new Date(viewY, viewM, 1);
      // Monday-first offset (JS getDay: 0=Sun).
      const lead = (first.getDay() + 6) % 7;
      const daysInMonth = new Date(viewY, viewM + 1, 0).getDate();

      let cells = '';
      for (let i = 0; i < lead; i++) cells += '<span class="dp-day dp-blank"></span>';
      for (let d = 1; d <= daysInMonth; d++) {
        const ds = `${viewY}-${String(viewM + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
        const cls = ['dp-day'];
        if (ds === current) cls.push('dp-selected');
        if (ds === today) cls.push('dp-today');
        const disabled = min && ds < min;
        if (disabled) cls.push('dp-disabled');
        cells += `<button type="button" class="${cls.join(' ')}"${disabled ? ' disabled' : ''} data-date="${ds}">${d}</button>`;
      }

      pop.innerHTML =
        `<div class="dp-head">` +
          `<button type="button" class="dp-nav" data-nav="-1">‹</button>` +
          `<span class="dp-title">${MONTHS[viewM]} ${viewY}</span>` +
          `<button type="button" class="dp-nav" data-nav="1">›</button>` +
        `</div>` +
        `<div class="dp-weekdays">${WEEKDAYS.map(w => `<span>${w}</span>`).join('')}</div>` +
        `<div class="dp-grid">${cells}</div>` +
        `<div class="dp-foot">` +
          `<button type="button" class="dp-today-btn">Today</button>` +
          `<button type="button" class="dp-clear-btn">Clear</button>` +
        `</div>`;
    }

    pop.addEventListener('click', e => {
      const nav = e.target.closest('[data-nav]');
      if (nav) {
        viewM += Number(nav.dataset.nav);
        if (viewM < 0) { viewM = 11; viewY--; }
        else if (viewM > 11) { viewM = 0; viewY++; }
        draw();
        return;
      }
      if (e.target.classList.contains('dp-today-btn')) {
        if (!(min && today < min)) commit(today);
        return;
      }
      if (e.target.classList.contains('dp-clear-btn')) { commit(null); return; }
      const day = e.target.closest('.dp-day[data-date]');
      if (day && !day.disabled) commit(day.dataset.date);
    });

    draw();
    document.addEventListener('keydown', onKey, true);
    // Defer outside-click wiring so the opening click doesn't immediately close it.
    setTimeout(() => document.addEventListener('mousedown', onOutside, true), 0);
  }

  function render() {
    sortSelect.style.display = TAB_SORT[activeFilter] ? 'none' : '';

    const filtered = filterTasks();

    // Index tasks once per render — taskHTML runs per card and would otherwise
    // scan the whole task array for every blocked-by/blocking lookup.
    const byId = new Map(tasks.map(t => [t.id, t]));
    const blockingIndex = new Map(); // blocker id → open tasks it blocks
    for (const t of tasks) {
      if (t.completed_at || !t.blocked_by) continue;
      for (const bid of t.blocked_by) {
        if (!blockingIndex.has(bid)) blockingIndex.set(bid, []);
        blockingIndex.get(bid).push(t);
      }
    }

    // In "open" (Backlog) view split into: due now (pinned top), ordered, new (inbox)
    let dueTasks, orderedTasks, newTasks;
    if (activeFilter === 'open') {
      const today = todayStr();
      const isDueNow = t => t.due_date && t.due_date <= today;
      dueTasks = filtered.filter(isDueNow)
                         .sort((a, b) => a.due_date.localeCompare(b.due_date));
      const rest = filtered.filter(t => !isDueNow(t));
      orderedTasks = sortTasks(rest.filter(t => t.sort_order != null));
      newTasks   = rest.filter(t => t.sort_order == null)
                       .sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
    } else if (activeFilter === 'focus') {
      dueTasks     = [];
      orderedTasks = sortTasks(filtered.filter(t => t.focus_order != null));
      newTasks     = filtered.filter(t => t.focus_order == null)
                             .sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
    } else {
      dueTasks     = [];
      orderedTasks = sortTasks(filtered);
      newTasks     = [];
    }

    if (activeFilter !== 'due' &&
        dueTasks.length === 0 && orderedTasks.length === 0 && newTasks.length === 0) {
      listEl.innerHTML = '<li class="tasks-empty">No tasks here.</li>';
      return;
    }

    const taskHTML = (task, draggable = (activeFilter === 'focus' || (!TAB_SORT[activeFilter] && sortSelect.value === 'manual')), extraClass = '') => {
      const checked = task.completed_at ? 'checked' : '';
      const completedClass = (task.completed_at ? 'task-completed' : '') + (task.wont_do ? ' task-wont-do' : '')
        + (!task.completed_at && isBlocked(task, byId) ? ' task-blocked-item' : '');
      const dueClass = dueDateClass(task);
      const urgClass = urgencyClass(task);
      const tags = (task.tags || []).map(t => `<span class="task-tag">#${esc(t)}</span>`).join('');
      const tagsHtml = tags ? `<div class="task-tags-right">${tags}</div>` : '';
      const recIcon = task.recurrence ? '<span class="task-recurring" title="Recurring">↻</span>' : '';
      const focusToday = task.focus_date === dateOffset(0);
      const focusTmrw  = task.focus_date === dateOffset(1);
      const focusMon   = task.focus_date === nextMonday();
      const focusClass = focusToday ? ' task-marked-today' : focusTmrw ? ' task-marked-tmrw' : focusMon ? ' task-marked-mon' : '';
      const focusMetaClass = focusToday ? 'focus-today' : focusTmrw ? 'focus-tmrw' : focusMon ? 'focus-mon' : '';
      const dueDisplay = !task.completed_at
        ? `<span class="task-due task-clickable-date ${dueClass}${task.due_date ? '' : ' task-date-empty'}" data-id="${task.id}" data-field="due_date" title="${task.due_date ? 'Click to change due date' : 'Set due date'}">⏰${task.due_date ? ' ' + formatDate(task.due_date) : ''}</span>`
        : (task.due_date ? `<span class="task-due ${dueClass}">⏰ ${formatDate(task.due_date)}</span>` : '');
      // Past plans show their age instead of a date, so a stale Focus working
      // set is visible at a glance (stale styling from STALE_DAYS on).
      const focusAge = (!task.completed_at && task.focus_date && task.focus_date < todayStr())
        ? Math.round((new Date(todayStr() + 'T00:00:00') - new Date(task.focus_date + 'T00:00:00')) / 86400000)
        : 0;
      const focusLabel = task.focus_date
        ? (focusAge > 0 ? `planned ${focusAge}d ago` : formatDate(task.focus_date)) : '';
      const staleClass = focusAge >= STALE_DAYS ? ' task-focus-stale' : '';
      const focusDisplay = !task.completed_at
        ? `<span class="task-date-info task-focus-info task-clickable-date ${focusMetaClass}${staleClass}${task.focus_date ? '' : ' task-date-empty'}" data-id="${task.id}" data-field="focus_date" title="${task.focus_date ? 'Click to change plan date' : 'Set plan date'}">📅${focusLabel ? ' ' + focusLabel : ''}</span>`
        : (task.focus_date ? `<span class="task-date-info task-focus-info ${focusMetaClass}">📅 ${formatDate(task.focus_date)}</span>` : '');
      // Opt-in hiding: badge the date the task stays out of Backlog/Plan until.
      const hideUntil = task.hide_until_plan ? (task.focus_date || task.due_date) : null;
      const startDisplay = (hideUntil && !task.completed_at)
        ? `<span class="task-date-info">🙈 hidden until ${formatDate(hideUntil)}</span>` : '';
      const deferDaysDisplay = task.defer_days != null ? `<span class="task-date-info">${task.defer_days === 0 ? 'plan on due date' : `plan ${task.defer_days}d before due`}</span>` : '';
      const completedDisplay = task.completed_at
        ? (task.wont_do
            ? `<span class="task-date-info task-wontdo-badge">✗ won't do</span>`
            : `<span class="task-date-info">done: ${task.completed_at.slice(0, 10)}</span>`)
        : '';
      const pageDisplay = task.page ? `<a href="/page/${esc(task.page)}" class="task-page-link">📄 ${esc(task.page)}</a>` : '';

      // Blocked by: show full title, click to open modal
      let blockedDisplay = '';
      if (task.blocked_by && task.blocked_by.length) {
        const links = task.blocked_by.map(bid => {
          const blocker = byId.get(bid);
          const title = blocker ? esc(blocker.title) : bid.slice(0, 6);
          return `<a href="#" class="dep-link" data-id="${bid}">${title}</a>`;
        }).join(' ');
        blockedDisplay = `<span class="task-blocked">🚫 blocked by ${links}</span>`;
      }

      // Blocking: show full title, click to open modal
      let blockingDisplay = '';
      const blocking = blockingIndex.get(task.id) || [];
      if (blocking.length) {
        const links = blocking.map(t => {
          return `<a href="#" class="dep-link" data-id="${t.id}">${esc(t.title)}</a>`;
        }).join(' ');
        blockingDisplay = `<span class="task-blocking">⛓ blocking ${links}</span>`;
      }

      const tagsRight = tags ? `<span class="task-tags-right">${tags}</span>` : '';
      const hasRealMeta = !!(tags || task.due_date || task.focus_date || task.recurrence ||
        task.page || (task.blocked_by && task.blocked_by.length) || blocking.length ||
        task.hide_until_plan || task.defer_days != null || task.completed_at);
      const hasMeta = !task.completed_at || dueDisplay || focusDisplay || startDisplay || deferDaysDisplay || completedDisplay || recIcon || pageDisplay || blockedDisplay || blockingDisplay || tagsRight;
      const metaClass = (hasMeta && !hasRealMeta) ? 'task-meta task-meta--hover-only' : 'task-meta';
      const metaHtml = hasMeta ? `<div class="${metaClass}">${startDisplay}${deferDaysDisplay}${tagsRight}${recIcon}${pageDisplay}${blockedDisplay}${blockingDisplay}<span class="task-meta-right">${dueDisplay}${focusDisplay}${completedDisplay}</span></div>` : '';
      const descHtml = task.description ? `<div class="task-desc">${renderDesc(task.description)}</div>` : '';
      const skipBtnHtml = (!task.completed_at && task.recurrence)
        ? `<button class="task-skip-btn" data-id="${task.id}" title="Skip this occurrence (series continues)">⏭</button>`
        : '';
      const wontDoBtnHtml = !task.completed_at
        ? `<button class="task-wontdo-btn" data-id="${task.id}" title="Won't do">✗</button>`
        : '';
      const dragAttr = draggable ? 'draggable="true"' : '';
      const duePinnedAttr = draggable ? '' : 'data-due-pinned="true"';
      return `
        <li class="task-item ${completedClass} ${urgClass}${focusClass} ${extraClass}" data-id="${task.id}" ${dragAttr} ${duePinnedAttr}>
          <label class="task-checkbox">
            <input type="checkbox" ${checked} data-id="${task.id}">
          </label>
          <div class="task-content" data-id="${task.id}">
            <span class="task-title" data-id="${task.id}">${linkifyTitle(task.title)}</span>
          </div>
          <div class="task-actions">
            ${skipBtnHtml}${wontDoBtnHtml}
          </div>
          ${metaHtml}${descHtml}
        </li>`;
    };

    let html = '';
    let truncated = false;

    // Upcoming day board: one section per day, dividers double as drop targets.
    if (activeFilter === 'due') {
      html = boardBuckets(filtered).map(s => {
        const overCls = (s.date && s.tasks.length >= CAPACITY) ? ' board-day--over' : '';
        const overIcon = (s.date && s.tasks.length >= CAPACITY)
          ? `<span class="board-day-over-icon" title="Over capacity (${s.tasks.length}/${CAPACITY})">⚠</span>`
          : '';
        const dropAttr = s.droppable ? ` data-drop-bucket="${s.key}" data-drop-day="${s.date || ''}"` : '';
        const header = `<li class="board-day-divider${overCls}"${dropAttr}>` +
          `<span class="board-day-label">${esc(s.label)}</span>` +
          `${overIcon}<span class="board-day-count">${s.tasks.length}</span></li>`;
        // Empty droppable days need a real drop area — a bare divider is too
        // thin to hit. Give them a placeholder that acts as the drop target.
        const body = s.tasks.length
          ? s.tasks.map(t => taskHTML(t, true, 'board-item')).join('')
          : (s.droppable ? `<li class="board-day-empty"${dropAttr}></li>` : '');
        return header + body;
      }).join('');
      listEl.innerHTML = html;
      wireListHandlers();
      return;
    }

    // Due now section (overdue + due today) pinned at top of Open tab
    if (dueTasks.length > 0) {
      html += '<li class="task-inbox-divider task-inbox-divider--only">Due</li>';
      html += dueTasks.map(t => taskHTML(t, false)).join('');
      if (orderedTasks.length > 0 || newTasks.length > 0) html += '<li class="task-inbox-divider"></li>';
    }
    if (newTasks.length > 0) {
      html += '<li class="task-inbox-divider task-inbox-divider--only">New</li>';
      html += newTasks.map(t => taskHTML(t, undefined, 'task-inbox')).join('');
      if (orderedTasks.length > 0) html += '<li class="task-inbox-divider"></li>';
    }
    // Focus: surface stale plans (planned >= STALE_DAYS ago) with one-click clear
    if (activeFilter === 'focus') {
      const staleCount = filtered.filter(t => t.focus_date && t.focus_date <= dateOffset(-STALE_DAYS)).length;
      if (staleCount) {
        html += `<li class="focus-stale-bar">${staleCount} stale plan${staleCount > 1 ? 's' : ''}` +
          `<button class="btn btn-sm" id="focus-clear-stale">Clear</button></li>`;
      }
      if (orderedTasks.length >= CAPACITY) {
        html += `<li class="focus-stale-bar focus-over-capacity">` +
          `<span>⚠ ${orderedTasks.length} tasks in focus (limit: ${CAPACITY})</span></li>`;
      }
    }
    // Limit completed tab display
    let displayTasks = orderedTasks;
    if (activeFilter === 'completed' && orderedTasks.length > completedLimit) {
      displayTasks = orderedTasks.slice(0, completedLimit);
      truncated = true;
    }
    html += displayTasks.map(t => taskHTML(t)).join('');
    if (truncated) {
      html += `<li class="tasks-show-more"><button class="btn" id="tasks-show-more-btn">Show more (${orderedTasks.length - completedLimit} remaining)</button></li>`;
    }
    listEl.innerHTML = html;
    wireListHandlers();

    function wireListHandlers() {
    listEl.querySelector('#focus-clear-stale')?.addEventListener('click', clearStalePlans);

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
        if (completed) {
          beginComplete(id, e.target.closest('.task-item'));
        } else if (!cancelPendingComplete(id)) {
          updateTask(id, { completed: false });
          if (task) showUndo('Task reopened', () => completeTasks([id], true));
        }
      });
    });

    listEl.querySelectorAll('.task-clickable-date').forEach(el => {
      el.addEventListener('click', e => {
        e.stopPropagation();
        const id = el.dataset.id;
        const field = el.dataset.field;
        const task = tasks.find(t => t.id === id);
        if (!task) return;
        openDatePicker(task[field] || '', d => updateTask(id, { [field]: d }), el, todayStr());
      });
    });

    listEl.querySelectorAll('.task-wontdo-btn').forEach(btn => {
      btn.addEventListener('click', e => {
        e.stopPropagation();
        const id = btn.dataset.id;
        updateTask(id, { wont_do: true });
        showUndo("Marked won't do", () => updateTask(id, { wont_do: false }));
      });
    });

    listEl.querySelectorAll('.task-skip-btn').forEach(btn => {
      btn.addEventListener('click', e => {
        e.stopPropagation();
        const id = btn.dataset.id;
        updateTask(id, { skip: true });
        showUndo('Occurrence skipped', () => updateTask(id, { wont_do: false }));
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
        if (e.target.closest('a')) return;
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
    if (activeFilter === 'due') {
      setupBoardDragAndDrop();
    } else if (activeFilter === 'focus' || (!TAB_SORT[activeFilter] && sortSelect.value === 'manual')) {
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

    // Click to select (clicks on checkbox/button/link are handled separately)
    listEl.querySelectorAll('.task-item').forEach(el => {
      el.addEventListener('mousedown', e => {
        if (!e.target.closest('input, button, a')) setSelected(el.dataset.id);
      });
    });
    }  // end wireListHandlers
  }

  // String-based escape (mirrors leaf.js he()); also escapes quotes, since esc
  // output lands in attribute values. No DOM allocation — this runs many times
  // per task per render.
  function esc(str) {
    return String(str)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function linkifyTitle(text) {
    const escaped = esc(text);
    return escaped.replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" class="task-link">$1</a>');
  }

  // ── Inline markdown rendering for descriptions ─────────────────────────────

  function renderDesc(text) {
    if (!text) return '';
    let h = esc(text);
    // Wikilinks: [[page|label]] and [[page]] — slugified with the shared
    // toSlug from leaf.js so links resolve exactly like the server's.
    h = h.replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, (_, page, label) => {
      return `<a href="/page/${window.toSlug(page)}">${label}</a>`;
    });
    h = h.replace(/\[\[([^\]]+)\]\]/g, (_, page) => {
      return `<a href="/page/${window.toSlug(page)}">${esc(page.trim())}</a>`;
    });
    // Markdown links: [label](url)
    h = h.replace(/\[([^\]]*)\]\(([^)]+)\)/g, '<a href="$2">$1</a>');
    // Bare URLs (skip those already inside href="..." or anchor text)
    h = h.replace(/(?<![">])(https?:\/\/[^\s<]+)/g, '<a href="$1" class="task-link">$1</a>');
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
    const coords = window.getCaretCoords(ta);  // shared caret-mirror from leaf.js
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

  // ── URL paste handling for description textarea ────────────────────────────
  // askUrlName (the paste-link modal) is shared from leaf.js.

  if (descTextarea) {
    descTextarea.addEventListener('paste', async (e) => {
      const text = (e.clipboardData.getData('text/plain') || '').trim();
      if (!/^https?:\/\/\S+$/.test(text)) return;
      e.preventDefault();
      const start = descTextarea.selectionStart;
      const end = descTextarea.selectionEnd;
      if (start !== end) {
        // Text selected — wrap as [selection](url)
        const selected = descTextarea.value.slice(start, end);
        const md = `[${selected}](${text})`;
        descTextarea.value = descTextarea.value.slice(0, start) + md + descTextarea.value.slice(end);
        descTextarea.selectionStart = descTextarea.selectionEnd = start + md.length;
        return;
      }
      // No selection — ask for display name
      const name = await window.askUrlName(text);
      if (name === false) {
        // Cancelled — insert raw URL
        const pos = descTextarea.selectionStart;
        descTextarea.value = descTextarea.value.slice(0, pos) + text + descTextarea.value.slice(pos);
        descTextarea.selectionStart = descTextarea.selectionEnd = pos + text.length;
        return;
      }
      const md = name ? `[${name}](${text})` : text;
      const pos = descTextarea.selectionStart;
      descTextarea.value = descTextarea.value.slice(0, pos) + md + descTextarea.value.slice(pos);
      descTextarea.selectionStart = descTextarea.selectionEnd = pos + md.length;
    });
  }

  // ── Tabs ───────────────────────────────────────────────────────────────────

  tabsEl.addEventListener('change', () => {
    activeFilter = tabsEl.value;
    saveActiveTab(activeFilter);
    if (activeFilter === 'completed') fetchCompleted();
    render();
  });

  sortSelect.addEventListener('change', render);
  // Tag filter: only apply on Enter, blur, or autocomplete selection (not every keystroke)
  filterTag.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !tagAcEl) {
      e.preventDefault();
      render();
    } else if (e.key === 'Escape' && !tagAcEl) {
      e.preventDefault();
      filterTag.blur();
    }
  });
  filterTag.addEventListener('blur', () => { setTimeout(() => { render(); }, 160); });
  if (filterSearch) {
    // Debounced — render() rebuilds the whole list, too heavy per keystroke.
    let searchTimer = null;
    filterSearch.addEventListener('input', () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(render, 150);
    });
    filterSearch.addEventListener('keydown', e => {
      if (e.key === 'Escape') { e.preventDefault(); filterSearch.blur(); }
    });
  }

  // ── Tag autocomplete ───────────────────────────────────────────────────────

  let tagAcEl = null;
  let tagAcIndex = -1;

  function hideTagAc() {
    tagAcEl?.remove();
    tagAcEl = null;
    tagAcIndex = -1;
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
      filterTag.blur();
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
      .map(tag => ({ tag, score: tagMatchScore(partial, tag) }))
      .filter(({ tag, score }) => score >= 0 && !committed.includes(tag.toLowerCase()))
      .sort((a, b) => b.score - a.score || a.tag.localeCompare(b.tag))
      .map(({ tag }) => tag)
      .slice(0, 8);
  }

  function maybeShowTagAc() {
    const val = filterTag.value;
    const commaIdx = val.lastIndexOf(',');
    // Current segment, with an optional leading '#' stripped. Suggest on any
    // non-empty partial (typing '#' first is optional), or on an empty focused
    // field so the full tag menu is one keystroke away.
    const partial = (commaIdx >= 0 ? val.slice(commaIdx + 1) : val).trim().replace(/^#/, '');
    if (partial || document.activeElement === filterTag) {
      showTagAc(computeTagMatches());
    } else {
      hideTagAc();
    }
  }
  filterTag.addEventListener('input', maybeShowTagAc);
  filterTag.addEventListener('focus', maybeShowTagAc);

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
        .map(tag => ({ tag, score: tagMatchScore(partial, tag) }))
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
    // "Plan before due" (defer_days): 0 shows as "0" (plan on the due date).
    document.getElementById('task-edit-defer-days' + sfx).value = task?.defer_days ?? '';
    document.getElementById('task-edit-hide' + sfx).checked = !!task?.hide_until_plan;
    document.getElementById('task-edit-due' + sfx).value = task ? task.due_date || '' : '';
    document.getElementById('task-edit-focus' + sfx).value = task ? task.focus_date || '' : (activeFilter === 'focus' ? dateOffset(0) : '');
    document.getElementById('task-edit-tags' + sfx).value = task ? (task.tags || []).join(', ') : (defaultTags || filterTag.value.trim() || initTag || '');
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
    const recFromCb = document.getElementById('task-edit-rec-from' + sfx);
    if (recFromCb) recFromCb.checked = task?.recurrence?.from === 'completion';
    syncRecFields();
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
    syncPlanLead();
    modal.showModal();
    document.getElementById('task-edit-title' + sfx).focus();
  }

  // Relative-date helpers (used by card rendering and focus defaults).
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

  // Every N / From done are only meaningful with a repeat type — disable them
  // (rather than hide, which would shift the row) when Repeat is "—".
  function syncRecFields() {
    // Sub-controls are only active when Repeat is both enabled and set.
    const on = !recTypeSelect.disabled && !!recTypeSelect.value;
    const subHint = on ? '' : (recTypeSelect.disabled ? 'Set a due or plan date first' : 'Select a repeat type first');
    const intervalEl = document.getElementById('task-edit-rec-interval' + sfx);
    intervalEl.disabled = !on;
    intervalEl.closest('.task-field').title = subHint;
    const rf = document.getElementById('task-edit-rec-from' + sfx);
    if (rf) {
      rf.disabled = !on;
      rf.closest('.task-field').title = on ? 'Repeat from the completion date instead of the anchor date' : subHint;
    }
    const anchorLabel = document.getElementById('task-edit-rec-anchor-label' + sfx);
    if (anchorLabel) {
      const anchor = document.getElementById('task-edit-due' + sfx)?.value ? 'due date' : 'plan date';
      anchorLabel.textContent = on ? anchor : '—';
      anchorLabel.closest('.task-field').title = on ? `Each cycle advances the ${anchor}` : subHint;
    }
  }
  recTypeSelect.addEventListener('change', syncRecFields);

  // ── Modal date triggers (custom calendar popup, no native <input type=date>) ──
  // Each date field is a hidden <input> holding the value plus a visible button
  // showing it; clicking the button opens the same in-page calendar the cards use.
  function refreshDateTriggers() {
    modal.querySelectorAll('.date-trigger').forEach(btn => {
      const input = document.getElementById(btn.dataset.dateTrigger);
      btn.textContent = input && input.value ? formatDate(input.value) : (btn.dataset.placeholder || 'Set date');
      btn.classList.toggle('date-trigger--set', !!(input && input.value));
    });
  }
  modal.querySelectorAll('.date-trigger').forEach(btn => {
    btn.addEventListener('click', () => {
      const input = document.getElementById(btn.dataset.dateTrigger);
      openDatePicker(input.value || '', d => {
        input.value = d || '';
        // Picking an explicit plan date leaves lead mode (see syncPlanLead).
        if (btn.dataset.dateTrigger === 'task-edit-focus' + sfx) {
          document.getElementById('task-edit-defer-days' + sfx).value = '';
        }
        syncPlanLead();
      }, btn, todayStr(), modal);
    });
  });

  // "Plan for" (focus_date) and "Plan before due" (defer_days) both set the one
  // plan date. The lead is the single source of truth while it has a value: it
  // fills the plan date (due − lead) and locks that field so the two can't
  // silently disagree. Clearing the lead (or picking an explicit plan date,
  // above) hands control back to the plan-date field.
  //
  // Also gates Repeat on having at least one date, and auto-selects the "plan
  // date" cycle anchor when only a plan date is set.
  function syncPlanLead() {
    const leadEl  = document.getElementById('task-edit-defer-days' + sfx);
    const dueEl   = document.getElementById('task-edit-due' + sfx);
    const focusEl = document.getElementById('task-edit-focus' + sfx);
    const hintEl  = document.getElementById('task-edit-lead-hint' + sfx);
    const trigger = modal.querySelector('[data-date-trigger="task-edit-focus' + sfx + '"]');
    const hasDue = !!dueEl.value;
    const hasFocus = !!focusEl.value;
    // "Plan before due" is only meaningful relative to a due date — disable it
    // (with a hint) until one is set, rather than letting it silently do nothing.
    leadEl.disabled = !hasDue;
    if (hintEl) hintEl.textContent = hasDue ? '' : 'Set a due date first';
    const lead = leadEl.value.trim();
    const driving = lead !== '' && hasDue;
    if (driving) {
      focusEl.value = addDays(dueEl.value, -Math.max(0, parseInt(lead, 10) || 0));
    }
    if (trigger) {
      trigger.disabled = driving;
      trigger.classList.toggle('date-trigger--derived', driving);
    }
    // "Hide until planned" needs something to hide until — a plan date, or a
    // due date to fall back on. With neither, disable it rather than let it
    // silently do nothing (same treatment as "Plan before due" above).
    const hideEl = document.getElementById('task-edit-hide' + sfx);
    const hideField = document.getElementById('task-edit-hide-field' + sfx);
    if (hideEl) {
      const canHide = hasDue || hasFocus || driving;
      hideEl.disabled = !canHide;
      if (!canHide) hideEl.checked = false;
      if (hideField) {
        hideField.classList.toggle('task-field--disabled', !canHide);
        hideField.title = canHide
          ? 'Keep this out of Backlog and Plan until its plan date'
          : 'Set a due or plan date first';
      }
    }
    refreshDateTriggers();
    // Repeat requires at least one date to be meaningful.
    const noDate = !hasDue && !hasFocus;
    recTypeSelect.disabled = noDate;
    recTypeSelect.closest('.task-field').title = noDate ? 'Set a due or plan date first' : '';
    syncRecFields();
  }
  document.getElementById('task-edit-defer-days' + sfx).addEventListener('input', syncPlanLead);

  cancelBtn.addEventListener('click', () => { hideEditTagAc(); modal.close(); });

  modal.addEventListener('keydown', e => { if ((e.key === 'Enter' || e.key === 's') && (e.ctrlKey || e.metaKey)) { e.preventDefault(); e.stopPropagation(); form.requestSubmit(); } }, true);

  form.addEventListener('submit', async e => {
    e.preventDefault();
    const id = document.getElementById('task-edit-id' + sfx).value;
    const data = {
      title: document.getElementById('task-edit-title' + sfx).value.trim(),
      description: document.getElementById('task-edit-desc' + sfx).value.trim(),
      due_date: document.getElementById('task-edit-due' + sfx).value || null,
      focus_date: document.getElementById('task-edit-focus' + sfx).value || null,
      hide_until_plan: document.getElementById('task-edit-hide' + sfx).checked,
      // "Plan before due": empty → null; otherwise clamp to >= 0 (0 = on due date).
      defer_days: (() => {
        const v = document.getElementById('task-edit-defer-days' + sfx).value.trim();
        return v === '' ? null : Math.max(0, parseInt(v, 10) || 0);
      })(),
      tags: document.getElementById('task-edit-tags' + sfx).value.split(',').map(t => t.trim()).filter(Boolean),
      rec_type: recTypeSelect.value || null,
      rec_interval: parseInt(document.getElementById('task-edit-rec-interval' + sfx).value) || 1,
      rec_from: document.getElementById('task-edit-rec-from' + sfx)?.checked ? 'completion' : 'due',
      rec_anchor: document.getElementById('task-edit-due' + sfx).value ? 'due' : 'plan',
      blocked_by: getLinkIds('blocked_by'),
      // The server reconciles other tasks' blocked_by from this in one write.
      blocking: getLinkIds('blocking'),
    };
    if (!data.title) { document.getElementById('task-edit-title' + sfx).focus(); return; }
    // Move to new: reset sort_order to null
    if (id && document.getElementById('task-edit-inbox' + sfx).checked) {
      data.sort_order = null;
    }
    const blockingChanged = data.blocking.length !== originalBlockingIds.length ||
      data.blocking.some(tid => !originalBlockingIds.includes(tid));
    modal.close();
    if (id) {
      await updateTask(id, data);
    } else {
      await createTask(data);
    }
    // Blocking reconciliation mutates other tasks server-side — re-fetch them.
    if (blockingChanged) await fetchDeck();
  });

  deleteBtn.addEventListener('click', async () => {
    const id = document.getElementById('task-edit-id' + sfx).value;
    if (!id) return;
    const task = tasks.find(t => t.id === id);
    modal.close();
    if (await confirmDelete(task?.title || '')) {
      const snapshot = task ? [{ ...task,
        blocking: tasks.filter(x => (x.blocked_by || []).includes(task.id)).map(x => x.id) }] : [];
      await deleteTask(id);
      showUndo('Task deleted', async () => {
        await fetch('/api/deck/restore', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tasks: snapshot }),
        });
        await fetchDeck();
        window.refreshDueBadge?.();
        refreshOtherInstances(instanceId);
      });
    }
  });

  // ── Drag and Drop ──────────────────────────────────────────────────────────

  // Upcoming board: make cards draggable; the list-level dragover/drop handlers
  // (attached once at init) re-plan the task to the day section it's dropped on,
  // or reorder within a bucket when the drop target is in the same day section.
  function setupBoardDragAndDrop() {
    const byId = new Map(tasks.map(tk => [tk.id, tk]));

    function executeBoardItemDrop(targetItem) {
      const targetId = targetItem.dataset.id;
      if (!draggedId || targetId === draggedId) return;
      const draggedEl = listEl.querySelector(`.task-item[data-id="${CSS.escape(draggedId)}"]`);
      const draggedDiv = draggedEl && boardDivider(draggedEl);
      const targetDiv = boardDivider(targetItem);
      if (draggedDiv && targetDiv && draggedDiv === targetDiv) {
        // Same bucket: reorder within the day section using sort_order.
        const bucketIds = Array.from(listEl.querySelectorAll('.task-item'))
          .filter(el => boardDivider(el) === draggedDiv)
          .map(el => el.dataset.id);
        const fromIdx = bucketIds.indexOf(draggedId);
        const toIdx   = bucketIds.indexOf(targetId);
        if (fromIdx === -1 || toIdx === -1) return;
        bucketIds.splice(fromIdx, 1);
        bucketIds.splice(toIdx, 0, draggedId);
        interpolateOrder(bucketIds, toIdx, [draggedId], byId, 'sort_order');
        const draggedTask = byId.get(draggedId);
        reorderUpdates([{ id: draggedId, sort_order: draggedTask.sort_order }], 'sort_order');
        render();
      } else {
        // Different bucket: replan to the target's day (same logic as divider drop).
        clearBoardDropOver();
        if (!targetDiv || !targetDiv.dataset.dropBucket) return;
        const id = draggedId;
        if (targetDiv.dataset.dropBucket === 'someday') updateTask(id, { focus_date: null });
        else if (targetDiv.dataset.dropBucket === 'later') {
          openDatePicker('', d => { if (d) updateTask(id, { focus_date: d }); }, targetDiv, dateOffset(7));
        } else if (targetDiv.dataset.dropDay) {
          updateTask(id, { focus_date: targetDiv.dataset.dropDay });
        }
      }
    }

    listEl.querySelectorAll('.task-item[draggable]').forEach(item => {
      item.addEventListener('dragstart', e => {
        draggedId = item.dataset.id;
        item.classList.add('task-dragging');
        e.dataTransfer.effectAllowed = 'move';
      });
      item.addEventListener('dragend', () => {
        item.classList.remove('task-dragging');
        draggedId = null;
        clearBoardDropOver();
        listEl.querySelectorAll('.task-drag-over').forEach(el => el.classList.remove('task-drag-over'));
      });
      item.addEventListener('dragover', e => {
        if (!draggedId) return;
        e.preventDefault();
        e.stopPropagation();  // suppress list-level bucket-highlight when over a task item
        e.dataTransfer.dropEffect = 'move';
        if (item.dataset.id !== draggedId) item.classList.add('task-drag-over');
      });
      item.addEventListener('dragleave', () => item.classList.remove('task-drag-over'));
      item.addEventListener('drop', e => {
        e.preventDefault();
        e.stopPropagation();  // prevent list-level handler from also firing
        item.classList.remove('task-drag-over');
        executeBoardItemDrop(item);
      });
    });
  }

  function setupDragAndDrop() {
    function executeDrop(targetId) {
      if (!draggedId || targetId === draggedId) return;
      const ids = Array.from(listEl.querySelectorAll('.task-item:not([data-due-pinned])'))
                       .map(el => el.dataset.id);
      const fromIdx = ids.indexOf(draggedId);
      const toIdx = ids.indexOf(targetId);
      if (fromIdx === -1 || toIdx === -1) return;
      const byId = new Map(tasks.map(tk => [tk.id, tk]));
      const draggedTask = byId.get(draggedId);
      const wasNew = activeFilter === 'focus'
        ? draggedTask && draggedTask.focus_order == null
        : draggedTask && draggedTask.sort_order == null;
      ids.splice(fromIdx, 1);
      // When dragging from new into ordered, insert BEFORE target (adjust index).
      // Within-section reorder uses standard behavior (no adjustment).
      const insertIdx = (wasNew && fromIdx < toIdx) ? toIdx - 1 : toIdx;
      ids.splice(insertIdx, 0, draggedId);
      const field = activeFilter === 'focus' ? 'focus_order' : 'sort_order';
      if (hasNarrowingFilter()) {
        interpolateOrder(ids, insertIdx, [draggedId], byId, field);
        reorderUpdates([{ id: draggedId, [field]: draggedTask[field] }], field);
      } else {
        // Only assign the field to items that already had one, plus the dragged item
        ids.forEach((tid, i) => {
          const t = byId.get(tid);
          if (t && (t[field] != null || t.id === draggedId)) t[field] = i;
        });
        const orderedIds = ids.filter(tid => byId.get(tid)?.[field] != null);
        reorder(orderedIds, field);
      }
      render();
    }

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
        executeDrop(item.dataset.id);
      });
    });

    // Section dividers are also drop targets — dropping on one inserts the
    // dragged task before the first task-item that follows the divider.
    listEl.querySelectorAll('.task-inbox-divider:not(.task-inbox-divider--only)').forEach(div => {
      div.addEventListener('dragover', e => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        div.classList.add('task-drag-over');
      });
      div.addEventListener('dragleave', () => div.classList.remove('task-drag-over'));
      div.addEventListener('drop', e => {
        e.preventDefault();
        div.classList.remove('task-drag-over');
        let next = div.nextElementSibling;
        while (next && !next.matches('.task-item:not([data-due-pinned])')) next = next.nextElementSibling;
        if (next) executeDrop(next.dataset.id);
      });
    });
  }

  // ── Keyboard navigation ────────────────────────────────────────────────────

  function reorderSelected(direction) {
    if (activeFilter !== 'focus' && sortSelect.value !== 'manual' || !selectedIds.size) return;
    const items = visibleItems();
    const ids = items.map(el => el.dataset.id);
    const byId = new Map(tasks.map(tk => [tk.id, tk]));

    if (selectedIds.size === 1) {
      const idx = ids.indexOf(selectedId);
      if (idx === -1) return;
      const selTask = byId.get(selectedId);

      if (direction === 'down') {
        if (idx === ids.length - 1) return;
        if (selTask && selTask.sort_order == null) {
          // Moving new item into ordered: place at top of ordered section
          ids.splice(idx, 1);
          const firstOrd = ids.findIndex(id => byId.get(id)?.sort_order != null);
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
    const field = activeFilter === 'focus' ? 'focus_order' : 'sort_order';
    if (hasNarrowingFilter()) {
      const groupIds = ids.filter(id => selectedIds.has(id));
      const insertIdx = ids.findIndex(id => selectedIds.has(id));
      interpolateOrder(ids, insertIdx, groupIds, byId, field);
      reorderUpdates(groupIds.map(id => ({ id, [field]: byId.get(id)[field] })), field);
    } else {
      ids.forEach((tid, i) => {
        const t = byId.get(tid);
        if (t && (t[field] != null || selectedIds.has(tid))) t[field] = i;
      });
      const orderedIds = ids.filter(tid => byId.get(tid)?.[field] != null);
      reorder(orderedIds, field);
    }
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

    // '/' focuses search, '#' focuses tag filter — even from non-input context
    if (e.key === '/' && !e.ctrlKey && !e.metaKey && !focus?.closest('input, textarea, select, [contenteditable]')) {
      e.preventDefault();
      filterSearch?.focus();
      return;
    }
    if (e.key === '#' && !e.ctrlKey && !e.metaKey && !focus?.closest('input, textarea, select, [contenteditable]')) {
      e.preventDefault();
      if (filterTag) { filterTag.focus(); filterTag.value = '#'; filterTag.dispatchEvent(new Event('input')); }
      return;
    }

    if (focus && focus.closest('input, textarea, select, [contenteditable]')) return;

    const items = visibleItems();
    const currentIdx = selectedId ? items.findIndex(el => el.dataset.id === selectedId) : -1;

    // 'n' opens new task modal
    if (e.key === 'n' && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      openEditModal(null);
      return;
    }

    // 'f' / 'F' — plan selected task for today / tomorrow
    if ((e.key === 'f' || e.key === 'F') && !e.ctrlKey && !e.metaKey && !e.altKey && selectedId) {
      e.preventDefault();
      const task = tasks.find(t => t.id === selectedId);
      if (task && !task.completed_at) {
        const target = e.key === 'f' ? dateOffset(0) : dateOffset(1);
        updateTask(task.id, { focus_date: task.focus_date === target ? null : target });
      }
      return;
    }

    // Tab/Shift+Tab cycles the view selector
    if (e.key === 'Tab') {
      e.preventDefault();
      const count = tabsEl.options.length;
      const activeIdx = tabsEl.selectedIndex;
      tabsEl.selectedIndex = e.shiftKey
        ? (activeIdx > 0 ? activeIdx - 1 : count - 1)
        : (activeIdx < count - 1 ? activeIdx + 1 : 0);
      tabsEl.dispatchEvent(new Event('change'));
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
        if (completing) {
          if (pendingCompletions.has(task.id)) {
            cancelPendingComplete(task.id);
          } else {
            const li = listEl.querySelector(`.task-item[data-id="${CSS.escape(task.id)}"]`);
            beginComplete(task.id, li);
          }
        } else {
          updateTask(task.id, { completed: false });
          showUndo('Task reopened', () => completeTasks([task.id], true));
        }
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
        // Snapshot deleted tasks for undo, including reverse dependency edges
        // (deletion strips this id from other tasks' blocked_by).
        const deleted = ids.map(id => tasks.find(t => t.id === id)).filter(Boolean)
          .map(t => ({ ...t, blocking: tasks.filter(x => (x.blocked_by || []).includes(t.id)).map(x => x.id) }));
        const next = items.find(el => !selectedIds.has(el.dataset.id) &&
                       items.indexOf(el) > currentIdx)
                  || items.find(el => !selectedIds.has(el.dataset.id));
        selectedId = next?.dataset.id || null;
        selectedIds = new Set(selectedId ? [selectedId] : []);
        anchorId = selectedId;
        await deleteTasks(ids);
        const label = count === 1 ? 'Task deleted' : `${count} tasks deleted`;
        // Undo restores the full snapshots server-side (same ids, all fields).
        showUndo(label, async () => {
          await fetch('/api/deck/restore', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tasks: deleted }),
          });
          await fetchDeck();
          window.refreshDueBadge?.();
          refreshOtherInstances(instanceId);
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

  // Register this instance so writes in sibling embeds can refresh it.
  deckInstances.push({ id: instanceId, refresh, openEdit: openEditModal });

  // Quick-add (from any page) refreshes every embed on this page, not just the
  // last-initialized one. Both hooks reference the shared registry, so the fact
  // that each instance reassigns them is harmless.
  window.refreshDeck = refreshAllInstances;
  window.openDeckEdit = openEditModal;

  // ── Init ───────────────────────────────────────────────────────────────────

  // Apply URL-param pre-filtering
  if (initTag && filterTag) filterTag.value = initTag;
  const initSearch = urlParams.get('search') || '';
  if (initSearch && filterSearch) filterSearch.value = initSearch;
  // Precedence: URL ?filter= > embed filter (always wins when specified) >
  // tab remembered this session > Backlog default. So a [[deck filter=…]] embed
  // always opens on its configured tab; a plain [[deck]] remembers the tab the
  // user last clicked across refreshes within the same browser tab.
  // The canonical tab names are the <option> values (focus/due/open/hidden/
  // completed); this map mirrors deck.py's _FILTER_ALIASES for friendly names
  // in ?filter= URLs. The embed filter arrives already normalized by the server.
  const FILTER_ALIASES = {
    focus: 'focus', today: 'focus',
    backlog: 'open', open: 'open',
    plan: 'due', upcoming: 'due', due: 'due',
    deferred: 'hidden', hidden: 'hidden',
    completed: 'completed', done: 'completed',
  };
  const restoreFilter = FILTER_ALIASES[urlFilter] || embedFilter || loadActiveTab() || 'open';
  if (restoreFilter && tabsEl) {
    const hasOption = Array.from(tabsEl.options).some(o => o.value === restoreFilter);
    if (hasOption) {
      tabsEl.value = restoreFilter;
      activeFilter = restoreFilter;
    }
  }

  fetchDeck().then(() => {
    if (activeFilter === 'completed') fetchCompleted();
  });

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
