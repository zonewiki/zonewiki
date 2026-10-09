import { closeOnBackdropClick } from './util.js';

// ── Raw page paste handler ────────────────────────────────────────────────────

(function () {
  if (window.location.pathname !== '/page/_raw') return;

  const article = document.querySelector('.page-content');
  if (article) {
    const hint = document.createElement('div');
    hint.className = 'raw-hint';
    hint.textContent = 'Paste a URL or text to add it as a list item.';
    article.appendChild(hint);
  }

  // Build title prompt modal
  const modal = document.createElement('dialog');
  modal.className = 'scratch-modal';
  modal.innerHTML =
    '<p class="scratch-label" id="raw-modal-label">Title</p>' +
    '<input type="text" id="raw-title-input" autocomplete="off" spellcheck="false">' +
    '<div class="scratch-actions">' +
    '<button type="button" class="btn btn-primary" id="raw-modal-confirm">Add</button>' +
    '<button type="button" class="btn" id="raw-modal-cancel">Cancel</button>' +
    '</div>';
  document.body.appendChild(modal);
  closeOnBackdropClick(modal);

  const titleInput  = document.getElementById('raw-title-input');
  const confirmBtn  = document.getElementById('raw-modal-confirm');
  const cancelBtn   = document.getElementById('raw-modal-cancel');

  function askTitle(placeholder) {
    return new Promise(resolve => {
      titleInput.value = '';
      titleInput.placeholder = placeholder;
      modal.showModal();
      titleInput.focus();

      function done(value) {
        confirmBtn.onclick = null;
        cancelBtn.onclick  = null;
        titleInput.onkeydown = null;
        modal.close();
        resolve(value);
      }

      confirmBtn.onclick   = () => done(titleInput.value.trim());
      cancelBtn.onclick    = () => done(null);
      titleInput.onkeydown = (e) => {
        if (e.key === 'Enter')  { e.preventDefault(); done(titleInput.value.trim()); }
        if (e.key === 'Escape') { e.preventDefault(); done(null); }
      };
      modal.addEventListener('close', () => resolve(null), { once: true });
    });
  }

  function showRawToast(msgHtml, isError) {
    const t = document.createElement('div');
    t.className = 'raw-toast' + (isError ? ' raw-toast-error' : '');
    t.innerHTML = msgHtml;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 6000);
  }

  document.addEventListener('paste', async (e) => {
    if (e.target.matches('input,textarea,select,[contenteditable]')) return;
    const text = (e.clipboardData.getData('text/plain') || '').trim();
    if (!text) return;
    e.preventDefault();

    const isUrl = /^https?:\/\/\S+$/.test(text);
    const title = await askTitle(isUrl ? 'Link title…' : 'Snippet title…');
    if (title === null) return;

    const data = new FormData();
    data.append('title', title);

    try {
      if (isUrl) {
        data.append('url', text);
        await fetch('/api/raw/append-url', { method: 'POST', body: data });
      } else {
        data.append('text', text);
        await fetch('/api/raw/create-page', { method: 'POST', body: data });
      }
      window.location.reload();
    } catch (_) {
      showRawToast('Error saving — please try again.', true);
    }
  });
}());
