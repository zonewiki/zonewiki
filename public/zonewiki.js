// ── Zone ─────────────────────────────────────────────────────────────────────
// The entry module every page loads (templates/layout.html). The code lives
// in ES modules under js/, imported here; no build step, the browser loads
// them as they are. Each module wires up what it finds on the page.

import './js/oldnames.js';    // browser storage from before the renames, under its new keys
import './js/chrome.js';      // navigation trail, backlinks chip, side columns, module counts
import './js/git.js';         // uncommitted changes badge, Commit buttons
import './js/search.js';      // search modal and preview, Notes filter, search highlights
import './js/actions.js';     // new note / page / task, action bar, sharing mode
import './js/pagemenu.js';    // the page's ⋯ menu and its dialogs
import './js/shortcuts.js';   // keyboard shortcuts
import './js/editor.js';      // the editor
import './js/page.js';        // the page view: checkboxes, task chips, tables, lightbox
import './js/fold.js';        // folding sections
import './js/present.js';     // presentation mode
import './tasks.js';           // tasks apps on the page
