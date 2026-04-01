// ── Navigation trail breadcrumb ───────────────────────────────────────────────

(function () {
  const bc = document.querySelector('.breadcrumb[data-slug]');
  if (!bc) return;
  const slug  = bc.dataset.slug;
  const title = bc.dataset.title;
  if (!slug) return;

  // MRU list: visiting a page already in the list moves it to the end
  const KEY = 'leafHistory';
  let hist = JSON.parse(sessionStorage.getItem(KEY) || '[]');
  hist = hist.filter(p => p.slug !== slug);
  hist.push({ slug, title });
  if (hist.length > 6) hist = hist.slice(-6);
  sessionStorage.setItem(KEY, JSON.stringify(hist));

  // All entries before the current page (last) become breadcrumb links
  const intermediates = hist.slice(0, -1).filter(p => p.slug !== 'index' && p.slug !== '_scratch');
  if (!intermediates.length) return;

  const homeEl = bc.querySelector('.breadcrumb-home');
  if (!homeEl) return;

  const mkSep = () => {
    const s = document.createElement('span');
    s.className = 'breadcrumb-sep';
    s.textContent = '›';
    return s;
  };

  bc.innerHTML = '';
  bc.appendChild(homeEl);
  for (const p of intermediates) {
    bc.appendChild(mkSep());
    const a = document.createElement('a');
    a.href = `/page/${p.slug}`;
    a.textContent = p.title;
    bc.appendChild(a);
  }
  bc.appendChild(mkSep());
  const cur = document.createElement('span');
  cur.textContent = title;
  bc.appendChild(cur);

  const clr = document.createElement('button');
  clr.className = 'breadcrumb-clear';
  clr.title = 'Clear navigation history';
  clr.textContent = '×';
  clr.addEventListener('click', () => {
    sessionStorage.removeItem(KEY);
    bc.innerHTML = '';
    bc.appendChild(homeEl);
    bc.appendChild(mkSep());
    const s = document.createElement('span');
    s.textContent = title;
    bc.appendChild(s);
    clr.remove();
  });
  bc.appendChild(clr);
}());

// ── Search modal ──────────────────────────────────────────────────────────────

const searchModal    = document.getElementById('search-modal');
const modalFilter    = document.getElementById('modal-filter');
const modalPageList  = document.getElementById('modal-page-list');
const modalSort      = document.getElementById('modal-sort');
const modalUnlinked  = document.getElementById('modal-unlinked');
const modalCM        = document.getElementById('modal-content-matches');

let modalPages = null;
let modalActiveIdx = -1;
let modalCMTimer = null;

document.getElementById('open-search')?.addEventListener('click', openSearchModal);

// Close when clicking the backdrop (backdrop clicks have the dialog as target)
searchModal?.addEventListener('click', e => {
  if (e.target === searchModal) searchModal.close();
});

function openSearchModal() {
  if (!searchModal) return;
  searchModal.showModal();
  modalFilter.focus();
  modalFilter.select();
  if (!modalPages) loadModalPages();
}

async function loadModalPages() {
  try {
    const res = await fetch('/api/pages-list');
    modalPages = await res.json();
    renderModalList(modalPages);
    applyModalControls();
  } catch (_) {}
}

