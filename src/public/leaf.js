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
  hist.push({ slug, title, url: window.location.pathname + window.location.search });
  if (hist.length > 6) hist = hist.slice(-6);
  sessionStorage.setItem(KEY, JSON.stringify(hist));

  // All entries before the current page (last) become breadcrumb links. A
  // phone shows only the last 3 of them, so the trail is at most 4 pages.
  const maxPrev = window.matchMedia('(max-width: 640px)').matches ? 3 : Infinity;
  // The modules' own pages are in the module switcher already: home,
  // Scratch, /deck and the deck's home page (where the Deck link goes).
  const deckHref = bc.querySelector('.module-link[data-module="deck"]')?.getAttribute('href');
  const intermediates = hist.slice(0, -1)
    .filter(p => !['index', '_scratch', '/deck'].includes(p.slug) && (p.url || '').split('?')[0] !== deckHref)
                            .slice(-maxPrev);
  if (!intermediates.length) return;

  const homeEl = bc.querySelector('.breadcrumb-home');
  if (!homeEl) return;
  const rightEl = bc.querySelector('.breadcrumb-right');

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
    a.href = p.url || `/page/${p.slug}`;
    a.textContent = p.title;
    bc.appendChild(a);
  }
  bc.appendChild(mkSep());
  if (window.location.search) {
    const cur = document.createElement('a');
    cur.href = window.location.pathname + window.location.search;
    cur.textContent = title;
    bc.appendChild(cur);
  } else {
    const cur = document.createElement('span');
    cur.textContent = title;
    bc.appendChild(cur);
  }

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
    if (rightEl) bc.appendChild(rightEl);
    clr.remove();
  });
  bc.appendChild(clr);
  if (rightEl) bc.appendChild(rightEl);
}());

// ── Backlinks chip ───────────────────────────────────────────────────────────
// The "← N" chip lists the linking pages on hover and jumps to the Backlinks
// section when clicked. Touch has no hover, so there a tap toggles the list
// instead of scrolling the page down; a tap anywhere else closes it.
// Delegated, because the breadcrumb code above rebuilds the breadcrumb.

(function () {
  document.addEventListener('click', e => {
    const open = document.querySelector('.breadcrumb-right.bl-open');
    const chip = e.target.closest('.bl-count');
    if (chip && matchMedia('(hover: none)').matches) {
      e.preventDefault();
      chip.parentElement.classList.toggle('bl-open');
      return;
    }
    if (open && !e.target.closest('.bl-tooltip')) open.classList.remove('bl-open');
  });
}());

// ── Uncommitted changes badge ────────────────────────────────────────────────

(function () {
  const uncommittedBadge = document.getElementById('uncommitted-badge');
  const uncommittedCount = document.getElementById('uncommitted-count');

  if (!uncommittedBadge) return;

  async function checkUncommitted() {
    try {
      const res = await fetch('/api/uncommitted');
      const files = await res.json();

      if (files.length > 0) {
        uncommittedCount.textContent = files.length;
        uncommittedBadge.style.display = 'inline-flex';
      } else {
        uncommittedBadge.style.display = 'none';
      }
    } catch (e) {
      // Silently ignore errors (git might not be enabled)
    }
  }

  // Check on page load and every 30 seconds (skipped while the tab is hidden;
  // refreshed immediately when it becomes visible again)
  checkUncommitted();
  setInterval(() => { if (!document.hidden) checkUncommitted(); }, 30000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) checkUncommitted(); });
}());

// ── Modules ─────────────────────────────────────────────────────────────────
// The module switcher (breadcrumb) and the phone's bottom bar link to the
// three modules; the server marks the current one (aria-current). The Deck
// links carry the counts of tasks due and in Focus.

(function () {
  // The server renders the counts into the page (no change of width after
  // it has painted); this keeps them current: every minute, when the tab
  // comes back and after deck writes (window.refreshDueBadge).
  const links = document.querySelectorAll('.module-link[data-module="deck"]');
  if (!links.length) return;

  // Same as server.py's _deck_counts_title.
  function deckCountsTitle({ focus, overdue, today }) {
    const parts = [];
    if (focus > 0) parts.push(`${focus} in Focus`);
    if (overdue > 0) parts.push(`${overdue} overdue`);
    if (today > 0) parts.push(`${today} due today`);
    return parts.length ? `Deck: ${parts.join(' · ')}` : 'Deck';
  }

  function show(el, n) {
    const text = n > 0 ? String(n) : '';
    if (el && el.textContent !== text) el.textContent = text;
    if (el) el.hidden = !(n > 0);
  }

  async function checkDue() {
    try {
      const res = await fetch('/api/deck/due-count');
      const c = await res.json();
      links.forEach(a => {
        show(a.querySelector('.module-due'), c.count);
        show(a.querySelector('.module-focus'), c.focus);
        a.title = deckCountsTitle(c);
      });
    } catch (e) {}
  }

  window.refreshDueBadge = checkDue;
  setInterval(() => { if (!document.hidden) checkDue(); }, 60000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) checkDue(); });
  // A page restored from the back/forward cache shows old counts.
  window.addEventListener('pageshow', e => { if (e.persisted) checkDue(); });
}());

// ── Helpers ───────────────────────────────────────────────────────────────────

function closeOnBackdropClick(dialog) {
  dialog?.addEventListener('click', e => {
    if (e.target === dialog) dialog.close();
  });
}

