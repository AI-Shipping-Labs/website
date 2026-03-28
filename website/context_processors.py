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
