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
