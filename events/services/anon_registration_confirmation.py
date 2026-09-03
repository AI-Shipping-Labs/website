"""Anonymous event-registration confirmation flash (issue #1508).

After a successful anonymous ``POST /api/events/<slug>/register``, the
API stores a signed Django session value bound to ``event_id`` and
``user_id``. The event detail page reads that flash and looks the
display email up from ``EventRegistration`` / ``User``. Query params
``registered`` and ``account_created`` are never treated as identity.
"""

from content.access import LEVEL_OPEN
from events.models import EventRegistration

SESSION_KEY = 'anon_event_registration_confirmation'
CONFIRMATION_QUERY_KEYS = ('registered', 'account_created')


def store_anon_registration_confirmation(
    request, event, user, *, account_created,
):
    """Persist confirmation flash for this anonymous register response."""
    request.session[SESSION_KEY] = {
        'event_id': event.pk,
        'user_id': user.pk,
        'account_created': bool(account_created),
    }


def confirmation_query_params_present(request):
    """Return True when a legacy/spoofed confirmation query key is present."""
    return any(key in request.GET for key in CONFIRMATION_QUERY_KEYS)


def canonical_event_url_without_confirmation_params(event, querydict):
    """Return the canonical event URL with confirmation query keys removed."""
    remaining = querydict.copy()
    for key in CONFIRMATION_QUERY_KEYS:
        remaining.pop(key, None)
    url = event.get_absolute_url()
    encoded = remaining.urlencode()
    if encoded:
        return f'{url}?{encoded}'
    return url


def resolve_anon_registration_confirmation(request, event):
    """Return ``(email, account_created)`` for this anonymous GET, or empty.

    The flash is ignored unless the GET is for the same event, the visitor
    is still anonymous, the event is an upcoming in-app free event, and an
    ``EventRegistration`` row still exists for the flashed user+event.
    """
    if request.user.is_authenticated:
        return '', False
    if not event.is_upcoming or event.is_external:
        return '', False
    if event.required_level > LEVEL_OPEN:
        return '', False

    payload = request.session.get(SESSION_KEY)
    if not isinstance(payload, dict):
        return '', False

    try:
        flash_event_id = int(payload.get('event_id'))
        flash_user_id = int(payload.get('user_id'))
    except (TypeError, ValueError):
        return '', False

    if flash_event_id != event.pk:
        return '', False

    registration = (
        EventRegistration.objects.select_related('user')
        .filter(event=event, user_id=flash_user_id)
        .first()
    )
    if registration is None:
        return '', False

    email = (registration.user.email or '').strip()
    if not email:
        return '', False

    return email, payload.get('account_created') is True
