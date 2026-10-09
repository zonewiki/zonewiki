import { openLightbox } from './page.js';

// ── Presentation mode ─────────────────────────────────────────────────────────

(function () {
  const presentBtn = document.getElementById('present-btn');
  if (!presentBtn) return;

  let overlay = null;
  let slides = [];
  let currentSlide = 0;

  // Each h1, h2, h3 starts a new slide. Content following a heading belongs to it.
  // The page H1 (from the page header) always opens the first slide.
  function buildSlides() {
    const article = document.querySelector('.page-content');
    if (!article) return [];

    const groups = [];

    // Open first slide with the page H1 — intro slide has no specific section
    let current = document.createElement('div');
    current.className = 'slide-body page-content';
    current.dataset.sectionN = '';
    const pageH1 = document.querySelector('.page-header h1');
    if (pageH1) {
      const h = document.createElement('h1');
      h.textContent = pageH1.textContent;
      current.appendChild(h);
    }

    for (const node of article.children) {
      const isH2 = node.matches('div.section-header') || node.tagName === 'H2';
      if (isH2 || node.tagName === 'H3') {
        if (current.children.length) groups.push(current);
        current = document.createElement('div');
        current.className = 'slide-body page-content';
        // Extract section number from the embedded section-edit-btn link
        const editLink = node.querySelector('.section-edit-btn');
        const m = editLink && editLink.getAttribute('href').match(/[?&]section=(\d+)/);
        current.dataset.sectionN = m ? m[1] : '';
        current.appendChild(node.cloneNode(true));
      } else {
        current.appendChild(node.cloneNode(true));
      }
    }
    if (current.children.length) groups.push(current);

    return groups;
  }

  function showSlide(n) {
    currentSlide = n;
    const body = overlay.querySelector('.slide-body-wrap');
    body.innerHTML = '';
    body.appendChild(slides[n].cloneNode(true));
    // Clones don't inherit event listeners - wire lightbox directly
    body.querySelectorAll('img').forEach(img => {
      img.addEventListener('click', () => openLightbox(img.src));
    });
    overlay.querySelector('.slide-counter').textContent = `${n + 1} / ${slides.length}`;
    overlay.querySelector('.slide-progress-fill').style.width =
      `${((n + 1) / slides.length) * 100}%`;
    // Re-run syntax highlighting on code blocks inside the new slide
    body.querySelectorAll('pre code').forEach(block => {
      if (window.hljs) window.hljs.highlightElement(block);
    });
    body.scrollTop = 0;

    // Update the edit button href for this slide's section
    const slug = window.location.pathname.replace(/^\/page\//, '');
    const sectionN = slides[n].dataset.sectionN;
    const editHref = sectionN ? `/edit/${slug}?section=${sectionN}` : `/edit/${slug}`;
    const presEditBtn = overlay.querySelector('.pres-edit-btn');
    if (presEditBtn) presEditBtn.href = editHref;

    // Persist slide position so F5/session-restore reopens the presentation
    localStorage.setItem('leaf_pres', JSON.stringify({ slug, slide: n }));
  }

  function openPresentation(startAt) {
    slides = buildSlides();
    if (!slides.length) return;

    overlay = document.createElement('div');
    overlay.className = 'presentation-overlay';
    overlay.innerHTML = `
      <div class="presentation-slide">
        <div class="slide-body-wrap"></div>
      </div>
      <div class="presentation-controls">
        <span class="slide-counter"></span>
        <div class="slide-progress"><div class="slide-progress-fill"></div></div>
      </div>
      <a class="pres-edit-btn" title="Edit section" href="#">✎</a>`;

    overlay.querySelector('.pres-edit-btn').addEventListener('click', () => {
      sessionStorage.setItem('leaf_pres_slide', String(currentSlide));
    });

    // Block wheel events from reaching the page behind; navigate slides on scroll
    let _wheelCooldown = false;
    overlay.addEventListener('wheel', e => {
      e.preventDefault();
      if (_wheelCooldown) return;
      _wheelCooldown = true;
      setTimeout(() => { _wheelCooldown = false; }, 400);
      if (e.deltaY > 0 || e.deltaX > 0) {
        if (currentSlide < slides.length - 1) showSlide(currentSlide + 1);
      } else {
        if (currentSlide > 0) showSlide(currentSlide - 1);
      }
    }, { passive: false });

    document.body.appendChild(overlay);

    const resumeAt = (typeof startAt === 'number')
      ? Math.min(startAt, slides.length - 1)
      : Math.min(currentSlide, slides.length - 1);
    showSlide(resumeAt);
    document.addEventListener('keydown', onPresentKey);
  }

  function closePresentation() {
    if (overlay) { overlay.remove(); overlay = null; }
    document.removeEventListener('keydown', onPresentKey);
    sessionStorage.removeItem('leaf_pres_slide');
    localStorage.removeItem('leaf_pres');
  }

  function onPresentKey(e) {
    // Suppress other keybindings during presentation
    if (e.ctrlKey || e.metaKey || e.altKey) { e.stopPropagation(); return; }
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown' || e.key === 'PageDown') {
      e.preventDefault();
      if (currentSlide < slides.length - 1) showSlide(currentSlide + 1);
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp' || e.key === 'PageUp') {
      e.preventDefault();
      if (currentSlide > 0) showSlide(currentSlide - 1);
    } else if (e.key === 'Escape') {
      closePresentation();
    } else if (e.key === 'e') {
      e.preventDefault();
      const editBtn = overlay && overlay.querySelector('.pres-edit-btn');
      if (editBtn) editBtn.click();
    }
  }

  // The ⋯ menu offers Present only when the page has headings (hidden in the HTML).
  function syncPresentBtn() {
    presentBtn.hidden = !document.querySelector('.page-content h1, .page-content h2, .page-content h3');
  }
  requestAnimationFrame(syncPresentBtn);
  window.addEventListener('pageshow', syncPresentBtn);

  presentBtn.addEventListener('click', openPresentation);

  // Auto-reopen presentation after F5 or after returning from section edit
  const _currentSlug = window.location.pathname.replace(/^\/page\//, '');
  const _presState = localStorage.getItem('leaf_pres');
  const _returnSlide = sessionStorage.getItem('leaf_pres_slide');

  if (_presState) {
    try {
      const { slug, slide } = JSON.parse(_presState);
      if (slug === _currentSlug) {
        requestAnimationFrame(() => openPresentation(slide));
      } else {
        localStorage.removeItem('leaf_pres');
      }
    } catch (_) { localStorage.removeItem('leaf_pres'); }
  } else if (_returnSlide !== null) {
    sessionStorage.removeItem('leaf_pres_slide');
    requestAnimationFrame(() => openPresentation(parseInt(_returnSlide, 10)));
  }
}());
