"""Background tasks for notifying attendees when an event is cancelled."""

import logging

from django.contrib.auth import get_user_model
from django.template.loader import render_to_string

from accounts.services.timezones import (
    build_timezone_account_url,
    build_timezone_email_line,
    format_user_datetime,
)
from email_app.services.email_service import EmailService
from events.models import Event, EventRegistration, SeriesRegistration
from events.services.calendar_invite import AUDIENCE_HOST, generate_ics
from events.services.calendar_lifecycle import user_has_permanent_bounce
from events.services.host_registration import is_host_registration
from events.services.registration_email import _send_raw_email
from integrations.config import site_base_url

logger = logging.getLogger(__name__)


def enqueue_cancellation_notice(event_id):
    """Enqueue the stage-1 fan-out for a cancelled event."""
    from jobs.tasks import async_task, build_task_name

    return async_task(
        "events.tasks.notify_cancellation.send_cancellation_notice_fanout",
        event_id,
        task_name=build_task_name(
            "Send cancellation notice",
            f"event #{event_id}",
            "event cancellation",
        ),
    )


def send_cancellation_notice_fanout(event_id):
    """Stage-1 fan-out: enqueue one cancellation send per registration."""
    try:
        event = Event.objects.get(pk=event_id)
    except Event.DoesNotExist:
        logger.warning(
            "send_cancellation_notice_fanout: event %s no longer exists",
            event_id,
        )
        return {"status": "skipped", "reason": "missing_event", "event_id": event_id}

    from jobs.tasks import async_task, build_task_name

    registrations = list(
        EventRegistration.objects.filter(event=event).select_related('user'),
    )
    for registration in registrations:
        async_task(
            "events.tasks.notify_cancellation.send_cancellation_notice_one",
            event_id,
            registration.user_id,
            task_name=build_task_name(
                "Send cancellation notice (user)",
                f"event #{event_id} user #{registration.user_id}",
                "event cancellation fan-out",
            ),
        )

    logger.info(
        "Cancellation notice fan-out: event %s, %d registrations enqueued",
        event_id, len(registrations),
    )
    return {"status": "enqueued", "event_id": event_id, "count": len(registrations)}


def send_cancellation_notice_one(event_id, user_id):
    """Stage-2 per-user cancellation send."""
    User = get_user_model()

    try:
        event = Event.objects.get(pk=event_id)
    except Event.DoesNotExist:
        return {"status": "skipped", "reason": "missing_event", "event_id": event_id}

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return {"status": "skipped", "reason": "missing_user", "user_id": user_id}

    if user_has_permanent_bounce(user):
        return {
            "status": "skipped",
            "reason": "permanent_bounce",
            "user_id": user_id,
        }

    try:
        registration = EventRegistration.objects.get(event=event, user=user)
    except EventRegistration.DoesNotExist:
        return {
            "status": "skipped",
            "reason": "registration_cancelled",
            "user_id": user_id,
        }

    if event.event_series_id and SeriesRegistration.objects.filter(
        series_id=event.event_series_id, user=user,
    ).exists():
        return {
            "status": "skipped",
            "reason": "series_subscriber",
            "user_id": user_id,
        }

    site_url = site_base_url()
    is_host = is_host_registration(registration)

    from email_app.models import EmailLog
    dedupe_key = f'event-cancelled:{event.pk}:{user.pk}:{event.ics_sequence}'
    existing_log = EmailLog.objects.filter(dedupe_key=dedupe_key).first()
    if existing_log is not None:
        return {
            'status': 'deduplicated',
            'user_id': user_id,
            'email_log_id': existing_log.pk,
        }
    email_service = EmailService()
    subject, body_html = email_service._render_template(
        'event_cancelled',
        user,
        {
            'event_title': event.title,
            'event_datetime': format_user_datetime(event.start_datetime, user),
            'timezone_help': build_timezone_email_line(
                user, build_timezone_account_url(site_url),
            ),
        },
    )
    full_html = render_to_string('email_app/base_email.html', {
        'subject': subject,
        'body_html': body_html,
    })
    ics_content = generate_ics(
        event,
        method='CANCEL',
        audience=AUDIENCE_HOST if is_host else 'attendee',
        attendee_email=user.email,
    )
    ses_message_id = _send_raw_email(
        to_email=user.email,
        subject=subject,
        html_body=full_html,
        ics_content=ics_content,
        method='CANCEL',
    )

    email_log = EmailLog.objects.create(
        user=user,
        event=event,
        email_type='event_cancelled',
        ses_message_id=ses_message_id,
        dedupe_key=dedupe_key,
    )

    logger.info(
        'Sent cancellation notice to %s for event "%s" (SES: %s)',
        user.email, event.title, ses_message_id,
    )
    return {"status": "sent", "user_id": user_id, "email_log_id": email_log.pk}
