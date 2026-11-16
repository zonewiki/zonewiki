// The Tasks page's row of lists (.tasks-places in tasks_page.html): the
// My tasks, the Inbox and the pinned lists as pills and More, a details
// holding the other lists and All tasks. Here: More closes as a menu does and can be filtered by name; a
// list this browser hasn't shown before is marked new; and the pinned pills
// can be dragged into another order.

// A list made while the page is open (New list… in the dialog that moves
// tasks to a list, js/taskselect.js) is in the row at once: the row is asked
// for again and wired again (initRow).

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

function initRow() {
const nav = document.querySelector('.tasks-places');
// On a phone the row scrolls sideways: the current pill is in view.
nav?.querySelector('.tasks-places-group [aria-current]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
const more = nav?.querySelector('.lists-more');

if (more) {
  const button = more.querySelector('summary');
  const filter = more.querySelector('.lists-more-filter');
  const items = [...more.querySelectorAll('.lists-more-item[data-list]')];

  // Escape closes it.
  more.addEventListener('keydown', e => {
    if (e.key !== 'Escape' || !more.open) return;
    e.preventDefault();
    e.stopPropagation();
    more.open = false;
    button.focus();
  });

  // The filter (with many lists): the lists whose name has what is typed;
  // Enter opens the first. All tasks stays.
  if (filter) {
    const apply = () => {
      const q = filter.value.trim().toLowerCase().replace(/^#/, '');
      for (const a of items) a.hidden = q !== '' && !a.dataset.list.toLowerCase().includes(q);
      // A group's word (Your lists, Other lists) goes with its last list.
      for (const head of more.querySelectorAll('.lists-more-head')) {
        let el = head.nextElementSibling, any = false;
        for (; el && el.matches('.lists-more-item[data-list]'); el = el.nextElementSibling) any = any || !el.hidden;
        head.hidden = !any;
      }
    };
    filter.addEventListener('input', apply);
    filter.addEventListener('keydown', e => {
      if (e.key !== 'Enter') return;
      const first = items.find(a => !a.hidden);
      if (first && filter.value.trim() !== '') { e.preventDefault(); first.click(); }
    });
    more.addEventListener('toggle', () => {
      if (!more.open) return;
      filter.value = '';
      apply();
      filter.focus();
    });
  }

  // Lists new to this browser. What it has shown is remembered by key
  // (localStorage, zone_lists_seen); a list under More that isn't among
  // them was made since, by someone else or an assistant (one made here is
  // pinned, and so a pill): "new" beside its name, a dot on More. Opening
  // More, or the list, makes it seen. The first visit knows them all.
  const pills = [...nav.querySelectorAll('a.tasks-place[data-place^="list:"]')].map(a => a.dataset.place);
  const under = items.map(a => a.dataset.place);
  let seen = readSeen();
  if (!seen) seen = new Set(under);
  for (const key of pills) seen.add(key);
  for (const a of items) if (a.hasAttribute('aria-current')) seen.add(a.dataset.place);
  const fresh = items.filter(a => !seen.has(a.dataset.place));
  for (const a of fresh) {
    const mark = document.createElement('span');
    mark.className = 'lists-more-new';
    mark.textContent = 'new';
    a.append(mark);
  }
  button.classList.toggle('has-new', fresh.length > 0);
  writeSeen(seen);
  more.addEventListener('toggle', () => {
    if (!more.open || !fresh.length) return;
    for (const key of under) seen.add(key);
    writeSeen(seen);
    button.classList.remove('has-new');   // the marks stay while the page does
  });
}

// Dragging a pinned list's pill puts the pins in the row's new order
// (POST /tasks/pin-order), for who may pin (data-reorder). With the mouse:
// a finger scrolls and taps here, and has Move first in the list's menu.
if (nav?.hasAttribute('data-reorder')) {
  const group = nav.querySelector('.tasks-places-group');
  const pills = () => [...group.querySelectorAll('a.tasks-place[data-place^="list:"]')];
  const order = () => pills().map(a => a.dataset.place.slice('list:'.length));
  let dragged = null, before = null, dropped = false;
  const restore = () => {
    const by = new Map(pills().map(a => [a.dataset.place.slice('list:'.length), a]));
    const anchor = pills().at(-1)?.nextSibling ?? null;
    for (const ref of before) group.insertBefore(by.get(ref), anchor);
  };
  if (pills().length > 1) {
    for (const a of pills()) {
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
        group.insertBefore(dragged, e.clientX < r.left + r.width / 2 ? a : a.nextSibling);
      });
      a.addEventListener('dragend', async () => {
        a.classList.remove('is-dragging');
        const now = order(), was = before;
        dragged = null;
        if (!dropped) { restore(); return; }          // let go outside the row: as it was
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
}
}
initRow();

// The list is one this browser has shown (it made it), so it isn't marked
// new under More; then the row as the server draws it now, in the old
// one's place (or at the sheet's top, where a first list's row goes).
document.addEventListener('zone:list-made', async e => {
  if (location.pathname !== '/tasks') return;
  const seen = readSeen();
  if (seen && e.detail?.ref) { seen.add('list:' + e.detail.ref); writeSeen(seen); }
  try {
    const res = await fetch(location.href, { headers: { Accept: 'text/html' } });
    if (!res.ok) return;
    const fresh = new DOMParser().parseFromString(await res.text(), 'text/html').querySelector('.tasks-places');
    if (!fresh) return;
    const now = document.importNode(fresh, true);
    const old = document.querySelector('.tasks-places');
    if (old) old.replaceWith(now);
    else document.querySelector('.sheet')?.prepend(now);
    initRow();
  } catch (_) {}
});

// A For field (New list…, Who it is for…): whom a shared list is for, groups
// (@dev) and people, commas or spaces between them. The browser's own
// completion (a datalist) takes the whole field for one word, so its
// choices are made here: what is typed so far, and each group and person
// not yet named that holds the last word. The words come with the page
// (data-words on the datalist); without this script the groups are offered
// for the first word.
const forWords = document.getElementById('list-for-words');
if (forWords) {
  const all = (forWords.dataset.words || '').split(' ').filter(Boolean);
  document.addEventListener('input', e => {
    if (e.target.getAttribute?.('list') !== 'list-for-words') return;
    const value = e.target.value;
    const cut = Math.max(value.lastIndexOf(','), value.lastIndexOf(' ')) + 1;
    const head = value.slice(0, cut), last = value.slice(cut).toLowerCase();
    const named = new Set(head.toLowerCase().split(/[\s,]+/).filter(Boolean));
    const lead = head && !/\s$/.test(head) ? head + ' ' : head;
    forWords.replaceChildren(...all
      .filter(w => !named.has(w) && (last ? w.replace(/^@/, '').startsWith(last.replace(/^@/, '')) && (!last.startsWith('@') || w.startsWith('@')) : true))
      .map(w => new Option('', lead + w)));
  });
}
