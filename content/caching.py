"""Anonymous-only per-view response cache for public pages.

Every public list/detail view re-queries the database per request; the
CDN contract in ``website/middleware.py`` only helps when a shared edge
is in front of us. This module adds a small server-side cache that is
safe to use on public views:

- Only anonymous requests without a session cookie are cached. Any
  authenticated user, session-bearing request, or non-GET/HEAD request
  bypasses the cache entirely so logged-in pages stay correct per user.
- Responses that set cookies (CSRF, session, analytics) are never
  stored: caching a ``Set-Cookie`` response would replay one user's
  token to another.
- The cache key includes the path, sorted query string, and analytics
  consent state so the consent banner does not leak across states.
"""

from functools import wraps

from django.conf import settings
from django.core.cache import cache

from analytics.consent import analytics_consent_state

ANON_PAGE_CACHE_TIMEOUT = 300
_ANON_PAGE_KEY_PREFIX = "anon-page:v1"


def _anonymous_cache_key(request):
    """Return the cache key for an anonymous public request."""
    query = request.META.get("QUERY_STRING", "")
    consent = analytics_consent_state(request) or "undecided"
    return f"{_ANON_PAGE_KEY_PREFIX}:{consent}:{request.path}?{query}"


def _cache_enabled():
    """Return True when the public-page caches may be used.

    Disabled by default while running tests so cached rows can never
    leak across the test database flush between cases. Tests that
    cover caching itself opt in with
    ``@override_settings(PUBLIC_PAGE_CACHE_ENABLED=True)``.
    """
    return bool(
        getattr(settings, "PUBLIC_PAGE_CACHE_ENABLED", not settings.TESTING),
    )


def _is_cacheable_request(request):
    """Return True when the request is safe to serve from the anon cache."""
    if not _cache_enabled():
        return False
    if request.method not in ("GET", "HEAD"):
        return False
    user = getattr(request, "user", None)
    if getattr(user, "is_authenticated", False):
        return False
    session_cookie = getattr(settings, "SESSION_COOKIE_NAME", "sessionid")
    if request.COOKIES.get(session_cookie):
        return False
    return True


def cache_anonymous_page(timeout=ANON_PAGE_CACHE_TIMEOUT):
    """Cache anonymous GET responses in the default cache backend.

    Bypassed for authenticated/session requests and for responses that
    set cookies. Public views apply this to skip every database query
    on a cache hit.
    """

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not _is_cacheable_request(request):
                return view(request, *args, **kwargs)
            key = _anonymous_cache_key(request)
            cached = cache.get(key)
            if cached is not None:
                return cached
            response = view(request, *args, **kwargs)
            if getattr(response, "cookies", None):
                if response.cookies:
                    return response
            if getattr(response, "status_code", 200) == 200:
                cache.set(key, response, timeout)
            return response

        return wrapper

    return decorator
