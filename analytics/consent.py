"""Consent helpers for optional first-party and Google analytics."""

from django.conf import settings

ANALYTICS_CONSENT_COOKIE = 'aslab_analytics_consent'
ANALYTICS_CONSENT_GRANTED = 'granted'
ANALYTICS_CONSENT_DENIED = 'denied'
ANALYTICS_CONSENT_CHOICES = {
    ANALYTICS_CONSENT_GRANTED,
    ANALYTICS_CONSENT_DENIED,
}
ANALYTICS_CONSENT_MAX_AGE = 60 * 60 * 24 * 365


def analytics_consent_state(request):
    """Return the saved consent choice, or an empty string when undecided."""
    value = request.COOKIES.get(ANALYTICS_CONSENT_COOKIE, '')
    return value if value in ANALYTICS_CONSENT_CHOICES else ''


def analytics_consent_granted(request):
    return analytics_consent_state(request) == ANALYTICS_CONSENT_GRANTED


def consent_cookie_kwargs():
    return {
        'max_age': ANALYTICS_CONSENT_MAX_AGE,
        'samesite': 'Lax',
        'httponly': True,
        'secure': not settings.DEBUG,
        'domain': (
            getattr(settings, 'ANALYTICS_COOKIE_DOMAIN', None)
            or getattr(settings, 'SESSION_COOKIE_DOMAIN', None)
            or None
        ),
    }
