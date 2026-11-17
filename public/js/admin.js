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

// A row's Change and Set show once its field says something else than the
// file does: an untouched list is names, roles and groups, no buttons. The
// button keeps its place (hidden, not gone), so the table doesn't move, and
// a change still takes a second step: picking a role saves nothing by
// itself. Enter in a field saves as before. Without JavaScript the buttons
// are there all along.
for (const form of document.querySelectorAll('.user-role-form')) {
  const field = form.querySelector('select, input[type=text]');
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
