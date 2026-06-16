"""Auto-register event hosts as attendees for reminder delivery."""

import logging

from accounts.services.email_resolution import resolve_user_by_email
from events.services.host_invite import resolve_host_email

logger = logging.getLogger(__name__)


def maybe_register_host_as_attendee(event):
    """Create an ``EventRegistration`` for the resolved host user.

    Best-effort and idempotent: failures are logged and swallowed so Studio
    and API event saves keep the same non-blocking contract as host invites.
    """
    try:
        if event.status == 'draft':
            return None
        if not event.is_upcoming:
            return None

        host_email = resolve_host_email(event)
        if not host_email:
            return None

        host_user = resolve_user_by_email(host_email)
        if host_user is None:
            logger.warning(
                'Skipping host auto-registration for event "%s": host email '
                '%s did not resolve to a platform user.',
                event.slug,
                host_email,
            )
            return None

        from events.models import EventRegistration

        registration, _created = EventRegistration.objects.get_or_create(
            event=event,
            user=host_user,
        )
        return registration
    except Exception:
        logger.exception(
            'Failed to auto-register host for event "%s"',
            getattr(event, 'slug', getattr(event, 'pk', 'unknown')),
        )
        return None
