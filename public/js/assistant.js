// ── Connect an assistant ─────────────────────────────────────────────────────
// A connection's Change… (templates/assistant.html): the row's <details>
// holds its form, which this opens in the page's dialog instead of
// unfolding it in the table; without JavaScript the <details> unfolds.
// The password field shows only while a choice gives the connection more
// than it has (a choice it has now is marked data-had): taking away needs
// none. The server decides that all the same (assistantChangeRoute).

import { closeOnBackdropClick } from './util.js';

const dialog = document.getElementById('connection-change-dialog');
if (dialog) {
  const label = dialog.querySelector('.modal-label');
  let shown = null; // the <details> whose form is in the dialog

  const open = details => {
    const form = details.querySelector('form');
    label.textContent = `Change ${form.dataset.client}`;
    dialog.append(form);
    shown = details;
    dialog.showModal();
  };
  // Closed, the form goes back to its row, as it was (a choice not saved
  // isn't kept).
  dialog.addEventListener('close', () => {
    if (!shown) return;
    const form = dialog.querySelector('form');
    form.reset();
    form.dispatchEvent(new Event('change'));
    shown.append(form);
    shown.open = false;
    shown = null;
  });
  closeOnBackdropClick(dialog);

  for (const details of document.querySelectorAll('.assistant-change')) {
    const form = details.querySelector('form');
    const password = form.querySelector('.assistant-change-password');
    const field = password.querySelector('input[type=password]');
    const gives = () => [...form.querySelectorAll('input[name=may][value=write], input[name=see]')]
      .some(input => input.checked && !('had' in input.dataset));
    const update = () => {
      const more = gives();
      password.hidden = !more;
      field.required = more;
    };
    form.addEventListener('change', update);
    update();
    const cancel = form.querySelector('[data-close]');
    cancel.hidden = false;
    cancel.addEventListener('click', () => dialog.close());
    details.querySelector('summary').addEventListener('click', e => {
      e.preventDefault();
      open(details);
    });
    // Back from a change refused (a wrong password): open again, as chosen.
    if (details.open) {
      details.open = false;
      open(details);
      if (!password.hidden) field.focus();
    }
  }
}
