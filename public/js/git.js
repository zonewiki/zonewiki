// ── Uncommitted changes badge ────────────────────────────────────────────────

(function () {
  const uncommittedBadge = document.getElementById('uncommitted-badge');
  const uncommittedCount = document.getElementById('uncommitted-count');

  if (!uncommittedBadge) return;

  async function checkUncommitted() {
    try {
      const res = await fetch('/api/uncommitted');
      const files = await res.json();

      if (files.length > 0) {
        uncommittedCount.textContent = files.length;
        uncommittedBadge.style.display = 'inline-flex';
      } else {
        uncommittedBadge.style.display = 'none';
      }
    } catch (e) {
      // Silently ignore errors (git might not be enabled)
    }
  }

  // Check on page load and every 30 seconds (skipped while the tab is hidden;
  // refreshed immediately when it becomes visible again)
  checkUncommitted();
  setInterval(() => { if (!document.hidden) checkUncommitted(); }, 30000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) checkUncommitted(); });
}());

// ── Commit file actions ───────────────────────────────────────────────────────
// Handles .commit-file-form submit and #commit-all-btn click.
// Forms use data-filepath, data-title, and data-on-success attributes.
// data-on-success: "reload" (default) or a URL path to redirect to.

document.addEventListener('submit', async (e) => {
  const form = e.target.closest('.commit-file-form');
  if (!form) return;
  e.preventDefault();
  const filepath = form.dataset.filepath;
  const title    = form.dataset.title;
  const onSuccess = form.dataset.onSuccess || 'reload';
  const btn = form.querySelector('button');

  if (!confirm(`Commit "${title}"?`)) return;
  btn.disabled = true;
  btn.textContent = 'Committing…';

  try {
    // The commit message is derived server-side from the filepath.
    const res = await fetch('/api/commit-file', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filepath }),
    });
    if (res.ok) {
      if (onSuccess === 'reload') window.location.reload();
      else window.location.href = onSuccess;
    } else {
      alert('Error committing file');
      btn.disabled = false;
      btn.textContent = 'Commit';
    }
  } catch (err) {
    alert('Error: ' + err.message);
    btn.disabled = false;
    btn.textContent = 'Commit';
  }
});

// Forms that ask first (data-confirm), e.g. restoring an old version.
document.querySelectorAll('form[data-confirm]').forEach(form => form.addEventListener('submit', e => {
  if (!confirm(form.dataset.confirm)) e.preventDefault();
}));

document.getElementById('commit-all-btn')?.addEventListener('click', async () => {
  if (!confirm('Commit all uncommitted files? Each file will get its own commit.')) return;
  const btn = document.getElementById('commit-all-btn');
  btn.disabled = true;
  btn.textContent = 'Committing…';
  try {
    const res = await fetch('/api/commit-all', { method: 'POST' });
    if (res.ok) {
      window.location.reload();
    } else {
      alert('Error committing files');
      btn.disabled = false;
      btn.textContent = 'Commit All';
    }
  } catch (err) {
    alert('Error: ' + err.message);
    btn.disabled = false;
    btn.textContent = 'Commit All';
  }
});
