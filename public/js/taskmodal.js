// A task app's edit modal (tasks.js initTasksInstance): a task's fields,
// its tags as chips, the dates with their pickers and warnings, the notes
// edited as a page is (with Edit | Preview), history and comments, the
// quick-add shorthand, several new tasks at once (the editor's Make task)
// and the private box. What it uses of the app comes in app (its state in
// app.st).
import TasksCore from '../tasks-core.js';
import { markdownEditor } from './mdedit.js';
import { toggleCheckboxText } from './mdtext.js';
import { renderFigures } from './figures.js';
import { openDatePicker } from './datepicker.js';
import { IMPORTANCE, attachTagAc, commentStamp, dateOffset, esc, formatDate, hashWordAtCaret, insertHashTag, noticeFromResponse, shortDate, todayStr } from './taskutil.js';
import { highlightCode, renderTexts, textsOf } from './tasktexts.js';

const { addDays, tagCounts, rankTags, parseQuickAdd } = TasksCore;

export function initTaskModal(app) {
const { st } = app;

// The notes are edited as a page is (js/mdedit.js): the formatting bar,
// completion, snippets, lists, file paste and drop. Tab stays the key to the
// next field (it still expands a snippet). Edit | Preview (or F2) switches
// the field for the notes as the card will show them, in the field's place
// and at its height, so the modal keeps its layout; it opens in Edit.
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
  // The bar's and the field's space together, so nothing below moves.
  if (show) {
    const top = (notesBar || app.descTextarea).getBoundingClientRect().top;
    notesPreview.style.height = `${app.descTextarea.getBoundingClientRect().bottom - top}px`;
  }
  notesPreview.hidden = !show;
  app.descTextarea.hidden = show;
  if (notesBar) notesBar.hidden = show;
  notesModeBtns.forEach(b => b.setAttribute('aria-pressed', String((b.dataset.notesMode === 'preview') === show)));
  // Focus follows: the hidden field would drop it out of the modal, whose
  // keys (F2 back, Esc) then wouldn't reach it. Arrows scroll the preview.
  if (show) { notesPreview.innerHTML = ''; fetchNotesPreview(); if (focus) notesPreview.focus({ preventScroll: true }); }
  else if (focus) app.descTextarea.focus();
}
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
                                 tabIndents: false, page: () => app.privateCb?.checked ? privateDir + '/task' : '' });
}

const importanceSelect = document.getElementById('task-edit-importance' + app.sfx);
let privateWas = false;   // as the modal opened: was the task private

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
const editChips = editTagsInput && app.chipField(editTagsInput, {
  label: t => '#' + t, listWord: t => app.listSet.has(t.toLowerCase()), onChange: () => syncTarget() });
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
  word: () => {
    const partial = editTagsInput.value.trim();
    return partial ? { partial } : null;
  },
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

