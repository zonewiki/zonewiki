// Notes and comments, rendered as pages are (for every task app on the page).
// The server renders them (POST /api/render-notes, renderNotes in go/render.go: the
// page renderer, so headings, lists, tables, code, wikilinks and task chips
// look as on a page). The HTML is kept per text for the page's life and
// shared by every task list app on it, so each text is fetched once.

import { HIGHLIGHT_CODE } from './figures.js';

export const renderedHtml = { notes: new Map(), comments: new Map() };
// Until its HTML is here, a text shows as it is (line breaks kept).
export const plainHtml = text => String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;').replace(/\n/g, '<br>');
// Math and diagrams are not kept rendered: renderFigures draws them in
// the live list after each draw (from its caches, so without a flash), and
// a library that failed to load is tried again then. Code likewise
// (highlightIn): the HTML kept is the server's as it came, never put
// through a <template> and back into a string, a parse and a serialization
// a browser needn't agree on (the HTML the second parse makes may not be
// the HTML the server sent, nor what its defusing held for).
const highlighted = new Map();   // a block's class and text → hljs's class and HTML of it
export function highlightIn(root) {
  if (!window.hljs) return;
  root.querySelectorAll(HIGHLIGHT_CODE).forEach(el => {
    if (el.classList.contains('hljs')) return;
    const key = el.className + '\n' + el.textContent;
    const done = highlighted.get(key);
    if (done) {
      el.innerHTML = done.html;   // hljs's own, its text escaped
      el.className = done.cls;
      return;
    }
    hljs.highlightElement(el);
    if (highlighted.size > 200) highlighted.delete(highlighted.keys().next().value);
    highlighted.set(key, { html: el.innerHTML, cls: el.className });
  });
}
// The notes and comments of `taskList`, for renderTexts.
export function textsOf(taskList) {
  return {
    notes: taskList.map(t => t?.description).filter(Boolean),
    comments: taskList.flatMap(t => (t?.comments || []).map(c => c.text)),
  };
}
// HTML the page brought along for `texts` (initialTasks in taskdata.js), in
// their order; texts and HTML that don't pair up are left to renderTexts.
export function keepRendered(texts, html) {
  for (const kind in renderedHtml) {
    const list = texts[kind] || [], out = html[kind];
    if (!Array.isArray(out) || out.length !== list.length) continue;
    list.forEach((t, i) => { if (!renderedHtml[kind].has(t)) renderedHtml[kind].set(t, String(out[i])); });
  }
}
// Fetch the HTML of the texts not rendered yet; true when there is new.
// Texts another task list app is fetching already are waited for, not asked
// again. A failed request caches nothing: the next render asks again.
const renderInFlight = { notes: new Map(), comments: new Map() };
export async function renderTexts({ notes = [], comments = [] }) {
  const want = {}, waits = [];
  for (const [kind, texts] of Object.entries({ notes, comments })) {
    want[kind] = [];
    for (const t of new Set(texts)) {
      if (!t || renderedHtml[kind].has(t)) continue;
      const pending = renderInFlight[kind].get(t);
      if (pending) waits.push(pending);
      else want[kind].push(t);
    }
  }
  let fresh = false;
  if (want.notes.length || want.comments.length) {
    const req = (async () => {
      try {
        const res = await fetch('/api/render-notes', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(want),
        });
        if (!res.ok) return false;
        const out = await res.json();
        for (const kind in want) want[kind].forEach((t, i) => renderedHtml[kind].set(t, String(out[kind][i])));
        return true;
      } catch (_) {
        return false;
      }
    })();
    for (const kind in want) want[kind].forEach(t => renderInFlight[kind].set(t, req));
    try { fresh = await req; } finally {
      for (const kind in want) want[kind].forEach(t => renderInFlight[kind].delete(t));
    }
  }
  if (waits.length) fresh = (await Promise.all(waits)).some(Boolean) || fresh;
  return fresh;
}
