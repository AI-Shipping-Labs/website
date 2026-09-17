"""Custom ``handler404`` for a friendly, fully-chromed 404 page (#1724).

Issue #1720 dropped the ``/tutorial/`` segment from workshop page URLs with
no redirect, leaving roughly 270 previously-indexed
``/workshops/<slug>/tutorial/<page_slug>`` URLs returning a bare 404 with no
site chrome. This module supplies the friendly ``templates/404.html`` page
and, when the dead path sits under a still-published workshop slug, a
visible secondary link back to that workshop's live landing page.

Django's URLconf never needs an explicit ``handler404`` assignment to pick
up a ``404.html`` template — its built-in default already does that. This
custom handler exists only because the workshop-link derivation needs
Python: it must inspect the requested path, look up a ``Workshop`` row, and
choose between a workshop-specific link, a generic workshops-catalog link,
or no secondary link at all, all without ever issuing a redirect (the 404
status and body are all this view controls; it never sets ``Location``).

Taking over the view also takes over its template context, so the handler
reproduces the ``exception``/``request_path`` keys Django's own
``page_not_found`` supplies (see ``default_404_context``).
"""

import copy
import re
from urllib.parse import quote

from django.http import HttpResponseNotFound
from django.template import loader

from content.models import Workshop

# Matches the first path segment after /workshops/, covering both the
# removed .../tutorial/<page_slug> shape and any other dead path nested
# under a workshop slug (e.g. .../video, a mistyped page slug, etc).
_WORKSHOP_PATH_RE = re.compile(r'^/workshops/([^/]+)(?:/|$)')

WORKSHOPS_CATALOG_LABEL = 'Browse workshops'
WORKSHOPS_CATALOG_URL = '/workshops'


def resolve_workshop_secondary_cta(path):
    """Return ``(label, url)`` for the 404 page's secondary CTA, or ``None``.

    Pure function of the requested path string so it is directly
    unit-testable without a request/response cycle:

    - Path outside ``/workshops/`` -> ``None`` (no secondary CTA).
    - Path under ``/workshops/<slug>/...`` where ``<slug>`` matches a
      published ``Workshop`` -> that workshop's title and landing URL.
    - Path under ``/workshops/`` with no matching published workshop
      (unknown or unpublished slug) -> the generic workshops catalog link.
    """
    match = _WORKSHOP_PATH_RE.match(path or '')
    if not match:
        return None
    slug = match.group(1)
    workshop = Workshop.objects.filter(slug=slug, status='published').first()
    if workshop is not None:
        return (workshop.title, workshop.get_absolute_url())
    return (WORKSHOPS_CATALOG_LABEL, WORKSHOPS_CATALOG_URL)


def _request_with_scrubbed_query_string(request):
    """Return a shallow-copied request whose query string is cleared.

    The header's ``login_url``/``logout_url`` tags and the unverified-email
    banner all build a ``?next=`` round-trip from ``request.get_full_path()``
    so a visitor lands back where they were after signing in. On a 404 that
    "where they were" is a dead URL, and its query string was never
    validated or intended for display — it can be anything an enumerator
    put there (see the retired host-management routes' non-disclosing-404
    contract, #1550). Reflecting it back into the page would leak it. This
    only touches the copy used for rendering; the original ``request`` (and
    anything else the error-handling pipeline does with it, like access
    logs) is untouched. ``get_full_path()`` reads ``META['QUERY_STRING']``
    fresh on every call, so clearing it here is sufficient without touching
    ``request.GET``.
    """
    scrubbed = copy.copy(request)
    scrubbed.META = {**request.META, 'QUERY_STRING': ''}
    return scrubbed


def default_404_context(request, exception):
    """Return the context keys Django's own ``page_not_found`` view supplies.

    Replacing Django's default 404 view replaced its template context too,
    and that context is a contract: ``exception`` (the ``Http404``
    message, which views raise as human-readable text like "This reader has
    not started this book.") and ``request_path``. Tests and templates
    across the site read them -- ``bookclub`` asserts on ``exception`` to
    pin down *which* 404 a view raised -- so dropping them silently turned
    an unrelated passing test into a ``KeyError`` (#1731).

    The derivation mirrors ``django.views.defaults.page_not_found``: prefer
    the exception's first argument when it is a string, otherwise fall back
    to the exception class name (``Resolver404`` carries a dict there, not a
    message). Neither key is rendered by ``templates/404.html``: an
    ``Http404`` message can name a private object, and 404s that exist to
    avoid disclosing one (#1550) must keep saying nothing.
    """
    if exception is None:
        # Django always passes the exception, but ``handler404`` keeps a
        # default so it stays directly callable; ``NoneType`` would be a
        # nonsense message, the HTTP reason phrase is not.
        return {'request_path': quote(request.path), 'exception': 'Not Found'}
    exception_repr = exception.__class__.__name__
    try:
        message = exception.args[0]
    except (AttributeError, IndexError):
        pass
    else:
        if isinstance(message, str):
            exception_repr = message
    return {
        'request_path': quote(request.path),
        'exception': exception_repr,
    }


def handler404(request, exception=None):
    """Render the friendly 404 page as a genuine, non-redirecting 404.

    Rendered with ``request`` (via ``loader.render_to_string``'s ``request``
    kwarg) so the same context processors that power every other page run
    here too, letting ``templates/404.html`` extend ``base.html`` and
    include the normal header/footer.
    """
    context = default_404_context(request, exception)
    secondary_cta = resolve_workshop_secondary_cta(request.path)
    if secondary_cta is not None:
        context['secondary_cta_label'], context['secondary_cta_url'] = (
            secondary_cta
        )
    render_request = _request_with_scrubbed_query_string(request)
    body = loader.render_to_string('404.html', context, request=render_request)
    return HttpResponseNotFound(body)
