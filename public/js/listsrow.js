// The Tasks page's lists, in two forms of the same entries:
// the row (.tasks-places in tasks_page.html): My tasks, the Inbox and the
// pinned lists as pills, then More, a details holding the other lists and
// All tasks; and on big screens the left column (.lists-rail), where the
// row hides: every list a line, the pins after the Inbox, the others below.
// Here: More closes as a menu does; both can be filtered by name; a list
// this browser hasn't shown before is marked new; and the pins can be
// dragged into another order (across in the row, up and down in the column).

// A list made while the page is open (New list… in the dialog that moves
// tasks to a list, js/taskselect.js) is in the row and the column at once:
// they are asked for again and wired again (initRow).

const KEY = 'zone_lists_seen';
const readSeen = () => {
  try { const v = JSON.parse(localStorage.getItem(KEY)); return Array.isArray(v) ? new Set(v) : null; } catch (_) { return null; }
};
const writeSeen = seen => { try { localStorage.setItem(KEY, JSON.stringify([...seen])); } catch (_) {} };

// A click elsewhere closes More.
document.addEventListener('click', e => {
  const more = document.querySelector('.tasks-places .lists-more');
  if (more?.open && !more.contains(e.target)) more.open = false;
});

// The column is showing (a big screen): the side columns are display: none
// below 1320px.
const railShown = rail => !!rail && getComputedStyle(rail.closest('.rail') || rail).display !== 'none';

