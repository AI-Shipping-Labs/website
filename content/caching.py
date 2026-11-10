"""Per-view anonymous page cache with host-specific canonical HTML."""

import hashlib
from functools import wraps
from threading import Event, Lock
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, QueryDict

from analytics.consent import analytics_consent_state
from content.cache_facets import known_facet_values
from content.cache_facets import known_public_tags as _cached_public_tags
from content.utils.tags import parse_bounded_selected_tags
from integrations.shared_cache import get_shared_cache, set_shared_cache

ANON_PAGE_CACHE_TIMEOUT = 300
_ANON_PAGE_KEY_PREFIX = "anon-page:v1"
MAX_LISTING_PAGE = 1000
_page_render_guard = Lock()
_page_renders_in_flight = {}

# Query parameters affect cached HTML only on these listing routes. Keeping
# the allowlist here prevents tracking and arbitrary parameters from making
# unbounded cache entries.
_LISTING_QUERY_ALLOWLISTS = {
    "/blog": frozenset({"tag", "topic"}),
    "/courses": frozenset({"tag"}),
    "/downloads": frozenset({"tag"}),
    "/events": frozenset({"filter", "page", "tag"}),
    "/projects": frozenset({"difficulty", "tag"}),
    "/resources": frozenset({"tag"}),
    "/sitemap.xml": frozenset({"p"}),
    "/workshops/catalog": frozenset({"tag", "topic"}),
}


def _query_allowlist(path):
    """Return query inputs that can change this cached public response."""
    if path.startswith("/courses/"):
        return frozenset({"cohort", "view"})
    return _LISTING_QUERY_ALLOWLISTS.get(path, frozenset())


def _safe_cache_key_segment(value, max_length=100):
    """Return a memcache-safe key segment bounded to ``max_length`` chars."""
    if len(value) > max_length:
        digest = hashlib.sha256(
            value.encode("utf-8", errors="replace"),
        ).hexdigest()[:32]
        return f"{value[:40]}:{digest}"
    for ch in value:
        code = ord(ch)
        if code < 33 or code >= 127:
            digest = hashlib.sha256(
                value.encode("utf-8", errors="replace"),
            ).hexdigest()[:32]
            return f"sha256:{digest}"
    return value


def _canonical_path(request):
    """Return a route path without an optional trailing slash."""
    return request.path_info.rstrip("/") or "/"


def known_public_tags(path=None):
    """Expose the shared published-tag cache for sibling tag views."""
    return _cached_public_tags(path)


def _unknown_facet_response(request):
    """Return a no-store 404 for a facet unavailable to this public listing."""
    path = _canonical_path(request)
    for facet in ("tag", "topic", "difficulty"):
        if facet not in _query_allowlist(path):
            continue
        if facet not in request.GET:
            continue
        if facet == "tag":
            values, is_valid = parse_bounded_selected_tags(
                request.GET.getlist(facet),
            )
            if not is_valid:
                continue
        else:
            values = [request.GET.get(facet, "").strip().lower()]
        if not any(values):
            continue
        known = known_facet_values(path, facet)
        if any(value and value not in known for value in values):
            return HttpResponse(
                "Unknown listing filter.\n",
                status=404,
                content_type="text/plain; charset=utf-8",
                headers={
                    "Cache-Control": "no-store",
                    "X-Robots-Tag": "noindex, nofollow",
                },
            )
    return None


def _bounded_page(value):
    """Normalize a page query value into the supported 1..1000 range."""
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 1
    return min(max(page, 1), MAX_LISTING_PAGE)


def _canonical_query_items(request):
    """Return sorted, normalized query pairs supported by this listing."""
    path = _canonical_path(request)
    allowlist = _query_allowlist(path)
    items = []
    if "tag" in allowlist:
        tags, valid = parse_bounded_selected_tags(request.GET.getlist("tag"))
        if valid:
            items.extend(("tag", tag) for tag in sorted(tags))
    for name in allowlist - {"tag"}:
        values = request.GET.getlist(name)
        if not values:
            continue
        value = values[-1].strip().lower()
        if name == "page":
            value = str(_bounded_page(value))
        elif name == "filter" and value != "past":
            continue
        if value:
            items.append((name, value))
    return sorted(items)


def _normalize_request_query(request):
    """Drop ignored query parameters and install canonical listing values."""
    request.GET = QueryDict(urlencode(_canonical_query_items(request)), mutable=True)


