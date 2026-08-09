"""Server-level middleware that runs before host validation."""

from django.conf import settings
from django.http import HttpResponse

from website.search_indexing import (
    NOINDEX_ROBOTS_DIRECTIVE,
    request_indexing_disabled,
)


class SearchIndexingPolicyMiddleware:
    """Apply the central environment/route/state robots response policy.

    This middleware deliberately sits outside ``HealthCheckMiddleware`` so
    even early responses, redirects, errors, and non-HTML responses can carry
    the applicable exclusion directive without changing their body or status.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request_indexing_disabled(request):
            response['X-Robots-Tag'] = NOINDEX_ROBOTS_DIRECTIVE
        return response


# Compatibility import for tests/extensions written against issue #1376.
DevSearchExclusionMiddleware = SearchIndexingPolicyMiddleware


class HealthCheckMiddleware:
    """Respond to ``/ping`` with 200 + VERSION before any host-validation runs.

    The ALB health check probes the container's VPC IP directly (e.g.
    ``10.0.1.189:8000``), so the request's Host header is the IP, not a
    public domain. Django's ``CommonMiddleware`` calls
    ``request.get_host()`` which validates against ``ALLOWED_HOSTS`` and
    raises ``DisallowedHost`` for any host not on the list. Returning 200
    here short-circuits the request before that check, so ``ALLOWED_HOSTS``
    can stay strict (no wildcard / no IP whitelist) while health checks
    still pass.

    Must be placed before host-validating middleware. The dev search-exclusion
    policy wrapper is allowed ahead of it because it does not inspect the
    request host and still lets this middleware short-circuit the request.

    The body is the ``settings.VERSION`` string (e.g.
    ``20260426-130731-b126a1e``) so the post-deploy Verify step can curl
    ``/ping`` and string-compare against the expected commit hash without
    parsing HTML. ALB only checks the status code, so the body is free
    real estate.
    """

    PATH = '/ping'

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == self.PATH:
            return HttpResponse(
                settings.VERSION or 'N/A',
                status=200,
                content_type='text/plain',
            )
        return self.get_response(request)
