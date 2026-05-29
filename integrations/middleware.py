"""Middleware for URL redirects and trailing slash removal."""

from django.core.cache import caches
from django.http import HttpResponsePermanentRedirect, HttpResponseRedirect


class RemoveTrailingSlashMiddleware:
    """Redirect URLs with trailing slashes to the version without.

    Skips the root URL ('/') and paths under prefixes that use trailing slashes
    (admin, accounts, allauth, studio, Django static/media).
    """

    SKIP_PREFIXES = ('/admin/', '/accounts/', '/account/', '/onboarding/', '/studio/', '/static/', '/media/')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if path != '/' and path.endswith('/'):
            if not any(path.startswith(p) for p in self.SKIP_PREFIXES):
                new_path = path.rstrip('/')
                if request.META.get('QUERY_STRING'):
                    new_path = f'{new_path}?{request.META["QUERY_STRING"]}'
                return HttpResponsePermanentRedirect(new_path)
        return self.get_response(request)

# --- Announcement banner cross-process cache -----------------------------
# The banner singleton is read on every public page request, so we cache the
# row in the cross-process ``django_q`` cache (DatabaseCache in production,
# LocMemCache in tests) instead of a module-level dict. A module-level dict
# is per-process, so any save in Studio or the Django admin only invalidates
# the worker that handled the POST — every other gunicorn / django-q worker
# would keep serving the stale value until restart. See issue #288.
_BANNER_CACHE_KEY = 'announcement_banner:v1'
_BANNER_CACHE_TTL = 300  # 5 minutes — matches REDIRECT_CACHE_TIMEOUT below.
# Sentinel for "no row exists". ``cache.get`` returns ``None`` for both
# "missing key" and "stored value of None", so we need a distinct marker to
# avoid hitting the DB on every request when the banner row is absent.
_BANNER_CACHE_MISSING = '__missing__'


def get_announcement_banner():
    """Return the AnnouncementBanner singleton or None if no row exists.

    The result is cached in the cross-process ``django_q`` cache. Call
    ``clear_announcement_banner_cache`` after saving the banner to invalidate.
    """
    cached = caches['django_q'].get(_BANNER_CACHE_KEY)
    if cached == _BANNER_CACHE_MISSING:
        return None
    if cached is not None:
        return cached
    from integrations.models import AnnouncementBanner
    banner = AnnouncementBanner.objects.filter(pk=1).first()
    caches['django_q'].set(
        _BANNER_CACHE_KEY,
        banner if banner is not None else _BANNER_CACHE_MISSING,
        _BANNER_CACHE_TTL,
    )
    return banner


def clear_announcement_banner_cache():
    """Clear the cross-process announcement banner cache."""
    caches['django_q'].delete(_BANNER_CACHE_KEY)


REDIRECT_CACHE_KEY = 'active_redirects'
REDIRECT_CACHE_TIMEOUT = 300  # 5 minutes


def get_active_redirects():
    """Return a dict of active redirects, cached for performance.

    The result is cached in the cross-process ``django_q`` cache
    (DatabaseCache in production, LocMemCache in tests). The default
    Django cache is ``LocMemCache``, which is per-process: each gunicorn
    worker maintains its own copy, so a ``clear_redirect_cache()`` call
    from Studio would only invalidate the worker that handled the POST.
    Using a cross-process backend ensures any toggle / delete in Studio
    propagates to every worker on the next request. See issue #695.
    """
    redirects = caches['django_q'].get(REDIRECT_CACHE_KEY)
    if redirects is None:
        from integrations.models import Redirect
        redirects = {
            r.source_path: (r.target_path, r.redirect_type)
            for r in Redirect.objects.filter(is_active=True)
        }
        caches['django_q'].set(REDIRECT_CACHE_KEY, redirects, REDIRECT_CACHE_TIMEOUT)
    return redirects


def clear_redirect_cache():
    """Clear the cross-process redirect cache.

    Call after any redirect model change so every worker refetches on
    the next request.
    """
    caches['django_q'].delete(REDIRECT_CACHE_KEY)


class RedirectMiddleware:
    """Middleware that checks incoming requests against active redirects."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        redirects = get_active_redirects()

        if path in redirects:
            target_path, redirect_type = redirects[path]
            if redirect_type == 301:
                return HttpResponsePermanentRedirect(target_path)
            return HttpResponseRedirect(target_path)

        response = self.get_response(request)
        return response
