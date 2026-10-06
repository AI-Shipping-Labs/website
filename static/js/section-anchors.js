/*
 * Section anchors (issue #1833).
 *
 * Rendered markdown headings carry slug ids (content/utils/heading_ids.py).
 * This script adds the two client-side pieces:
 *
 * 1. Section link: each h2/h3/h4 with an id inside `.prose` (excluding
 *    `.prose-note` member notes and code-annotation panels) gets a trailing
 *    inline `<a class="section-anchor" href="#id">` holding a link icon,
 *    after the heading text. On hover-capable pointers it shows while the
 *    heading is hovered or the link has keyboard focus; on touch
 *    (`@media (hover: none)`) it is always shown, muted, with a 44px tap
 *    target (see `.section-anchor` in assets/css/tailwind.css). Activating
 *    it copies the section URL to the clipboard, sets `location.hash` and
 *    announces "Link copied" through a polite live-region toast. It is added here, never in stored HTML, so
 *    excerpts, plain text, RSS and emails never contain the link.
 *
 * 2. Fragment through sign-in: a gated page renders a teaser, so a
 *    `#section` link has no target. The fragment never reaches the server,
 *    so `?next=` loses it. We remember `{path, hash, savedAt}` under the
 *    sessionStorage key `aisl:pending-section` and, when the member comes
 *    back to the same path (same tab) within 30 minutes and the heading now
 *    exists, restore the hash and scroll to it.
 */
