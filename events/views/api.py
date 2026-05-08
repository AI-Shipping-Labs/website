import datetime
import json
import logging

from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from accounts.services.verification import resolve_unverified_ttl_days
from content.access import LEVEL_OPEN, can_access
from events.models import Event, EventRegistration

logger = logging.getLogger(__name__)

User = get_user_model()


def _is_valid_email(email):
    """Cheap structural email check — same rules as ``subscribe_api``."""
    if not email:
        return False
    if "@" not in email:
        return False
    domain = email.split("@", 1)[1]
    return "." in domain


def _create_unverified_subscriber(email):
    """Create a free, unverified ``User`` for an anonymous registrant.

    Mirrors ``subscribe_api`` so anonymous event registration produces the
    same shape of row: free tier, ``email_verified=False``, and a
    ``verification_expires_at`` window so the daily purge job (#452)
    cleans up abandoned accounts. ``import_source`` defaults to
    ``"manual"`` and ``imported_at`` stays ``None``.
    """
    ttl_days = resolve_unverified_ttl_days()
    verification_expires_at = (
        timezone.now() + datetime.timedelta(days=ttl_days)
    )
    return User.objects.create_user(
        email=email,
        verification_expires_at=verification_expires_at,
    )


def _send_event_verification_email(user):
    """Send the standard ``email_verification`` email after anonymous register.

    Distinct from the registration confirmation: this lets the new
    user claim the account that was created on their behalf. Failure
    is logged and swallowed so the registration response still goes
    through — the registration itself does not depend on this send.
    """
    try:
        # Imports are local to avoid a circular import between
        # accounts.views.auth and events.views.api at module load time.
        # Reuses the auth-side sender so the verify URL, token shape
        # and template context match the password-signup flow exactly.
        from accounts.views.auth import _send_verification_email

        return _send_verification_email(user)
    except Exception:
        logger.exception(
            'Failed to send post-registration verification email to %s',
            user.email,
        )
        return None


def _register_anonymous(request, event):
    """Anonymous email-only event registration path (issue #513).

    Only allowed for upcoming events with ``required_level == LEVEL_OPEN``.
    Anonymous registration on gated events is rejected with 403 — anonymous
    email submission must NOT be a way to bypass the tier check.
    """
    try:
        data = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    email = (data.get('email') or '').strip().lower()
    if not _is_valid_email(email):
        return JsonResponse(
            {'error': 'A valid email address is required'},
            status=400,
        )

    # Gate anonymous registration to free events only. Gated events keep
    # the existing tier-check CTA on the detail page; we must not let an
    # unauthenticated email submit bypass it.
    if event.required_level > LEVEL_OPEN:
        return JsonResponse(
            {'error': 'Insufficient access level'},
            status=403,
        )

    # If the email already maps to a User, register that user. Do NOT
    # touch their tier, password, ``email_verified`` flag, or
    # ``verification_expires_at`` — the existing account is canonical.
    existing_user = User.objects.filter(email__iexact=email).first()
    if existing_user is not None:
        # Defensive: if the existing user somehow can't access the event
        # (e.g. they were downgraded), behave the same as the gated
        # response above. ``required_level == 0`` should always pass
        # this check today, but check explicitly so the contract is
        # consistent with the authenticated path.
        if not can_access(existing_user, event):
            return JsonResponse(
                {'error': 'Insufficient access level'},
                status=403,
            )

        if EventRegistration.objects.filter(
            event=event, user=existing_user,
        ).exists():
            return JsonResponse(
                {'error': 'Already registered'},
                status=409,
            )

        if event.is_full:
            return JsonResponse({'error': 'Event is full'}, status=410)

        registration = EventRegistration.objects.create(
            event=event, user=existing_user,
        )
        try:
            from events.services.registration_email import (
                send_registration_confirmation,
            )
            send_registration_confirmation(registration)
        except Exception:
            logger.exception(
                'Failed to send registration email for event "%s" to user %s',
                event.slug, existing_user.email,
            )
        return JsonResponse({
            'status': 'registered',
            'event_slug': event.slug,
            'registered_at': registration.registered_at.isoformat(),
            'account_created': False,
        }, status=201)

    # No existing user — create a free, unverified one.
    if event.is_full:
        # Don't create an orphan unverified account if registration is
        # going to fail anyway.
        return JsonResponse({'error': 'Event is full'}, status=410)

    user = _create_unverified_subscriber(email)
    registration = EventRegistration.objects.create(event=event, user=user)

    # Send registration confirmation (with .ics) AND the verification
    # email so the user can claim the account. Both emails are sent
    # because they serve different jobs: registration carries the
    # calendar invite, verification surfaces "we created an account"
    # and lets the user verify + sign in.
    try:
        from events.services.registration_email import (
            send_registration_confirmation,
        )
        send_registration_confirmation(registration)
    except Exception:
        logger.exception(
            'Failed to send registration email for event "%s" to user %s',
            event.slug, user.email,
        )

    _send_event_verification_email(user)

    return JsonResponse({
        'status': 'registered',
        'event_slug': event.slug,
        'registered_at': registration.registered_at.isoformat(),
        'account_created': True,
    }, status=201)


