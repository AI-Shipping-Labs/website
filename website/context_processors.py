from django.conf import settings


def site_context(request):
    """Add site-wide context variables to all templates."""
    return {
        'VERSION': settings.VERSION,
        'site_name': settings.SITE_NAME,
        'site_url': settings.SITE_URL,
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

    from integrations.middleware import get_announcement_banner

    banner = get_announcement_banner()
    if banner is None or not banner.is_enabled:
        return {'announcement_banner': None}
    return {'announcement_banner': banner}