function he(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
window.he = he;

// Prompt for a display name when pasting a bare URL. Resolves with the name,
// null (no name — insert plain URL), or false (cancelled). Shared with deck.js.
function askUrlName(url) {
  const urlNameModal   = document.getElementById('url-name-modal');
  const urlNameHref    = document.getElementById('url-name-href');
  const urlNameInput   = document.getElementById('url-name-input');
  const urlNameConfirm = document.getElementById('url-name-confirm');
  const urlNameCancel  = document.getElementById('url-name-cancel');
  return new Promise(resolve => {
    urlNameHref.textContent = url;
    urlNameInput.value = '';
    urlNameModal.showModal();
    urlNameInput.focus();

    function done(result) {
      urlNameModal.close();
      urlNameConfirm.onclick = null;
      urlNameCancel.onclick = null;
      urlNameInput.onkeydown = null;
      resolve(result);
    }
    urlNameConfirm.onclick = () => done(urlNameInput.value.trim() || null);
    urlNameCancel.onclick  = () => done(false);
    urlNameInput.onkeydown = e => {
      if (e.key === 'Enter') { e.preventDefault(); done(urlNameInput.value.trim() || null); }
    };
  });
}
window.askUrlName = askUrlName;
closeOnBackdropClick(document.getElementById('url-name-modal'));

// Pasting a bare URL into a markdown textarea (page editor, deck notes):
// selected text becomes [selection](url); otherwise the name prompt asks for
// a label (none, or cancel, inserts the plain URL). The insertion goes through
// execCommand('insertText') so it is one step on the native undo stack and
// fires `input` (preview, auto-save). Returns false if the paste is no URL.
async function pasteUrlAsLink(ta, e) {
  const url = (e.clipboardData.getData('text/plain') || '').trim();
  if (!/^https?:\/\/\S+$/.test(url)) return false;
  e.preventDefault();
  const start = ta.selectionStart, end = ta.selectionEnd;
  let md;
  if (start !== end) {
    md = `[${ta.value.slice(start, end)}](${url})`;
  } else {
    const name = await askUrlName(url);
    md = name ? `[${name}](${url})` : url;
    ta.focus();                       // the prompt took focus and selection
    ta.setSelectionRange(start, end);
  }
  document.execCommand('insertText', false, md);
  return true;
}
window.pasteUrlAsLink = pasteUrlAsLink;

// Mirror of server-side to_slug(): lowercase, non [a-z0-9_] runs → '-',
// '/' preserved as a path separator. Keep the two in sync.
function toSlug(name) {
  return name.trim().split('/')
    .map(p => p.trim().toLowerCase().replace(/[^a-z0-9_]+/g, '-').replace(/^-+|-+$/g, ''))
    .filter(Boolean)
    .join('/');
}
window.toSlug = toSlug;

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
let modalCMSeq = 0;

closeOnBackdropClick(searchModal);

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

function renderModalList(pages) {
  modalPageList.innerHTML = pages.map(p =>
    `<li class="page-list-item${p.special ? ' page-list-special' : ''}" data-title="${he(p.title.toLowerCase())}" data-slug="${he(p.slug)}" data-links="${p.links}" data-mtime="${p.mtime}">` +
    `<a href="${he(p.href || `/page/${p.slug}`)}">${he(p.title)}</a>` +
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
// With an empty query the list is a quick switcher, so it opens on the most
// recently edited pages. A sort picked from the dropdown is remembered.
const MODAL_SORT_KEY = 'leaf-search-sort';
try {
  const saved = localStorage.getItem(MODAL_SORT_KEY);
  if (modalSort && saved && [...modalSort.options].some(o => o.value === saved)) modalSort.value = saved;
} catch (_) {}
modalSort?.addEventListener('change', () => {
  try { localStorage.setItem(MODAL_SORT_KEY, modalSort.value); } catch (_) {}
  applyModalControls();
});
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

// The words of a search query (search.Query in search.py): phrases lose their
// quotes; -word, -"phrase", #tag, -#tag and the date filters (edited:,
// after:, before:) are filters on content, which a title can't be checked
// against, so the title filter leaves them out.
function queryWords(raw) {
  return [...raw.matchAll(/(-?)(?:"([^"]+)"|(\S+))/g)]
    .filter(m => !m[1] && !(m[3] && m[3].startsWith('#') && m[3].length > 1)
                 && !(m[3] && /^(edited|after|before):\d{4}(-\d\d(-\d\d)?)?$/i.test(m[3])))
    .map(m => m[2] ?? m[3]).join(' ').trim();
}

// How well q matches a page in the title list: the better of its title and
// its file name (the slug's last part, scored on its own, so a file name
// starting with the query counts as a match at the start). A match that
// only reaches into folder names (".../2026-05-26_alex-1on1/prep") is
// weaker, so it scores FOLDER_PENALTY less. `field` says which was matched
// ('title' or 'slug'), `indices` where in it, for the highlighting.
const FOLDER_PENALTY = 6;

// fuzzyScore for several query words: each must match within one word of s,
// and all must match; the scores add up. Matching the whole input as one
// letter sequence let "alex again" find "A-dd a-l-e-rt e-x-porter t-a-g
// h-a-ndl-i-n-g" spread over a long title and nothing better.
function wordsScore(words, s) {
  const parts = [...s.matchAll(/[\p{L}\p{N}]+/gu)];
  let score = 0;
  const indices = new Set();
  for (const w of words) {
    let best = null;
    for (const part of parts) {
      const m = fuzzyScore(w, part[0]);
      if (m && (!best || m.score > best.score)) best = { score: m.score, at: part.index, indices: m.indices };
    }
    if (!best) return null;
    score += best.score;
    best.indices.forEach(i => indices.add(i + best.at));
  }
  return { score, indices: [...indices].sort((a, b) => a - b) };
}

function titleMatch(q, p) {
  const words = q.match(/[\p{L}\p{N}]+/gu) || [];
  const match = words.length > 1 ? (s => wordsScore(words, s)) : (s => fuzzyScore(q, s));
  const ts = match(p.title);
  const file = p.slug.slice(p.slug.lastIndexOf('/') + 1);
  const fs = match(file);
  if (ts && (!fs || ts.score >= fs.score)) return { ...ts, field: 'title' };
  const offset = p.slug.length - file.length;
  if (fs) return { score: fs.score, indices: fs.indices.map(i => i + offset), field: 'slug' };
  const ss = match(p.slug);
  return ss ? { ...ss, score: ss.score - FOLDER_PENALTY, field: 'slug' } : null;
}

// Equal fuzzy scores are common: every title starting with the query gets
// the same one. Then the page most likely meant comes first, not the
// alphabetical order: scratch notes last (temporary, often titled by their
// file name), then pages in fewer folders (a top-level page is the main page
// for a name), then the shorter name, which the query covers more of. So
// "alex" puts "Alex Morgan" before "Alex 1:1 Kickoff" (three folders
// down) and the scratch note "alex_prep".
const SCRATCH_SLUG_RE = /^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_/;  // server.py SCRATCH_RE

function tieBreak(p, field) {
  const name = p.slug.split('/').pop();
  return {
    scratch: SCRATCH_SLUG_RE.test(name) ? 1 : 0,
    depth: p.slug.split('/').length - 1,
    len: (field === 'title' ? p.title : p.slug).length,
  };
}

function applyModalControls() {
  if (!modalPages) return;
  const raw = modalFilter.value.trim();
  const q = queryWords(raw);
  const sort = modalSort.value;
  const unlinked = modalUnlinked.checked;

  if (raw) {
    // Fuzzy mode: score, rank by quality, render with highlights. A query
    // of only filters (#tag, -word) has no words for titles: no title hits.
    const scored = [];
    for (const p of (q ? modalPages : [])) {
      if (unlinked && p.links !== 0) continue;
      const best = titleMatch(q, p);
      if (!best) continue;
      scored.push({ p, ...best, ...tieBreak(p, best.field) });
    }
    scored.sort((a, b) => b.score - a.score || a.scratch - b.scratch
                          || a.depth - b.depth || a.len - b.len);
    modalPageList.innerHTML = scored.slice(0, 200).map(({ p, indices, field }) => {
      const titleHtml = field === 'title' ? highlightIndices(p.title, indices) : he(p.title);
      const slugHtml  = field === 'slug'  ? highlightIndices(p.slug,  indices) : he(p.slug);
      return `<li class="page-list-item" data-title="${he(p.title.toLowerCase())}" data-slug="${he(p.slug)}" data-links="${p.links}" data-mtime="${p.mtime}">` +
        `<a href="${he(p.href || `/page/${p.slug}`)}">${titleHtml}</a>` +
        `<span class="page-item-right"><span class="page-slug">${slugHtml}</span></span></li>`;
    }).join('');
  } else {
    // No query: static list, sort by dropdown, filter by unlinked checkbox.
    // Sort the data first and render once (no post-render DOM shuffling).
    // Deleted Pages (`special`) comes first, whatever the sort; the other
    // built-in pages (Deck, Scratch) are modules, in the module switcher, so
    // only typing lists them. "Unlinked only" looks for orphan pages, so it
    // leaves them all out.
    const special = unlinked ? [] : modalPages.filter(p => p.special);
    const sorted = modalPages
      .filter(p => !p.builtin && !(unlinked && p.links > 0))
      .sort((a, b) => {
        if (sort === 'title-desc') return b.title.toLowerCase().localeCompare(a.title.toLowerCase());
        if (sort === 'mtime-desc') return b.mtime - a.mtime;
        if (sort === 'links-desc') return b.links - a.links;
        return a.title.toLowerCase().localeCompare(b.title.toLowerCase());
      });
    renderModalList([...special, ...sorted.slice(0, 200)]);
  }

  setModalActive(raw ? 0 : -1);

  // Content matches get the raw query: the server applies the operators.
  clearTimeout(modalCMTimer);
  if (raw.length >= 2) {
    // Short: the server answers in a few tens of ms, so the wait is what you feel.
    modalCMTimer = setTimeout(() => fetchModalContentMatches(raw), 150);
  } else {
    // Drop a request still in flight, or its results would come back
    // under the now (nearly) empty filter.
    modalCMSeq++;
    modalCM.innerHTML = '';
    modalCM.classList.add('hidden');
  }
}

const MODAL_RESULTS = 15;  // server.py SEARCH_LIMIT

async function fetchModalContentMatches(q) {
  const seq = ++modalCMSeq;
  try {
    // "Unlinked only" applies to content matches too: the server filters
    // before it cuts the list, so the 15 results are all unlinked pages.
    const unlinked = modalUnlinked.checked ? '&unlinked=1' : '';
    // One more than shown: if it comes back, there are more for /search.
    const res = await fetch(`/api/search?q=${encodeURIComponent(q)}${unlinked}&limit=${MODAL_RESULTS + 1}`);
    if (seq !== modalCMSeq) return;  // stale response — discard
    const results = await res.json();
    if (!results.length) { modalCM.innerHTML = ''; modalCM.classList.add('hidden'); return; }
    const more = results.length > MODAL_RESULTS
      ? `<a class="search-more" href="/search?q=${encodeURIComponent(q)}${unlinked}">All results →</a>` : '';
    modalCM.innerHTML = '<p class="section-label">Content matches</p>' +
      results.slice(0, MODAL_RESULTS).map(r =>
        `<a class="search-result-card" href="${he(r.href)}"><span class="result-title">${he(r.title)}</span>` +
        `<span class="result-meta">${he(r.meta || '')}</span><p>${r.excerpt}</p></a>`
      ).join('') + more;
    modalCM.classList.remove('hidden');
    // Re-clamp active index now that content cards are in the pool
    if (modalActiveIdx >= 0) setModalActive(modalActiveIdx);
  } catch (_) {}
}

// ── Quick-task modal ──────────────────────────────────────────────────────────

function openQuickTask(defaultTag) {
  // Always use the full deck edit modal — navigate to the deck page (/deck redirects there) with new param
  if (typeof window.openDeckEdit === 'function') {
    window.openDeckEdit(null, { tags: defaultTag || '' });
  } else {
    const params = new URLSearchParams({ new: '1' });
    if (defaultTag) params.set('tag', defaultTag);
    window.location.href = '/deck?' + params.toString();
  }
}

// ── Scratch modal ───────────────────────────────────────────────────────────────

const scratchModal  = document.getElementById('scratch-modal');
const scratchForm   = document.getElementById('scratch-form');
const scratchTitle  = document.getElementById('scratch-title');
const scratchCancel = document.getElementById('scratch-cancel');

scratchCancel?.addEventListener('click', () => scratchModal.close());
closeOnBackdropClick(scratchModal);

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
closeOnBackdropClick(document.getElementById('page-delete-dialog'));

// ── New-page modal ─────────────────────────────────────────────────────────────

const newPageModal  = document.getElementById('new-page-modal');
const newPageInput  = document.getElementById('new-page-input');
const newPageCancel = document.getElementById('new-page-cancel');

newPageCancel?.addEventListener('click', () => newPageModal.close());
closeOnBackdropClick(newPageModal);

function openNewPage() {
  if (!newPageModal) return;
  newPageInput.value = '';
  newPageModal.showModal();
  newPageInput.focus();
}

// ── Action bar ───────────────────────────────────────────────────────────────
// Search is Ctrl+P. New creates the current module's kind of thing: a task
// in the deck (its own "+ New", so the view's tags and dates apply; on the
// deck's home page the home embed's), a
// scratch note in scratch (`c s`), else a page (Ctrl+N).

const deckAddButton = () =>
  document.querySelector('.tasks-app[data-home] [id^="task-add-btn"]')
  || document.querySelector('.tasks-app:not([data-headless]) [id^="task-add-btn"]');

const NEW_ACTIONS = {
  deck:    { label: 'New task (n)', run: () => deckAddButton()?.click() },
  scratch: { label: 'New scratch note (c s)', run: openScratch },
  wiki:    { label: 'New page (Ctrl+N)', run: openNewPage },
};

(function () {
  const btn = document.querySelector('.action-new');
  if (!btn) return;
  let action = NEW_ACTIONS[document.body.dataset.module] || NEW_ACTIONS.scratch;
  // A deck page without its deck app (it failed to load) still gets a note.
  if (action === NEW_ACTIONS.deck && !deckAddButton()) action = NEW_ACTIONS.scratch;
  btn.title = action.label;
  btn.setAttribute('aria-label', action.label.replace(/ \(.*\)$/, ''));
  btn.addEventListener('click', action.run);
}());
document.querySelector('.action-search')?.addEventListener('click', openSearchModal);

// ── Keyboard shortcuts ────────────────────────────────────────────────────────

const shortcutsModal = document.getElementById('shortcuts-modal');
closeOnBackdropClick(shortcutsModal);

let awaitingG = false;
let gPrefixTimer = null;
function resetG() { awaitingG = false; clearTimeout(gPrefixTimer); }

let awaitingC = false;
let cPrefixTimer = null;
function resetC() { awaitingC = false; clearTimeout(cPrefixTimer); }

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
  if (e.target.matches('input,textarea,select,[contenteditable]') || e.ctrlKey || e.metaKey || e.altKey) {
    resetG(); resetC();
    return;
  }

  // g-prefix navigation: gh=home, gs=scratch, gd=deck, gt=deck(task)
  if (awaitingG) {
    resetG();
    const dest = { h: '/page/index', s: '/page/_scratch', d: '/deck', t: '/deck?tag=task' };
    if (dest[e.key]) { e.preventDefault(); window.location.href = dest[e.key]; }
    return;
  }
  if (e.key === 'g') {
    e.preventDefault();
    awaitingG = true;
    gPrefixTimer = setTimeout(resetG, 1000);
    return;
  }

  // c-prefix create: cs=scratch note, ct=task-tagged deck item
  if (awaitingC) {
    resetC();
    if (e.key === 's') { e.preventDefault(); openScratch(); }
    else if (e.key === 't') { e.preventDefault(); openQuickTask('task'); }
    return;
  }
  if (e.key === 'c') {
    e.preventDefault();
    awaitingC = true;
    cPrefixTimer = setTimeout(resetC, 1000);
    return;
  }

  // E — go to edit
  if (e.key === 'e') {
    const editLink = document.querySelector('a[href^="/edit/"]');
    if (editLink) window.location.href = editLink.href;
  }

  // P — enter presentation mode
  if (e.key === 'p') {
    document.getElementById('present-btn')?.click();
  }

  // ? — show keyboard shortcuts
  if (e.key === '?') shortcutsModal?.showModal();
});

// ── Editor ────────────────────────────────────────────────────────────────────

const textarea = document.getElementById('editor-textarea');
const previewPane = document.getElementById('editor-preview');
let previewTimer = null;
let applyFormat = () => {};   // set up with the editor's formatting bar

if (textarea && previewPane) {
  // Auto-show preview on wide screens
  if (window.innerWidth >= 1000) togglePreview();

  // Pre-fetch all page slugs for wikilink autocomplete
  fetch('/api/pages').then(r => r.json()).then(p => { wikiPages = p; }).catch(() => {});

  textarea.addEventListener('keydown', e => {
    // Wikilink autocomplete keyboard nav
    if (acDropdown) {
      if (e.key === 'ArrowDown') { e.preventDefault(); acMove(1); return; }
      if (e.key === 'ArrowUp')   { e.preventDefault(); acMove(-1); return; }
      if (e.key === 'Enter' || e.key === 'Tab') {
        const sel = acDropdown.querySelector('.selected');
        if (sel) {
          e.preventDefault();
          acInsert(sel.dataset.value);
          return;
        }
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
        document.execCommand('insertText', false, `[[${selected}]]`);
        const p = textarea.selectionStart - 2; // before ]]
        textarea.setSelectionRange(p, p);
        clearTimeout(previewTimer);
        previewTimer = setTimeout(fetchPreview, 300);
        return;
      }
      // Cursor inside [[...]] → follow the link
      const wlText = wikilinkAtCursor();
      if (wlText !== null) {
        e.preventDefault();
        const pageName = wlText.split('|')[0].trim();
        const targetSlug = toSlug(pageName);
        formSubmitting = true;
        const data = new FormData(editorForm);
        fetch(editorForm.action, { method: 'POST', body: data, redirect: 'follow' })
          .finally(() => { window.location.href = `/page/${targetSlug}`; });
        return;
      }
      if (continueList(textarea, e)) {
        clearTimeout(previewTimer);
        previewTimer = setTimeout(fetchPreview, 300);
      }
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
  // Buttons above the textarea (FORMAT_BAR in server.py) and Ctrl+B/I/K. Every
  // change goes through execCommand('insertText'), so Ctrl+Z undoes it in one
  // step and the input event drives preview and auto-save as typing does.

  function replaceRange(start, end, text, selStart, selEnd) {
    textarea.focus();
    textarea.setSelectionRange(start, end);
    if (text) document.execCommand('insertText', false, text);
    else if (start !== end) document.execCommand('delete');
    textarea.setSelectionRange(start + selStart, start + selEnd);
    updateAcDropdown();   // e.g. `[[` or `[text](` just inserted: offer pages
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

  function localToday() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }

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
    if (!todo.length) { showCheckboxToast('Nothing to make a task from'); return; }
    const description = pageSlug ? `From [[${pageSlug}]]` : '';
    const ref = it => `[[task:${it.id.slice(0, 8)}]]`;
    const replaceWithRefs = created => {
      const text = inline ? ref(items[0])
        : items.map(it => (it.id ? it.prefix + ref(it) : it.line)).join('\n');
      const original = v.slice(start, end);
      replaceRange(start, end, text, text.length, text.length);
      const msg = created.length === 1 ? `Task “${todo[0].title}” created` : `${created.length} tasks created`;
      showCheckboxToast(msg, async () => {
        await fetch('/api/deck/batch-delete', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids: created }),
        });
        if (textarea.value.slice(start, start + text.length) === text) {
          replaceRange(start, start + text.length, original, original.length, original.length);
        }
      });
    };

    if (window.openDeckEdit) {
      // One item: the modal with its text as title. Several: the modal in
      // batch mode, its fields (tags, dates, …) for all of them.
      window.openDeckEdit(null, {
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
            showCheckboxToast(tasks.some(t => !t) ? 'Could not create the tasks'
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
    const today = localToday();
    const created = [];
    for (const it of todo) {
      const q = window.DeckCore?.parseQuickAdd(it.text, today)
        || { title: it.text, tags: [], start_date: null, due_date: null };
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
      showCheckboxToast(created.length < todo.length ? 'Could not create the task'
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
  if (textarea.dataset.new === '1') {
    // Brand-new page: land right after the pre-filled "# Title\n\n" so typing
    // continues the body. Existing pages keep the cursor at the start instead
    // of jumping (and scrolling) to the end of a possibly long file.
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

  async function autoSave() {
    if (!autoSaveUrl || sectionConflict) return;
    const data = new FormData();
    data.append('content', textarea.value);
    appendSaveFields(data);
    try {
      const res = await fetch(autoSaveUrl, { method: 'POST', body: data });
      if (res.ok) {
        lastSavedContent = textarea.value;
        saveDot.classList.remove('unsaved');
        if (res.headers.get('content-type')?.includes('json')) {
          const json = await res.json();
          if (sectionHashEl && json.file_hash) sectionHashEl.value = json.file_hash;
          if (isSectionEdit && json.start_char !== undefined) {
            sectionStartEl.value = json.start_char;
            sectionEndEl.value = json.end_char;
          }
        }
      } else if (res.status === 409) {
        showConflictBanner();
      }
    } catch { /* ignore */ }
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
        const res = await fetch(autoSaveUrl, { method: 'POST', body: data });
        if (res.ok) {
          lastSavedContent = textarea.value;
          saveDot.classList.remove('unsaved');
          banner.remove();
          sectionConflict = false;
          if (res.headers.get('content-type')?.includes('json')) {
            const json = await res.json();
            if (sectionHashEl && json.file_hash) sectionHashEl.value = json.file_hash;
            if (isSectionEdit && json.start_char !== undefined) {
              sectionStartEl.value = json.start_char;
              sectionEndEl.value = json.end_char;
            }
          }
          if (fromDone) {
            editorForm.submit();
          }
        }
      } catch { /* ignore */ }
    });
  }

  // Flag form submission so pagehide beacon doesn't race with the POST
  editorForm?.addEventListener('submit', (e) => {
    if (sectionHashEl?.value) {
      // Submit via fetch so we can handle conflicts in-place
      e.preventDefault();
      formSubmitting = true;
      const data = new FormData(editorForm);
      fetch(editorForm.action, { method: 'POST', body: data, redirect: 'follow' })
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
    const data = new FormData();
    data.append('content', textarea.value);
    appendSaveFields(data);
    navigator.sendBeacon(autoSaveUrl, data);
  });
}

function togglePreview() {
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

// ── List continuation ─────────────────────────────────────────────────────────

// Enter at the end of a list item starts the next one (a checkbox item gets
// an unchecked box, a number counts up); Enter on an empty item ends the
// list. Returns true when it handled the key. Shared with the deck's task
// notes (deck.js), so it takes the textarea.
function continueList(ta, e) {
  const pos = ta.selectionStart;
  if (pos !== ta.selectionEnd) return false;
  const val = ta.value;
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
    ta.setSelectionRange(lineStart, pos);
    document.execCommand('insertText', false, '\n');
  } else if (cb) {
    e.preventDefault();
    document.execCommand('insertText', false, '\n' + cb[1] + cb[2] + ' [ ] ');
  } else if (ocb) {
    e.preventDefault();
    document.execCommand('insertText', false, '\n' + ocb[1] + (parseInt(ocb[2]) + 1) + '. [ ] ');
  } else if (ulEmpty || olEmpty) {
    // Empty list item → exit list
    e.preventDefault();
    ta.setSelectionRange(lineStart, pos);
    document.execCommand('insertText', false, '\n');
  } else if (ul) {
    e.preventDefault();
    document.execCommand('insertText', false, '\n' + ul[1] + ul[2] + ' ');
  } else if (ol) {
    e.preventDefault();
    document.execCommand('insertText', false, '\n' + ol[1] + (parseInt(ol[2]) + 1) + '. ');
  } else {
    return false;
  }
  return true;
}
window.continueList = continueList;

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
let acMode = 'wikilink'; // 'wikilink' | 'mdlink' | 'task'

function getWikilinkQuery() {
  const before = textarea.value.slice(0, textarea.selectionStart);
  const m = before.match(/\[\[([^\][\n]*)$/);
  return m ? m[1] : null;
}

function getMdLinkQuery() {
  const before = textarea.value.slice(0, textarea.selectionStart);
  // Match [any desc]( followed by typed chars that look like a page path (no space, no closing paren)
  const m = before.match(/\[[^\]]*\]\(([^()\n]*)$/);
  if (!m) return null;
  const typed = m[1];
  // Only trigger when it looks like an internal link (empty or starts with / or a slug char)
  if (typed === '' || /^[/a-z0-9_-]/i.test(typed)) return typed;
  return null;
}

// Open tasks for `[[task:` completion, fetched when first needed and again
// when older than a minute (tasks change in other tabs).
let acTasks = null, acTasksAt = 0, acTasksLoading = false;

function taskAcMatches(query) {
  if (!acTasks || Date.now() - acTasksAt > 60000) {
    if (!acTasksLoading) {
      acTasksLoading = true;
      fetch('/api/deck').then(r => r.json()).then(tasks => {
        acTasks = tasks; acTasksAt = Date.now();
      }).catch(() => {}).finally(() => { acTasksLoading = false; updateAcDropdown(); });
    }
    if (!acTasks) return [];
  }
  const q = query.trim().toLowerCase();
  const words = q.split(/\s+/).filter(Boolean);
  return acTasks
    .filter(t => t.id.startsWith(q) || words.every(w => t.title.toLowerCase().includes(w)))
    .sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''))
    .slice(0, 8)
    .map(t => ({
      value: t.id.slice(0, 8),
      label: t.title,
      meta: [t.list, t.due_date && `⏰ ${t.due_date}`].filter(Boolean).join(' · '),
    }));
}

function updateAcDropdown() {
  const wikiQuery = getWikilinkQuery();
  if (wikiQuery !== null && /^task:/i.test(wikiQuery)) {
    acMode = 'task';
    const matches = taskAcMatches(wikiQuery.slice(5));
    if (!matches.length) { hideAcDropdown(); return; }
    showAcDropdown(matches);
    return;
  }
  if (wikiQuery !== null) {
    acMode = 'wikilink';
    const q = wikiQuery.toLowerCase();
    const matches = wikiPages.filter(p => p.includes(q)).slice(0, 8);
    if (!matches.length) { hideAcDropdown(); return; }
    showAcDropdown(matches);
    return;
  }
  const mdQuery = getMdLinkQuery();
  if (mdQuery !== null) {
    acMode = 'mdlink';
    const q = mdQuery.replace(/^\/page\//, '').toLowerCase();
    const matches = wikiPages.filter(p => p.includes(q)).slice(0, 8);
    if (!matches.length) { hideAcDropdown(); return; }
    showAcDropdown(matches);
    return;
  }
  hideAcDropdown();
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
window.getCaretCoords = getCaretCoords;

function showAcDropdown(matches) {
  if (!acDropdown) {
    acDropdown = document.createElement('div');
    acDropdown.className = 'wikilink-ac';
    document.body.appendChild(acDropdown);
    acIndex = 0;
  }

  // An item is a page slug, or { value, label, meta } (tasks).
  acDropdown.innerHTML = '';
  matches.forEach((m, i) => {
    const item = document.createElement('div');
    item.className = 'wikilink-ac-item' + (i === acIndex ? ' selected' : '');
    const value = typeof m === 'string' ? m : m.value;
    item.dataset.value = value;
    item.textContent = typeof m === 'string' ? m : m.label;
    if (m.meta) {
      const meta = document.createElement('span');
      meta.className = 'wikilink-ac-meta';
      meta.textContent = m.meta;
      item.append(meta);
    }
    item.addEventListener('mousedown', ev => {
      ev.preventDefault();
      acInsert(value);
    });
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
  left = Math.max(4, Math.min(left, window.innerWidth - acDropdown.offsetWidth - 8));
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

function acInsert(value) {
  if (acMode === 'mdlink') insertMdLink(value);
  else if (acMode === 'task') insertTaskRef(value);
  else insertWikilink(value);
}

function insertTaskRef(id) {
  const pos = textarea.selectionStart;
  const bracketPos = textarea.value.lastIndexOf('[[', pos);
  textarea.setSelectionRange(bracketPos, pos);
  document.execCommand('insertText', false, `[[task:${id}]]`);
  hideAcDropdown();
}

function insertWikilink(page) {
  const pos = textarea.selectionStart;
  const val = textarea.value;
  const bracketPos = val.lastIndexOf('[[', pos);
  textarea.setSelectionRange(bracketPos, pos);
  document.execCommand('insertText', false, '[[' + page + ']]');
  hideAcDropdown();
  clearTimeout(previewTimer);
  previewTimer = setTimeout(fetchPreview, 300);
}

function insertMdLink(page) {
  const pos = textarea.selectionStart;
  const val = textarea.value;
  // Find the opening '(' of the markdown link before the cursor
  const parenPos = val.lastIndexOf('(', pos - 1);
  textarea.setSelectionRange(parenPos + 1, pos);
  document.execCommand('insertText', false, '/page/' + page);
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

// ── Scratch filter ────────────────────────────────────────────────────────────
// The Scratch page's filter box runs the search (/api/search?scratch=1: the
// same ranking, query syntax, excerpts and on-page highlights) over scratch
// notes only. Matches show in ranked order with their excerpt, linking to
// the section that matched; an empty box restores the newest-first list.
// The query lives in ?q=, so Back returns to the filtered list. Enter opens
// the first match, Esc clears the box (a second Esc leaves it).

(function initScratchFilter() {
  const input = document.getElementById('scratch-filter');
  const list = document.querySelector('.notes-list');
  if (!input || !list) return;
  const countEl = document.getElementById('scratch-filter-count');
  const items = [...list.children];
  const bySlug = new Map(items.map(li => [li.dataset.slug, li]));
  const empty = document.createElement('p');
  empty.className = 'scratch-filter-empty';
  empty.textContent = 'No scratch note matches.';
  empty.hidden = true;
  list.after(empty);
  let seq = 0, timer = null;

  function reset(li) {
    li.hidden = false;
    li.querySelector('a').href = `/page/${li.dataset.slug}`;
    li.querySelector('.note-excerpt')?.remove();
  }

  function remember(q) {
    const url = new URL(location.href);
    if (q) url.searchParams.set('q', q); else url.searchParams.delete('q');
    history.replaceState(history.state, '', url);
  }

  async function apply() {
    const q = input.value.trim();
    const mine = ++seq;
    remember(q);
    if (!q) {
      items.forEach(li => { reset(li); list.appendChild(li); });
      empty.hidden = true;
      countEl.textContent = '';
      return;
    }
    let results;
    try {
      const res = await fetch(`/api/search?scratch=1&limit=1000&q=${encodeURIComponent(q)}`);
      results = await res.json();
    } catch (_) { return; }
    if (mine !== seq) return;   // a newer query is on its way
    items.forEach(li => { reset(li); li.hidden = true; });
    let shown = 0;
    for (const r of results) {
      const li = bySlug.get(r.slug);
      if (!li) continue;
      li.hidden = false;
      li.querySelector('a').href = r.href;
      if (r.excerpt) {
        const ex = document.createElement('div');
        ex.className = 'note-excerpt';
        ex.innerHTML = r.excerpt;   // escaped by the server, <mark> around matches
        li.appendChild(ex);
      }
      list.appendChild(li);   // ranked order
      shown++;
    }
    empty.hidden = shown > 0;
    countEl.textContent = shown ? `${shown} of ${items.length}` : '';
  }

  input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(apply, 150); });
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') {
      const first = [...list.children].find(li => !li.hidden);
      if (first) { e.preventDefault(); location.href = first.querySelector('a').href; }
    } else if (e.key === 'Escape') {
      e.preventDefault();
      if (input.value) { input.value = ''; apply(); } else input.blur();
    }
  });
  if (input.value.trim()) apply();
}());

// ── Search highlights ─────────────────────────────────────────────────────────
// A page opened from a search result carries the words that matched as
// ?hl=… (search.matched_words: as written in the page, stems, typos and
// phrases already resolved). They are marked in the article, a bar shows
// "word · 2 of 5" with ↑ ↓ ×, n/N step through them and Esc clears them.
// Code blocks (highlight.js rewrites them) and deck embeds are left alone.

function initSearchHighlights() {
  const article = document.querySelector('article.page-content');
  const params = new URLSearchParams(location.search);
  const terms = params.getAll('hl').map(t => t.trim()).filter(Boolean);
  if (!article || !terms.length) return;

  const escape = t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s+');
  const alternatives = [...terms].sort((a, b) => b.length - a.length).map(escape).join('|');
  const re = new RegExp(`(?<![\\p{L}\\p{N}])(?:${alternatives})(?![\\p{L}\\p{N}])`, 'giu');
  const walker = document.createTreeWalker(article, NodeFilter.SHOW_TEXT, {
    acceptNode: n => n.parentElement.closest('pre, script, style, .tasks-app')
      ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  const marks = [];
  for (const node of nodes) {
    const text = node.nodeValue;
    const found = [...text.matchAll(re)];
    if (!found.length) continue;
    const frag = document.createDocumentFragment();
    let at = 0;
    for (const m of found) {
      frag.append(text.slice(at, m.index));
      const mark = document.createElement('mark');
      mark.className = 'search-hl';
      mark.textContent = m[0];
      frag.append(mark);
      marks.push(mark);
      at = m.index + m[0].length;
    }
    frag.append(text.slice(at));
    node.replaceWith(frag);
  }
  if (!marks.length) return;

  const bar = document.createElement('div');
  bar.className = 'search-hl-bar';
  bar.setAttribute('role', 'status');
  bar.innerHTML =
    `<span class="search-hl-terms"></span><span class="search-hl-count"></span>` +
    `<button type="button" data-step="-1" title="Previous (N)" aria-label="Previous match">↑</button>` +
    `<button type="button" data-step="1" title="Next (n)" aria-label="Next match">↓</button>` +
    `<button type="button" data-clear title="Clear highlights (Esc)" aria-label="Clear highlights">×</button>`;
  bar.querySelector('.search-hl-terms').textContent = terms.map(t => `“${t}”`).join(' ');
  document.body.append(bar);

  let current = -1;
  const show = (i, scroll = true) => {
    marks[current]?.classList.remove('current');
    current = (i + marks.length) % marks.length;
    marks[current].classList.add('current');
    bar.querySelector('.search-hl-count').textContent = `${current + 1} of ${marks.length}`;
    if (scroll) marks[current].scrollIntoView({ block: 'center' });
  };
  const clear = () => {
    for (const mark of marks) mark.replaceWith(mark.textContent);
    article.normalize();
    bar.remove();
    window.removeEventListener('keydown', onKey, true);
    params.delete('hl');
    const qs = params.toString();
    history.replaceState(history.state, '', location.pathname + (qs ? `?${qs}` : '') + location.hash);
  };
  // In the capture phase, so n doesn't also reach a deck embed's "new task".
  const onKey = e => {
    if (e.ctrlKey || e.metaKey || e.altKey || document.querySelector('dialog[open], .presentation-overlay')) return;
    if (e.target.closest?.('input, textarea, select, [contenteditable]')) return;
    if (e.key === 'n' || e.key === 'N') {
      e.preventDefault();
      e.stopImmediatePropagation();
      show(current + (e.key === 'n' ? 1 : -1));
    } else if (e.key === 'Escape') {
      clear();
    }
  };
  window.addEventListener('keydown', onKey, true);
  bar.addEventListener('click', e => {
    const btn = e.target.closest('button');
    if (!btn) return;
    if (btn.hasAttribute('data-clear')) clear();
    else show(current + Number(btn.dataset.step));
  });

  // Start at the first match in the section the link opened (the browser has
  // already scrolled there), else at the first match, scrolled into view.
  const target = location.hash && document.getElementById(decodeURIComponent(location.hash.slice(1)));
  const inSection = target
    ? marks.findIndex(m => target.compareDocumentPosition(m) & Node.DOCUMENT_POSITION_FOLLOWING) : -1;
  show(inSection >= 0 ? inSection : 0, inSection < 0);
}

document.addEventListener('DOMContentLoaded', initSearchHighlights);

// ── Syntax highlighting ───────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  if (!window.hljs) return;
  document.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
});

// ── Task list checkboxes ───────────────────────────────────────────────────────

// Character offsets of the mark inside each task-list checkbox ("[ ]"/"[x]"),
// in rendered order. Mirrors server.py's _task_item_lines/_TASK_ITEM_RE: only
// list items whose text starts with the box produce a checkbox, fenced code
// and a leading frontmatter block are skipped — so the Nth rendered checkbox
// is the Nth offset here.
const TASK_ITEM_RE = /^([ \t]*(?:>[ \t]?)*[ \t]*(?:[-*+]|\d{1,9}[.)])[ \t]+\[)([ xX])\](?=[ \t]+\S)/;

function taskCheckboxOffsets(text) {
  const lines = text.split('\n');
  let start = 0;
  if (lines.length && lines[0].trim() === '---') {
    const close = lines.findIndex((l, i) => i > 0 && l.trim() === '---');
    if (close !== -1) start = close + 1;
  }
  const offsets = [];
  let pos = 0, fence = null;
  lines.forEach((line, i) => {
    const t = line.trim();
    const run = t.length - t.replace(/^(`+|~+)/, '').length;  // leading fence chars
    if (fence) {
      // Closing fence: same char, at least as long, nothing else on the line
      if (t[0] === fence[0] && run >= fence.length && run === t.length) fence = null;
    } else if (run >= 3) {
      fence = t.slice(0, run);
    } else if (i >= start) {
      const m = TASK_ITEM_RE.exec(line);
      if (m) offsets.push(pos + m[1].length);
    }
    pos += line.length + 1;
  });
  return offsets;
}

document.addEventListener('change', e => {
  if (!e.target.matches('.task-list-item-checkbox')) return;
  const checkboxes = Array.from(document.querySelectorAll('.task-list-item-checkbox'));
  const idx = checkboxes.indexOf(e.target);
  if (idx === -1) return;

  if (textarea) {
    // Editor: toggle the Nth checkbox in the textarea. Goes through
    // execCommand so it lands on the undo stack and fires `input` (live
    // preview, unsaved dot, auto-save) like any other edit.
    const offsets = taskCheckboxOffsets(textarea.value);
    if (idx < offsets.length) {
      const at = offsets[idx];
      const newChar = textarea.value[at] === ' ' ? 'x' : ' ';
      const { selectionStart, selectionEnd, scrollTop } = textarea;
      textarea.focus({ preventScroll: true });
      textarea.setSelectionRange(at, at + 1);
      document.execCommand('insertText', false, newChar);
      textarea.setSelectionRange(selectionStart, selectionEnd);
      textarea.scrollTop = scrollTop;
    }
  } else {
    // Page view: persist the toggle, with an undo toast — a mis-tap on a
    // phone otherwise edits the file silently. Undo is a second toggle.
    const cb = e.target;
    const post = () => toggleCheckboxOnServer(idx).then(ok => {
      if (!ok) { cb.checked = !cb.checked; showCheckboxToast('Could not save the checkbox'); }
    });
    post();
    const text = (cb.closest('li')?.textContent || '').trim().split('\n')[0];
    const label = text.length > 40 ? text.slice(0, 39) + '…' : text;
    showCheckboxToast(`${cb.checked ? 'Checked' : 'Unchecked'} “${label}”`, () => {
      cb.checked = !cb.checked;
      post();
    });
  }
});

// Toggles are chained, so an undo can never overtake the toggle it reverts
// (the server flips whatever state the file has when the request arrives).
let checkboxQueue = Promise.resolve();
function toggleCheckboxOnServer(idx) {
  const slug = window.location.pathname.replace(/^\/page\//, '');
  const data = new FormData();
  data.append('idx', idx);
  checkboxQueue = checkboxQueue
    .then(() => fetch(`/api/toggle-checkbox/${slug}`, { method: 'POST', body: data }))
    .then(r => r.ok, () => false);
  return checkboxQueue;
}

let checkboxToast = null;
function showCheckboxToast(msg, undo) {
  checkboxToast?.remove();
  const el = document.createElement('div');
  el.className = 'raw-toast task-undo-toast';
  const span = document.createElement('span');
  span.textContent = msg;
  el.appendChild(span);
  if (undo) {
    const btn = document.createElement('button');
    btn.className = 'btn task-undo-btn';
    btn.textContent = 'Undo';
    btn.addEventListener('click', () => { el.remove(); undo(); });
    el.appendChild(btn);
  }
  document.body.appendChild(el);
  checkboxToast = el;
  setTimeout(() => el.remove(), 6000);
}

// ── Task references ([[task:…]] chips) ───────────────────────────────────────

// A chip's checkbox completes (or reopens) the task in the deck, through the
// same API as the deck, so repeating and keep tasks behave as there. The chip
// is then re-rendered by the server (/api/preview of the reference): a
// repeating task shows its next occurrence, a keep task its "✓ today".
async function rerenderTaskRef(chip, id) {
  // The lists the page declares (its list embeds), so the fresh chip links to
  // the task in the same list as the server-rendered one (_link_list).
  const lists = [...document.querySelectorAll('.tasks-app[data-init-list]')]
    .map(el => el.dataset.initList).filter(Boolean);
  const query = lists.length ? `?lists=${encodeURIComponent(lists.join(','))}` : '';
  try {
    const res = await fetch(`/api/preview${query}`, {
      method: 'POST', headers: { 'Content-Type': 'text/plain' }, body: `[[task:${id}]]`,
    });
    const fresh = new DOMParser().parseFromString(await res.text(), 'text/html').querySelector('.task-ref');
    if (fresh) { chip.replaceWith(fresh); return fresh; }
  } catch (_) { /* keep the old chip */ }
  return chip;
}

function putTask(id, body) {
  return fetch(`/api/deck/${encodeURIComponent(id)}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
}

document.addEventListener('change', async e => {
  const cb = e.target;
  if (!cb.matches('.task-ref-check')) return;
  let chip = cb.closest('.task-ref');
  const id = chip.dataset.taskId;
  const done = cb.checked;
  // The open task as it is now, for undo: completing a keep task changes
  // its dates instead of archiving it, and undo must restore them.
  let before = null;
  if (done) {
    try { before = (await (await fetch('/api/deck')).json()).find(t => t.id === id) || null; } catch (_) { /* no undo data */ }
  }
  const res = await putTask(id, { completed: done }).catch(() => null);
  if (!res?.ok) {
    cb.checked = !done;
    showCheckboxToast('Could not update the task');
    return;
  }
  const title = chip.querySelector('.task-ref-title')?.textContent || '';
  const label = title.length > 40 ? title.slice(0, 39) + '…' : title;
  chip = await rerenderTaskRef(chip, id);
  showCheckboxToast(`${done ? 'Done' : 'Reopened'} “${label}”`, async () => {
    const undo = before?.keep
      ? { start_date: before.start_date, due_date: before.due_date, last_done: before.last_done,
          history: before.history }
      : { completed: !done, ...(before ? { history: before.history } : {}) };
    const r = await putTask(id, undo).catch(() => null);
    if (r?.ok) chip = await rerenderTaskRef(chip, id);
    else showCheckboxToast('Could not undo');
  });
});

// ── Sortable tables ───────────────────────────────────────────────────────────

// Clicking a column header of a table in a page's article sorts its rows:
// ascending, descending, then back to the file's order. View only — the file
// is never touched. Tables with merged cells (raw HTML) are left alone. Click
// handling is delegated, so cloned presentation slides sort too.
const tableCollator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

function tableCellNumber(text) {
  const t = text.replace(/[\s%€$£]/g, '');
  if (!/^[-+]?(\d+|\d{1,3}(,\d{3})+)(\.\d+)?$/.test(t)) return null;
  return parseFloat(t.replace(/,/g, ''));
}

function sortTableBy(th) {
  const table = th.closest('table');
  const tbody = table.tBodies[0];
  const col = th.cellIndex;
  const next = { none: 'ascending', ascending: 'descending', descending: 'none' };
  const dir = next[th.getAttribute('aria-sort') || 'none'];
  table.querySelectorAll('thead th').forEach(h => h.setAttribute('aria-sort', 'none'));
  th.setAttribute('aria-sort', dir);

  const rows = Array.from(tbody.rows);
  const text = r => (r.cells[col]?.textContent || '').trim();
  // A column sorts as numbers only when every non-empty cell is one
  const numeric = rows.every(r => !text(r) || tableCellNumber(text(r)) !== null);
  const sign = dir === 'descending' ? -1 : 1;
  rows.sort((a, b) => {
    if (dir === 'none') return a.dataset.sortIdx - b.dataset.sortIdx;
    const ta = text(a), tb = text(b);
    if (!ta || !tb) return (!ta) - (!tb);  // empty cells last either way
    const c = numeric ? tableCellNumber(ta) - tableCellNumber(tb) : tableCollator.compare(ta, tb);
    return sign * c || a.dataset.sortIdx - b.dataset.sortIdx;
  });
  rows.forEach(r => tbody.appendChild(r));
}

document.querySelectorAll('article.page-content table').forEach(table => {
  if (!table.tHead || table.tBodies.length !== 1 || table.tBodies[0].rows.length < 2) return;
  if (table.querySelector('[colspan], [rowspan]')) return;
  table.classList.add('sortable-table');
  Array.from(table.tBodies[0].rows).forEach((r, i) => { r.dataset.sortIdx = i; });
  table.querySelectorAll('thead th').forEach(th => {
    th.tabIndex = 0;
    th.setAttribute('aria-sort', 'none');
  });
});

document.addEventListener('click', e => {
  const th = e.target.closest('table.sortable-table thead th');
  if (!th || e.target.closest('a') || getSelection().toString()) return;
  sortTableBy(th);
});
document.addEventListener('keydown', e => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const th = e.target.closest?.('table.sortable-table thead th');
  if (!th) return;
  e.preventDefault();
  sortTableBy(th);
});

// ── Presentation mode ─────────────────────────────────────────────────────────

(function () {
  const presentBtn = document.getElementById('present-btn');
  if (!presentBtn) return;

  let overlay = null;
  let slides = [];
  let currentSlide = 0;

  // Each h1, h2, h3 starts a new slide. Content following a heading belongs to it.
  // The page H1 (from the page header) always opens the first slide.
  function buildSlides() {
    const article = document.querySelector('.page-content');
    if (!article) return [];

    const groups = [];

    // Open first slide with the page H1 — intro slide has no specific section
    let current = document.createElement('div');
    current.className = 'slide-body page-content';
    current.dataset.sectionN = '';
    const pageH1 = document.querySelector('.page-header h1');
    if (pageH1) {
      const h = document.createElement('h1');
      h.textContent = pageH1.textContent;
      current.appendChild(h);
    }

    for (const node of article.children) {
      const isH2 = node.matches('div.section-header') || node.tagName === 'H2';
      if (isH2 || node.tagName === 'H3') {
        if (current.children.length) groups.push(current);
        current = document.createElement('div');
        current.className = 'slide-body page-content';
        // Extract section number from the embedded section-edit-btn link
        const editLink = node.querySelector('.section-edit-btn');
        const m = editLink && editLink.getAttribute('href').match(/[?&]section=(\d+)/);
        current.dataset.sectionN = m ? m[1] : '';
        current.appendChild(node.cloneNode(true));
      } else {
        current.appendChild(node.cloneNode(true));
      }
    }
    if (current.children.length) groups.push(current);

    return groups;
  }

  function showSlide(n) {
    currentSlide = n;
    const body = overlay.querySelector('.slide-body-wrap');
    body.innerHTML = '';
    body.appendChild(slides[n].cloneNode(true));
    // Clones don't inherit event listeners - wire lightbox directly
    if (window._leafImgLightboxOpen) {
      body.querySelectorAll('img').forEach(img => {
        img.addEventListener('click', () => window._leafImgLightboxOpen(img.src));
      });
    }
    overlay.querySelector('.slide-counter').textContent = `${n + 1} / ${slides.length}`;
    overlay.querySelector('.slide-progress-fill').style.width =
      `${((n + 1) / slides.length) * 100}%`;
    // Re-run syntax highlighting on code blocks inside the new slide
    body.querySelectorAll('pre code').forEach(block => {
      if (window.hljs) window.hljs.highlightElement(block);
    });
    body.scrollTop = 0;

    // Update the edit button href for this slide's section
    const slug = window.location.pathname.replace(/^\/page\//, '');
    const sectionN = slides[n].dataset.sectionN;
    const editHref = sectionN ? `/edit/${slug}?section=${sectionN}` : `/edit/${slug}`;
    const presEditBtn = overlay.querySelector('.pres-edit-btn');
    if (presEditBtn) presEditBtn.href = editHref;

    // Persist slide position so F5/session-restore reopens the presentation
    localStorage.setItem('leaf_pres', JSON.stringify({ slug, slide: n }));
  }

  function openPresentation(startAt) {
    slides = buildSlides();
    if (!slides.length) return;

    overlay = document.createElement('div');
    overlay.className = 'presentation-overlay';
    overlay.innerHTML = `
      <div class="presentation-slide">
        <div class="slide-body-wrap"></div>
      </div>
      <div class="presentation-controls">
        <span class="slide-counter"></span>
        <div class="slide-progress"><div class="slide-progress-fill"></div></div>
      </div>
      <a class="pres-edit-btn" title="Edit section" href="#">✎</a>`;

    overlay.querySelector('.pres-edit-btn').addEventListener('click', () => {
      sessionStorage.setItem('leaf_pres_slide', String(currentSlide));
    });

    // Block wheel events from reaching the page behind; navigate slides on scroll
    let _wheelCooldown = false;
    overlay.addEventListener('wheel', e => {
      e.preventDefault();
      if (_wheelCooldown) return;
      _wheelCooldown = true;
      setTimeout(() => { _wheelCooldown = false; }, 400);
      if (e.deltaY > 0 || e.deltaX > 0) {
        if (currentSlide < slides.length - 1) showSlide(currentSlide + 1);
      } else {
        if (currentSlide > 0) showSlide(currentSlide - 1);
      }
    }, { passive: false });

    document.body.appendChild(overlay);

    const resumeAt = (typeof startAt === 'number')
      ? Math.min(startAt, slides.length - 1)
      : Math.min(currentSlide, slides.length - 1);
    showSlide(resumeAt);
    document.addEventListener('keydown', onPresentKey);
  }

  function closePresentation() {
    if (overlay) { overlay.remove(); overlay = null; }
    document.removeEventListener('keydown', onPresentKey);
    sessionStorage.removeItem('leaf_pres_slide');
    localStorage.removeItem('leaf_pres');
  }

  function onPresentKey(e) {
    // Suppress other keybindings during presentation
    if (e.ctrlKey || e.metaKey || e.altKey) { e.stopPropagation(); return; }
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown' || e.key === 'PageDown') {
      e.preventDefault();
      if (currentSlide < slides.length - 1) showSlide(currentSlide + 1);
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp' || e.key === 'PageUp') {
      e.preventDefault();
      if (currentSlide > 0) showSlide(currentSlide - 1);
    } else if (e.key === 'Escape') {
      closePresentation();
    } else if (e.key === 'e') {
      e.preventDefault();
      const editBtn = overlay && overlay.querySelector('.pres-edit-btn');
      if (editBtn) editBtn.click();
    }
  }

  // Show the button only when the page has headings — hidden by default in CSS.
  function syncPresentBtn() {
    const has = document.querySelector('.page-content h1, .page-content h2, .page-content h3');
    presentBtn.style.display = has ? 'inline-block' : 'none';
  }
  requestAnimationFrame(syncPresentBtn);
  window.addEventListener('pageshow', syncPresentBtn);

  presentBtn.addEventListener('click', openPresentation);

  // Auto-reopen presentation after F5 or after returning from section edit
  const _currentSlug = window.location.pathname.replace(/^\/page\//, '');
  const _presState = localStorage.getItem('leaf_pres');
  const _returnSlide = sessionStorage.getItem('leaf_pres_slide');

  if (_presState) {
    try {
      const { slug, slide } = JSON.parse(_presState);
      if (slug === _currentSlug) {
        requestAnimationFrame(() => openPresentation(slide));
      } else {
        localStorage.removeItem('leaf_pres');
      }
    } catch (_) { localStorage.removeItem('leaf_pres'); }
  } else if (_returnSlide !== null) {
    sessionStorage.removeItem('leaf_pres_slide');
    requestAnimationFrame(() => openPresentation(parseInt(_returnSlide, 10)));
  }
}());

// ── Raw page paste handler ────────────────────────────────────────────────────

(function () {
  if (window.location.pathname !== '/page/_raw') return;

  const article = document.querySelector('.page-content');
  if (article) {
    const hint = document.createElement('div');
    hint.className = 'raw-hint';
    hint.textContent = 'Paste a URL or text to add it as a list item.';
    article.appendChild(hint);
  }

  // Build title prompt modal
  const modal = document.createElement('dialog');
  modal.className = 'scratch-modal';
  modal.innerHTML =
    '<p class="scratch-label" id="raw-modal-label">Title</p>' +
    '<input type="text" id="raw-title-input" autocomplete="off" spellcheck="false">' +
    '<div class="scratch-actions">' +
    '<button type="button" class="btn btn-primary" id="raw-modal-confirm">Add</button>' +
    '<button type="button" class="btn" id="raw-modal-cancel">Cancel</button>' +
    '</div>';
  document.body.appendChild(modal);
  closeOnBackdropClick(modal);

  const titleInput  = document.getElementById('raw-title-input');
  const confirmBtn  = document.getElementById('raw-modal-confirm');
  const cancelBtn   = document.getElementById('raw-modal-cancel');

  function askTitle(placeholder) {
    return new Promise(resolve => {
      titleInput.value = '';
      titleInput.placeholder = placeholder;
      modal.showModal();
      titleInput.focus();

      function done(value) {
        confirmBtn.onclick = null;
        cancelBtn.onclick  = null;
        titleInput.onkeydown = null;
        modal.close();
        resolve(value);
      }

      confirmBtn.onclick   = () => done(titleInput.value.trim());
      cancelBtn.onclick    = () => done(null);
      titleInput.onkeydown = (e) => {
        if (e.key === 'Enter')  { e.preventDefault(); done(titleInput.value.trim()); }
        if (e.key === 'Escape') { e.preventDefault(); done(null); }
      };
      modal.addEventListener('close', () => resolve(null), { once: true });
    });
  }

  function showRawToast(msgHtml, isError) {
    const t = document.createElement('div');
    t.className = 'raw-toast' + (isError ? ' raw-toast-error' : '');
    t.innerHTML = msgHtml;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 6000);
  }

  document.addEventListener('paste', async (e) => {
    if (e.target.matches('input,textarea,select,[contenteditable]')) return;
    const text = (e.clipboardData.getData('text/plain') || '').trim();
    if (!text) return;
    e.preventDefault();

    const isUrl = /^https?:\/\/\S+$/.test(text);
    const title = await askTitle(isUrl ? 'Link title…' : 'Snippet title…');
    if (title === null) return;

    const data = new FormData();
    data.append('title', title);

    try {
      if (isUrl) {
        data.append('url', text);
        await fetch('/api/raw/append-url', { method: 'POST', body: data });
      } else {
        data.append('text', text);
        await fetch('/api/raw/create-page', { method: 'POST', body: data });
      }
      window.location.reload();
    } catch (_) {
      showRawToast('Error saving — please try again.', true);
    }
  });
}());

// ── Commit file actions ───────────────────────────────────────────────────────
// Handles .commit-file-form submit and #commit-all-btn click.
// Forms use data-filepath, data-title, and data-on-success attributes.
// data-on-success: "reload" (default) or a URL path to redirect to.

document.addEventListener('submit', async (e) => {
  const form = e.target.closest('.commit-file-form');
  if (!form) return;
  e.preventDefault();
  const filepath = form.dataset.filepath;
  const title    = form.dataset.title;
  const onSuccess = form.dataset.onSuccess || 'reload';
  const btn = form.querySelector('button');

  if (!confirm(`Commit "${title}"?`)) return;
  btn.disabled = true;
  btn.textContent = 'Committing…';

  try {
    // The commit message is derived server-side from the filepath.
    const res = await fetch('/api/commit-file', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filepath }),
    });
    if (res.ok) {
      if (onSuccess === 'reload') window.location.reload();
      else window.location.href = onSuccess;
    } else {
      alert('Error committing file');
      btn.disabled = false;
      btn.textContent = 'Commit';
    }
  } catch (err) {
    alert('Error: ' + err.message);
    btn.disabled = false;
    btn.textContent = 'Commit';
  }
});

// ── Image lightbox ────────────────────────────────────────────────────────────

(function () {
  const lb = document.createElement('div');
  lb.id = 'img-lightbox';
  const lbImg = document.createElement('img');
  lb.appendChild(lbImg);
  document.body.appendChild(lb);

  function open(src) {
    lbImg.src = src;
    lb.classList.add('open');
  }
  function close() {
    lb.classList.remove('open');
    lbImg.src = '';
  }

  // Click backdrop to close; clicking the image itself does nothing (cursor: default)
  lb.addEventListener('click', e => { if (e.target === lb) close(); });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && lb.classList.contains('open')) {
      e.stopImmediatePropagation();
      close();
    }
  });

  // Attach to all page-content images (both on load and after dynamic content)
  function attachLightbox(root) {
    root.querySelectorAll('.page-content img').forEach(img => {
      if (img.dataset.lightbox) return;
      img.dataset.lightbox = '1';
      img.addEventListener('click', () => open(img.src));
    });
  }
  attachLightbox(document);

  // Expose open() so presentation mode can wire cloned slides
  window._leafImgLightboxOpen = open;
}());

document.getElementById('commit-all-btn')?.addEventListener('click', async () => {
  if (!confirm('Commit all uncommitted files? Each file will get its own commit.')) return;
  const btn = document.getElementById('commit-all-btn');
  btn.disabled = true;
  btn.textContent = 'Committing…';
  try {
    const res = await fetch('/api/commit-all', { method: 'POST' });
    if (res.ok) {
      window.location.reload();
    } else {
      alert('Error committing files');
      btn.disabled = false;
      btn.textContent = 'Commit All';
    }
  } catch (err) {
    alert('Error: ' + err.message);
    btn.disabled = false;
    btn.textContent = 'Commit All';
  }
});
