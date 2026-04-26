from urllib.parse import urlparse

from django.conf import settings

from integrations.middleware import get_announcement_banner

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

    Returns ``None`` if the request matches ``SITE_BASE_URL`` after
    normalization. Returns a dict with ``configured_base_url``,
    ``request_url``, and ``configured_host`` otherwise.

    The dict shape is what the partial template consumes; if the
    detector can't make sense of the configured URL (empty / unparseable
    netloc) it returns ``None`` rather than raising — the banner is a
    best-effort warning, not a hard failure.
    """
    configured = (settings.SITE_BASE_URL or '').rstrip('/')
    if not configured:
        return None

    parsed = urlparse(configured)
    if not parsed.netloc:
        return None

    configured_triple = _normalize_host_triple(parsed.scheme, parsed.netloc)
    request_triple = _normalize_host_triple(request.scheme, request.get_host())

    if configured_triple == request_triple:
        return None

    return {
        'configured_base_url': configured,
        'request_url': f'{request.scheme}://{request.get_host()}',
        'configured_host': parsed.netloc,
    }


def site_context(request):
    """Add site-wide context variables to all templates."""
    return {
        'VERSION': settings.VERSION,
        'site_name': settings.SITE_NAME,
        'site_url': settings.SITE_BASE_URL,
        'site_description': settings.SITE_DESCRIPTION,
        'stripe_customer_portal_url': settings.STRIPE_CUSTOMER_PORTAL_URL,
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
    derived from ``settings.SITE_BASE_URL``. Returns ``{'env_mismatch':
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
