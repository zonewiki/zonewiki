// ── Lines to copy ────────────────────────────────────────────────────────────
// A .copy-line (Connect an assistant: the address, a token, a command) is a
// <code> with a Copy button, which the page sends hidden: without JavaScript
// a click on the line still selects it.

import { copyText } from './util.js';

for (const line of document.querySelectorAll('.copy-line')) {
  const button = line.querySelector('.copy-btn');
  if (!button) continue;
  button.hidden = false;
  let back = 0;
  button.addEventListener('click', async () => {
    button.textContent = await copyText(line.querySelector('code').textContent) ? 'Copied ✓' : 'Copy failed';
    clearTimeout(back);
    back = setTimeout(() => { button.textContent = 'Copy'; }, 1500);
  });
}
