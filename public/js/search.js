import { closeOnBackdropClick, he, localizeDates } from './util.js';
import { sharingOn } from './actions.js';
import { revealElement } from './fold.js';
import { renderFigures } from './figures.js';

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

// Whether content matches are found by meaning too (retrieval.semantic_status):
// the page says how it was when rendered, each /api/search how it is now.
const searchMode = document.getElementById('search-mode');
const SEARCH_MODES = {
  on: ['Keywords + meaning', 'Content matches are found by their words and by meaning (semantic search).'],
  indexing: ['Meaning: indexing…', 'Semantic search is still indexing the wiki; until then content matches are found by their words only.'],
  failed: ['Keywords only', "The semantic search model fails (see Zone's log); content matches are found by their words only."],
  off: ['Keywords only', 'Semantic search is off (ZONE_SEMANTIC_SEARCH=on in .env turns it on); content matches are found by their words.'],
};

function showSearchMode(state) {
  const mode = SEARCH_MODES[state];
  if (!searchMode || !mode) return;
  searchMode.dataset.state = state;
  searchMode.textContent = mode[0];
  searchMode.title = mode[1];
}
if (searchMode) showSearchMode(searchMode.dataset.state);

export function openSearchModal() {
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
    `<span class="page-item-right">${p.private ? '<span class="lock" title="Private: only you see this page">🔒</span>' : ''}<span class="page-slug">${he(p.slug)}</span></span></li>`
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
  queueModalPreview(items[modalActiveIdx]);
}

// ── Search preview ───────────────────────────────────────────────────────────
// On wide screens the search modal shows the highlighted result on the right:
// a page from its section on (/api/page-preview, the matched words marked as
// on the opened page), a task or other entry as its result card.
const modalPreview = document.getElementById('modal-preview');
const modalPreviewWide = matchMedia('(min-width: 1100px)');
const modalPreviewCache = new Map();
let modalPreviewTimer = null, modalPreviewFor = null;

function queueModalPreview(item) {
  if (!modalPreview || !modalPreviewWide.matches) return;
  clearTimeout(modalPreviewTimer);
  modalPreviewTimer = setTimeout(() => showModalPreview(item), 80);
}

async function showModalPreview(item) {
  const link = item && (item.matches('a') ? item : item.querySelector('a[href]'));
  const href = link?.getAttribute('href') || '';
  if (href === modalPreviewFor) return;
  modalPreviewFor = href;
  if (!href) { modalPreview.replaceChildren(); return; }
  const url = new URL(href, location.origin);
  const m = url.pathname.match(/^\/page\/(.+)$/);
  if (!m || m[1].startsWith('_')) {
    // A task, the tasks, a built-in page: the result card says enough.
    const card = document.createElement('div');
    card.className = 'search-preview-card';
    card.append(...[...link.childNodes].map(n => n.cloneNode(true)));
    modalPreview.replaceChildren(card);
    return;
  }
  const slug = decodeURIComponent(m[1]);
  let data = modalPreviewCache.get(slug);
  if (!data) {
    try {
      const res = await fetch(`/api/page-preview/${encodeURI(slug)}`);
      if (!res.ok) throw new Error(res.status);
      data = await res.json();
      modalPreviewCache.set(slug, data);
    } catch (_) {
      if (modalPreviewFor === href) modalPreview.replaceChildren();
      return;
    }
  }
  if (modalPreviewFor !== href) return;   // the highlight moved on meanwhile
  const title = document.createElement('h2');
  title.className = 'search-preview-title';
  title.textContent = data.title;
  const body = document.createElement('div');
  body.className = 'page-content';
  body.innerHTML = data.html;
  localizeDates(body);
  modalPreview.replaceChildren(title, body);
  renderFigures(body);
  const marks = markTerms(body, url.searchParams.getAll('hl').filter(Boolean));
  const anchor = url.hash && body.querySelector(`[data-pid="${CSS.escape(decodeURIComponent(url.hash.slice(1)))}"]`);
  const target = anchor || marks[0];
  modalPreview.scrollTop = target
    ? target.getBoundingClientRect().top - modalPreview.getBoundingClientRect().top + modalPreview.scrollTop - 12
    : 0;
}