function he(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function renderModalList(pages) {
  modalPageList.innerHTML = pages.map(p =>
    `<li class="page-list-item" data-title="${he(p.title.toLowerCase())}" data-slug="${he(p.slug)}" data-links="${p.links}" data-mtime="${p.mtime}">` +
    `<a href="/page/${p.slug}">${he(p.title)}</a>` +
    `<span class="page-item-right"><span class="page-slug">${he(p.slug)}</span></span></li>`
  ).join('');
}

function modalVisibleItems() {
  const pages = Array.from(modalPageList.querySelectorAll('.page-list-item:not(.hidden)'));
  const cards = Array.from(modalCM.querySelectorAll('.search-result-card'));
  return [...pages, ...cards];
}

function setModalActive(idx) {
  modalPageList.querySelectorAll('.page-list-item').forEach(i => i.classList.remove('active'));
  modalCM.querySelectorAll('.search-result-card').forEach(i => i.classList.remove('active'));
  const items = modalVisibleItems();
  modalActiveIdx = items.length ? Math.max(0, Math.min(idx, items.length - 1)) : -1;
  if (modalActiveIdx >= 0) {
    items[modalActiveIdx].classList.add('active');
    items[modalActiveIdx].scrollIntoView({ block: 'nearest' });
  }
}

modalFilter?.addEventListener('input', applyModalControls);
modalSort?.addEventListener('change', applyModalControls);
modalUnlinked?.addEventListener('change', applyModalControls);

modalFilter?.addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    const item = modalVisibleItems()[modalActiveIdx];
    const target = item?.tagName === 'A' ? item : item?.querySelector('a');
    if (target) { e.preventDefault(); window.location.href = target.href; }
  } else if (e.key === 'ArrowDown') {
    e.preventDefault(); setModalActive(modalActiveIdx < 0 ? 0 : modalActiveIdx + 1);
  } else if (e.key === 'ArrowUp') {
    e.preventDefault(); setModalActive(Math.max(0, modalActiveIdx - 1));
  }
});

function fuzzyScore(pattern, str) {
  const p = pattern.toLowerCase();
  const s = str.toLowerCase();
  let pi = 0, score = 0, run = 0;
  const indices = [];
  for (let si = 0; si < s.length && pi < p.length; si++) {
    if (s[si] === p[pi]) {
      run++;
      score += run;
      if (si === 0) score += 8;
      else if ('-/ _'.includes(s[si - 1])) score += 4;
      indices.push(si);
      pi++;
    } else {
      run = 0;
    }
  }
  return pi === p.length ? { score, indices } : null;
}

function highlightIndices(str, indices) {
  const set = new Set(indices);
  let out = '', inMark = false;
  for (let i = 0; i < str.length; i++) {
    const matched = set.has(i);
    if (matched && !inMark)  { out += '<mark>'; inMark = true; }
    if (!matched && inMark)  { out += '</mark>'; inMark = false; }
    out += he(str[i]);
  }
  if (inMark) out += '</mark>';
  return out;
}

function applyModalControls() {
  if (!modalPages) return;
  const q = modalFilter.value.trim();
  const sort = modalSort.value;
  const unlinked = modalUnlinked.checked;

  if (q) {
    // Fuzzy mode: score, rank by quality, render with highlights
    const scored = [];
    for (const p of modalPages) {
      if (unlinked && p.links !== 0) continue;
      const ts = fuzzyScore(q, p.title);
      const ss = fuzzyScore(q, p.slug);
      const best = ts && (!ss || ts.score >= ss.score) ? { ...ts, field: 'title' }
                 : ss ? { ...ss, field: 'slug' } : null;
      if (!best) continue;
      scored.push({ p, ...best });
    }
    scored.sort((a, b) => b.score - a.score);
    modalPageList.innerHTML = scored.slice(0, 200).map(({ p, indices, field }) => {
      const titleHtml = field === 'title' ? highlightIndices(p.title, indices) : he(p.title);
      const slugHtml  = field === 'slug'  ? highlightIndices(p.slug,  indices) : he(p.slug);
      return `<li class="page-list-item" data-title="${he(p.title.toLowerCase())}" data-slug="${he(p.slug)}" data-links="${p.links}" data-mtime="${p.mtime}">` +
        `<a href="/page/${p.slug}">${titleHtml}</a>` +
        `<span class="page-item-right"><span class="page-slug">${slugHtml}</span></span></li>`;
    }).join('');
  } else {
    // No query: static list, sort by dropdown, filter by unlinked checkbox
    renderModalList(modalPages.slice(0, 200));
    let items = Array.from(modalPageList.querySelectorAll('.page-list-item'));
    items.sort((a, b) => {
      if (sort === 'title-desc') return b.dataset.title.localeCompare(a.dataset.title);
      if (sort === 'mtime-desc') return parseInt(b.dataset.mtime) - parseInt(a.dataset.mtime);
      if (sort === 'links-desc') return parseInt(b.dataset.links) - parseInt(a.dataset.links);
      return a.dataset.title.localeCompare(b.dataset.title);
    });
    items.forEach(i => modalPageList.appendChild(i));
    items.forEach(i => i.classList.toggle('hidden', unlinked && parseInt(i.dataset.links) > 0));
  }

  setModalActive(q ? 0 : -1);

  clearTimeout(modalCMTimer);
  if (q.length >= 2) {
    modalCMTimer = setTimeout(() => fetchModalContentMatches(q), 300);
  } else {
    modalCM.innerHTML = '';
    modalCM.classList.add('hidden');
  }
}

