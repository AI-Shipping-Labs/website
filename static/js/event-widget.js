/* Event claim widget hydration (issue #1070).
 *
 * The python-markdown side (content/markdown_extensions/event_widget.py)
 * emits a stable, user-agnostic placeholder:
 *
 *     <div class="event-widget" data-event-widget="<slug>"></div>
 *
 * Because the surrounding HTML is rendered once at save and cached, NO
 * per-user state is baked in. This script runs only when at least one
 * such node is present, fetches the per-user state from the authed
 * endpoint, and renders the right control — the same hydration pattern
 * the notification bell uses.
 *
 * Django is always the trust boundary: the claim POST re-checks min_level
 * and dedup server-side. This script is a thin renderer; it never decides
 * eligibility on its own.
 */
(function () {
  function getCsrfToken() {
    var cookie = document.cookie.split(';').find(function (c) {
      return c.trim().startsWith('csrftoken=');
    });
    return cookie ? cookie.split('=')[1] : '';
  }

  function clear(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function renderMessage(node, text, testid) {
    clear(node);
    var p = document.createElement('p');
    p.className = 'event-widget-message text-sm text-muted-foreground';
    if (testid) {
      p.setAttribute('data-testid', testid);
    }
    p.textContent = text;
    node.appendChild(p);
  }

  function renderLink(node, href, label, testid) {
    clear(node);
    var a = document.createElement('a');
    a.href = href;
    a.className =
      'event-widget-link inline-flex items-center rounded-lg bg-accent ' +
      'px-4 py-2 text-sm font-medium text-accent-foreground hover:opacity-90';
    if (testid) {
      a.setAttribute('data-testid', testid);
    }
    a.textContent = label;
    node.appendChild(a);
  }

  function renderClaimable(node, slug, data) {
    clear(node);
    node.classList.add('event-widget-card');

    if (data.claim_body) {
      var body = document.createElement('p');
      body.className = 'event-widget-body mb-3 text-sm text-foreground';
      body.setAttribute('data-testid', 'event-widget-body');
      body.textContent = data.claim_body;
      node.appendChild(body);
    }

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className =
      'event-widget-claim inline-flex items-center rounded-lg bg-accent ' +
      'px-4 py-2 text-sm font-medium text-accent-foreground hover:opacity-90 ' +
      'disabled:opacity-60';
    btn.setAttribute('data-testid', 'event-widget-claim');
    btn.textContent = data.claim_label || 'Claim';
    btn.addEventListener('click', function () {
      btn.disabled = true;
      claim(node, slug);
    });
    node.appendChild(btn);
  }

  function renderState(node, slug, data) {
    var state = data.state;
    if (state === 'claimable') {
      renderClaimable(node, slug, data);
    } else if (state === 'claimed') {
      renderMessage(
        node,
        data.claimed_label || 'Claimed',
        'event-widget-claimed'
      );
    } else if (state === 'signin_required') {
      renderLink(
        node,
        data.login_url || '/accounts/login/',
        data.signin_cta || 'Sign in to claim',
        'event-widget-signin'
      );
    } else if (state === 'under_level') {
      renderMessage(
        node,
        'Your membership level is not eligible for this claim.',
        'event-widget-under-level'
      );
    } else if (state === 'paused') {
      renderMessage(
        node,
        'Claims are paused right now. Please check back later.',
        'event-widget-paused'
      );
    } else if (state === 'rate_limited') {
      renderMessage(
        node,
        data.error || 'Too many claim attempts. Please wait a minute and try again.',
        'event-widget-rate-limited'
      );
    } else {
      // unavailable / unknown: render nothing visible.
      clear(node);
    }
  }

  function fetchState(node, slug) {
    fetch('/widgets/' + encodeURIComponent(slug) + '/state', {
      headers: { Accept: 'application/json' },
      credentials: 'same-origin'
    })
      .then(function (resp) {
        return resp.json();
      })
      .then(function (data) {
        renderState(node, slug, data);
      })
      .catch(function () {
        // On a transport error render nothing rather than break the page.
        clear(node);
      });
  }

  function claim(node, slug) {
    fetch('/widgets/' + encodeURIComponent(slug) + '/claim', {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCsrfToken(),
        'Content-Type': 'application/json',
        Accept: 'application/json'
      },
      credentials: 'same-origin'
    })
      .then(function (resp) {
        return resp.json();
      })
      .then(function (data) {
        renderState(node, slug, data);
      })
      .catch(function () {
        renderMessage(
          node,
          'Something went wrong. Please try again.',
          'event-widget-error'
        );
      });
  }

  function init() {
    var nodes = document.querySelectorAll('[data-event-widget]');
    if (!nodes.length) {
      return;
    }
    nodes.forEach(function (node) {
      var slug = node.getAttribute('data-event-widget');
      if (!slug) {
        return;
      }
      fetchState(node, slug);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
