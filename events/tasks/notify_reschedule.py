"""Background tasks for notifying registered attendees when an event is rescheduled.

Submits one Relay campaign per reschedule event (with per-recipient context
and ``.ics`` payload) so Relay owns pacing, retries, and deduplication.
Calendar lifecycle email is transactional: global newsletter unsubscribe does
not suppress it. Permanent bounce state still suppresses delivery.
"""

from __future__ import annotations

import logging
from datetime import datetime

from accounts.services.timezones import (
    CALENDAR_INVITE_DATETIME_FORMAT,
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
from events.models import Event, EventRegistration, HostInviteDelivery, SeriesRegistration
from events.services.calendar_invite import AUDIENCE_ATTENDEE, generate_ics
from events.services.calendar_lifecycle import user_has_permanent_bounce
from events.services.cancel_token import generate_cancel_token
from events.services.host_registration import is_host_registration
from events.services.relay_event_notice import (
    build_relay_recipient,
    submit_event_notice_campaign,
)
from integrations.config import site_base_url

logger = logging.getLogger(__name__)


def enqueue_reschedule_notice(event_id, old_start_iso):
    """Enqueue the reschedule notice job for a rescheduled event."""
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
    """Submit all eligible reschedule notices as one Relay campaign."""
    event = Event.objects.filter(pk=event_id).first()
    if event is None:
        logger.warning("send_reschedule_notice_fanout: event %s no longer exists", event_id)
        return {"status": "skipped", "reason": "missing_event", "event_id": event_id}

    old_start = _parse_iso(old_start_iso)
    prepared = _prepare_reschedule_recipients(event, old_start)
    if not prepared:
        return {"status": "skipped", "reason": "no_recipients", "event_id": event_id, "count": 0}

    external_key = f"event-rescheduled-{event.pk}-seq-{event.ics_sequence}"
    subject = prepared[0]["subject"]
    submit_event_notice_campaign(
        external_key=external_key,
        subject=subject,
        template_key="event_rescheduled",
        recipients=[item["member"] for item in prepared],
        context={"event_title": event.title},
        sender_id=relay_sender_id(get_calendar_sender()),
        metadata={
            "event_id": event.pk,
            "email_type": "event_rescheduled",
            "ics_sequence": event.ics_sequence,
        },
    )
    _record_reschedule_logs(event, prepared, external_key)
    logger.info(
        'Submitted reschedule notice campaign %s for event "%s" (%d recipients)',
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


def _sent_host_user_ids(event) -> set[int]:
    return set(
        HostInviteDelivery.objects.filter(
            event=event,
            access_version=event.host_access_version,
            status=HostInviteDelivery.STATUS_SENT,
            sent_ics_sequence__gte=event.ics_sequence,
        ).values_list("user_id", flat=True),
    )


def _prepare_reschedule_recipients(event, old_start) -> list[dict]:
    from email_app.models import EmailLog

    series_ids = _series_subscriber_ids(event)
    sent_hosts = _sent_host_user_ids(event)
    registrations = EventRegistration.objects.filter(event=event).select_related("user")
    site_url = site_base_url()
    prepared = []
    for registration in registrations:
        user = registration.user
        if user_has_permanent_bounce(user) or user.pk in series_ids:
            continue
        is_host = is_host_registration(registration)
        if is_host and user.pk in sent_hosts:
            continue
        dedupe_key = f"event-rescheduled:{event.pk}:{user.pk}:{event.ics_sequence}"
        if EmailLog.objects.filter(dedupe_key=dedupe_key).exists():
            continue
        prepared.append(_build_reschedule_item(event, registration, old_start, site_url, is_host, dedupe_key))
    return prepared


def _build_reschedule_item(event, registration, old_start, site_url, is_host, dedupe_key) -> dict:
    user = registration.user
    cancel_token = generate_cancel_token(registration)
    base_ctx = {
        "event_title": event.title,
        "old_event_datetime": format_user_datetime(old_start, user, fmt=CALENDAR_INVITE_DATETIME_FORMAT),
        "new_event_datetime": format_user_datetime(event.start_datetime, user, fmt=CALENDAR_INVITE_DATETIME_FORMAT),
        "timezone_help": build_timezone_email_line(user, build_timezone_account_url(site_url)),
        "join_url": f"{site_url}{event.get_join_url()}",
        "cancel_url": f"{site_url}/events/{event.slug}/cancel-registration?token={cancel_token}",
        "is_host_registration": is_host,
    }
    full_ctx = recipient_template_context(user, base_ctx)
    subject = relay_template_subject("event_rescheduled", full_ctx)
    ics_content = generate_ics(
        event, method="REQUEST", audience=AUDIENCE_ATTENDEE, attendee_email=user.email,
    )
    calendar = calendar_alternative(ics_content, method="REQUEST", name="event.ics")
    member = build_relay_recipient(user.email, f"user-{user.pk}", full_ctx, calendar=calendar)
    return {"user": user, "dedupe_key": dedupe_key, "subject": subject, "member": member}


def _record_reschedule_logs(event, prepared: list[dict], external_key: str):
    from email_app.models import EmailLog

    for item in prepared:
        EmailLog.objects.get_or_create(
            dedupe_key=item["dedupe_key"],
            defaults={
                "user": item["user"],
                "event": event,
                "recipient_email": item["user"].email,
                "email_type": "event_rescheduled",
                "subject": item["subject"],
                "ses_message_id": external_key,
            },
        )


def _parse_iso(value):
    """Parse an ISO datetime string back into a timezone-aware datetime."""
    if value is None:
        return None
    return datetime.fromisoformat(value)
