
// ── Helpers ───────────────────────────────────────────────────────────────────

export function closeOnBackdropClick(dialog) {
  dialog?.addEventListener('click', e => {
    if (e.target === dialog) dialog.close();
  });
}

// The first key of a two-key shortcut (g or c, shortcuts.js) while it waits
// for the second, else null. The deck's keydown runs before the page's (deck.js
// is imported first), so it leaves that second key alone: the t of c t isn't
// the deck's t (tag the selected tasks).
export const keyPrefix = { key: null, timer: null };

export function he(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// A day as YYYY-MM-DD in local time (toISOString() would give UTC's day,
// yesterday's shortly after midnight in Europe).
export const localDate = (d = new Date()) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

// Prompt for a display name when pasting a bare URL. Resolves with the name,
// null (no name — insert plain URL), or false (cancelled). Shared with deck.js.
function askUrlName(url) {
  const urlNameModal   = document.getElementById('url-name-modal');
  const urlNameHref    = document.getElementById('url-name-href');
  const urlNameInput   = document.getElementById('url-name-input');
  const urlNameConfirm = document.getElementById('url-name-confirm');
  const urlNameCancel  = document.getElementById('url-name-cancel');
  return new Promise(resolve => {
    urlNameHref.textContent = url;
    urlNameInput.value = '';
    urlNameModal.showModal();
    urlNameInput.focus();

    function done(result) {
      urlNameModal.close();
      urlNameConfirm.onclick = null;
      urlNameCancel.onclick = null;
      urlNameInput.onkeydown = null;
      resolve(result);
    }
    urlNameConfirm.onclick = () => done(urlNameInput.value.trim() || null);
    urlNameCancel.onclick  = () => done(false);
    urlNameInput.onkeydown = e => {
      if (e.key === 'Enter') { e.preventDefault(); done(urlNameInput.value.trim() || null); }
    };
  });
}
closeOnBackdropClick(document.getElementById('url-name-modal'));

// Pasting a bare URL into a markdown textarea (page editor, deck notes):
// selected text becomes [selection](url); otherwise the name prompt asks for
// a label (none, or cancel, inserts the plain URL). The insertion goes through
// execCommand('insertText') so it is one step on the native undo stack and
// fires `input` (preview, auto-save). Returns false if the paste is no URL.
export async function pasteUrlAsLink(ta, e) {
  const url = (e.clipboardData.getData('text/plain') || '').trim();
  if (!/^https?:\/\/\S+$/.test(url)) return false;
  e.preventDefault();
  const start = ta.selectionStart, end = ta.selectionEnd;
  let md;
  if (start !== end) {
    md = `[${ta.value.slice(start, end)}](${url})`;
  } else {
    const name = await askUrlName(url);
    md = name ? `[${name}](${url})` : url;
    ta.focus();                       // the prompt took focus and selection
    ta.setSelectionRange(start, end);
  }
  document.execCommand('insertText', false, md);
  return true;
}

// Mirror of server-side to_slug(): lowercase, non [a-z0-9_] runs → '-',
// '/' preserved as a path separator. Keep the two in sync.
export function toSlug(name) {
  return name.trim().split('/')
    .map(p => p.trim().toLowerCase().replace(/[^a-z0-9_]+/g, '-').replace(/^-+|-+$/g, ''))
    .filter(Boolean)
    .join('/');
}

// A heading's id as rendered (mdtext.py heading_id, mdit-py-plugins'
// anchors slugify): no punctuation, lowercase, spaces → hyphens.
const headingId = text => text.replace(/[^\p{L}\p{N}\p{M}_\- ]+/gu, '').trim().toLowerCase().replace(/ /g, '-');

// A wikilink's inside → { href, label }, as mdtext.py split_wikilink and
// process_wikilinks: `Page`, `Page|Label`, `Page#Section` (that heading),
// `#Section` (a heading on this page).
export function wikilinkHref(inner) {
  const bar = inner.indexOf('|');
  const target = bar < 0 ? inner : inner.slice(0, bar);
  const hash = target.indexOf('#');
  const page = (hash < 0 ? target : target.slice(0, hash)).trim();
  const section = hash < 0 ? '' : target.slice(hash + 1).trim();
  const label = bar >= 0 ? inner.slice(bar + 1).trim()
    : page && section ? `${page} › ${section}` : section || page;
  const anchor = section ? '#' + headingId(section) : '';
  return { href: page ? `/page/${toSlug(page)}${anchor}` : anchor || '#', label };
}

// ── List continuation ─────────────────────────────────────────────────────────

// Enter at the end of a list item starts the next one (a checkbox item gets
// an unchecked box, a number counts up); Enter on an empty item ends the
// list. Returns true when it handled the key. Shared with the deck's task
// notes (deck.js), so it takes the textarea.
export function continueList(ta, e) {
  const pos = ta.selectionStart;
  if (pos !== ta.selectionEnd) return false;
  const val = ta.value;
  const lineStart = val.lastIndexOf('\n', pos - 1) + 1;
  const line = val.slice(lineStart, pos);

  const cb       = line.match(/^(\s*)([-*+]) \[[xX ]\] (.+)/);
  const cbEmpty  = line.match(/^(\s*)([-*+]) \[[xX ]\] $/);
  const ocb      = line.match(/^(\s*)(\d+)\. \[[xX ]\] (.+)/);
  const ocbEmpty = line.match(/^(\s*)(\d+)\. \[[xX ]\] $/);
  const ul       = line.match(/^(\s*)([-*+]) (.+)/);
  const ulEmpty  = line.match(/^(\s*)([-*+]) $/);
  const ol       = line.match(/^(\s*)(\d+)\. (.+)/);
  const olEmpty  = line.match(/^(\s*)(\d+)\. $/);

  if (cbEmpty || ocbEmpty) {
    // Empty checkbox item → exit list
    e.preventDefault();
    ta.setSelectionRange(lineStart, pos);
    document.execCommand('insertText', false, '\n');
  } else if (cb) {
    e.preventDefault();
    document.execCommand('insertText', false, '\n' + cb[1] + cb[2] + ' [ ] ');
  } else if (ocb) {
    e.preventDefault();
    document.execCommand('insertText', false, '\n' + ocb[1] + (parseInt(ocb[2]) + 1) + '. [ ] ');
  } else if (ulEmpty || olEmpty) {
    // Empty list item → exit list
    e.preventDefault();
    ta.setSelectionRange(lineStart, pos);
    document.execCommand('insertText', false, '\n');
  } else if (ul) {
    e.preventDefault();
    document.execCommand('insertText', false, '\n' + ul[1] + ul[2] + ' ');
  } else if (ol) {
    e.preventDefault();
    document.execCommand('insertText', false, '\n' + ol[1] + (parseInt(ol[2]) + 1) + '. ');
  } else {
    return false;
  }
  return true;
}

// Put text in the clipboard; false when the browser won't. The Clipboard
// API needs a secure context (plain http on the LAN isn't), so without it
// the old copy command, which works during a user's click.
export async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (_) {}
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  ta.style.cssText = 'position:fixed;opacity:0;top:0;left:0';
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (_) {}
  ta.remove();
  return ok;
}

// A short message at the bottom with an optional Undo; one at a time.
let toastEl = null;
export function showToast(msg, undo) {
  toastEl?.remove();
  const el = document.createElement('div');
  el.className = 'raw-toast task-undo-toast';
  const span = document.createElement('span');
  span.textContent = msg;
  el.appendChild(span);
  if (undo) {
    const btn = document.createElement('button');
    btn.className = 'btn task-undo-btn';
    btn.textContent = 'Undo';
    btn.addEventListener('click', () => { el.remove(); undo(); });
    el.appendChild(btn);
  }
  document.body.appendChild(el);
  toastEl = el;
  setTimeout(() => el.remove(), 6000);
}

// PUT fields of a deck task (the task chips here, deck.js's batched writes).
export function putTask(id, body) {
  return fetch(`/api/deck/${encodeURIComponent(id)}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
}
