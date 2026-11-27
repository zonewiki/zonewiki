import { whileUsed } from './util.js';

// ── Navigation trail breadcrumb ───────────────────────────────────────────────

(function () {
  const bc = document.querySelector('.breadcrumb[data-slug]');
  if (!bc) return;
  const slug  = bc.dataset.slug;
  const title = bc.dataset.title;
  if (!slug) return;
  // The server draws the breadcrumb without the trail (it is in sessionStorage);
  // style.css keeps the crumbs after the switcher hidden until this is done, so
  // adding the trail doesn't make them jump.
  const ready = () => bc.classList.add('trail-ready');

  // MRU list: visiting a page already in the list moves it to the end
  const KEY = 'zoneHistory';
  let hist = JSON.parse(sessionStorage.getItem(KEY) || '[]');
  hist = hist.filter(p => p.slug !== slug);
  hist.push({ slug, title, url: window.location.pathname + window.location.search });
  if (hist.length > 6) hist = hist.slice(-6);
  sessionStorage.setItem(KEY, JSON.stringify(hist));

  // All entries before the current page (last) become breadcrumb links. A
  // phone shows only the last 3 of them, so the trail is at most 4 pages.
  const maxPrev = window.matchMedia('(max-width: 640px)').matches ? 3 : Infinity;
  // The modules' own pages are in the module switcher already: home,
  // Notes, /tasks and where the Tasks link goes (My tasks,
  // /tasks?mine=1), and your private home (the Private pill).
  const tasksHref = bc.querySelector('.module-link[data-module="tasks"]')?.getAttribute('href');
  const intermediates = hist.slice(0, -1)
    .filter(p => !['index', '/notes', '/tasks', tasksHref].includes(p.slug)
      && !/^private\/[^/]+\/index$/.test(p.slug) && (p.url || '').split('?')[0] !== tasksHref)
                            .slice(-maxPrev);
  const homeEl = bc.querySelector('.breadcrumb-home');
  if (!intermediates.length || !homeEl) return ready();
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

  // The row has room for so many names: where the window is not wide (the
  // top actions take their part of it), six pages would each be cut down to
  // a letter or two. The oldest pages then leave the row (.trail-off, they
  // stay in the list) until every name left shows whole or at least
  // MIN_CRUMB wide; the page itself always stays. Again whenever the row's
  // width changes.
  const MIN_CRUMB = 72;
  const links = [...bc.querySelectorAll('.trail-link')];
  const crumbs = [...links, clr.previousElementSibling];
  const cramped = () => crumbs.some(el => el.offsetParent && el.scrollWidth > el.clientWidth && el.clientWidth < MIN_CRUMB);
  // With the separator after it: the first one is the switcher's divider.
  const setOff = (a, off) => {
    a.classList.toggle('trail-off', off);
    a.nextElementSibling.classList.toggle('trail-off', off);
  };
  const fitTrail = () => {
    links.forEach(a => setOff(a, false));
    for (const a of links) {
      if (!cramped()) break;
      setOff(a, true);
    }
  };
  fitTrail();
  // The room is the row's content box: the window's width, and less of it
  // once the uncommitted badge shows (the row keeps its place free).
  let width = null;
  new ResizeObserver(([entry]) => {
    const now = entry.contentRect.width;
    if (clr.isConnected && now !== width) fitTrail();
    width = now;
  }).observe(bc);
  ready();
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
// On big screens a page's table of contents is the left column (railToc in
// go/views.go, templates/partials/page.html); it marks the section whose heading was scrolled past
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

// A page's sheet is as wide as other views' (style.css, The sheet) unless
// something on it needs more room: a table, code block or image wider than
// the text. Then .sheet-wide gives the sheet the whole middle column, so the
// wide thing stays on the white. Measured with the room it would have (the
// class on, then off again if nothing needs it, before the browser paints),
// again when the window or the page's content changes size (an image
// loading, a section unfolding).
(function () {
  const col = document.querySelector('.page-grid > .page-col');
  const text = col && col.querySelector(':scope > .page-content');
  if (!text) return;
  const big = matchMedia('(min-width: 1320px)');
  let queued = false;
  const update = () => {
    queued = false;
    if (!big.matches) { col.classList.remove('sheet-wide'); return; }
    col.classList.add('sheet-wide');
    const limit = text.clientWidth + 1;
    const needs = [...text.querySelectorAll(':scope > :is(pre, table, p.img-block)')]
      .some(el => el.offsetWidth > limit);
    col.classList.toggle('sheet-wide', needs);
  };
  const queue = () => { if (!queued) { queued = true; requestAnimationFrame(update); } };
  update();
  big.addEventListener('change', queue);
  new ResizeObserver(queue).observe(text);
}());

// ── Modules ─────────────────────────────────────────────────────────────────
// The module switcher (breadcrumb) and the phone's bottom bar link to the
// modules; the server marks the current one (aria-current). The Tasks
// links carry the counts of tasks due and in Focus.

// The server renders the counts into the page (no change of width after it
// has painted); refreshDueBadge keeps them current: every minute, when the
// tab comes back, and after tasks writes (tasks.js). The task list
// switcher's places (.tasks-place, and their lines in the left column,
// .lists-rail-place) carry counts too, refreshed with them.
const tasksLinks = document.querySelectorAll('.module-link[data-module="tasks"]');
// (Asked for at each refresh: the row is drawn again when a list is made on
// the page, js/listsrow.js.)
const placesNow = () => document.querySelectorAll('.tasks-place[data-place], .lists-rail-place[data-place]');

// Same as taskCountsTitle in go/views.go.
function taskCountsTitle({ focus, overdue, today }) {
  const parts = [];
  if (focus > 0) parts.push(`${focus} for today`);
  if (overdue > 0) parts.push(`${overdue} overdue`);
  if (today > 0) parts.push(`${today} due today`);
  return parts.length ? `Tasks: ${parts.join(' · ')}` : 'Tasks';
}

function showCount(el, n) {
  const text = n > 0 ? String(n) : '';
  if (el && el.textContent !== text) el.textContent = text;
  if (el) el.hidden = !(n > 0);
}

// The answer's Zone-Tasks header is a word that changes with every write to
// the lists the page holds (pageScopes), not with one elsewhere. The page keeps the word of the tasks it holds
// (<body data-tasks-word>, set where they are loaded: js/taskdata.js). When
// the minute's own asking (poll) finds another word, someone changed a task
// since: the page's task lists are told (zone:tasks-changed; tasks.js loads
// them again when idle). A refresh after this page's own write only takes
// note of the word.
// What the page's task lists hold, for the word: their lists' refs, ","
// between; '' when one of them holds more than a list (My tasks, All tasks,
// a tag's view): then any write the user sees counts.
export function pageScopes() {
  const scopes = [];
  for (const app of document.querySelectorAll('.tasks-app:not([data-headless])')) {
    const d = app.dataset;
    if (d.initMine || !d.initList) return '';
    scopes.push(d.initList);
  }
  return scopes.join(',');
}
export async function refreshDueBadge(poll = false) {
  if (!tasksLinks.length) return;
  try {
    const places = placesNow();
    const held = pageScopes();
    const query = [places.length ? 'places=1' : '', held ? 'held=' + encodeURIComponent(held) : ''].filter(Boolean).join('&');
    const res = await fetch('/api/tasks/due-count' + (query ? '?' + query : ''));
    const c = await res.json();
    const word = res.headers.get('Zone-Tasks');
    const had = document.body.dataset.tasksWord;
    const changed = poll === true && word && had && word !== had;
    // After this page's own write the word is its own; a poll leaves it to
    // the lists' load, so a list that was busy is told again next minute.
    if (word && had && poll !== true) document.body.dataset.tasksWord = word;
    if (changed) document.dispatchEvent(new CustomEvent('zone:tasks-changed'));
    tasksLinks.forEach(a => {
      showCount(a.querySelector('.module-due'), c.count);
      showCount(a.querySelector('.module-focus'), c.focus);
      a.title = taskCountsTitle(c);
    });
    // What is new for the user, of every kind: the top bar's bell (Updates).
    document.querySelectorAll('.top-updates-count').forEach(el => showCount(el, c.updates || 0));
    places.forEach(a => {
      const p = c.places?.[a.dataset.place];
      showCount(a.querySelector('.module-due'), p ? p.count : 0);
      showCount(a.querySelector('.module-focus'), p ? p.focus : 0);
      showCount(a.querySelector('.module-new'), p ? p.new || 0 : 0);
    });
  } catch (e) {}
}

if (tasksLinks.length) {
  // Every minute while someone uses the page, and when the tab comes back.
  whileUsed(() => refreshDueBadge(true), 60000);
  // A page restored from the back/forward cache shows old counts.
  window.addEventListener('pageshow', e => { if (e.persisted) refreshDueBadge(); });
}

// ── The user menu, and the groups' ───────────────────────────────────────────
// A menu rendered hidden, opened as a popover under the button that was
// pressed. ↑/↓ move, Esc closes and returns focus. The button at the top
// right opens one in every wiki (.module-menu: Settings, Help;
// with users who is signed in, Sign out); for who is in several groups the switcher's Groups pill opens
// another (.groups-menu: each group's home), where a click on the link
// itself would go to the list of them (/groups, which is what a phone's tab
// and a browser without JavaScript get).
function popoverMenu(menu, backdrop, buttons) {
  if (!menu || !backdrop || !buttons.length) return;
  let opener = null;
  const items = () => [...menu.querySelectorAll('[role="menuitem"]')];

  function close(refocus) {
    if (menu.hidden) return;
    menu.hidden = backdrop.hidden = true;
    document.removeEventListener('keydown', onKey, true);
    opener?.setAttribute('aria-expanded', 'false');
    if (refocus) opener?.focus();
    opener = null;
  }
  function open(btn) {
    opener = btn;
    menu.hidden = backdrop.hidden = false;
    btn.setAttribute('aria-expanded', 'true');
    // Under the button, flipped up if it doesn't fit below (the tab bar's).
    const r = btn.getBoundingClientRect(), m = menu.getBoundingClientRect();
    const top = r.bottom + 4 + m.height > window.innerHeight ? r.top - 4 - m.height : r.bottom + 4;
    menu.style.top = `${Math.max(4, top)}px`;
    menu.style.left = `${Math.max(4, Math.min(r.left, window.innerWidth - m.width - 4))}px`;
    document.addEventListener('keydown', onKey, true);
    items()[0]?.focus({ preventScroll: true });
  }
  function onKey(e) {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(true); }
    else if (e.key === 'Tab') close(false);
    else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault(); e.stopPropagation();
      const list = items(), i = list.indexOf(document.activeElement);
      list[(i + (e.key === 'ArrowDown' ? 1 : -1) + list.length) % list.length]?.focus();
    }
  }
  buttons.forEach(btn => {
    btn.addEventListener('click', e => {
      // A link that opens a menu: with Ctrl, Shift or the middle button it is a link.
      if (btn.tagName === 'A') {
        if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
        e.preventDefault();
      }
      const again = opener === btn;
      close(false);
      if (!again) open(btn);
    });
    // ↓ on the button opens it too.
    btn.addEventListener('keydown', e => {
      if (e.key === 'ArrowDown' && menu.hidden) { e.preventDefault(); open(btn); }
    });
  });
  backdrop.addEventListener('click', () => close(false));
  menu.addEventListener('click', () => close(false));
}
popoverMenu(document.querySelector('.module-menu:not(.groups-menu)'),
  document.querySelector('.module-menu-backdrop:not(.groups-menu-backdrop)'), document.querySelectorAll('.module-menu-btn'));
(function () {
  const pill = document.querySelector('.module-switch .module-link[data-module="groups"]');
  const menu = document.querySelector('.groups-menu');
  if (!pill || !menu) return;
  pill.setAttribute('aria-haspopup', 'menu');
  pill.setAttribute('aria-expanded', 'false');
  popoverMenu(menu, document.querySelector('.groups-menu-backdrop'), [pill]);
}());