async function fetchModalContentMatches(q) {
  try {
    const res = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
    const results = await res.json();
    if (!results.length) { modalCM.innerHTML = ''; modalCM.classList.add('hidden'); return; }
    modalCM.innerHTML = '<p class="section-label">Content matches</p>' +
      results.map(r =>
        `<a class="search-result-card" href="/page/${r.slug}"><span class="result-title">${r.title}</span><p>${r.excerpt}</p></a>`
      ).join('');
    modalCM.classList.remove('hidden');
    // Re-clamp active index now that content cards are in the pool
    if (modalActiveIdx >= 0) setModalActive(modalActiveIdx);
  } catch (_) {}
}

// ── Quick-task modal ──────────────────────────────────────────────────────────

const quickTaskModal  = document.getElementById('quick-task-modal');
const quickTaskForm   = document.getElementById('quick-task-form');
const quickTaskInput  = document.getElementById('quick-task-input');
const quickTaskCancel = document.getElementById('quick-task-cancel');

quickTaskCancel?.addEventListener('click', () => quickTaskModal.close());
quickTaskModal?.addEventListener('click', e => {
  if (e.target === quickTaskModal) quickTaskModal.close();
});

function openQuickTask() {
  if (!quickTaskModal) return;
  quickTaskInput.value = '';
  quickTaskModal.showModal();
  quickTaskInput.focus();
}

quickTaskForm?.addEventListener('submit', async e => {
  e.preventDefault();
  const description = quickTaskInput.value.trim();
  if (!description) return;
  quickTaskModal.close();
  const data = new FormData();
  data.append('description', description);
  try {
    await fetch('/api/add-task', { method: 'POST', body: data });
    const path = window.location.pathname;
    if (path === '/page/tasks') {
      window.location.reload();
    } else {
      // If we're editing the tasks page, sync the textarea too
      const editorTextarea = document.getElementById('editor-textarea');
      if (editorTextarea && path === '/edit/tasks') {
        const line = `- [ ] ${description}`;
        if (editorTextarea.value && !editorTextarea.value.endsWith('\n'))
          editorTextarea.value += '\n';
        editorTextarea.value += line + '\n';
        if (typeof fetchPreview === 'function') fetchPreview();
      }
    }
  } catch (_) {}
});

// ── Scratch modal ─────────────────────────────────────────────────────────────

const scratchModal  = document.getElementById('scratch-modal');
const scratchForm   = document.getElementById('scratch-form');
const scratchTitle  = document.getElementById('scratch-title');
const scratchCancel = document.getElementById('scratch-cancel');

document.getElementById('open-scratch')?.addEventListener('click', openScratch);
scratchCancel?.addEventListener('click', () => scratchModal.close());

scratchModal?.addEventListener('click', e => {
  if (e.target === scratchModal) scratchModal.close();
});

function openScratch() {
  if (!scratchModal) return;
  scratchTitle.value = '';
  scratchModal.showModal();
  scratchTitle.focus();
}

