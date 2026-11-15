// A task app's edit modal (tasks.js initTasksInstance): a task's fields
// (the rare ones shown when set or asked for), its tags as chips, the
// dates with their pickers and warnings, the notes
// edited as a page is (with Edit | Preview), history and comments, the
// quick-add shorthand, several new tasks at once (the editor's Make task)
// and the private box. What it uses of the app comes in app (its state in
// app.st).
import TasksCore from '../tasks-core.js';
import { markdownEditor } from './mdedit.js';
import { toggleCheckboxText } from './mdtext.js';
import { renderFigures } from './figures.js';
import { openDatePicker } from './datepicker.js';
import { IMPORTANCE, attachTagAc, commentStamp, dateOffset, esc, formatDate, hashWordAtCaret, insertHashTag, loadPeople, noticeFromResponse, people, shortDate, tagWordOf, todayStr } from './taskutil.js';
import { highlightCode, renderTexts, textsOf } from './tasktexts.js';

const { addDays, tagCounts, rankTags, parseQuickAdd } = TasksCore;

export function initTaskModal(app) {
const { st } = app;

// The notes are edited as a page is (js/mdedit.js): the formatting bar
// (shown while the field has the focus, tasks.css), completion, snippets,
// lists, file paste and drop. Tab stays the key to the next field (it still
// expands a snippet). Edit | Preview (or F2) switches the field for the
// notes as the card will show them, in the field's place and at least as
// tall; it opens in Edit.
const notesPreview = document.getElementById('task-edit-preview' + app.sfx);
const notesBar = document.getElementById('task-edit-format' + app.sfx);
const notesModeBtns = [...app.modal.querySelectorAll('[data-notes-mode]')];
async function fetchNotesPreview() {
  const text = app.descTextarea.value.trim();
  if (!text) { notesPreview.innerHTML = '<p class="task-notes-empty">Nothing to preview</p>'; return; }
  try {
    const res = await fetch('/api/render-notes', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ notes: [text] }),
    });
    if (res.ok && app.descTextarea.value.trim() === text) {
      notesPreview.innerHTML = highlightCode((await res.json()).notes[0]);
      renderFigures(notesPreview);
    }
  } catch (_) { notesPreview.textContent = 'Could not render the preview'; }
}
const previewing = () => !!notesPreview && !notesPreview.hidden;
function setNotesPreview(show, focus = true) {
  if (!notesPreview) return;
  // No shorter than the field, so a short description moves nothing below.
  if (show) notesPreview.style.minHeight = `${app.descTextarea.offsetHeight}px`;
  notesPreview.hidden = !show;
  app.descTextarea.hidden = show;
  if (notesBar) notesBar.hidden = show;
  notesModeBtns.forEach(b => b.setAttribute('aria-pressed', String((b.dataset.notesMode === 'preview') === show)));
  // Focus follows: the hidden field would drop it out of the modal, whose
  // keys (F2 back, Esc) then wouldn't reach it. Arrows scroll the preview.
  if (show) { notesPreview.innerHTML = ''; fetchNotesPreview(); if (focus) notesPreview.focus({ preventScroll: true }); }
  else { fitNotes(); if (focus) app.descTextarea.focus(); }
}
// The field is as tall as its text: two lines when empty, growing as the
// text does up to its max-height (tasks.css), where it scrolls. Measured
// with the height let go, which must not move what the dialog shows.
function fitNotes() {
  const ta = app.descTextarea;
  if (!ta || ta.hidden || !ta.offsetParent) return;
  const scrolled = app.modal.scrollTop;
  ta.style.height = 'auto';
  ta.style.height = `${ta.scrollHeight + ta.offsetHeight - ta.clientHeight}px`;
  app.modal.scrollTop = scrolled;
}
app.descTextarea?.addEventListener('input', fitNotes);
notesModeBtns.forEach(b => b.addEventListener('click', () => setNotesPreview(b.dataset.notesMode === 'preview')));
// Ticking a box in the preview ticks the item in the text, recording when
// as on the card (the field is hidden, so not through its undo; unticking
// undoes it); the preview redraws with the date chip, keeping the focus.
notesPreview?.addEventListener('change', async e => {
  if (!e.target.matches('.task-list-item-checkbox')) return;
  const idx = [...notesPreview.querySelectorAll('.task-list-item-checkbox')].indexOf(e.target);
  app.descTextarea.value = toggleCheckboxText(app.descTextarea.value, idx, false);
  await fetchNotesPreview();
  notesPreview.focus({ preventScroll: true });
});
// Tab goes from field to field (Title, Description, Tags, the dates …):
// the formatting bar's buttons are a click or Ctrl+B/I/K away, not a Tab.
notesBar?.querySelectorAll('button').forEach(b => { b.tabIndex = -1; });
if (app.descTextarea) {
  // A private task's files go where a private page's do (the user's folder).
  const privateDir = app.appEl?.dataset.privateFolder;
  markdownEditor(app.descTextarea, { bar: notesBar, attachInput: document.getElementById('task-edit-attach' + app.sfx),
                                 tabIndents: false, page: () => app.editPrivate() ? privateDir + '/task' : '' });
}

const importanceSelect = document.getElementById('task-edit-importance' + app.sfx);

