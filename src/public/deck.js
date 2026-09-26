// ── Leaf Task App ────────────────────────────────────────────────────────────

(function () {
  'use strict';

  // Pure task logic lives in deck-core.js (loaded before this file).
  const { dateToStr, addDays, isUnplanned, isFocus, tagCounts, rankTags, sanitizeTagInput, parseFilter, matchesFilter, tagParamToQuery, parseQuickAdd } = window.DeckCore;

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

  // A deck page can stay open for days (phones keep their tabs). Coming back
  // to it — the tab shown again, or the page restored from the back/forward
  // cache — reloads every embed, so tasks changed elsewhere appear and the
  // day sections are computed for today, not the day the page was loaded.
  function refreshStaleInstances() {
    deckInstances.forEach(inst => inst.refreshIfIdle());
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshStaleInstances(); });
  window.addEventListener('pageshow', e => { if (e.persisted) refreshStaleInstances(); });

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
  const filterSearch = document.getElementById('task-filter-search' + sfx);
  const unplannedEl = document.getElementById('task-unplanned' + sfx);
  const unplannedWrap = document.getElementById('task-unplanned-wrap' + sfx);
  const completedLink = document.getElementById('task-completed-link' + sfx);
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
  let unplannedOnly = false;  // Open tab's "Unplanned only" — resolved during init

  // Plan day board: dragging a task onto a day section sets its start_date;
  // dropping on "Unplanned" clears it. These list-level
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
    dropOnBucket(draggedId, div);
  });
  // Move a task to a board section: a day sets its start date, Unplanned
  // clears it, and Later asks for a date.
  function dropOnBucket(id, div) {
    clearBoardDropOver();
    if (div.dataset.dropBucket === 'unplanned') updateTask(id, { start_date: null });
    else if (div.dataset.dropBucket === 'later') {
      // "Later" is a range, not a day — ask which date (min: beyond this week).
      openDatePicker('', d => { if (d) updateTask(id, { start_date: d }); }, div, dateOffset(7));
    }
    else if (div.dataset.dropDay) updateTask(id, { start_date: div.dataset.dropDay });
  }

  // Touch: a tap on a card opens the editor (double-click, the desktop
  // gesture, is unreliable on phones). Mouse clicks keep selecting cards.
  let lastPointerType = 'mouse';
  listEl.addEventListener('pointerdown', e => { lastPointerType = e.pointerType; }, true);
  listEl.addEventListener('click', e => {
    if (lastPointerType !== 'touch') return;
    const card = e.target.closest('.task-item');
    if (!card || e.target.closest('input, button, a, label, .task-clickable-date, .task-inline-input')) return;
    openEditModal(card.dataset.id);
  });

  // Touch drag. Phones never fire HTML5 drag events, so a long press picks a
  // card up and the finger carries it; releasing drops it through the same code
  // as a mouse drop. `touchDrop` is set by setupBoardDragAndDrop /
  // setupDragAndDrop on each render (null where the view has no dragging):
  //   target(el) → the element under the finger that would take the drop, or null
  //   drop(target) → perform the drop (draggedId is set while it runs)
  // Moving before the press completes is a scroll, and cancels the pick-up —
  // unless the move is mostly sideways on an open task: that is a swipe.
  // Swipe right past the threshold completes the task (the checkbox's grace
  // period, so a tap on the checkbox still cancels); swipe left starts it
  // today (with an undo toast). Swipes work in every view, drags only where
  // touchDrop is set.
  const LONG_PRESS_MS = 350;
  let touchDrop = null;
  let touchDrag = null;  // { item, x, y, timer, active, swiping, canSwipe, ghost, grabY, target, raf }
  function setSwipe(dx) {
    const item = touchDrag.item;
    const threshold = Math.min(110, item.offsetWidth * 0.3);
    item.style.transform = `translateX(${dx}px)`;
    item.dataset.swipe = dx > 0 ? 'complete' : 'start';
    item.classList.toggle('task-swipe-armed', Math.abs(dx) >= threshold);
  }
  function touchDropTarget(x, y) {
    const el = document.elementFromPoint(x, y);
    return el && listEl.contains(el) && touchDrop ? touchDrop.target(el) : null;
  }
  function setTouchTarget(target) {
    if (target === touchDrag.target) return;
    touchDrag.target?.classList.remove('task-drag-over', 'board-drop-over');
    touchDrag.target = target;
    target?.classList.add(target.classList.contains('board-day-divider') ? 'board-drop-over' : 'task-drag-over');
  }
  function startTouchDrag() {
    const t = touchDrag, rect = t.item.getBoundingClientRect();
    t.active = true;
    draggedId = t.item.dataset.id;
    t.item.classList.add('task-dragging');
    t.grabY = t.y - rect.top;
    t.ghost = t.item.cloneNode(true);
    t.ghost.classList.remove('task-dragging', 'selected');
    t.ghost.classList.add('task-touch-ghost');
    Object.assign(t.ghost.style, { left: rect.left + 'px', top: rect.top + 'px', width: rect.width + 'px' });
    document.body.appendChild(t.ghost);
    navigator.vibrate?.(15);
    // Near the top or bottom edge the page scrolls, so a drop target that is
    // off screen can still be reached.
    const edge = 70;
    const step = () => {
      if (!touchDrag?.active) return;
      const dy = touchDrag.y < edge ? -12 : touchDrag.y > window.innerHeight - edge ? 12 : 0;
      if (dy) {
        window.scrollBy(0, dy);
        setTouchTarget(touchDropTarget(touchDrag.x, touchDrag.y));
      }
      touchDrag.raf = requestAnimationFrame(step);
    };
    t.raf = requestAnimationFrame(step);
  }
  function endTouchDrag(drop) {
    const t = touchDrag;
    touchDrag = null;
    if (!t) return;
    clearTimeout(t.timer);
    if (t.swiping) {
      const armed = t.item.classList.contains('task-swipe-armed');
      const action = t.item.dataset.swipe;
      t.item.style.transform = '';
      t.item.classList.remove('task-swipe-armed');
      delete t.item.dataset.swipe;
      if (drop && armed && action === 'complete') beginComplete(t.item.dataset.id, t.item);
      if (drop && armed && action === 'start') setStart(t.item.dataset.id, todayStr());
      return;
    }
    if (!t.active) return;
    cancelAnimationFrame(t.raf);
    t.ghost.remove();
    t.item.classList.remove('task-dragging');
    const target = t.target;
    target?.classList.remove('task-drag-over', 'board-drop-over');
    if (drop && target && touchDrop) touchDrop.drop(target);
    draggedId = null;
  }
  listEl.addEventListener('touchstart', e => {
    endTouchDrag(false);
    if (e.touches.length !== 1) return;
    const item = e.target.closest('.task-item');
    if (!item || e.target.closest('input, button, a, .task-clickable-date, .task-inline-input')) return;
    const canDrag = !!touchDrop && item.matches('[draggable]');
    const canSwipe = !tasks.find(t => t.id === item.dataset.id)?.completed_at;
    if (!canDrag && !canSwipe) return;
    const p = e.touches[0];
    touchDrag = { item, x: p.clientX, y: p.clientY, active: false, swiping: false, canSwipe,
                  target: null, timer: canDrag ? setTimeout(startTouchDrag, LONG_PRESS_MS) : null };
  }, { passive: true });
  listEl.addEventListener('touchmove', e => {
    if (!touchDrag) return;
    const p = e.touches[0];
    const dx = p.clientX - touchDrag.x, dy = p.clientY - touchDrag.y;
    if (touchDrag.swiping) {
      e.preventDefault();
      setSwipe(dx);
      return;
    }
    if (!touchDrag.active) {
      const sideways = Math.abs(dx) > 1.5 * Math.abs(dy);
      if (touchDrag.canSwipe && sideways && Math.abs(dx) > 10) {
        clearTimeout(touchDrag.timer);
        touchDrag.swiping = true;
        e.preventDefault();
        setSwipe(dx);
      } else if (!sideways && Math.hypot(dx, dy) > 10) {
        endTouchDrag(false);  // a vertical move: the page scrolls
      }
      return;
    }
    e.preventDefault();  // the finger is carrying a card, not scrolling the page
    touchDrag.x = p.clientX;
    touchDrag.y = p.clientY;
    touchDrag.ghost.style.top = (p.clientY - touchDrag.grabY) + 'px';
    setTouchTarget(touchDropTarget(p.clientX, p.clientY));
  }, { passive: false });
  listEl.addEventListener('touchend', e => {
    // A finished drag or swipe must not also count as a tap (which opens the editor).
    if (touchDrag?.active || touchDrag?.swiping) e.preventDefault();
    endTouchDrag(true);
  });
  listEl.addEventListener('touchcancel', () => endTouchDrag(false));
  // The long press would otherwise open the browser's context menu.
  listEl.addEventListener('contextmenu', e => { if (touchDrag) e.preventDefault(); });

  // Claim keyboard focus when user interacts with this instance
  appEl.addEventListener('pointerdown', () => { activeDeckInstance = instanceId; });
  appEl.addEventListener('focusin', () => { activeDeckInstance = instanceId; });

  const urlParams = new URLSearchParams(window.location.search);
  const initTag = appEl?.dataset.initTag || urlParams.get('tag') || '';
  // [[deck list=…]] shows one separate list and nothing else; every other
  // embed shows only the tasks in no list. ?list= opens a list in the main
  // deck when no page embeds it (/deck redirects there).
  const initList = cleanList(appEl?.dataset.initList || urlParams.get('list'));
  function cleanList(name) {
    return String(name || '').trim().replace(/^#/, '').trim().toLowerCase()
      .replace(/\s+/g, '-').replace(/^-+|-+$/g, '');
  }
  function inScope(t) { return (t.list || '') === initList; }
  const urlFilter = urlParams.get('filter') || '';       // explicit per-navigation override
  const embedFilter = appEl?.dataset.initFilter || '';   // [[deck filter=…]] — always wins when specified
  const initNew = appEl?.dataset.initNew === '1' || urlParams.has('new');

  // Remember the active tab per page + embed instance for the current tab only
  // (sessionStorage): a refresh lands on the last-clicked tab, but a fresh load
  // in a new tab / next day starts empty so the embed's configured filter loads.
  const tabStorageKey = `leaf-deck-tab:${location.pathname}#${instanceId}`;
  function saveActiveTab(f) { try { sessionStorage.setItem(tabStorageKey, f); } catch (_) {} }
  function loadActiveTab() { try { return sessionStorage.getItem(tabStorageKey); } catch (_) { return null; } }

  // "Unplanned only" is a standing preference, not a per-visit choice like the
  // tab, so it persists across sessions (localStorage). It defaults OFF: the
  // Open tab shows every open task, and checking it narrows the list to work no
  // day has claimed yet. `[[deck unplanned=0|1]]` pins it either way for an
  // embed. The key is versioned so a changed default actually reaches anyone
  // whose browser stored the previous default (v2 defaulted on).
  const unplannedPin = appEl?.dataset.initUnplanned || '';
  const unplannedStorageKey = `leaf-deck-unplanned-v3:${location.pathname}#${instanceId}`;
  function saveUnplanned(v) { try { localStorage.setItem(unplannedStorageKey, v ? '1' : '0'); } catch (_) {} }
  function loadUnplanned() {
    try {
      const stored = localStorage.getItem(unplannedStorageKey);
      return stored === '1';
    } catch (_) { return false; }
  }

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
    tasks = (await res.json()).filter(inScope);
    render();
    refreshCompletedCount();
  }

  // Completed is not a tab: a link under the list opens it, labelled with the
  // number of completed tasks. The count comes from the server because the
  // completed tasks themselves are only loaded when the view is opened.
  let completedCount = null;
  async function refreshCompletedCount() {
    try {
      const res = await fetch('/api/deck/completed-count' + (initList ? `?list=${encodeURIComponent(initList)}` : ''));
      if (res.ok) { completedCount = (await res.json()).count; syncCompletedLink(); }
    } catch (_) {}
  }

  // Reload this instance from the server, honoring its current tab. Used to
  // pull in changes made by another embed on the same page.
  async function refresh() {
    completedLoaded = false;
    await fetchDeck();
    if (activeFilter === 'completed') await fetchCompleted();
  }

  let completedLoading = null;
  function fetchCompleted() {
    if (completedLoaded) return Promise.resolve();
    // One request at a time: the filter field asks for these on every render.
    return completedLoading ||= loadCompleted().finally(() => { completedLoading = null; });
  }
  async function loadCompleted() {
    const res = await fetch('/api/deck/completed');
    const completed = (await res.json()).filter(inScope);
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
      if (inScope(task)) tasks.push(task);
      else showNotice(listMovedNotice(task));
      render();
      window.refreshDueBadge?.(); refreshCompletedCount();
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
      if (idx !== -1) {
        if (inScope(updated)) tasks[idx] = updated;
        else { tasks.splice(idx, 1); showNotice(listMovedNotice(updated)); }
      }
      // Completion/skip/reopen of a recurring task spawns or removes an
      // instance server-side — re-fetch to pick it up.
      if (updated.recurrence && (data.completed || data.skip || 'wont_do' in data)) {
        await fetchDeck();
      } else {
        render();
      }
      window.refreshDueBadge?.(); refreshCompletedCount();
      refreshOtherInstances(instanceId);
      return updated;
    }
    await noticeFromResponse(res, 'Update rejected');
    return null;
  }

  // A task saved into another list leaves this view; say where it went.
  function listMovedNotice(task) {
    return task.list ? `Saved to list “${task.list}”.` : 'Moved to the main deck.';
  }

  async function deleteTask(id) {
    const res = await fetch(`/api/deck/${id}`, { method: 'DELETE' });
    if (res.ok) {
      tasks = tasks.filter(t => t.id !== id);
      render();
      window.refreshDueBadge?.(); refreshCompletedCount();
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
      window.refreshDueBadge?.(); refreshCompletedCount();
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
      window.refreshDueBadge?.(); refreshCompletedCount();
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

  async function afterBatchWrite() {
    await fetchDeck();
    window.refreshDueBadge?.(); refreshCompletedCount();
    refreshOtherInstances(instanceId);
  }

  // ── Card actions (shared by buttons, the ⋯ menu and the keyboard) ────────

  function markWontDo(id) {
    updateTask(id, { wont_do: true });
    showUndo("Marked won't do", () => updateTask(id, { wont_do: false }));
  }

  function skipOccurrence(id) {
    updateTask(id, { skip: true });
    showUndo('Occurrence skipped', () => updateTask(id, { wont_do: false }));
  }

  function setStart(id, startDate) {
    const prev = tasks.find(t => t.id === id)?.start_date ?? null;
    updateTask(id, { start_date: startDate });
    const msg = startDate ? `Starts ${formatDate(startDate)}` : 'Start date cleared';
    showUndo(msg, () => updateTask(id, { start_date: prev }));
  }

  // Confirm, delete, and offer undo that restores the full snapshots (ids and
  // every field). beforeDelete runs after confirmation.
  async function deleteWithUndo(ids, beforeDelete) {
    const count = ids.length;
    const title = count === 1 ? (tasks.find(t => t.id === ids[0])?.title || '') : '';
    if (!await confirmDelete(title, count)) return;
    const snapshot = ids.map(id => tasks.find(t => t.id === id)).filter(Boolean);
    beforeDelete?.();
    await (count === 1 ? deleteTask(ids[0]) : deleteTasks(ids));
    showUndo(count === 1 ? 'Task deleted' : `${count} tasks deleted`, async () => {
      await fetch('/api/deck/restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tasks: snapshot }),
      });
      await afterBatchWrite();
    });
  }

  // ── ⋯ task menu ────────────────────────────────────────────────────────────
  // Every card action in one place, reachable by tap — the hover buttons,
  // double-click and keyboard shortcuts don't exist on a phone. Renders as a
  // popover at the button on desktop and as a bottom sheet on narrow screens.

  let taskMenu = null;

  function closeTaskMenu() {
    if (!taskMenu) return;
    taskMenu.backdrop.remove();
    taskMenu.el.remove();
    document.removeEventListener('keydown', taskMenu.onKey, true);
    taskMenu.anchor?.focus({ preventScroll: true });
    taskMenu = null;
  }

  function openTaskMenu(id, anchor) {
    closeTaskMenu();
    const task = tasks.find(t => t.id === id);
    if (!task) return;
    const items = task.completed_at ? [
      ['edit', 'Edit…'],
      ['reopen', 'Reopen'],
      ['delete', 'Delete…', 'danger'],
    ] : [
      ['edit', 'Edit…'],
      ['today', 'Start today'],
      ['tomorrow', 'Start tomorrow'],
      ['pick', 'Pick start date…'],
      task.start_date && ['unplan', 'Unplan'],
      task.recurrence && ['skip', 'Skip this occurrence'],
      ['wontdo', "Won't do"],
      ['delete', 'Delete…', 'danger'],
    ].filter(Boolean);

    const backdrop = document.createElement('div');
    backdrop.className = 'task-menu-backdrop';
    const el = document.createElement('div');
    el.className = 'task-menu';
    el.setAttribute('role', 'menu');
    el.innerHTML = `<div class="task-menu-title">${esc(task.title)}</div>` + items.map(([key, label, cls]) =>
      `<button type="button" role="menuitem" data-action="${key}"${cls ? ` class="${cls}"` : ''}>${label}</button>`
    ).join('');
    document.body.append(backdrop, el);

    // Desktop popover: under the button, right-aligned, flipped up if needed.
    // (The narrow-screen bottom sheet is pure CSS and ignores these.)
    const r = anchor.getBoundingClientRect();
    const m = el.getBoundingClientRect();
    const top = r.bottom + 4 + m.height > window.innerHeight ? r.top - 4 - m.height : r.bottom + 4;
    el.style.top = `${Math.max(4, top)}px`;
    el.style.left = `${Math.max(4, Math.min(r.right - m.width, window.innerWidth - m.width - 4))}px`;

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
    taskMenu = { el, backdrop, anchor, onKey };
    el.querySelector('button')?.focus({ preventScroll: true });

    el.addEventListener('click', e => {
      const action = e.target.closest('[data-action]')?.dataset.action;
      if (!action) return;
      closeTaskMenu();
      runTaskAction(task, action, anchor);
    });
  }

  function runTaskAction(task, action, anchor) {
    const id = task.id;
    switch (action) {
      case 'edit':     openEditModal(id); break;
      case 'today':    setStart(id, dateOffset(0)); break;
      case 'tomorrow': setStart(id, dateOffset(1)); break;
      case 'unplan':   setStart(id, null); break;
      case 'pick':
        openDatePicker(task.start_date || '', d => d !== undefined && setStart(id, d), anchor, todayStr());
        break;
      case 'skip':     skipOccurrence(id); break;
      case 'wontdo':   markWontDo(id); break;
      case 'reopen':
        updateTask(id, { completed: false });
        showUndo('Task reopened', () => completeTasks([id], true));
        break;
      case 'delete':   deleteWithUndo([id]); break;
    }
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

  // True when the visible task list is a narrowed view (tag/search
  // filter) of the full Backlog rather than every open task. Manual reorders
  // done under a narrowed view must not renumber the whole visible list —
  // tasks hidden by the filter would keep their old sort_order and collide
  // with the freshly-renumbered ones once the filter is cleared.
  function hasNarrowingFilter() {
    return !!filterSearch?.value.trim();
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

  // `query` defaults to the filter field; the tag autocomplete passes the
  // field minus the word being typed.
  function filterTasks(query = filterSearch?.value || '') {
    const today = todayStr();
    let filtered = tasks;

    // Filter field: text search plus '#tag' words (DeckCore.parseFilter).
    const f = parseFilter(query);
    if (f.text || f.tagGroups.length) filtered = filtered.filter(t => matchesFilter(t, f));

    // Tab filter
    if (activeFilter === 'focus') {
      filtered = filtered.filter(t => !t.completed_at && isFocus(t, today));
    } else if (activeFilter === 'due') {
      // Plan is a day board: show all open tasks grouped by start date; tasks
      // without one go to the Unplanned section so they can be dragged into
      // the week (DeckCore.boardBuckets).
      filtered = filtered.filter(t => !t.completed_at);
    } else if (activeFilter === 'completed') {
      filtered = filtered.filter(t => !!t.completed_at);
    } else {
      // "open" (Open) shows non-completed by default. With "Unplanned only"
      // on it narrows to tasks with no start date, so the list reads as the
      // triage queue; giving a task a start date is what removes it from here.
      filtered = filtered.filter(t => !t.completed_at);
      if (unplannedOnly) filtered = filtered.filter(isUnplanned);
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
      if (a.start_date && b.start_date) return a.start_date.localeCompare(b.start_date);
      // Final fallback: inherit backlog/plan priority so tasks land in Focus in the
      // same relative order they were given in the Backlog or Plan tab.
      if (a.sort_order != null && b.sort_order != null) return a.sort_order - b.sort_order;
      if (a.sort_order != null) return -1;
      if (b.sort_order != null) return 1;
      return 0;
    },
    due:       (a, b) => (a.due_date || a.start_date || '').localeCompare(b.due_date || b.start_date || ''),
    completed: (a, b) => (b.completed_at || '').localeCompare(a.completed_at || ''),
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
    // Adding: any tag in use. Removing: only tags the selected tasks carry.
    // Enter completes a half-typed tag to the best match and applies it.
    const idSet = new Set(ids);
    noSpacesInTags(input, false);
    attachTagAc(input, {
      word: () => (input.value.trim() ? { partial: input.value.trim() } : null),
      suggest: w => rankTags(w.partial, tagCounts(removing ? tasks.filter(t => idSet.has(t.id)) : tasks)),
      accept: tag => { input.value = tag; },
      onEnter: () => submit(),
    });

    input.addEventListener('keydown', ev => {
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
      dlg.close();   // the tag dropdown lives inside dlg and goes with it
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

  // "Sep 21" (plus the year when it isn't this year), for "since …" labels.
  function shortDate(dateStr) {
    const d = new Date(dateStr + 'T00:00:00');
    const opts = { month: 'short', day: 'numeric' };
    if (d.getFullYear() !== new Date().getFullYear()) opts.year = 'numeric';
    return d.toLocaleDateString('en-US', opts);
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
    const r = anchor ? anchor.getBoundingClientRect() : { left: 20, top: 16, bottom: 20 };
    // A transformed parent makes position:fixed resolve against its box rather
    // than the viewport; offset by its rect then. (The edit modal is no longer
    // transformed, so normally this is 0,0.)
    const pr = (parent && parent !== document.body && getComputedStyle(parent).transform !== 'none')
      ? parent.getBoundingClientRect() : { left: 0, top: 0 };
    pop.style.cssText = 'position:fixed;z-index:1000;';
    parent.appendChild(pop);

    // Keep the calendar on screen. Runs after every draw(): the size is only
    // known once the month is rendered, and a 6-week month is a row taller.
    // Prefer below the anchor, else above it, else pinned inside the window;
    // horizontally left-aligned with the anchor but never past either edge.
    function place() {
      const w = pop.offsetWidth, h = pop.offsetHeight;
      const vw = window.innerWidth, vh = window.innerHeight, m = 4;
      let top = r.bottom + m;
      if (top + h > vh - m) top = r.top - m - h >= m ? r.top - m - h : Math.max(m, vh - m - h);
      const left = Math.max(m, Math.min(r.left, vw - m - w));
      pop.style.top = `${Math.round(top - pr.top)}px`;
      pop.style.left = `${Math.round(left - pr.left)}px`;
    }

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
      place();
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
    syncCompletedLink();

    const filtered = filterTasks();


    // In "open" (Open) view split into: due now (pinned top), ordered, new (inbox)
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
      if (activeFilter === 'focus') {
        listEl.innerHTML = '<li class="tasks-empty">Nothing planned for today. ' +
          '<button type="button" class="btn btn-sm" data-goto-tab="due">Plan your day</button></li>';
        listEl.querySelector('[data-goto-tab]').addEventListener('click', () => switchTab('due'));
      } else {
        listEl.innerHTML = '<li class="tasks-empty">No tasks here.</li>';
      }
      return;
    }

    const taskHTML = (task, draggable = (activeFilter === 'focus' || (!TAB_SORT[activeFilter] && sortSelect.value === 'manual')), extraClass = '') => {
      const checked = task.completed_at ? 'checked' : '';
      const completedClass = (task.completed_at ? 'task-completed' : '') + (task.wont_do ? ' task-wont-do' : '');
      const dueClass = dueDateClass(task);
      const urgClass = urgencyClass(task);
      const tags = (task.tags || []).map(t => `<span class="task-tag">#${esc(t)}</span>`).join('');
      const tagsHtml = tags ? `<div class="task-tags-right">${tags}</div>` : '';
      const recIcon = task.recurrence ? '<span class="task-recurring" title="Recurring">↻</span>' : '';
      const focusToday = task.start_date === dateOffset(0);
      const focusTmrw  = task.start_date === dateOffset(1);
      const focusMon   = task.start_date === nextMonday();
      const focusClass = focusToday ? ' task-marked-today' : focusTmrw ? ' task-marked-tmrw' : focusMon ? ' task-marked-mon' : '';
      const focusMetaClass = focusToday ? 'focus-today' : focusTmrw ? 'focus-tmrw' : focusMon ? 'focus-mon' : '';
      const dueDisplay = !task.completed_at
        ? `<span class="task-due task-clickable-date ${dueClass}${task.due_date ? '' : ' task-date-empty'}" data-id="${task.id}" data-field="due_date" title="${task.due_date ? 'Click to change due date' : 'Set due date'}">⏰${task.due_date ? ' ' + formatDate(task.due_date) : ''}</span>`
        : (task.due_date ? `<span class="task-due ${dueClass}">⏰ ${formatDate(task.due_date)}</span>` : '');
      // A past start date means "available since", not late — so it reads
      // "since Sep 21" with no warning styling.
      const focusLabel = !task.start_date ? ''
        : (!task.completed_at && task.start_date < todayStr())
          ? `since ${shortDate(task.start_date)}` : formatDate(task.start_date);
      const focusDisplay = !task.completed_at
        ? `<span class="task-date-info task-focus-info task-clickable-date ${focusMetaClass}${task.start_date ? '' : ' task-date-empty'}" data-id="${task.id}" data-field="start_date" title="${task.start_date ? 'Click to change start date' : 'Set start date'}">📅${focusLabel ? ' ' + focusLabel : ''}</span>`
        : (task.start_date ? `<span class="task-date-info task-focus-info ${focusMetaClass}">📅 ${formatDate(task.start_date)}</span>` : '');
      const completedDisplay = task.completed_at
        ? (task.wont_do
            ? `<span class="task-date-info task-wontdo-badge">✗ won't do</span>`
            : `<span class="task-date-info">done: ${task.completed_at.slice(0, 10)}</span>`)
        : '';

      const tagsRight = tags ? `<span class="task-tags-right">${tags}</span>` : '';
      const hasRealMeta = !!(tags || task.due_date || task.start_date || task.recurrence ||
        task.completed_at);
      const hasMeta = !task.completed_at || dueDisplay || focusDisplay || completedDisplay || recIcon || tagsRight;
      const metaClass = (hasMeta && !hasRealMeta) ? 'task-meta task-meta--hover-only' : 'task-meta';
      const metaHtml = hasMeta ? `<div class="${metaClass}">${tagsRight}${recIcon}<span class="task-meta-right">${dueDisplay}${focusDisplay}${completedDisplay}</span></div>` : '';
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
            ${skipBtnHtml}${wontDoBtnHtml}<button type="button" class="task-menu-btn" data-id="${task.id}" aria-label="More actions" aria-haspopup="menu" title="More actions">⋯</button>
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
        const emptyCls = s.tasks.length ? '' : ' board-day--empty';
        const header = `<li class="board-day-divider${overCls}${emptyCls}"${dropAttr}>` +
          `<span class="board-day-label">${esc(s.label)}</span>` +
          `${overIcon}<span class="board-day-count">${s.tasks.length}</span></li>`;
        // An empty section is just its header row, which is itself the drop
        // target (styled tall enough to hit). Nothing grows when a drag
        // starts, so the section you aim at never moves under the pointer.
        const body = s.tasks.map(t => taskHTML(t, true, 'board-item')).join('');
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
    if (activeFilter === 'focus') {
      if (orderedTasks.length >= CAPACITY) {
        html += `<li class="focus-stale-bar focus-over-capacity">` +
          `<span>⚠ ${orderedTasks.length} tasks in focus (limit: ${CAPACITY})</span></li>`;
      }
    }
    // No tab is highlighted in Completed, so the list says where you are.
    if (activeFilter === 'completed') {
      html += '<li class="task-inbox-divider task-inbox-divider--only">Completed</li>';
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
    listEl.querySelectorAll('.task-menu-btn').forEach(btn => {
      btn.addEventListener('click', e => {
        e.stopPropagation();
        openTaskMenu(btn.dataset.id, btn);
      });
    });

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
      btn.addEventListener('click', e => { e.stopPropagation(); markWontDo(btn.dataset.id); });
    });

    listEl.querySelectorAll('.task-skip-btn').forEach(btn => {
      btn.addEventListener('click', e => { e.stopPropagation(); skipOccurrence(btn.dataset.id); });
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
    touchDrop = null;
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

  // Pasting a URL into the notes makes a markdown link (pasteUrlAsLink,
  // shared from leaf.js, undoable).
  descTextarea?.addEventListener('paste', e => window.pasteUrlAsLink(descTextarea, e));

  // ── Tabs ───────────────────────────────────────────────────────────────────
  // Segmented control: one button per view, data-filter = the view's name.

  const tabButtons = () => Array.from(tabsEl.querySelectorAll('[data-filter]'));
  const VIEWS = ['open', 'due', 'focus', 'completed'];
  // The tab to return to from Completed (the link's "back" state).
  let lastTab = 'open';

  // While the filter field is in use, the link counts the completed tasks it
  // matches ("✓ 2 matches in Completed →"), so a search that comes up empty
  // here still shows where the task went. That needs the completed tasks,
  // which are otherwise only loaded when the view is opened. The unfiltered
  // total is never shown while filtering: it would read as the number of
  // completed tasks the filter leaves.
  function syncCompletedLink() {
    if (!completedLink) return;
    let matches = null;
    const f = parseFilter(filterSearch?.value || '');
    const filtering = activeFilter !== 'completed' && (f.text || f.tagGroups.length);
    if (filtering) {
      if (!completedLoaded) { fetchCompleted(); }
      else {
        matches = tasks.filter(t => t.completed_at && matchesFilter(t, f)).length;
      }
    }
    completedLink.classList.toggle('has-matches', matches > 0);
    completedLink.textContent = activeFilter === 'completed' ? '← Back to open tasks'
      : matches ? `✓ ${matches} ${matches === 1 ? 'match' : 'matches'} in Completed →`
      : matches === 0 ? '✓ No matches in Completed →'
      : filtering ? '✓ Completed →'
      : `✓ Completed${completedCount != null ? ` (${completedCount})` : ''} →`;
  }

  // Mark the view's button active (no side effects — used during init too).
  function markActiveTab(filter) {
    tabButtons().forEach(b => {
      const on = b.dataset.filter === filter;
      b.classList.toggle('active', on);
      b.setAttribute('aria-selected', on ? 'true' : 'false');
    });
  }

  function switchTab(filter) {
    if (!VIEWS.includes(filter)) return;
    if (filter !== 'completed') lastTab = filter;
    activeFilter = filter;
    markActiveTab(filter);   // Completed has no tab: none is marked
    syncCompletedLink();
    // Keyboard focus follows the active view (Tab-cycling from a focused tab
    // would otherwise leave the focus ring on the previous one).
    if (tabsEl.contains(document.activeElement)) {
      tabsEl.querySelector(`[data-filter="${filter}"]`)?.focus();
    }
    saveActiveTab(activeFilter);
    syncUnplannedToggle();
    if (activeFilter === 'completed') fetchCompleted();
    render();
  }

  tabsEl.addEventListener('click', e => {
    const btn = e.target.closest('[data-filter]');
    if (btn) switchTab(btn.dataset.filter);
  });
  completedLink?.addEventListener('click', () => {
    switchTab(activeFilter === 'completed' ? lastTab : 'completed');
    if (activeFilter === 'completed') tabsEl.scrollIntoView({ block: 'nearest' });
  });

  // The toggle only means anything on All — every other tab is defined by
  // dates — so it rides along with the active tab instead of always showing.
  function syncUnplannedToggle() {
    if (unplannedWrap) unplannedWrap.hidden = activeFilter !== 'open';
  }

  if (unplannedEl) {
    unplannedEl.addEventListener('change', () => {
      unplannedOnly = unplannedEl.checked;
      saveUnplanned(unplannedOnly);
      render();
    });
  }

  sortSelect.addEventListener('change', render);
  if (filterSearch) {
    // Debounced — render() rebuilds the whole list, too heavy per keystroke.
    let searchTimer = null;
    filterSearch.addEventListener('input', () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(render, 150);
    });
    // Esc leaves the field (an open tag dropdown takes the first Esc).
    filterSearch.addEventListener('keydown', e => {
      if (e.key === 'Escape') { e.preventDefault(); filterSearch.blur(); }
    });
  }

  // ── Tag autocomplete ───────────────────────────────────────────────────────
  // One dropdown for every place a tag is typed: the filter field (#tag), the
  // edit modal's Tags field, a new task's title (#tag shorthand) and the bulk
  // tag dialog. The best match (DeckCore.rankTags) is highlighted from the
  // start, so Tab or Enter completes a half-typed tag to it; ↑/↓ pick another,
  // Esc closes (and keeps what was typed). Each field supplies:
  //   word()          the tag being typed at the caret, or null
  //   suggest(word)   tags to offer, best first
  //   accept(tag, w)  write the chosen tag into the field
  //   onEnter()       optional: Enter also does this after completing
  function attachTagAc(input, { word, suggest, accept, onEnter }) {
    let el = null, idx = 0, tags = [];
    const hide = () => { el?.remove(); el = null; tags = []; };
    function highlight(i) {
      idx = i;
      el?.querySelectorAll('.tag-ac-item').forEach((it, j) => it.classList.toggle('selected', j === idx));
    }
    function choose(i) {
      const w = word();
      if (w && tags[i] != null) accept(tags[i], w);
      hide();
    }
    function show() {
      const w = document.activeElement === input ? word() : null;
      const next = w ? suggest(w) : [];
      hide();
      if (!next.length) return;
      tags = next;
      el = document.createElement('div');
      el.className = 'tag-ac-dropdown';
      tags.forEach((tag, i) => {
        const item = document.createElement('div');
        item.className = 'tag-ac-item';
        item.textContent = '#' + tag;
        item.addEventListener('mouseenter', () => highlight(i));
        item.addEventListener('mousedown', e => { e.preventDefault(); choose(i); });
        el.appendChild(item);
      });
      // Inside the field's parent (not <body>): in the edit modal the dropdown
      // must be in the dialog's top layer to be visible at all.
      const parent = input.parentElement;
      if (getComputedStyle(parent).position === 'static') parent.style.position = 'relative';
      el.style.top = input.offsetTop + input.offsetHeight + 'px';
      el.style.left = input.offsetLeft + 'px';
      el.style.width = input.offsetWidth + 'px';
      parent.appendChild(el);
      highlight(0);
    }
    // Capture phase: runs before the field's own key handlers, which must not
    // see a key the dropdown used (Enter submitting, Esc closing the dialog).
    input.addEventListener('keydown', e => {
      if (!el) return;
      const used = () => { e.preventDefault(); e.stopImmediatePropagation(); };
      if (e.key === 'ArrowDown') { used(); highlight((idx + 1) % tags.length); }
      else if (e.key === 'ArrowUp') { used(); highlight((idx - 1 + tags.length) % tags.length); }
      else if ((e.key === 'Tab' && !e.shiftKey) || e.key === 'Enter') {
        used(); choose(idx);
        if (e.key === 'Enter') onEnter?.();
      }
      else if (e.key === 'Escape') { used(); hide(); }
    }, true);
    input.addEventListener('input', show);
    input.addEventListener('click', show);
    input.addEventListener('blur', () => setTimeout(hide, 150));
    return { hide, isOpen: () => !!el };
  }

  // Keep spaces out of a tag-only field as it is typed (sanitizeTagInput).
  // On `input`, not keydown: that also covers paste and phone keyboards,
  // which often report no key for a space. Registered before attachTagAc's
  // own input listener, so suggestions see the cleaned value.
  function noSpacesInTags(input, list) {
    input.addEventListener('input', e => {
      // Deleting never adds a space, and cleaning after it would undo the
      // deletion (Backspace on "work, " re-added the space after the comma).
      if (e.isComposing || e.inputType?.startsWith('delete')) return;
      const { value, caret } = sanitizeTagInput(input.value, input.selectionStart, list);
      if (value === input.value) return;
      input.value = value;
      input.setSelectionRange(caret, caret);
    });
  }

  // The word under the caret, if it starts with '#'. Inside an OR word (#a|b)
  // only the part after the last pipe is being completed.
  function hashWordAtCaret(input) {
    const val = input.value;
    const caret = input.selectionStart ?? val.length;
    const start = val.lastIndexOf(' ', caret - 1) + 1;
    let end = val.indexOf(' ', caret);
    if (end < 0) end = val.length;
    const w = val.slice(start, end);
    if (!w.startsWith('#')) return null;
    const pipe = w.lastIndexOf('|', caret - start - 1);
    const partStart = pipe >= 0 ? start + pipe + 1 : start;
    return { start, end, partStart, partial: val.slice(partStart, end).replace(/^#/, '') };
  }

  // Replace that word (or its OR part) with "#tag " and put the caret after it.
  function insertHashTag(input, tag, w) {
    const val = input.value;
    const after = val.slice(w.end);
    const insert = (w.partStart === w.start ? '#' : '') + tag + (after.startsWith(' ') ? '' : ' ');
    input.value = val.slice(0, w.partStart) + insert + after;
    const caret = w.partStart + insert.length;
    input.focus();
    input.setSelectionRange(caret, caret);
  }

  // Filter field: tags of the tasks the *other* tag words leave visible in
  // this tab, minus those already used. Free text is ignored: it is usually
  // still being typed ("some text #ta…") and would otherwise hide every tag.
  if (filterSearch) attachTagAc(filterSearch, {
    word: () => hashWordAtCaret(filterSearch),
    suggest: w => {
      const val = filterSearch.value;
      const rest = val.slice(0, w.start) + val.slice(w.end);
      const otherTags = rest.split(/\s+/).filter(word => word.startsWith('#')).join(' ');
      const used = parseFilter(rest).tagGroups.flat();
      const counts = tagCounts(filterTasks(otherTags));
      for (const tag of Object.keys(counts)) if (used.includes(tag.toLowerCase())) delete counts[tag];
      return rankTags(w.partial, counts, w.partial ? 8 : 12);
    },
    accept: (tag, w) => { insertHashTag(filterSearch, tag, w); render(); },
  });
  // A new task starts with the tags the filter requires (single-tag words;
  // an OR word doesn't say which one), so it shows up in the current view.
  function filterDefaultTags() {
    return parseFilter(filterSearch?.value).tagGroups.filter(g => g.length === 1).map(g => g[0]).join(', ');
  }

  // Edit modal Tags field (comma-separated): completes the last entry and
  // adds ", " so the next tag can follow straight away.
  const editTagsInput = document.getElementById('task-edit-tags' + sfx);
  if (editTagsInput) noSpacesInTags(editTagsInput, true);
  const editTagAc = editTagsInput && attachTagAc(editTagsInput, {
    word: () => {
      const val = editTagsInput.value;
      const comma = val.lastIndexOf(',');
      const partial = (comma >= 0 ? val.slice(comma + 1) : val).trim();
      return partial ? { comma, partial } : null;
    },
    suggest: w => {
      const committed = editTagsInput.value.slice(0, Math.max(w.comma, 0))
        .split(',').map(t => t.trim().toLowerCase()).filter(Boolean);
      const counts = tagCounts(tasks);
      for (const tag of Object.keys(counts)) if (committed.includes(tag.toLowerCase())) delete counts[tag];
      return rankTags(w.partial, counts);
    },
    accept: (tag, w) => {
      const val = editTagsInput.value;
      editTagsInput.value = (w.comma >= 0 ? val.slice(0, w.comma + 1) + ' ' : '') + tag + ', ';
      editTagsInput.focus();
    },
  });

  // A new task's title: the #tag shorthand (DeckCore.parseQuickAdd) gets the
  // same suggestions, from every tag in use.
  if (titleInput) attachTagAc(titleInput, {
    word: () => document.getElementById('task-edit-id' + sfx).value ? null : hashWordAtCaret(titleInput),
    suggest: w => rankTags(w.partial, tagCounts(tasks), w.partial ? 8 : 12),
    accept: (tag, w) => { insertHashTag(titleInput, tag, w); syncQuickPreview(); },
  });

  // ── Add task ───────────────────────────────────────────────────────────────

  addBtn.addEventListener('click', () => openEditModal(null));

  // ── Edit modal ─────────────────────────────────────────────────────────────

  // Quick-add shorthand (DeckCore.parseQuickAdd) — new tasks only: a title
  // ending in "tomorrow #work due:fri" fills those fields on save. The line
  // under the title shows what was recognised, so nothing is a surprise.
  const quickPreview = document.getElementById('task-edit-quick' + sfx);
  function quickParse() {
    if (document.getElementById('task-edit-id' + sfx).value) return null;   // editing
    const q = parseQuickAdd(titleInput.value, todayStr());
    return q.found ? q : null;
  }
  function syncQuickPreview() {
    const q = quickParse();
    quickPreview.hidden = !q;
    if (!q) return;
    quickPreview.textContent = [
      q.start_date && `📅 start ${formatDate(q.start_date)}`,
      q.due_date && `⏰ due ${formatDate(q.due_date)}`,
      ...q.tags.map(t => '#' + t),
    ].filter(Boolean).join(' · ');
  }
  titleInput.addEventListener('input', syncQuickPreview);

  const linksDetails = document.getElementById('task-edit-links' + sfx);
  const linksSummary = document.getElementById('task-edit-links-summary' + sfx);
  function syncLinksSummary() {
    linksSummary.textContent = document.getElementById('task-edit-list' + sfx).value.trim();
  }
  linksDetails.addEventListener('input', syncLinksSummary);

  function openEditModal(taskId, opts) {
    const task = taskId ? tasks.find(t => t.id === taskId) : null;
    const defaultTags = opts?.tags || '';
    document.getElementById('task-edit-id' + sfx).value = task ? task.id : '';
    document.getElementById('task-edit-title' + sfx).value = task ? task.title : '';
    document.getElementById('task-edit-desc' + sfx).value = task ? task.description || '' : '';
    document.getElementById('task-edit-due' + sfx).value = task ? task.due_date || '' : '';
    document.getElementById('task-edit-start' + sfx).value = task ? task.start_date || '' : (activeFilter === 'focus' ? dateOffset(0) : '');
    document.getElementById('task-edit-tags' + sfx).value = task ? (task.tags || []).join(', ') : (defaultTags || filterDefaultTags());
    // A new task in a [[deck list=…]] embed belongs to that list; otherwise
    // it would vanish from the embed as soon as it is saved.
    document.getElementById('task-edit-list' + sfx).value = task ? task.list || '' : initList;
    // The embed usually sets it, so it sits in a closed section whose
    // summary shows the value.
    linksDetails.open = false;
    syncLinksSummary();
    titleInput.placeholder = task ? '' : 'e.g. Call Bob tomorrow #work due:fri';
    syncQuickPreview();
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
    syncDates('open');
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
    const subHint = on ? '' : (recTypeSelect.disabled ? 'Set a due or start date first' : 'Select a repeat type first');
    const intervalEl = document.getElementById('task-edit-rec-interval' + sfx);
    intervalEl.disabled = !on;
    intervalEl.closest('.task-field').title = subHint;
    const rf = document.getElementById('task-edit-rec-from' + sfx);
    if (rf) {
      rf.disabled = !on;
      rf.closest('.task-field').title = on ? 'Repeat from the completion date instead of the due (or start) date' : subHint;
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
        syncDates(btn.dataset.dateTrigger === 'task-edit-due' + sfx ? 'due' : 'start');
      }, btn, todayStr(), modal);
    });
  });

  // "Days before due" is not stored: it is just another way to enter the start
  // date, shown as the gap between the two dates (and that gap is what a
  // recurring task carries to its next instance). `changed` says which field
  // the user touched: typing a number ('lead') sets start = due − N; any other
  // change recomputes the number from the two dates. Moving the due date never
  // moves the start date — like the inline ⏰ edit, one edit changes one date.
  // Also gates Repeat on having at least one date.
  function syncDates(changed) {
    const leadEl  = document.getElementById('task-edit-lead' + sfx);
    const dueEl   = document.getElementById('task-edit-due' + sfx);
    const startEl = document.getElementById('task-edit-start' + sfx);
    const lead = leadEl.value.trim();
    if (changed === 'lead' && dueEl.value && lead !== '') {
      startEl.value = addDays(dueEl.value, -Math.max(0, parseInt(lead, 10) || 0));
    }
    if (changed !== 'lead') {
      const gap = (dueEl.value && startEl.value)
        ? Math.round((new Date(dueEl.value + 'T00:00:00') - new Date(startEl.value + 'T00:00:00')) / 86400000)
        : null;
      leadEl.value = gap != null && gap >= 0 ? gap : '';
    }
    // Only meaningful relative to a due date.
    leadEl.disabled = !dueEl.value;
    refreshDateTriggers();
    // Repeat requires at least one date to be meaningful.
    const noDate = !dueEl.value && !startEl.value;
    recTypeSelect.disabled = noDate;
    recTypeSelect.closest('.task-field').title = noDate ? 'Set a due or start date first' : '';
    syncRecFields();
  }
  document.getElementById('task-edit-lead' + sfx).addEventListener('input', () => syncDates('lead'));

  cancelBtn.addEventListener('click', () => { editTagAc?.hide(); modal.close(); });

  modal.addEventListener('keydown', e => { if ((e.key === 'Enter' || e.key === 's') && (e.ctrlKey || e.metaKey)) { e.preventDefault(); e.stopPropagation(); form.requestSubmit(); } }, true);

  form.addEventListener('submit', async e => {
    e.preventDefault();
    const id = document.getElementById('task-edit-id' + sfx).value;
    const data = {
      title: document.getElementById('task-edit-title' + sfx).value.trim(),
      description: document.getElementById('task-edit-desc' + sfx).value.trim(),
      due_date: document.getElementById('task-edit-due' + sfx).value || null,
      start_date: document.getElementById('task-edit-start' + sfx).value || null,
      tags: document.getElementById('task-edit-tags' + sfx).value.split(',').map(t => t.trim()).filter(Boolean),
      rec_type: recTypeSelect.value || null,
      rec_interval: parseInt(document.getElementById('task-edit-rec-interval' + sfx).value) || 1,
      rec_from: document.getElementById('task-edit-rec-from' + sfx)?.checked ? 'completion' : 'due',
      list: cleanList(document.getElementById('task-edit-list' + sfx).value) || null,
    };
    // Shorthand in a new task's title wins over the fields (the preview said so).
    const quick = !id && quickParse();
    if (quick) {
      data.title = quick.title;
      if (quick.start_date) data.start_date = quick.start_date;
      if (quick.due_date) data.due_date = quick.due_date;
      data.tags = [...new Set([...data.tags, ...quick.tags])];
    }
    if (!data.title) { document.getElementById('task-edit-title' + sfx).focus(); return; }
    // Move to new: reset sort_order to null
    if (id && document.getElementById('task-edit-inbox' + sfx).checked) {
      data.sort_order = null;
    }
    modal.close();
    if (id) {
      await updateTask(id, data);
    } else {
      await createTask(data);
    }
  });

  deleteBtn.addEventListener('click', () => {
    const id = document.getElementById('task-edit-id' + sfx).value;
    if (!id) return;
    modal.close();
    deleteWithUndo([id]);
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
        // Different bucket: move to the target's section (same as a divider drop).
        clearBoardDropOver();
        if (targetDiv && targetDiv.dataset.dropBucket) dropOnBucket(draggedId, targetDiv);
      }
    }

    touchDrop = {
      target: el => {
        const item = el.closest('.task-item');
        if (item) return item.dataset.id === draggedId ? null : item;
        const div = boardDivider(el);
        return div && div.dataset.dropBucket ? div : null;
      },
      drop: target => target.classList.contains('task-item')
        ? executeBoardItemDrop(target) : dropOnBucket(draggedId, target),
    };

    listEl.querySelectorAll('.task-item[draggable]').forEach(item => {
      item.addEventListener('dragstart', e => {
        // Touch has its own long-press drag (see touchDrop).
        if (lastPointerType === 'touch') { e.preventDefault(); return; }
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

    // Dropping on a section divider inserts the task before the first
    // reorderable task that follows it.
    function dropOnDivider(div) {
      let next = div.nextElementSibling;
      while (next && !next.matches('.task-item:not([data-due-pinned])')) next = next.nextElementSibling;
      if (next) executeDrop(next.dataset.id);
    }
    touchDrop = {
      target: el => {
        const item = el.closest('.task-item[draggable]');
        if (item) return item.dataset.id === draggedId ? null : item;
        return el.closest('.task-inbox-divider:not(.task-inbox-divider--only)');
      },
      drop: target => target.classList.contains('task-item')
        ? executeDrop(target.dataset.id) : dropOnDivider(target),
    };

    const items = listEl.querySelectorAll('.task-item[draggable]');
    items.forEach(item => {
      item.addEventListener('dragstart', e => {
        if (lastPointerType === 'touch') { e.preventDefault(); return; }
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
        dropOnDivider(div);
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
    // The ⋯ menu owns the keyboard while open (its buttons take Enter/Space)
    if (taskMenu) return;

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

    // '/' focuses the filter field; '#' focuses it and starts a tag word
    if (e.key === '/' && !e.ctrlKey && !e.metaKey && !focus?.closest('input, textarea, select, [contenteditable]')) {
      e.preventDefault();
      filterSearch?.focus();
      return;
    }
    if (e.key === '#' && !e.ctrlKey && !e.metaKey && !focus?.closest('input, textarea, select, [contenteditable]')) {
      e.preventDefault();
      if (filterSearch) {
        const v = filterSearch.value.trimEnd();
        filterSearch.value = v ? v + ' #' : '#';
        filterSearch.focus();
        filterSearch.setSelectionRange(filterSearch.value.length, filterSearch.value.length);
        filterSearch.dispatchEvent(new Event('input'));
      }
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

    // 'f' / 'F' — start selected task today / tomorrow
    if ((e.key === 'f' || e.key === 'F') && !e.ctrlKey && !e.metaKey && !e.altKey && selectedId) {
      e.preventDefault();
      const task = tasks.find(t => t.id === selectedId);
      if (task && !task.completed_at) {
        const target = e.key === 'f' ? dateOffset(0) : dateOffset(1);
        updateTask(task.id, { start_date: task.start_date === target ? null : target });
      }
      return;
    }

    // Tab/Shift+Tab cycles the views
    if (e.key === 'Tab') {
      e.preventDefault();
      // Only the tabs: Completed (reached by its link) is not in the cycle,
      // and from there Tab goes to the first tab, Shift+Tab to the last.
      const filters = tabButtons().map(b => b.dataset.filter);
      const i = filters.indexOf(activeFilter);
      const n = filters.length;
      switchTab(filters[i < 0 ? (e.shiftKey ? n - 1 : 0) : e.shiftKey ? (i - 1 + n) % n : (i + 1) % n]);
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
      await deleteWithUndo([...selectedIds], () => {
        // Move the selection to the next surviving card
        const next = items.find(el => !selectedIds.has(el.dataset.id) &&
                       items.indexOf(el) > currentIdx)
                  || items.find(el => !selectedIds.has(el.dataset.id));
        selectedId = next?.dataset.id || null;
        selectedIds = new Set(selectedId ? [selectedId] : []);
        anchorId = selectedId;
      });
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
  // Background refresh re-renders the list, so it waits while the user is in
  // the middle of something the re-render would destroy: a drag or swipe, a
  // completion's grace period, an inline title edit, the ⋯ menu or the editor.
  function refreshIfIdle() {
    const busy = draggedId || touchDrag || pendingCompletions.size || taskMenu || modal.open ||
      listEl.querySelector('.task-inline-input');
    if (!busy) refresh();
  }
  deckInstances.push({ id: instanceId, refresh, refreshIfIdle, openEdit: openEditModal });

  // Quick-add (from any page) refreshes every embed on this page, not just the
  // last-initialized one. Both hooks reference the shared registry, so the fact
  // that each instance reassigns them is harmless.
  window.refreshDeck = refreshAllInstances;
  window.openDeckEdit = openEditModal;

  // ── Init ───────────────────────────────────────────────────────────────────

  // Apply URL-param pre-filtering
  // [[deck tag=…]] / ?tag= and ?search= all land in the one filter field.
  const initSearch = urlParams.get('search') || '';
  if (filterSearch) filterSearch.value = [tagParamToQuery(initTag), initSearch].filter(Boolean).join(' ');
  // Precedence: URL ?filter= > embed filter (always wins when specified) >
  // tab remembered this session > All default. So a [[deck filter=…]] embed
  // always opens on its configured tab; a plain [[deck]] remembers the tab the
  // user last clicked across refreshes within the same browser tab.
  // The canonical tab names are the <option> values (focus/due/open/completed);
  // this map mirrors deck.py's _FILTER_ALIASES for friendly names in ?filter=
  // URLs. The embed filter arrives already normalized by the server.
  const FILTER_ALIASES = {
    focus: 'focus', today: 'focus',
    all: 'open', backlog: 'open', open: 'open',
    plan: 'due', upcoming: 'due', due: 'due',
    completed: 'completed', done: 'completed',
  };
  const restoreFilter = FILTER_ALIASES[urlFilter] || embedFilter || loadActiveTab() || 'open';
  if (VIEWS.includes(restoreFilter)) {
    activeFilter = restoreFilter;
    if (restoreFilter !== 'completed') lastTab = restoreFilter;
  }
  markActiveTab(activeFilter);
  syncCompletedLink();

  unplannedOnly = unplannedPin ? unplannedPin === '1' : loadUnplanned();
  if (unplannedEl) unplannedEl.checked = unplannedOnly;
  syncUnplannedToggle();

  // ?task=<id> (search results, a page's "Linked tasks"): show exactly that
  // task — the link sets its tab, and here it is selected, scrolled into view
  // and briefly highlighted. By id, not by title: a title can be shared, or
  // contain a #word the filter field would read as a tag.
  const initTaskId = urlParams.get('task') || '';
  function revealTask(id) {
    if (!tasks.some(t => t.id === id)) {
      // Deleted, or rolled into a previous-year archive. One notice per page.
      if (instanceId === '0') showNotice('That task is no longer in the deck.');
      return;
    }
    // "Unplanned only" would hide a planned task: off for this visit only
    // (the stored preference is left alone).
    if (unplannedOnly && !filterTasks().some(t => t.id === id)) {
      unplannedOnly = false;
      if (unplannedEl) unplannedEl.checked = false;
      render();
    }
    setSelected(id);
    const el = listEl.querySelector(`.task-item[data-id="${CSS.escape(id)}"]`);
    if (!el) return;
    el.scrollIntoView({ block: 'center' });
    el.classList.add('task-flash');
    setTimeout(() => el.classList.remove('task-flash'), 2000);
  }

  fetchDeck().then(async () => {
    if (activeFilter === 'completed') await fetchCompleted();
    if (initTaskId) revealTask(initTaskId);
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
