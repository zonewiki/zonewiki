// A task app's settings dialog (tasks.js initTasksInstance): the gear in
// the toolbar. It changes the app's params where they are written: the
// [[tasks: …]] of a page (the app's own, a list's home on /tasks?list=, the
// default list's on Pinned), or the settings of All tasks. The server
// names that place in data-settings-* (tasks.go buildTasksEmbed) and
// rewrites it (POST /api/tasks/settings); without them there is no gear.
// Only what was changed is sent, so the other params stay as written.
// The dialog also has the list's tasks as a file: Export and Import.
import { closeOnBackdropClick } from './util.js';
import { showNotice } from './taskutil.js';
import { refreshDueBadge } from './chrome.js';

// The params as written: key=value words (tasksyntax.go parseTasksParams).
function parseParams(raw) {
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
    view: view === 'open' || view === 'plan' ? 'open' : view === 'completed' ? 'completed' : 'focus',
    // view=open has always been the one list, view=plan the board.
    plan: plan === 'on' || plan === 'off' ? plan === 'on' : view !== 'open',
    label_open: name(0),
    label_focus: name(1),
    limit: Number.isNaN(limit) ? '' : String(Math.max(1, Math.min(99, limit))),
    new: p.new || '',
    tag: p.tag || '',
    pin: pin === 'default' ? 'default' : isTrue(pin) || isTrue(p.home) ? '1' : '',
  };
}

// A tab's name as labels= takes it: no commas or brackets, "_" for a space.
const labelWord = s => s.replace(/[,[\]`_]/g, ' ').trim().replace(/\s+/g, '_');
// A tag filter or a list of tags as a param: one word.
const oneWord = s => s.replace(/[[\]`]/g, '').replace(/\s*([,|])\s*/g, '$1').trim().replace(/\s+/g, '-');

// The params to change (key → value, '' takes it out) for the fields now,
// given those the dialog opened with and the params they came from.
export function changedParams(was, now, p) {
  const set = {};
  if (now.view !== was.view || now.plan !== was.plan) {
    // Open's two views keep their names: view=plan the board, view=open
    // the list; plan=off only says it where view= doesn't.
    if (now.view === 'open') set.view = now.plan ? 'plan' : 'open';
    else if (now.view === 'completed') set.view = 'completed';
    else set.view = now.view !== was.view || p.view ? 'focus' : '';
    set.plan = now.view !== 'open' && !now.plan ? 'off' : '';
  }
  if (now.label_open !== was.label_open || now.label_focus !== was.label_focus) {
    const a = labelWord(now.label_open), b = labelWord(now.label_focus);
    set.labels = b ? `${a},${b}` : a;
  }
  for (const k of ['limit', 'pin']) if (now[k] !== was[k]) set[k] = now[k];
  for (const k of ['new', 'tag']) if (now[k] !== was[k]) set[k] = oneWord(now[k]);
  return set;
}

export function initTaskSettings(app) {
  const { appEl, sfx } = app;
  const btn = document.getElementById('task-settings-btn' + sfx);
  const dialog = document.getElementById('task-settings-modal' + sfx);
  const form = document.getElementById('task-settings-form' + sfx);
  if (!btn || !dialog || !form || appEl.dataset.settingsIndex === undefined) return {};
  const page = appEl.dataset.settingsPage || '';
  const index = parseInt(appEl.dataset.settingsIndex, 10);
  const raw = appEl.dataset.settingsRaw || '';
  const all = index < 0;
  const errorEl = document.getElementById('task-settings-error' + sfx);
  const whereEl = document.getElementById('task-settings-where' + sfx);
  const f = form.elements;
  let was = null;

  const read = () => ({
    view: f.view.value, plan: f.plan.checked,
    label_open: f.label_open.value.trim(), label_focus: f.label_focus.value.trim(),
    limit: f.limit.value.trim(), new: f.new.value.trim(),
    tag: f.tag.value.trim(), pin: f.pin.value,
  });

  function open() {
    const fields = fieldsOf(parseParams(raw));
    // Opens on: the tabs by their names here; Completed only where a page says so.
    f.view.replaceChildren(...['open', 'focus', fields.view === 'completed' && 'completed'].filter(Boolean)
      .map(v => new Option(v === 'completed' ? 'Completed' : app.tabLabel(v), v)));
    for (const k of ['view', 'label_open', 'label_focus', 'limit', 'new', 'tag', 'pin']) f[k].value = fields[k];
    f.plan.checked = fields.plan;
    form.querySelector('.task-settings-embed').hidden = all;
    // Where it is written: an embed's page says it itself.
    whereEl.textContent = all ? 'For All tasks, for everyone.'
      : location.pathname === '/tasks' ? `Written in the list's [[tasks: …]] on the page “${page}”.`
      : 'Written in this list’s [[tasks: …]] on this page.';
    errorEl.hidden = true;
    was = read();
    dialog.showModal();
  }

  async function save() {
    const set = changedParams(was, read(), parseParams(raw));
    if (!Object.keys(set).length) { dialog.close(); return; }
    let message = 'The settings couldn’t be saved.';
    try {
      const res = await fetch('/api/tasks/settings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(all ? { all: true, was: raw, set } : { page, index, was: raw, set }),
      });
      if (res.ok) {
        // The new defaults count from now: what this tab remembered goes.
        app.forgetSession();
        location.reload();
        return;
      }
      message = (await res.json()).error || message;
    } catch (_) {}
    errorEl.textContent = message;
    errorEl.hidden = false;
  }

  // Export, Import: the tasks this app shows (its list= and tag=), as a file
  // (tasks.go apiExport, apiImport). An imported task gets the tags a new
  // task here would, and is private where a new one is.
  const exportLink = document.getElementById('task-settings-export' + sfx);
  const importBtn = document.getElementById('task-settings-import' + sfx);
  const importFile = document.getElementById('task-settings-import-file' + sfx);
  const scope = { list: appEl.dataset.initList || '', tag: appEl.dataset.initTag || '' };
  exportLink.href = '/api/tasks/export?' + new URLSearchParams(scope);
  importBtn.addEventListener('click', () => importFile.click());
  importFile.addEventListener('change', async () => {
    const picked = importFile.files[0];
    importFile.value = '';   // the same file can be picked again
    if (!picked) return;
    let message = 'That isn\u2019t a file of tasks exported from Zone.';
    try {
      const file = JSON.parse(await picked.text());
      message = 'The tasks couldn\u2019t be imported.';
      const res = await fetch('/api/tasks/import', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file, ...scope, new: appEl.dataset.newTags || '', private: app.initPrivate }),
      });
      const out = await res.json();
      if (res.ok) {
        dialog.close();
        const n = count => `${count} task${count === 1 ? '' : 's'}`;
        showNotice(out.added ? `Imported ${n(out.added)}${out.skipped ? `; ${out.skipped} already here` : ''}.`
          : out.skipped ? 'Nothing imported: the tasks are already here.' : 'Nothing imported: the file has no tasks.');
        if (out.added) { app.refresh(); app.refreshOtherInstances(app.instanceId); refreshDueBadge(); }
        return;
      }
      message = out.error || message;
    } catch (_) {}
    errorEl.textContent = message;
    errorEl.hidden = false;
  });

  btn.addEventListener('click', open);
  form.addEventListener('submit', e => { e.preventDefault(); save(); });
  document.getElementById('task-settings-cancel' + sfx).addEventListener('click', () => dialog.close());
  closeOnBackdropClick(dialog);
  return {};
}
