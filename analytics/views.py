"""Public endpoint for saving the optional analytics preference."""

import json

from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from analytics.consent import (
    ANALYTICS_CONSENT_CHOICES,
    ANALYTICS_CONSENT_COOKIE,
    ANALYTICS_CONSENT_DENIED,
    consent_cookie_kwargs,
)
from analytics.middleware import (
    SESSION_LAST_TOUCH,
    SESSION_LAST_TOUCH_REFERRER,
    delete_analytics_cookies,
)


@require_GET
@never_cache
def analytics_consent_csrf(request):
    """Issue a token on demand when a cached page reopens preferences."""
    return JsonResponse({'csrf_token': get_token(request)})


@require_POST
def set_analytics_consent(request):
    try:
        payload = json.loads(request.body or b'{}')
    except (TypeError, ValueError):
        return JsonResponse({'status': 'error', 'error': 'Invalid request.'}, status=400)

    choice = payload.get('consent') if isinstance(payload, dict) else None
    if choice not in ANALYTICS_CONSENT_CHOICES:
        return JsonResponse(
            {'status': 'error', 'error': 'Choose granted or denied.'},
            status=400,
        )

    if choice == ANALYTICS_CONSENT_DENIED:
        request.session.pop(SESSION_LAST_TOUCH, None)
        request.session.pop(SESSION_LAST_TOUCH_REFERRER, None)

    response = JsonResponse({'status': 'ok', 'consent': choice})
    response.set_cookie(
        ANALYTICS_CONSENT_COOKIE,
        choice,
        **consent_cookie_kwargs(),
    )
    if choice == ANALYTICS_CONSENT_DENIED:
        delete_analytics_cookies(response)
    return response
