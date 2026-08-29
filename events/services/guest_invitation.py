"""Idempotent, event-only staff guest invitations."""

import logging
from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import EmailAlias
from accounts.services.email_resolution import normalize_email
from accounts.services.verification import resolve_unverified_ttl_days
from events.models import EventRegistration, GuestInviteDelivery
from events.services.host_registration import resolve_host_user

User = get_user_model()
logger = logging.getLogger(__name__)


class GuestInvitationError(Exception):
    """Safe operator-facing rejection raised before invitation side effects."""

    def __init__(self, message, code, *, status=409, details=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.details = details


@dataclass(frozen=True)
class GuestResolution:
    email: str
    user: object | None


def _classify_delivery_error(exc):
    if isinstance(exc, TimeoutError):
        return GuestInviteDelivery.ERROR_TIMEOUT
    if isinstance(exc, ConnectionError):
        return GuestInviteDelivery.ERROR_CONNECTION
    categories = {
        'ConnectTimeoutError': GuestInviteDelivery.ERROR_TIMEOUT,
        'ReadTimeoutError': GuestInviteDelivery.ERROR_TIMEOUT,
        'EndpointConnectionError': GuestInviteDelivery.ERROR_CONNECTION,
        'ConnectionClosedError': GuestInviteDelivery.ERROR_CONNECTION,
        'NoCredentialsError': GuestInviteDelivery.ERROR_CONFIGURATION,
        'PartialCredentialsError': GuestInviteDelivery.ERROR_CONFIGURATION,
        'ServiceUnavailable': GuestInviteDelivery.ERROR_UNAVAILABLE,
        'ServiceUnavailableError': GuestInviteDelivery.ERROR_UNAVAILABLE,
        'ClientError': GuestInviteDelivery.ERROR_REJECTED,
    }
    return categories.get(
        type(exc).__name__, GuestInviteDelivery.ERROR_PROVIDER,
    )


def _validate_event(event):
    if not event.published:
        raise GuestInvitationError(
            'Guest invitations require a published event.',
            'event_not_published',
        )
    if not event.is_upcoming:
        raise GuestInvitationError(
            'Guest invitations require an upcoming event.',
            'event_not_upcoming',
        )
    if event.is_external:
        raise GuestInvitationError(
            'Guest invitations are only available for in-app events.',
            'external_event',
        )


def resolve_guest(event, raw_email):
    """Validate the exact recipient and resolve only an active primary user."""
    _validate_event(event)
    email = normalize_email(raw_email)
    try:
        validate_email(email)
    except ValidationError as exc:
        raise GuestInvitationError(
            'A valid guest email is required.',
            'invalid_guest_email',
            status=422,
            details={'field': 'email'},
        ) from exc

    primary = User.objects.filter(
        email__iexact=email, is_active=True,
    ).first()
    host_user = resolve_host_user(event)
    if primary is not None:
        if host_user is not None and primary.pk == host_user.pk:
            raise GuestInvitationError(
                'The operational host cannot be invited as an ordinary guest.',
                'guest_is_operational_host',
            )
        return GuestResolution(email=email, user=primary)

    alias = EmailAlias.objects.select_related('user').filter(email=email).first()
    if alias is not None:
        if host_user is not None and alias.user_id == host_user.pk:
            raise GuestInvitationError(
                'The operational host cannot be invited as an ordinary guest.',
                'guest_is_operational_host',
            )
        raise GuestInvitationError(
            'This address is an alias of another account; invite its primary email.',
            'guest_email_is_alias',
        )

    if User.objects.filter(email__iexact=email).exists():
        raise GuestInvitationError(
            'This email belongs to an inactive account.',
            'guest_account_inactive',
        )
    return GuestResolution(email=email, user=None)


def _serialize(event, email, registration, delivery, *, registration_status,
               email_status, dry_run=False):
    return {
        'event_id': event.pk,
        'event_slug': event.slug,
        'event_title': event.title,
        'guest_email': email,
        'registration_id': registration.pk if registration else None,
        'registration_status': registration_status,
        'email_status': email_status,
        'dry_run': dry_run,
        'attempt_count': delivery.attempt_count if delivery else 0,
    }


def preview_guest_invitation(event, raw_email):
    """Validate and report a guest invitation without creating any rows."""
    resolution = resolve_guest(event, raw_email)
    registration = None
    delivery = None
    if resolution.user is not None:
        registration = EventRegistration.objects.filter(
            event=event, user=resolution.user,
        ).first()
        if registration is not None:
            delivery = GuestInviteDelivery.objects.filter(
                registration=registration,
            ).first()
    registration_status = (
        'already_registered' if registration else 'would_register'
    )
    email_status = (
        'already_sent'
        if delivery and delivery.status == GuestInviteDelivery.STATUS_SENT
        else 'would_send'
    )
    return _serialize(
        event, resolution.email, registration, delivery,
        registration_status=registration_status,
        email_status=email_status,
        dry_run=True,
    )


def _create_guest_user(email):
    import datetime

    expires_at = timezone.now() + datetime.timedelta(
        days=resolve_unverified_ttl_days(),
    )
    return User.objects.create_user(
        email=email,
        verification_expires_at=expires_at,
        signup_source='signup',
    )


def _send_verification_once(user):
    from accounts.views.auth import _send_verification_email

    return _send_verification_email(user)


def invite_guest(event, raw_email):
    """Create/reuse one event registration and attempt its attendee invite."""
    resolution = resolve_guest(event, raw_email)
    user_created = False

    if resolution.user is None:
        try:
            with transaction.atomic():
                user = _create_guest_user(resolution.email)
                user_created = True
        except IntegrityError:
            user = User.objects.filter(
                email__iexact=resolution.email, is_active=True,
            ).first()
            if user is None:
                raise
    else:
        user = resolution.user

    with transaction.atomic():
        registration, registration_created = EventRegistration.objects.get_or_create(
            event=event, user=user,
        )
        delivery, _ = GuestInviteDelivery.objects.get_or_create(
            registration=registration,
            defaults={'guest_email': resolution.email},
        )

    if user_created:
        try:
            _send_verification_once(user)
        except Exception:
            logger.exception(
                'Failed to send verification email for guest user %s', user.pk,
            )

    with transaction.atomic():
        delivery = GuestInviteDelivery.objects.select_for_update().get(
            pk=delivery.pk,
        )
        if delivery.status == GuestInviteDelivery.STATUS_SENT:
            return _serialize(
                event, resolution.email, registration, delivery,
                registration_status=(
                    'registered' if registration_created
                    else 'already_registered'
                ),
                email_status='already_sent',
            )

        delivery.attempt_count += 1
        delivery.status = GuestInviteDelivery.STATUS_SENDING
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
            delivery.status = GuestInviteDelivery.STATUS_FAILED
            delivery.last_error = _classify_delivery_error(exc)
            delivery.save(update_fields=['status', 'last_error'])
            return _serialize(
                event, resolution.email, registration, delivery,
                registration_status=(
                    'registered' if registration_created
                    else 'already_registered'
                ),
                email_status='failed_retryable',
            )

        delivery.status = GuestInviteDelivery.STATUS_SENT
        delivery.sent_at = timezone.now()
        delivery.sent_ics_sequence = event.ics_sequence
        from email_app.models import EmailLog
        if isinstance(email_log, EmailLog):
            delivery.email_log = email_log
        delivery.save(update_fields=[
            'status', 'sent_at', 'sent_ics_sequence', 'email_log',
        ])

    return _serialize(
        event, resolution.email, registration, delivery,
        registration_status=(
            'registered' if registration_created else 'already_registered'
        ),
        email_status='sent',
    )


def get_guest_invitation(event, registration_id):
    """Return authoritative current state for one guest invitation."""
    registration = (
        EventRegistration.objects.select_related('user')
        .filter(pk=registration_id, event=event)
        .first()
    )
    if registration is None:
        raise GuestInvitationError(
            'Guest invitation not found.', 'unknown_guest_invitation', status=404,
        )
    delivery = GuestInviteDelivery.objects.filter(
        registration=registration,
    ).first()
    if delivery is None:
        raise GuestInvitationError(
            'Guest invitation not found.', 'unknown_guest_invitation', status=404,
        )
    email_status = {
        GuestInviteDelivery.STATUS_SENT: 'sent',
        GuestInviteDelivery.STATUS_FAILED: 'failed_retryable',
        GuestInviteDelivery.STATUS_SENDING: 'sending',
        GuestInviteDelivery.STATUS_PENDING: 'pending',
    }[delivery.status]
    return _serialize(
        event, delivery.guest_email, registration, delivery,
        registration_status='registered',
        email_status=email_status,
    )
