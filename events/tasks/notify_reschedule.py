"""Background tasks for notifying registered attendees when an event is rescheduled.

Two-stage fan-out (issue #670):

1. ``enqueue_reschedule_notice(event_id, old_start_iso)`` — enqueues the
   stage-1 fan-out task. Called from the Studio edit view after detecting
   a start_datetime change.

2. ``send_reschedule_notice_fanout(event_id, old_start_iso)`` — loads the
   event, iterates its registrations, and enqueues one stage-2 per-user
   task per registration. Mirrors the campaign send_campaign / batch
   split so a poisoned per-user send does not kill the whole batch.

3. ``send_reschedule_notice_one(event_id, user_id, old_start_iso)`` —
   per-user send. Re-loads the recipient, formats both old and new
   start times in the recipient's timezone via the shared
   ``format_user_datetime`` helper, renders the ``event_rescheduled``
   template, sends a raw SES message with a fresh ``.ics`` attachment
   carrying ``METHOD:REQUEST`` and a bumped SEQUENCE, and writes one
   ``EmailLog(email_type='event_rescheduled')`` row.

Skips unsubscribed users (mirrors ``email_app.tasks.welcome_imported``)
even though the message is transactional — a fully unsubscribed user
should not be re-notified.
"""

import logging

from django.contrib.auth import get_user_model
from django.template.loader import render_to_string

from accounts.services.timezones import (
    CALENDAR_INVITE_DATETIME_FORMAT,
    build_timezone_account_url,
    build_timezone_email_line,
    format_user_datetime,
)
from events.models import Event, EventRegistration, SeriesRegistration
from events.services.calendar_invite import generate_ics
from events.services.cancel_token import generate_cancel_token
from events.services.host_registration import (
    build_host_management_links,
    is_host_registration,
)
from events.services.registration_email import _send_raw_email
from integrations.config import site_base_url

logger = logging.getLogger(__name__)


def enqueue_reschedule_notice(event_id, old_start_iso):
    """Enqueue the stage-1 fan-out for a rescheduled event.

    Called synchronously from the Studio edit view; the actual fan-out
    (and every per-user send) runs on a worker so the request returns
    quickly even when an event has thousands of registrations.
    """
    from jobs.tasks import async_task, build_task_name

    return async_task(
        "events.tasks.notify_reschedule.send_reschedule_notice_fanout",
        event_id,
        old_start_iso,
        task_name=build_task_name(
            "Send reschedule notice",
            f"event #{event_id}",
            "studio reschedule",
        ),
    )


def send_reschedule_notice_fanout(event_id, old_start_iso):
    """Stage-1 fan-out: enqueue one per-user send job per registration.

    Uses ``select_related('user')`` so the registration list is fetched
    with a single JOIN (no N+1 over the user FK when we serialize
    ``user_id`` for the per-user task). The actual user object is
    re-fetched in the per-user job, so a stale user in this iteration
    is not a correctness issue — only an efficiency one.
    """
    try:
        event = Event.objects.get(pk=event_id)
    except Event.DoesNotExist:
        logger.warning(
            "send_reschedule_notice_fanout: event %s no longer exists",
            event_id,
        )
        return {"status": "skipped", "reason": "missing_event", "event_id": event_id}

    from jobs.tasks import async_task, build_task_name

    registrations = list(
        EventRegistration.objects.filter(event=event).select_related('user'),
    )

    for registration in registrations:
        async_task(
            "events.tasks.notify_reschedule.send_reschedule_notice_one",
            event_id,
            registration.user_id,
            old_start_iso,
            task_name=build_task_name(
                "Send reschedule notice (user)",
                f"event #{event_id} user #{registration.user_id}",
                "studio reschedule fan-out",
            ),
        )

    logger.info(
        "Reschedule notice fan-out: event %s, %d registrations enqueued",
        event_id, len(registrations),
    )
    return {
        "status": "enqueued",
        "event_id": event_id,
        "count": len(registrations),
    }


