"""Background tasks for notifying attendees when an event is cancelled."""

from __future__ import annotations

import logging

from accounts.services.timezones import (
    build_timezone_account_url,
    build_timezone_email_line,
    format_user_datetime,
)
from email_app.relay_transactional import (
    calendar_alternative,
    recipient_template_context,
)
from email_app.services.email_classification import (
    get_calendar_sender,
    relay_sender_id,
)
from email_app.services.relay_templates import relay_template_subject
from events.models import Event, EventRegistration, SeriesRegistration
from events.services.calendar_invite import AUDIENCE_ATTENDEE, generate_ics
from events.services.calendar_lifecycle import user_has_permanent_bounce
from events.services.relay_event_notice import (
    build_relay_recipient,
    submit_event_notice_campaign,
)
from integrations.config import site_base_url

logger = logging.getLogger(__name__)


def enqueue_cancellation_notice(event_id):
    """Enqueue the cancellation notice job for a cancelled event."""
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
    """Submit all eligible cancellation notices as one Relay campaign."""
    event = Event.objects.filter(pk=event_id).first()
    if event is None:
        logger.warning("send_cancellation_notice_fanout: event %s no longer exists", event_id)
        return {"status": "skipped", "reason": "missing_event", "event_id": event_id}

    prepared = _prepare_cancellation_recipients(event)
    if not prepared:
        return {"status": "skipped", "reason": "no_recipients", "event_id": event_id, "count": 0}

    external_key = f"event-cancelled-{event.pk}-seq-{event.ics_sequence}"
    subject = prepared[0]["subject"]
    submit_event_notice_campaign(
        external_key=external_key,
        subject=subject,
        template_key="event_cancelled",
        recipients=[item["member"] for item in prepared],
        context={"event_title": event.title},
        sender_id=relay_sender_id(get_calendar_sender()),
        metadata={
            "event_id": event.pk,
            "email_type": "event_cancelled",
            "ics_sequence": event.ics_sequence,
        },
    )
    _record_cancellation_logs(event, prepared, external_key)
    logger.info(
        'Submitted cancellation notice campaign %s for event "%s" (%d recipients)',
        external_key, event.title, len(prepared),
    )
    return {
        "status": "submitted",
        "event_id": event_id,
        "count": len(prepared),
        "external_key": external_key,
    }


def _series_subscriber_ids(event) -> set[int]:
    if not event.event_series_id:
        return set()
    return set(
        SeriesRegistration.objects.filter(series_id=event.event_series_id)
        .values_list("user_id", flat=True),
    )


def _prepare_cancellation_recipients(event) -> list[dict]:
    from email_app.models import EmailLog

    series_ids = _series_subscriber_ids(event)
    registrations = EventRegistration.objects.filter(event=event).select_related("user")
    site_url = site_base_url()
    prepared = []
    for registration in registrations:
        user = registration.user
        if user_has_permanent_bounce(user) or user.pk in series_ids:
            continue
        dedupe_key = f"event-cancelled:{event.pk}:{user.pk}:{event.ics_sequence}"
        if EmailLog.objects.filter(dedupe_key=dedupe_key).exists():
            continue
        prepared.append(_build_cancellation_item(event, user, site_url, dedupe_key))
    return prepared


def _build_cancellation_item(event, user, site_url, dedupe_key) -> dict:
    base_ctx = {
        "event_title": event.title,
        "event_datetime": format_user_datetime(event.start_datetime, user),
        "timezone_help": build_timezone_email_line(user, build_timezone_account_url(site_url)),
    }
    full_ctx = recipient_template_context(user, base_ctx)
    subject = relay_template_subject("event_cancelled", full_ctx)
    ics_content = generate_ics(
        event, method="CANCEL", audience=AUDIENCE_ATTENDEE, attendee_email=user.email,
    )
    calendar = calendar_alternative(ics_content, method="CANCEL", name="event.ics")
    member = build_relay_recipient(user.email, f"user-{user.pk}", full_ctx, calendar=calendar)
    return {"user": user, "dedupe_key": dedupe_key, "subject": subject, "member": member}


def _record_cancellation_logs(event, prepared: list[dict], external_key: str):
    from email_app.models import EmailLog

    for item in prepared:
        EmailLog.objects.get_or_create(
            dedupe_key=item["dedupe_key"],
            defaults={
                "user": item["user"],
                "event": event,
                "recipient_email": item["user"].email,
                "email_type": "event_cancelled",
                "subject": item["subject"],
                "ses_message_id": external_key,
            },
        )
