// ── Name fields: users and groups as pills ──────────────────────────────────
// A field that takes users or groups (data-pick="users" or "groups" on a
// text input; Groups and the admin page, docs/spec/users.md) takes only
// those there are: the names in the datalist the field's list names, less
// data-pick-not's. Each chosen name is a pill with × to take it out; typing
// offers the names that start with (then hold) what is typed, and Enter,
// Tab, a comma or a click picks the one marked. What matches no name stays
// in the box, marked, and the form isn't sent with it. The field itself
// becomes hidden and holds the names ("anna, bo"), so the form sends what
// it sent before; without JavaScript it is text, and the server refuses a
// name that is none.

let seq = 0;

for (const field of document.querySelectorAll('input[data-pick]')) setup(field);

function setup(field) {
  const list = document.getElementById(field.getAttribute('list') || '');
  const not = (field.dataset.pickNot || '').split(/\s+/).filter(Boolean);
  const all = list ? [...list.options].map(o => o.value).filter(v => v && !not.includes(v)) : [];
  const groups = field.dataset.pick === 'groups';
  const required = field.required;
  let chosen = names(field.value);

  const box = document.createElement('span');
  box.className = 'pick';
  const pills = document.createElement('span');
  pills.className = 'pick-pills';
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'pick-input';
  input.autocomplete = 'off';
  input.spellcheck = false;
  input.setAttribute('autocapitalize', 'none');
  input.setAttribute('role', 'combobox');
  input.setAttribute('aria-autocomplete', 'list');
  input.setAttribute('aria-expanded', 'false');
  const menu = document.createElement('ul');
  menu.className = 'pick-menu';
  menu.id = 'pick-menu-' + (++seq);
  menu.setAttribute('role', 'listbox');
  menu.hidden = true;
  input.setAttribute('aria-controls', menu.id);
  // Its name: the field's own, or its <label>'s words, goes to the box's
  // input (the hidden field keeps none).
  const name = field.getAttribute('aria-label') || field.labels?.[0]?.textContent.trim();
  if (name) input.setAttribute('aria-label', name);
  field.removeAttribute('aria-label');
  const placeholder = field.placeholder;
  const hadFocus = document.activeElement === field;   // focused before this ran: the box takes it
  field.type = 'hidden';
  field.removeAttribute('list');
  box.append(pills, input, menu);
  field.after(box);
  box.addEventListener('click', e => { if (e.target === box || e.target === pills) input.focus(); });

  let active = -1;   // the marked choice in the menu
  let shown = [];

  function names(text) {
    return [...new Set(text.split(/[\s,]+/).map(w => w.replace(/^@/, '').toLowerCase()).filter(Boolean))];
  }

  // The field holds the chosen names; who listens to it (the admin page's
  // Save, shown once a field is edited) hears of each change.
  function write() {
    const value = chosen.join(', ');
    if (field.value === value) return;
    field.value = value;
    field.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function draw() {
    pills.replaceChildren(...chosen.map(name => {
      const pill = document.createElement('span');
      pill.className = 'pick-pill';
      pill.dataset.name = name;
      const label = document.createElement('span');
      label.textContent = (groups ? '👥 ' : '') + name;
      const x = document.createElement('button');
      x.type = 'button';
      x.className = 'pick-remove';
      x.textContent = '×';
      x.setAttribute('aria-label', 'Take out ' + name);
      x.addEventListener('click', () => { remove(name); input.focus(); });
      pill.append(label, x);
      return pill;
    }));
    input.placeholder = chosen.length ? '' : placeholder;
    input.required = required && !chosen.length;
  }

  function remove(name) {
    chosen = chosen.filter(n => n !== name);
    draw();
    write();
    offer();
  }

  function pick(name) {
    if (!chosen.includes(name)) chosen.push(name);
    input.value = '';
    mark(false);
    draw();
    write();
    offer();
  }

  // What the menu offers: the names not chosen that start with what is
  // typed, then those that hold it; all of them for nothing typed.
  function offer() {
    const typed = input.value.trim().replace(/^@/, '').toLowerCase();
    const left = all.filter(n => !chosen.includes(n));
    shown = typed
      ? [...left.filter(n => n.startsWith(typed)), ...left.filter(n => !n.startsWith(typed) && n.includes(typed))]
      : left;
    shown = shown.slice(0, 8);
    active = typed && shown.length ? 0 : -1;
    menu.replaceChildren(...shown.map((name, i) => {
      const li = document.createElement('li');
      li.id = menu.id + '-' + i;
      li.setAttribute('role', 'option');
      li.className = 'pick-option';
      li.textContent = (groups ? '👥 ' : '') + name;
      li.addEventListener('mousedown', e => { e.preventDefault(); pick(name); });
      return li;
    }));
    if (typed && !shown.length) {
      const li = document.createElement('li');
      li.className = 'pick-none';
      li.textContent = groups ? 'No group called ' + typed : 'No user called ' + typed;
      menu.append(li);
    }
    const open = document.activeElement === input && menu.children.length > 0;
    menu.hidden = !open;
    input.setAttribute('aria-expanded', String(open));
    paint();
  }

  function paint() {
    [...menu.querySelectorAll('.pick-option')].forEach((li, i) => li.setAttribute('aria-selected', String(i === active)));
    if (active >= 0) input.setAttribute('aria-activedescendant', menu.id + '-' + active);
    else input.removeAttribute('aria-activedescendant');
  }

  // Typed text that names nobody is marked, and keeps the form from going.
  function mark(bad) {
    input.toggleAttribute('aria-invalid', bad);
    input.setCustomValidity(bad ? (groups ? 'Choose a group there is' : 'Choose a user of this wiki') : '');
  }

  // What is typed becomes a pill when it names one exactly, or the marked
  // choice when there is one; true if something was picked.
  function take(useMarked) {
    const typed = input.value.trim().replace(/^@/, '').toLowerCase();
    if (!typed) return false;
    if (all.includes(typed)) { pick(typed); return true; }
    if (useMarked && active >= 0) { pick(shown[active]); return true; }
    return false;
  }

  input.addEventListener('focus', offer);
  input.addEventListener('input', () => {
    mark(false);
    if (/[,\s]/.test(input.value)) {
      // A comma or space ends a name: one typed takes the marked choice, as
      // Enter does; several pasted are each taken when they name one, and
      // what names nobody stays.
      const parts = input.value.split(/[,\s]+/);
      const rest = parts.pop();
      const words = parts.filter(Boolean);
      if (words.length === 1 && !rest) {
        input.value = words[0];
        offer();
        if (!take(true)) mark(true);
        return;
      }
      const bad = [];
      for (const w of words) {
        const n = w.replace(/^@/, '').toLowerCase();
        if (all.includes(n)) { if (!chosen.includes(n)) chosen.push(n); } else bad.push(w);
      }
      draw();
      write();
      input.value = [...bad, rest].filter(Boolean).join(' ');
      if (bad.length) mark(true);
    }
    offer();
  });
  input.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (!shown.length) return;
      e.preventDefault();
      if (menu.hidden) { menu.hidden = false; input.setAttribute('aria-expanded', 'true'); }
      active = e.key === 'ArrowDown' ? (active + 1) % shown.length : (active <= 0 ? shown.length : active) - 1;
      paint();
    } else if (e.key === 'Enter') {
      if (input.value.trim()) {                // a name first; with none typed Enter sends the form
        e.preventDefault();
        if (!take(true)) mark(true);
      } else if (active >= 0 && !menu.hidden) {
        e.preventDefault();
        pick(shown[active]);
      }
    } else if (e.key === 'Tab') {
      if (input.value.trim() && !take(true)) mark(true);
    } else if (e.key === 'Backspace' && !input.value && chosen.length) {
      remove(chosen[chosen.length - 1]);
    } else if (e.key === 'Escape' && !menu.hidden) {
      e.preventDefault();
      e.stopPropagation();
      menu.hidden = true;
      input.setAttribute('aria-expanded', 'false');
    }
  });
  input.addEventListener('blur', () => {
    menu.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    if (input.value.trim() && !take(false)) mark(true);
  });
  // Sent with a name typed that is none: not sent, and the box says why.
  field.form?.addEventListener('submit', e => {
    if (input.value.trim() && !take(false)) {
      mark(true);
      e.preventDefault();
      input.reportValidity();
    }
  });

  draw();
  if (hadFocus) input.focus();
}
