"""Auto-register event hosts as normal event attendees."""

import logging

from accounts.services.email_resolution import resolve_user_by_email
from integrations.config import site_base_url

logger = logging.getLogger(__name__)


def resolve_host_user(event):
    """Resolve ``event.host_email`` to a platform user, or ``None``."""
    host_email = (getattr(event, 'host_email', '') or '').strip()
    if not host_email:
        return None
    return resolve_user_by_email(host_email)


def is_host_registration(registration):
    """Return whether ``registration`` belongs to the event's resolved host."""
    host_user = resolve_host_user(registration.event)
    return host_user is not None and host_user.pk == registration.user_id


def build_host_management_links(event):
    """Build absolute host-only management links for attendee emails."""
    site_url = site_base_url()
    studio_edit = f'{site_url}/studio/events/{event.pk}/edit'
    return {
        'edit_url': studio_edit,
        'manage_url': studio_edit,
        'create_zoom_url': f'{site_url}/studio/events/{event.pk}/create-zoom',
        'studio_url': studio_edit,
        'zoom_join_url': getattr(event, 'zoom_join_url', '') or '',
    }


def maybe_register_host_as_attendee(event):
    """Create an ``EventRegistration`` for the resolved host user.

    Best-effort and idempotent: failures are logged and swallowed so Studio
    and API event saves keep the same non-blocking contract as event email.
    """
    try:
        if event.status == 'draft':
            return None
        if not event.published:
            return None
        if not event.is_upcoming:
            return None

        host_email = (getattr(event, 'host_email', '') or '').strip()
        if not host_email:
            logger.warning(
                'Skipping host auto-registration for event "%s": host_email '
                'is blank.',
                event.slug,
            )
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

        registration, created = EventRegistration.objects.get_or_create(
            event=event,
            user=host_user,
        )
        if created:
            from events.services.registration_email import (
                send_registration_confirmation,
            )
            send_registration_confirmation(registration)
        return registration
    except Exception:
        logger.exception(
            'Failed to auto-register host for event "%s"',
            getattr(event, 'slug', getattr(event, 'pk', 'unknown')),
        )
        return None
