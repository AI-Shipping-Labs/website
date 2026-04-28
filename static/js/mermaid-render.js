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
   * tokens. Mapping rationale (see #306 spec, updated for #359):
   *
   *   background          -> --background  (outer canvas)
   *   primaryColor/mainBkg-> --card        (node fill)
   *   primaryTextColor    -> --foreground  (node + title text)
   *   primaryBorderColor  -> --accent      (node + cluster border, #359)
   *   secondaryColor/clusterBkg -> --muted (subgraph backgrounds)
   *   lineColor           -> --accent      (edges/arrows pop with brand)
   *   edgeLabelBackground -> --background  (label boxes match canvas)
   *
   * Why borders use --accent (#359): Mermaid sets `stroke` as an SVG
   * attribute, so CSS overrides would lose specificity. The previous
   * --border token (HSL ~18% L in dark mode) had ~1.2:1 contrast with
   * --card (HSL 7% L), making nodes look borderless on dark cards.
   * --accent is the brand-yellow already used for `lineColor`/edges and
   * provides good contrast against --card in both light and dark
   * palettes, so node outlines pop the same way edges already do.
   */
  function buildThemeVariables() {
    var bg = readToken('--background');
    var fg = readToken('--foreground');
    var card = readToken('--card');
    var muted = readToken('--muted');
    var mutedFg = readToken('--muted-foreground');
    var accent = readToken('--accent');
    return {
      background: bg,
      primaryColor: card,
      primaryTextColor: fg,
      primaryBorderColor: accent,
      secondaryColor: muted,
      secondaryTextColor: fg,
      secondaryBorderColor: accent,
      tertiaryColor: bg,
      tertiaryTextColor: mutedFg,
      tertiaryBorderColor: accent,
      lineColor: accent,
      textColor: fg,
      mainBkg: card,
      nodeBorder: accent,
      clusterBkg: muted,
      clusterBorder: accent,
      edgeLabelBackground: bg,
      titleColor: fg,
      // Sequence + flowchart commonly read these too.
      actorBkg: card,
      actorBorder: accent,
      actorTextColor: fg,
      actorLineColor: accent,
      signalColor: fg,
      signalTextColor: fg,
      labelBoxBkgColor: card,
      labelBoxBorderColor: accent,
      labelTextColor: fg,
    };
  }

  /**
   * Lock each rendered SVG to its intrinsic viewBox width (#359).
   *
   * Mermaid 10 emits the rendered SVG with `width="100%"` and an inline
   * `style="max-width: <natural>px"`. The CSS rule in
   * `templates/_partials/mermaid_script.html` clears the `max-width`
   * cap, but the `width="100%"` attribute still makes the SVG shrink
   * to the container width on narrow viewports -- which is exactly
   * what the auto-shrink fix was meant to prevent. Setting
   * `style.width = <viewBox-width>px` overrides the attribute by
   * specificity (inline style beats presentation attribute) and locks
   * the SVG to the intrinsic content size, so the surrounding
   * `div.mermaid` scrolls horizontally on mobile while the diagram
   * stays at a readable size.
   */
  function lockSvgWidthToViewBox(nodes) {
    nodes.forEach(function (n) {
      var svg = n.querySelector('svg');
      if (!svg || !svg.viewBox || !svg.viewBox.baseVal) return;
      var w = svg.viewBox.baseVal.width;
      if (w > 0) {
        svg.style.width = w + 'px';
        svg.style.maxWidth = 'none';
      }
    });
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
    return mermaid.run({ nodes: nodes }).then(function () {
      lockSvgWidthToViewBox(nodes);
    });
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
          // Lock each SVG to its viewBox width so wide diagrams keep
          // their natural rendered size on narrow viewports (#359).
          lockSvgWidthToViewBox(nodes);
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
