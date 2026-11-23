// ── Forms only where Zonewiki puts them ──────────────────────────────────────────
// A form posts to Zonewiki as the reader (same-origin, so the Origin check lets
// it through): one in a page's raw HTML would delete or change what the
// reader may with one click. The server defuses those (go/markdown.go
// defuseHTML), the diagrams theirs (js/figures.js); and this is the line
// behind: a submit is cancelled, before any of Zonewiki's own handlers see it,
//
// - from a <dialog> that isn't open: CSS can show a closed dialog of Zonewiki's
//   (a page's own <style>) and lay its button over the whole page, but can't
//   open it, which only Zonewiki's code does;
// - from a form inside what Zonewiki renders from text (a page's article, a
//   preview, a task's notes and comments, a slide, a diagram, the side
//   column's headings: [data-rendered]), unless the form is Zonewiki's own task
//   list's in it (in an element of the list's, the list itself, its dialogs
//   or its no-list line, known by an id starting tasks-app, task- or
//   tasks-no-list, which a page's raw HTML can't give: its ids start
//   user-content-, a diagram's mermaid-).

const RENDERED = '[data-rendered], .mermaid-diagram, .rail-toc';
const OWN = '[id^="tasks-app"], [id^="task-"], [id^="tasks-no-list"]';

export function submitAllowed(form) {
  const dialog = form.closest('dialog');
  if (dialog && !dialog.open) return false;
  const rendered = form.closest(RENDERED);
  if (!rendered) return true;
  const own = form.closest(OWN);
  return !!own && rendered.contains(own);
}

document.addEventListener('submit', e => {
  if (e.target instanceof HTMLFormElement && submitAllowed(e.target)) return;
  e.preventDefault();
  e.stopImmediatePropagation();
}, true);
