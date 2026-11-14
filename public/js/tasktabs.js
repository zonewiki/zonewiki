// A task app's tabs and its filter field (tasks.js initTasksInstance): Open
// (as the Plan board or one list) and Focus, the Closed view behind its
// link, and the filter's chips and text. What it uses of the app comes in
// app (its state in app.st).

import TasksCore from '../tasks-core.js';
import { people } from './taskutil.js';

const { parseFilter, tagParamToQuery } = TasksCore;

export function initTaskTabs(app) {
const { st } = app;

// ── Tabs ───────────────────────────────────────────────────────────────────
// Segmented control: one button per view, data-view = the view's name.

const tabButtons = () => Array.from(app.tabsEl.querySelectorAll('[data-view]'));
const VIEWS = ['open', 'plan', 'focus', 'closed'];
// The tab to return to from Closed (the link's "back" state).
st.lastTab = 'focus';

// While the filter field is in use (or the embed has a tag), the link has
// no number: the total would read as the number of closed tasks the filter
// leaves. Those aren't counted either: that took every closed task of the
// year, loaded behind each filtered list only for this link; the filter
// carries over into Closed, which shows them.
// The view is called Closed: it holds what is done and what was closed
// undone (won't do). So are its param (view=closed), routes and files.
function syncClosedLink() {
  if (!app.closedLink) return;
  const f = parseFilter(filterQuery(), people);
  const filtering = f.text || f.tagGroups.length || f.excludeTags.length || f.assigneeGroups.length || f.excludeAssignees.length;
  app.closedLink.textContent = st.activeView === 'closed' ? `← Back to ${app.tabLabel(st.lastTab)}`
    : filtering ? 'Closed →'
    : `Closed${st.closedCount != null ? ` (${st.closedCount})` : ''} →`;
}

// Mark the view's button active (no side effects — used during init too).
function markActiveTab(view) {
  const tab = view === 'plan' ? 'open' : view;   // Open grouped by day
  tabButtons().forEach(b => {
    const on = b.dataset.view === tab;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  });
}

function switchTab(view) {
  if (!VIEWS.includes(view)) return;
  if (st.selectMode) app.exitSelectMode();
  if (view !== 'closed') st.lastTab = view;
  st.activeView = view;
  app.collapseAll();
  app.applySort();
  app.applyGroup();
  // Back from an earlier year: the other views count this year's matches.
  if (view !== 'closed' && st.closedYear) app.setClosedYear('');
  markActiveTab(view);   // Closed has no tab: none is marked
  syncClosedLink();
  // Keyboard focus follows the active view (Tab-cycling from a focused tab
  // would otherwise leave the focus ring on the previous one).
  if (app.tabsEl.contains(document.activeElement)) {
    app.tabsEl.querySelector(`[data-view="${view === 'plan' ? 'open' : view}"]`)?.focus();
  }
  app.saveActiveTab(st.activeView);
  if (st.activeView === 'closed') app.fetchClosed();
  app.render();
}

app.tabsEl.addEventListener('click', e => {
  const btn = e.target.closest('[data-view]');
  if (btn) switchTab(app.tabView(btn.dataset.view));
});
app.closedLink?.addEventListener('click', () => {
  switchTab(st.activeView === 'closed' ? st.lastTab : 'closed');
  if (st.activeView === 'closed') app.tabsEl.scrollIntoView({ block: 'nearest' });
});

// The filter: its chips (completed tag words) plus the text typed after them.
// The embed's own filter words (list= and tag=) are locked: the page says
// what it shows. A URL ?tag= (e.g. g t) gives ordinary chips.
const embedFilterWords = [
  ...(app.initList ? ['#' + app.initList] : []),
  ...tagParamToQuery(app.appEl?.dataset.initTag || '').split(' ').filter(Boolean),
  // [[tasks: assignee=…]] (with users): whose tasks the page shows.
  ...tagParamToQuery(app.appEl?.dataset.initAssignee || '', '@').split(' ').filter(Boolean),
];
// A chip that is just a separate list's tag (#groceries) is marked as a list.
const filterChips = app.filterSearch && app.chipField(app.filterSearch, {
  onChange: () => app.render(), locked: embedFilterWords,
  listWord: w => w.startsWith('#') && app.listSet.has(w.slice(1).toLowerCase()) });
function filterQuery() {
  return [...(filterChips?.words() || []), app.filterSearch?.value || ''].join(' ').trim();
}
// Whether the viewer filters: words beyond the embed's locked ones (a
// list= tasks always has its list's tag in the field, and isn't filtered).
function userFiltering() {
  const locked = new Set(embedFilterWords.map(w => w.toLowerCase()));
  return (filterChips?.words() || []).some(w => !locked.has(w.toLowerCase())) || !!app.filterSearch?.value.trim();
}

if (app.filterSearch) {
  // Debounced — render() rebuilds the whole list, too heavy per keystroke.
  let searchTimer = null;
  app.filterSearch.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(app.render, 150);
  });
  // With users the tooltip names the '@' words too (without, '@' is text).
  if (document.body.dataset.users === '1') {
    app.filterSearch.title += ' · @name = assigned to them · @me = to you · @none = to no one · -@name = not theirs';
  }
  // ×, at the field's end while it holds something of the viewer's (text, or
  // a chip that isn't the page's: tasks.css shows it): clears all of it.
  const clear = document.createElement('button');
  clear.type = 'button';
  clear.className = 'chip-input-clear';
  clear.tabIndex = -1;
  clear.textContent = '×';
  clear.title = 'Clear the filter';
  clear.setAttribute('aria-label', 'Clear the filter');
  clear.addEventListener('mousedown', e => e.preventDefault());   // keep the caret in the field
  clear.addEventListener('click', () => {
    clearTimeout(searchTimer);
    app.filterSearch.value = '';
    filterChips.set([]);   // the page's own words stay (locked); draws the list
    app.filterSearch.focus();
  });
  app.filterSearch.after(clear);
  // Esc leaves the field (an open tag dropdown takes the first Esc).
  app.filterSearch.addEventListener('keydown', e => {
    if (e.key === 'Escape') { e.preventDefault(); app.filterSearch.blur(); }
  });
}

return { tabButtons, VIEWS, syncClosedLink, markActiveTab, switchTab, embedFilterWords, filterChips, filterQuery, userFiltering };
}