// A find field over a set of list links (with many lists): the lists whose
// name has what is typed; a group's word (Your lists, Other lists) goes
// with its last list; Enter opens the first. Returns apply, to clear.
function wireFilter(filter, items, box, headSel) {
  const apply = () => {
    const q = filter.value.trim().toLowerCase().replace(/^#/, '');
    for (const a of items) a.hidden = q !== '' && !a.dataset.list.toLowerCase().includes(q);
    for (const head of box.querySelectorAll(headSel)) {
      let el = head.nextElementSibling, any = false;
      for (; el && !el.matches(headSel); el = el.nextElementSibling) {
        if (el.matches('[data-list]')) any = any || !el.hidden;
      }
      head.hidden = !any;
    }
  };
  filter.addEventListener('input', apply);
  filter.addEventListener('keydown', e => {
    if (e.key !== 'Enter') return;
    const first = items.find(a => !a.hidden);
    if (first && filter.value.trim() !== '') { e.preventDefault(); first.click(); }
  });
  return apply;
}

// Dragging a pinned list's entry puts the pins in the new order
// (POST /tasks/pin-order), for who may pin (data-reorder on nav). With the
// mouse only: a finger scrolls and taps here. vertical: the column's lines,
// else the row's pills.
function wireDrag(nav, group, sel, vertical) {
  if (!nav?.hasAttribute('data-reorder') || !group) return;
  const pins = () => [...group.querySelectorAll(sel)];
  const order = () => pins().map(a => a.dataset.place.slice('list:'.length));
  let dragged = null, before = null, dropped = false;
  const restore = () => {
    const by = new Map(pins().map(a => [a.dataset.place.slice('list:'.length), a]));
    const anchor = pins().at(-1)?.nextSibling ?? null;
    for (const ref of before) group.insertBefore(by.get(ref), anchor);
  };
  if (pins().length < 2) return;
  for (const a of pins()) {
    a.draggable = true;
    a.addEventListener('dragstart', e => {
      dragged = a; before = order(); dropped = false;
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', a.dataset.place);
      a.classList.add('is-dragging');
    });
    a.addEventListener('dragover', e => {
      if (!dragged) return;
      e.preventDefault();
      if (dragged === a) return;
      const r = a.getBoundingClientRect();
      const first = vertical ? e.clientY < r.top + r.height / 2 : e.clientX < r.left + r.width / 2;
      group.insertBefore(dragged, first ? a : a.nextSibling);
    });
    a.addEventListener('dragend', async () => {
      a.classList.remove('is-dragging');
      const now = order(), was = before;
      dragged = null;
      if (!dropped) { restore(); return; }          // let go outside: as it was
      if (now.join('\n') === was.join('\n')) return;
      const body = new URLSearchParams();
      for (const ref of now) body.append('list', ref);
      try {
        const res = await fetch('/tasks/pin-order', { method: 'POST', body });
        if (!res.ok) throw new Error(String(res.status));
      } catch (_) { before = was; restore(); }
    });
  }
  group.addEventListener('dragover', e => { if (dragged) e.preventDefault(); });
  group.addEventListener('drop', e => { if (dragged) { e.preventDefault(); dropped = true; } });
}

function initRow() {
const nav = document.querySelector('.tasks-places');
const rail = document.querySelector('.lists-rail');
// On a phone the row scrolls sideways: the current pill is in view.
if (!railShown(rail)) nav?.querySelector('.tasks-places-group [aria-current]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
const more = nav?.querySelector('.lists-more');
const items = more ? [...more.querySelectorAll('.lists-more-item[data-list]')] : [];
const railItems = rail ? [...rail.querySelectorAll('.lists-rail-item[data-list]')] : [];

if (more) {
  const button = more.querySelector('summary');
  const filter = more.querySelector('.lists-more-filter');

  // Escape closes it.
  more.addEventListener('keydown', e => {
    if (e.key !== 'Escape' || !more.open) return;
    e.preventDefault();
    e.stopPropagation();
    more.open = false;
    button.focus();
  });

  // The filter (with many lists); All tasks stays.
  if (filter) {
    const apply = wireFilter(filter, items, more, '.lists-more-head');
    more.addEventListener('toggle', () => {
      if (!more.open) return;
      filter.value = '';
      apply();
      filter.focus();
    });
  }
}
const railFilter = rail?.querySelector('.lists-rail-filter');
if (railFilter) wireFilter(railFilter, railItems, rail.querySelector('.lists-rail-rest'), '.lists-rail-head');

// Lists new to this browser. What it has shown is remembered by key
// (localStorage, zone_lists_seen); a list under More that isn't among
// them was made since, by someone else or an assistant (one made here is
// pinned, and so a pill): "new" beside its name, in More and in the
// column, and a dot on More. Opening More, the list, or the column on the
// screen makes it seen (the marks stay while the page does). The first
// visit knows them all.
if (more) {
  const button = more.querySelector('summary');
  const pills = [...nav.querySelectorAll('a.tasks-place[data-place^="list:"]')].map(a => a.dataset.place);
  const under = items.map(a => a.dataset.place);
  let seen = readSeen();
  if (!seen) seen = new Set(under);
  for (const key of pills) seen.add(key);
  for (const a of items) if (a.hasAttribute('aria-current')) seen.add(a.dataset.place);
  const fresh = new Set(under.filter(key => !seen.has(key)));
  for (const a of [...items, ...railItems]) {
    if (!fresh.has(a.dataset.place)) continue;
    const mark = document.createElement('span');
    mark.className = 'lists-more-new';
    mark.textContent = 'new';
    a.append(mark);
  }
  const shown = railShown(rail);
  if (shown) for (const key of under) seen.add(key);
  button.classList.toggle('has-new', fresh.size > 0 && !shown);
  writeSeen(seen);
  more.addEventListener('toggle', () => {
    if (!more.open || !fresh.size) return;
    for (const key of under) seen.add(key);
    writeSeen(seen);
    button.classList.remove('has-new');
  });
}

// The column's current line in view, when the column scrolls.
const current = rail?.querySelector('[aria-current]'), box = rail?.closest('.rail');
if (current && box && railShown(rail) && box.scrollHeight > box.clientHeight) {
  const top = current.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop;
  if (top + current.offsetHeight > box.clientHeight - 32) box.scrollTop = top - box.clientHeight / 2;
}

wireDrag(nav, nav?.querySelector('.tasks-places-group'), 'a.tasks-place[data-place^="list:"]', false);
wireDrag(rail, rail?.querySelector('.lists-rail-pins'), 'a.lists-rail-item[data-place^="list:"]', true);
}
initRow();

// The list is one this browser has shown (it made it), so it isn't marked
// new; then the row and the column as the server draws them now, in the
// old ones' places (or at the sheet's top and the column's, where a first
// list's go).
document.addEventListener('zone:list-made', async e => {
  if (location.pathname !== '/tasks') return;
  const seen = readSeen();
  if (seen && e.detail?.ref) { seen.add('list:' + e.detail.ref); writeSeen(seen); }
  try {
    const res = await fetch(location.href, { headers: { Accept: 'text/html' } });
    if (!res.ok) return;
    const doc = new DOMParser().parseFromString(await res.text(), 'text/html');
    const swap = (sel, into) => {
      const fresh = doc.querySelector(sel);
      if (!fresh) return;
      const now = document.importNode(fresh, true);
      const old = document.querySelector(sel);
      if (old) old.replaceWith(now);
      else document.querySelector(into)?.prepend(now);
    };
    swap('.tasks-places', '.sheet');
    swap('.lists-rail', '.rail-lists');
    initRow();
  } catch (_) {}
});

