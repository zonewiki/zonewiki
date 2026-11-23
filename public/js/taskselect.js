// A task app's selection (tasks.js initTasksInstance): one card or several
// (Shift and Ctrl, selection mode on touch), the selection bar and what it
// does to every selected task at once (start, complete, won't do with one
// undo), and the Tags dialog for several tasks. What it uses of the app
// comes in app (its state in app.st).
import TasksCore from '../tasks-core.js';
import { putTask } from './util.js';
import { attachTagAc, esc, formatDate, noSpacesInTags, noticeFromResponse, people, showNotice, startsAfterDeadline, tagWordOf, todayStr } from './taskutil.js';

const { tagCounts, selectionTags, bulkTagChange, rankTags } = TasksCore;
// Past this many people the Assign… dialog has a field to find one.
const MANY_PEOPLE = 12;

export function initTaskSelect(app) {
const { st } = app;

// The tags of several selected tasks at once (t, T, the selection bar's
// Tag…). Each tag the selection carries is a row with a three-state box:
// on every task (✓), on some of them (–, left as it is), on none (it goes).
// A click or tap cycles it, back to "some" only where it started so. The
// field below adds tags: chips, completed from every tag in use. T opens
// it for removing: the field then completes to the selection's own tags
// and turns the completed one off. Enter applies, taking a half-typed tag
// too. One request (batch-tag), and one undo puts back every task's tags.

function openBulkTagModal(ids, removing = false) {
  const idSet = new Set(ids);
  const chosen = st.tasks.filter(t => idSet.has(t.id));
  if (!chosen.length) return;
  const count = chosen.length;
  const rows = selectionTags(chosen);
  const initial = Object.fromEntries(rows.map(r => [r.tag, r.count === count ? 'all' : 'some']));
  const states = { ...initial };

  const dlg = document.createElement('dialog');
  dlg.className = 'modal bulk-tag-modal';
  dlg.innerHTML = `
    <p class="modal-label">Tags of ${count} task${count > 1 ? 's' : ''}</p>
    <ul class="bulk-tag-list"${rows.length ? '' : ' hidden'}></ul>
    <div class="task-field">
      <input type="text" id="bulk-tag-input" autocomplete="off" spellcheck="false"
             aria-label="${removing ? 'Tag to remove' : 'Tags to add'}"
             placeholder="${removing ? 'Tag to remove…' : 'Add tags…'}">
    </div>
    <div class="modal-actions">
      <button type="button" class="btn btn-primary" id="bulk-tag-ok">Apply</button>
      <button type="button" class="btn" id="bulk-tag-cancel">Cancel</button>
    </div>`;
  document.body.appendChild(dlg);

  const list = dlg.querySelector('.bulk-tag-list');
  const ARIA = { all: 'true', some: 'mixed', none: 'false' };
  function drawRows() {
    list.innerHTML = rows.map(r => {
      const part = initial[r.tag] === 'some' ? `<span class="bulk-tag-count">${r.count} of ${count}</span>` : '';
      return `<li><button type="button" role="checkbox" class="bulk-tag-row" data-tag="${esc(r.tag)}" aria-checked="${ARIA[states[r.tag]]}">` +
        `<span class="bulk-tag-box" aria-hidden="true"></span>` +
        `<span class="task-tag">#${esc(r.tag)}</span>${part}</button></li>`;
    }).join('');
  }
  drawRows();
  // all → none → some (if it started so) → all …
  const nextState = tag => states[tag] === 'all' ? 'none'
    : states[tag] === 'none' && initial[tag] === 'some' ? 'some' : 'all';
  list.addEventListener('click', e => {
    const row = e.target.closest('.bulk-tag-row');
    if (!row) return;
    const tag = row.dataset.tag;
    states[tag] = nextState(tag);
    row.setAttribute('aria-checked', ARIA[states[tag]]);
  });

  const input = dlg.querySelector('#bulk-tag-input');
  const chips = removing ? null
    : app.chipField(input, { label: t => '#' + t });
  // A comma or space makes chips, as in the edit modal's Tags.
  if (chips) app.chipsOnSeparators(input, chips);
  else noSpacesInTags(input, false);
  const rowOf = tag => rows.find(r => r.tag.toLowerCase() === tag.toLowerCase());
  // Removing: turn the tag's row off.
  function removeTag(tag) {
    const row = rowOf(tag);
    if (!row) return;
    states[row.tag] = 'none';
    list.querySelector(`[data-tag="${CSS.escape(row.tag)}"]`)?.setAttribute('aria-checked', 'false');
  }
  attachTagAc(input, {
    word: () => tagWordOf(input),
    suggest: w => {
      if (removing) {
        return rankTags(w.partial, Object.fromEntries(
          rows.filter(r => states[r.tag] !== 'none').map(r => [r.tag, r.count])));
      }
      // Adding: any tag in use, but not one every selected task has already.
      const have = new Set([...chips.words(), ...rows.filter(r => states[r.tag] === 'all').map(r => r.tag)]
        .map(t => t.toLowerCase()));
      const counts = tagCounts(st.tasks);
      for (const tag of Object.keys(counts)) if (have.has(tag.toLowerCase())) delete counts[tag];
      return rankTags(w.partial, counts);
    },
    accept: tag => {
      if (removing) removeTag(tag); else chips.add(tag);
      input.value = '';
    },
    onEnter: () => submit(),
  });
  input.addEventListener('keydown', ev => {
    if (ev.key === 'Enter') { ev.preventDefault(); ev.stopPropagation(); submit(); }
  });

  function submit() {
    const typed = app.cleanTag(input.value);
    if (typed && removing) removeTag(typed);
    const added = removing ? [] : [...chips.words(), typed].filter(Boolean);
    // Only the rows changed: a tag every task has already needn't be sent.
    const changedRows = Object.fromEntries(Object.entries(states).filter(([t, st]) => st !== initial[t]));
    const { add, remove } = bulkTagChange(changedRows, added);
    close();
    if (add.length || remove.length) bulkTag(ids, add, remove);
  }

  function close() {
    if (dlg.open) dlg.close();   // the tag dropdown lives inside dlg and goes with it
  }
  dlg.addEventListener('close', () => dlg.remove());

  dlg.querySelector('#bulk-tag-ok').addEventListener('click', submit);
  dlg.querySelector('#bulk-tag-cancel').addEventListener('click', close);
  dlg.addEventListener('click', ev => { if (ev.target === dlg) close(); });

  dlg.showModal();
  // On a phone with tags to tick, the keyboard waits until the field is tapped.
  if (!(st.lastPointerType === 'touch' && rows.length)) input.focus();
  else dlg.querySelector('.bulk-tag-row')?.focus({ preventScroll: true });
}

// The list of selected tasks (the selection bar's List…, a card's Move to
// list…, the m key): every list the user sees, A–Z, each a row, then No
// list; a click moves the tasks there at once (batch-update), with one
// undo. The row of the list every task is in already is ticked; one that
// only some are in says how many. A task is as private as its list: a
// shared task can't go into a private list nor, with users, into the Inbox
// (those rows are off when one is selected), and a private one that leaves
// for a shared list is shared, which the row says and no undo takes back;
// it is assigned to its user then, so it stays in their My tasks.
// The last row, New list…, asks for a name in the dialog's place and makes
// the list (POST /api/tasks/lists), then moves the tasks into it: so the
// dialog opens with no list too.
// With giveTo it is the second step of giving private tasks to that user
// (the assign dialog, a drop on their section By person): only the shared
// lists they see and New list…, and a choice shares the tasks there and
// makes them theirs in one write (giveTasks), with the shared tasks `also`.
function openBulkListModal(ids, { giveTo = '', also = [] } = {}) {
  const idSet = new Set(ids);
  const chosen = st.tasks.filter(t => idSet.has(t.id) && (!giveTo || t.private));
  // To give private tasks to someone: the shared lists that person sees.
  const lists = [...app.listSet].sort((a, b) => app.listName(a).localeCompare(app.listName(b)) || a.localeCompare(b))
    .filter(ref => !giveTo || (!app.isPrivateList(ref) && app.assignableIn(ref).has(giveTo)));
  if (!chosen.length) return;
  const count = chosen.length;
  const tasksWord = `${count} task${count > 1 ? 's' : ''}`;
  const anyShared = chosen.some(t => !t.private), anyPrivate = chosen.some(t => t.private);
  const inList = ref => chosen.filter(t => (app.taskList(t) || '') === ref).length;
  const row = (ref, label, n) => {
    const all = n === count;
    const isPrivate = app.isPrivateList(ref);
    const off = isPrivate && anyShared;
    const withWhom = app.listGroup(ref) ? `the group ${app.listGroup(ref)}` : 'everyone';
    const note = off ? 'a shared task stays shared'
      : anyPrivate && !isPrivate && !all ? `shares it with ${withWhom}` + (giveTo ? '' : people.users?.has(people.me) ? ', assigned to you' : '')
      : chosen.some(t => app.widens(t, ref)) ? 'everyone will see it'
      : chosen.some(t => app.narrows(t, ref)) ? 'fewer people will see it' : '';
    const part = note ? `<span class="bulk-tag-count">${note}</span>`
      : n && !all ? `<span class="bulk-tag-count">${n} of ${count}</span>` : '';
    const tip = ref ? ` title="${esc(app.listTooltip(ref))}"` : '';
    return `<li><button type="button" class="bulk-tag-row bulk-list-row" data-list="${esc(ref)}" aria-current="${all}"${off ? ' disabled' : ''}>` +
      `<span class="bulk-list-mark" aria-hidden="true">${all ? '✓' : ''}</span>` +
      `<span class="${ref ? 'task-tag task-tag-list' : 'bulk-list-none'}"${tip}>${label}</span>${part}</button></li>`;
  };
  const label = ref => (app.privateLists.has(ref) ? '🔒 ' : '') + esc(app.listName(ref)) + (ref.startsWith('shared/') ? ' (shared)' : '');
  // Private lists are a user's (with users); a shared task stays shared,
  // and so does one given to someone.
  const canPrivate = !!app.appEl?.dataset.privateFolder && !giveTo;
  const heading = giveTo ? `Give ${tasksWord} to ${giveTo}: choose a shared list` : `Move ${tasksWord} to a list`;
  const dlg = document.createElement('dialog');
  dlg.className = 'modal bulk-tag-modal bulk-list-modal';
  dlg.innerHTML = `
    <p class="modal-label">${esc(heading)}</p>
    ${giveTo ? `<p class="bulk-list-note bulk-give-note">${count > 1 ? 'These tasks are' : 'This task is'} private, yours alone. Whoever sees the list will see and change ${count > 1 ? 'them' : 'it'}, and ${count > 1 ? 'they' : 'it'} can’t be made private again.</p>` : ''}
    <ul class="bulk-tag-list">${giveTo ? '' : row('', (app.inboxPrivate ? '🔒 ' : '') + 'Inbox', inList(''))}${lists.map(ref => row(ref, label(ref), inList(ref))).join('')}` +
      `<li><button type="button" class="bulk-tag-row bulk-list-row bulk-list-new" aria-current="false">` +
      `<span class="bulk-list-mark" aria-hidden="true">+</span><span class="bulk-list-none">New list…</span></button></li></ul>
    <form class="bulk-list-form" hidden>
      <div class="task-field">
        <input type="text" id="bulk-list-name" autocomplete="off" spellcheck="false" required
               aria-label="The list's name" placeholder="Its name, e.g. shop">
      </div>
      ${canPrivate ? `<label class="promote-check"${anyShared ? ' title="A shared task stays shared"' : ''}>` +
        `<input type="checkbox" id="bulk-list-private"${anyShared ? ' disabled' : ' checked'}> Private: only you see it and its tasks</label>` : ''}
      <p class="bulk-list-note" hidden></p>
      <p class="rename-error" hidden></p>
      <div class="modal-actions">
        <button type="submit" class="btn btn-primary" id="bulk-list-create">Create and move</button>
        <button type="button" class="btn" id="bulk-list-back">Back</button>
      </div>
    </form>
    <div class="modal-actions bulk-list-actions">
      <button type="button" class="btn" id="bulk-list-cancel">Cancel</button>
    </div>`;
  document.body.appendChild(dlg);
  const close = () => { if (dlg.open) dlg.close(); };
  dlg.addEventListener('close', () => dlg.remove());
  dlg.querySelector('#bulk-list-cancel').addEventListener('click', close);
  dlg.addEventListener('click', ev => { if (ev.target === dlg) close(); });
  const title = dlg.querySelector('.modal-label'), listEl = dlg.querySelector('.bulk-tag-list');
  const form = dlg.querySelector('.bulk-list-form'), actions = dlg.querySelector('.bulk-list-actions');
  const nameInput = form.querySelector('#bulk-list-name'), privateBox = form.querySelector('#bulk-list-private');
  const note = form.querySelector('.bulk-list-note'), error = form.querySelector('.rename-error');
  const giveNote = dlg.querySelector('.bulk-give-note');
  const rows = [...dlg.querySelectorAll('.bulk-list-row:not([disabled])')];
  dlg.addEventListener('keydown', ev => {
    if (!form.hidden || (ev.key !== 'ArrowDown' && ev.key !== 'ArrowUp')) return;
    ev.preventDefault();
    const i = rows.indexOf(document.activeElement);
    rows[(i + (ev.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length].focus();
  });

  const move = (target, targetPrivate, made) => giveTo ? giveTasks(chosen, also, giveTo, target, made)
    : moveTasks(chosen, target, targetPrivate, made);
  listEl.addEventListener('click', ev => {
    const btn = ev.target.closest('.bulk-list-row');
    if (!btn || btn.disabled) return;
    if (btn.classList.contains('bulk-list-new')) { showForm(true); return; }
    const target = btn.dataset.list;
    close();
    move(target, app.isPrivateList(target), false);
  });

  // New list…: the rows give way to the name (and, with users, whether the
  // list is private: off for a shared task, on when every task is private,
  // so nothing is shared by not looking).
  const syncNote = () => {
    const sharing = anyPrivate && !privateBox?.checked;
    note.hidden = !sharing;
    const n = chosen.filter(t => t.private).length;
    note.textContent = !sharing ? '' : n === count
      ? `Everyone who uses the wiki will see and change ${count > 1 ? 'these tasks' : 'this task'}. ${count > 1 ? 'They' : 'It'} can’t be made private again.`
      : `Everyone who uses the wiki will see and change the ${n > 1 ? n + ' private tasks' : 'private task'} among them. ${n > 1 ? 'They' : 'It'} can’t be made private again.`;
  };
  function showForm(on) {
    title.textContent = !on ? heading : giveTo ? `Give ${tasksWord} to ${giveTo}: a new shared list` : `Move ${tasksWord} to a new list`;
    if (giveNote) giveNote.hidden = on;   // the form says it itself
    listEl.hidden = actions.hidden = on;
    form.hidden = !on;
    error.hidden = true;
    if (on) { syncNote(); nameInput.focus(); } else dlg.querySelector('.bulk-list-new').focus();
  }
  privateBox?.addEventListener('change', syncNote);
  form.querySelector('#bulk-list-back').addEventListener('click', () => showForm(false));
  let creating = false;
  form.addEventListener('submit', async ev => {
    ev.preventDefault();
    if (creating) return;
    creating = true;
    let made = null, why = 'The list couldn’t be made';
    try {
      const res = await fetch('/api/tasks/lists', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: nameInput.value, private: !!privateBox?.checked }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.ref) made = data; else if (data.error) why = data.error;
    } catch (_) { why = 'The list couldn’t be made: no answer from Zonewiki'; }
    creating = false;
    if (!made) {
      error.textContent = why;
      error.hidden = false;
      nameInput.focus();
      return;
    }
    // Every task app of the page has the list from now (tasks.js), and the
    // Tasks page's row of lists shows it (js/listsrow.js).
    document.dispatchEvent(new CustomEvent('zone:list-made', { detail: made }));
    close();
    move(made.ref, !!made.private, true);
  });

  dlg.showModal();
  (rows.find(r => r.getAttribute('aria-current') === 'true') || rows[0]).focus({ preventScroll: true });
}

// The tasks `chosen` into the list `target` ('' for the Inbox), which is
// private or not (the move dialog's rows, a drop on a list's section By
// list), with one undo. True when they are there now.
async function moveTasks(chosen, target, targetPrivate, made) {
  const moving = chosen.filter(t => (app.taskList(t) || '') !== target);
  if (!moving.length) return true;   // there already
  app.dismissUndo();
  // A task of the user's that is shared is given to them: the server's rule
  // (sharedBy in go/tasks.go), whatever shares it.
  const shares = t => !!t.private && !targetPrivate;
  if (!(await batchUpdate(moving.map(t => ({ id: t.id, list: target, ...(shares(t) ? { share: true } : {}) }))))) return false;
  const what = `${moving.length} task${moving.length > 1 ? 's' : ''}`;
  const msg = target ? `${what} moved to ${made ? 'the new list' : 'list'} “${app.listName(target)}”` : `${what} moved to the Inbox`;
  // Back where they were, but those that were private and are shared now.
  // (A list just made stays: it is a list from now on, empty or not.)
  const back = moving.filter(t => !shares(t));
  if (back.length) app.showUndo(msg, () => batchUpdate(back.map(t => ({ id: t.id, list: app.taskList(t) || '', history: t.history || [] }))));
  else showNotice(msg);
  return true;
}

// The private tasks `own` to the user `name`: into the shared list `target`
// (everyone who sees it sees them from now on) and theirs, in one write,
// together with the shared tasks `also`, which only change hands (the
// assign dialog's second step, a drop on a person's section By person).
// The undo gives the shared ones back; what was private stays shared.
async function giveTasks(own, also, name, target, made) {
  const changing = also.filter(t => (t.assignee || '') !== name);
  app.dismissUndo();
  if (!(await batchUpdate([...own.map(t => ({ id: t.id, list: target, share: true, assignee: name })),
    ...changing.map(t => ({ id: t.id, assignee: name }))]))) return false;
  const n = own.length + changing.length;
  const where = `${made ? 'the new list' : 'list'} “${app.listName(target)}”`;
  const msg = changing.length ? `${n} tasks assigned to ${name}, ${own.length} of them shared in ${where}`
    : `${n} task${n > 1 ? 's' : ''} shared in ${where} and assigned to ${name}`;
  if (changing.length) app.showUndo(msg, () => batchUpdate(changing.map(t => ({ id: t.id, assignee: t.assignee || null, history: t.history || [] }))));
  else showNotice(msg);
  return true;
}

// The shared tasks `chosen` to the user `name` ('' no one), with one undo
// (the assign dialog's rows, a drop on a person's section By person). True
// when they are that user's now.
async function assignTasks(chosen, name) {
  const changing = chosen.filter(t => (t.assignee || '') !== name);
  if (!changing.length) return true;
  app.dismissUndo();
  if (!(await batchUpdate(changing.map(t => ({ id: t.id, assignee: name || null }))))) return false;
  const what = `${changing.length} task${changing.length > 1 ? 's' : ''}`;
  // The old history goes with the old name, so the undo leaves no trace.
  app.showUndo(name ? `${what} assigned to ${name === people.me ? 'you' : name}` : `${what} assigned to no one`,
    () => batchUpdate(changing.map(t => ({ id: t.id, assignee: t.assignee || null, history: t.history || [] }))));
  return true;
}

// Who has several selected tasks, at once (the selection bar's Assign…,
// the a key; with users): every user who can be assigned, yourself first,
// each a row, then No one; a click assigns the selected tasks at once
// (batch-update), with one undo. The row of who has them all already is
// ticked; one who has only some says how many. A private task is its
// user's own: it counts as yours, it can't be no one's (No one is for the
// shared ones), and someone else's name asks for a shared list next (the
// move dialog, giveTo), since a task for someone else is shared work.
function openBulkAssignModal(ids) {
  if (!people.users) return;
  const idSet = new Set(ids);
  const picked = st.tasks.filter(t => idSet.has(t.id));
  if (!picked.length) return;
  const chosen = picked.filter(t => !t.private), own = picked.filter(t => t.private);
  const count = picked.length;
  // Who every chosen task can be given to: in a group's list its members
  // (the server refuses anyone else), yourself first.
  const allowed = name => chosen.every(t => app.assignableIn(app.taskList(t) || '').has(name));
  const names = [...people.users].filter(allowed).sort((a, b) => (b === people.me) - (a === people.me) || a.localeCompare(b));
  const has = name => chosen.filter(t => (t.assignee || '') === name).length + (name === people.me ? own.length : 0);
  const row = (name, label, n) => {
    const all = n === count;
    const part = n && !all ? `<span class="bulk-tag-count">${n} of ${count}</span>` : '';
    return `<li><button type="button" class="bulk-tag-row bulk-list-row" data-assignee="${esc(name)}" aria-current="${all}">` +
      `<span class="bulk-list-mark" aria-hidden="true">${all ? '✓' : ''}</span>` +
      `<span class="${name ? 'task-assignee' + (name === people.me ? ' task-assignee-me' : '') : 'bulk-list-none'}">${label}</span>${part}</button></li>`;
  };
  const dlg = document.createElement('dialog');
  dlg.className = 'modal bulk-tag-modal bulk-list-modal bulk-assign-modal';
  dlg.innerHTML = `
    <p class="modal-label">Assign ${count} task${count > 1 ? 's' : ''}</p>
    ${names.length > MANY_PEOPLE ? `<div class="task-field"><input type="search" id="bulk-assign-find" autocomplete="off" spellcheck="false"
      aria-label="Find a person" placeholder="Find a person…"></div>` : ''}
    ${own.length ? `<p class="bulk-list-note">${own.length === count ? (count > 1 ? 'These tasks are' : 'This task is') + ' private, yours alone'
      : (own.length > 1 ? `${own.length} of them are` : 'One of them is') + ' private, yours alone'}: for someone else, you choose a shared list next.</p>` : ''}
    <ul class="bulk-tag-list">${names.map(name => row(name, '@' + esc(name), has(name))).join('')}${chosen.length ? row('', 'No one', has('')) : ''}</ul>
    <div class="modal-actions">
      <button type="button" class="btn" id="bulk-assign-cancel">Cancel</button>
    </div>`;
  document.body.appendChild(dlg);
  const close = () => { if (dlg.open) dlg.close(); };
  dlg.addEventListener('close', () => dlg.remove());
  dlg.querySelector('#bulk-assign-cancel').addEventListener('click', close);
  dlg.addEventListener('click', ev => { if (ev.target === dlg) close(); });
  const all = [...dlg.querySelectorAll('.bulk-list-row')];
  const shown = () => all.filter(r => !r.closest('li').hidden);
  // Many people: a field finds the one meant (the rows it leaves are the
  // names that hold what is typed; Enter takes the first, ↓ goes into them).
  const find = dlg.querySelector('#bulk-assign-find');
  find?.addEventListener('input', () => {
    const q = find.value.trim().replace(/^@/, '').toLowerCase();
    for (const r of all) r.closest('li').hidden = !!q && !!r.dataset.assignee && !r.dataset.assignee.includes(q);
  });
  find?.addEventListener('keydown', ev => {
    if (ev.key !== 'Enter') return;
    ev.preventDefault();
    if (find.value.trim()) shown()[0]?.click();
  });
  dlg.addEventListener('keydown', ev => {
    if (ev.key !== 'ArrowDown' && ev.key !== 'ArrowUp') return;
    ev.preventDefault();
    const rows = shown();
    const i = rows.indexOf(document.activeElement);
    if (i < 0) { rows[ev.key === 'ArrowDown' ? 0 : rows.length - 1]?.focus(); return; }
    if (find && (i === 0 && ev.key === 'ArrowUp' || i === rows.length - 1 && ev.key === 'ArrowDown')) { find.focus(); return; }
    rows[(i + (ev.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length].focus();
  });
  const rows = all;
  dlg.querySelector('.bulk-tag-list').addEventListener('click', async ev => {
    const btn = ev.target.closest('.bulk-list-row');
    if (!btn) return;
    close();
    const name = btn.dataset.assignee;
    // Yours stay yours; for someone else they need a shared list first.
    if (own.length && name && name !== people.me) openBulkListModal(own.map(t => t.id), { giveTo: name, also: chosen });
    else assignTasks(chosen, name);
  });
  dlg.showModal();
  if (find && st.lastPointerType !== 'touch') find.focus();
  else (rows.find(r => r.getAttribute('aria-current') === 'true') || rows[0]).focus({ preventScroll: true });
}

// Add and remove tags on the tasks `ids` (batch-tag), with one undo that
// puts back each changed task's tags (batch-update). Tasks the change takes
// out of this view (another list, the filter) say where they went, unless
// `label` says what happened (a move to a list).
async function bulkTag(ids, add, remove, label = '') {
  const idSet = new Set(ids);
  const before = new Map(st.tasks.filter(t => idSet.has(t.id)).map(t => [t.id, t]));
  app.dismissUndo();
  let res;
  try {
    res = await fetch('/api/tasks/batch-tag', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids, add, remove }),
    });
  } catch (_) { showNotice('Could not change the tags'); return; }
  if (!res.ok) { await noticeFromResponse(res, 'Could not change the tags'); return; }
  const updated = await res.json();
  const sameTags = (a, b) => (a || []).join('\n') === (b || []).join('\n');
  const changed = updated.filter(u => !sameTags(before.get(u.id)?.tags, u.tags));
  const gone = [];
  for (const u of updated) {
    const idx = st.tasks.findIndex(t => t.id === u.id);
    if (idx === -1) continue;
    const msg = app.leftViewMessage(st.tasks[idx], u);
    if (app.inScope(u)) st.tasks[idx] = app.own(u);
    else st.tasks.splice(idx, 1);
    if (msg) gone.push({ msg, list: !app.inScope(u) && app.taskList(u) });
  }
  app.render();
  app.refreshOtherInstances(app.instanceId);
  if (!changed.length) return;
  const n = changed.length;
  const what = `${n} task${n > 1 ? 's' : ''}`;
  let msg = add.length === 1 && !remove.length ? `#${add[0]} added to ${what}`
    : remove.length === 1 && !add.length ? `#${remove[0]} removed from ${what}`
    : `Tags changed on ${what}`;
  if (gone.length) {
    const lists = new Set(gone.map(g => g.list));
    const one = lists.size === 1 && [...lists][0];   // all went into one list
    msg = gone.length === 1 ? gone[0].msg
      : one ? `${gone.length} tasks saved to list “${one}”.` : `${gone.length} tasks no longer show here.`;
  }
  app.showUndo(label || msg, () => batchUpdate(changed.map(u => ({ id: u.id, tags: before.get(u.id).tags || [] }))));
}

// ── Selection ──────────────────────────────────────────────────────────────
// One selected task (selectedId) or several (selectedIds): a click selects
// one, Shift+click or Shift+↑/↓ a range from the anchor, Ctrl/Cmd+click
// adds or drops one, Ctrl+A takes every task shown. While several are
// selected the selection bar (syncSelBar) and the ⋯ menu of a selected card
// act on all of them (runSelectionAction).

function updateSelectionClasses() {
  app.listEl.querySelectorAll('.task-item').forEach(el => {
    el.classList.toggle('task-selected', st.selectedIds.has(el.dataset.id));
  });
  // Several selected, none, or another one: the expanded task closes.
  if (st.expandedId && (st.selectedIds.size !== 1 || st.expandedId !== st.selectedId)) app.setExpanded(null);
  syncSelBar();
}

// Whether `id` is one of several selected tasks.
const inSelection = id => st.selectedIds.size > 1 && st.selectedIds.has(id);

function toggleSelection(id) {
  if (st.selectedIds.has(id)) {
    st.selectedIds.delete(id);
    if (st.selectedId === id) st.selectedId = st.selectedIds.size ? [...st.selectedIds].at(-1) : null;
  } else {
    st.selectedIds.add(id);
    st.selectedId = id;
  }
  st.anchorId = id;   // a Shift+click next extends from here
  updateSelectionClasses();
}

function selectAllVisible() {
  const ids = visibleItems().map(el => el.dataset.id);
  if (!ids.length) return;
  st.selectedIds = new Set(ids);
  if (!st.selectedIds.has(st.selectedId)) st.selectedId = ids[0];
  st.anchorId = st.selectedId;
  updateSelectionClasses();
}

function clearSelection() {
  st.selectedId = st.anchorId = null;
  st.selectedIds = new Set();
  updateSelectionClasses();
}

// Selection mode starts with the card whose menu it came from. It ends with
// ✕, Esc, another tab, Done or Won't do, or the browser's back (a phone's
// back gesture): entering adds a history entry, so back leaves the mode,
// not the page.
function enterSelectMode(id) {
  st.selectMode = true;
  app.appEl.classList.add('tasks-selecting');
  st.selectedId = st.anchorId = id;
  st.selectedIds = new Set([id]);
  updateSelectionClasses();
  try { history.pushState({ ...history.state, tasksSelect: app.instanceId }, ''); } catch (_) {}
}
function exitSelectMode() {
  if (!st.selectMode) { clearSelection(); return; }
  st.selectMode = false;
  app.appEl.classList.remove('tasks-selecting');
  clearSelection();
  if (history.state?.tasksSelect === app.instanceId) history.back();
}
window.addEventListener('popstate', () => { if (st.selectMode) exitSelectMode(); });

// The selection bar: while several tasks are selected (on a page with
// several task lists, only the active one's), how many, and what to do to all.
// Today and Won't do go when every selected task is closed, and Done says Reopen;
// Unplan shows while an open selected task has a start date.
function syncSelBar() {
  if (!app.selBar) return;
  const show = (st.selectMode || st.selectedIds.size > 1) &&
    (app.tasksInstances.length < 2 || app.activeTasksInstance === app.instanceId);
  app.selBar.hidden = !show;
  if (!show) return;
  const allDone = st.tasks.filter(t => st.selectedIds.has(t.id)).every(t => t.closed_at);
  app.selBar.querySelector('[data-sel="unplan"]').hidden = !anyPlanned();
  app.selBar.querySelector('.tasks-selbar-count').textContent =
    st.selectedIds.size ? `${st.selectedIds.size} selected` : 'Tap tasks to select';
  app.selBar.querySelectorAll('.tasks-selbar-actions .btn').forEach(b => { b.disabled = !st.selectedIds.size; });
  app.selBar.querySelector('[data-sel="today"]').hidden = allDone;
  app.selBar.querySelector('[data-sel="wontdo"]').hidden = allDone;
  const done = app.selBar.querySelector('[data-sel="done"]');
  done.textContent = allDone ? 'Reopen' : 'Done';
  done.title = allDone ? 'Reopen the selected tasks (Space)' : 'Complete the selected tasks (Space)';
  app.selBar.querySelector('[data-sel="all"]').disabled =
    visibleItems().every(el => st.selectedIds.has(el.dataset.id));
}
// An open selected task has a start date: there is something to unplan.
function anyPlanned() {
  return st.tasks.some(t => st.selectedIds.has(t.id) && !t.closed_at && t.start_date);
}
app.selBar?.addEventListener('click', e => {
  const action = e.target.closest('[data-sel]')?.dataset.sel;
  if (action) runSelectionAction(action);
});

// What the selection bar and a selected card's ⋯ menu do to every selected task.
function runSelectionAction(action) {
  const ids = [...st.selectedIds];
  switch (action) {
    case 'all':    selectAllVisible(); break;
    case 'clear':  exitSelectMode(); break;
    case 'tag':    if (ids.length) openBulkTagModal(ids); break;
    case 'list':   if (ids.length) openBulkListModal(ids); break;
    case 'assign': if (ids.length) openBulkAssignModal(ids); break;
    case 'today':  startToday(ids); break;
    case 'unplan': startOn(ids, null); break;
    case 'done':
      if (ids.length) mayComplete(ids).then(ok => { if (ok) { exitSelectMode(); completeMany(ids); } });
      break;
    case 'wontdo': if (ids.length) { exitSelectMode(); app.wontDoMany(ids); } break;
  }
}

// Fields of several tasks in one request (each task its own; batch-update),
// then the tasks reloads. False if the server refused.
async function batchUpdate(changes) {
  const res = await fetch('/api/tasks/batch-update', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tasks: changes }),
  });
  if (!res.ok) { await noticeFromResponse(res, 'Update rejected'); return false; }
  await app.afterBatchWrite();
  return true;
}

