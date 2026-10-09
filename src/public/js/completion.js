import { sharingOn } from './actions.js';

// ── Link completion ───────────────────────────────────────────────────────────
// In a text field, `[[` offers pages, `[[task:` open deck tasks (with
// `tasks: true`, the editor) and `[text](` pages as /page/ links. One
// implementation for the editor and the deck's task fields (deck.js).

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
export function linkAutocomplete(ta, { tasks = false } = {}) {
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
    return [null];
  }

  function update(onlyIfFocused = false) {
    if (onlyIfFocused && document.activeElement !== ta) return;
    const [kind, q] = context();
    // Sharing mode: no list of pages or tasks before two letters are typed.
    if (!kind || (sharingOn() && q.trim().length < 2)) { hide(); return; }
    mode = kind;
    const matches = kind === 'task' ? taskAcMatches(q, () => update(true))
      : wikiPages.filter(p => p.includes(q.toLowerCase())).slice(0, 8);
    if (matches.length) show(matches); else hide();
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
