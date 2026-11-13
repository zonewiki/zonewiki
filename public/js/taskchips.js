// Tags typed as chips (tasks.js initTasksInstance): a field whose words
// become chips (the filter field, the modal's Tags), and the completion of a
// tag while it is typed. What it uses of the app comes in app (its state in
// app.st).

import TasksCore from '../tasks-core.js';
import { attachTagAc, hashWordAtCaret } from './taskutil.js';

const { parseFilter, rankTags, tagCounts } = TasksCore;

export function initTaskChips(app) {

// ── Tag chips ──────────────────────────────────────────────────────────────
// A completed tag becomes a chip in front of the field's text: it can't be
// edited, only removed (its × or Backspace at the start of the field). The
// input is wrapped in a .chip-input box that looks like the field. Used by
// the filter field (chips are filter words: #a, -#b, #a|b) and the edit
// modal's Tags field (chips are tag names, shown as #name). `locked` words
// (an embed's filter words, case ignored) are always there and have no ×.
// `listWord(w)` marks a separate list's tag: such chips come first, with a
// list icon, a tint and a tooltip naming the list's page.
function chipField(input, { label = w => w, onChange, locked = [], listWord = () => false } = {}) {
  const lockedSet = new Set(locked.map(w => w.toLowerCase()));
  const isLocked = w => lockedSet.has(w.toLowerCase());
  const box = document.createElement('div');
  box.className = 'chip-input';
  input.parentNode.insertBefore(box, input);
  box.appendChild(input);
  let words = [];
  function draw() {
    box.querySelectorAll('.tag-chip').forEach(c => c.remove());
    words.forEach((w, i) => {
      const chip = document.createElement('span');
      chip.className = 'tag-chip';
      const text = document.createElement('span');
      text.className = 'tag-chip-label';
      text.textContent = label(w);
      chip.appendChild(text);
      const isList = listWord(w);
      if (isList) {
        chip.classList.add('tag-chip-list');
        chip.title = app.listTooltip(w);
      }
      if (isLocked(w)) {
        chip.classList.add('tag-chip-locked');
        if (!isList) chip.title = 'Set by this page';
        box.insertBefore(chip, input);
        return;
      }
      const x = document.createElement('button');
      x.type = 'button';
      x.className = 'tag-chip-x';
      x.tabIndex = -1;
      x.textContent = '×';
      x.setAttribute('aria-label', `Remove ${label(w)}`);
      x.addEventListener('mousedown', e => e.preventDefault());   // keep the caret in the field
      x.addEventListener('click', e => { e.stopPropagation(); remove(i); input.focus(); });
      chip.appendChild(x);
      box.insertBefore(chip, input);
    });
  }
  function set(list, silent) {
    const seen = new Set();
    words = list.filter(w => w && !seen.has(w.toLowerCase()) && seen.add(w.toLowerCase()));
    words.unshift(...locked.filter(w => !seen.has(w.toLowerCase())));
    words = [...words.filter(w => listWord(w)), ...words.filter(w => !listWord(w))];
    draw();
    if (!silent) onChange?.();
  }
  function remove(i) {
    if (i < 0 || isLocked(words[i])) return;
    words.splice(i, 1); draw(); onChange?.();
  }
  input.addEventListener('keydown', e => {
    if (e.key === 'Backspace' && !e.defaultPrevented &&
        input.selectionStart === 0 && input.selectionEnd === 0) {
      const i = words.findLastIndex(w => !isLocked(w));
      if (i === -1) return;
      e.preventDefault();
      remove(i);
    }
  });
  box.addEventListener('mousedown', e => {
    if (e.target === box) { e.preventDefault(); input.focus(); }
  });
  return { words: () => [...words], add: w => set([...words, w]), set };
}

// ── Tag autocomplete ───────────────────────────────────────────────────────

// Filter field: tags of the tasks the *other* tag words leave visible in
// this tab, minus those already used. Free text is ignored: it is usually
// still being typed ("some text #ta…") and would otherwise hide every tag.
if (app.filterSearch) attachTagAc(app.filterSearch, {
  word: () => hashWordAtCaret(app.filterSearch),
  suggest: w => {
    const val = app.filterSearch.value;
    const rest = [...app.filterChips.words(), val.slice(0, w.start) + val.slice(w.end)].join(' ');
    const otherTags = rest.split(/\s+/).filter(word => /^-?#/.test(word)).join(' ');
    const restFilter = parseFilter(rest);
    const used = [...restFilter.tagGroups.flat(), ...restFilter.excludeTags];
    const counts = tagCounts(app.filterTasks(otherTags));
    for (const tag of Object.keys(counts)) if (used.includes(tag.toLowerCase())) delete counts[tag];
    return rankTags(w.partial, counts, w.partial ? 8 : 12);
  },
  // The completed word (with its '-' and any OR parts) becomes a chip.
  accept: (tag, w) => {
    const val = app.filterSearch.value;
    const start = val[w.start - 1] === '-' ? w.start - 1 : w.start;
    const chip = val.slice(start, w.partStart) + (w.partStart === w.start ? '#' : '') + tag;
    app.filterSearch.value = (val.slice(0, start) + val.slice(w.end).replace(/^ +/, '')).replace(/ {2,}/g, ' ');
    app.filterSearch.focus();
    app.filterSearch.setSelectionRange(start, start);
    app.filterChips.add(chip);
  },
});
// A new task starts with the tags the filter requires (single-tag words;
// an OR word doesn't say which one), so it shows up in the current view.
// [[tasks: new=a,b]] (the Pinned view: the default list's tags) gives new
// tasks those tags in place of the embed's own filter words.
const newTags = (app.appEl?.dataset.newTags || '').split(',').filter(Boolean);
function filterDefaultTags() {
  const locked = new Set(newTags.length ? app.embedFilterWords.map(w => w.toLowerCase()) : []);
  const own = (app.filterChips?.words() || []).filter(w => !locked.has(w.toLowerCase()));
  const query = newTags.length ? [...own, app.filterSearch?.value || ''].join(' ') : app.filterQuery();
  return [...newTags, ...parseFilter(query).tagGroups.filter(g => g.length === 1).map(g => g[0])];
}

return { chipField, filterDefaultTags };
}
