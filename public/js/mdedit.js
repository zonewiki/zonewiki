import { closeOnBackdropClick, continueList, localDate, pasteUrlAsLink } from './util.js';
import { linkAutocomplete } from './completion.js';
import { taskCheckboxOffsets } from './mdtext.js';

// ── Markdown textarea ─────────────────────────────────────────────────────────
// What makes a textarea Zone's markdown editor, shared by the page editor
// (js/editor.js) and the task app's task notes (tasks.js): page and task
// completion, the formatting bar and its shortcuts, snippets, list
// continuation, file paste/drop/attach, URL paste. Every change goes through
// execCommand('insertText'), so Ctrl+Z undoes it in one step and the input
// event follows as for typing; uploads, which set the value, call onChange.
//
// opts: bar (the formatting bar, partials/format_bar.html), attachInput (its
// file input), onChange, onEnter(e) (runs before list continuation; may
// preventDefault), formats (more bar buttons by data-fmt, e.g. the editor's
// Make task), tabIndents (Tab inserts two spaces after a failed snippet; the
// tasks' modal leaves Tab to move between its fields), page (the slug of
// the page being edited, or a function giving it: an upload for a private
// page is private too).

export function markdownEditor(textarea, { bar = null, attachInput = null, onChange = () => {},
                                          onEnter = null, formats = {}, tabIndents = true,
                                          page = '' } = {}) {
  // Page and task completion; its keys (↑/↓, Enter, Tab) come first.
  const linkAc = linkAutocomplete(textarea, { tasks: true, page });

  textarea.addEventListener('keydown', e => {
    if (e.defaultPrevented || e.isComposing) return;   // handled by the completion

    // Tab → try snippet expansion, then insert spaces
    if (e.key === 'Tab' && (tabIndents || !(e.shiftKey || e.ctrlKey || e.altKey || e.metaKey))) {
      if (expandSnippet(textarea)) {
        e.preventDefault();
        onChange();
        return;
      }
      if (tabIndents) {
        e.preventDefault();
        document.execCommand('insertText', false, '  ');
      }
      return;
    }

    // Ctrl/Cmd+B / I / K → bold / italic / link (same as the formatting bar)
    const fmtKey = { b: 'bold', i: 'italic', k: 'link' }[e.key.toLowerCase()];
    if (fmtKey && (e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey) {
      e.preventDefault();
      applyFormat(fmtKey);
      return;
    }

    // Ctrl+Shift+X → toggle checkbox on current line
    if (e.code === 'KeyX' && e.ctrlKey && e.shiftKey) {
      e.preventDefault();
      const pos = textarea.selectionStart;
      const val = textarea.value;
      const lineStart = val.lastIndexOf('\n', pos - 1) + 1;
      const lineEnd = val.indexOf('\n', pos);
      const line = val.slice(lineStart, lineEnd === -1 ? undefined : lineEnd);
      const toggled = line.replace(/^(\s*(?:[-*+]|\d+\.)\s+)\[([xX ])\]/, (_, pre, mark) => pre + (mark === ' ' ? '[x]' : '[ ]'));
      if (toggled !== line) {
        textarea.setSelectionRange(lineStart, lineEnd === -1 ? val.length : lineEnd);
        document.execCommand('insertText', false, toggled);
        textarea.setSelectionRange(pos, pos);
      }
      return;
    }

    // Enter → wikilink selection, the caller's own (the editor follows a
    // wikilink under the cursor), or continue a list
    if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const start = textarea.selectionStart;
      const end   = textarea.selectionEnd;
      if (start !== end) {
        // Selection → wrap as [[wikilink]]
        e.preventDefault();
        const selected = textarea.value.slice(start, end);
        document.execCommand('insertText', false, `[[${selected}]]`);
        const p = textarea.selectionStart - 2; // before ]]
        textarea.setSelectionRange(p, p);
        return;
      }
      onEnter?.(e);
      if (e.defaultPrevented) return;
      continueList(textarea, e);
    }
  });

  // ── Uploads: paste, drop, attach ──────────────────────────────────────────

  async function uploadFileAtCursor(file, filenameOverride, overwrite, unique) {
    const start = textarea.selectionStart, end = textarea.selectionEnd;
    const isImage = file.type.startsWith('image/');
    const name = filenameOverride || file.name || 'file';
    const placeholder = isImage ? '![uploading…]()' : `[${name}]()`;
    textarea.value = textarea.value.slice(0, start) + placeholder + textarea.value.slice(end);
    textarea.selectionStart = textarea.selectionEnd = start + placeholder.length;
    const fd = new FormData();
    fd.append('file', file, file.name || 'upload');
    if (unique) fd.append('unique', '1');
    if (filenameOverride) fd.append('filename', filenameOverride);
    if (overwrite) fd.append('overwrite', '1');
    // The page the file goes in: a private page's uploads are private too.
    const into = typeof page === 'function' ? page() : page;
    if (into) fd.append('page', into);
    try {
      const res = await fetch('/api/upload', { method: 'POST', body: fd });
      if (res.status === 409) {
        const { filename } = await res.json();
        // Remove placeholder while dialog is open
        textarea.value = textarea.value.slice(0, start) + textarea.value.slice(start + placeholder.length);
        textarea.selectionStart = textarea.selectionEnd = start;
        onChange();
        const choice = await askConflict(filename);
        if (choice) await uploadFileAtCursor(file, choice.name, choice.overwrite);
        return;
      }
      // Too large (413) or the wiki full (507): the server says why.
      if (res.status === 413 || res.status === 507) alert(await res.text());
      if (!res.ok) throw new Error();
      const { url } = await res.json();
      const md = isImage ? `![${name}](${url})` : `[${name}](${url})`;
      textarea.value = textarea.value.slice(0, start) + md + textarea.value.slice(start + placeholder.length);
      textarea.selectionStart = textarea.selectionEnd = start + md.length;
    } catch (_) {
      textarea.value = textarea.value.slice(0, start) + textarea.value.slice(start + placeholder.length);
    }
    onChange();
  }

  textarea.addEventListener('paste', async (e) => {
    const fileItem = Array.from(e.clipboardData.items).find(i => i.kind === 'file');
    if (fileItem) {
      e.preventDefault();
      const file = fileItem.getAsFile();
      // Clipboard images (screenshots) get a unique UUID — no conflict dialog
      const unique = file.type.startsWith('image/');
      await uploadFileAtCursor(file, null, false, unique);
      return;
    }
    await pasteUrlAsLink(textarea, e);
  });

  textarea.addEventListener('dragover', (e) => { e.preventDefault(); });
  textarea.addEventListener('drop', async (e) => {
    const files = Array.from(e.dataTransfer.files);
    if (!files.length) return;
    e.preventDefault();
    textarea.focus();
    for (const file of files) {
      await uploadFileAtCursor(file);
    }
  });

  attachInput?.addEventListener('change', async () => {
    const files = Array.from(attachInput.files);
    attachInput.value = '';
    textarea.focus();
    for (const file of files) await uploadFileAtCursor(file);
  });

  // ── Formatting bar ──────────────────────────────────────────────────────────
  // Buttons above the textarea (partials/format_bar.html) and Ctrl+B/I/K.

  function replaceRange(start, end, text, selStart, selEnd) {
    textarea.focus();
    textarea.setSelectionRange(start, end);
    if (text) document.execCommand('insertText', false, text);
    else if (start !== end) document.execCommand('delete');
    textarea.setSelectionRange(start + selStart, start + selEnd);
    linkAc.update();   // e.g. `[[` or `[text](` just inserted: offer pages
  }

  // Wrap the selection in `mark`, or unwrap it when it is wrapped already
  // (markers inside or just outside the selection). Surrounding spaces stay
  // outside: `**word **` would not render bold.
  function toggleWrap(mark) {
    const v = textarea.value, n = mark.length;
    let s = textarea.selectionStart, e = textarea.selectionEnd;
    if (s >= n && v.slice(s - n, s) === mark && v.slice(e, e + n) === mark) {
      // For single-char marks (like `), the mark just before the selection might
      // be a closing mark from a prior span rather than an opening mark for this
      // selection. Count the marks before it: even = opening (inside a span →
      // unwrap), odd = closing (between two spans → don't remove their backticks).
      const shouldUnwrap = n > 1 ||
        (v.slice(0, s - n).split(mark).length - 1) % 2 === 0;
      if (shouldUnwrap) {
        replaceRange(s - n, e + n, v.slice(s, e), 0, e - s);
        return;
      }
    }
    const sel = v.slice(s, e);
    if (sel.length >= 2 * n && sel.startsWith(mark) && sel.endsWith(mark)) {
      const inner = sel.slice(n, -n);
      replaceRange(s, e, inner, 0, inner.length);
      return;
    }
    const lead = sel.length - sel.trimStart().length;
    const trail = sel.length - sel.trimEnd().length;
    if (lead < sel.length) { s += lead; e -= trail; }
    const text = v.slice(s, e);
    replaceRange(s, e, mark + text + mark, n, n + text.length);
  }

  // The full lines the selection touches (a selection ending right at the
  // start of a line doesn't include that line).
  function selectedLines() {
    const v = textarea.value, s = textarea.selectionStart;
    let e = textarea.selectionEnd;
    if (e > s && v[e - 1] === '\n') e--;
    const start = v.lastIndexOf('\n', s - 1) + 1;
    let end = v.indexOf('\n', e);
    if (end === -1) end = v.length;
    return { start, end, lines: v.slice(start, end).split('\n') };
  }

  // Put the new lines in place: one line keeps the cursor at its end (where
  // you continue typing), several stay selected so a second click undoes.
  function replaceLines({ start, end }, lines) {
    const text = lines.join('\n');
    if (lines.length === 1) replaceRange(start, end, text, text.length, text.length);
    else replaceRange(start, end, text, 0, text.length);
  }

  const LIST_KINDS = {
    task: /^(\s*)[-*+] \[[ xX]\] /,
    ul:   /^(\s*)[-*+] (?!\[[ xX]\] )/,
    ol:   /^(\s*)\d+[.)] /,
  };

  // Make the lines a list of `kind`, replacing another list marker; when they
  // all are one already, remove the markers. Blank lines in a selection stay.
  function toggleList(kind) {
    const range = selectedLines();
    const items = range.lines.filter(l => l.trim() || range.lines.length === 1);
    const remove = items.every(l => LIST_KINDS[kind].test(l));
    let num = 0;
    const lines = range.lines.map(l => {
      if (!l.trim() && range.lines.length > 1) return l;
      let indent = l.match(/^\s*/)[0], rest = l.slice(indent.length);
      for (const re of Object.values(LIST_KINDS)) {
        const m = l.match(re);
        if (m) { indent = m[1]; rest = l.slice(m[0].length); break; }
      }
      if (remove) return indent + rest;
      const marker = kind === 'task' ? '- [ ] ' : kind === 'ol' ? `${++num}. ` : '- ';
      return indent + marker + rest;
    });
    replaceLines(range, lines);
  }

  function toggleQuote() {
    const range = selectedLines();
    const remove = range.lines.every(l => l.startsWith('>'));
    replaceLines(range, range.lines.map(l =>
      remove ? l.replace(/^> ?/, '') : (l ? '> ' + l : '>')));
  }

  // Cycle the line through ## → ### → plain. The page title is the H1, so
  // the bar starts at ##; any other level goes to ##.
  function cycleHeading() {
    const v = textarea.value;
    const start = v.lastIndexOf('\n', textarea.selectionStart - 1) + 1;
    let end = v.indexOf('\n', start);
    if (end === -1) end = v.length;
    const line = v.slice(start, end);
    const m = line.match(/^(#{1,6})\s+/);
    const rest = m ? line.slice(m[0].length) : line;
    const prefix = !m ? '## ' : m[1] === '##' ? '### ' : m[1] === '###' ? '' : '## ';
    replaceLines({ start, end }, [prefix + rest]);
  }

  // `[text](url)`: a selected URL becomes the target (type the text), other
  // selected text the label (type or pick a page for the target).
  function insertLink() {
    const v = textarea.value, s = textarea.selectionStart, e = textarea.selectionEnd;
    const sel = v.slice(s, e);
    if (/^(https?:\/\/|www\.)\S+$/.test(sel.trim())) replaceRange(s, e, `[](${sel.trim()})`, 1, 1);
    else if (sel) replaceRange(s, e, `[${sel}]()`, sel.length + 3, sel.length + 3);
    else replaceRange(s, e, '[]()', 1, 1);
  }

  function wikilinkFormat() {
    const s = textarea.selectionStart, e = textarea.selectionEnd;
    const sel = textarea.value.slice(s, e);
    // Bare `[[` opens the page autocomplete, which closes the link itself.
    if (sel) replaceRange(s, e, `[[${sel}]]`, sel.length + 4, sel.length + 4);
    else replaceRange(s, e, '[[', 2, 2);
  }

  // A fenced block around the selected lines (empty: a block to type into).
  function insertCodeBlock() {
    const range = selectedLines();
    const body = range.lines.join('\n');
    if (!body.trim()) {
      replaceRange(range.start, range.end, '```\n\n```', 4, 4);
      return;
    }
    replaceRange(range.start, range.end, '```\n' + body + '\n```', 4, 4 + body.length);
  }

  // A block on its own lines after the current one (on the current line if
  // that is blank), selecting [selStart, selEnd) of the block.
  function insertBlockAfterLine(block, selStart, selEnd) {
    const v = textarea.value;
    let at = v.indexOf('\n', textarea.selectionEnd);
    if (at === -1) at = v.length;
    const lineStart = v.lastIndexOf('\n', at - 1) + 1;
    const blankLine = !v.slice(lineStart, at).trim();
    const pos = blankLine ? lineStart : at;
    const before = blankLine ? '' : (v[pos - 1] === '\n' ? '\n' : '\n\n');
    const end = blankLine ? at : pos;
    replaceRange(pos, end, before + block, before.length + selStart, before.length + selEnd);
  }

  // A two-column table, header selected so typing names the first column.
  function insertTable() {
    insertBlockAfterLine('| Column 1 | Column 2 |\n| --- | --- |\n|  |  |', 2, 10);
  }

  // Math: a selection on one line (or the cursor in text) as inline `$…$`,
  // toggling; selected lines, or the cursor on a blank line, as a `$$` block.
  function insertMath() {
    const v = textarea.value, s = textarea.selectionStart, e = textarea.selectionEnd;
    const range = selectedLines();
    const sel = v.slice(s, e);
    const multiLine = sel.trim().includes('\n');
    const blankLine = s === e && !range.lines[0].trim();
    if (!multiLine && !blankLine) {
      toggleWrap('$');
      return;
    }
    const body = blankLine ? '' : range.lines.join('\n');
    replaceRange(range.start, range.end, '$$\n' + body + '\n$$', 3, 3 + body.length);
  }

  // A Mermaid diagram: the selected lines fenced as one, or a small
  // flowchart to start from, its edge selected so typing replaces it.
  function insertDiagram() {
    const fence = '```mermaid\n';
    if (textarea.selectionEnd > textarea.selectionStart) {
      const range = selectedLines();
      const body = range.lines.join('\n');
      if (body.trim()) {
        replaceRange(range.start, range.end, fence + body + '\n```',
          fence.length, fence.length + body.length);
        return;
      }
    }
    const head = fence + 'flowchart LR\n    ', edge = 'A[Start] --> B[End]';
    insertBlockAfterLine(head + edge + '\n```', head.length, head.length + edge.length);
  }

  // A task list: `[[tasks: ]]` on its own line, the cursor where its
  // params go (list=, tag=, view= …; none embeds all tasks).
  function insertTaskList() {
    insertBlockAfterLine('[[tasks: ]]', 9, 9);
  }

  const FORMATS = {
    bold: () => toggleWrap('**'),
    italic: () => toggleWrap('_'),
    strike: () => toggleWrap('~~'),
    code: () => toggleWrap('`'),
    heading: cycleHeading,
    ul: () => toggleList('ul'),
    ol: () => toggleList('ol'),
    task: () => toggleList('task'),
    quote: toggleQuote,
    codeblock: insertCodeBlock,
    table: insertTable,
    math: insertMath,
    diagram: insertDiagram,
    link: insertLink,
    wikilink: wikilinkFormat,
    taskref: () => {
      // `[[task:` opens the task autocomplete, which closes the reference.
      // Inserted after a selection, never over it.
      const at = textarea.selectionEnd;
      replaceRange(at, at, '[[task:', 7, 7);
    },
    tasklist: insertTaskList,
    attach: () => attachInput?.click(),
    ...formats,
  };
  const applyFormat = kind => FORMATS[kind]?.();

  // Pressing a button must not take focus (and the selection) from the
  // textarea; on a phone that would also close the keyboard.
  bar?.addEventListener('pointerdown', e => { if (e.target.closest('.fmt-btn')) e.preventDefault(); });
  bar?.addEventListener('mousedown', e => { if (e.target.closest('.fmt-btn')) e.preventDefault(); });
  bar?.addEventListener('click', e => {
    const btn = e.target.closest('.fmt-btn');
    if (btn) applyFormat(btn.dataset.fmt);
  });

  return { applyFormat, replaceRange, selectedLines, linkAc };
}

// ── Upload conflict ───────────────────────────────────────────────────────────

// The dialog (layout.html) when an uploaded file's name exists already;
// resolves with { name, overwrite } or null on cancel.
function askConflict(existingName) {
  const modal = document.getElementById('upload-conflict-modal');
  const msg = document.getElementById('upload-conflict-msg');
  const nameInput = document.getElementById('upload-conflict-name');
  const ok = document.getElementById('upload-conflict-confirm');
  const cancel = document.getElementById('upload-conflict-cancel');
  if (!modal) return Promise.resolve(null);
  if (!modal.dataset.wired) {
    modal.dataset.wired = '1';
    closeOnBackdropClick(modal);
  }
  return new Promise(resolve => {
    msg.textContent = `"${existingName}" already exists. Enter a new name or keep it to overwrite.`;
    nameInput.value = existingName;
    const updateBtn = () => {
      ok.textContent = nameInput.value.trim() === existingName ? 'Overwrite' : 'Upload';
    };
    updateBtn();
    nameInput.oninput = updateBtn;
    modal.showModal();
    nameInput.focus();
    nameInput.select();

    function done(result) {
      modal.close();
      nameInput.oninput = null;
      ok.onclick = null;
      cancel.onclick = null;
      resolve(result);
    }
    ok.onclick = () => {
      const n = nameInput.value.trim() || existingName;
      done({ name: n, overwrite: n === existingName });
    };
    cancel.onclick = () => done(null);
  });
}

// ── Snippets ──────────────────────────────────────────────────────────────────

// Local date and time, as the user's clock shows them.
const snippets = {
  '/date':     () => localDate(),
  '/time':     () => new Date().toTimeString().slice(0, 5),
  '/datetime': () => `${localDate()} ${new Date().toTimeString().slice(0, 5)}`,
};

function expandSnippet(textarea) {
  const pos = textarea.selectionStart;
  if (pos !== textarea.selectionEnd) return false;
  const before = textarea.value.slice(0, pos);
  for (const [trigger, expand] of Object.entries(snippets)) {
    if (before.endsWith(trigger)) {
      textarea.setSelectionRange(pos - trigger.length, pos);
      document.execCommand('insertText', false, expand());
      return true;
    }
  }
  return false;
}

// A click on the Nth checkbox of a preview ticks the Nth item in the
// textarea. Through execCommand, so it lands on the undo stack and fires
// `input` (preview, unsaved dot, auto-save) like any other edit.
export function toggleCheckboxInTextarea(textarea, idx, frontmatter = true) {
  const offsets = taskCheckboxOffsets(textarea.value, frontmatter);
  if (idx >= offsets.length) return;
  const at = offsets[idx];
  const newChar = textarea.value[at] === ' ' ? 'x' : ' ';
  const { selectionStart, selectionEnd, scrollTop } = textarea;
  textarea.focus({ preventScroll: true });
  textarea.setSelectionRange(at, at + 1);
  document.execCommand('insertText', false, newChar);
  textarea.setSelectionRange(selectionStart, selectionEnd);
  textarea.scrollTop = scrollTop;
}
