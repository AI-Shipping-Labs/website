"""Studio / accounts cache-header middleware.

Defensive cache headers for authenticated Studio and account-management
pages so that no browser back-forward cache, service worker, or future
intermediary CDN can ever serve one user's HTML to another. See issue
``#347`` for the bug that motivated this — django-allauth's
"Successfully signed in as ..." flash leaked between viewers because the
HTML containing the message was cacheable.

Behaviour:
    For every response whose ``request.path`` begins with ``/studio/``
    or ``/accounts/``, set:

    - ``Cache-Control: private, no-store`` — never cache, anywhere.
    - ``Vary: Cookie`` — appended to any existing ``Vary`` header so
      that any well-behaved cache that does observe these responses
      keys them by the session cookie.

    Public pages (``/``, ``/blog/``, ``/courses/``, etc.) are left
    untouched so existing public-page caching behaviour is preserved.
"""

from django.utils.cache import patch_vary_headers

PROTECTED_PREFIXES = ('/studio/', '/accounts/')


class StudioNoStoreMiddleware:
    """Mark ``/studio/*`` and ``/accounts/*`` responses as uncacheable.

    Wired into ``MIDDLEWARE`` in ``website/settings.py``. Runs on every
    response: matching paths get ``Cache-Control: private, no-store``
    (overwriting any prior value) and ``Vary: Cookie`` (appended).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        path = request.path or ''
        if path.startswith(PROTECTED_PREFIXES):
            # Overwrite any cache-control hints from upstream views;
            # private + no-store is the strictest combo and is what we
            # want for any HTML that could contain a flash message,
            # CSRF-bound form, or per-user content.
            response['Cache-Control'] = 'private, no-store'
            patch_vary_headers(response, ('Cookie',))
        return response
