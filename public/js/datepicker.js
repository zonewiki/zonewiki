// The task app's calendar popup (openDatePicker), and swallowNextClick,
// which keeps the press that closes a popup from doing anything else.
import TasksCore from '../tasks-core.js';
import { DATES, LOCALE, dateOffset, esc, todayStr } from './taskutil.js';

// Custom in-page calendar popup, anchored at `anchor`. Calls cb with the
// chosen YYYY-MM-DD, or null if the user clicks Clear. Dismissing without a
// choice (Escape / outside click) never calls cb. We build our own calendar
// instead of <input type=date> because Firefox's native picker fires `change`
// on month navigation, committing a date before the user picks a day.
// `min` (YYYY-MM-DD) restricts the earliest selectable date.
// `parent` is where the popup is appended — defaults to document.body, but
// callers inside a <dialog> (the edit modal) must pass the dialog element so
// the popup renders in the same top layer instead of behind the modal.
export function openDatePicker(current, cb, anchor, min, parent = document.body) {
  // Only one picker at a time.
  document.querySelectorAll('.tasks-datepicker').forEach(el => el.remove());

  const WEEK_START = TasksCore.weekStart(LOCALE);
  const WEEKDAYS = TasksCore.weekdayNames(LOCALE, WEEK_START);
  const today = todayStr();
  // View month starts on the current value, else today.
  const anchorDate = new Date((current || today) + 'T00:00:00');
  let viewY = anchorDate.getFullYear();
  let viewM = anchorDate.getMonth();

  const pop = document.createElement('div');
  pop.className = 'tasks-datepicker';
  const r = anchor ? anchor.getBoundingClientRect() : { left: 20, top: 16, bottom: 20 };
  // A transformed parent makes position:fixed resolve against its box rather
  // than the viewport; offset by its rect then. (The edit modal is no longer
  // transformed, so normally this is 0,0.)
  const pr = (parent && parent !== document.body && getComputedStyle(parent).transform !== 'none')
    ? parent.getBoundingClientRect() : { left: 0, top: 0 };
  pop.style.cssText = 'position:fixed;z-index:1000;';
  parent.appendChild(pop);

  // Keep the calendar on screen. Runs after every draw(): the size is only
  // known once the month is rendered, and a 6-week month is a row taller.
  // Prefer below the anchor, else above it, else pinned inside the window;
  // horizontally left-aligned with the anchor but never past either edge.
  function place() {
    const w = pop.offsetWidth, h = pop.offsetHeight;
    const vw = window.innerWidth, vh = window.innerHeight, m = 4;
    let top = r.bottom + m;
    if (top + h > vh - m) top = r.top - m - h >= m ? r.top - m - h : Math.max(m, vh - m - h);
    const left = Math.max(m, Math.min(r.left, vw - m - w));
    pop.style.top = `${Math.round(top - pr.top)}px`;
    pop.style.left = `${Math.round(left - pr.left)}px`;
  }

  let done = false;
  function cleanup() {
    if (done) return;
    done = true;
    pop.remove();
    document.removeEventListener('keydown', onKey, true);
    document.removeEventListener('pointerdown', onOutside, true);
  }
  function commit(val) { const c = cb; cleanup(); c(val); }
  function onKey(e) { if (e.key === 'Escape') { e.stopPropagation(); cleanup(); } }
  // A press outside only closes the picker, with mouse and touch alike: its
  // mouse events and click are swallowed, so it can't also select or open
  // another card, tick its checkbox or press a button (like a native menu).
  function onOutside(e) {
    if (pop.contains(e.target) || e.target === anchor) return;
    cleanup();
    swallowNextClick();
  }

  // One-tap shortcuts, counted from today (not from the current value):
  // today, tomorrow, in two days, next Monday, in a week. Short labels, so
  // all five fit on one row; the tooltip says what each means and the day.
  const now = new Date();
  const QUICK = [
    ['Today', 0, 'Today'], ['Tmrw', 1, 'Tomorrow'], ['+2d', 2, 'In 2 days'],
    ['Mon', (8 - now.getDay()) % 7 || 7, 'Next Monday'], ['+1w', 7, 'In a week'],
  ];
  const quickHtml = QUICK.map(([label, days, name]) => {
    const ds = dateOffset(days);
    const off = min && ds < min;
    return `<button type="button" class="dp-quick-btn${ds === current ? ' dp-selected' : ''}" data-date="${ds}"` +
      `${off ? ' disabled' : ''} title="${esc(name + ': ' + TasksCore.longDayLabel(ds, DATES))}"` +
      ` aria-label="${esc(name)}">${label}</button>`;
  }).join('');

  function draw() {
    const first = new Date(viewY, viewM, 1);
    // Blank cells before the 1st, counted from the locale's first weekday.
    const lead = (first.getDay() - WEEK_START + 7) % 7;
    const daysInMonth = new Date(viewY, viewM + 1, 0).getDate();

    let cells = '';
    for (let i = 0; i < lead; i++) cells += '<span class="dp-day dp-blank"></span>';
    for (let d = 1; d <= daysInMonth; d++) {
      const ds = `${viewY}-${String(viewM + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
      const cls = ['dp-day'];
      if (ds === current) cls.push('dp-selected');
      if (ds === today) cls.push('dp-today');
      const disabled = min && ds < min;
      if (disabled) cls.push('dp-disabled');
      cells += `<button type="button" class="${cls.join(' ')}"${disabled ? ' disabled' : ''} data-date="${ds}">${d}</button>`;
    }

    pop.innerHTML =
      `<div class="dp-quick">${quickHtml}</div>` +
      `<div class="dp-head">` +
        `<button type="button" class="dp-nav" data-nav="-1">‹</button>` +
        `<span class="dp-title">${esc(TasksCore.monthTitle(viewY, viewM, LOCALE))}</span>` +
        `<button type="button" class="dp-nav" data-nav="1">›</button>` +
      `</div>` +
      `<div class="dp-weekdays">${WEEKDAYS.map(w => `<span>${w}</span>`).join('')}</div>` +
      `<div class="dp-grid">${cells}</div>` +
      `<div class="dp-foot">` +
        `<button type="button" class="dp-clear-btn">Clear</button>` +
      `</div>`;
    place();
  }

  pop.addEventListener('click', e => {
    const nav = e.target.closest('[data-nav]');
    if (nav) {
      viewM += Number(nav.dataset.nav);
      if (viewM < 0) { viewM = 11; viewY--; }
      else if (viewM > 11) { viewM = 0; viewY++; }
      draw();
      return;
    }
    if (e.target.classList.contains('dp-clear-btn')) { commit(null); return; }
    const day = e.target.closest('.dp-day[data-date], .dp-quick-btn');
    if (day && !day.disabled) commit(day.dataset.date);
  });

  draw();
  document.addEventListener('keydown', onKey, true);
  // Defer outside-click wiring so the opening click doesn't immediately close it.
  setTimeout(() => document.addEventListener('pointerdown', onOutside, true), 0);
}

// Swallow the mouse events and the click of the current press (mousedown
// selects a card; touch fires its mouse events with the click). Dropped at
// the click, the next press or after a second, since a scroll or a drag
// ends without any click.
const SWALLOWED = ['mousedown', 'mouseup', 'click'];
export function swallowNextClick() {
  const swallow = e => {
    e.preventDefault();  // mousedown's default would focus a field
    e.stopPropagation();
    if (e.type === 'click') stop();
  };
  const stop = () => {
    SWALLOWED.forEach(t => document.removeEventListener(t, swallow, true));
    document.removeEventListener('pointerdown', onNextPress, true);
    clearTimeout(timer);
  };
  const onNextPress = () => stop();
  SWALLOWED.forEach(t => document.addEventListener(t, swallow, true));
  // Registered after this press has finished dispatching.
  setTimeout(() => document.addEventListener('pointerdown', onNextPress, true), 0);
  const timer = setTimeout(stop, 1000);
}
