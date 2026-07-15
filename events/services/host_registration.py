"""Auto-register event hosts as normal event attendees."""

import logging

from django.db import transaction
from django.utils import timezone

from accounts.services.email_resolution import resolve_user_by_email

logger = logging.getLogger(__name__)


def _safe_delivery_error(exc):
    """Classify a provider failure without retaining exception details."""
    from events.models import HostInviteDelivery

    if isinstance(exc, TimeoutError):
        return HostInviteDelivery.ERROR_TIMEOUT
    if isinstance(exc, ConnectionError):
        return HostInviteDelivery.ERROR_CONNECTION

    categories = {
        'ConnectTimeoutError': HostInviteDelivery.ERROR_TIMEOUT,
        'ReadTimeoutError': HostInviteDelivery.ERROR_TIMEOUT,
        'EndpointConnectionError': HostInviteDelivery.ERROR_CONNECTION,
        'ConnectionClosedError': HostInviteDelivery.ERROR_CONNECTION,
        'NoCredentialsError': HostInviteDelivery.ERROR_CONFIGURATION,
        'PartialCredentialsError': HostInviteDelivery.ERROR_CONFIGURATION,
        'ServiceUnavailable': HostInviteDelivery.ERROR_UNAVAILABLE,
        'ServiceUnavailableError': HostInviteDelivery.ERROR_UNAVAILABLE,
        'ClientError': HostInviteDelivery.ERROR_REJECTED,
    }
    return categories.get(type(exc).__name__, HostInviteDelivery.ERROR_PROVIDER)


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
    """Build signed links to the current host's safe management landing."""
    from events.services.host_access import build_host_access_url

    host_user = resolve_host_user(event)
    if host_user is None:
        return {}
    manage_url = build_host_access_url(event, host_user)
    return {
        'edit_url': build_host_access_url(event, host_user, anchor='edit'),
        'manage_url': build_host_access_url(
            event, host_user, anchor='registrations',
        ),
        'create_zoom_url': build_host_access_url(
            event, host_user, anchor='zoom',
        ),
        'studio_url': manage_url,
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

        from events.models import EventRegistration, HostInviteDelivery

        registration, _created = EventRegistration.objects.get_or_create(
            event=event,
            user=host_user,
        )

        # Serialize attempts on a durable row. Holding the row lock across
        # the provider call prevents concurrent Studio/API saves from sending
        # two initial invitations. A failed attempt remains observable and a
        # later save retries, up to the deliberately bounded maximum.
        with transaction.atomic():
            delivery, _ = HostInviteDelivery.objects.get_or_create(
                event=event,
                user=host_user,
                access_version=event.host_access_version,
            )
            delivery = HostInviteDelivery.objects.select_for_update().get(
                pk=delivery.pk,
            )
            if delivery.status == HostInviteDelivery.STATUS_SENT:
                return registration
            if delivery.attempt_count >= HostInviteDelivery.MAX_ATTEMPTS:
                logger.error(
                    'Host invite delivery exhausted for event "%s" user %s',
                    event.slug,
                    host_user.pk,
                )
                return registration

            delivery.attempt_count += 1
            delivery.status = HostInviteDelivery.STATUS_SENDING
            delivery.last_attempt_at = timezone.now()
            delivery.last_error = ''
            delivery.save(update_fields=[
                'attempt_count', 'status', 'last_attempt_at', 'last_error',
            ])

            try:
                from events.services.registration_email import (
                    send_registration_confirmation,
                )
                email_log = send_registration_confirmation(registration)
            except Exception as exc:
                delivery.status = HostInviteDelivery.STATUS_FAILED
                delivery.last_error = _safe_delivery_error(exc)
                delivery.save(update_fields=['status', 'last_error'])
                logger.error(
                    'Host invitation attempt %s/%s failed for event "%s" '
                    '(%s)',
                    delivery.attempt_count,
                    HostInviteDelivery.MAX_ATTEMPTS,
                    event.slug,
                    delivery.last_error,
                )
                return registration

            delivery.status = HostInviteDelivery.STATUS_SENT
            delivery.sent_at = timezone.now()
            delivery.sent_ics_sequence = event.ics_sequence
            from email_app.models import EmailLog
            if isinstance(email_log, EmailLog):
                delivery.email_log = email_log
            delivery.save(update_fields=[
                'status', 'sent_at', 'sent_ics_sequence', 'email_log',
            ])
        return registration
    except Exception:
        logger.exception(
            'Failed to auto-register host for event "%s"',
            getattr(event, 'slug', getattr(event, 'pk', 'unknown')),
        )
        return None
