// ── Search ────────────────────────────────────────────────────────────────────

const searchInput = document.getElementById('search-input');
const searchDropdown = document.getElementById('search-dropdown');
let searchTimer = null;

if (searchInput) {
  searchInput.addEventListener('input', () => {
    clearTimeout(searchTimer);
    const q = searchInput.value.trim();
    if (!q) { searchDropdown.classList.add('hidden'); return; }
    searchTimer = setTimeout(() => fetchSearch(q), 300);
  });

  searchInput.addEventListener('keydown', e => {
    if (e.key === 'Enter') {
      const q = searchInput.value.trim();
      if (q) window.location.href = `/search?q=${encodeURIComponent(q)}`;
    }
    if (e.key === 'Escape') {
      searchDropdown.classList.add('hidden');
      searchInput.blur();
    }
  });

  document.addEventListener('click', e => {
    if (!e.target.closest('.search-wrap')) {
      searchDropdown.classList.add('hidden');
    }
  });
}

async function fetchSearch(q) {
  try {
    const res = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
    const results = await res.json();
    renderDropdown(results);
  } catch (_) { /* network error — ignore */ }
}

function renderDropdown(results) {
  if (!results.length) {
    searchDropdown.innerHTML =
      '<div class="search-result-item"><span class="result-title">No results</span></div>';
  } else {
    searchDropdown.innerHTML = results.map(r => `
      <a class="search-result-item" href="/page/${r.slug}">
        <div class="result-title">${r.title}</div>
        <div class="result-excerpt">${r.excerpt}</div>
      </a>`).join('');
  }
  searchDropdown.classList.remove('hidden');
}

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

// ── Page list filter ─────────────────────────────────────────────────────────

const pageFilter = document.getElementById('page-filter');
const pageList = document.getElementById('page-list');

if (pageFilter && pageList) {
  pageFilter.focus();
  pageFilter.addEventListener('input', () => {
    const q = pageFilter.value.toLowerCase();
    pageList.querySelectorAll('.page-list-item').forEach(item => {
      const match = item.dataset.title.includes(q) || item.dataset.slug.includes(q);
      item.classList.toggle('hidden', !match);
    });
  });
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
