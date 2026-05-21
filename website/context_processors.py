import json
import re
import uuid
from urllib.parse import urlparse

from django.conf import settings

from integrations.config import get_config, site_base_url
from integrations.middleware import get_announcement_banner

# Cookie name for the analytics anonymous-visitor UUID4 set by
# ``analytics.middleware.CampaignTrackingMiddleware``. Duplicated here
# (rather than imported from ``analytics.middleware``) to avoid an
# import cycle: ``website`` is loaded extremely early and importing
# ``analytics.middleware`` would drag in the analytics models and
# their app config before Django is ready in some test paths.
_ASLAB_AID_COOKIE = 'aslab_aid'


def _validated_aslab_anon_id(request):
    """Return the ``aslab_aid`` cookie value if it parses as a UUID, else ''.

    The cookie is set ``httponly=True`` (see
    ``analytics/middleware.py:cookie_kwargs``) so JavaScript cannot
    read it; we render it server-side into GA's ``user_property`` /
    ``user_id`` calls. Validating as a UUID is defensive — a forged
    or empty cookie should not be allowed to inject arbitrary content
    into the template's inline script block.
    """
    raw = request.COOKIES.get(_ASLAB_AID_COOKIE, '') or ''
    if not raw:
        return ''
    try:
        uuid.UUID(str(raw))
        return raw
    except (ValueError, TypeError):
        return ''

# Default ports per scheme. Used by the host-mismatch detector so an
# explicit ":80" / ":443" compares equal to an omitted port — operators
# rarely set the default port explicitly, and warning on it would fire
# on every prod hit.
_DEFAULT_PORTS = {
    'http': 80,
    'https': 443,
}


def _normalize_host_triple(scheme, host_with_port):
    """Normalize a ``(scheme, host:port)`` pair into a comparable triple.

    Returns ``(scheme_lower, host_lower, port_int)``.

    Rules (locked in spec):

    - Scheme is lowercased. ``http`` and ``https`` are NOT considered
      equal — a scheme mismatch is itself a meaningful warning.
    - Host is lowercased. No ``www``-stripping; the deploy lists the
      apex and ``www`` variants separately in ``ALLOWED_HOSTS`` and we
      treat them as different hosts here.
    - Port: if absent, defaults to 80 for ``http`` and 443 for
      ``https``. So ``https://aishippinglabs.com`` (no port) compares
      equal to ``https://aishippinglabs.com:443`` but ``localhost:8000``
      vs ``localhost:8001`` is a mismatch.
    - Anything other than scheme / host / port (path, query, fragment)
      is the caller's responsibility to strip.
    """
    scheme = (scheme or '').lower()

    host = (host_with_port or '').lower()
    port = None
    # IPv6 literals come wrapped in ``[...]``; we don't normalise those
    # specifically because Django's ``request.get_host()`` returns the
    # same shape for both sides of the comparison and the bracket form
    # is preserved end-to-end.
    if host.startswith('[') and ']' in host:
        bracket_end = host.index(']')
        host_part = host[: bracket_end + 1]
        rest = host[bracket_end + 1:]
        if rest.startswith(':'):
            try:
                port = int(rest[1:])
            except ValueError:
                port = None
        host = host_part
    elif ':' in host:
        host_part, _, port_str = host.rpartition(':')
        try:
            port = int(port_str)
            host = host_part
        except ValueError:
            port = None

    if port is None:
        port = _DEFAULT_PORTS.get(scheme)

    return (scheme, host, port)


def _build_env_mismatch_payload(request):
    """Return banner data for the Studio host-mismatch warning.

    Returns ``None`` if the request matches ``SITE_BASE_URL`` (or any
    alias from ``SITE_BASE_URL_ALIASES``) after normalization. Returns a
    dict with ``configured_base_url``, ``request_url``, and
    ``configured_host`` otherwise.

    Aliases are read from the ``SITE_BASE_URL_ALIASES`` integration
    setting, parsed with ``re.split(r'[\\s,]+', value)`` so operators
    can separate entries with commas, whitespace, or newlines. Each
    alias is run through the same ``_normalize_host_triple`` as the
    canonical URL; malformed aliases (empty netloc) are skipped. Empty
    / unset aliases preserve today's banner behavior bit-for-bit.

    The dict shape is what the partial template consumes; if the
    detector can't make sense of the configured URL (empty / unparseable
    netloc) it returns ``None`` rather than raising — the banner is a
    best-effort warning, not a hard failure.
    """
    configured = (site_base_url() or '').rstrip('/')
    if not configured:
        return None

    parsed = urlparse(configured)
    if not parsed.netloc:
        return None

    configured_triple = _normalize_host_triple(parsed.scheme, parsed.netloc)
    request_triple = _normalize_host_triple(request.scheme, request.get_host())

    if configured_triple == request_triple:
        return None

    aliases_raw = get_config('SITE_BASE_URL_ALIASES', '')
    if aliases_raw:
        for alias in re.split(r'[\s,]+', aliases_raw):
            if not alias:
                continue
            alias_parsed = urlparse(alias)
            if not alias_parsed.netloc:
                continue
            alias_triple = _normalize_host_triple(
                alias_parsed.scheme, alias_parsed.netloc,
            )
            if alias_triple == request_triple:
                return None

    return {
        'configured_base_url': configured,
        'request_url': f'{request.scheme}://{request.get_host()}',
        'configured_host': parsed.netloc,
    }