(function () {
  'use strict';

  var STORAGE_KEY = 'aisl:pending-section';
  var MAX_AGE_MS = 30 * 60 * 1000;
  var ANCHOR_SELECTOR = '.prose h2[id], .prose h3[id], .prose h4[id]';

  function targetFor(hash) {
    if (!hash || hash.length < 2) return null;
    var id = hash.slice(1);
    try {
      id = decodeURIComponent(id);
    } catch (e) {
      // Malformed escape: fall back to the raw fragment.
    }
    return document.getElementById(id);
  }

  function readPending() {
    try {
      var raw = window.sessionStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }

  function writePending(value) {
    try {
      window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(value));
    } catch (e) {
      // Storage disabled (private mode, quota): the feature degrades to a
      // normal sign-in that lands at the top of the page.
    }
  }

  function clearPending() {
    try {
      window.sessionStorage.removeItem(STORAGE_KEY);
    } catch (e) {
      // Nothing to clear.
    }
  }

  function whenLoaded(callback) {
    if (document.readyState === 'complete') {
      callback();
    } else {
      window.addEventListener('load', callback, { once: true });
    }
  }

  function savePendingSection() {
    var hash = window.location.hash;
    if (!hash || hash === '#' || targetFor(hash)) return;
    writePending({
      path: window.location.pathname,
      hash: hash,
      savedAt: Date.now(),
    });
  }

  function restorePendingSection() {
    if (window.location.hash) return;
    var pending = readPending();
    if (!pending) return;
    if (typeof pending.savedAt !== 'number' || typeof pending.hash !== 'string' ||
        Date.now() - pending.savedAt > MAX_AGE_MS) {
      clearPending();
      return;
    }
    // Never touch a page the pending section was not saved for.
    if (pending.path !== window.location.pathname) return;
    var target = targetFor(pending.hash);
    // Still gated (e.g. signed in without the tier): keep the entry and let
    // it expire naturally.
    if (!target) return;
    clearPending();
    window.history.replaceState(
      window.history.state,
      '',
      window.location.pathname + window.location.search + pending.hash
    );
    pinHeading(target);
  }

  // Keeping a fragment target in place.
  //
  // Native fragment scrolling picks its position once, before lazy images,
  // fonts and mermaid diagrams above the heading have their final size, so
  // the heading drifts off-screen as they load. It also ignores
  // `scroll-margin-top` when the heading sits in a wrapper that is itself a
  // scroll container (reader/project prose use `overflow-x-auto`). So the
  // target is "pinned": whenever the layout changes (an image loads, the
  // body resizes, the window finishes loading) and the heading is not at its
  // intended offset below the header, we scroll it back. The pin ends as
  // soon as the reader or another script scrolls the page, on the first
  // wheel/touch/key/mouse interaction, or after PIN_MS.
  var PIN_MS = 15000;
  var pinned = null;
  var pinnedUntil = 0;
  var realignQueued = false;
  // The script starts scrolls of its own: the instant correction in
  // realign() and the smooth glide in onHashChange() before scrollend.
  // Both dispatch `scroll` events that must not release the pin, so each
  // marks the window in which its own scroll events are expected. The
  // window is short and is cleared when the glide settles, so it never
  // suppresses a later scroll the script did not start.
  var SELF_SCROLL_MS = 250;
  var GLIDE_MS = 1500;
  var selfScrollUntil = 0;

  function markSelfScroll(durationMs) {
    var until = Date.now() + durationMs;
    if (until > selfScrollUntil) selfScrollUntil = until;
  }

  function isProseHeading(el) {
    return !!(el && /^H[1-6]$/.test(el.tagName) && el.closest('.prose'));
  }

  function headingOffset(heading) {
    return parseFloat(window.getComputedStyle(heading).scrollMarginTop) || 0;
  }

  function realign() {
    realignQueued = false;
    if (!pinned) return;
    if (Date.now() > pinnedUntil || !pinned.isConnected) {
      pinned = null;
      return;
    }
    var delta = pinned.getBoundingClientRect().top - headingOffset(pinned);
    if (Math.abs(delta) > 2) {
      markSelfScroll(SELF_SCROLL_MS);
      window.scrollTo({
        top: Math.max(0, window.scrollY + delta),
        behavior: 'instant',
      });
    }
  }

  function queueRealign() {
    if (!pinned || realignQueued) return;
    realignQueued = true;
    window.requestAnimationFrame(realign);
  }

  function pinHeading(heading) {
    pinned = heading;
    pinnedUntil = Date.now() + PIN_MS;
    queueRealign();
    whenLoaded(queueRealign);
  }

  function unpin() {
    pinned = null;
  }

  // Any scroll the script did not start releases the pin: a scrollbar drag,
  // another script calling scrollBy/scrollTo, an accessibility scroll. The
  // script's own scrolls are excluded through selfScrollUntil, and a scroll
  // that leaves the heading at its intended offset is the browser's own
  // fragment positioning or layout compensation, not the reader leaving.
  function onScroll() {
    if (!pinned) return;
    if (Date.now() < selfScrollUntil) return;
    if (Math.abs(pinned.getBoundingClientRect().top - headingOffset(pinned)) <= 2) {
      return;
    }
    unpin();
  }

  function watchLayout() {
    // `load` does not bubble, so listen in the capture phase for images
    // (lazy or not) that finish loading anywhere on the page.
    document.addEventListener('load', function (event) {
      if (event.target && event.target.tagName === 'IMG') queueRealign();
    }, true);
    if (window.ResizeObserver) {
      new ResizeObserver(queueRealign).observe(document.body);
    }
    ['wheel', 'touchstart', 'keydown', 'mousedown'].forEach(function (name) {
      window.addEventListener(name, unpin, { capture: true, passive: true });
    });
    // Without capture: page scrolls (scrollbar drag, another script's
    // scroll) fire on `window`; scrolls of inner scroll containers such as
    // the reader's `overflow-x-auto` prose must not release the pin.
    window.addEventListener('scroll', onScroll, { passive: true });
  }

  function pinInitialFragment() {
    var target = targetFor(window.location.hash);
    if (isProseHeading(target)) pinHeading(target);
  }

  function onHashChange() {
    var target = targetFor(window.location.hash);
    if (!isProseHeading(target)) return;
    // In-page jumps keep the site's smooth scroll: glide to the intended
    // offset, then pin once the scroll has settled.
    pinned = null;
    // The glide's own scroll events must not release the pin that settle()
    // arms; settle() clears the window so any later scroll counts.
    markSelfScroll(GLIDE_MS);
    window.scrollTo({
      top: Math.max(0, target.getBoundingClientRect().top + window.scrollY - headingOffset(target)),
      behavior: 'smooth',
    });
    var settled = false;
    function settle() {
      if (settled) return;
      settled = true;
      window.removeEventListener('scrollend', settle);
      // The glide has ended (scrollend) or the fallback fired: every scroll
      // from here on is external and releases the pin again.
      selfScrollUntil = 0;
      pinHeading(target);
    }
    window.addEventListener('scrollend', settle);
    window.setTimeout(settle, 1000);
  }

  // Lucide `link` icon, inlined so it does not depend on lucide.createIcons()
  // having run before this script.
  var LINK_ICON_SVG =
    '<svg class="section-anchor-icon" xmlns="http://www.w3.org/2000/svg" ' +
    'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">' +
    '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>' +
    '<path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>' +
    '</svg>';
  var TOAST_MS = 2000;
  var toast = null;
  var toastTimer = null;

  function ensureToast() {
    if (toast && toast.isConnected) return toast;
    // Created up front (empty) so assistive tech has registered the live
    // region before the first announcement.
    toast = document.createElement('div');
    toast.className = 'section-anchor-toast';
    toast.setAttribute('role', 'status');
    toast.setAttribute('aria-live', 'polite');
    toast.setAttribute('data-testid', 'section-anchor-toast');
    document.body.appendChild(toast);
    return toast;
  }

  // Height of whatever is pinned over the bottom of the viewport (consent
  // banner, a reader's sticky bottom bar), so the toast sits above it.
  // Floating panels keep a gap to the viewport edge, so sample a band of
  // points up the bottom-centre rather than only the last pixel row.
  var CLEARANCE_SAMPLES = [1, 8, 16, 24, 32, 48];

  function bottomClearance(toastEl) {
    if (!document.elementsFromPoint) return 0;
    var clearance = 0;
    var viewport = window.innerHeight;
    var seen = [];
    CLEARANCE_SAMPLES.forEach(function (offset) {
      document.elementsFromPoint(window.innerWidth / 2, viewport - offset)
        .forEach(function (el) {
          for (var node = el; node && node !== document.body; node = node.parentElement) {
            if (node === toastEl) return;
            if (seen.indexOf(node) !== -1) return;
            var position = window.getComputedStyle(node).position;
            if (position === 'fixed' || position === 'sticky') {
              seen.push(node);
              clearance = Math.max(clearance, viewport - node.getBoundingClientRect().top);
              return;
            }
          }
        });
    });
    return Math.min(clearance, viewport / 2);
  }

  function showToast(message) {
    var el = ensureToast();
    el.style.setProperty(
      '--section-anchor-toast-clearance', Math.ceil(bottomClearance(el)) + 'px'
    );
    el.textContent = message;
    el.classList.add('is-visible');
    if (toastTimer) window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () {
      el.classList.remove('is-visible');
      toastTimer = null;
    }, TOAST_MS);
  }

  function legacyCopy(text) {
    var field = document.createElement('textarea');
    field.value = text;
    field.setAttribute('readonly', '');
    field.style.position = 'fixed';
    field.style.top = '0';
    field.style.opacity = '0';
    document.body.appendChild(field);
    field.select();
    var ok = false;
    try {
      ok = document.execCommand('copy');
    } catch (e) {
      ok = false;
    }
    field.remove();
    return ok;
  }

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text).then(
        function () { return true; },
        function () { return legacyCopy(text); }
      );
    }
    return Promise.resolve(legacyCopy(text));
  }

  function onAnchorClick(event) {
    var link = event.target.closest && event.target.closest('a.section-anchor');
    if (!link) return;
    // Let modified clicks (new tab, new window) keep the browser default.
    if (event.button !== 0 || event.metaKey || event.ctrlKey ||
        event.shiftKey || event.altKey) {
      return;
    }
    var heading = link.parentElement;
    if (!heading || !heading.id) return;
    event.preventDefault();
    var hash = '#' + encodeURIComponent(heading.id);
    var url = window.location.href.split('#')[0] + hash;
    if (window.location.hash === hash) {
      onHashChange();
    } else {
      window.location.hash = hash;
    }
    copyText(url).then(function (ok) {
      showToast(ok ? 'Link copied' : 'Link ready in the address bar');
    });
  }

  function addSectionLinks() {
    var headings = document.querySelectorAll(ANCHOR_SELECTOR);
    Array.prototype.forEach.call(headings, function (heading) {
      if (heading.closest('.prose-note, .code-annotations')) return;
      if (heading.querySelector(':scope > a.section-anchor')) return;
      var text = (heading.textContent || '').replace(/\s+/g, ' ').trim();
      var link = document.createElement('a');
      link.className = 'section-anchor';
      link.href = '#' + encodeURIComponent(heading.id);
      link.setAttribute('aria-label', 'Link to section: ' + text);
      link.setAttribute('data-testid', 'section-anchor');
      // U+2060 WORD JOINER forbids a line break between the heading's last
      // word and the icon, so on a long title the icon wraps with that word
      // instead of dangling alone on a new line.
      link.innerHTML = '\u2060' + LINK_ICON_SVG;
      // Keep the heading's accessible name equal to its own text; without
      // this a screen reader announces "X Link to section: X".
      if (!heading.hasAttribute('aria-label')) {
        heading.setAttribute('aria-label', text);
      }
      heading.appendChild(link);
    });
  }

  function init() {
    addSectionLinks();
    if (document.querySelector('a.section-anchor')) {
      ensureToast();
      document.addEventListener('click', onAnchorClick);
    }
    savePendingSection();
    watchLayout();
    restorePendingSection();
    pinInitialFragment();
    window.addEventListener('hashchange', onHashChange);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