scratchForm?.addEventListener('submit', async e => {
  e.preventDefault();
  const title = scratchTitle.value.trim() || 'Untitled';
  const data = new FormData();
  data.append('title', title);
  try {
    const res = await fetch('/api/scratch', { method: 'POST', body: data });
    const { slug } = await res.json();
    window.location.href = `/edit/${slug}`;
  } catch (_) {}
});

// ── Page delete dialog ────────────────────────────────────────────────────────

document.getElementById('page-delete-cancel')?.addEventListener('click', () => {
  document.getElementById('page-delete-dialog').close();
});
document.getElementById('page-delete-dialog')?.addEventListener('click', e => {
  if (e.target === document.getElementById('page-delete-dialog'))
    document.getElementById('page-delete-dialog').close();
});

// ── New-page modal ─────────────────────────────────────────────────────────────

const newPageModal  = document.getElementById('new-page-modal');
const newPageInput  = document.getElementById('new-page-input');
const newPageCancel = document.getElementById('new-page-cancel');

newPageCancel?.addEventListener('click', () => newPageModal.close());
newPageModal?.addEventListener('click', e => {
  if (e.target === newPageModal) newPageModal.close();
});

function openNewPage() {
  if (!newPageModal) return;
  newPageInput.value = '';
  newPageModal.showModal();
  newPageInput.focus();
}

// ── Keyboard shortcuts ────────────────────────────────────────────────────────

const shortcutsModal = document.getElementById('shortcuts-modal');
shortcutsModal?.addEventListener('click', e => { if (e.target === shortcutsModal) shortcutsModal.close(); });

