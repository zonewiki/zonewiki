import DeckCore from '../deck-core.js';
import { openDeckEdit } from '../deck.js';
import { closeOnBackdropClick, continueList, localDate, pasteUrlAsLink, showToast, wikilinkHref } from './util.js';
import { linkAutocomplete } from './completion.js';

// ── Editor ────────────────────────────────────────────────────────────────────

export const textarea = document.getElementById('editor-textarea');
const previewPane = document.getElementById('editor-preview');
let previewTimer = null;
// The live preview follows the text ~300ms after the last change.
function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(fetchPreview, 300);
}
let applyFormat = () => {};   // set up with the editor's formatting bar

if (textarea && previewPane) {
  // Auto-show preview on wide screens
  if (window.innerWidth >= 1000) togglePreview();

  // Page and task completion; its keys (↑/↓, Enter, Tab) come first.
  const linkAc = linkAutocomplete(textarea, { tasks: true });

  textarea.addEventListener('keydown', e => {
    if (e.defaultPrevented) return;   // handled by the completion

    // Tab → try snippet expansion, then insert spaces
    if (e.key === 'Tab') {
      e.preventDefault();
      if (expandSnippet()) {
        schedulePreview();
        return;
      }
      document.execCommand('insertText', false, '  ');
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
        schedulePreview();
      }
      return;
    }

    // Enter → wikilink selection, follow wikilink under cursor, or continue list
    if (e.key === 'Enter' && !e.shiftKey) {
      const start = textarea.selectionStart;
      const end   = textarea.selectionEnd;
      if (start !== end) {
        // Selection → wrap as [[wikilink]]
        e.preventDefault();
        const selected = textarea.value.slice(start, end);
        document.execCommand('insertText', false, `[[${selected}]]`);
        const p = textarea.selectionStart - 2; // before ]]
        textarea.setSelectionRange(p, p);
        schedulePreview();
        return;
      }
      // Cursor inside [[...]] → follow the link
      const wlText = wikilinkAtCursor();
      if (wlText !== null) {
        e.preventDefault();
        let { href } = wikilinkHref(wlText);
        if (href.startsWith('#')) href = new URL(editorForm.action).pathname.replace(/^\/edit\//, '/page/') + href;
        formSubmitting = true;
        clearTimeout(autoSaveTimer);
        queueSave(() => fetch(editorForm.action,
                              { method: 'POST', body: new FormData(editorForm), redirect: 'follow' }))
          .finally(() => { window.location.href = href; });
        return;
      }
      if (continueList(textarea, e)) {
        schedulePreview();
      }
    }
  });

  textarea.addEventListener('input', schedulePreview);

  const uploadConflictModal = document.getElementById('upload-conflict-modal');
  const uploadConflictMsg   = document.getElementById('upload-conflict-msg');
  const uploadConflictName  = document.getElementById('upload-conflict-name');
  const uploadConflictOk    = document.getElementById('upload-conflict-confirm');
  const uploadConflictCancel = document.getElementById('upload-conflict-cancel');

  // Show conflict dialog; resolves with { name, overwrite } or null on cancel
  function askConflict(existingName) {
    return new Promise(resolve => {
      uploadConflictMsg.textContent = `"${existingName}" already exists. Enter a new name or keep it to overwrite.`;
      uploadConflictName.value = existingName;
      updateConflictBtn();
      uploadConflictModal.showModal();
      uploadConflictName.focus();
      uploadConflictName.select();

      function updateConflictBtn() {
        uploadConflictOk.textContent =
          uploadConflictName.value.trim() === existingName ? 'Overwrite' : 'Upload';
      }
      uploadConflictName.oninput = updateConflictBtn;

      function done(result) {
        uploadConflictModal.close();
        uploadConflictName.oninput = null;
        uploadConflictOk.onclick = null;
        uploadConflictCancel.onclick = null;
        resolve(result);
      }
      uploadConflictOk.onclick = () => {
        const n = uploadConflictName.value.trim() || existingName;
        done({ name: n, overwrite: n === existingName });
      };
      uploadConflictCancel.onclick = () => done(null);
    });
  }
  closeOnBackdropClick(uploadConflictModal);

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
    try {
      const res = await fetch('/api/upload', { method: 'POST', body: fd });
      if (res.status === 409) {
        const { filename } = await res.json();
        // Remove placeholder while dialog is open
        textarea.value = textarea.value.slice(0, start) + textarea.value.slice(start + placeholder.length);
        textarea.selectionStart = textarea.selectionEnd = start;
        fetchPreview();
        const choice = await askConflict(filename);
        if (choice) await uploadFileAtCursor(file, choice.name, choice.overwrite);
        return;
      }
      if (!res.ok) throw new Error();
      const { url } = await res.json();
      const md = isImage ? `![${name}](${url})` : `[${name}](${url})`;
      textarea.value = textarea.value.slice(0, start) + md + textarea.value.slice(start + placeholder.length);
      textarea.selectionStart = textarea.selectionEnd = start + md.length;
    } catch (_) {
      textarea.value = textarea.value.slice(0, start) + textarea.value.slice(start + placeholder.length);
    }
    fetchPreview();
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

  // ── Formatting bar ──────────────────────────────────────────────────────────
  // Buttons above the textarea (FORMAT_BAR in views.py) and Ctrl+B/I/K. Every
  // change goes through execCommand('insertText'), so Ctrl+Z undoes it in one
  // step and the input event drives preview and auto-save as typing does.

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

  // A two-column table on its own lines after the current one, header
  // selected so typing names the first column.
  function insertTable() {
    const v = textarea.value;
    let at = v.indexOf('\n', textarea.selectionEnd);
    if (at === -1) at = v.length;
    const lineStart = v.lastIndexOf('\n', at - 1) + 1;
    const blankLine = !v.slice(lineStart, at).trim();
    const pos = blankLine ? lineStart : at;
    const before = blankLine ? '' : (v[pos - 1] === '\n' ? '\n' : '\n\n');
    const table = '| Column 1 | Column 2 |\n| --- | --- |\n|  |  |';
    const end = blankLine ? at : pos;
    replaceRange(pos, end, before + table, before.length + 2, before.length + 10);
  }

  // Make task: the selected text, or the current line, becomes a deck task,
  // and the text is replaced by its [[task:…]] reference, so the page shows
  // the live task where the text was. It opens the deck's edit modal (from
  // the page's hidden deck app) to review first; several selected lines make
  // one task each (a list of action items), the modal's fields shared by
  // all, and keep their list markers. A checklist item's `[ ]` goes, the chip has its own
  // checkbox. Trailing quick-add shorthand works as in the deck (`tomorrow
  // #work due:fri`), and the task's notes link back to this page.
  const LIST_PREFIX_RE = /^(\s*(?:>\s?)*(?:[-*+]|\d+[.)])\s+)(?:\[[ xX]\]\s+)?/;
  const TASK_REF_ONLY_RE = /^\[\[task:[^\]]*\]\]$/;
  const pageSlug = (document.getElementById('editor-form')?.getAttribute('action') || '')
    .replace(/^\/edit\//, '');

  async function makeTasks() {
    const v = textarea.value, s = textarea.selectionStart, e = textarea.selectionEnd;
    const sel = v.slice(s, e);
    let start, end, items, inline = false;
    if (sel.trim() && !sel.includes('\n')) {
      inline = true;
      start = s + (sel.length - sel.trimStart().length);
      end = e - (sel.length - sel.trimEnd().length);
      items = [{ text: sel.trim() }];
    } else {
      const range = selectedLines();
      ({ start, end } = range);
      items = range.lines.map(line => {
        const m = line.match(LIST_PREFIX_RE);
        const prefix = m ? m[1] : line.match(/^\s*/)[0];
        return { line, prefix, text: line.slice(m ? m[0].length : prefix.length).trim() };
      });
    }
    // Blank lines, headings and existing references stay as they are.
    const todo = items.filter(it => it.text && !TASK_REF_ONLY_RE.test(it.text)
                                    && !(it.line && /^\s*#{1,6}\s/.test(it.line)));
    if (!todo.length) { showToast('Nothing to make a task from'); return; }
    const description = pageSlug ? `From [[${pageSlug}]]` : '';
    const ref = it => `[[task:${it.id.slice(0, 8)}]]`;
    const replaceWithRefs = created => {
      const text = inline ? ref(items[0])
        : items.map(it => (it.id ? it.prefix + ref(it) : it.line)).join('\n');
      const original = v.slice(start, end);
      replaceRange(start, end, text, text.length, text.length);
      const msg = created.length === 1 ? `Task “${todo[0].title}” created` : `${created.length} tasks created`;
      showToast(msg, async () => {
        await fetch('/api/deck/batch-delete', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids: created }),
        });
        if (textarea.value.slice(start, start + text.length) === text) {
          replaceRange(start, start + text.length, original, original.length, original.length);
        }
      });
    };

    if (document.querySelector('.tasks-app[data-ready]')) {
      // One item: the modal with its text as title. Several: the modal in
      // batch mode, its fields (tags, dates, …) for all of them.
      openDeckEdit(null, {
        ...(todo.length === 1 ? { title: todo[0].text } : { titles: todo.map(it => it.text) }),
        description, start_date: null,
        onCreated: made => {
          const tasks = Array.isArray(made) ? made : [made];
          if (tasks.some(t => !t) || textarea.value !== v) {
            const ids = tasks.filter(Boolean).map(t => t.id);
            if (ids.length) {
              fetch('/api/deck/batch-delete', {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids }),
              });
            }
            showToast(tasks.some(t => !t) ? 'Could not create the tasks'
                                                   : 'The text changed meanwhile; try again');
            return;
          }
          tasks.forEach((t, i) => { todo[i].id = t.id; todo[i].title = t.title; });
          replaceWithRefs(tasks.map(t => t.id));
        },
      });
      return;
    }

    // Without the deck app (it failed to load): create right away.
    const today = localDate();
    const created = [];
    for (const it of todo) {
      const q = DeckCore.parseQuickAdd(it.text, today);
      const body = { title: q.title, tags: q.tags, start_date: q.start_date, due_date: q.due_date, description };
      const res = await fetch('/api/deck', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      }).catch(() => null);
      if (!res?.ok) break;
      it.id = (await res.json()).id;
      it.title = q.title;
      created.push(it.id);
    }
    const deleteCreated = () => created.length && fetch('/api/deck/batch-delete', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids: created }),
    });
    // Typed on while the tasks were being created: the range is stale.
    if (created.length < todo.length || textarea.value !== v) {
      await deleteCreated();
      showToast(created.length < todo.length ? 'Could not create the task'
                                                     : 'The text changed meanwhile; try again');
      return;
    }

    replaceWithRefs(created);
  }

  const attachInput = document.getElementById('fmt-attach-input');
  attachInput?.addEventListener('change', async () => {
    const files = Array.from(attachInput.files);
    attachInput.value = '';
    textarea.focus();
    for (const file of files) await uploadFileAtCursor(file);
  });

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
    link: insertLink,
    wikilink: wikilinkFormat,
    taskref: () => {
      // `[[task:` opens the task autocomplete, which closes the reference.
      // Inserted after a selection, never over it.
      const at = textarea.selectionEnd;
      replaceRange(at, at, '[[task:', 7, 7);
    },
    maketask: makeTasks,
    attach: () => attachInput?.click(),
  };
  applyFormat = kind => FORMATS[kind]?.();

  const formatBar = document.getElementById('format-bar');
  // Pressing a button must not take focus (and the selection) from the
  // textarea; on a phone that would also close the keyboard.
  formatBar?.addEventListener('pointerdown', e => { if (e.target.closest('.fmt-btn')) e.preventDefault(); });
  formatBar?.addEventListener('mousedown', e => { if (e.target.closest('.fmt-btn')) e.preventDefault(); });
  formatBar?.addEventListener('click', e => {
    const btn = e.target.closest('.fmt-btn');
    if (btn) applyFormat(btn.dataset.fmt);
  });

  textarea.focus();
  if (textarea.dataset.cursor === 'end') {
    // A page with only its title (brand-new, or a new scratch note): land
    // right after the "# Title\n\n" so typing continues the body. Other pages
    // keep the cursor at the start instead of jumping (and scrolling) to the
    // end of a possibly long file.
    textarea.selectionStart = textarea.selectionEnd = textarea.value.length;
  }

  // Sync preview scroll position with editor
  const previewScroller = previewPane.parentElement;
  textarea.addEventListener('scroll', () => {
    const ratio = textarea.scrollTop / (textarea.scrollHeight - textarea.clientHeight || 1);
    previewScroller.scrollTop = ratio * (previewScroller.scrollHeight - previewScroller.clientHeight);
  });

  // Auto-save setup
  const editorForm = document.getElementById('editor-form');
  const autoSaveUrl = editorForm?.action.replace('/edit/', '/api/save/');
  const saveDot = document.getElementById('save-dot');
  const sectionHashEl = document.getElementById('section-file-hash');
  const sectionStartEl = document.getElementById('section-start-char');
  const sectionEndEl = document.getElementById('section-end-char');
  const isSectionEdit = !!sectionStartEl;
  let autoSaveTimer = null;
  let lastSavedContent = textarea.value;
  let formSubmitting = false;
  let sectionConflict = false;

  function appendSaveFields(data) {
    if (sectionHashEl?.value) data.append('file_hash', sectionHashEl.value);
    if (isSectionEdit) {
      data.append('start_char', sectionStartEl.value);
      data.append('end_char', sectionEndEl.value);
    }
  }

  // Saves run one at a time. Each sends the file hash the previous one
  // returned; two in flight at once would both send the older hash, and the
  // second would be refused as if someone else had changed the file.
  let saveQueue = Promise.resolve();
  function queueSave(fn) {
    const run = saveQueue.then(fn);
    saveQueue = run.catch(() => {});
    return run;
  }

  // Take a successful save's answer: the new hash and section range, and the
  // content it wrote (not what the textarea holds by now).
  async function saved(res, content) {
    lastSavedContent = content;
    if (textarea.value === content) saveDot.classList.remove('unsaved');
    if (res.headers.get('content-type')?.includes('json')) {
      const json = await res.json();
      if (sectionHashEl && json.file_hash) sectionHashEl.value = json.file_hash;
      if (isSectionEdit && json.start_char !== undefined) {
        sectionStartEl.value = json.start_char;
        sectionEndEl.value = json.end_char;
      }
    }
  }

  function autoSave() {
    return queueSave(async () => {
      if (!autoSaveUrl || sectionConflict) return;
      const content = textarea.value;
      if (content === lastSavedContent) return;
      const data = new FormData();
      data.append('content', content);
      appendSaveFields(data);
      try {
        const res = await fetch(autoSaveUrl, { method: 'POST', body: data });
        if (res.ok) {
          await saved(res, content);
        } else if (res.status === 409) {
          showConflictBanner();
        }
      } catch { /* ignore */ }
    });
  }

  function showConflictBanner(fromDone) {
    if (sectionConflict) return;
    sectionConflict = true;
    clearTimeout(autoSaveTimer);
    const banner = document.createElement('div');
    banner.className = 'conflict-banner';
    banner.innerHTML = '\u26a0 File was modified on disk while you were editing.'
      + '<span class="conflict-actions">'
      + '<button class="btn btn-small" id="conflict-reload">Reload from disk</button>'
      + '<button class="btn btn-small btn-primary" id="conflict-overwrite">Save my version</button>'
      + '</span>';
    editorForm.parentElement.insertBefore(banner, editorForm);
    document.getElementById('conflict-reload').addEventListener('click', () => {
      window.location.reload();
    });
    document.getElementById('conflict-overwrite').addEventListener('click', async () => {
      const sectionIdxEl = document.getElementById('section-idx');
      const data = new FormData();
      data.append('content', textarea.value);
      if (isSectionEdit && sectionIdxEl) {
        // Section edit: force-splice into current file by section index
        data.append('force_section', sectionIdxEl.value);
      }
      // Full-file edit: no file_hash, no section fields → plain overwrite
      try {
        const content = textarea.value;
        const res = await queueSave(() => fetch(autoSaveUrl, { method: 'POST', body: data }));
        if (res.ok) {
          banner.remove();
          sectionConflict = false;
          await saved(res, content);
          if (fromDone) {
            editorForm.submit();
          }
        }
      } catch { /* ignore */ }
    });
  }

  // Flag form submission so pagehide beacon doesn't race with the POST
  editorForm?.addEventListener('submit', (e) => {
    clearTimeout(autoSaveTimer);
    if (sectionHashEl?.value) {
      // Submit via fetch so we can handle conflicts in-place
      e.preventDefault();
      formSubmitting = true;
      // After any save still in flight, so the form carries its new hash
      queueSave(() => fetch(editorForm.action,
                            { method: 'POST', body: new FormData(editorForm), redirect: 'follow' }))
        .then(res => {
          if (res.redirected && res.url.includes('conflict=1')) {
            formSubmitting = false;
            showConflictBanner(true);
          } else {
            window.location.href = res.url;
          }
        })
        .catch(() => { editorForm.submit(); });
    } else {
      formSubmitting = true;
    }
  });

  textarea.addEventListener('input', () => {
    saveDot.classList.add('unsaved');
    clearTimeout(autoSaveTimer);
    autoSaveTimer = setTimeout(autoSave, 2000);
  });

  // Save file on navigation away if content has changed (no git commit)
  window.addEventListener('pagehide', () => {
    if (formSubmitting || sectionConflict) return;
    if (!autoSaveUrl || textarea.value === lastSavedContent) return;
    clearTimeout(autoSaveTimer);
    const content = textarea.value;
    const data = new FormData();
    data.append('content', content);
    appendSaveFields(data);
    // Not a beacon: if the page comes back from the back/forward cache, this
    // answer still arrives and updates the hash, so the next save isn't
    // refused for the change this one made. keepalive outlives the page.
    fetch(autoSaveUrl, { method: 'POST', body: data, keepalive: true })
      .then(res => { if (res.ok) return saved(res, content); if (res.status === 409) showConflictBanner(); })
      .catch(() => {});
  });
}

