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
  // Intercept Tab key to insert spaces instead of moving focus
  textarea.addEventListener('keydown', e => {
    if (e.key === 'Tab') {
      e.preventDefault();
      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      textarea.value = textarea.value.slice(0, start) + '  ' + textarea.value.slice(end);
      textarea.selectionStart = textarea.selectionEnd = start + 2;
    }
  });

  textarea.addEventListener('input', () => {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(fetchPreview, 300);
  });

  textarea.addEventListener('paste', async (e) => {
    const imageItem = Array.from(e.clipboardData.items).find(i => i.type.startsWith('image/'));
    if (!imageItem) return;
    e.preventDefault();

    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const placeholder = '![uploading…]()';
    textarea.value = textarea.value.slice(0, start) + placeholder + textarea.value.slice(end);
    textarea.selectionStart = textarea.selectionEnd = start + placeholder.length;

    const formData = new FormData();
    formData.append('file', imageItem.getAsFile());
    try {
      const res = await fetch('/api/upload', { method: 'POST', body: formData });
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

  // Render initial preview on load
  fetchPreview();
}

async function fetchPreview() {
  try {
    const res = await fetch('/api/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain' },
      body: textarea.value,
    });
    const html = await res.text();
    previewPane.innerHTML = html;
    highlightPreview();
  } catch (_) { /* ignore */ }
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

function highlightAll(root) {
  if (!window.hljs) return;
  root.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
}

function highlightPreview() {
  if (previewPane) highlightAll(previewPane);
}

document.addEventListener('DOMContentLoaded', () => {
  highlightAll(document);
});
