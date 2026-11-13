import { closeOnBackdropClick } from './util.js';

// ── Task list dialog ──────────────────────────────────────────────────────────
// The editor's Task list button (formatting bar): a small dialog for what a
// task list shows and how it opens, which writes the `[[tasks: …]]` line. With
// the cursor on a line that is one already it changes that one: its params
// fill the fields, and only the fields changed are written (a param the
// dialog doesn't know, `labels=`, `new=`, `plan=` …, stays as written).
// The params are the embed's own (docs/spec/tasks.md, Task list embed).

const EMBED_LINE = /^(\s*)\[\[tasks:\s*([^\]]*)\]\](\s*)$/i;

// `key=value` words, as parseTasksParams (go/tasksyntax.go) reads them: a
// word without `=` counts for nothing, a key written twice keeps its place
// and takes the last value.
export function parseEmbedParams(raw) {
  const params = new Map();
  for (const word of raw.split(/\s+/)) {
    const eq = word.indexOf('=');
    if (eq > 0) params.set(word.slice(0, eq).toLowerCase(), word.slice(eq + 1));
  }
  return params;
}

// cleanList (go/tasksyntax.go): a list's name is one lowercase word.
const cleanList = name => name.trim().replace(/^#+/, '').trim().toLowerCase().replace(/\s+/g, '-').replace(/^-+|-+$/g, '');
// A value is one word, and can't hold what would end the embed.
const cleanValue = v => v.replace(/[\s\[\]`]+/g, '');

// The line for `params`: a param keeps its place, a new one goes last, an
// empty one goes.
export function embedLine(params) {
  const words = [...params].filter(([, v]) => v !== '').map(([k, v]) => `${k}=${v}`);
  return words.length ? `[[tasks: ${words.join(' ')}]]` : '[[tasks:]]';
}

const dialog = document.getElementById('tasklist-dialog');
const el = id => document.getElementById('tasklist-' + id);
closeOnBackdropClick(dialog);

// What the fields say, as params: { key: value }, '' for none.
function fieldValues() {
  const limit = parseInt(el('limit').value, 10);
  return {
    list: cleanList(el('list').value),
    tag: cleanValue(el('tag').value),
    view: el('view').value,
    limit: limit >= 1 && limit <= 99 ? String(limit) : '',
    keep: el('keep').checked ? '1' : '',
    pin: el('pin').checked ? '1' : '',
  };
}

export function taskListDialog(textarea, { replaceRange, insertBlockAfterLine }) {
  if (!dialog) { insertBlockAfterLine('[[tasks: ]]', 9, 9); return; }
  // The cursor's line: a task list already?
  const v = textarea.value, selStart = textarea.selectionStart, selEnd = textarea.selectionEnd;
  const lineStart = v.lastIndexOf('\n', selStart - 1) + 1;
  let lineEnd = v.indexOf('\n', selStart);
  if (lineEnd === -1) lineEnd = v.length;
  const existing = EMBED_LINE.exec(v.slice(lineStart, lineEnd));
  const params = parseEmbedParams(existing ? existing[2] : '');

  // The fields as the line has them. A view or a truthy value the dialog
  // has no choice for (view=completed, pin=default) shows as its nearest
  // and stays as written unless that field is changed.
  const truthy = k => /^(1|true|yes|default)$/i.test(params.get(k) || '');
  const view = (params.get('view') || '').toLowerCase();
  el('list').value = params.get('list') || '';
  el('tag').value = params.get('tag') || '';
  el('view').value = view === 'plan' || view === 'open' ? view : '';
  el('limit').value = params.get('limit') || '';
  el('keep').checked = truthy('keep');
  el('pin').checked = truthy('pin');
  el('label').textContent = existing ? 'Change the task list' : 'Task list';
  el('ok').textContent = existing ? 'Change' : 'Insert';
  const shown = fieldValues();

  const result = () => {
    const now = fieldValues(), out = new Map(params);
    for (const k of Object.keys(now)) if (now[k] !== shown[k]) out.set(k, now[k]);
    return embedLine(out);
  };
  const preview = () => { el('preview').textContent = result(); };
  preview();

  const form = el('form'), cancel = el('cancel');
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
  el('list').focus();
}
