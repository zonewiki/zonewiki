// ── Zonewiki ─────────────────────────────────────────────────────────────────────
// The entry module every page loads (templates/layout.html). The code lives
// in ES modules under js/, imported here; no build step, the browser loads
// them as they are. Each module wires up what it finds on the page.

import './js/forms.js';       // a submit only from a form where Zonewiki puts one (first: before any handler)
import './js/chrome.js';      // navigation trail, backlinks chip, side columns, module counts
import './js/updates.js';     // Updates: Mark all read for the mentions on pages
import './js/git.js';         // uncommitted changes badge, Commit buttons
import './js/search.js';      // search modal and preview, Notes filter, search highlights
import './js/actions.js';     // new note / page / task, action bar, sharing mode
import './js/pagemenu.js';    // the page's ⋯ menu and its dialogs
import './js/listsrow.js';    // the Tasks page's row of lists: More, its filter, dragging the pills
import './js/shortcuts.js';   // keyboard shortcuts
import './js/editor.js';      // the editor
import './js/page.js';        // the page view: checkboxes, task chips, tables, lightbox
import './js/fold.js';        // folding sections
import './js/present.js';     // presentation mode
import './js/admin.js';       // the admin page: the Remove dialog, Save shown once a field is edited
import './js/namepick.js';    // fields that take users or groups: those there are, as pills (Groups, the admin page)
import './js/copyline.js';    // lines to copy: their Copy buttons
import './js/assistant.js';   // Connect an assistant: a connection's Change… in a dialog, the password only to give more
import './tasks.js';           // tasks apps on the page

// Every module above has wired up what it found on the page: from here on
// a click or a key does what the code says, not what the plain HTML would
// (a dialog's form posted as it is). The browser tests wait for this
// before they act (tests/e2e/conftest.py); nothing else reads it.
document.documentElement.dataset.ready = '1';
