/*
 * Section anchors (issue #1833).
 *
 * Rendered markdown headings carry slug ids (content/utils/heading_ids.py).
 * This script adds the two client-side pieces:
 *
 * 1. Hover link: each h2/h3/h4 with an id inside `.prose` (excluding
 *    `.prose-note` member notes and code-annotation panels) gets a trailing
 *    `<a class="section-anchor" href="#id">#</a>`. It is invisible until the
 *    heading is hovered or the link is focused (see `.section-anchor` in
 *    assets/css/tailwind.css). It is added here, never in stored HTML, so
 *    excerpts, plain text, RSS and emails never contain the `#`.
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
  // soon as the reader scrolls or interacts, or after PIN_MS.
  var PIN_MS = 15000;
  var pinned = null;
  var pinnedUntil = 0;
  var realignQueued = false;

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
    window.scrollTo({
      top: Math.max(0, target.getBoundingClientRect().top + window.scrollY - headingOffset(target)),
      behavior: 'smooth',
    });
    var settled = false;
    function settle() {
      if (settled) return;
      settled = true;
      window.removeEventListener('scrollend', settle);
      pinHeading(target);
    }
    window.addEventListener('scrollend', settle);
    window.setTimeout(settle, 1000);
  }

  // The md+ gutter position sits left of the heading box. A wrapper that
  // clips overflow (e.g. `overflow-x-auto` on reader/project prose) would
  // hide it, so those headings get the trailing inline position instead.
  var GUTTER_PX = 32;

  function gutterIsClipped(heading) {
    var headingLeft = heading.getBoundingClientRect().left;
    for (var el = heading.parentElement; el && el !== document.body; el = el.parentElement) {
      var style = window.getComputedStyle(el);
      if (style.overflowX !== 'visible' &&
          el.getBoundingClientRect().left > headingLeft - GUTTER_PX) {
        return true;
      }
    }
    return false;
  }

  function addHoverLinks() {
    var headings = document.querySelectorAll(ANCHOR_SELECTOR);
    Array.prototype.forEach.call(headings, function (heading) {
      if (heading.closest('.prose-note, .code-annotations')) return;
      if (heading.querySelector(':scope > a.section-anchor')) return;
      var text = (heading.textContent || '').replace(/\s+/g, ' ').trim();
      var link = document.createElement('a');
      link.className = gutterIsClipped(heading)
        ? 'section-anchor section-anchor-inline'
        : 'section-anchor';
      link.href = '#' + encodeURIComponent(heading.id);
      link.setAttribute('aria-label', 'Link to section: ' + text);
      link.setAttribute('data-testid', 'section-anchor');
      link.textContent = '#';
      // Keep the heading's accessible name equal to its own text; without
      // this a screen reader announces "X Link to section: X".
      if (!heading.hasAttribute('aria-label')) {
        heading.setAttribute('aria-label', text);
      }
      heading.appendChild(link);
    });
  }

  function init() {
    addHoverLinks();
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