export function togglePreview() {
  if (!previewPane) return;
  const showing = previewPane.parentElement.classList.toggle('visible');
  if (showing) fetchPreview();
}

// Mobile: the toolbar's Preview button toggles the stacked preview pane (F2
// doesn't exist on phones). Hidden on wide screens via .mobile-only.
document.getElementById('mobile-preview-btn')?.addEventListener('click', togglePreview);

async function fetchPreview() {
  if (!previewPane?.parentElement.classList.contains('visible')) return;
  try {
    const res = await fetch('/api/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain' },
      body: textarea.value,
    });
    previewPane.innerHTML = await res.text();
    if (window.hljs) previewPane.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
    highlightPreviewBlock(true);
  } catch (_) { /* ignore */ }
}

let _lastHlLine = -1;

function highlightPreviewBlock(force) {
  if (!textarea || !previewPane) return;
  const pos = textarea.selectionStart;
  const text = textarea.value;
  const cursorLine = text.slice(0, pos).split('\n').length - 1;
  // selectionchange fires on every caret move — skip the (expensive) block
  // walk when the cursor is still on the same line and the preview is unchanged.
  if (!force && cursorLine === _lastHlLine) return;
  _lastHlLine = cursorLine;
  const lines = text.split('\n');

  // Walk lines up to cursor, counting rendered block elements.
  // Rules that mirror how markdown-it produces block elements:
  //  - blank line ends the current block
  //  - ATX headings (# …) always occupy their own block, regardless of surrounding blank lines
  //  - fenced code blocks (``` / ~~~) are one block regardless of their interior content
  let blockIdx = -1;
  let inBlock = false;
  let inFence = false;
  for (let i = 0; i <= cursorLine; i++) {
    const trimmed = lines[i].trim();

    // Fenced code block toggle
    if (!inFence && (trimmed.startsWith('```') || trimmed.startsWith('~~~'))) {
      inFence = true;
      if (!inBlock) { blockIdx++; inBlock = true; }
      continue;
    }
    if (inFence) {
      if (trimmed.startsWith('```') || trimmed.startsWith('~~~')) inFence = false;
      continue;
    }

    // Blank line → end current block
    if (trimmed === '') { inBlock = false; continue; }

    // ATX heading → always its own block; reset inBlock so the next
    // non-blank line (if not separated by a blank) also opens a new block
    if (/^#{1,6}\s/.test(trimmed)) {
      blockIdx++;
      inBlock = false;
      continue;
    }

    // Regular content line
    if (!inBlock) { blockIdx++; inBlock = true; }
  }

  const blockEls = previewPane.querySelectorAll(
    ':scope > p, :scope > h1, :scope > h2, :scope > h3, :scope > h4, :scope > h5, :scope > h6, ' +
    ':scope > pre, :scope > ul, :scope > ol, :scope > blockquote, :scope > hr, :scope > table'
  );
  blockEls.forEach(el => el.classList.remove('preview-hl'));
  if (blockIdx >= 0 && blockIdx < blockEls.length) {
    blockEls[blockIdx].classList.add('preview-hl');
  }
}