def site_context(request):
    """Add site-wide context variables to all templates.

    ``aslab_anon_id`` carries the validated ``aslab_aid`` cookie so
    ``templates/base.html`` can emit ``gtag('set', 'user_properties',
    ...)`` + ``gtag('config', GA_ID, { user_id: ... })`` with the
    same UUID we use server-side on ``UserAttribution.anonymous_id``.
    The middleware skips bot / admin / static paths so the cookie may
    be absent — the template guards on the value being truthy.

    ``gtag_pending_event`` is a one-shot conversion-event payload set
    by server-side flows that complete on a redirect (course enroll,
    OAuth signup). The session key is *popped* here (not merely read)
    so the next page render fires the event exactly once, regardless
    of whether the template branch that emits the ``<script>`` runs
    multiple times.
    """
    pending_event = None
    if hasattr(request, 'session'):
        try:
            raw_pending = request.session.pop('gtag_event_pending', None)
        except Exception:  # pragma: no cover — defensive
            # Session backends can raise on edge cases (decoder errors,
            # missing session row). A broken pop must never break the
            # page render.
            raw_pending = None
        # Shape: {'event': 'sign_up', 'params': {'method': 'oauth', ...}}.
        # Only accept a dict with a non-empty 'event' name and serialise
        # the params dict to JSON server-side so the template can splice
        # it into the gtag() call without quoting headaches.
        if isinstance(raw_pending, dict):
            event_name = raw_pending.get('event') or ''
            params = raw_pending.get('params') or {}
            # GA event names are ``[A-Za-z][A-Za-z0-9_]{0,39}``. Guard
            # against arbitrary strings sneaking into the inline script
            # via a malicious or buggy server flow — only allow names
            # that match the safe pattern.
            if (
                isinstance(event_name, str)
                and re.match(r'^[A-Za-z][A-Za-z0-9_]{0,39}$', event_name)
                and isinstance(params, dict)
            ):
                try:
                    params_json = json.dumps(params)
                except (TypeError, ValueError):
                    params_json = '{}'
                pending_event = {
                    'event': event_name,
                    'params_json': params_json,
                }

    return {
        'VERSION': settings.VERSION,
        'site_name': settings.SITE_NAME,
        'site_url': site_base_url(),
        'site_description': settings.SITE_DESCRIPTION,
        'stripe_customer_portal_url': get_config('STRIPE_CUSTOMER_PORTAL_URL', ''),
        'google_analytics_id': get_config('GOOGLE_ANALYTICS_ID', ''),
        'aslab_anon_id': _validated_aslab_anon_id(request),
        'gtag_pending_event': pending_event,
        'current_year': __import__('datetime').datetime.now().year,
    }


def impersonation_context(request):
    """Add impersonation state to all templates."""
    return {
        'is_impersonating': bool(request.session.get('_impersonator_id')),
    }


def announcement_banner_context(request):
    """Expose the active announcement banner singleton to public templates.

    Returns ``{'announcement_banner': None}`` when:
      - no row exists,
      - the banner is disabled, or
      - the request is for /studio/... or /admin/... (banner is public-only).
    """
    path = request.path or ''
    if path.startswith('/studio/') or path.startswith('/admin/'):
        return {'announcement_banner': None}

    banner = get_announcement_banner()
    if banner is None or not banner.is_enabled:
        return {'announcement_banner': None}
    return {'announcement_banner': banner}


def studio_env_mismatch_context(request):
    """Expose host-mismatch banner data to Studio templates only.

    Mirrors :func:`announcement_banner_context`'s path-based scoping so
    the banner only renders inside ``/studio/``. The detector compares
    the request's ``(scheme, host, port)`` triple against the one
    derived from the resolved ``SITE_BASE_URL`` (DB override > env).
    Returns ``{'env_mismatch':
    None}`` for non-Studio paths or when the triples match (after
    normalization rules in ``_normalize_host_triple``).

    The dict has the keys ``configured_base_url`` (raw setting value),
    ``request_url`` (``scheme://host`` of the live request), and
    ``configured_host`` (the host portion of ``SITE_BASE_URL``) so the
    template can render the warning copy without re-parsing.
    """
    path = request.path or ''
    if not path.startswith('/studio/'):
        return {'env_mismatch': None}

    payload = _build_env_mismatch_payload(request)
    return {'env_mismatch': payload}
