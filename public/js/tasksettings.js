// A task app's settings dialog (tasks.js initTasksInstance): List settings…
// in the list's ⋯ menu in the toolbar. It changes the settings where they
// are kept: a list's own (its limit, Plan, tab names, new tasks' tags, keep,
// whether it hides in sharing mode) in the lists' registry, wherever the
// list is shown; how a page looks at it (the tab it opens on, a filter) in
// that page's [[tasks: …]]; an embed of no list all in its [[tasks: …]];
// those of All tasks in the registry too. The pin is the user's own. The
// server names the places in data-settings-* (tasksembed.go
// buildTasksEmbed) and writes them (POST /api/tasks/settings, POST
// /tasks/pin-list); without them there is no ⋯ menu.
// Only what was changed is sent, so the other settings stay as written.
// The menu's other items: the list's tasks as a file, Export and Import….
import { closeOnBackdropClick } from './util.js';
import { showNotice } from './taskutil.js';
import { refreshDueBadge } from './chrome.js';

// The params as written: key=value words (tasksyntax.go parseTasksParams).
export function parseParams(raw) {
  const out = {};
  for (const word of raw.split(/\s+/)) {
    const at = word.indexOf('=');
    if (at > 0) out[word.slice(0, at).toLowerCase()] = word.slice(at + 1).trim();
  }
  return out;
}

// The kinds of mail about tasks a user can turn on (mailKinds in go/notify.go).
const MAIL = ['assigned', 'mentioned', 'comments'];

// What the Open tab shows, as group= names it (tasksGroups in go/tasksyntax.go).
const GROUPS = ['none', 'day', 'person', 'list'];

const isTrue = v => ['1', 'true', 'yes', 'on'].includes((v || '').trim().toLowerCase());

// What the dialog shows for params: its fields' values.
function fieldsOf(p, defaultView = 'today') {
  const view = (p.view || '').toLowerCase();
  const group = (p.group || '').toLowerCase();
  const names = (p.labels || '').split(',');
  const name = i => (names[i] || '').replace(/_/g, ' ').trim();
  const limit = parseInt(p.limit, 10);
  return {
    view: ['today', 'open', 'closed'].includes(view) ? view : defaultView,
    // What the Open tab shows: group=, else the board by day.
    group: GROUPS.includes(group) ? group : 'day',
    label_open: name(0),
    label_focus: name(1),
    limit: Number.isNaN(limit) ? '' : String(Math.max(1, Math.min(99, limit))),
    new: p.new || '',
    tag: p.tag || '',
    assignee: p.assignee || '',
    // Which list it is: only where the dialog makes the embed (the editor).
    list: p.list || '',
    keep: isTrue(p.keep),
    noshare: isTrue(p.noshare),
    // home=off: the list's tasks are left out of My tasks.
    home: String(p.home || '').toLowerCase() === 'off',
    // My tasks: the kinds of mail the user turned on (none until they do;
    // "off", from when one mail was on by itself, is none), in MAIL's order.
    mail: MAIL.filter(k => (p.mail || '').toLowerCase().split(',').includes(k)).join(','),
  };
}

