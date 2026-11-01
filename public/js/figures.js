// ── Math and diagrams ─────────────────────────────────────────────────────────
// The server leaves math as TeX in .math elements (go/math.go) and a
// ```mermaid or ```math fence as a code block; renderFigures(root) renders
// them in the browser with KaTeX and Mermaid (vendored in public/vendor/,
// nothing from other hosts). Each library loads the first time a page has
// something for it. Without them (offline, an error) the source shows as
// written.

const KATEX = '/public/vendor/katex/';
const MERMAID = '/public/vendor/mermaid/mermaid.min.js';

// The code blocks highlight.js colours: not the ones rendered here.
export const HIGHLIGHT_CODE = 'pre code:not(.language-mermaid):not(.language-math)';

const loading = new Map();
function loadScript(src) {
  if (!loading.has(src)) {
    loading.set(src, new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = src;
      s.onload = resolve;
      s.onerror = () => { loading.delete(src); reject(new Error(`could not load ${src}`)); };
      document.head.append(s);
    }));
  }
  return loading.get(src);
}

function loadKatex() {
  if (!document.querySelector(`link[href="${KATEX}katex.min.css"]`)) {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = KATEX + 'katex.min.css';
    document.head.append(link);
  }
  return loadScript(KATEX + 'katex.min.js');
}

// Render the math and diagrams in root (an element or a template's
// content); what is rendered already is skipped, so it is safe to call
// again on the same content.
export async function renderFigures(root) {
  await Promise.all([renderMath(root), renderDiagrams(root)]);
}

async function renderMath(root) {
  // A ```math fence is display math.
  for (const code of root.querySelectorAll('pre > code.language-math')) {
    const div = document.createElement('div');
    div.className = 'math math-display';
    div.textContent = code.textContent;
    code.parentElement.replaceWith(div);
  }
  const els = [...root.querySelectorAll('.math:not([data-tex])')];
  if (!els.length) return;
  try { await loadKatex(); } catch (_) { return; }
  for (const el of els) {
    const tex = el.textContent;
    el.dataset.tex = tex;
    // An error shows the TeX in red with KaTeX's message as its title.
    window.katex.render(tex, el, { displayMode: el.classList.contains('math-display'), throwOnError: false });
  }
}

// The SVG of each diagram drawn, by theme and source, so the editor's
// preview, which is redrawn as you type, doesn't flash the source.
const svgs = new Map();
let diagrams = 0;

async function renderDiagrams(root) {
  const codes = [...root.querySelectorAll('pre > code.language-mermaid')];
  if (!codes.length) return;
  try { await loadScript(MERMAID); } catch (_) { return; }
  const theme = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'default';
  // strict: no scripts or click handlers from a diagram's text.
  window.mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme, suppressErrorRendering: true });
  for (const code of codes) {
    const pre = code.parentElement;
    if (!pre.parentNode) continue;
    const src = code.textContent;
    const key = theme + '\n' + src;
    let svg = svgs.get(key);
    if (svg === undefined) {
      try {
        await window.mermaid.parse(src);
        svg = (await window.mermaid.render(`mermaid-${++diagrams}`, src)).svg;
      } catch (e) {
        // The source stays, with what is wrong under it.
        if (pre.nextElementSibling?.classList.contains('mermaid-error')) pre.nextElementSibling.remove();
        const p = document.createElement('p');
        p.className = 'mermaid-error';
        p.textContent = `Diagram: ${String(e?.message || e).split('\n')[0]}`;
        pre.after(p);
        continue;
      }
      if (svgs.size > 50) svgs.delete(svgs.keys().next().value);
      svgs.set(key, svg);
    }
    const div = document.createElement('div');
    div.className = 'mermaid-diagram';
    div.dataset.source = src;
    div.innerHTML = svg;
    pre.replaceWith(div);
  }
}