document.addEventListener('selectionchange', () => {
  if (document.activeElement === textarea) highlightPreviewBlock();
});

// The inside of the [[wikilink]] around the cursor, or null.
function wikilinkAtCursor() {
  const pos    = textarea.selectionStart;
  const val    = textarea.value;
  const before = val.slice(0, pos);
  const after  = val.slice(pos);
  const openIdx = before.lastIndexOf('[[');
  if (openIdx === -1) return null;
  if (before.slice(openIdx + 2).includes(']]')) return null; // already closed before cursor
  const closeIdx = after.indexOf(']]');
  if (closeIdx === -1) return null;
  return before.slice(openIdx + 2) + after.slice(0, closeIdx);
}

// ── Snippets ──────────────────────────────────────────────────────────────────

// Local date and time, as the user's clock shows them.
const snippets = {
  '/date':     () => localDate(),
  '/time':     () => new Date().toTimeString().slice(0, 5),
  '/datetime': () => `${localDate()} ${new Date().toTimeString().slice(0, 5)}`,
};

function expandSnippet() {
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

// ── Delete confirmation ───────────────────────────────────────────────────────

function getPrevPageUrl() {
  const KEY = 'leafHistory';
  const hist = JSON.parse(sessionStorage.getItem(KEY) || '[]');
  const currentSlug = document.querySelector('.breadcrumb[data-slug]')?.dataset.slug;
  for (let i = hist.length - 1; i >= 0; i--) {
    if (hist[i].slug !== currentSlug) return hist[i].url || '/page/' + hist[i].slug;
  }
  return '/page/index';
}

function injectNextField(form) {
  const input = document.createElement('input');
  input.type = 'hidden';
  input.name = 'next';
  input.value = getPrevPageUrl();
  form.appendChild(input);
}

document.getElementById('delete-btn')?.addEventListener('click', e => {
  const btn = e.currentTarget;
  const title = btn.dataset.title;
  const msg = btn.dataset.git
    ? `Delete "${title}"? It can be restored from history.`
    : `Delete "${title}"? This cannot be undone.`;
  if (!confirm(msg)) { e.preventDefault(); return; }
  const form = document.getElementById('delete-form');
  if (form) injectNextField(form);
});

document.getElementById('page-delete-dialog')?.querySelector('form')?.addEventListener('submit', function () {
  injectNextField(this);
});

// ── Rename bar toggle ─────────────────────────────────────────────────────────

const renameToggle = document.getElementById('rename-toggle');
const renameBar = document.getElementById('rename-bar');
const renameCancel = document.getElementById('rename-cancel');
const renameInput = document.getElementById('rename-input');

if (renameToggle && renameBar) {
  renameToggle.addEventListener('click', () => {
    renameBar.classList.remove('hidden');
    renameInput.focus();
    renameInput.select();
  });
  renameCancel.addEventListener('click', () => {
    renameBar.classList.add('hidden');
  });
}

// ── Editor ⋯ menu (phones) ───────────────────────────────────────────────────
// Holds Rename…/Delete on small screens; any click closes it again (after the
// item's own handler has run).

const editorMoreBtn = document.getElementById('editor-more-btn');
const editorMore = document.getElementById('editor-more');
if (editorMoreBtn && editorMore) {
  const setOpen = open => {
    editorMore.classList.toggle('open', open);
    editorMoreBtn.setAttribute('aria-expanded', String(open));
  };
  editorMoreBtn.addEventListener('click', e => {
    e.stopPropagation();
    setOpen(!editorMore.classList.contains('open'));
  });
  document.addEventListener('click', () => setOpen(false));
  document.addEventListener('keydown', e => { if (e.key === 'Escape') setOpen(false); });
}
