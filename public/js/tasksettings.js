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

const isTrue = v => ['1', 'true', 'yes', 'on'].includes((v || '').trim().toLowerCase());

// What the dialog shows for params: its fields' values.
function fieldsOf(p) {
  const view = (p.view || '').toLowerCase();
  const plan = (p.plan || '').toLowerCase();
  let names = (p.labels || '').split(',');
  if (names.length === 3) names = [names[0], names[2]];   // from when Plan was a tab
  const name = i => (names[i] || '').replace(/_/g, ' ').trim();
  const pin = (p.pin || '').trim().toLowerCase();
  const limit = parseInt(p.limit, 10);
  return {
    view: view === 'open' || view === 'plan' ? 'open' : view === 'closed' ? 'closed' : 'focus',
    // view=open has always been the one list, view=plan the board.
    plan: plan === 'on' || plan === 'off' ? plan === 'on' : view !== 'open',
    label_open: name(0),
    label_focus: name(1),
    limit: Number.isNaN(limit) ? '' : String(Math.max(1, Math.min(99, limit))),
    new: p.new || '',
    tag: p.tag || '',
    assignee: p.assignee || '',
    pin: isTrue(pin) ? '1' : '',
    // Which list it is: only where the dialog makes the embed (the editor).
    list: p.list || '',
    keep: isTrue(p.keep),
    noshare: isTrue(p.noshare),
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
  if (now.view !== was.view || now.plan !== was.plan) {
    // Open's two views keep their names: view=plan the board, view=open
    // the list; plan=off only says it where view= doesn't.
    if (now.view === 'open') set.view = now.plan ? 'plan' : 'open';
    else if (now.view === 'closed') set.view = 'closed';
    else set.view = now.view !== was.view || p.view ? 'focus' : '';
    set.plan = now.view !== 'open' && !now.plan ? 'off' : '';
  }
  if (now.label_open !== was.label_open || now.label_focus !== was.label_focus) {
    const a = labelWord(now.label_open), b = labelWord(now.label_focus);
    set.labels = b ? `${a},${b}` : a;
  }
  for (const k of ['limit', 'pin']) if (now[k] !== was[k]) set[k] = now[k];
  for (const k of ['new', 'tag']) if (now[k] !== was[k]) set[k] = oneWord(now[k]);
  if ('assignee' in now && now.assignee !== was.assignee) set.assignee = oneWord(now.assignee).toLowerCase().replace(/@/g, '');
  if ('keep' in now && now.keep !== was.keep) set.keep = now.keep ? '1' : '';
  if ('noshare' in now && now.noshare !== was.noshare) set.noshare = now.noshare ? '1' : '';
  return set;
}

// The dialog's form (partials/tasks_embed.html task_settings_dialog), for a
// list's ⋯ menu and for the editor's Task list button (js/tasklist.js), whose
// form says which list the embed shows. It shows what is changed most
// (Opens on, Plan, the pin); the other fields ([data-field]) only when set,
// or once asked for in the line of words under them ([data-more]), as the
// task dialog's rarer fields are. The pin is a button, pressed for a list
// the user pinned; the hidden field pin holds its value ("", "1").
// fill(params, mode): the fields as the params have them; mode says what
// the dialog is for: all (All tasks: no filter, no pin), list (a list: its
// pin, keep, hide when sharing), embed (an embed on a page: its filter).
// Returns what read() then gives.
export function settingsForm(form, tabLabel = v => (v === 'open' ? 'Open' : 'Focus')) {
  const f = form.elements;
  const pinBtn = form.querySelector('.task-settings-pin');
  const moreEl = form.querySelector('.task-add-fields');
  const fieldEl = name => form.querySelector(`[data-field="${name}"]`);
  // (What a click needs later is the form's own: the handlers are wired once.)
  const mode = () => ({ all: form.dataset.all === '1', list: form.dataset.list === '1', embed: form.dataset.embed === '1' });
  // Which fields the dialog has here: a filter where there is an embed to
  // write it in (the editor's always is one), keep where a list is named
  // or made, hiding in sharing mode on a list.
  const has = name => {
    const m = mode();
    if (name === 'tag' || name === 'assignee') return !!f.list || m.embed;
    if (name === 'keep') return !!f.list || m.list;
    if (name === 'noshare') return m.list;
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
  function syncPin() {
    if (!pinBtn) return;
    const pinned = f.pin.value !== '';
    pinBtn.hidden = !mode().list;
    pinBtn.setAttribute('aria-pressed', String(pinned));
    pinBtn.querySelector('.task-settings-pin-label').textContent = pinned ? 'Pinned' : 'Pin';
  }
  // Wired once per form: the editor's dialog is opened again and again.
  if (!form.dataset.wired) {
    form.dataset.wired = '1';
    const changed = () => form.dispatchEvent(new Event('input', { bubbles: true }));   // the editor's preview follows
    pinBtn?.addEventListener('click', () => {
      f.pin.value = f.pin.value === '' ? '1' : '';
      syncPin();
      changed();
    });
    moreEl.addEventListener('click', e => {
      const name = e.target.closest('[data-more]')?.dataset.more;
      if (!name) return;
      showField(name);
      // Keep tasks and Hide when sharing are boxes: asked for, they are ticked.
      if (name === 'keep' || name === 'noshare') { f[name].checked = true; changed(); }
      syncMore();
      fieldEl(name).querySelector('input')?.focus();
    });
  }
  const read = () => ({
    view: f.view.value, plan: f.plan.checked,
    label_open: f.label_open.value.trim(), label_focus: f.label_focus.value.trim(),
    limit: f.limit.value.trim(), new: f.new.value.trim(),
    tag: f.tag.value.trim(), pin: f.pin.value, keep: f.keep.checked,
    ...(f.list ? { list: cleanList(f.list.value) } : {}),
    ...(f.noshare ? { noshare: f.noshare.checked } : {}),
    ...(f.assignee ? { assignee: f.assignee.value.trim() } : {}),   // with users
  });
  const fill = (params, { all = false, list = false, embed = false } = {}) => {
    const fields = fieldsOf(params);
    form.dataset.all = all ? '1' : '';
    form.dataset.list = list ? '1' : '';
    form.dataset.embed = embed ? '1' : '';
    // Opens on: the tabs by their names here; Closed only where a page says so.
    f.view.replaceChildren(...['open', 'focus', fields.view === 'closed' && 'closed'].filter(Boolean)
      .map(v => new Option(v === 'closed' ? 'Closed' : tabLabel(v), v)));
    for (const k of ['view', 'label_open', 'label_focus', 'limit', 'new', 'tag', 'pin']) f[k].value = fields[k];
    f.plan.checked = fields.plan;
    f.keep.checked = fields.keep;
    if (f.noshare) f.noshare.checked = fields.noshare;
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
    syncMore();
    syncPin();
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
  // embed of no list), All tasks.
  const list = appEl.dataset.settingsList || '';
  const embed = appEl.dataset.settingsEmbed === '1';
  const all = !list && !embed;
  let listSet = {};
  try { listSet = JSON.parse(appEl.dataset.settingsListSet || '{}'); } catch (_) {}
  const pinWas = appEl.dataset.settingsPin || '';
  // What of a list's embed is the page's own: the tab and the filter.
  const PAGE_KEYS = ['view', 'tag', 'assignee'];
  const written = parseParams(raw);
  // The params the fields start from: a list's settings, with the page's
  // own over them; else what the embed (or All tasks) has.
  const params = !list ? written
    : { ...listSet, ...(embed ? Object.fromEntries(PAGE_KEYS.filter(k => k in written).map(k => [k, written[k]])) : {}), pin: pinWas };
  const errorEl = document.getElementById('task-settings-error' + sfx);
  const whereEl = document.getElementById('task-settings-where' + sfx);
  const { read, fill } = settingsForm(form, app.tabLabel);
  let was = null;

  function open() {
    was = fill(params, { all, list: !!list, embed });
    const who = app.privateLists.has(list) ? 'which only you see' : 'for everyone';
    whereEl.textContent = all ? 'For All tasks, for everyone.'
      : list && embed ? `The list’s settings hold wherever it shows, ${who}; the tab and the filter are this page’s. The pin is yours.`
      : list ? `For this list, ${who}. The pin is yours.`
      : 'Written in this [[tasks: …]] on this page.';
    errorEl.hidden = true;
    dialog.showModal();
  }

  // One write: the settings of a place ({list, set}, {page, index, was, set}
  // or {all, was, set}); its error's message, '' when it went through.
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
        message = await post(all ? { all: true, was: raw, set } : { page, index, was: raw, set });
      } else {
        // A list: its own settings to the registry, the page's to its embed,
        // the pin to the user's pins. The tab and Plan are said apart here:
        // the tab is the page's (or the list's), Plan the list's.
        const pin = set.pin;
        delete set.pin;
        if ('view' in set || 'plan' in set) {
          set.view = now.view;
          set.plan = now.plan ? 'on' : 'off';
        }
        const ofPage = {}, ofList = {};
        for (const [k, v] of Object.entries(set)) (embed && PAGE_KEYS.includes(k) ? ofPage : ofList)[k] = v;
        if (Object.keys(ofList).length) message = await post({ list, set: ofList });
        if (!message && Object.keys(ofPage).length) message = await post({ page, index, was: raw, set: ofPage });
        if (!message && pin !== undefined) {
          const res = await fetch('/tasks/pin-list', {
            method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: new URLSearchParams({ list, pin: pin || '0' }), redirect: 'manual',
          });
          if (!res.ok && res.type !== 'opaqueredirect') message = 'The pin couldn’t be changed.';
        }
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
  const scope = { list: appEl.dataset.initList || '', ...(appEl.dataset.initLists ? { lists: appEl.dataset.initLists } : {}),
                  tag: appEl.dataset.initTag || '',
                  ...(appEl.dataset.initAssignee ? { assignee: appEl.dataset.initAssignee } : {}) };
  const n = count => `${count} task${count === 1 ? '' : 's'}`;
  let picked = null;   // the file's content, while the dialog asks for the tag

  moreBtn.addEventListener('click', () => app.showMenu('This list', [
    ['settings', 'List settings…'],
    ['export', 'Export tasks'],
    ['import', 'Import tasks…'],
  ], action => {
    if (action === 'settings') { open(); return; }
    if (action === 'import') { importFile.click(); return; }
    const a = document.createElement('a');
    a.href = '/api/tasks/export?' + new URLSearchParams(scope);
    a.download = '';
    document.body.append(a);
    a.click();
    a.remove();
  }, moreBtn));

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