modalFilter?.addEventListener('input', applyModalControls);
// With an empty query the list is a quick switcher, so it opens on the most
// recently edited pages. A sort picked from the dropdown is remembered.
const MODAL_SORT_KEY = 'zone-search-sort';
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
// alphabetical order: notes last (temporary, often titled by their
// file name), then pages in fewer folders (a top-level page is the main page
// for a name), then the shorter name, which the query covers more of. So
// "alex" puts "Alex Morgan" before "Alex 1:1 Kickoff" (three folders
// down) and the note "alex_prep".
const QUICKNOTE_SLUG_RE = /^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_/;  // mdtext.py QUICKNOTE_RE

function tieBreak(p, field) {
  const name = p.slug.split('/').pop();
  return {
    quicknote: QUICKNOTE_SLUG_RE.test(name) ? 1 : 0,
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
    scored.sort((a, b) => b.score - a.score || a.quicknote - b.quicknote
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
    // built-in pages (Tasks, Notes) are modules, in the module switcher, so
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
    // Sharing mode: no list of recent pages before anything is typed.
    renderModalList(sharingOn() ? [] : [...special, ...sorted.slice(0, 200)]);
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

const MODAL_RESULTS = 15;  // retrieval.py SEARCH_LIMIT

async function fetchModalContentMatches(q) {
  const seq = ++modalCMSeq;
  try {
    // "Unlinked only" applies to content matches too: the server filters
    // before it cuts the list, so the 15 results are all unlinked pages.
    const unlinked = modalUnlinked.checked ? '&unlinked=1' : '';
    // One more than shown: if it comes back, there are more for /search.
    const res = await fetch(`/api/search?q=${encodeURIComponent(q)}${unlinked}&limit=${MODAL_RESULTS + 1}`);
    if (seq !== modalCMSeq) return;  // stale response — discard
    showSearchMode(res.headers.get('X-Zone-Semantic'));
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
    // Re-clamp active index now that content cards are in the pool; with no
    // title match the first content match is highlighted (Enter opens it).
    if (modalActiveIdx >= 0 || modalFilter.value.trim()) setModalActive(Math.max(0, modalActiveIdx));
  } catch (_) {}
}

// ── Notes filter ────────────────────────────────────────────────────────────
// The Notes page's filter box runs the search (/api/search?notes=1: the
// same ranking, query syntax, excerpts and on-page highlights) over quicknote
// notes only. Matches show in ranked order with their excerpt, linking to
// the section that matched; an empty box restores the newest-first list.
// The query lives in ?q=, so Back returns to the filtered list. Enter opens
// the first match, Esc clears the box (a second Esc leaves it).

(function initNotesFilter() {
  const input = document.getElementById('notes-filter');
  const list = document.querySelector('.notes-list');
  if (!input || !list) return;
  const countEl = document.getElementById('notes-filter-count');
  const items = [...list.children];
  const bySlug = new Map(items.map(li => [li.dataset.slug, li]));
  const empty = document.createElement('p');
  empty.className = 'notes-filter-empty';
  empty.textContent = 'No note matches.';
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
      const res = await fetch(`/api/search?notes=1&limit=1000&q=${encodeURIComponent(q)}`);
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
// Code blocks (highlight.js rewrites them) and task list embeds are left alone.

function initSearchHighlights() {
  const article = document.querySelector('article.page-content');
  const params = new URLSearchParams(location.search);
  const terms = params.getAll('hl').map(t => t.trim()).filter(Boolean);
  if (!article || !terms.length) return;
  const marks = markTerms(article, terms);
  if (!marks.length) return;
  searchHighlightBar(article, params, marks, terms);
}

// Mark every whole-word occurrence of the terms in root (<mark class=
// "search-hl">), skipping code and task list embeds; returns the marks in order.
function markTerms(root, terms) {
  // No terms (a page matched by its title): nothing to mark. An empty
  // pattern would match everywhere.
  if (!terms.length) return [];
  const escape = t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s+');
  const alternatives = [...terms].sort((a, b) => b.length - a.length).map(escape).join('|');
  const re = new RegExp(`(?<![\\p{L}\\p{N}])(?:${alternatives})(?![\\p{L}\\p{N}])`, 'giu');
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: n => n.parentElement.closest('pre, script, style, .tasks-app, .section-folded-label, .math, .mermaid-diagram')
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
  return marks;
}

function searchHighlightBar(article, params, marks, terms) {
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
    revealElement(marks[current]);
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
  // In the capture phase, so n doesn't also reach a task list embed's "new task".
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
