// The Tasks page's row of lists (.tasks-places in tasks_page.html): the
// pinned lists as pills and More, a details holding the other lists and All
// tasks. Here: More closes as a menu does and can be filtered by name; a
// list this browser hasn't shown before is marked new; and the pinned pills
// can be dragged into another order (the first is where a new task goes).

const nav = document.querySelector('.tasks-places');
const more = nav?.querySelector('.lists-more');

if (more) {
  const button = more.querySelector('summary');
  const filter = more.querySelector('.lists-more-filter');
  const items = [...more.querySelectorAll('.lists-more-item[data-list]')];

  // A click elsewhere and Escape close it.
  document.addEventListener('click', e => { if (more.open && !more.contains(e.target)) more.open = false; });
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
  const KEY = 'zone_lists_seen';
  const read = () => {
    try { const v = JSON.parse(localStorage.getItem(KEY)); return Array.isArray(v) ? new Set(v) : null; } catch (_) { return null; }
  };
  const write = seen => { try { localStorage.setItem(KEY, JSON.stringify([...seen])); } catch (_) {} };
  const pills = [...nav.querySelectorAll('a.tasks-place[data-place^="list:"]')].map(a => a.dataset.place);
  const under = items.map(a => a.dataset.place);
  let seen = read();
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
  write(seen);
  more.addEventListener('toggle', () => {
    if (!more.open || !fresh.length) return;
    for (const key of under) seen.add(key);
    write(seen);
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
          location.reload();                           // the first pill's mark, where New task goes
        } catch (_) { before = was; restore(); }
      });
    }
    group.addEventListener('dragover', e => { if (dragged) e.preventDefault(); });
    group.addEventListener('drop', e => { if (dragged) { e.preventDefault(); dropped = true; } });
  }
}
