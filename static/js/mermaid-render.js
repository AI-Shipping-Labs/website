/* Mermaid diagram renderer (issue #300).
 *
 * The python-markdown side (content/markdown_extensions/mermaid.py) emits
 * <div class="mermaid">SOURCE</div> with the source HTML-escaped. This
 * script lazy-imports Mermaid 10 ESM from jsdelivr ONLY when at least one
 * such div is present on the page, then asks Mermaid to render every
 * <div class="mermaid"> in place.
 *
 * Why lazy-load? Mermaid is ~600 KB minified. Pages without diagrams must
 * not pay that cost. Loading it via a dynamic import() means the network
 * request is only kicked off when we have something to render.
 *
 * If the import or the render fails (CDN blocked, malformed diagram),
 * the original source remains visible as text — no thrown error breaks
 * unrelated page scripts.
 */
(function () {
  function renderMermaid() {
    var nodes = document.querySelectorAll('div.mermaid');
    if (nodes.length === 0) return;

    import('https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs')
      .then(function (mod) {
        var mermaid = mod.default;
        // securityLevel:'strict' disables click handlers and HTML in
        // labels — defence in depth on top of the server-side escape.
        mermaid.initialize({
          startOnLoad: false,
          theme: 'default',
          securityLevel: 'strict',
        });
        // mermaid.run() reads textContent from each node (already
        // un-escaped by the browser when it parsed the div), replaces
        // the contents with the SVG, and sets data-processed="true".
        return mermaid.run({ nodes: nodes });
      })
      .catch(function (err) {
        if (window.console) {
          // eslint-disable-next-line no-console
          console.warn('[mermaid] render failed', err);
        }
      });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', renderMermaid);
  } else {
    renderMermaid();
  }
})();
