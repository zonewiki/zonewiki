// ── The admin page ───────────────────────────────────────────────────────────
// Users tab (templates/users.html): Remove… asks in a dialog, as deleting a
// page does. Each row's <details> holds the question and the form; this
// opens the dialog with them instead of unfolding it, and without
// JavaScript the <details> still asks.

import { closeOnBackdropClick } from './util.js';

const dialog = document.getElementById('user-remove-dialog');
if (dialog) {
  const desc = dialog.querySelector('.modal-desc');
  const form = dialog.querySelector('form');
  const confirm = form.querySelector('[type=submit]');
  for (const details of document.querySelectorAll('.user-remove')) {
    details.querySelector('summary').addEventListener('click', e => {
      e.preventDefault();
      const rowForm = details.querySelector('form');
      desc.replaceChildren(...details.querySelector('p').cloneNode(true).childNodes);
      form.action = rowForm.action;
      confirm.textContent = rowForm.querySelector('button').textContent;
      dialog.showModal();
    });
  }
  document.getElementById('user-remove-cancel').addEventListener('click', () => dialog.close());
  closeOnBackdropClick(dialog);
}

// A row's Save shows once its field says something else than the file
// does: an untouched list is names, roles and groups, no buttons. The
// button keeps its place (hidden, not gone), so the table doesn't move, and
// a change still takes a second step: picking a role saves nothing by
// itself. Enter in a field saves as before. Without JavaScript the buttons
// are there all along. The Groups field is pills (js/namepick.js): its
// value is the hidden field's, which says when it changes.
for (const form of document.querySelectorAll('.user-role-form')) {
  const field = form.querySelector('select, input[data-pick], input[type=text], input[type=email]');
  const button = form.querySelector('[type=submit]');
  if (!field || !button) continue;
  const saved = field.tagName === 'SELECT'
    ? [...field.options].find(o => o.defaultSelected)?.value ?? field.value
    : field.defaultValue;
  const show = () => form.classList.toggle('edited', field.value.trim() !== saved.trim());
  form.classList.add('saves-when-edited');
  field.addEventListener('input', show);
  field.addEventListener('change', show);
  show();   // a value the browser kept from before a reload
}

// Taking someone out of a group takes its pages and lists from them at once,
// as leaving one does (which asks): a Save that takes names out says whom
// on its button first, and the second click saves. People of a group
// (Groups: Manage…), a user's groups (the admin page).
for (const field of document.querySelectorAll('.group-form input[name="people"], .user-groups-form input[name="groups"]')) {
  const form = field.form, button = form?.querySelector('[type=submit]');
  if (!form || !button) continue;
  const names = text => text.split(/[\s,]+/).map(n => n.replace(/^@/, '').toLowerCase()).filter(Boolean);
  const was = names(field.defaultValue), label = button.textContent, spoken = button.getAttribute('aria-label');
  const people = field.name === 'people', primary = button.classList.contains('btn-primary');
  let asked = '';
  const reset = () => {
    asked = '';
    button.textContent = label;
    button.classList.remove('btn-danger');
    button.classList.toggle('btn-primary', primary);
    if (spoken) button.setAttribute('aria-label', spoken);
  };
  form.addEventListener('input', reset);
  form.addEventListener('change', reset);
  form.addEventListener('submit', e => {
    if (e.defaultPrevented) return;
    const now = new Set(names(field.value)), gone = was.filter(n => !now.has(n));
    if (!gone.length || asked === gone.join(',')) return;
    e.preventDefault();
    asked = gone.join(',');
    const who = form.action.match(/\/admin\/users\/([^/]+)\/groups$/)?.[1];
    button.textContent = people ? `Yes, take out ${gone.join(', ')}` : `Yes, take ${decodeURIComponent(who || 'them')} out of ${gone.join(', ')}`;
    if (spoken) button.setAttribute('aria-label', button.textContent);
    button.classList.remove('btn-primary');   // its white text would be lost on the danger button's ground
    button.classList.add('btn-danger');
    form.classList.add('edited');
  });
}

// Wiki tab (templates/admin_wiki.html): the timezone field can take this
// browser's own, which is the admin's guess more often than not. The
// button names it, and fills the field; Save is still the admin's.
const zoneButton = document.querySelector('[data-browser-zone]');
if (zoneButton) {
  const field = zoneButton.form.elements.timezone;
  let mine = '';
  try { mine = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch { /* no button */ }
  if (mine && mine !== field.value) {
    zoneButton.textContent = `Use this browser's: ${mine}`;
    zoneButton.hidden = false;
    zoneButton.addEventListener('click', () => {
      field.value = mine;
      zoneButton.hidden = true;
      field.focus();
    });
  }
}
