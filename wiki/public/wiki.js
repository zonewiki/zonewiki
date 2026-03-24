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

function applyModalControls() {
  if (!modalPages) return;
  const q = modalFilter.value.toLowerCase().trim();
  const sort = modalSort.value;
  const unlinked = modalUnlinked.checked;

  let items = Array.from(modalPageList.querySelectorAll('.page-list-item'));
  items.sort((a, b) => {
    if (sort === 'title-desc') return b.dataset.title.localeCompare(a.dataset.title);
    if (sort === 'mtime-desc') return parseInt(b.dataset.mtime) - parseInt(a.dataset.mtime);
    if (sort === 'links-desc') return parseInt(b.dataset.links) - parseInt(a.dataset.links);
    return a.dataset.title.localeCompare(b.dataset.title);
  });
  items.forEach(i => modalPageList.appendChild(i));
  items.forEach(i => {
    const textMatch = !q || i.dataset.title.includes(q) || i.dataset.slug.includes(q);
    const linkMatch = !unlinked || i.dataset.links === '0';
    i.classList.toggle('hidden', !textMatch || !linkMatch);
  });

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

// ── Memo modal ────────────────────────────────────────────────────────────────

const memoModal  = document.getElementById('memo-modal');
const memoForm   = document.getElementById('memo-form');
const memoTitle  = document.getElementById('memo-title');
const memoCancel = document.getElementById('memo-cancel');

document.getElementById('open-memo')?.addEventListener('click', openMemo);
memoCancel?.addEventListener('click', () => memoModal.close());

memoModal?.addEventListener('click', e => {
  if (e.target === memoModal) memoModal.close();
});

function openMemo() {
  if (!memoModal) return;
  memoTitle.value = '';
  memoModal.showModal();
  memoTitle.focus();
}

memoForm?.addEventListener('submit', async e => {
  e.preventDefault();
  const title = memoTitle.value.trim() || 'Untitled';
  const data = new FormData();
  data.append('title', title);
  try {
    const res = await fetch('/api/memo', { method: 'POST', body: data });
    const { slug } = await res.json();
    window.location.href = `/edit/${slug}`;
  } catch (_) {}
});

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

  // Ctrl/Cmd+M — new memo
  if (e.key === 'm' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    openMemo();
    return;
  }

  // Ctrl/Cmd+/ — show keyboard shortcuts
  if (e.key === '/' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    shortcutsModal?.showModal();
    return;
  }

  // Ctrl/Cmd+S — save (in editor)
  if (e.key === 's' && (e.ctrlKey || e.metaKey)) {
    const form = document.getElementById('editor-form');
    if (form) { e.preventDefault(); form.submit(); }
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

    // Enter → continue list
    if (e.key === 'Enter' && !e.shiftKey) {
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

  textarea.addEventListener('paste', async (e) => {
    const imageItem = Array.from(e.clipboardData.items).find(i => i.type.startsWith('image/'));
    if (!imageItem) return;
    e.preventDefault();
    const start = textarea.selectionStart, end = textarea.selectionEnd;
    const placeholder = '![uploading…]()';
    textarea.value = textarea.value.slice(0, start) + placeholder + textarea.value.slice(end);
    textarea.selectionStart = textarea.selectionEnd = start + placeholder.length;
    const fd = new FormData();
    fd.append('file', imageItem.getAsFile());
    try {
      const res = await fetch('/api/upload', { method: 'POST', body: fd });
      if (!res.ok) throw new Error();
      const { url } = await res.json();
      const md = `![image](${url})`;
      textarea.value = textarea.value.slice(0, start) + md + textarea.value.slice(start + placeholder.length);
      textarea.selectionStart = textarea.selectionEnd = start + md.length;
    } catch (_) {
      textarea.value = textarea.value.slice(0, start) + textarea.value.slice(start + placeholder.length);
    }
    fetchPreview();
  });

  fetchPreview();
  textarea.focus();
  textarea.selectionStart = textarea.selectionEnd = textarea.value.length;

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

async function fetchPreview() {
  try {
    const res = await fetch('/api/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain' },
      body: textarea.value,
    });
    previewPane.innerHTML = await res.text();
    if (window.hljs) previewPane.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
  } catch (_) { /* ignore */ }
}

// ── List continuation ─────────────────────────────────────────────────────────

function continueList(e) {
  const pos = textarea.selectionStart;
  if (pos !== textarea.selectionEnd) return;
  const val = textarea.value;
  const lineStart = val.lastIndexOf('\n', pos - 1) + 1;
  const line = val.slice(lineStart, pos);

  const ul = line.match(/^(\s*)([-*+]) (.+)/);
  const ulEmpty = line.match(/^(\s*)([-*+]) $/);
  const ol = line.match(/^(\s*)(\d+)\. (.+)/);
  const olEmpty = line.match(/^(\s*)(\d+)\. $/);

  if (ulEmpty || olEmpty) {
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