document.addEventListener('keydown', e => {
  // Ctrl/Cmd+P — open search modal
  if (e.key === 'p' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    openSearchModal();
    return;
  }

  // Ctrl/Cmd+N — new page
  if (e.key === 'n' && (e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey) {
    e.preventDefault();
    openNewPage();
    return;
  }

  // Ctrl/Cmd+1 — new scratch
  if (e.key === '1' && (e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey) {
    e.preventDefault();
    openScratch();
    return;
  }

  // Ctrl/Cmd+2 — quick add task
  if (e.key === '2' && (e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey) {
    e.preventDefault();
    openQuickTask();
    return;
  }

  // Ctrl/Cmd+Alt+0–3 — navigate to pinned pages
  if (e.altKey && (e.ctrlKey || e.metaKey)) {
    const pinned = { '0': '/page/index', '1': '/page/_scratch', '2': '/page/tasks', '3': '/page/state' };
    if (pinned[e.key]) { e.preventDefault(); window.location.href = pinned[e.key]; return; }
  }

  // Ctrl/Cmd+/ — show keyboard shortcuts
  if (e.key === '/' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    shortcutsModal?.showModal();
    return;
  }

  // F2 — toggle preview (in editor)
  if (e.key === 'F2' && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey) {
    if (document.getElementById('editor-textarea')) { e.preventDefault(); togglePreview(); }
    return;
  }

  // Ctrl/Cmd+S — save (in editor)
  if (e.key === 's' && (e.ctrlKey || e.metaKey)) {
    const form = document.getElementById('editor-form');
    if (form) { e.preventDefault(); form.submit(); }
    return;
  }

  // Ctrl/Cmd+Delete — delete current page (page view only)
  if (e.key === 'Delete' && (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey) {
    const dlg = document.getElementById('page-delete-dialog');
    if (dlg) { e.preventDefault(); dlg.showModal(); }
    return;
  }

  // Bare key shortcuts — skip when focus is in an input/textarea/select
  if (e.target.matches('input,textarea,select,[contenteditable]') || e.ctrlKey || e.metaKey || e.altKey) return;

  // E — go to edit
  if (e.key === 'e') {
    const editLink = document.querySelector('a[href^="/edit/"]');
    if (editLink) window.location.href = editLink.href;
  }

  // ? — show keyboard shortcuts
  if (e.key === '?') shortcutsModal?.showModal();
});

// ── Editor ────────────────────────────────────────────────────────────────────

const textarea = document.getElementById('editor-textarea');
const previewPane = document.getElementById('editor-preview');
let previewTimer = null;

if (textarea && previewPane) {
  // Pre-fetch all page slugs for wikilink autocomplete
  fetch('/api/pages').then(r => r.json()).then(p => { wikiPages = p; }).catch(() => {});

  textarea.addEventListener('keydown', e => {
    // Wikilink autocomplete keyboard nav
    if (acDropdown) {
      if (e.key === 'ArrowDown') { e.preventDefault(); acMove(1); return; }
      if (e.key === 'ArrowUp')   { e.preventDefault(); acMove(-1); return; }
      if (e.key === 'Enter' || e.key === 'Tab') {
        const sel = acDropdown.querySelector('.selected');
        if (sel) { e.preventDefault(); insertWikilink(sel.textContent); return; }
      }
      if (e.key === 'Escape') { hideAcDropdown(); return; }
    }

    // Tab → try snippet expansion, then insert spaces
    if (e.key === 'Tab') {
      e.preventDefault();
      if (expandSnippet()) {
        clearTimeout(previewTimer);
        previewTimer = setTimeout(fetchPreview, 300);
        return;
      }
      const s = textarea.selectionStart, end = textarea.selectionEnd;
      textarea.value = textarea.value.slice(0, s) + '  ' + textarea.value.slice(end);
      textarea.selectionStart = textarea.selectionEnd = s + 2;
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
        textarea.value = val.slice(0, lineStart) + toggled + val.slice(lineEnd === -1 ? val.length : lineEnd);
        textarea.selectionStart = textarea.selectionEnd = pos;
        clearTimeout(previewTimer);
        previewTimer = setTimeout(fetchPreview, 300);
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
        const wl = `[[${selected}]]`;
        textarea.value = textarea.value.slice(0, start) + wl + textarea.value.slice(end);
        textarea.selectionStart = textarea.selectionEnd = start + wl.length - 2; // before ]]
        clearTimeout(previewTimer);
        previewTimer = setTimeout(fetchPreview, 300);
        return;
      }
      // Cursor inside [[...]] → follow the link
      const wlText = wikilinkAtCursor();
      if (wlText !== null) {
        e.preventDefault();
        const pageName = wlText.split('|')[0].trim();
        const slug = pageName.toLowerCase().replace(/[^a-z0-9/]+/g, '-').replace(/^-+|-+$/g, '');
        window.location.href = `/page/${slug}`;
        return;
      }
      continueList(e);
    }
  });

  textarea.addEventListener('input', () => {
    updateAcDropdown();
    clearTimeout(previewTimer);
    previewTimer = setTimeout(fetchPreview, 300);
  });

  textarea.addEventListener('blur', () => {
    // Delay so a mousedown on a dropdown item fires first
    setTimeout(hideAcDropdown, 150);
  });

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
  uploadConflictModal?.addEventListener('click', e => {
    if (e.target === uploadConflictModal) uploadConflictModal.close();
  });

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

  const urlNameModal   = document.getElementById('url-name-modal');
  const urlNameHref    = document.getElementById('url-name-href');
  const urlNameInput   = document.getElementById('url-name-input');
  const urlNameConfirm = document.getElementById('url-name-confirm');
  const urlNameCancel  = document.getElementById('url-name-cancel');

  function askUrlName(url) {
    return new Promise(resolve => {
      urlNameHref.textContent = url;
      urlNameInput.value = '';
      urlNameModal.showModal();
      urlNameInput.focus();

      function done(result) {
        urlNameModal.close();
        urlNameConfirm.onclick = null;
        urlNameCancel.onclick = null;
        resolve(result);
      }
      urlNameConfirm.onclick = () => done(urlNameInput.value.trim() || null);
      urlNameCancel.onclick  = () => done(false);
    });
  }
  urlNameModal?.addEventListener('click', e => {
    if (e.target === urlNameModal) urlNameModal.close();
  });

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
    // URL paste: wrap selection or ask for display name
    const text = (e.clipboardData.getData('text/plain') || '').trim();
    if (/^https?:\/\/\S+$/.test(text)) {
      e.preventDefault();
      const start = textarea.selectionStart;
      const end   = textarea.selectionEnd;
      if (start !== end) {
        // Text selected — wrap as [selection](url) without modal
        const selected = textarea.value.slice(start, end);
        const md = `[${selected}](${text})`;
        textarea.value = textarea.value.slice(0, start) + md + textarea.value.slice(end);
        textarea.selectionStart = textarea.selectionEnd = start + md.length;
        fetchPreview();
        return;
      }
      // No selection — existing modal behavior
      const name = await askUrlName(text);
      if (name === false) {
        // cancelled — insert raw URL
        document.execCommand('insertText', false, text);
        return;
      }
      const md = name ? `[${name}](${text})` : text;
      const pos = textarea.selectionStart;
      textarea.value = textarea.value.slice(0, pos) + md + textarea.value.slice(textarea.selectionEnd);
      textarea.selectionStart = textarea.selectionEnd = pos + md.length;
      fetchPreview();
    }
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

  textarea.focus();
  textarea.selectionStart = textarea.selectionEnd = textarea.value.length;

  // Sync preview scroll position with editor
  const previewScroller = previewPane.parentElement;
  textarea.addEventListener('scroll', () => {
    const ratio = textarea.scrollTop / (textarea.scrollHeight - textarea.clientHeight || 1);
    previewScroller.scrollTop = ratio * (previewScroller.scrollHeight - previewScroller.clientHeight);
  });

  // Auto-save on navigation away if content has changed
  const savedContent = textarea.value;
  const editorForm = document.getElementById('editor-form');
  const autoSaveUrl = editorForm?.action.replace('/edit/', '/api/save/');

  window.addEventListener('pagehide', () => {
    if (!autoSaveUrl || textarea.value === savedContent) return;
    const data = new FormData();
    data.append('content', textarea.value);
    navigator.sendBeacon(autoSaveUrl, data);
  });
}

function togglePreview() {
  if (!previewPane) return;
  const showing = previewPane.parentElement.classList.toggle('visible');
  if (showing) fetchPreview();
}

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
    highlightPreviewBlock();
  } catch (_) { /* ignore */ }
}

function highlightPreviewBlock() {
  if (!textarea || !previewPane) return;
  const pos = textarea.selectionStart;
  const text = textarea.value;
  const cursorLine = text.slice(0, pos).split('\n').length - 1;
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

// ── List continuation ─────────────────────────────────────────────────────────

function continueList(e) {
  const pos = textarea.selectionStart;
  if (pos !== textarea.selectionEnd) return;
  const val = textarea.value;
  const lineStart = val.lastIndexOf('\n', pos - 1) + 1;
  const line = val.slice(lineStart, pos);

  const cb       = line.match(/^(\s*)([-*+]) \[[xX ]\] (.+)/);
  const cbEmpty  = line.match(/^(\s*)([-*+]) \[[xX ]\] $/);
  const ocb      = line.match(/^(\s*)(\d+)\. \[[xX ]\] (.+)/);
  const ocbEmpty = line.match(/^(\s*)(\d+)\. \[[xX ]\] $/);
  const ul       = line.match(/^(\s*)([-*+]) (.+)/);
  const ulEmpty  = line.match(/^(\s*)([-*+]) $/);
  const ol       = line.match(/^(\s*)(\d+)\. (.+)/);
  const olEmpty  = line.match(/^(\s*)(\d+)\. $/);

  if (cbEmpty || ocbEmpty) {
    // Empty checkbox item → exit list
    e.preventDefault();
    textarea.value = val.slice(0, lineStart) + '\n' + val.slice(pos);
    textarea.selectionStart = textarea.selectionEnd = lineStart + 1;
  } else if (cb) {
    e.preventDefault();
    const insert = '\n' + cb[1] + cb[2] + ' [ ] ';
    textarea.value = val.slice(0, pos) + insert + val.slice(pos);
    textarea.selectionStart = textarea.selectionEnd = pos + insert.length;
  } else if (ocb) {
    e.preventDefault();
    const insert = '\n' + ocb[1] + (parseInt(ocb[2]) + 1) + '. [ ] ';
    textarea.value = val.slice(0, pos) + insert + val.slice(pos);
    textarea.selectionStart = textarea.selectionEnd = pos + insert.length;
  } else if (ulEmpty || olEmpty) {
    // Empty list item → exit list
    e.preventDefault();
    textarea.value = val.slice(0, lineStart) + '\n' + val.slice(pos);
    textarea.selectionStart = textarea.selectionEnd = lineStart + 1;
  } else if (ul) {
    e.preventDefault();
    const insert = '\n' + ul[1] + ul[2] + ' ';
    textarea.value = val.slice(0, pos) + insert + val.slice(pos);
    textarea.selectionStart = textarea.selectionEnd = pos + insert.length;
  } else if (ol) {
    e.preventDefault();
    const insert = '\n' + ol[1] + (parseInt(ol[2]) + 1) + '. ';
    textarea.value = val.slice(0, pos) + insert + val.slice(pos);
    textarea.selectionStart = textarea.selectionEnd = pos + insert.length;
  } else {
    return;
  }
  clearTimeout(previewTimer);
  previewTimer = setTimeout(fetchPreview, 300);
}

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

// ── Wikilink autocomplete ─────────────────────────────────────────────────────

let wikiPages = [];
let acDropdown = null;
let acIndex = 0;

function getWikilinkQuery() {
  const before = textarea.value.slice(0, textarea.selectionStart);
  const m = before.match(/\[\[([^\][\n]*)$/);
  return m ? m[1] : null;
}

function updateAcDropdown() {
  const query = getWikilinkQuery();
  if (query === null) { hideAcDropdown(); return; }
  const q = query.toLowerCase();
  const matches = wikiPages.filter(p => p.includes(q)).slice(0, 8);
  if (!matches.length) { hideAcDropdown(); return; }
  showAcDropdown(matches);
}

// Measure approximate caret position inside textarea using a mirror div
function getCaretCoords(el) {
  const computed = window.getComputedStyle(el);
  const mirror = document.createElement('div');
  Object.assign(mirror.style, {
    position: 'absolute', visibility: 'hidden', whiteSpace: 'pre-wrap',
    wordWrap: 'break-word', overflow: 'hidden',
    boxSizing: computed.boxSizing,
    width: computed.width,
    paddingTop: computed.paddingTop, paddingRight: computed.paddingRight,
    paddingBottom: computed.paddingBottom, paddingLeft: computed.paddingLeft,
    borderTopWidth: computed.borderTopWidth, borderLeftWidth: computed.borderLeftWidth,
    fontFamily: computed.fontFamily, fontSize: computed.fontSize,
    fontWeight: computed.fontWeight, lineHeight: computed.lineHeight,
    letterSpacing: computed.letterSpacing,
  });
  document.body.appendChild(mirror);
  mirror.textContent = el.value.slice(0, el.selectionStart);
  const span = document.createElement('span');
  span.textContent = '\u200b';
  mirror.appendChild(span);
  const coords = { top: span.offsetTop, left: span.offsetLeft };
  document.body.removeChild(mirror);
  return coords;
}

function showAcDropdown(matches) {
  if (!acDropdown) {
    acDropdown = document.createElement('div');
    acDropdown.className = 'wikilink-ac';
    document.body.appendChild(acDropdown);
    acIndex = 0;
  }

  acDropdown.innerHTML = '';
  matches.forEach((page, i) => {
    const item = document.createElement('div');
    item.className = 'wikilink-ac-item' + (i === acIndex ? ' selected' : '');
    item.textContent = page;
    item.addEventListener('mousedown', ev => { ev.preventDefault(); insertWikilink(page); });
    acDropdown.appendChild(item);
  });
  if (acIndex >= matches.length) acIndex = 0;

  const elRect = textarea.getBoundingClientRect();
  const coords = getCaretCoords(textarea);
  const lineH = parseFloat(window.getComputedStyle(textarea).lineHeight);
  let top = elRect.top + coords.top - textarea.scrollTop + lineH;
  let left = elRect.left + coords.left;
  // Keep within viewport
  top = Math.min(top, window.innerHeight - 220);
  left = Math.min(left, window.innerWidth - 200);
  acDropdown.style.top = top + 'px';
  acDropdown.style.left = left + 'px';
}

function hideAcDropdown() {
  if (acDropdown) { acDropdown.remove(); acDropdown = null; }
}

function acMove(dir) {
  if (!acDropdown) return;
  const items = acDropdown.querySelectorAll('.wikilink-ac-item');
  items[acIndex].classList.remove('selected');
  acIndex = (acIndex + dir + items.length) % items.length;
  items[acIndex].classList.add('selected');
}

function insertWikilink(page) {
  const pos = textarea.selectionStart;
  const val = textarea.value;
  const bracketPos = val.lastIndexOf('[[', pos);
  textarea.value = val.slice(0, bracketPos) + '[[' + page + ']]' + val.slice(pos);
  const newPos = bracketPos + 2 + page.length + 2;
  textarea.selectionStart = textarea.selectionEnd = newPos;
  hideAcDropdown();
  clearTimeout(previewTimer);
  previewTimer = setTimeout(fetchPreview, 300);
}

// ── Snippets ──────────────────────────────────────────────────────────────────

const snippets = {
  '/date':     () => new Date().toISOString().slice(0, 10),
  '/time':     () => new Date().toTimeString().slice(0, 5),
  '/datetime': () => new Date().toISOString().slice(0, 16).replace('T', ' '),
};

function expandSnippet() {
  const pos = textarea.selectionStart;
  if (pos !== textarea.selectionEnd) return false;
  const before = textarea.value.slice(0, pos);
  for (const [trigger, expand] of Object.entries(snippets)) {
    if (before.endsWith(trigger)) {
      const expansion = expand();
      textarea.value = textarea.value.slice(0, pos - trigger.length) + expansion + textarea.value.slice(pos);
      textarea.selectionStart = textarea.selectionEnd = pos - trigger.length + expansion.length;
      return true;
    }
  }
  return false;
}


// ── Delete confirmation ───────────────────────────────────────────────────────

document.getElementById('delete-btn')?.addEventListener('click', e => {
  const title = e.currentTarget.dataset.title;
  if (!confirm(`Delete "${title}"? This cannot be undone.`)) e.preventDefault();
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

// ── Syntax highlighting ───────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  if (!window.hljs) return;
  document.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
});

// ── Task list checkboxes ───────────────────────────────────────────────────────

document.addEventListener('change', e => {
  if (!e.target.matches('.task-list-item-checkbox')) return;
  const checkboxes = Array.from(document.querySelectorAll('.task-list-item-checkbox'));
  const idx = checkboxes.indexOf(e.target);
  if (idx === -1) return;

  if (textarea) {
    // Editor: toggle the Nth checkbox directly in the textarea, then refresh preview
    const val = textarea.value;
    const matches = [...val.matchAll(/\[([xX ])\]/g)];
    if (idx < matches.length) {
      const m = matches[idx];
      const newChar = m[1] === ' ' ? 'x' : ' ';
      textarea.value = val.slice(0, m.index) + `[${newChar}]` + val.slice(m.index + m[0].length);
      clearTimeout(previewTimer);
      previewTimer = setTimeout(fetchPreview, 300);
    }
  } else {
    // Page view: persist toggle to server
    const slug = window.location.pathname.replace(/^\/page\//, '');
    const data = new FormData();
    data.append('idx', idx);
    fetch(`/api/toggle-checkbox/${slug}`, { method: 'POST', body: data });
  }
});