// A new task's title: the #tag shorthand (TasksCore.parseQuickAdd) gets the
// same suggestions, from every tag in use.
if (app.titleInput) attachTagAc(app.titleInput, {
  word: () => document.getElementById('task-edit-id' + app.sfx).value ? null : hashWordAtCaret(app.titleInput),
  suggest: w => rankTags(w.partial, tagCounts(st.tasks), w.partial ? 8 : 12),
  accept: (tag, w) => { insertHashTag(app.titleInput, tag, w); syncQuickPreview(); },
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
  const q = parseQuickAdd(app.titleInput.value, todayStr());
  return q.found ? q : null;
}
function quickText(q) {
  return [
    q.start_date && `📅 start ${formatDate(q.start_date)}`,
    q.due_date && `⏰ due ${formatDate(q.due_date)}`,
    q.importance && IMPORTANCE[q.importance][1].toLowerCase(),
    ...q.tags.map(t => '#' + t),
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
    const q = parseQuickAdd(t, todayStr());
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

// A private task is shared by unticking the Private box (the note says what
// that does); a shared task stays shared, so its box is disabled.
function syncPrivateNote() {
  if (!app.privateCb) return;
  const share = privateWas && !app.privateCb.checked;
  app.privateNote.hidden = !share;
  if (share) app.privateNote.textContent = 'Everyone who uses the wiki will see and change this task.';
}
app.privateCb?.addEventListener('change', syncPrivateNote);

// Where a new task goes, under its title: the list its tags put it in (the
// first A–Z that is a list, as the server files it), or none, and whether
// it is private; it follows the Tags field and the Private box.
const targetEl = document.getElementById('task-edit-target' + app.sfx);
function syncTarget() {
  if (!targetEl) return;
  const isNew = !document.getElementById('task-edit-id' + app.sfx).value && !batchTitles;
  targetEl.hidden = !isNew;
  if (!isNew) return;
  const list = editTags().map(t => t.toLowerCase()).filter(t => app.listSet.has(t)).sort()[0];
  const where = list ? `Adds to the list #${list}` : 'Adds to no list (tags only)';
  targetEl.textContent = where + (app.privateCb?.checked ? ', private' : '') + '.';
}
app.privateCb?.addEventListener('change', syncTarget);
editTagsInput?.addEventListener('input', syncTarget);
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
// never lost to Cancel. Hidden for a new task. Add comment shows the field
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
markdownEditor(commentInput, { tabIndents: false, page: () => app.privateCb?.checked ? app.appEl?.dataset.privateFolder + '/task' : '' });
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

// opts (new tasks): `tags` (comma-separated; given, even empty, it
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
  if (app.privateCb) {
    app.privateCb.checked = task ? !!task.private : opts && 'private' in opts ? !!opts.private : app.initPrivate;
    privateWas = app.privateCb.checked;
    app.privateCb.disabled = !!task && !task.private;   // shared stays shared
    app.privateNote.hidden = true;
  }
  const recType = task?.recurrence?.type || '';
  app.recTypeSelect.value = recType;
  document.getElementById('task-edit-rec-interval' + app.sfx).value = task?.recurrence?.interval || 1;
  const recFromCb = document.getElementById('task-edit-rec-from' + app.sfx);
  if (recFromCb) recFromCb.checked = task?.recurrence?.from === 'completion';
  syncRecFields();
  syncTarget();
  app.wontDoBtn.style.display = task && !task.closed_at ? '' : 'none';
  syncDates('open');
  app.modal.showModal();
  if (task && opts?.focusComment) openCommentBox();
  else (batchTitles ? editTagsInput : app.titleInput).focus();
}

// Every N / From done are only meaningful with a repeat type — disable them
// (rather than hide, which would shift the row) when Repeat is "—".
function syncRecFields() {
  // Sub-controls are only active when Repeat is both enabled and set.
  const on = !app.recTypeSelect.disabled && !!app.recTypeSelect.value;
  const subHint = on ? '' : (app.recTypeSelect.disabled ? app.recTypeSelect.closest('.task-field').title : 'Select a repeat type first');
  const intervalEl = document.getElementById('task-edit-rec-interval' + app.sfx);
  intervalEl.disabled = !on;
  intervalEl.closest('.task-field').title = subHint;
  const rf = document.getElementById('task-edit-rec-from' + app.sfx);
  if (rf) {
    rf.disabled = !on;
    rf.closest('.task-field').title = on ? 'Repeat from the completion date instead of the due (or start) date' : subHint;
  }
}
app.recTypeSelect.addEventListener('change', syncRecFields);

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
  app.recTypeSelect.closest('.task-field').title = app.keepCb.checked ? 'A keep task does not repeat'
    : noDate ? 'Set a due or start date first' : '';
  syncRecFields();
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

app.cancelBtn.addEventListener('click', () => { editTagAc?.hide(); app.modal.close(); });
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
// Alt+C: the comment field (Add comment is no stop for Tab).
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
    ...(app.privateCb ? { private: app.privateCb.checked } : {}),
  };
  if (batchTitles && !id) {
    const titles = batchTitles, created = onCreated;
    onCreated = null;
    app.modal.close();
    const today = todayStr(), made = [];
    for (const title of titles) {
      const q = parseQuickAdd(title, today);
      const task = await app.createTask({
        ...data, title: q.title,
        start_date: q.start_date || data.start_date, due_date: q.due_date || data.due_date,
        importance: q.importance || data.importance,
        tags: [...new Set([...data.tags, ...q.tags])],
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
    data.tags = [...new Set([...data.tags, ...quick.tags])];
    if (quick.importance) data.importance = quick.importance;
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
