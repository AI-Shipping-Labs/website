import datetime
import json
import logging

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from accounts.services.timezones import is_valid_timezone
from accounts.services.verification import resolve_unverified_ttl_days
from content.access import LEVEL_OPEN, can_access
from events.models import Event, EventRegistration, EventSeries
from events.services.series_registration import (
    enroll_user_in_series,
    series_registration_summary,
)
from events.views.pages import _resolve_cancel_state

logger = logging.getLogger(__name__)

User = get_user_model()

# Rate-limit knobs for the anonymous email-submit branch (issue #672).
# Mirrors the cache-add pattern shipped under #448; no new library is
# introduced. Limits apply only to the anonymous path — authenticated
# registration is unaffected.
ANON_REGISTER_IP_LIMIT = 5
ANON_REGISTER_IP_WINDOW_SECONDS = 60
ANON_REGISTER_EMAIL_LIMIT = 3
ANON_REGISTER_EMAIL_WINDOW_SECONDS = 3600
ANON_REGISTER_IP_CACHE_KEY = "event-anon-register:ip:{addr}"
ANON_REGISTER_EMAIL_CACHE_KEY = "event-anon-register:email:{email}"
ANON_REGISTER_RATE_LIMIT_MESSAGE = (
    "Too many registration attempts. Please try again in a few minutes."
)


def _consume_anon_register_rate_limit(request, email):
    """Return ``True`` if the request is rate-limited, ``False`` otherwise.

    Atomically increments per-IP and per-email counters in the cache. The
    first gate to trip wins — both counters are advanced only when both
    pass so we do not penalise a user whose email gate is fine after
    their IP gate has already fired.

    The IP comes from ``REMOTE_ADDR`` — the existing nginx / Cloudflare
    setup rewrites it for us, matching ``_resolve_cancel_state``'s style.
    """
    ip = request.META.get("REMOTE_ADDR") or ""
    ip_key = ANON_REGISTER_IP_CACHE_KEY.format(addr=ip)
    email_key = ANON_REGISTER_EMAIL_CACHE_KEY.format(email=email.lower())

    # ``cache.add`` only sets when the key is missing; once it exists we
    # use ``cache.incr`` so concurrent requests increment the same slot.
    if cache.add(ip_key, 1, ANON_REGISTER_IP_WINDOW_SECONDS):
        ip_count = 1
    else:
        try:
            ip_count = cache.incr(ip_key)
        except ValueError:
            # Key expired between ``add`` and ``incr``; treat as fresh.
            cache.add(ip_key, 1, ANON_REGISTER_IP_WINDOW_SECONDS)
            ip_count = 1
    if ip_count > ANON_REGISTER_IP_LIMIT:
        return True

    if cache.add(email_key, 1, ANON_REGISTER_EMAIL_WINDOW_SECONDS):
        email_count = 1
    else:
        try:
            email_count = cache.incr(email_key)
        except ValueError:
            cache.add(email_key, 1, ANON_REGISTER_EMAIL_WINDOW_SECONDS)
            email_count = 1
    if email_count > ANON_REGISTER_EMAIL_LIMIT:
        return True

    return False


def _is_valid_email(email):
    """Cheap structural email check — same rules as ``subscribe_api``."""
    if not email:
        return False
    if "@" not in email:
        return False
    domain = email.split("@", 1)[1]
    return "." in domain


def _create_unverified_subscriber(email, preferred_timezone=""):
    """Create a free, unverified ``User`` for an anonymous registrant.

    Mirrors ``subscribe_api`` so anonymous event registration produces the
    same shape of row: free tier, ``email_verified=False``, and a
    ``verification_expires_at`` window so the daily purge job (#452)
    cleans up abandoned accounts. ``import_source`` defaults to
    ``"manual"`` and ``imported_at`` stays ``None``.

    ``preferred_timezone`` (issue #672) is the browser-detected IANA
    timezone forwarded by the email-only registration form. Callers are
    expected to validate the string before passing it in — invalid /
    missing values become ``""`` (UTC fallback in
    ``format_user_datetime``).
    """
    ttl_days = resolve_unverified_ttl_days()
    verification_expires_at = (
        timezone.now() + datetime.timedelta(days=ttl_days)
    )
    return User.objects.create_user(
        email=email,
        verification_expires_at=verification_expires_at,
        preferred_timezone=preferred_timezone,
        signup_source="signup",
    )


