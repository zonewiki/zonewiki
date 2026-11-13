import { closeOnBackdropClick } from './util.js';
import { changedParams, settingsForm } from './tasksettings.js';

// ── The editor's Task list button ────────────────────────────────────────────
// The formatting bar's Task list shows the list settings dialog, the one a
// list's ⋯ menu shows (js/tasksettings.js; in the editor with List and keep,
// which make a list), and writes the `[[tasks: …]]` line it makes into the
// text. With the cursor on a line that is one already it changes that one:
// its params fill the fields, and only what was changed is written
// (changedParams), so a param without a field stays as written. The menu's
// dialog saves the same change to the page's file; here it goes into the text.

const EMBED_LINE = /^(\s*)\[\[tasks:\s*([^\]]*)\]\](\s*)$/i;
const SFX = '-editor';

// `key=value` words in their order, as parseTasksParams (go/tasksyntax.go)
// reads them: a word without `=` counts for nothing, a key written twice
// keeps its place and takes the last value.
function orderedParams(raw) {
  const params = new Map();
  for (const word of raw.split(/\s+/)) {
    const eq = word.indexOf('=');
    if (eq > 0) params.set(word.slice(0, eq).toLowerCase(), word.slice(eq + 1));
  }
  return params;
}

// The line for the params with `set` applied, as the server rewrites an
// embed (setEmbedParams): a param keeps its place, a new one goes last, an
// empty one goes, and pin replaces the old home.
function embedLine(params, set) {
  const out = new Map(params);
  for (const [k, v] of Object.entries(set)) out.set(k, v);
  if ('pin' in set) out.delete('home');
  const words = [...out].filter(([, v]) => v !== '').map(([k, v]) => `${k}=${v}`);
  return words.length ? `[[tasks: ${words.join(' ')}]]` : '[[tasks:]]';
}

const el = id => document.getElementById(`task-settings-${id}${SFX}`);
const dialog = el('modal');
closeOnBackdropClick(dialog);

export function taskListDialog(textarea, { replaceRange, insertBlockAfterLine }) {
  if (!dialog) { insertBlockAfterLine('[[tasks: ]]', 9, 9); return; }
  // The cursor's line: a task list already?
  const v = textarea.value, selStart = textarea.selectionStart, selEnd = textarea.selectionEnd;
  const lineStart = v.lastIndexOf('\n', selStart - 1) + 1;
  let lineEnd = v.indexOf('\n', selStart);
  if (lineEnd === -1) lineEnd = v.length;
  const existing = EMBED_LINE.exec(v.slice(lineStart, lineEnd));
  const params = orderedParams(existing ? existing[2] : '');
  const written = Object.fromEntries(params);

  const form = el('form'), cancel = el('cancel');
  const { read, fill } = settingsForm(form);
  const was = fill(written);
  el('title').textContent = existing ? 'Change the task list' : 'Task list';
  el('save').textContent = existing ? 'Change' : 'Insert';
  el('error').hidden = true;
  const result = () => embedLine(params, changedParams(was, read(), written));
  // In place of where the list's own dialog says it writes: the line this writes.
  const preview = () => { el('where').textContent = result(); };
  preview();

  const done = line => {
    form.removeEventListener('input', preview);
    form.removeEventListener('submit', onSubmit);
    cancel.removeEventListener('click', onCancel);
    dialog.removeEventListener('close', onClose);
    if (dialog.open) dialog.close();
    // The dialog took the focus and the selection.
    textarea.focus();
    textarea.setSelectionRange(selStart, selEnd);
    if (line === null) return;
    if (existing) {
      const text = existing[1] + line + existing[3];
      replaceRange(lineStart, lineEnd, text, text.length, text.length);
    } else {
      insertBlockAfterLine(line, line.length, line.length);
    }
  };
  const onSubmit = e => { e.preventDefault(); done(result()); };
  const onCancel = () => done(null);
  const onClose = () => { if (!dialog.open) done(null); };   // Esc
  form.addEventListener('input', preview);
  form.addEventListener('submit', onSubmit);
  cancel.addEventListener('click', onCancel);
  dialog.addEventListener('close', onClose);
  dialog.showModal();
  form.elements.list.focus();
}
