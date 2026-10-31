import { closeOnBackdropClick, copyText, toSlug } from './util.js';

// ── Page delete dialog ────────────────────────────────────────────────────────

document.getElementById('page-delete-cancel')?.addEventListener('click', () => {
  document.getElementById('page-delete-dialog').close();
});
closeOnBackdropClick(document.getElementById('page-delete-dialog'));

// mdtext.promoted_slug: where a scratch note moved to the wiki goes.
function promotedSlug(folder, name, date) {
  let n = toSlug(name.replace(/\//g, '-'));
  if (!n) return '';
  if (date) n = `${date}_${n}`;
  return [toSlug(folder), n].filter(Boolean).join('/');
}

// The editor's hint after Move to wiki (views.paste_hint, templates/partials/page.html): Copy puts
// the link in the clipboard (again), × closes it; either way it is gone
// from the URL, so a reload doesn't bring it back.
(function () {
  const hint = document.querySelector('.paste-hint');
  if (!hint) return;
  const url = new URL(location.href);
  url.searchParams.delete('paste');
  url.searchParams.delete('copied');
  history.replaceState(null, '', url);
  const copy = hint.querySelector('.paste-hint-copy');
  copy.addEventListener('click', async () => {
    copy.textContent = await copyText(hint.dataset.link) ? 'Copied ✓' : 'Copy failed';
    document.getElementById('editor-textarea')?.focus();
  });
  hint.querySelector('.paste-hint-close').addEventListener('click', () => {
    hint.remove();
    document.getElementById('editor-textarea')?.focus();
  });
}());

// ── Page ⋯ menu ───────────────────────────────────────────────────────────────
// Present, History, Rename…, Delete… on a page view. One menu (#page-menu,
// rendered hidden) for both ⋯ buttons, the header's and the right column's:
// a popover under the pressed one, a bottom sheet on phones (the tasks'
// .task-menu styles). ↑/↓ move, Esc closes and returns focus to the ⋯.

(function () {
  const menu = document.getElementById('page-menu');
  const backdrop = document.querySelector('.page-menu-backdrop');
  if (!menu || !backdrop) return;
  let opener = null;
  const items = () => [...menu.querySelectorAll('[role="menuitem"]')].filter(el => !el.hidden);

  function close(refocus) {
    if (menu.hidden) return;
    menu.hidden = backdrop.hidden = true;
    document.removeEventListener('keydown', onKey, true);
    opener?.setAttribute('aria-expanded', 'false');
    if (refocus) opener?.focus();
    opener = null;
  }
  function open(btn) {
    opener = btn;
    menu.hidden = backdrop.hidden = false;
    btn.setAttribute('aria-expanded', 'true');
    // Under the button, right-aligned, flipped up if it doesn't fit below.
    const r = btn.getBoundingClientRect(), m = menu.getBoundingClientRect();
    const top = r.bottom + 4 + m.height > window.innerHeight ? r.top - 4 - m.height : r.bottom + 4;
    menu.style.top = `${Math.max(4, top)}px`;
    menu.style.left = `${Math.max(4, Math.min(r.right - m.width, window.innerWidth - m.width - 4))}px`;
    document.addEventListener('keydown', onKey, true);
    items()[0]?.focus({ preventScroll: true });
  }
  function onKey(e) {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(true); }
    else if (e.key === 'Tab') close(false);
    else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault(); e.stopPropagation();
      const list = items(), i = list.indexOf(document.activeElement);
      list[(i + (e.key === 'ArrowDown' ? 1 : -1) + list.length) % list.length]?.focus();
    }
  }

  document.querySelectorAll('.page-menu-btn').forEach(btn => btn.addEventListener('click', () => {
    const again = opener === btn;
    close(false);
    if (!again) open(btn);
  }));
  backdrop.addEventListener('click', () => close(false));
  menu.addEventListener('click', e => {
    const item = e.target.closest('[role="menuitem"]');
    if (!item) return;
    close(false);
    if (item.dataset.action === 'delete') document.getElementById('page-delete-dialog')?.showModal();
    else if (item.dataset.action === 'rename') openRename();
    else if (item.dataset.action === 'privacy') document.getElementById('page-privacy-dialog')?.showModal();
    else if (item.dataset.action === 'promote') openPromote();
    else if (item.dataset.action === 'append') openAppend();
  });

  // Move to wiki (scratch notes): the slug the page will get, as the server
  // makes it (promoted_slug), shown while typing.
  const promote = document.getElementById('page-promote-dialog');
  function openPromote() {
    if (!promote) return;
    promote.showModal();
    const name = document.getElementById('promote-name');
    name.focus();
    name.select();
  }
  if (promote) {
    const preview = document.getElementById('promote-slug');
    const fields = ['promote-folder', 'promote-name', 'promote-keep-date'].map(id => document.getElementById(id));
    const [folder, name, keepDate] = fields;
    // Link from: the folder's index page while it has one (the home page
    // for no folder), until edited.
    const linkFrom = document.getElementById('promote-link-from');
    const pages = new Set([...document.querySelectorAll('#wiki-pages option')].map(o => o.value));
    linkFrom.addEventListener('input', () => { linkFrom.dataset.edited = '1'; });
    const update = () => {
      preview.textContent = promotedSlug(folder.value, name.value,
                                         keepDate.checked ? keepDate.value : '') || '…';
      if (!linkFrom.dataset.edited) {
        const dir = toSlug(folder.value);
        const index = dir ? `${dir}/index` : 'index';
        linkFrom.value = pages.has(index) ? index : '';
      }
    };
    fields.forEach(f => f.addEventListener('input', update));
    update();
    // With a page to link from, Move copies the new page's link first: the
    // editor opens on that page to paste it (mdtext.page_wikilink).
    const form = promote.querySelector('form');
    form.addEventListener('submit', async e => {
      if (!linkFrom.value.trim() || form.dataset.copying) return;
      e.preventDefault();
      form.dataset.copying = '1';
      const slug = promotedSlug(folder.value, name.value, keepDate.checked ? keepDate.value : '');
      const label = promote.dataset.title.replace(/[[\]|]/g, '').trim() || slug;
      const ok = await copyText(`[[${slug}|${label}]]`);
      document.getElementById('promote-copied').value = ok ? '1' : '';
      form.submit();
    });
    document.getElementById('page-promote-cancel').addEventListener('click', () => promote.close());
    closeOnBackdropClick(promote);
    reopenWithError(promote, openPromote, 'promote_error');
  }

  // Append to page (scratch notes): the page, then the section's heading.
  const append = document.getElementById('page-append-dialog');
  function openAppend() {
    if (!append) return;
    append.showModal();
    document.getElementById('append-page').focus();
  }
  if (append) {
    document.getElementById('page-append-cancel').addEventListener('click', () => append.close());
    closeOnBackdropClick(append);
    reopenWithError(append, openAppend, 'append_error');
  }

  // A refused move comes back to the note with the error: show it in its
  // dialog, and drop it from the URL.
  function reopenWithError(dialog, open, param) {
    if (!dialog.hasAttribute('data-open')) return;
    open();
    const url = new URL(location.href);
    url.searchParams.delete(param);
    history.replaceState(null, '', url);
  }

  // Make private / Make shared: the confirmation (a rename into or out of the folder).
  const privacy = document.getElementById('page-privacy-dialog');
  if (privacy) {
    document.getElementById('page-privacy-cancel').addEventListener('click', () => privacy.close());
    closeOnBackdropClick(privacy);
  }

  const rename = document.getElementById('page-rename-dialog');
  const renameInput = document.getElementById('page-rename-input');
  function openRename() {
    if (!rename) return;
    rename.showModal();
    renameInput.focus();
    renameInput.select();
  }
  if (rename) {
    document.getElementById('page-rename-cancel').addEventListener('click', () => rename.close());
    closeOnBackdropClick(rename);
    // A refused rename comes back here with the error: show it in the dialog.
    if (rename.hasAttribute('data-open')) {
      openRename();
      const url = new URL(location.href);
      for (const name of ['rename_error', 'rename_name']) url.searchParams.delete(name);
      history.replaceState(null, '', url);
    }
  }
}());
