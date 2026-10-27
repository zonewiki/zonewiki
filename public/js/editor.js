import DeckCore from '../deck-core.js';
import { openDeckEdit } from '../deck.js';
import { localDate, localizeDates, showToast, wikilinkHref } from './util.js';
import { markdownEditor } from './mdedit.js';

// ── Editor ────────────────────────────────────────────────────────────────────

export const textarea = document.getElementById('editor-textarea');
const previewPane = document.getElementById('editor-preview');
let previewTimer = null;
// The live preview follows the text ~300ms after the last change.
function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(fetchPreview, 300);
}

if (textarea && previewPane) {
  // Auto-show preview on wide screens
  if (window.innerWidth >= 1000) togglePreview();

  const pageSlug = decodeURIComponent((document.getElementById('editor-form')?.getAttribute('action') || '')
    .replace(/^\/edit\//, ''));
  // The markdown helpers (js/mdedit.js), shared with the deck's task notes;
  // Enter on a [[wikilink]] follows it (after saving).
  const { replaceRange, selectedLines } = markdownEditor(textarea, {
    bar: document.getElementById('format-bar'),
    attachInput: document.getElementById('fmt-attach-input'),
    onChange: schedulePreview,
    formats: { maketask: makeTasks },
    page: pageSlug,
    onEnter: e => {
      const wlText = wikilinkAtCursor();
      if (wlText === null) return;
      e.preventDefault();
      let { href } = wikilinkHref(wlText);
      if (href.startsWith('#')) href = new URL(editorForm.action).pathname.replace(/^\/edit\//, '/page/') + href;
      formSubmitting = true;
      clearTimeout(autoSaveTimer);
      queueSave(() => fetch(editorForm.action,
                            { method: 'POST', body: new FormData(editorForm), redirect: 'follow' }))
        .finally(() => { window.location.href = href; });
    },
  });

  textarea.addEventListener('input', schedulePreview);

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
    localizeDates(previewPane);
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
