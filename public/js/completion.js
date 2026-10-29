import { sharingOn } from './actions.js';
import { toSlug } from './util.js';

// ── Link completion ───────────────────────────────────────────────────────────
// In a text field, `[[` offers pages, `[[task:` open deck tasks (with
// `tasks: true`, the editor) and `[text](` pages as /page/ links. One
// implementation for the editor and the deck's task fields (deck.js). `page`
// (its slug, or a function giving it; '' for text that is on no page, such as
// a shared task's; null: unknown) says where the text is. On a page in a private folder `[[` also offers a new
// page in that folder: wikilinks are absolute, so the private page is named
// in full, `[[private/alice/idea|Idea]]`. On a shared page private pages
// aren't offered at all: others couldn't follow a link to one.

let wikiPages = null;   // page slugs, fetched once when first needed

const fields = [];      // the update() of every field with completion

function loadWikiPages() {
  if (wikiPages) return;
  wikiPages = [];
  fetch('/api/pages').then(r => r.json()).then(p => {
    wikiPages = p;
    // `[[` typed before the list arrived: offer it now.
    fields.forEach(update => update(true));
  }).catch(() => {});
}

// Open tasks for `[[task:` completion, fetched when first needed and again
// when older than a minute (tasks change in other tabs); `onLoad` runs when
// a fetch it started has come back.
let acTasks = null, acTasksAt = 0, acTasksLoading = false;

function taskAcMatches(query, onLoad) {
  if (!acTasks || Date.now() - acTasksAt > 60000) {
    if (!acTasksLoading) {
      acTasksLoading = true;
      fetch('/api/deck').then(r => r.json()).then(tasks => {
        acTasks = tasks; acTasksAt = Date.now();
      }).catch(() => {}).finally(() => { acTasksLoading = false; onLoad(); });
    }
    if (!acTasks) return [];
  }
  const q = query.trim().toLowerCase();
  const words = q.split(/\s+/).filter(Boolean);
  return acTasks
    .filter(t => t.id.startsWith(q) || words.every(w => t.title.toLowerCase().includes(w)))
    .sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''))
    .slice(0, 8)
    .map(t => ({
      value: t.id.slice(0, 8),
      label: t.title,
      meta: [t.list, t.due_date && `⏰ ${t.due_date}`].filter(Boolean).join(' · '),
    }));
}

// Emoji for `:name` completion: GitHub's gemoji table (vendored, MIT),
// fetched when first needed; `onLoad` runs when a fetch it started is back.
let emojiList = null, emojiLoading = false;

function loadEmoji(onLoad) {
  if (emojiList || emojiLoading) return;
  emojiLoading = true;
  fetch('/public/vendor/gemoji/emoji.json').then(r => r.json()).then(list => {
    emojiList = list;
  }).catch(() => {}).finally(() => { emojiLoading = false; onLoad(); });
}

// The emoji a shortcode names exactly (`+1`, `tada`), once the table is in.
function emojiNamed(name) {
  const n = name.toLowerCase();
  return emojiList?.find(e => e.aliases.includes(n))?.emoji || null;
}

// Best first: the exact name, one that starts with the query, then one
// containing it, then a tag (`:happy` finds 😀) or the description.
function emojiMatches(query, onLoad) {
  if (!emojiList) { loadEmoji(onLoad); return []; }
  const q = query.toLowerCase();
  const rank = e =>
    e.aliases.includes(q) ? 0
    : e.aliases.some(a => a.startsWith(q)) ? 1
    : e.aliases.some(a => a.includes(q)) ? 2
    : e.tags.some(t => t.startsWith(q)) || e.description.startsWith(q) ? 3
    : e.description.includes(q) ? 4 : 5;
  return emojiList
    .map((e, i) => ({ e, r: rank(e), i }))
    .filter(x => x.r < 5)
    .sort((a, b) => a.r - b.r || a.i - b.i)
    .slice(0, 8)
    .map(({ e }) => {
      const name = e.aliases.find(a => a.startsWith(q)) || e.aliases.find(a => a.includes(q)) || e.aliases[0];
      return { value: e.emoji, label: `${e.emoji}  ${name}`, meta: e.description };
    });
}

