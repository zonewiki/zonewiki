// A task app's tabs and its filter field (tasks.js initTasksInstance): Open
// and Today (Open with a filter), each as one list or a board, the Closed
// view behind its link, and the filter's chips and text. What it uses of the app comes in
// app (its state in app.st).

import TasksCore from '../tasks-core.js';
import { people } from './taskutil.js';

const { parseFilter, tagParamToQuery } = TasksCore;

export function initTaskTabs(app) {
const { st } = app;

// ── Tabs ───────────────────────────────────────────────────────────────────
// Segmented control: one button per view, data-view = the view's name.

const tabButtons = () => Array.from(app.tabsEl.querySelectorAll('[data-view]'));
// A view is two things, as view= and group= say them: which tasks (st.today:
// only those to do now, the Today tab; else every open one, the Open tab)
// and how they are drawn (st.activeView: 'open' one list, 'plan' the board
// by what st.cut says, 'closed' the Closed view). Today is Open with a
// filter: both tabs draw the same way, grouped or not. A tab is asked for,
// and remembered, by a word: 'focus' Today, 'open' or 'plan' the Open tab
// drawn that way, 'closed'.
const VIEWS = ['open', 'plan', 'focus', 'closed'];
// shown: the word of what is shown now.
const shown = () => st.activeView !== 'closed' && st.today ? 'focus' : st.activeView;
// The tab to return to from Closed (the link's "back" state).
st.lastTab = 'focus';

// While the filter field is in use (or the embed has a tag), the link has
// no number: the total would read as the number of closed tasks the filter
// leaves. Those aren't counted either: that took every closed task of the
// year, loaded behind each filtered list only for this link; the filter
// carries over into Closed, which shows them.
// The view is called Closed: it holds what is done and what was closed
// undone (won't do). So are its param (view=closed), routes and files.
const scopeWord = app.initList ? ('list:' + app.listName(app.initList)).toLowerCase() : '';
function syncClosedLink() {
  if (!app.closedLink) return;
  // Any word of the filter: list:, from:, ! and is:updated too; but not the
  // view's own list (a list's or the Inbox's locked list: chip): the count
  // is of that list already (scopeQuery).
  const filtering = TasksCore.filterActive(parseFilter(filterQuery(scopeWord), people));
  app.closedLink.textContent = st.activeView === 'closed' ? `← Back to ${app.tabLabel(st.lastTab)}`
    : filtering ? 'Closed →'
    : `Closed${st.closedCount != null ? ` (${st.closedCount})` : ''} →`;
}

// Mark the view's button active (no side effects — used during init too).
function markActiveTab(view) {
  // Closed: the lines above the list that are about its open tasks ("4
  // unassigned", Given to others, what the view is for) aren't shown (CSS).
  app.appEl?.closest('.page-content')?.classList.toggle('tasks-shows-closed', view === 'closed');
  const tab = view === 'closed' ? view : st.today ? 'focus' : 'open';
  tabButtons().forEach(b => {
    const on = b.dataset.view === tab;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  });
}

// switchTab('focus'): the Today tab, drawn as its grouping says;
// switchTab('open' | 'plan'): the Open tab, drawn that way; 'closed': the
// Closed view. keepTab: only how the tab shown draws its tasks changes (the
// grouping control).
function switchTab(view, keepTab = false) {
  if (!VIEWS.includes(view)) return;
  if (st.selectMode) app.exitSelectMode();
  if (view === 'focus') {
    st.today = true;
    view = app.todayView();
  } else if (view !== 'closed' && !keepTab) {
    st.today = false;
  }
  st.activeView = view;
  if (view !== 'closed') st.lastTab = shown();
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
    app.tabsEl.querySelector(`[data-view="${st.today ? 'focus' : 'open'}"]`)?.focus();
  }
  app.saveActiveTab(shown());
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
  ...(app.initList ? ['list:' + app.listName(app.initList)] : []),
  ...tagParamToQuery(app.appEl?.dataset.initTag || '').split(' ').filter(Boolean),
  // [[tasks: assignee=…]] (with users): whose tasks the page shows.
  ...tagParamToQuery(app.appEl?.dataset.initAssignee || '', '@').split(' ').filter(Boolean),
];
// A chip that names a list (list:groceries) is marked as a list.
const filterChips = app.filterSearch && app.chipField(app.filterSearch, {
  onChange: () => app.render(), locked: embedFilterWords,
  label: w => app.inboxView && /^list:inbox$/i.test(w) ? 'Inbox' : /^list:/i.test(w) ? w.slice(5) : w,
  listWord: w => /^list:./i.test(w) });
// but: a chip word left out (lower case).
function filterQuery(but = '') {
  return [...(filterChips?.words() || []).filter(w => !but || w.toLowerCase() !== but), app.filterSearch?.value || ''].join(' ').trim();
}
// Whether the viewer filters: words beyond the embed's locked ones (a
// list= tasks always has its list's tag in the field, and isn't filtered).
function userFiltering() {
  const locked = new Set(embedFilterWords.map(w => w.toLowerCase()));
  return (filterChips?.words() || []).some(w => !locked.has(w.toLowerCase())) || !!app.filterSearch?.value.trim();
}

// On a phone the filter field is behind the header's 🔍 (tasks.css): shown
// once it is asked for (the button; / # @, which go to the field:
// showFilter), and for as long as the viewer filters (syncFilterShown, with
// each draw), so what narrows the list is never out of sight.
const headerEl = app.appEl?.querySelector('.tasks-header');
const searchBtn = app.appEl?.querySelector('.tasks-search-btn');
function showFilter(show = true) {
  headerEl?.classList.toggle('tasks-searching', show);
  searchBtn?.setAttribute('aria-expanded', String(show));
}
const syncFilterShown = () => headerEl?.classList.toggle('tasks-filtered', userFiltering());
searchBtn?.addEventListener('click', () => {
  const open = !headerEl.classList.contains('tasks-searching');
  showFilter(open);
  if (open) app.filterSearch?.focus();
});
// Left empty, it is put away again.
app.filterSearch?.addEventListener('blur', () => { if (!userFiltering()) showFilter(false); });

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

return { tabButtons, VIEWS, syncClosedLink, markActiveTab, switchTab, embedFilterWords, filterChips, filterQuery, userFiltering, shown, showFilter, syncFilterShown };
}