// The fields a task rarely has (Repeat with Every and From done, Days
// before due, Keep after done) show only when set as the dialog opens, or
// once asked for: the line under the fields (.task-add-fields) has a button
// for each one not shown, which shows it and goes to it; Keep is ticked by
// it, since asking for the box is asking for that.
// A field stays for as long as the dialog is open, also emptied again.
const propsEl = document.getElementById('task-edit-props' + app.sfx);
const moreEl = document.getElementById('task-edit-more' + app.sfx);
const recNote = document.getElementById('task-edit-rec-note' + app.sfx);
// (Assignee is a field of its own above the boxes.)
const fieldEl = name => (name === 'assignee' ? app.form : propsEl).querySelector(`[data-field="${name}"]`);
const fieldShown = name => { const el = fieldEl(name); return !!el && !el.hidden; };
function showField(name, show = true) {
  const el = fieldEl(name);
  if (el) el.hidden = !show;
}
// Which buttons the line has: no Repeat for a keep task (it can't repeat),
// Days before due only with a due date, no Assignee for a task in a private
// list (it has none).
function syncMore() {
  const can = {
    assignee: assigning(),
    repeat: !app.keepCb.checked,
    lead: !!document.getElementById('task-edit-due' + app.sfx).value,
    keep: true,
  };
  let any = false;
  moreEl.querySelectorAll('[data-more]').forEach(btn => {
    btn.hidden = fieldShown(btn.dataset.more) || !can[btn.dataset.more];
    any = any || !btn.hidden;
  });
  moreEl.hidden = !any;
}
moreEl.addEventListener('click', e => {
  const name = e.target.closest('[data-more]')?.dataset.more;
  if (!name) return;
  showField(name);
  const box = { keep: app.keepCb }[name];
  if (box) {
    box.checked = true;
    box.dispatchEvent(new Event('change', { bubbles: true }));
  }
  syncDates('more');
  // A Repeat that is off (no date yet) can't take the focus: the first field that can.
  [...fieldEl(name).querySelectorAll('input, select')].find(el => !el.disabled)?.focus();
});
let privateWas = false;   // as the modal opened: was the task in a private list

