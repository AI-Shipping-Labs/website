"""Server-level middleware that runs before host validation."""

from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponse, HttpResponsePermanentRedirect
from django.http.request import split_domain_port

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


def _production_canonical_redirect_config():
    """Return the configured production canonical origin and host pair.

    Routing uses the process-start ``SITE_BASE_URL`` setting rather than the
    Studio-editable integration override.  Requiring both the canonical host
    and its exact ``www`` alias in ``ALLOWED_HOSTS`` limits this redirect to
    the production deployment; dev and local environments do not advertise
    that alias pair.
    """
    try:
        parsed = urlsplit(str(settings.SITE_BASE_URL).strip())
        port = parsed.port
    except ValueError:
        return None

    if (
        parsed.scheme.lower() != 'https'
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.path not in ('', '/')
        or parsed.query
        or parsed.fragment
    ):
        return None

    canonical_host = parsed.hostname.lower().rstrip('.')
    alias_host = f'www.{canonical_host}'
    allowed_hosts = {
        str(host).strip().lower().rstrip('.')
        for host in settings.ALLOWED_HOSTS
    }
    if not {canonical_host, alias_host}.issubset(allowed_hosts):
        return None

    return f'https://{canonical_host}', canonical_host, alias_host


class CanonicalHostRedirectMiddleware:
    """Redirect production HTTP/``www`` requests to clean apex HTTPS."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        redirect_config = _production_canonical_redirect_config()
        if redirect_config is None:
            return self.get_response(request)

        canonical_origin, canonical_host, alias_host = redirect_config

        # ``get_host`` deliberately performs Django's normal ALLOWED_HOSTS
        # validation before we decide to redirect.  Unknown hosts therefore
        # remain 400 responses instead of becoming an open redirect.
        request_host, _port = split_domain_port(request.get_host().lower())
        request_host = request_host.rstrip('.')
        should_redirect = request_host == alias_host or (
            request_host == canonical_host and not request.is_secure()
        )
        if should_redirect:
            return HttpResponsePermanentRedirect(
                f'{canonical_origin}{request.get_full_path()}',
            )

        return self.get_response(request)