// The importance of the tasks `ids`: 'high', 'normal' or 'low' (the ⋯ menu,
// the i key), with one undo that gives each its own back, as every quick
// action has (no history to put back: importance isn't logged). Those
// of that importance already are left alone. True when they are now.
async function setImportance(ids, level) {
  const idSet = new Set(ids);
  const changing = st.tasks.filter(t => idSet.has(t.id) && (t.importance || 'normal') !== level);
  if (!changing.length) return true;
  app.dismissUndo();
  const ok = changing.length === 1 ? !!await app.updateTask(changing[0].id, { importance: level })
    : await batchUpdate(changing.map(t => ({ id: t.id, importance: level })));
  if (!ok) return false;
  const name = level[0].toUpperCase() + level.slice(1);
  app.showUndo(changing.length === 1 ? `${name} importance` : `${changing.length} tasks of ${level} importance`, () =>
    changing.length === 1 ? app.updateTask(changing[0].id, { importance: changing[0].importance || 'normal' })
      : batchUpdate(changing.map(t => ({ id: t.id, importance: t.importance || 'normal' }))));
  return true;
}

// Start the open tasks among `ids` today (those not starting today
// already), with one undo that puts back each one's start date and history.
// Today is never after a deadline that hasn't passed, and an overdue task
// started today is the way to catch up, so no deadline question comes up.
function startToday(ids) {
  return startOn(ids, todayStr());
}