// Edit modal Tags field: each tag is a chip; the input holds the one being
// typed. Completing it (Tab/Enter on a suggestion), typing a comma or a
// space, or Enter on a tag no suggestion matches makes it a chip (Enter on
// an empty field saves); Backspace at the start removes the last chip.
const editTagsInput = document.getElementById('task-edit-tags' + app.sfx);
const cleanTag = t => t.trim().replace(/^#+/, '').replace(/\s+/g, '-');
// A chip field of tags: a comma or a space (typed or pasted: "#a #b, c")
// commits everything before it as chips. Tags are one word each, so a
// space ends one rather than becoming part of it.
function chipsOnSeparators(input, chips) {
  input.addEventListener('input', () => {
    const parts = input.value.split(/[\s,]+/);
    if (parts.length < 2) return;
    const last = parts.pop();
    chips.set([...chips.words(), ...parts.map(cleanTag).filter(Boolean)], true);
    input.value = last;
  });
}
const editChips = editTagsInput && app.chipField(editTagsInput, { label: t => '#' + t });
if (editTagsInput) {
  chipsOnSeparators(editTagsInput, editChips);
  // Enter with no suggestion open (the dropdown takes it first when one
  // is): a typed tag becomes a chip rather than saving the task.
  editTagsInput.addEventListener('keydown', e => {
    if (e.key !== 'Enter' || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.isComposing) return;
    const typed = cleanTag(editTagsInput.value);
    if (!typed) return;
    e.preventDefault();
    editChips.add(typed);
    editTagsInput.value = '';
  });
}
// Every tag in the field: the chips plus a half-typed one (Save takes it too).
function editTags() {
  return [...editChips.words(), cleanTag(editTagsInput.value)].filter(Boolean);
}
const editTagAc = editTagsInput && attachTagAc(editTagsInput, {
  word: () => tagWordOf(editTagsInput),
  suggest: w => {
    const committed = editChips.words().map(t => t.toLowerCase());
    const counts = tagCounts(st.tasks);
    for (const tag of Object.keys(counts)) if (committed.includes(tag.toLowerCase())) delete counts[tag];
    return rankTags(w.partial, counts);
  },
  accept: tag => {
    editChips.add(tag);
    editTagsInput.value = '';
    editTagsInput.focus();
  },
});

// The Assignee field (with users): one user's name as a chip, completed
// from the users who can be assigned (your own name first). Only a name
// that is one is taken; a half-typed one keeps the modal open and says so.
// The name of someone who can no longer be given tasks stays as the task
// has it (struck through) until it is taken off or replaced. The field
// shows when the task has an assignee, or once asked for (showField). A
// private task has no assignee: the field hides while its list is private.
const assigneeInput = document.getElementById('task-edit-assignee' + app.sfx);
const assigneeField = document.getElementById('task-edit-assignee-field' + app.sfx);
const cleanName = v => v.trim().replace(/^@/, '').toLowerCase();
// The user a typed name means ('' none): a user who can be assigned, or me.
function userNamed(typed) {
  const name = cleanName(typed) === 'me' ? people.me : cleanName(typed);
  return people.users?.has(name) ? name : '';
}
const assigneeChip = assigneeInput && app.chipField(assigneeInput, {
  onChange: () => syncAutoList(),
  label: n => '@' + n,
  mark: (chip, n) => {
    if (!people.users || people.users.has(n)) return;
    chip.classList.add('tag-chip-former');
    chip.title = `${n} can no longer be given tasks`;
  },
});
function setAssignee(name) {
  if (!assigneeInput) return;
  assigneeChip.set(name ? [name] : [], true);
  assigneeInput.value = '';
  assigneeInput.setCustomValidity('');
  syncAutoList();
}
// What Save sends: a name, null for no one, undefined when what is typed
// names no one who can be assigned.
function editAssignee() {
  const typed = cleanName(assigneeInput.value);
  if (!typed) return assigneeChip.words()[0] || null;
  if (typed === 'none') return null;
  return userNamed(typed) || undefined;
}
// A new task's list that nobody chose: the user's default list when it is
// private (their first pinned list, or the list "private" of one who pinned
// nothing: fallbackList in go/tasklists.go), for as long as the List field
// holds what the dialog put there. Such a task can still be assigned: a task given
// to someone is shared work, so with an assignee (the field, or the title's
// @name) it is in no list, and without one again in the private list. The
// line under the title says which. Choosing a list oneself ends it.
let autoList = '';
let autoSetting = false;
// How often the dialog was opened: what a late answer checks before it
// fills a field in.
let openCount = 0;
app.listSelect?.addEventListener('change', () => { if (!autoSetting) autoList = ''; });
function syncAutoList() {
  if (!autoList || !app.listSelect || !assigneeInput) return;
  const assigned = !!assigneeChip.words().length || !!assigneeInput.value.trim() || !!quickParse()?.assignee;
  const want = assigned ? '' : autoList;
  if (app.listSelect.value === want) return;
  autoSetting = true;
  app.listSelect.value = want;
  app.listSelect.dispatchEvent(new Event('change'));
  autoSetting = false;
}
const assigning = () => !!assigneeInput && (!app.editPrivate() || !!autoList);
function syncAssigneeField() {
  if (!assigneeField) return;
  showField('assignee', assigning() && (!!assigneeChip.words().length || !!assigneeInput.value.trim()));
}
app.listSelect?.addEventListener('change', () => { syncAssigneeField(); syncMore(); });
const assigneeAc = assigneeInput && attachTagAc(assigneeInput, {
  prefix: '@',
  // Something typed, if only "@" (which lists everyone): a click into the
  // empty field offers nothing, so Tab moves on without assigning anyone.
  word: () => assigneeInput.value.trim() ? { partial: cleanName(assigneeInput.value) } : null,
  suggest: w => {
    const counts = {};
    for (const name of people.users || []) counts[name] = 1;
    for (const t of st.tasks) if (t.assignee in counts) counts[t.assignee]++;
    if (people.me in counts) counts[people.me] += st.tasks.length + 1;   // yourself first
    delete counts[assigneeChip.words()[0]];
    return rankTags(w.partial, counts);
  },
  accept: name => { setAssignee(name); assigneeInput.focus(); },
});
if (assigneeInput) {
  loadPeople();
  assigneeInput.addEventListener('input', () => { assigneeInput.setCustomValidity(''); syncAutoList(); });
  // Enter with no suggestion open: a typed name becomes the chip rather
  // than saving the task (as in Tags); Enter on an empty field saves.
  assigneeInput.addEventListener('keydown', e => {
    if (e.key !== 'Enter' || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.isComposing) return;
    if (!assigneeInput.value.trim()) return;
    e.preventDefault();
    const name = editAssignee();
    if (name === undefined) return refuseAssignee();
    setAssignee(name);
  });
}
function refuseAssignee() {
  assigneeInput.setCustomValidity(`“${assigneeInput.value.trim()}” is no user a task can be assigned to`);
  assigneeInput.reportValidity();
}

// A new task's title: the #tag shorthand (TasksCore.parseQuickAdd) gets the
// same suggestions, from every tag in use and the lists (#shop puts the
// task in the list shop: quickList).
if (app.titleInput) attachTagAc(app.titleInput, {
  word: () => document.getElementById('task-edit-id' + app.sfx).value ? null : hashWordAtCaret(app.titleInput),
  suggest: w => rankTags(w.partial, { ...Object.fromEntries([...app.listSet].filter(r => !r.includes('/')).map(r => [r, 1])), ...tagCounts(st.tasks) }, w.partial ? 8 : 12),
  accept: (tag, w) => { insertHashTag(app.titleInput, tag, w); syncQuickPreview(); },
});
// And the @name shorthand, from the users who can be assigned.
if (app.titleInput && assigneeInput) attachTagAc(app.titleInput, {
  prefix: '@',
  word: () => document.getElementById('task-edit-id' + app.sfx).value || !assigning() || !people.users
    ? null : hashWordAtCaret(app.titleInput, '@'),
  suggest: w => {
    const counts = {};
    for (const name of people.users) counts[name] = 1;
    for (const t of st.tasks) if (t.assignee in counts) counts[t.assignee]++;
    if (people.me in counts) counts[people.me] += st.tasks.length + 1;
    return rankTags(w.partial, counts);
  },
  accept: (name, w) => { insertHashTag(app.titleInput, name, w); syncQuickPreview(); },
});

// ── Add task ───────────────────────────────────────────────────────────────

app.addBtn.addEventListener('click', () => openEditModal(null));

// ── Edit modal ─────────────────────────────────────────────────────────────

// Quick-add shorthand (TasksCore.parseQuickAdd) — new tasks only: a title
// ending in "tomorrow #work due:fri" fills those fields on save. The line
// under the title shows what was recognised, so nothing is a surprise.
const quickPreview = document.getElementById('task-edit-quick' + app.sfx);
function quickParse() {
  if (document.getElementById('task-edit-id' + app.sfx).value) return null;   // editing
  const q = parseQuickAdd(app.titleInput.value, todayStr(), quickPeople());
  return q.found ? q : null;
}
// Whom the shorthand's @name may name: no one on a private task, nor
// without users.
const quickPeople = () => assigning() ? people : null;
// The shorthand's #words: one that names a list (the first) is the task's
// list, the others its tags.
function quickList(q) {
  const list = q.tags.find(t => app.listSet.has(t)) || '';
  return { list, tags: q.tags.filter(t => t !== list) };
}
function quickText(q) {
  const { list, tags } = quickList(q);
  return [
    q.start_date && `📅 start ${formatDate(q.start_date)}`,
    q.due_date && `⏰ due ${formatDate(q.due_date)}`,
    q.importance && IMPORTANCE[q.importance][1].toLowerCase(),
    list && `list ${list}`,
    ...tags.map(t => '#' + t),
    q.assignee && '@' + q.assignee,
  ].filter(Boolean).join(' · ');
}

// Batch mode (the editor's Make task with several lines): the modal makes
// one task per title. The titles are listed instead of the title field,
// and the fields apply to all of them; shorthand on a line adds to them.
let batchTitles = null;
const titleField = app.titleInput.closest('.task-field');
const batchField = document.getElementById('task-edit-batch' + app.sfx);
function setBatch(titles) {
  batchTitles = titles?.length ? titles : null;
  titleField.style.display = batchTitles ? 'none' : '';
  app.titleInput.required = !batchTitles;
  if (!batchField) return;
  batchField.style.display = batchTitles ? '' : 'none';
  if (!batchTitles) return;
  document.getElementById('task-edit-batch-label' + app.sfx).textContent = `${batchTitles.length} tasks`;
  const list = document.getElementById('task-edit-batch-list' + app.sfx);
  list.replaceChildren(...batchTitles.map(t => {
    const q = parseQuickAdd(t, todayStr(), quickPeople());
    const li = document.createElement('li');
    li.textContent = q.title;
    if (q.found) {
      const extra = document.createElement('span');
      extra.className = 'task-quick-preview';
      extra.textContent = ' ' + quickText(q);
      li.append(extra);
    }
    return li;
  }));
}

function syncQuickPreview() {
  const q = !batchTitles && quickParse();
  quickPreview.hidden = !q;
  if (!q) return;
  quickPreview.textContent = quickText(q);
}

// The List field: the lists the user sees, their own private ones marked,
// and No list. A task is as private as its list: one in a private list is
// the user's alone, any other everyone's. A shared task stays shared, so
// the private lists are off for it; a private task leaves for a shared
// place by choosing one, and the line under the fields says what that does.
// Its last choice, New list…, asks for a name (openNewList) and makes the
// list, which is then the field's choice; the task goes into it with Save.
const NEW_LIST = '/new';   // no list's ref: a name has no slash
let listWas = '';          // the field's choice, to go back to from New list…
let listTask = null;       // the task the field was filled for (null: a new one)
function fillLists(task, ref) {
  const sel = app.listSelect;
  if (!sel) return;
  listTask = task || null;
  const shared = !!task && !task.private;   // a shared task: no private list
  const options = [new Option('No list', '')];
  for (const l of app.listChoices) {
    const twin = l.ref.startsWith('shared/');
    const o = new Option((l.private ? '🔒 ' : '') + app.listName(l.ref) + (twin ? ' (shared)' : ''), l.ref);
    o.disabled = l.private && shared;
    if (o.disabled) o.title = 'A shared task stays shared';
    options.push(o);
  }
  options.push(new Option('New list…', NEW_LIST));
  sel.replaceChildren(...options);
  sel.value = app.listSet.has(ref) ? ref : '';
  listWas = sel.value;
  showField('list', app.listChoices.length > 0);
}
// Before the field's other listeners: New list… is no list to sync to, so
// the field goes back to what it said and the name is asked for.
app.listSelect?.addEventListener('change', e => {
  const sel = app.listSelect;
  if (sel.value !== NEW_LIST) { listWas = sel.value; return; }
  e.stopImmediatePropagation();
  sel.value = listWas;
  openNewList();
}, true);
// New list…: a small dialog over the edit modal with the list's name and,
// with users, whether it is private (off for a shared task, which stays
// shared; on when the task is in a private list now). The list is made at
// once (POST /api/tasks/lists, as the dialog that moves tasks makes one),
// every task app of the page learns of it (zone:list-made), and the field
// names it.
function openNewList() {
  const sel = app.listSelect;
  const canPrivate = !!app.appEl?.dataset.privateFolder;
  const shared = !!listTask && !listTask.private;
  const dlg = document.createElement('dialog');
  dlg.className = 'modal list-new-modal';
  dlg.innerHTML = `
    <p class="modal-label">New list</p>
    <form>
      <input type="text" id="task-new-list-name" autocomplete="off" spellcheck="false" required
             aria-label="The list's name" placeholder="Its name, e.g. shop">
      ${canPrivate ? `<label class="promote-check"${shared ? ' title="A shared task stays shared"' : ''}>` +
        `<input type="checkbox" id="task-new-list-private"${shared ? ' disabled' : app.editPrivate() ? ' checked' : ''}> Private: only you see it and its tasks</label>` : ''}
      <p class="rename-error" hidden></p>
      <div class="modal-actions">
        <button type="submit" class="btn btn-primary" id="task-new-list-create">Create</button>
        <button type="button" class="btn" id="task-new-list-cancel">Cancel</button>
      </div>
    </form>`;
  document.body.appendChild(dlg);
  const nameInput = dlg.querySelector('#task-new-list-name'), privateBox = dlg.querySelector('#task-new-list-private');
  const error = dlg.querySelector('.rename-error');
  const close = () => { if (dlg.open) dlg.close(); };
  dlg.addEventListener('close', () => { dlg.remove(); sel.focus(); });
  dlg.querySelector('#task-new-list-cancel').addEventListener('click', close);
  dlg.addEventListener('click', ev => { if (ev.target === dlg) close(); });
  // Keys stay in this dialog: the edit modal under it has its own.
  dlg.addEventListener('keydown', ev => ev.stopPropagation());
  let creating = false;
  dlg.querySelector('form').addEventListener('submit', async ev => {
    ev.preventDefault();
    ev.stopPropagation();
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
    } catch (_) { why = 'The list couldn’t be made: no answer from Zone'; }
    creating = false;
    if (!made) {
      error.textContent = why;
      error.hidden = false;
      nameInput.focus();
      return;
    }
    document.dispatchEvent(new CustomEvent('zone:list-made', { detail: made }));
    fillLists(listTask, made.ref);
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    close();
  });
  dlg.showModal();
  nameInput.focus();
}
function syncListNote() {
  if (!app.listNote) return;
  const users = document.body.dataset.users === '1';
  const isPrivate = app.editPrivate();
  const text = isPrivate ? 'Private: only you see this task.'
    : privateWas ? 'Everyone who uses the wiki will see and change this task. It can’t be made private again.' : '';
  app.listNote.hidden = !users || !text;
  app.listNote.textContent = text;
}
app.listSelect?.addEventListener('change', syncListNote);

