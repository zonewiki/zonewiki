import { closeOnBackdropClick } from './util.js';
import { deckHomeHref, openHomeTask, openNewPage, openScratch, toggleSharing } from './actions.js';
import { openSearchModal } from './search.js';
import { togglePreview } from './editor.js';

// ── Keyboard shortcuts ────────────────────────────────────────────────────────

// The section headings of a page view (the article's ## and ###, not those
// of a deck embed). The current section is the last heading scrolled past,
// as the side TOC marks it; ] / [ scroll to the next / previous one and put
// its anchor in the URL. False where there is nothing to jump to.
function jumpSection(step) {
  const article = document.querySelector('article.page-content');
  if (!article || document.querySelector('.presentation-overlay')) return false;
  const heads = [...article.querySelectorAll('h2[id], h3[id]')].filter(h => !h.closest('.tasks-app'));
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
    resetG(); resetC();
    return;
  }

  // g-prefix navigation: gh=home, gs=scratch, gd=deck (every task),
  // gt=the deck's home (your own deck; /deck without one)
  if (awaitingG) {
    resetG();
    const dest = { h: '/page/index', s: '/page/_scratch', d: '/deck', t: deckHomeHref() };
    if (dest[e.key]) { e.preventDefault(); window.location.href = dest[e.key]; }
    return;
  }
  if (e.key === 'g') {
    e.preventDefault();
    awaitingG = true;
    gPrefixTimer = setTimeout(resetG, 1000);
    return;
  }

  // c-prefix create: cs=scratch note, ct=task in the deck's home
  if (awaitingC) {
    resetC();
    if (e.key === 's') { e.preventDefault(); openScratch(); }
    else if (e.key === 't') { e.preventDefault(); openHomeTask(); }
    return;
  }
  if (e.key === 'c') {
    e.preventDefault();
    awaitingC = true;
    cPrefixTimer = setTimeout(resetC, 1000);
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

  // P — enter presentation mode
  if (e.key === 'p') {
    document.getElementById('present-btn')?.click();
  }

  // ? — show keyboard shortcuts
  if (e.key === '?') shortcutsModal?.showModal();
});
