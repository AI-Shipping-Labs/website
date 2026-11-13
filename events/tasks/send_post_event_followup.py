"""Background tasks for the post-event follow-up email (issue #680).

Submits all eligible post-event follow-up notices as ONE Relay campaign
so Relay owns pacing, retries, and deduplication.

Unsubscribed users still receive the follow-up — the message is
transactional (the recipient registered for this event).
"""

from __future__ import annotations

import logging

from email_app.relay_transactional import recipient_template_context
from email_app.services.relay_templates import relay_template_subject
from events.models import Event, EventRegistration
from events.services.post_event_mail import (
    build_followup_context,
    recording_url_for,
)
from events.services.relay_event_notice import (
    build_relay_recipient,
    submit_event_notice_campaign,
)

logger = logging.getLogger(__name__)

INTERVAL_FOLLOWUP = "followup"


def enqueue_post_event_followup(event_id):
    """Enqueue the post-event follow-up campaign submission."""
    from jobs.tasks import async_task, build_task_name

    return async_task(
        "events.tasks.send_post_event_followup.send_post_event_followup_fanout",
        event_id,
        task_name=build_task_name(
            "Send post-event follow-up",
            f"event #{event_id}",
            "post-event recap fan-out",
        ),
    )


def send_post_event_followup_fanout(event_id):
    """Submit all eligible post-event follow-ups as one Relay campaign."""
    from notifications.models import EventReminderLog

    event = Event.objects.filter(pk=event_id).first()
    if event is None:
        logger.warning("send_post_event_followup_fanout: event %s no longer exists", event_id)
        return {"status": "skipped", "reason": "missing_event", "event_id": event_id}

    if not recording_url_for(event):
        logger.warning(
            "send_post_event_followup_fanout: event %s has no recording URL",
            event_id,
        )
        return {"status": "skipped", "reason": "no_recording_url", "event_id": event_id}

    already_sent_ids = set(
        EventReminderLog.objects.filter(event=event, interval=INTERVAL_FOLLOWUP)
        .values_list("user_id", flat=True),
    )
    registrations = list(
        EventRegistration.objects.filter(event=event).select_related("user"),
    )
    eligible = [reg.user for reg in registrations if reg.user_id not in already_sent_ids]
    if not eligible:
        return {"status": "skipped", "reason": "no_recipients", "event_id": event_id, "count": 0}

    base_context = build_followup_context(event)
    recipients = [
        build_relay_recipient(
            user.email,
            f"user-{user.pk}",
            recipient_template_context(user, base_context),
        )
        for user in eligible
    ]
    external_key = f"event-followup-{event.pk}"
    subject = relay_template_subject(
        "post_event_followup",
        recipient_template_context(eligible[0], base_context),
    )
    submit_event_notice_campaign(
        external_key=external_key,
        subject=subject,
        template_key="post_event_followup",
        recipients=recipients,
        context=base_context,
        metadata={"event_id": event.pk, "email_type": "post_event_followup"},
    )
    _record_followup_logs(event, eligible, external_key, subject)
    logger.info(
        'Submitted post-event follow-up campaign %s for event "%s" (%d recipients)',
        external_key, event.title, len(eligible),
    )
    return {
        "status": "submitted",
        "event_id": event_id,
        "count": len(eligible),
        "external_key": external_key,
    }


def _record_followup_logs(event, users, external_key: str, subject: str):
    from email_app.models import EmailLog
    from notifications.models import EventReminderLog

    EventReminderLog.objects.bulk_create(
        [EventReminderLog(event=event, user=user, interval=INTERVAL_FOLLOWUP) for user in users],
        ignore_conflicts=True,
    )
    for user in users:
        EmailLog.objects.get_or_create(
            dedupe_key=f"post_event_followup:{event.pk}:{user.pk}",
            defaults={
                "user": user,
                "event": event,
                "recipient_email": user.email,
                "email_type": "post_event_followup",
                "subject": subject,
                "ses_message_id": external_key,
            },
        )