// cleanList (go/tasksyntax.go): a list's name is one lowercase word.
const cleanList = name => name.replace(/[[\]`]/g, '').trim().replace(/^#+/, '').trim().toLowerCase()
  .replace(/\s+/g, '-').replace(/^-+|-+$/g, '');

// A tab's name as labels= takes it: no commas or brackets, "_" for a space.
const labelWord = s => s.replace(/[,[\]`_]/g, ' ').trim().replace(/\s+/g, '_');
// A tag filter or a list of tags as a param: one word.
const oneWord = s => s.replace(/[[\]`]/g, '').replace(/\s*([,|])\s*/g, '$1').trim().replace(/\s+/g, '-');

// The params to change (key → value, '' takes it out) for the fields now,
// given those the dialog opened with and the params they came from.
export function changedParams(was, now, p) {
  const set = {};
  if ('list' in now && now.list !== was.list) set.list = now.list;
  // view= says the tab, group= what the Open tab shows; each is written
  // when it was changed, or was written already.
  if (now.view !== was.view || p.view) set.view = now.view;
  if (now.group !== was.group || p.group) set.group = now.group;
  if (set.view === (p.view || '').toLowerCase()) delete set.view;
  if (set.group === (p.group || '').toLowerCase()) delete set.group;
  if (now.label_open !== was.label_open || now.label_focus !== was.label_focus) {
    const a = labelWord(now.label_open), b = labelWord(now.label_focus);
    set.labels = b ? `${a},${b}` : a;
  }
  if (now.limit !== was.limit) set.limit = now.limit;
  for (const k of ['new', 'tag']) if (now[k] !== was[k]) set[k] = oneWord(now[k]);
  if ('assignee' in now && now.assignee !== was.assignee) set.assignee = oneWord(now.assignee).toLowerCase().replace(/@/g, '');
  if ('keep' in now && now.keep !== was.keep) set.keep = now.keep ? '1' : '';
  if ('noshare' in now && now.noshare !== was.noshare) set.noshare = now.noshare ? '1' : '';
  if ('home' in now && now.home !== was.home) set.home = now.home ? 'off' : '';
  if ('mail' in now && now.mail !== was.mail) set.mail = now.mail;
  return set;
}

// The dialog's form (partials/tasks_embed.html task_settings_dialog), for a
// list's ⋯ menu and for the editor's Task list button (js/tasklist.js), whose
// form says which list the embed shows. It shows what is changed most
// (Opens on, Plan); the other fields ([data-field]) only when set, or once
// asked for in the line of words under them ([data-more]), as the task
// dialog's rarer fields are. (A list is pinned in its ⋯ menu, where this
// dialog is opened too, not here.)
// fill(params, mode): the fields as the params have them; mode says what
// the dialog is for: all (All tasks: no filter, no pin), mine (My tasks:
// as All tasks, less the tags of a new task, which are its list's), list
// (a list: its pin, keep, hide when sharing), embed (an embed on a page:
// its filter).
// Returns what read() then gives.
// The settings that are a list's own, wherever it shows (the fields' names).
export const LIST_OWN = ['limit', 'labels', 'new', 'keep'];
export function settingsForm(form, tabLabel = v => (v === 'open' ? 'Open' : 'Today')) {
  const f = form.elements;
  const moreEl = form.querySelector('.task-add-fields');
  const fieldEl = name => form.querySelector(`[data-field="${name}"]`);
  // (What a click needs later is the form's own: the handlers are wired once.)
  const mode = () => ({ all: form.dataset.all === '1', mine: form.dataset.mine === '1', list: form.dataset.list === '1', embed: form.dataset.embed === '1', home: form.dataset.home === '1', mail: form.dataset.mail === '1' });
  // Which fields the dialog has here: a filter where there is an embed to
  // write it in (the editor's always is one), keep where a list is named
  // or made, hiding in sharing mode on a list.
  const has = name => {
    const m = mode();
    // The editor's dialog with a list named: the embed says how the page
    // looks at the list (its tab, grouping and filter); the list's own
    // settings are set in the list's dialog, and written here they would
    // mean nothing.
    if (LIST_OWN.includes(name) && f.list && cleanList(f.list.value)) return false;
    if (name === 'tag' || name === 'assignee') return !!f.list || m.embed;
    if (name === 'keep') return !!f.list || m.list;
    if (name === 'noshare') return m.list;
    // Left out of My tasks: on a list that is the user's own (a private
    // one; without users any), whose tasks are in My tasks by the list.
    if (name === 'home') return m.list && m.home;
    // My tasks: Mail me when a task is assigned to me, where Zone can mail the user.
    if (name === 'mail') return m.mine && m.mail && !!f.mail_assigned;
    if (name === 'new') return !m.mine;
    return true;
  };
  // Which + words the line has: those of the fields not shown.
  function syncMore() {
    let any = false;
    for (const btn of moreEl.querySelectorAll('[data-more]')) {
      const el = fieldEl(btn.dataset.more);
      btn.hidden = !el || !el.hidden || !has(btn.dataset.more);
      any = any || !btn.hidden;
    }
    moreEl.hidden = !any;
  }
  function showField(name, show = true) {
    const el = fieldEl(name);
    if (el) el.hidden = !show || !has(name);
  }
  // Wired once per form: the editor's dialog is opened again and again.
  if (!form.dataset.wired) {
    form.dataset.wired = '1';
    // The List field named or emptied: the fields that are a list's own go or
    // can be asked for again.
    f.list?.addEventListener('input', () => {
      for (const name of LIST_OWN) if (!has(name)) fieldEl(name).hidden = true;
      syncMore();
    });
    const changed = () => form.dispatchEvent(new Event('input', { bubbles: true }));   // the editor's preview follows
    moreEl.addEventListener('click', e => {
      const name = e.target.closest('[data-more]')?.dataset.more;
      if (!name) return;
      showField(name);
      // Keep tasks and Hide when sharing are boxes: asked for, they are ticked.
      if (name === 'keep' || name === 'noshare') { f[name].checked = true; changed(); }
      if (name === 'home') { f.homeout.checked = true; changed(); }
      syncMore();
      fieldEl(name).querySelector('input')?.focus();
    });
  }
  const read = () => ({
    view: f.view.value, group: f.group.value,
    label_open: f.label_open.value.trim(), label_focus: f.label_focus.value.trim(),
    limit: f.limit.value.trim(), new: f.new.value.trim(),
    tag: f.tag.value.trim(), keep: f.keep.checked,
    ...(f.list ? { list: cleanList(f.list.value) } : {}),
    ...(f.noshare ? { noshare: f.noshare.checked } : {}),
    ...(f.homeout ? { home: f.homeout.checked } : {}),
    ...(f.mail_assigned && mode().mine && mode().mail ? { mail: MAIL.filter(k => f['mail_' + k].checked).join(',') } : {}),
    ...(f.assignee ? { assignee: f.assignee.value.trim() } : {}),   // with users
  });
  const fill = (params, { all = false, mine = false, list = false, embed = false, home = false, mail = false, ownList = false, defaultView = 'today' } = {}) => {
    const fields = fieldsOf(params, defaultView);
    form.dataset.home = home ? '1' : '';
    form.dataset.mail = mail ? '1' : '';
    form.dataset.all = all ? '1' : '';
    form.dataset.mine = mine ? '1' : '';
    form.dataset.list = list ? '1' : '';
    form.dataset.embed = embed ? '1' : '';
    // Opens on: the tabs by their names here; Closed only where a page says so.
    f.view.replaceChildren(...['open', 'today', fields.view === 'closed' && 'closed'].filter(Boolean)
      .map(v => new Option(v === 'closed' ? 'Closed' : tabLabel(v === 'today' ? 'focus' : v), v)));
    for (const k of ['view', 'label_open', 'label_focus', 'limit', 'new', 'tag']) f[k].value = fields[k];
    // By person where tasks have assignees (with users; not My tasks nor a
    // list of the user's own), By list where the view holds several lists;
    // what is set stays to be seen.
    const users = document.body.dataset.users === '1';
    const offered = { none: true, day: true, person: users && !mine && !ownList, list: !list };
    for (const o of f.group.options) o.hidden = o.disabled = !offered[o.value] && o.value !== fields.group;
    f.group.value = fields.group;
    f.keep.checked = fields.keep;
    if (f.noshare) f.noshare.checked = fields.noshare;
    if (f.homeout) f.homeout.checked = fields.home;
    if (f.mail_assigned) for (const k of MAIL) f['mail_' + k].checked = fields.mail.split(',').includes(k);
    if (f.list) f.list.value = fields.list;
    if (f.assignee) f.assignee.value = fields.assignee;
    // The rarer fields: shown when set.
    showField('limit', !!fields.limit);
    showField('labels', !!(fields.label_open || fields.label_focus));
    showField('new', !!fields.new);
    showField('tag', !!fields.tag);
    showField('assignee', !!fields.assignee);
    showField('keep', fields.keep);
    showField('noshare', fields.noshare);
    showField('home', fields.home);
    showField('mail');   // always there, where there is mail: nothing is sent until a box is ticked
    syncMore();
    return read();
  };
  return { read, fill };
}

export function initTaskSettings(app) {
  const { appEl, sfx } = app;
  const moreBtn = document.getElementById('task-more-btn' + sfx);
  const dialog = document.getElementById('task-settings-modal' + sfx);
  const form = document.getElementById('task-settings-form' + sfx);
  if (!moreBtn || !dialog || !form || appEl.dataset.settingsIndex === undefined) return {};
  const page = appEl.dataset.settingsPage || '';
  const index = parseInt(appEl.dataset.settingsIndex, 10);
  const raw = appEl.dataset.settingsRaw || '';
  // What the dialog is for: a list (its own settings and the user's pin of
  // it), an embed on a page (how the page looks at the list, or all of an
  // embed of no list), All tasks, My tasks (the user's own).
  const list = appEl.dataset.settingsList || '';
  const embed = appEl.dataset.settingsEmbed === '1';
  const all = !list && !embed;
  const mine = all && appEl.dataset.settingsMine === '1';
  let listSet = {};
  try { listSet = JSON.parse(appEl.dataset.settingsListSet || '{}'); } catch (_) {}
  // What of a list's embed is the page's own: the tab and the filter.
  const PAGE_KEYS = ['view', 'tag', 'assignee'];
  const written = parseParams(raw);
  // The params the fields start from: a list's settings, with the page's
  // own over them; else what the embed (or All tasks) has.
  const params = !list ? written
    : { ...listSet, ...(embed ? Object.fromEntries(PAGE_KEYS.filter(k => k in written).map(k => [k, written[k]])) : {}) };
  const errorEl = document.getElementById('task-settings-error' + sfx);
  const whereEl = document.getElementById('task-settings-where' + sfx);
  const { read, fill } = settingsForm(form, app.tabLabel);
  let was = null;

  function open() {
    was = fill(params, { all, mine, list: !!list, embed, ownList: !!list && app.privateLists.has(list), defaultView: appEl.dataset.defaultView === 'open' ? 'open' : 'today', home: appEl.dataset.settingsHome === '1', mail: appEl.dataset.settingsMail === '1' });
    const who = app.privateLists.has(list) ? 'which only you see' : 'for everyone';
    whereEl.textContent = mine ? (document.body.dataset.users === '1' ? 'For My tasks: yours alone.' : 'For My tasks.')
      : all ? 'For All tasks, for everyone.'
      : list && embed ? `The list’s settings hold wherever it shows, ${who}; the tab and the filter are this page’s.`
      : list ? `For this list, ${who}.`
      : 'Written in this [[tasks: …]] on this page.';
    errorEl.hidden = true;
    dialog.showModal();
  }

  // One write: the settings of a place ({list, set}, {page, index, was, set},
  // {all, was, set} or {mine, was, set}); its error's message, '' when it
  // went through.
  async function post(body) {
    const res = await fetch('/api/tasks/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    if (res.ok) return '';
    return (await res.json().catch(() => ({}))).error || 'The settings couldn’t be saved.';
  }

  async function save() {
    const now = read();
    const set = changedParams(was, now, params);
    if (!Object.keys(set).length) { dialog.close(); return; }
    let message = '';
    try {
      if (!list) {
        message = await post(mine ? { mine: true, was: raw, set }
          : all ? { all: true, was: raw, set } : { page, index, was: raw, set });
      } else {
        // A list: its own settings to the registry, the page's to its embed.
        // The tab and Plan are said apart here: the tab is the page's (or
        // the list's), Plan the list's.
        const ofPage = {}, ofList = {};
        for (const [k, v] of Object.entries(set)) (embed && PAGE_KEYS.includes(k) ? ofPage : ofList)[k] = v;
        if (Object.keys(ofList).length) message = await post({ list, set: ofList });
        if (!message && Object.keys(ofPage).length) message = await post({ page, index, was: raw, set: ofPage });
      }
      if (!message) {
        // The new defaults count from now: what this tab remembered goes.
        app.forgetSession();
        location.reload();
        return;
      }
    } catch (_) { message = 'The settings couldn’t be saved.'; }
    errorEl.textContent = message;
    errorEl.hidden = false;
  }

  // The ⋯ menu. List settings… opens the dialog. Export, Import…: the tasks this app shows (its list= and
  // tag=), as a file (tasksapi.go apiExport, apiImport). An imported task goes
  // into the list shown here and gets the tags a new task here would.
  const importFile = document.getElementById('task-import-file' + sfx);
  const importDialog = document.getElementById('task-import-modal' + sfx);
  const importTitle = document.getElementById('task-import-title' + sfx);
  const importTag = document.getElementById('task-import-tag' + sfx);
  const importError = document.getElementById('task-import-error' + sfx);
  const scope = { list: appEl.dataset.initList || '',
                  ...(appEl.dataset.initMine ? { mine: '1' } : appEl.dataset.initLists ? { lists: appEl.dataset.initLists } : {}),
                  tag: appEl.dataset.initTag || '',
                  ...(appEl.dataset.initAssignee ? { assignee: appEl.dataset.initAssignee } : {}) };
  const n = count => `${count} task${count === 1 ? '' : 's'}`;
  let picked = null;   // the file's content, while the dialog asks for the tag

  // What the menu offers: List settings…, Mark all read (My tasks, while a
  // task has news for the user: all of them seen), Export, Import.
  const actions = () => [
    ['settings', 'List settings…'],
    ...(app.initMine && app.updatesNow().length ? [['read', 'Mark all read']] : []),
    ['export', 'Export tasks'],
    ['import', 'Import tasks…'],
  ];
  function run(action) {
    if (action === 'settings') { open(); return; }
    if (action === 'read') { app.markAllSeen(); return; }
    if (action === 'import') { importFile.click(); return; }
    const a = document.createElement('a');
    a.href = '/api/tasks/export?' + new URLSearchParams(scope);
    a.download = '';
    document.body.append(a);
    a.click();
    a.remove();
  }
  // On the Tasks page they are the page's ⋯ menu's (beside the heading,
  // with the list's own actions: templates/tasks_page.html), so the page has
  // one such menu; an embed on a wiki page has no heading of the list's, and
  // keeps the ⋯ of its toolbar.
  const pageItems = location.pathname === '/tasks' && app.instanceId === '0'
    ? [...document.querySelectorAll('#page-menu [data-tasks-action]')] : [];
  if (pageItems.length) {
    moreBtn.hidden = true;
    const sync = () => {
      const now = actions().map(([action]) => action);
      for (const item of pageItems) item.hidden = !now.includes(item.dataset.tasksAction);
    };
    sync();
    document.querySelector('.page-menu-btn')?.addEventListener('click', sync, true);
    for (const item of pageItems) item.addEventListener('click', () => run(item.dataset.tasksAction));
  } else {
    moreBtn.addEventListener('click', () => app.showMenu('This list', actions(), run, moreBtn));
  }

  // A file is picked: what it holds, and a tag of your own for its tasks.
  importFile.addEventListener('change', async () => {
    const chosen = importFile.files[0];
    importFile.value = '';   // the same file can be picked again
    if (!chosen) return;
    let tasks;
    try {
      const file = JSON.parse(await chosen.text());
      tasks = Array.isArray(file) ? file : file?.tasks;
      picked = file;
    } catch (_) {}
    if (!Array.isArray(tasks)) { showNotice('That isn\u2019t a file of tasks exported from Zone.'); return; }
    if (!tasks.length) { showNotice('Nothing imported: the file has no tasks.'); return; }
    importTitle.textContent = `Import ${n(tasks.length)} from \u201c${chosen.name}\u201d`;
    importTag.value = '';
    importError.hidden = true;
    importDialog.showModal();
    importTag.focus();
  });

  async function runImport() {
    let message = 'The tasks couldn\u2019t be imported.';
    try {
      const res = await fetch('/api/tasks/import', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file: picked, list: scope.list, tag: scope.tag, new: appEl.dataset.newTags || '', also: importTag.value }),
      });
      const out = await res.json();
      if (res.ok) {
        importDialog.close();
        if (!out.added) {
          showNotice(out.skipped ? 'Nothing imported: the tasks are already here.' : 'Nothing imported: the file has no tasks.');
          return;
        }
        const reload = () => { app.refresh(); app.refreshOtherInstances(app.instanceId); refreshDueBadge(); };
        reload();
        // Undo (the toast's button, Ctrl+Z): the imported tasks go again.
        const lost = out.unassigned
          ? `; ${out.unassigned === 1 ? '1 was' : `${out.unassigned} were`} assigned to people who are not users of this wiki` : '';
        app.showUndo(`Imported ${n(out.added)}${out.skipped ? `; ${out.skipped} already here` : ''}${lost}.`, async () => {
          const gone = await fetch('/api/tasks/batch-delete', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids: out.ids }),
          });
          if (gone.ok) reload(); else showNotice('The import couldn\u2019t be undone.');
        });
        return;
      }
      message = out.error || message;
    } catch (_) {}
    importError.textContent = message;
    importError.hidden = false;
  }
  document.getElementById('task-import-form' + sfx).addEventListener('submit', e => { e.preventDefault(); runImport(); });
  document.getElementById('task-import-cancel' + sfx).addEventListener('click', () => importDialog.close());
  closeOnBackdropClick(importDialog);

  form.addEventListener('submit', e => { e.preventDefault(); save(); });
  document.getElementById('task-settings-cancel' + sfx).addEventListener('click', () => dialog.close());
  closeOnBackdropClick(dialog);
  return {};
}