// Where a new task goes, under its title: the list the List field names
// (or the title's #shorthand), or none, and whether that makes it private.
const targetEl = document.getElementById('task-edit-target' + app.sfx);
function syncTarget() {
  if (!targetEl) return;
  const isNew = !document.getElementById('task-edit-id' + app.sfx).value && !batchTitles;
  targetEl.hidden = !isNew;
  if (!isNew) return;
  const q = quickParse();
  const ref = q && quickList(q).list || app.listSelect?.value || '';
  // With users the line says who sees the task: a task in a shared list or
  // in none is everyone's, and can't be made private later.
  const where = ref ? `Adds to the list ${app.listName(ref)}` : 'Adds to no list';
  const who = app.privateLists.has(ref) ? ', private' : document.body.dataset.users === '1' ? ': everyone sees it' : '';
  targetEl.textContent = where + who + '.';
}
app.listSelect?.addEventListener('change', syncTarget);
app.listSelect?.addEventListener('change', () => syncQuickPreview());
app.titleInput.addEventListener('input', () => { syncAutoList(); syncTarget(); });
app.titleInput.addEventListener('input', syncQuickPreview);

// The modal's History section: the task's logged events, newest first
// (logEvent in go/taskstore.go), with its creation as the oldest. Closed on every open;
// hidden for a new task. Tasks from before the log existed get their
// creation from created_at.
const historyDetails = document.getElementById('task-edit-history' + app.sfx);
const HISTORY_TEXT = {
  created: () => 'Created', done: () => 'Done', reopened: () => 'Reopened',
  wontdo: () => "Won't do", skipped: () => 'Skipped',
  private: () => 'Made private', shared: () => 'Made shared',
  start: e => `Start ${historyDate(e.from)} → ${historyDate(e.to)}`,
  due: e => `Due ${historyDate(e.from)} → ${historyDate(e.to)}`,
  repeat: e => `Repeats: next ${historyDate(e.to)}`,
  assigned: e => (e.to ? `Assigned to ${e.to}` : 'Unassigned') + (e.from ? ` (was ${e.from})` : ''),
};
function historyDate(d) { return d ? shortDate(d) : '—'; }
function renderHistory(task) {
  const entries = (task?.history || []).filter(e => HISTORY_TEXT[e.event]);
  if (task?.created_at && !entries.some(e => e.event === 'created')) {
    entries.unshift({ at: task.created_at.slice(0, 16), event: 'created' });
  }
  historyDetails.hidden = !entries.length;
  historyDetails.open = false;
  document.getElementById('task-edit-history-count' + app.sfx).textContent = entries.length || '';
  document.getElementById('task-edit-history-list' + app.sfx).innerHTML = entries.slice().reverse().map(e =>
    `<li><span class="task-history-at">${esc(commentStamp(e))}</span> ` +
    `${esc(HISTORY_TEXT[e.event](e))}</li>`).join('');
}

