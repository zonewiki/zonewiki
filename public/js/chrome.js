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
  // The visited pages are marked: sharing mode hides them.
  for (const p of intermediates) {
    const sep = mkSep();
    sep.classList.add('trail-sep');
    bc.appendChild(sep);
    const a = document.createElement('a');
    a.className = 'trail-link';
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

// ── Side columns: the section being read ─────────────────────────────────────
// On big screens a page's table of contents is the left column (views.py
// rail_toc_items, templates/partials/page.html); it marks the section whose heading was scrolled past
// last, and at the end of the page the last one that came into view.

(function () {
  const links = [...document.querySelectorAll('.rail-toc a[href^="#"]')];
  if (!links.length) return;
  const heads = links.map(a => document.getElementById(decodeURIComponent(a.hash.slice(1))));
  let queued = false;
  function mark() {
    queued = false;
    const atEnd = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2;
    const line = atEnd ? window.innerHeight : 120;
    let current = 0;
    // A heading in a folded section has no box: it counts as not reached.
    heads.forEach((h, i) => { if (h?.getClientRects().length && h.getBoundingClientRect().top < line) current = i; });
    links.forEach((a, i) => {
      a.classList.toggle('active', i === current);
      if (i === current) a.setAttribute('aria-current', 'location');
      else a.removeAttribute('aria-current');
    });
    // A long TOC scrolls in its column: keep the marked entry in view
    // (by scrolling the column only, never the page).
    const rail = links[current].closest('.rail'), a = links[current];
    if (rail && rail.scrollHeight > rail.clientHeight) {
      const top = a.getBoundingClientRect().top - rail.getBoundingClientRect().top + rail.scrollTop, margin = 32;
      if (top < rail.scrollTop + margin) rail.scrollTop = top - margin;
      else if (top + a.offsetHeight > rail.scrollTop + rail.clientHeight - margin)
        rail.scrollTop = top + a.offsetHeight - rail.clientHeight + margin;
    }
  }
  const queue = () => { if (!queued) { queued = true; requestAnimationFrame(mark); } };
  window.addEventListener('scroll', queue, { passive: true });
  window.addEventListener('resize', queue);
  mark();
}());

// The page's actions at the top of the right column, once the header's
// have scrolled out of view.
(function () {
  const box = document.querySelector('.rail-actions');
  const header = document.querySelector('.page-header-actions');
  if (!box || !header) return;
  new IntersectionObserver(([entry]) => { box.hidden = entry.isIntersecting; }).observe(header);
}());

// "Back to top" under the left column's TOC, once the page has scrolled a
// screen down.
(function () {
  const link = document.querySelector('.rail-top');
  if (!link) return;
  const update = () => { link.hidden = window.scrollY < window.innerHeight; };
  window.addEventListener('scroll', update, { passive: true });
  update();
  link.addEventListener('click', e => {
    e.preventDefault();
    const smooth = !matchMedia('(prefers-reduced-motion: reduce)').matches;
    window.scrollTo({ top: 0, behavior: smooth ? 'smooth' : 'auto' });
    if (location.hash) history.replaceState(null, '', location.pathname + location.search);
  });
}());

// A side column taller than the window scrolls on its own; a fade at its
// top or bottom edge (.rail-more-above/-below) says there is more.
(function () {
  const rails = document.querySelectorAll('.rail');
  if (!rails.length) return;
  const update = rail => {
    rail.classList.toggle('rail-more-above', rail.scrollTop > 1);
    rail.classList.toggle('rail-more-below', rail.scrollTop + rail.clientHeight < rail.scrollHeight - 1);
  };
  rails.forEach(rail => {
    rail.addEventListener('scroll', () => update(rail), { passive: true });
    update(rail);
  });
  window.addEventListener('resize', () => rails.forEach(update));
}());

// ── Modules ─────────────────────────────────────────────────────────────────
// The module switcher (breadcrumb) and the phone's bottom bar link to the
// three modules; the server marks the current one (aria-current). The Deck
// links carry the counts of tasks due and in Focus.

// The server renders the counts into the page (no change of width after it
// has painted); refreshDueBadge keeps them current: every minute, when the
// tab comes back, and after deck writes (deck.js).
const deckLinks = document.querySelectorAll('.module-link[data-module="deck"]');

// Same as views.py's _deck_counts_title.
function deckCountsTitle({ focus, overdue, today }) {
  const parts = [];
  if (focus > 0) parts.push(`${focus} in Focus`);
  if (overdue > 0) parts.push(`${overdue} overdue`);
  if (today > 0) parts.push(`${today} due today`);
  return parts.length ? `Deck: ${parts.join(' · ')}` : 'Deck';
}

function showCount(el, n) {
  const text = n > 0 ? String(n) : '';
  if (el && el.textContent !== text) el.textContent = text;
  if (el) el.hidden = !(n > 0);
}

export async function refreshDueBadge() {
  if (!deckLinks.length) return;
  try {
    const res = await fetch('/api/deck/due-count');
    const c = await res.json();
    deckLinks.forEach(a => {
      showCount(a.querySelector('.module-due'), c.count);
      showCount(a.querySelector('.module-focus'), c.focus);
      a.title = deckCountsTitle(c);
    });
  } catch (e) {}
}

if (deckLinks.length) {
  setInterval(() => { if (!document.hidden) refreshDueBadge(); }, 60000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshDueBadge(); });
  // A page restored from the back/forward cache shows old counts.
  window.addEventListener('pageshow', e => { if (e.persisted) refreshDueBadge(); });
}
