// ── Folding sections ──────────────────────────────────────────────────────────

// On a page view each ## and ### heading with something under it gets a
// chevron: a click folds the section (what follows the heading up to the
// next heading of the same or a higher level; a folded ## hides its ###s
// too), Alt+click folds or unfolds every section, and z does too. A folded
// heading has a wavy line with "folded" in it, which unfolds it. View
// only: the file is untouched and every page opens unfolded.
//
// Folded content is hidden="until-found", so the browser's find in page
// still reaches it and unfolds it (beforematch); a link to a section or to
// something in one (an anchor, the side TOC, ] / [, a search highlight)
// unfolds it with revealElement(). Printing and presentation mode show
// everything.

import { hashTarget } from './util.js';

const CHEVRON = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 6l4 4 4-4"/></svg>';

// The sections, in page order: { header, level, body, folded, button }.
let sections = [];

// The article's section headers (only the page view has them, around a ##
// or ### with its ✎), not a task list embed's or those inside a quote or list.
function findSections(article) {
  const found = [];
  for (const header of article.querySelectorAll(':scope > .section-header')) {
    const level = header.classList.contains('section-header-h2') ? 2 : 3;
    const body = [];
    for (let el = header.nextElementSibling; el; el = el.nextElementSibling) {
      if (el.matches('h1, h2') || el.matches('.section-header-h2')) break;
      if (level === 3 && (el.matches('h3') || el.matches('.section-header'))) break;
      body.push(el);
    }
    if (body.length) found.push({ header, level, body, folded: false, button: null });
  }
  return found;
}

// Hide what a folded section holds, show the rest. An element can be in
// two sections (a ### inside a ##): it shows when neither is folded.
function apply() {
  const hidden = new Set();
  for (const s of sections) if (s.folded) s.body.forEach(el => hidden.add(el));
  for (const s of sections) {
    for (const el of s.body) {
      if (hidden.has(el)) {
        if (!el.hasAttribute('data-fold-hidden')) {
          el.setAttribute('hidden', 'until-found');
          el.setAttribute('data-fold-hidden', '');
        }
      } else if (el.hasAttribute('data-fold-hidden')) {
        el.removeAttribute('hidden');
        el.removeAttribute('data-fold-hidden');
      }
    }
    s.header.classList.toggle('section-folded', s.folded);
    s.button.setAttribute('aria-expanded', String(!s.folded));
    s.button.title = s.folded ? 'Unfold section (Alt+click: every section)' : 'Fold section (Alt+click: every section)';
    s.button.setAttribute('aria-label', s.folded ? 'Unfold section' : 'Fold section');
  }
}

// Fold or unfold every section; with no argument, fold them all unless
// all are folded already.
export function foldAll(folded) {
  if (!sections.length) return false;
  if (folded === undefined) folded = sections.some(s => !s.folded);
  sections.forEach(s => { s.folded = folded; });
  apply();
  return true;
}

// Unfold whatever hides el, and el's own section when it is a heading.
export function revealElement(el) {
  if (!el || !sections.length) return;
  let changed = false;
  for (const s of sections) {
    if (!s.folded) continue;
    if (s.body.some(b => b === el || b.contains(el)) || s.header.contains(el)) {
      s.folded = false;
      changed = true;
    }
  }
  if (changed) apply();
}

function revealHash(hash) {
  revealElement(hashTarget(hash));
}

(function () {
  const article = document.querySelector('.page-grid article.page-content');
  if (!article) return;
  sections = findSections(article);
  for (const s of sections) {
    const h = s.header.querySelector(':scope > h2, :scope > h3');
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'section-fold';
    button.innerHTML = CHEVRON;
    button.addEventListener('click', e => {
      e.preventDefault();
      if (e.altKey) foldAll(!s.folded);
      else { s.folded = !s.folded; apply(); }
    });
    // After the heading's text, before its link icon (js/page.js).
    h.insertBefore(button, h.querySelector(':scope > .heading-link'));
    s.button = button;
    // "folded" in the middle of the wavy line (shown only while folded),
    // on what has the line: a ##'s header, a ### itself.
    const label = document.createElement('button');
    label.type = 'button';
    label.className = 'section-folded-label';
    label.textContent = 'folded';
    label.title = 'Unfold section';
    label.addEventListener('click', e => {
      e.preventDefault();
      s.folded = false;
      apply();
    });
    (s.level === 2 ? s.header : h).append(label);
  }
  if (!sections.length) return;
  apply();

  // Find in page found something folded: the browser shows it, the
  // sections follow.
  article.addEventListener('beforematch', e => revealElement(e.target));

  // A link to an anchor on this page (the side TOC's, one in the text, a
  // section's link icon) unfolds its target before the browser scrolls.
  document.addEventListener('click', e => {
    const a = e.target.closest?.('a[href*="#"]');
    if (a && !a.matches('.heading-link') && a.pathname === location.pathname && a.hash) revealHash(a.hash);
  });
  window.addEventListener('hashchange', () => revealHash(location.hash));

  // Print the whole page, folds as they were afterwards.
  let saved = null;
  window.addEventListener('beforeprint', () => {
    saved = sections.map(s => s.folded);
    foldAll(false);
  });
  window.addEventListener('afterprint', () => {
    if (!saved) return;
    sections.forEach((s, i) => { s.folded = saved[i]; });
    saved = null;
    apply();
  });
}());
