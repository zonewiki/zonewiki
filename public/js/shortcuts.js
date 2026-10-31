import { closeOnBackdropClick, keyPrefix } from './util.js';
import { tasksHomeHref, openHomeTask, openScratch, toggleSharing } from './actions.js';
import { openSearchModal } from './search.js';
import { togglePreview } from './editor.js';
import { foldAll } from './fold.js';

// ── Keyboard shortcuts ────────────────────────────────────────────────────────

// The section headings of a page view (the article's ## and ###, not those
// of a task list embed). The current section is the last heading scrolled past,
// as the side TOC marks it; ] / [ scroll to the next / previous one and put
// its anchor in the URL. False where there is nothing to jump to.
function jumpSection(step) {
  const article = document.querySelector('article.page-content');
  if (!article || document.querySelector('.presentation-overlay')) return false;
  // Not those inside a folded section.
  const heads = [...article.querySelectorAll('h2[id], h3[id]')]
    .filter(h => !h.closest('.tasks-app, [data-fold-hidden]'));
  if (!heads.length) return false;
  let current = -1;
  heads.forEach((h, i) => { if (h.getBoundingClientRect().top < 121) current = i; });
  const target = heads[current + step];
  if (!target) {
    if (step < 0 && current === 0) { window.scrollTo({ top: 0 }); return true; }
    return false;
  }
  window.scrollTo({ top: window.scrollY + target.getBoundingClientRect().top - 40 });
  history.replaceState(null, '', '#' + target.id);
  return true;
}

const shortcutsModal = document.getElementById('shortcuts-modal');
closeOnBackdropClick(shortcutsModal);

function resetPrefix() { keyPrefix.key = null; clearTimeout(keyPrefix.timer); }
function startPrefix(key) {
  resetPrefix();
  keyPrefix.key = key;
  keyPrefix.timer = setTimeout(resetPrefix, 1000);
}

document.addEventListener('keydown', e => {
  // Ctrl/Cmd+P — open search modal
  if (e.key === 'p' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    openSearchModal();
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
    // requestSubmit, not submit(): the submit handler waits for a save in
    // flight and handles a conflict in place
    if (form) { e.preventDefault(); form.requestSubmit(); }
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
    resetPrefix();
    return;
  }

  // g-prefix navigation: gh=home, gn=Notes, ga=all tasks, gt=the Tasks
  // home (/deck without one), gp=your private home (with users: the Private
  // pill). gs and gd were Notes and all tasks before the rename, kept for
  // one release.
  if (keyPrefix.key === 'g') {
    resetPrefix();
    const dest = { h: '/page/index', n: '/page/_scratch', a: '/deck', t: tasksHomeHref() };
    dest.s = dest.n; dest.d = dest.a;
    if (document.querySelector('.module-link[data-module="private"]')) dest.p = '/private';
    if (dest[e.key]) { e.preventDefault(); window.location.href = dest[e.key]; }
    return;
  }
  if (e.key === 'g') {
    e.preventDefault();
    startPrefix('g');
    return;
  }

  // c-prefix create: cn=note (cs before the rename, kept for one release),
  // ct=task in the Tasks home
  if (keyPrefix.key === 'c') {
    resetPrefix();
    if (e.key === 'n' || e.key === 's') { e.preventDefault(); openScratch(); }
    else if (e.key === 't') { e.preventDefault(); openHomeTask(); }
    return;
  }
  if (e.key === 'c') {
    e.preventDefault();
    startPrefix('c');
    return;
  }

  // S — sharing mode on/off
  if (e.key === 'S' && e.shiftKey) {
    e.preventDefault();
    toggleSharing();
    return;
  }

  // ] / [ — next / previous section (## or ###) of the page
  if ((e.key === ']' || e.key === '[') && jumpSection(e.key === ']' ? 1 : -1)) {
    e.preventDefault();
    return;
  }

  // E — go to edit
  if (e.key === 'e') {
    const editLink = document.querySelector('a[href^="/edit/"]');
    if (editLink) window.location.href = editLink.href;
  }

  // Z — fold / unfold every section
  if (e.key === 'z' && !document.querySelector('.presentation-overlay')) {
    foldAll();
  }

  // P — enter presentation mode
  if (e.key === 'p') {
    document.getElementById('present-btn')?.click();
  }

  // ? — show keyboard shortcuts
  if (e.key === '?') shortcutsModal?.showModal();
});