// The modal's Comments: markdown, a line or several, newest first. They are
// saved at once through their own routes (not with Save), so a comment is
// never lost to Cancel. Hidden for a new task. "Write a comment…" shows the field
// (Ctrl+Enter or Add saves it, Esc or Cancel puts it away); ✎ puts the
// field in a comment's place, with its text; × deletes one with undo.
const commentsBox = document.getElementById('task-edit-comments' + app.sfx);
const commentBox = document.getElementById('task-edit-comment-box' + app.sfx);
const commentInput = document.getElementById('task-edit-comment' + app.sfx);
const commentOpenBtn = document.getElementById('task-edit-comment-open' + app.sfx);
const commentAddBtn = document.getElementById('task-edit-comment-add' + app.sfx);
const commentList = document.getElementById('task-edit-comment-list' + app.sfx);
st.commentsTaskId = null;
let editingCid = null;   // the comment the field edits; null: a new one
const commentItem = cid => cid ? [...commentList.children].find(li => li.dataset.comment === cid) : null;
function renderComments(task) {
  st.commentsTaskId = task?.id || null;
  commentsBox.hidden = !task;
  if (!task) return;
  const comments = task.comments || [];
  document.getElementById('task-edit-comments-count' + app.sfx).textContent = comments.length || '';
  // The field may be in a comment's place: out of the list while it is redrawn.
  const focused = document.activeElement === commentInput;
  commentList.before(commentBox);
  commentList.innerHTML = comments.slice().reverse().map(c =>
    `<li data-comment="${esc(c.id)}"><span class="task-history-at">${esc(commentStamp(c))}</span>` +
    `<div class="task-comment-text task-comment-body page-content">${app.htmlOf('comments', c.text)}</div>` +
    `<button type="button" class="task-comment-btn" data-comment-action="edit" tabindex="-1" title="Edit" aria-label="Edit comment">✎</button>` +
    `<button type="button" class="task-comment-btn" data-comment-action="delete" tabindex="-1" title="Delete" aria-label="Delete comment">×</button>` +
    '</li>').join('');
  // What the comments hold (links, checkboxes) is no stop for Tab either.
  commentList.querySelectorAll('a, input, summary').forEach(el => { el.tabIndex = -1; });
  renderFigures(commentList);
  if (!commentBox.hidden) {
    placeCommentBox();
    if (focused) commentInput.focus();
  }
}
// The field goes where it is used: in the edited comment's place, or (a new
// comment, or the edited one is gone) above the list.
function placeCommentBox() {
  const li = commentItem(editingCid);
  if (li) {
    li.classList.add('task-comment-editing');
    li.append(commentBox);
  } else {
    editingCid = null;
    commentList.before(commentBox);
  }
  commentAddBtn.textContent = editingCid ? 'Save' : 'Add';
}
function openCommentBox(cid = null, text = '') {
  commentItem(editingCid)?.classList.remove('task-comment-editing');
  editingCid = cid;
  commentInput.value = text;
  commentBox.hidden = false;
  commentOpenBtn.hidden = true;
  commentOpenBtn.setAttribute('aria-expanded', 'true');
  placeCommentBox();
  syncCommentAdd();
  commentInput.focus();
  commentInput.setSelectionRange(text.length, text.length);
}
function closeCommentBox() {
  commentItem(editingCid)?.classList.remove('task-comment-editing');
  editingCid = null;
  commentInput.value = '';
  commentBox.hidden = true;
  commentList.before(commentBox);
  commentOpenBtn.hidden = false;
  commentOpenBtn.setAttribute('aria-expanded', 'false');
}
// Add (or Save) is a stop for Tab only with a comment to add.
function syncCommentAdd() { commentAddBtn.tabIndex = commentInput.value.trim() ? 0 : -1; }
commentInput.addEventListener('input', syncCommentAdd);