def _send_event_verification_email(user):
    """Send the standard ``email_verification_signup`` email after anonymous register.

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
    # unauthenticated email submit bypass it. Bail BEFORE consuming a
    # rate-limit slot — the gate decision is deterministic per slug and
    # bots probing gated events should not exhaust a legitimate user's
    # quota on the same IP.
    if event.required_level > LEVEL_OPEN:
        return JsonResponse(
            {'error': 'Insufficient access level'},
            status=403,
        )

    # Rate-limit only the anonymous email-submit branch (issue #672).
    # Authenticated registration is unaffected — it takes a different
    # code path.
    if _consume_anon_register_rate_limit(request, email):
        return JsonResponse(
            {'error': ANON_REGISTER_RATE_LIMIT_MESSAGE},
            status=429,
        )

    # Browser-detected IANA timezone (issue #672). Optional; invalid /
    # missing values fall back to ``""`` so the email rendering helper
    # uses UTC. Never reject the request on a bad TZ alone.
    raw_timezone = data.get('timezone') or ''
    submitted_timezone = (
        raw_timezone if is_valid_timezone(raw_timezone) else ""
    )

    # If the email already maps to a User, register that user. Do NOT
    # touch their tier, password, ``email_verified`` flag, or
    # ``verification_expires_at`` — the existing account is canonical.
    existing_user = User.objects.filter(email__iexact=email).first()
    if existing_user is not None:
        # Issue #672: the event-level gate above (``required_level >
        # LEVEL_OPEN``) is the authoritative tier check for the
        # anonymous email-submit branch. ``can_access`` additionally
        # enforces ``email_verified`` even on LEVEL_OPEN content, which
        # would 403 an existing-unverified user — but the whole point
        # of this branch is to register them AND send a fresh claim
        # link (gap 1). So we skip ``can_access`` here. The new-user
        # path below registers unverified users by construction, so
        # this keeps both paths consistent.

        # Backfill ``preferred_timezone`` only when the user has none.
        # A non-empty existing value reflects either an account-setting
        # choice or a previous anonymous submit — both are canonical.
        if (
            submitted_timezone
            and not existing_user.preferred_timezone
        ):
            existing_user.preferred_timezone = submitted_timezone
            existing_user.save(update_fields=["preferred_timezone"])

        existing_registration = EventRegistration.objects.filter(
            event=event, user=existing_user,
        ).first()
        if existing_registration is not None:
            # Idempotent resubmit (issue #672, gap 4): return 201 with
            # ``already_registered: true`` so the frontend lands on the
            # same confirmation block instead of surfacing a 409. Do
            # NOT re-send any emails, do NOT create a duplicate row.
            return JsonResponse({
                'status': 'registered',
                'event_slug': event.slug,
                'registered_at': (
                    existing_registration.registered_at.isoformat()
                ),
                'account_created': False,
                'already_registered': True,
            }, status=201)

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

        # Issue #672, gap 1: an existing unverified user also needs the
        # claim-account magic link in their inbox. Verified accounts
        # are skipped — re-sending the verify email there would be
        # noise, and ``test_anonymous_with_existing_user_registers_without_resetting``
        # asserts the no-spam contract.
        if not existing_user.email_verified:
            _send_event_verification_email(existing_user)

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

    user = _create_unverified_subscriber(
        email, preferred_timezone=submitted_timezone,
    )
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
    # Issue #713: gate on the time-derived ``is_upcoming`` so a stale
    # ``status='upcoming'`` row whose end has passed already returns
    # 409 (rather than waiting for the daily cron to flip the field).
    if not event.is_upcoming:
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

    # Issue #768: event registration is a real platform action — flip
    # ``account_activated`` for the authenticated user. Idempotent.
    from accounts.utils.activation import mark_activated
    mark_activated(request.user)

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
    # Issue #713: gate on the time-derived ``is_upcoming``.
    if not event.is_upcoming:
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


@require_http_methods(['POST', 'DELETE'])
def series_registration(request, series_slug):
    """Register for / unregister from an entire event series (issue #857).

    POST creates the standing ``SeriesRegistration`` flag and fans it out
    into real per-event ``EventRegistration`` rows for every eligible
    upcoming occurrence (future, non-draft, non-cancelled, accessible by
    tier, not full, not already registered). One summary confirmation
    email is sent rather than N per-event emails.

    DELETE removes the standing flag AND unregisters the user from all
    FUTURE occurrences of the series. PAST occurrences the user attended
    are left intact so the dashboard history is preserved.

    Series registration is authenticated-only — the anonymous email path
    stays on the single-event route only.

    Returns:
        POST 201 with the fan-out summary on a fresh registration.
        POST 200 with the current summary when already series-registered
            (idempotent — no duplicate flag, no duplicate per-event rows).
        DELETE 200 with the count of future occurrences dropped.
        401 if anonymous (no row created).
        404 if the series does not exist.
    """
    from events.models import SeriesRegistration

    if not request.user.is_authenticated:
        return JsonResponse(
            {'error': 'Authentication required'},
            status=401,
        )

    series = get_object_or_404(EventSeries, slug=series_slug)

    if request.method == 'DELETE':
        flag = SeriesRegistration.objects.filter(
            series=series, user=request.user,
        ).first()
        if flag is None:
            return JsonResponse(
                {'error': 'Not registered for this series'},
                status=404,
            )
        flag.delete()

        # Drop only FUTURE occurrences; past attended occurrences stay.
        future_event_ids = [
            event.id
            for event in series.events.exclude(
                status__in=('draft', 'cancelled'),
            )
            if event.is_upcoming
        ]
        dropped = 0
        if future_event_ids:
            dropped, _ = EventRegistration.objects.filter(
                user=request.user, event_id__in=future_event_ids,
            ).delete()

        return JsonResponse({
            'status': 'unregistered',
            'series_slug': series.slug,
            'dropped': dropped,
        })

    # POST — register for the series.
    existing = SeriesRegistration.objects.filter(
        series=series, user=request.user,
    ).first()
    if existing is not None:
        # Idempotent re-register: do not create a duplicate flag or new
        # per-event rows, do not re-send the email. Return the current
        # state so the caller lands on the same "you're registered" view.
        return JsonResponse({
            'status': 'already_registered',
            'series_slug': series.slug,
            'summary': series_registration_summary(request.user, series),
        }, status=200)

    SeriesRegistration.objects.create(series=series, user=request.user)
    summary = enroll_user_in_series(request.user, series)
    new_events = summary.pop('new_events', [])

    # Issue #768: series registration is a real platform action.
    from accounts.utils.activation import mark_activated
    mark_activated(request.user)

    # Send ONE summary confirmation email (non-blocking).
    if new_events:
        try:
            from events.services.registration_email import (
                send_series_registration_confirmation,
            )
            send_series_registration_confirmation(
                request.user, series, new_events,
            )
        except Exception:
            logger.exception(
                'Failed to send series registration email for series '
                '"%s" to user %s',
                series.slug, request.user.email,
            )

    return JsonResponse({
        'status': 'registered',
        'series_slug': series.slug,
        'summary': summary,
    }, status=201)


@csrf_exempt
@require_POST
def cancel_registration_action(request, slug):
    """Cancel an event registration via signed URL token (POST).

    Sister of ``events.views.pages.cancel_registration_page``. The token
    in the URL IS the authorization, mirroring the existing
    ``unsubscribe_api`` pattern, so CSRF is not required and the user
    does not need to be signed in. Always returns an HTML response —
    this is a user-facing page reached from the inbox, not an SPA API.
    """
    token = request.GET.get('token', '')
    state, ctx = _resolve_cancel_state(slug, token)

    if state == 'confirm':
        registration = ctx['registration']
        event = ctx['event']
        registration.delete()
        ctx = {
            'event': event,
            'event_url': ctx['event_url'],
            'message': (
                f'Your registration for {event.title} has been cancelled.'
            ),
        }
        state = 'success'

    ctx['state'] = state
    return render(request, 'events/cancel_registration_result.html', ctx)
