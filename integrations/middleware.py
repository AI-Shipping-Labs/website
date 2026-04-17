"""Middleware for URL redirects and trailing slash removal."""

from django.core.cache import cache
from django.http import HttpResponsePermanentRedirect, HttpResponseRedirect


class RemoveTrailingSlashMiddleware:
    """Redirect URLs with trailing slashes to the version without.

    Skips the root URL ('/') and paths under prefixes that use trailing slashes
    (admin, accounts, allauth, studio, Django static/media).
    """

    SKIP_PREFIXES = ('/admin/', '/accounts/', '/account/', '/studio/', '/static/', '/media/')

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

# --- Announcement banner in-process cache --------------------------------
# Keep a module-level reference to the singleton so we don't hit the DB on
# every request. Cleared after the banner is saved in the studio view.
_ANNOUNCEMENT_BANNER_CACHE: dict = {'value': None, 'loaded': False}


def get_announcement_banner():
    """Return the AnnouncementBanner singleton or None if no row exists.

    The result is cached in-process. Call ``clear_announcement_banner_cache``
    after saving the banner to invalidate.
    """
    if not _ANNOUNCEMENT_BANNER_CACHE['loaded']:
        from integrations.models import AnnouncementBanner
        _ANNOUNCEMENT_BANNER_CACHE['value'] = AnnouncementBanner.objects.filter(pk=1).first()
        _ANNOUNCEMENT_BANNER_CACHE['loaded'] = True
    return _ANNOUNCEMENT_BANNER_CACHE['value']


def clear_announcement_banner_cache():
    """Clear the in-process announcement banner cache."""
    _ANNOUNCEMENT_BANNER_CACHE['value'] = None
    _ANNOUNCEMENT_BANNER_CACHE['loaded'] = False


REDIRECT_CACHE_KEY = 'active_redirects'
REDIRECT_CACHE_TIMEOUT = 300  # 5 minutes


def get_active_redirects():
    """Return a dict of active redirects, cached for performance."""
    redirects = cache.get(REDIRECT_CACHE_KEY)
    if redirects is None:
        from integrations.models import Redirect
        redirects = {
            r.source_path: (r.target_path, r.redirect_type)
            for r in Redirect.objects.filter(is_active=True)
        }
        cache.set(REDIRECT_CACHE_KEY, redirects, REDIRECT_CACHE_TIMEOUT)
    return redirects


def clear_redirect_cache():
    """Clear the redirect cache. Call after any redirect model change."""
    cache.delete(REDIRECT_CACHE_KEY)


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