// A comment route answered with the task: take it in and redraw.
async function commentWrite(url, method, body) {
  const res = await fetch(url, {
    method, headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) { await noticeFromResponse(res, 'Comment not saved'); return null; }
  const updated = await res.json();
  await renderTexts(textsOf([updated]));
  const idx = st.tasks.findIndex(t => t.id === updated.id);
  if (idx !== -1) st.tasks[idx] = updated;
  if (st.commentsTaskId === updated.id && app.modal.open) renderComments(updated);
  app.render();
  app.refreshOtherInstances(app.instanceId);
  return updated;
}
const commentsUrl = id => `/api/tasks/${encodeURIComponent(id)}/comments`;
// Saves what the field holds (a new comment, or the edited one's text) and
// puts the field away; an empty or unchanged text only puts it away. False
// when the server refused: the field stays, with its text.
async function saveComment() {
  const taskId = st.commentsTaskId;
  if (commentBox.hidden || !taskId) return true;
  if (commentInput.disabled) return false;   // being saved
  const text = commentInput.value.trim();
  const comment = editingCid && st.tasks.find(t => t.id === taskId)?.comments?.find(c => c.id === editingCid);
  if (!text || comment?.text === text) { closeCommentBox(); return true; }
  const hadFocus = commentBox.contains(document.activeElement);
  commentInput.disabled = true;
  const ok = comment
    ? await commentWrite(`${commentsUrl(taskId)}/${encodeURIComponent(comment.id)}`, 'PUT', { text })
    : await commentWrite(commentsUrl(taskId), 'POST', { text });
  commentInput.disabled = false;
  if (!ok) { commentInput.focus(); return false; }
  closeCommentBox();
  if (hadFocus && app.modal.open) commentOpenBtn.focus();
  return true;
}
// The page editor's markdown helpers: lists go on, [[ completes pages, a
// pasted URL asks for its name, a pasted or dropped file is uploaded.
markdownEditor(commentInput, { tabIndents: false, page: () => app.editPrivate() ? app.appEl?.dataset.privateFolder + '/task' : '' });
commentInput.addEventListener('keydown', e => {
  if (e.defaultPrevented || e.isComposing) return;   // the completion's key
  // Esc puts the field away, not the modal.
  if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeCommentBox(); commentOpenBtn.focus(); }
});
commentOpenBtn.addEventListener('click', () => openCommentBox());
commentAddBtn.addEventListener('click', saveComment);
document.getElementById('task-edit-comment-cancel' + app.sfx).addEventListener('click', () => { closeCommentBox(); commentOpenBtn.focus(); });
commentList.addEventListener('click', async e => {
  const btn = e.target.closest('[data-comment-action]');
  const li = btn?.closest('[data-comment]');
  if (!li || !st.commentsTaskId) return;
  const taskId = st.commentsTaskId, cid = li.dataset.comment;
  // What the field holds is saved before it is used for another comment.
  if (!(await saveComment()) || st.commentsTaskId !== taskId) return;
  const task = st.tasks.find(t => t.id === taskId);
  const comment = task?.comments?.find(c => c.id === cid);
  if (!comment) return;
  if (btn.dataset.commentAction === 'delete') {
    const before = task.comments;
    app.dismissUndo();
    commentWrite(`${commentsUrl(taskId)}/${encodeURIComponent(cid)}`, 'DELETE').then(ok => {
      if (ok) app.showUndo('Comment deleted', () => app.updateTask(taskId, { comments: before }).then(t => {
        if (t && st.commentsTaskId === taskId && app.modal.open) renderComments(t);
      }));
    });
    return;
  }
  openCommentBox(cid, comment.text);
});

// opts (new tasks): `list` (a ref, instead of the view's or the default
// list), `tags` (comma-separated; given, even empty, it
// replaces the filter's tags), `keep` (instead of the embed's keep=), and from the editor's Make
// task a starting `title` and `description`, a `start_date` (null: none,
// instead of Focus's today) and `onCreated(task)`, called once it is saved.
// `titles` instead of `title` makes one task per title (batch mode), and
// onCreated gets them all (a null where creating failed, then it stops).
let onCreated = null;
function openEditModal(taskId, opts) {
  const task = taskId ? st.tasks.find(t => t.id === taskId) : null;
  const defaultTags = opts?.tags || '';
  const givenTags = !!opts && 'tags' in opts;
  onCreated = !task && opts?.onCreated || null;
  setBatch(!task && opts?.titles);
  document.getElementById('task-edit-id' + app.sfx).value = task ? task.id : '';
  document.getElementById('task-edit-title' + app.sfx).value = task ? task.title : opts?.title || '';
  document.getElementById('task-edit-desc' + app.sfx).value = task ? task.description || '' : opts?.description || '';
  setNotesPreview(false, false);
  document.getElementById('task-edit-due' + app.sfx).value = task ? task.due_date || '' : '';
  document.getElementById('task-edit-start' + app.sfx).value = task ? task.start_date || ''
    : opts && 'start_date' in opts ? opts.start_date || '' : (st.activeView === 'focus' ? dateOffset(0) : '');
  const startTags = task ? task.tags || []
    : givenTags ? defaultTags.split(',').map(cleanTag).filter(Boolean) : app.filterDefaultTags();
  editChips.set(startTags, true);
  editTagsInput.value = '';
  renderHistory(task);
  closeCommentBox();
  renderComments(task);
  app.titleInput.placeholder = task ? '' : 'e.g. Call Bob tomorrow #work due:fri';
  syncQuickPreview();
  importanceSelect.value = task?.importance || '';
  app.keepCb.checked = task ? !!task.keep : opts && 'keep' in opts ? !!opts.keep : app.initKeep;
  fillLists(task, task ? app.taskList(task) || '' : opts && 'list' in opts ? opts.list || '' : app.newTaskList());
  privateWas = !!task && !!task.private;
  syncListNote();
  // Nobody chose the list when it is the default one in a view of no one list.
  autoList = !task && !app.initList && app.privateLists.has(app.defaultList) && !!app.listSelect && app.listSelect.value === app.defaultList
    ? app.defaultList : '';
  // A new task starts with the user the filter names, as with its tags.
  // One whose My tasks hides unassigned tasks gets the task they add where
  // no one list is in view (it goes to their default list, or none), unless
  // that list is private.
  const mineNew = !task && !(opts && 'assignee' in opts) && app.newMine && !app.initList && !app.noList && !!app.listSelect
    && app.listSelect.value === app.defaultList && !app.editPrivate();
  setAssignee(task ? task.assignee || '' : opts && 'assignee' in opts ? opts.assignee || '' : app.filterDefaultAssignee() || mineNew && people.me || '');
  // (Who that is may still be on its way: c t right after the page loaded.)
  const opened = ++openCount;
  if (mineNew && !people.me && assigneeInput) {
    loadPeople().then(() => {
      if (opened !== openCount || !app.modal.open || assigneeChip.words().length || assigneeInput.value.trim()) return;
      setAssignee(people.me);
      syncAssigneeField();
    });
  }
  syncAssigneeField();
  const recType = task?.recurrence?.type || '';
  app.recTypeSelect.value = recType;
  document.getElementById('task-edit-rec-interval' + app.sfx).value = task?.recurrence?.interval || 1;
  const recFromCb = document.getElementById('task-edit-rec-from' + app.sfx);
  if (recFromCb) recFromCb.checked = task?.recurrence?.from === 'completion';
  showField('repeat', !!recType);
  showField('lead', false);
  showField('keep', app.keepCb.checked);
  syncTarget();
  app.wontDoBtn.style.display = task && !task.closed_at ? '' : 'none';
  syncDates('open');
  app.modal.showModal();
  fitNotes();
  if (task && opts?.focusComment) openCommentBox();
  else (batchTitles ? editTagsInput : app.titleInput).focus();
}