def _anonymous_cache_key(request, cache_group=None):
    """Return the cache key for an anonymous public request."""
    query = urlencode(_canonical_query_items(request))
    consent = analytics_consent_state(request) or "undecided"
    group_version = ""
    if cache_group:
        version_key = _cache_group_version_key(cache_group)
        group_version = get_shared_cache(version_key, "initial")
    path_token = _safe_cache_key_segment(request.path, max_length=100)
    host = urlsplit(f"//{request.get_host()}").hostname or ""
    host_token = _safe_cache_key_segment(host.lower().rstrip("."))
    if not query:
        return (
            f"{_ANON_PAGE_KEY_PREFIX}:{consent}:{cache_group}:"
            f"{group_version}:{host_token}:{path_token}"
        )
    query_digest = hashlib.sha256(
        query.encode("utf-8", errors="replace"),
    ).hexdigest()[:32]
    return (
        f"{_ANON_PAGE_KEY_PREFIX}:{consent}:{cache_group}:"
        f"{group_version}:{host_token}:{path_token}?{query_digest}"
    )


def _cache_group_version_key(cache_group):
    """Return the shared cache key that versions a group of public pages."""
    return f"{_ANON_PAGE_KEY_PREFIX}:group-version:{cache_group}"


def invalidate_anonymous_page_cache(cache_group):
    """Invalidate cached anonymous pages in ``cache_group``."""
    set_shared_cache(
        _cache_group_version_key(cache_group), uuid4().hex, timeout=None,
    )


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


def _anonymous_session_is_cacheable(request):
    """Allow absent, invalid, unknown, and empty sessions for anonymous pages."""
    cookie_name = settings.SESSION_COOKIE_NAME
    if cookie_name not in request.COOKIES:
        return True
    session = getattr(request, "session", None)
    if session is None:
        return False
    cookie_value = request.COOKIES.get(cookie_name)
    if not cookie_value or session.session_key is None:
        _discard_stale_session_cookie(request, cookie_name)
        return True
    session_data = dict(session)
    return not session_data


def _discard_stale_session_cookie(request, cookie_name):
    """Stop Django from emitting a deletion cookie for invalid session keys."""
    # SessionMiddleware checks request.COOKIES when preparing its response.
    request.COOKIES.pop(cookie_name, None)


def _is_cacheable_request(request):
    """Return True when the request is safe to serve from the anon cache."""
    if not _cache_enabled():
        return False
    if request.method not in ("GET", "HEAD"):
        return False
    user = getattr(request, "user", None)
    if getattr(user, "is_authenticated", False):
        return False
    if not _anonymous_session_is_cacheable(request):
        return False
    _, is_valid_tags = parse_bounded_selected_tags(request.GET.getlist("tag"))
    if not is_valid_tags:
        return False
    return True


def _claim_page_render(key):
    with _page_render_guard:
        event = _page_renders_in_flight.get(key)
        if event is not None:
            return event, False
        event = Event()
        _page_renders_in_flight[key] = event
        return event, True


def _release_page_render(key, event):
    with _page_render_guard:
        if _page_renders_in_flight.get(key) is event:
            del _page_renders_in_flight[key]
    event.set()


def _render_and_cache(view, request, key, timeout, args, kwargs):
    response = view(request, *args, **kwargs)
    if hasattr(response, "render") and not response.is_rendered:
        response.render()
    if getattr(response, "cookies", None) and response.cookies:
        return response
    if getattr(response, "status_code", 200) == 200:
        cache.set(key, response, timeout)
    return response


def _singleflight_page_response(view, request, key, timeout, args, kwargs):
    while True:
        event, owns_render = _claim_page_render(key)
        if not owns_render:
            event.wait()
            cached = cache.get(key)
            if cached is not None:
                return cached
            continue
        try:
            cached = cache.get(key)
            if cached is not None:
                return cached
            return _render_and_cache(view, request, key, timeout, args, kwargs)
        finally:
            _release_page_render(key, event)


def cache_anonymous_page(timeout=ANON_PAGE_CACHE_TIMEOUT, cache_group=None):
    """Cache anonymous GETs, except authenticated, private, or cookie responses."""

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not _is_cacheable_request(request):
                return view(request, *args, **kwargs)
            rejected = _unknown_facet_response(request)
            if rejected is not None:
                return rejected
            _normalize_request_query(request)
            key = _anonymous_cache_key(request, cache_group)
            cached = cache.get(key)
            if cached is not None:
                return cached
            return _singleflight_page_response(view, request, key, timeout, args, kwargs)

        wrapper._uses_anonymous_page_cache = True
        return wrapper

    return decorator
