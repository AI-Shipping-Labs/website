/* Mermaid diagram renderer (issue #300, theme support added in #306).
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
 * Theme strategy (#306): we use Mermaid's `base` theme and feed it
 * `themeVariables` derived from the site's CSS custom properties at
 * render time. This keeps diagrams in lock-step with the page palette in
 * both light and dark mode, even when the user toggles between them.
 *
 * The site's dark mode is fully client-side: window.themeToggle.toggle()
 * just flips the `dark` class on <html> -- no page reload. We watch that
 * class with a MutationObserver, restore the original markdown source
 * (cached in data-mermaid-src on first render), drop data-processed, and
 * re-run mermaid with fresh themeVariables.
 *
 * If the import or the render fails (CDN blocked, malformed diagram),
 * the original source remains visible as text -- no thrown error breaks
 * unrelated page scripts.
 */
(function () {
  /**
   * Read a CSS custom property from <html> and wrap the raw HSL triple
   * in `hsl(...)` so Mermaid can use it as a color. The site stores the
   * tokens as raw triples like `0 0% 4%` so Tailwind can compose
   * `hsl(var(--background))`. Returns '' if the token is missing so
   * Mermaid falls back to its own base defaults for that variable.
   */
  function readToken(name) {
    var raw = getComputedStyle(document.documentElement)
      .getPropertyValue(name)
      .trim();
    return raw ? 'hsl(' + raw + ')' : '';
  }

  /**
   * Build the Mermaid `themeVariables` object from the site's CSS
   * tokens. Mapping rationale (see #306 spec):
   *
   *   background          -> --background  (outer canvas)
   *   primaryColor/mainBkg-> --card        (node fill)
   *   primaryTextColor    -> --foreground  (node + title text)
   *   primaryBorderColor  -> --border      (node + cluster border)
   *   secondaryColor/clusterBkg -> --muted (subgraph backgrounds)
   *   lineColor           -> --accent      (edges/arrows pop with brand)
   *   edgeLabelBackground -> --background  (label boxes match canvas)
   */
  function buildThemeVariables() {
    var bg = readToken('--background');
    var fg = readToken('--foreground');
    var card = readToken('--card');
    var muted = readToken('--muted');
    var mutedFg = readToken('--muted-foreground');
    var accent = readToken('--accent');
    var border = readToken('--border');
    return {
      background: bg,
      primaryColor: card,
      primaryTextColor: fg,
      primaryBorderColor: border,
      secondaryColor: muted,
      secondaryTextColor: fg,
      secondaryBorderColor: border,
      tertiaryColor: bg,
      tertiaryTextColor: mutedFg,
      tertiaryBorderColor: border,
      lineColor: accent,
      textColor: fg,
      mainBkg: card,
      nodeBorder: border,
      clusterBkg: muted,
      clusterBorder: border,
      edgeLabelBackground: bg,
      titleColor: fg,
      // Sequence + flowchart commonly read these too.
      actorBkg: card,
      actorBorder: border,
      actorTextColor: fg,
      actorLineColor: accent,
      signalColor: fg,
      signalTextColor: fg,
      labelBoxBkgColor: card,
      labelBoxBorderColor: border,
      labelTextColor: fg,
    };
  }

  /**
   * Cache the original markdown source of each div on first render so a
   * theme toggle can restore it before re-running Mermaid (Mermaid
   * replaces textContent with the rendered SVG and stamps
   * data-processed="true" on the node, so we cannot re-render in place
   * without first rolling that back).
   */
  function captureSources(nodes) {
    nodes.forEach(function (n) {
      if (n.dataset.mermaidSrc === undefined) {
        n.dataset.mermaidSrc = n.textContent;
      }
    });
  }

  /**
   * Restore each diagram's source markdown into the div, drop the
   * data-processed flag, re-initialize Mermaid with fresh themeVariables
   * for the current palette, and re-run. Idempotent: safe to call even
   * when an initial render is still in flight.
   */
  function rerender(mermaid) {
    var nodes = document.querySelectorAll('div.mermaid');
    if (nodes.length === 0) return;
    nodes.forEach(function (n) {
      n.textContent = n.dataset.mermaidSrc || '';
      n.removeAttribute('data-processed');
    });
    mermaid.initialize({
      startOnLoad: false,
      theme: 'base',
      themeVariables: buildThemeVariables(),
      // securityLevel:'strict' disables click handlers and HTML in
      // labels -- defence in depth on top of the server-side escape.
      // The XSS scenario from #300 still passes after re-render.
      securityLevel: 'strict',
    });
    return mermaid.run({ nodes: nodes });
  }

  /**
   * Watch <html>'s class attribute for additions/removals of the `dark`
   * class. window.themeToggle.toggle() flips that class without
   * reloading the page, so without this observer the diagrams would
   * stay frozen in the palette they rendered with on first paint.
   */
  function watchThemeChanges(mermaid) {
    var html = document.documentElement;
    var lastIsDark = html.classList.contains('dark');
    var observer = new MutationObserver(function () {
      var isDark = html.classList.contains('dark');
      if (isDark !== lastIsDark) {
        lastIsDark = isDark;
        rerender(mermaid);
      }
    });
    observer.observe(html, {
      attributes: true,
      attributeFilter: ['class'],
    });
  }

  function renderMermaid() {
    var nodes = document.querySelectorAll('div.mermaid');
    if (nodes.length === 0) return;

    // Cache sources BEFORE Mermaid mutates the DOM, otherwise the
    // textContent we'd read after rendering is the SVG markup, not the
    // original mermaid source.
    captureSources(nodes);

    import('https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs')
      .then(function (mod) {
        var mermaid = mod.default;
        mermaid.initialize({
          startOnLoad: false,
          theme: 'base',
          themeVariables: buildThemeVariables(),
          securityLevel: 'strict',
        });
        // mermaid.run() reads textContent from each node (already
        // un-escaped by the browser when it parsed the div), replaces
        // the contents with the SVG, and sets data-processed="true".
        return mermaid.run({ nodes: nodes }).then(function () {
          // Wire the theme observer once the first render has settled
          // so we never race the initial mermaid.run.
          watchThemeChanges(mermaid);
        });
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