// Every N / From done are only meaningful with a repeat type: they show
// once Repeat is both enabled and set. "Every 2 weeks": the unit follows
// the type and the number.
const REC_UNIT = { daily: 'day', weekly: 'week', monthly: 'month', yearly: 'year' };
function syncRecFields() {
  const on = !app.recTypeSelect.disabled && !!app.recTypeSelect.value;
  const intervalEl = document.getElementById('task-edit-rec-interval' + app.sfx);
  intervalEl.disabled = !on;
  intervalEl.closest('.task-prop').hidden = !on;
  const unit = REC_UNIT[app.recTypeSelect.value] || '';
  document.getElementById('task-edit-rec-unit' + app.sfx).textContent =
    unit && (parseInt(intervalEl.value, 10) || 1) > 1 ? unit + 's' : unit;
  const rf = document.getElementById('task-edit-rec-from' + app.sfx);
  if (rf) {
    rf.disabled = !on;
    rf.closest('.task-prop').hidden = !on;
  }
}
app.recTypeSelect.addEventListener('change', syncRecFields);
document.getElementById('task-edit-rec-interval' + app.sfx).addEventListener('input', syncRecFields);

// ── Modal date triggers (custom calendar popup, no native <input type=date>) ──
// Each date field is a hidden <input> holding the value plus a visible button
// showing it; clicking the button opens the same in-page calendar the cards use.
function refreshDateTriggers() {
  app.modal.querySelectorAll('.date-trigger').forEach(btn => {
    const input = document.getElementById(btn.dataset.dateTrigger);
    btn.textContent = input && input.value ? formatDate(input.value) : (btn.dataset.placeholder || 'Set date');
    btn.classList.toggle('date-trigger--set', !!(input && input.value));
  });
}
app.modal.querySelectorAll('.date-trigger').forEach(btn => {
  btn.addEventListener('click', () => {
    const input = document.getElementById(btn.dataset.dateTrigger);
    openDatePicker(input.value || '', d => {
      input.value = d || '';
      syncDates(btn.dataset.dateTrigger === 'task-edit-due' + app.sfx ? 'due' : 'start');
    }, btn, todayStr(), app.modal);
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
  const leadEl  = document.getElementById('task-edit-lead' + app.sfx);
  const dueEl   = document.getElementById('task-edit-due' + app.sfx);
  const startEl = document.getElementById('task-edit-start' + app.sfx);
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
  syncDateWarning(changed);
  refreshDateTriggers();
  // Repeat requires at least one date to be meaningful, and a keep task
  // never repeats (Keep and Repeat both say what happens after done).
  const noDate = !dueEl.value && !startEl.value;
  app.recTypeSelect.disabled = noDate || app.keepCb.checked;
  const why = app.keepCb.checked ? 'A keep task does not repeat.'
    : noDate ? 'To repeat, a task needs a due or start date.' : '';
  app.recTypeSelect.closest('.task-prop').title = why;
  // Said under the fields too, where Repeat shows and is off.
  recNote.hidden = !why || !fieldShown('repeat');
  recNote.textContent = why;
  syncRecFields();
  syncMore();
}
app.keepCb.addEventListener('change', () => syncDates('keep'));

// Start after Due: a hint under the dates with a one-click fix that moves
// the date not just changed (the deadline, or the start when Due was moved
// before it). It doesn't block saving: a late task may well start later.
const dateWarn = document.getElementById('task-edit-date-warn' + app.sfx);
const dateWarnFix = document.getElementById('task-edit-date-warn-fix' + app.sfx);
let dateWarnMove = null;   // 'due' | 'start'
function syncDateWarning(changed) {
  if (!dateWarn) return;
  const due = document.getElementById('task-edit-due' + app.sfx).value;
  const start = document.getElementById('task-edit-start' + app.sfx).value;
  const late = !!(due && start && start > due);
  dateWarn.hidden = !late;
  if (!late) return;
  if (changed === 'open') dateWarnMove = null;
  if (changed === 'due' || changed === 'start') dateWarnMove = changed === 'due' ? 'start' : 'due';
  if (!dateWarnMove) dateWarnMove = 'due';
  document.getElementById('task-edit-date-warn-text' + app.sfx).textContent =
    `Starts after its deadline (${formatDate(due)}).`;
  dateWarnFix.textContent = dateWarnMove === 'due'
    ? `Move deadline to ${formatDate(start)}` : `Move start to ${formatDate(due)}`;
}
dateWarnFix?.addEventListener('click', () => {
  const due = document.getElementById('task-edit-due' + app.sfx);
  const start = document.getElementById('task-edit-start' + app.sfx);
  if (dateWarnMove === 'due') due.value = start.value; else start.value = due.value;
  syncDates(dateWarnMove);
});
document.getElementById('task-edit-lead' + app.sfx).addEventListener('input', () => syncDates('lead'));

app.cancelBtn.addEventListener('click', () => { editTagAc?.hide(); assigneeAc?.hide(); app.modal.close(); });
// Closed without saving (Cancel, Esc): no callback later by mistake.
// Focus would stay on a field of the closed dialog, and the task app's keys
// (j/k, Enter, Esc …) ignore keys typed into a field. Esc's cancel comes
// at once (close only after the key), so the next key already works.
const blurInside = () => { if (app.modal.contains(document.activeElement)) document.activeElement.blur(); };
app.modal.addEventListener('cancel', blurInside);
app.modal.addEventListener('close', () => { onCreated = null; blurInside(); });

// Ctrl+Enter / Ctrl+S save the task; Ctrl+Enter in the comment field saves
// the comment.
app.modal.addEventListener('keydown', e => {
  if (!((e.key === 'Enter' || e.key === 's') && (e.ctrlKey || e.metaKey))) return;
  e.preventDefault(); e.stopPropagation();
  if (e.key === 'Enter' && e.target === commentInput) saveComment();
  else app.form.requestSubmit();
}, true);
// Alt+C: the comment field ("Write a comment…" is no stop for Tab).
app.modal.addEventListener('keydown', e => {
  if (e.code !== 'KeyC' || !e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
  if (!st.commentsTaskId) return;   // a new task has no comments yet
  e.preventDefault(); e.stopPropagation();
  if (commentBox.hidden) openCommentBox(); else commentInput.focus();
}, true);
// F2: Edit | Preview of the notes, as in the editor (not the editor's,
// on its page).
app.modal.addEventListener('keydown', e => {
  if (e.key !== 'F2' || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
  e.preventDefault(); e.stopPropagation();
  setNotesPreview(!previewing());
}, true);

app.form.addEventListener('submit', async e => {
  e.preventDefault();
  // A name typed into Assignee that names no one: say so, save nothing.
  const assignee = assigning() ? editAssignee() : null;
  if (assignee === undefined) return refuseAssignee();
  // A comment still in its field is saved with the task.
  if (!(await saveComment())) return;
  const id = document.getElementById('task-edit-id' + app.sfx).value;
  const data = {
    title: document.getElementById('task-edit-title' + app.sfx).value.trim(),
    description: document.getElementById('task-edit-desc' + app.sfx).value.trim(),
    due_date: document.getElementById('task-edit-due' + app.sfx).value || null,
    start_date: document.getElementById('task-edit-start' + app.sfx).value || null,
    tags: editTags(),
    rec_type: app.keepCb.checked ? null : app.recTypeSelect.value || null,
    rec_interval: parseInt(document.getElementById('task-edit-rec-interval' + app.sfx).value) || 1,
    rec_from: document.getElementById('task-edit-rec-from' + app.sfx)?.checked ? 'completion' : 'due',
    keep: app.keepCb.checked,
    importance: importanceSelect.value || 'normal',
    // Its list, and with it who sees it; leaving a private list for a shared
    // place shares the task (the line under the fields said so).
    ...(app.listSelect ? { list: app.listSelect.value } : {}),
    ...(privateWas && !app.editPrivate() ? { share: true } : {}),
    ...(assigning() ? { assignee } : {}),
  };
  if (batchTitles && !id) {
    const titles = batchTitles, created = onCreated;
    onCreated = null;
    app.modal.close();
    const today = todayStr(), made = [];
    for (const title of titles) {
      const q = parseQuickAdd(title, today, quickPeople());
      const ql = quickList(q);
      const task = await app.createTask({
        ...data, title: q.title,
        start_date: q.start_date || data.start_date, due_date: q.due_date || data.due_date,
        importance: q.importance || data.importance,
        ...(ql.list ? { list: ql.list } : {}),
        tags: [...new Set([...data.tags, ...ql.tags])],
        ...(q.assignee ? { assignee: q.assignee } : {}),
      });
      made.push(task);
      if (!task) break;
    }
    created?.(made);
    return;
  }
  // Shorthand in a new task's title wins over the fields (the preview said so).
  const quick = !id && quickParse();
  if (quick) {
    data.title = quick.title;
    if (quick.start_date) data.start_date = quick.start_date;
    if (quick.due_date) data.due_date = quick.due_date;
    const ql = quickList(quick);
    if (ql.list) data.list = ql.list;
    data.tags = [...new Set([...data.tags, ...ql.tags])];
    if (quick.importance) data.importance = quick.importance;
    if (quick.assignee) data.assignee = quick.assignee;
  }
  if (!data.title) { document.getElementById('task-edit-title' + app.sfx).focus(); return; }
  const created = onCreated;
  onCreated = null;
  app.modal.close();
  if (id) {
    await app.updateTask(id, data);
  } else if (created) {
    const task = await app.createTask(data);
    if (task) created(task);
  } else {
    // Select the new task so it is easy to find wherever the view sorts it
    // (on Focus it lands at the end, possibly below the fold). A view that
    // doesn't show it (another list, a filter) leaves the selection alone.
    const task = await app.createTask(data);
    if (task) app.unfoldFor(task.id);
    const el = task && app.listEl.querySelector(`.task-item[data-id="${CSS.escape(task.id)}"]`);
    if (el) {
      app.setSelected(task.id);
      app.flashTask(el);
    }
  }
});

// Won't do: the task is closed undone, what was typed in the modal dropped.
app.wontDoBtn.addEventListener('click', () => {
  const id = document.getElementById('task-edit-id' + app.sfx).value;
  if (!id) return;
  app.modal.close();
  app.markWontDo(id);
});

return { cleanTag, chipsOnSeparators, renderComments, openEditModal };
}
