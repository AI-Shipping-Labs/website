"""Server-level middleware that runs before host validation."""

import re
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponse, HttpResponsePermanentRedirect
from django.http.request import split_domain_port
from django.utils.cache import patch_vary_headers

from website.search_indexing import (
    NOINDEX_ROBOTS_DIRECTIVE,
    request_indexing_disabled,
)

# Paths whose responses must never be cached anywhere (CDN, browser,
# anything in between): authenticated surfaces, APIs, and admin tooling.
# ``/accounts/``, ``/account/`` and ``/studio/`` are also covered by
# StudioNoStoreMiddleware; they are listed here so the CDN contract in
# this middleware is self-contained.
UNCACHEABLE_PREFIXES = (
    '/accounts/',
    '/account/',
    '/admin/',
    '/api/',
    '/member-api/',
    '/onboarding/',
    '/studio/',
)

# Edge caches may keep anonymous public HTML for this long; browsers
# revalidate every time (max-age=0). Matches the CloudFront cache policy
# default TTL in aws-infra.
PUBLIC_HTML_SMAXAGE = 300


class PublicCachePolicyMiddleware:
    """CDN-facing Cache-Control policy for public pages.

    Prepares responses for a CDN (CloudFront) in front of the site that
    may cache anonymous public HTML but must never cache anything tied
    to a user, session, or cookie. The contract, in priority order:

    1. ``UNCACHEABLE_PREFIXES`` responses -> ``private, no-store``.
    2. Any response that sets or deletes cookies, or any request that
       carries a session cookie -> ``private, no-store``. This is the
       enforcement half of the CDN rule "never cache a response with
       Set-Cookie": by the time this middleware runs (top of the stack,
       so after every cookie-setting middleware below has produced its
       response), any Set-Cookie the app would emit is already visible.
    3. Anonymous 200 HTML -> ``public, max-age=0, s-maxage=300`` — the
       only response class the CDN is allowed to cache.
    4. Everything else (non-HTML 200s like sitemap.xml/robots.txt,
       redirects, 404s without cookies) is left untouched; the CDN's
       default TTL applies.

    Middleware order matters: this must sit ABOVE SessionMiddleware,
    CsrfViewMiddleware, allauth, and CampaignTrackingMiddleware in
    ``MIDDLEWARE``. Response hooks run bottom-up, so only a position
    above those sees the sessionid, csrftoken, and analytics cookies
    they add — and can flip such responses to ``no-store``.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        path = request.path or ''
        session_cookie = getattr(settings, 'SESSION_COOKIE_NAME', 'sessionid')
        sets_cookies = bool(getattr(response, 'cookies', None))

        def mark_no_store():
            # Never downgrade a stricter Cache-Control a view already set
            # (e.g. ``private, no-store, max-age=0`` on token pages); only
            # fill it in when the view left it cacheable or unset.
            if 'no-store' not in response.get('Cache-Control', '').lower():
                response['Cache-Control'] = 'private, no-store'
            patch_vary_headers(response, ('Cookie',))

        if request.method in ('GET', 'HEAD') and path.startswith(UNCACHEABLE_PREFIXES):
            mark_no_store()
            return response

        if request.method not in ('GET', 'HEAD'):
            return response

        if sets_cookies or request.COOKIES.get(session_cookie):
            # Could be (or become) user-specific: never cache anywhere.
            mark_no_store()
            return response

        is_public_html = (
            response.status_code == 200
            and response.get('Content-Type', '').lower().startswith('text/html')
        )
        if is_public_html:
            smaxage = getattr(settings, 'PUBLIC_HTML_SMAXAGE', PUBLIC_HTML_SMAXAGE)
            response['Cache-Control'] = f'public, max-age=0, s-maxage={smaxage}'
            patch_vary_headers(response, ('Cookie',))

        return response


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


_CRAWLER_USER_AGENT_RE = re.compile(
    r'(?:^|[^a-z0-9])(?:[a-z0-9._-]*(?:bot|crawler|spider))'
    r'(?:$|[/\s;,+()])',
    re.IGNORECASE,
)
_SOCIAL_LOGIN_START_RE = re.compile(
    r'^/accounts/(?:google|github|slack)/login/$',
)


def is_web_crawler_user_agent(user_agent):
    """Recognize crawler tokens without classifying browser automation."""
    return bool(_CRAWLER_USER_AGENT_RE.search(str(user_agent or '')))


class CrawlerOAuthGuardMiddleware:
    """Stop crawler reads before social login creates session/OAuth state."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.method in {'GET', 'HEAD'}
            and _SOCIAL_LOGIN_START_RE.fullmatch(request.path_info)
            and is_web_crawler_user_agent(
                request.META.get('HTTP_USER_AGENT', ''),
            )
        ):
            return HttpResponse(
                'Crawler access denied.\n',
                status=403,
                content_type='text/plain; charset=utf-8',
            )
        return self.get_response(request)


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