@require_POST
def register_for_event(request, slug):
    """Register a user for an event.

    Authenticated users post with no body (or a body without ``email``);
    anonymous users post ``{"email": "..."}`` and the view auto-creates a
    free unverified account on free events (``required_level == 0``).

    Returns:
        201 on success, with ``account_created`` true/false in the body
        401 if anonymous and no email body provided
        403 if tier too low (or anonymous attempt on a gated event)
        404 if event not found or draft
        409 if already registered or event not upcoming
        410 if event is full
        400 on invalid JSON or invalid email
    """
    event = get_object_or_404(Event, slug=slug)
    if event.status == 'draft':
        return JsonResponse({'error': 'Event not found'}, status=404)
    if event.status != 'upcoming':
        return JsonResponse(
            {'error': 'Event is not open for registration'},
            status=409,
        )

    if not request.user.is_authenticated:
        # Anonymous request with an email body uses the email-only
        # registration path. Anonymous request with no email body keeps
        # the historical 401 behavior so callers that expected a login
        # gate are not broken.
        try:
            preview = json.loads(request.body or b"{}")
        except (json.JSONDecodeError, ValueError):
            preview = {}
        if isinstance(preview, dict) and preview.get('email'):
            return _register_anonymous(request, event)
        return JsonResponse(
            {'error': 'Authentication required'},
            status=401,
        )

    # Check access (tier level) for the authenticated user.
    if not can_access(request.user, event):
        return JsonResponse(
            {'error': 'Insufficient access level'},
            status=403,
        )

    # Check if already registered
    if EventRegistration.objects.filter(
        event=event, user=request.user,
    ).exists():
        return JsonResponse(
            {'error': 'Already registered'},
            status=409,
        )

    # Check capacity
    if event.is_full:
        return JsonResponse(
            {'error': 'Event is full'},
            status=410,
        )

    # Register the user
    registration = EventRegistration.objects.create(
        event=event, user=request.user,
    )

    # Send confirmation email with calendar invite (non-blocking)
    try:
        from events.services.registration_email import send_registration_confirmation
        send_registration_confirmation(registration)
    except Exception:
        logger.exception(
            'Failed to send registration email for event "%s" to user %s',
            event.slug, request.user.email,
        )

    return JsonResponse({
        'status': 'registered',
        'event_slug': event.slug,
        'registered_at': registration.registered_at.isoformat(),
    }, status=201)


@require_http_methods(['DELETE'])
def unregister_from_event(request, slug):
    """Unregister the authenticated user from an event.

    Returns:
        200 on success
        401 if not authenticated
        404 if event not found or not registered
    """
    if not request.user.is_authenticated:
        return JsonResponse(
            {'error': 'Authentication required'},
            status=401,
        )

    event = get_object_or_404(Event, slug=slug)
    if event.status == 'draft':
        return JsonResponse({'error': 'Event not found'}, status=404)
    if event.status != 'upcoming':
        return JsonResponse(
            {'error': 'Event is not open for registration'},
            status=409,
        )

    deleted_count, _ = EventRegistration.objects.filter(
        event=event, user=request.user,
    ).delete()

    if deleted_count == 0:
        return JsonResponse(
            {'error': 'Not registered for this event'},
            status=404,
        )

    return JsonResponse({
        'status': 'unregistered',
        'event_slug': event.slug,
    })