// Inside code (a fence above the caret, or an open ` on its line): no emoji.
function inCode(before) {
  const fences = (before.match(/^ {0,3}(```|~~~)/gm) || []).length;
  const line = before.slice(before.lastIndexOf('\n') + 1);
  return fences % 2 === 1 || (line.match(/`/g) || []).length % 2 === 1;
}

// Measure approximate caret position inside textarea using a mirror div
function getCaretCoords(el) {
  const computed = window.getComputedStyle(el);
  const mirror = document.createElement('div');
  Object.assign(mirror.style, {
    position: 'absolute', visibility: 'hidden', whiteSpace: 'pre-wrap',
    wordWrap: 'break-word', overflow: 'hidden',
    boxSizing: computed.boxSizing,
    width: computed.width,
    paddingTop: computed.paddingTop, paddingRight: computed.paddingRight,
    paddingBottom: computed.paddingBottom, paddingLeft: computed.paddingLeft,
    borderTopWidth: computed.borderTopWidth, borderLeftWidth: computed.borderLeftWidth,
    fontFamily: computed.fontFamily, fontSize: computed.fontSize,
    fontWeight: computed.fontWeight, lineHeight: computed.lineHeight,
    letterSpacing: computed.letterSpacing,
  });
  document.body.appendChild(mirror);
  mirror.textContent = el.value.slice(0, el.selectionStart);
  const span = document.createElement('span');
  span.textContent = '\u200b';
  mirror.appendChild(span);
  const coords = { top: span.offsetTop, left: span.offsetLeft };
  document.body.removeChild(mirror);
  return coords;
}

// Attach link completion to a textarea or input. Keys: ↑/↓ move, Enter or
// Tab insert (the event's default is prevented, so later handlers can skip
// it), Esc closes. Returns { update }, for text inserted by script.
// `:name` (two letters or more, after a space or at the start) offers
// emoji, and a whole `:name:` turns into its emoji: the file holds the
// character itself, not the shortcode.
export function linkAutocomplete(ta, { tasks = false, page = null } = {}) {
  loadWikiPages();
  let el = null, index = 0, mode = 'wikilink';

  // [kind, query] at the caret, or [null] outside a link.
  function context() {
    const before = ta.value.slice(0, ta.selectionStart);
    const w = before.match(/\[\[([^\][\n]*)$/);
    if (w) return tasks && /^task:/i.test(w[1]) ? ['task', w[1].slice(5)] : ['wikilink', w[1]];
    // [label]( then what looks like a page path (no space, no closing paren)
    const m = before.match(/\[[^\]]*\]\(([^()\n]*)$/);
    if (m && (m[1] === '' || /^[/a-z0-9_-]/i.test(m[1]))) return ['mdlink', m[1].replace(/^\/page\//, '')];
    // Not after a letter or digit, so "Note:", 10:30 and http: don't open it.
    const e = before.match(/(?:^|[\s([{"'*_~>])(:([a-z0-9_+-]{2,}))$/i);
    if (e && !inCode(before)) return ['emoji', e[2]];
    return [null];
  }

  // With users (<body data-users>) private/ holds the users' private pages;
  // without, it is an ordinary folder.
  const users = () => document.body.dataset.users === '1';

  // Page slugs to offer: not private ones where the text is shared.
  function offered() {
    const slug = typeof page === 'function' ? page() : page;
    return users() && slug !== null && !slug.startsWith('private/') ? wikiPages.filter(p => !p.startsWith('private/')) : wikiPages;
  }

  // The folder of the private page being edited ("private/alice"), or ''.
  function privateFolder() {
    if (!users()) return '';
    const slug = typeof page === 'function' ? page() : page;
    const m = /^private\/[^/]+(?=\/)/.exec(slug || '');
    return m ? m[0] : '';
  }

  // `[[idea` on a private page: a new page in its folder. It comes first (so
  // Enter makes it), unless what is typed names a page exactly: then that
  // page stays first and the offer follows. Left out for a name that exists
  // in the folder.
  function newPrivate(q, found) {
    const folder = privateFolder(), name = q.trim();
    if (!folder || !name || /[|#]/.test(name) || /^private\//i.test(name)) return found;
    const slug = `${folder}/${toSlug(name)}`;
    if (slug === `${folder}/` || wikiPages.includes(slug)) return found;
    const offer = { value: `${slug}|${name}`, label: `New private page: ${name}`, meta: slug };
    const exact = toSlug(name);
    return found.includes(exact) ? [exact, offer, ...found.filter(p => p !== exact)] : [offer, ...found];
  }

  function update(onlyIfFocused = false) {
    if (onlyIfFocused && document.activeElement !== ta) return;
    if (completeShortcode()) return;
    const [kind, q] = context();
    // Sharing mode: no list of pages or tasks before two letters are typed.
    if (!kind || (kind !== 'emoji' && sharingOn() && q.trim().length < 2)) { hide(); return; }
    mode = kind;
    const matches = kind === 'task' ? taskAcMatches(q, () => update(true))
      : kind === 'emoji' ? emojiMatches(q, () => update(true))
      : (found => kind === 'wikilink' ? newPrivate(q, found) : found)(
          offered().filter(p => p.includes(q.toLowerCase())).slice(0, 8));
    if (matches.length) show(matches); else hide();
  }

  // `:tada:` typed out (the closing colon just now): replace it with 🎉.
  function completeShortcode() {
    const pos = ta.selectionStart;
    if (pos !== ta.selectionEnd || ta.value[pos - 1] !== ':') return false;
    const before = ta.value.slice(0, pos);
    const m = before.match(/(?:^|[\s([{"'*_~>])(:([a-z0-9_+-]+):)$/i);
    if (!m || inCode(before)) return false;
    const emoji = emojiNamed(m[2]);
    if (!emoji) return false;
    ta.setSelectionRange(pos - m[1].length, pos);
    document.execCommand('insertText', false, emoji);
    hide();
    return true;
  }

  // An item is a page slug, or { value, label, meta } (tasks).
  function show(matches) {
    const dialog = ta.closest('dialog');
    if (!el) {
      el = document.createElement('div');
      el.className = 'wikilink-ac';
      // In a modal dialog only the top layer shows: the list goes inside it.
      (dialog || document.body).appendChild(el);
      index = 0;
    }
    if (index >= matches.length) index = 0;
    el.innerHTML = '';
    matches.forEach((m, i) => {
      const item = document.createElement('div');
      item.className = 'wikilink-ac-item' + (i === index ? ' selected' : '');
      const value = typeof m === 'string' ? m : m.value;
      item.dataset.value = value;
      item.textContent = typeof m === 'string' ? m : m.label;
      if (m.meta) {
        const meta = document.createElement('span');
        meta.className = 'wikilink-ac-meta';
        meta.textContent = m.meta;
        item.append(meta);
      }
      item.addEventListener('mousedown', ev => {
        ev.preventDefault();
        insert(value);
      });
      el.appendChild(item);
    });

    const rect = ta.getBoundingClientRect();
    const coords = getCaretCoords(ta);
    const lineH = parseFloat(window.getComputedStyle(ta).lineHeight) || 20;
    let top = Math.min(rect.top + coords.top - ta.scrollTop + lineH, window.innerHeight - 220);
    let left = Math.max(4, Math.min(rect.left + coords.left, window.innerWidth - el.offsetWidth - 8));
    if (dialog) {
      // Absolutely positioned there (style.css), so it scrolls with the form.
      const d = dialog.getBoundingClientRect();
      top += dialog.scrollTop - d.top - dialog.clientTop;
      left += dialog.scrollLeft - d.left - dialog.clientLeft;
    }
    el.style.top = top + 'px';
    el.style.left = left + 'px';
  }

  function hide() {
    if (el) { el.remove(); el = null; }
  }

  function move(dir) {
    const items = el.querySelectorAll('.wikilink-ac-item');
    items[index]?.classList.remove('selected');
    index = (index + dir + items.length) % items.length;
    items[index]?.classList.add('selected');
  }

  // Replace what was typed of the link with the chosen page or task.
  function insert(value) {
    const pos = ta.selectionStart;
    if (mode === 'mdlink') {
      ta.setSelectionRange(ta.value.lastIndexOf('(', pos - 1) + 1, pos);
      document.execCommand('insertText', false, '/page/' + value);
    } else if (mode === 'emoji') {
      ta.setSelectionRange(ta.value.lastIndexOf(':', pos - 1), pos);
      document.execCommand('insertText', false, value);
    } else {
      ta.setSelectionRange(ta.value.lastIndexOf('[[', pos), pos);
      document.execCommand('insertText', false, mode === 'task' ? `[[task:${value}]]` : `[[${value}]]`);
    }
    hide();
  }

  ta.addEventListener('input', update);
  // Delay so a mousedown on a list item fires first.
  ta.addEventListener('blur', () => setTimeout(hide, 150));
  ta.addEventListener('keydown', e => {
    if (!el) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      move(e.key === 'ArrowDown' ? 1 : -1);
    } else if (e.key === 'Enter' || e.key === 'Tab') {
      const sel = el.querySelector('.selected');
      if (sel) { e.preventDefault(); insert(sel.dataset.value); }
    } else if (e.key === 'Escape') {
      hide();
    }
  });
  fields.push(update);
  return { update };
}