// Start the open tasks among `ids` on `day` (null: unplan them), with one
// undo: every quick way to set a start date comes here (f, F, 0–7, the ⋯
// menu, the selection bar, a card's 📅, a swipe, a drop on a day). A task
// whose deadline `day` passes asks whether the deadline moves too, one by
// one after the others (planOnDay). One task alone goes through setStart,
// which does the same. A task that starts on `day` already is left alone:
// nothing here toggles. Whether the tasks start on `day` now (the ones that
// asked aside).
async function startOn(ids, day) {
  const idSet = new Set(ids);
  const before = st.tasks.filter(t => idSet.has(t.id) && !t.closed_at && (t.start_date ?? null) !== day);
  if (before.length === 1) return app.setStart(before[0].id, day);
  const asks = before.filter(t => startsAfterDeadline(t, day));
  const plain = before.filter(t => !asks.includes(t));
  if (plain.length) {
    app.dismissUndo();
    if (!await batchUpdate(plain.map(t => ({ id: t.id, start_date: day })))) return false;
    const n = plain.length;
    const msg = n === 1 ? (day ? `Starts ${formatDate(day)}` : 'Start date cleared')
      : day ? `${n} tasks start ${/^(Today|Tomorrow)$/.test(formatDate(day)) ? formatDate(day).toLowerCase() : formatDate(day)}`
      : `${n} tasks unplanned`;
    app.showUndo(msg, () =>
      batchUpdate(plain.map(t => ({ id: t.id, start_date: t.start_date ?? null,
                                    recurrence: t.recurrence ?? null, history: t.history || [] }))));
  }
  for (const t of asks) await app.planOnDay(t.id, day);
  return true;
}