def send_reschedule_notice_one(event_id, user_id, old_start_iso):
    """Stage-2 per-user send.

    Skips unsubscribed users. Renders ``old_start`` and the event's
    current ``start_datetime`` in the recipient's preferred timezone
    (UTC fallback when unset) so the body shows both times in the same
    zone — never UTC for one and local for the other.

    Writes one ``EmailLog(email_type='event_rescheduled')`` row per
    successful send.
    """
    User = get_user_model()

    try:
        event = Event.objects.get(pk=event_id)
    except Event.DoesNotExist:
        return {"status": "skipped", "reason": "missing_event", "event_id": event_id}

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return {"status": "skipped", "reason": "missing_user", "user_id": user_id}

    if user.unsubscribed:
        return {"status": "skipped", "reason": "unsubscribed", "user_id": user_id}

    # The registration may have been cancelled between fan-out and the
    # per-user send; emailing a rescheduled time to someone who already
    # cancelled would be wrong.
    try:
        registration = EventRegistration.objects.get(event=event, user=user)
    except EventRegistration.DoesNotExist:
        return {
            "status": "skipped",
            "reason": "registration_cancelled",
            "user_id": user_id,
        }

    # Issue #869 de-dup contract: a series subscriber's canonical update
    # for a time change is the multi-event series invite
    # (``send_series_update_to_subscribers``), sent from the Studio
    # reschedule path. To avoid sending that subscriber two emails for the
    # same change (a single-event reschedule notice AND a series update),
    # the per-event reschedule notice goes to one-off registrants only.
    if event.event_series_id and SeriesRegistration.objects.filter(
        series_id=event.event_series_id, user=user,
    ).exists():
        return {
            "status": "skipped",
            "reason": "series_subscriber",
            "user_id": user_id,
        }

    old_start = _parse_iso(old_start_iso)

    site_url = site_base_url()
    join_url = f'{site_url}/events/{event.slug}/join'
    cancel_token = generate_cancel_token(registration)
    cancel_url = (
        f'{site_url}/events/{event.slug}/cancel-registration?token={cancel_token}'
    )
    is_host = is_host_registration(registration)
    host_links = build_host_management_links(event) if is_host else {}

    # Lazy import to avoid pulling the full EmailService at module load.
    from email_app.services.email_service import EmailService

    email_service = EmailService()
    subject, body_html = email_service._render_template(
        'event_rescheduled',
        user,
        {
            'event_title': event.title,
            # Issue #666 contract: pre-format BOTH times via
            # format_user_datetime so the template context carries
            # strings, not raw datetimes. Both render in the recipient's
            # timezone (or UTC fallback) — never mismatched. Issue #1071:
            # calendar-invite emails carry the weekday via the dedicated
            # CALENDAR_INVITE_DATETIME_FORMAT (the global default stays
            # weekday-free).
            'old_event_datetime': format_user_datetime(
                old_start, user, fmt=CALENDAR_INVITE_DATETIME_FORMAT,
            ),
            'new_event_datetime': format_user_datetime(
                event.start_datetime, user, fmt=CALENDAR_INVITE_DATETIME_FORMAT,
            ),
            'timezone_help': build_timezone_email_line(
                user, build_timezone_account_url(site_url),
            ),
            'join_url': join_url,
            'cancel_url': cancel_url,
            'is_host_registration': is_host,
            **host_links,
        },
    )

    full_html = render_to_string('email_app/base_email.html', {
        'subject': subject,
        'body_html': body_html,
    })

    # METHOD:REQUEST + bumped SEQUENCE so calendar clients overwrite the
    # original event entry instead of duplicating it. The SEQUENCE bump
    # itself happens at the Studio view layer (the same place that
    # detected the change), so by the time this task runs the event's
    # ``ics_sequence`` is already greater than whatever the registration
    # email's ``.ics`` carried.
    ics_content = generate_ics(event, method='REQUEST')

    ses_message_id = _send_raw_email(
        to_email=user.email,
        subject=subject,
        html_body=full_html,
        ics_content=ics_content,
        method='REQUEST',
    )

    from email_app.models import EmailLog
    email_log = EmailLog.objects.create(
        user=user,
        email_type='event_rescheduled',
        ses_message_id=ses_message_id,
    )

    logger.info(
        'Sent reschedule notice to %s for event "%s" (SES: %s)',
        user.email, event.title, ses_message_id,
    )

    return {"status": "sent", "user_id": user_id, "email_log_id": email_log.pk}


def _parse_iso(value):
    """Parse an ISO datetime string back into a timezone-aware datetime.

    The Studio view serializes ``old_start`` via ``datetime.isoformat()``
    so the worker receives a string (Django-Q's task arguments must be
    JSON-serialisable). This helper is the symmetric ``fromisoformat``
    that re-hydrates it without re-importing datetime at the call site.
    """
    from datetime import datetime
    if value is None:
        return None
    return datetime.fromisoformat(value)
