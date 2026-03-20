"""Middleware for configurable URL redirects."""

from django.http import HttpResponsePermanentRedirect, HttpResponseRedirect
from django.core.cache import cache

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