// Before completeMany, and before the selection moves on: some of `ids`
// have subtasks still open, so it asks (confirmOpenSubtasks; reopening
// asks nothing). False when the user cancelled.
function mayComplete(ids) {
  return app.confirmOpenSubtasks(ids.map(id => st.tasks.find(t => t.id === id)));
}

// Complete the tasks `ids`, or reopen them if every one is completed, with
// an undo. Keep tasks stay open when completed; undo restores their fields
// instead of reopening them from Closed. The completed ones stay where they
// were, ticked, as one ticked alone does (st.ticked in js/taskdata.js).
async function completeMany(ids) {
  const open = ids.map(id => st.tasks.find(t => t.id === id)).filter(t => t && !t.closed_at && !st.ticked.has(t.id));
  const completing = open.length > 0;
  const kept = open.filter(t => t.keep).map(t => [t.id, app.keepSnapshot(t)]);
  app.dismissUndo();
  const stay = app.initUpdates ? [] : open;
  for (const t of stay) st.ticked.set(t.id, { ...t });
  const unstay = () => { for (const t of stay) st.ticked.delete(t.id); app.holdTicked(); };
  if (!completing) for (const id of ids) st.ticked.delete(id);   // ticked here, reopened: as any open task
  app.holdTicked();
  if (!await app.completeTasks(ids, completing)) { unstay(); app.render(); return; }
  // They stay, ticked, but are done with: no longer the selection.
  if (stay.some(t => st.selectedIds.has(t.id))) { clearSelection(); syncSelBar(); }
  await app.holdClosed(stay.filter(t => !t.keep).map(t => t.id));
  const label = completing ? `${ids.length} tasks completed` : `${ids.length} tasks reopened`;
  app.showUndo(label, async () => {
    unstay();
    const keptIds = new Set(kept.map(([id]) => id));
    const others = ids.filter(id => !keptIds.has(id));
    if (kept.length) await Promise.all(kept.map(([id, before]) => putTask(id, before)));
    if (others.length) await app.completeTasks(others, !completing);
    else await app.afterBatchWrite();
  });
}

