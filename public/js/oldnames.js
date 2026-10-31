// What this browser kept under the names from before Zone's renames (Leaf
// is called Zone, the Deck Tasks, its page /deck /tasks) moves to the new
// keys, once: the presentation's slide, the search's sort, the navigation
// trail, the task lists' choices. zonewiki.js imports this first, so it runs
// before any module reads its keys.

const renamed = [
  [/^leaf-deck-(\w+):\/deck(\|.*)$/, 'zone-tasks-$1:/tasks$2'],
  [/^leaf-deck-(\w+):(.*)$/, 'zone-tasks-$1:$2'],
  [/^leaf_pres$/, 'zone_pres'],
  [/^leaf_pres_slide$/, 'zone_pres_slide'],
  [/^leaf-search-sort$/, 'zone-search-sort'],
  [/^leafHistory$/, 'zoneHistory'],
];

for (const store of ['localStorage', 'sessionStorage']) {
  try {
    const s = window[store];
    for (const key of Object.keys(s)) {
      const rule = renamed.find(([old]) => old.test(key));
      if (!rule) continue;
      const to = key.replace(rule[0], rule[1]);
      if (s.getItem(to) === null) s.setItem(to, s.getItem(key));
      s.removeItem(key);
    }
  } catch (_) { /* no storage: nothing kept */ }
}
