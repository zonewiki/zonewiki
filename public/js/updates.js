// Updates (templates/updates.html): Mark all read, beside the heading, reads
// the page's own rows too, the mentions on pages and the watched pages that
// changed (the tasks' news the task app reads itself: js/taskrender.js). A
// row read stays where it was, dimmed, until the page is left, as a task
// read does.
import { refreshDueBadge } from './chrome.js';

const button = document.getElementById('updates-read-btn');
const unread = () => document.querySelectorAll('.updates-row:not(.updates-read)');

if (button && document.querySelector('.updates-page')) {
  button.addEventListener('click', async () => {
    if (!unread().length) return;
    const res = await fetch('/updates/read', { method: 'POST' });
    if (!res.ok) return;
    unread().forEach(row => row.classList.add('updates-read'));
    // The task app, where the user may read its news, says whether the
    // button stays (taskrender.js syncReadAll); else nothing is left for it.
    if (document.querySelector('.tasks-app[data-writes="1"]')) document.dispatchEvent(new CustomEvent('zone:updates-read'));
    else button.hidden = true;
    refreshDueBadge();
  });
}