function setSelected(id) {
  st.selectedId = id;
  st.anchorId   = id;
  st.selectedIds = new Set(id ? [id] : []);
  updateSelectionClasses();
  // The selected task shows its whole title. On touch the tap
  // handler does it, since the compat mousedown selects without the click.
  // Selecting the expanded task again keeps its details as they are.
  if (id && st.lastPointerType !== 'touch' && id !== st.expandedId) app.expandSelected(id);
  // A pressed card is in view; scrolling an expanded one into view would
  // move it from under the pointer.
  if (id && !st.pressedCard) {
    const el = app.listEl.querySelector(`.task-item[data-id="${CSS.escape(id)}"]`);
    if (el) el.scrollIntoView({ block: 'nearest' });
  }
}

function extendSelection(id) {
  if (!id) return;
  st.selectedId = id;
  const items = visibleItems();
  const aIdx = st.anchorId ? items.findIndex(el => el.dataset.id === st.anchorId) : 0;
  const bIdx = items.findIndex(el => el.dataset.id === id);
  if (bIdx === -1) return;
  const [lo, hi] = aIdx <= bIdx ? [aIdx, bIdx] : [bIdx, aIdx];
  st.selectedIds = new Set(items.slice(lo, hi + 1).map(el => el.dataset.id));
  updateSelectionClasses();
  const el = app.listEl.querySelector(`.task-item[data-id="${CSS.escape(id)}"]`);
  if (el) el.scrollIntoView({ block: 'nearest' });
}

function visibleItems() {
  return Array.from(app.listEl.querySelectorAll('.task-item'));
}

return { moveTasks, assignTasks, openBulkTagModal, openBulkListModal, openBulkAssignModal, updateSelectionClasses, inSelection, toggleSelection, selectAllVisible, clearSelection, anyPlanned, enterSelectMode, exitSelectMode, syncSelBar, mayComplete, setImportance, runSelectionAction, startOn, completeMany, setSelected, extendSelection, visibleItems };
}
