"""Shared Relay campaign submit and status helpers for event notices."""

from __future__ import annotations

import logging

from email_app.services.relay_campaigns import (
    RelayCampaignError,
    get_native_campaign_stats,
    submit_native_campaign,
)

logger = logging.getLogger(__name__)


def build_relay_recipient(
    email: str,
    source_object_key: str,
    context: dict,
    *,
    calendar: dict | None = None,
) -> dict:
    """Build one Relay campaign recipient entry with per-recipient context."""
    relay_context = dict(context)
    if calendar is not None:
        relay_context["calendar_alternative"] = calendar
    member = {
        "email": email,
        "source_object_key": source_object_key,
        "subscribe_if_missing": True,
        "metadata": {
            "relay_campaign_context": relay_context,
        },
    }
    if calendar is not None:
        member["calendar_alternative"] = calendar
    return member


def submit_event_notice_campaign(
    *,
    external_key: str,
    subject: str,
    template_key: str,
    recipients: list[dict],
    context: dict | None = None,
    sender_id: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Submit an event notice batch as one Relay campaign."""
    return submit_native_campaign(
        external_key=external_key,
        subject=subject,
        template_key=template_key,
        recipients=recipients,
        context=context,
        sender_id=sender_id,
        metadata=metadata,
    )


def _notice_candidates(event) -> list[tuple[str, str]]:
    """Return (label, external_key) pairs for notices sent on this event."""
    from email_app.models import EmailLog
    from notifications.models import EventReminderLog

    candidates = []
    if EmailLog.objects.filter(event=event, email_type="event_rescheduled").exists():
        candidates.append((
            "Reschedule notice",
            f"event-rescheduled-{event.pk}-seq-{event.ics_sequence}",
        ))
    if EmailLog.objects.filter(event=event, email_type="event_cancelled").exists():
        candidates.append((
            "Cancellation notice",
            f"event-cancelled-{event.pk}-seq-{event.ics_sequence}",
        ))
    if EventReminderLog.objects.filter(event=event, interval="followup").exists():
        candidates.append(("Post-event follow-up", f"event-followup-{event.pk}"))
    if EmailLog.objects.filter(event=event, email_type="event_workshop_ready").exists():
        candidates.append(("Workshop-ready broadcast", f"event-workshop-ready-{event.pk}"))
    if EmailLog.objects.filter(event=event, email_type="event_recording_ready").exists():
        candidates.append(("Recording-ready notice", f"event-recording-ready-{event.pk}"))
    recap_keys = (
        EmailLog.objects.filter(event=event, email_type="event_recap_ready")
        .exclude(ses_message_id="")
        .values_list("ses_message_id", flat=True)
        .distinct()
        .order_by("ses_message_id")
    )
    for external_key in recap_keys:
        if external_key.startswith("event-recap-ready-"):
            candidates.append(("Recap-ready notice", external_key))
    return candidates


def get_event_notice_statuses(event) -> list[dict]:
    """Read sent/delivered/pending counts from Relay for an event's notices."""
    rows = []
    for label, external_key in _notice_candidates(event):
        stats = _safe_campaign_stats(external_key)
        if stats is None:
            continue
        rows.append({
            "label": label,
            "external_key": external_key,
            "sent_count": stats["sent_count"],
            "delivered_count": stats["delivered_count"],
            "pending_count": stats["pending_count"],
            "recipient_count": stats.get("recipient_count", 0),
        })
    return rows


def _safe_campaign_stats(external_key: str) -> dict | None:
    try:
        return get_native_campaign_stats(external_key, allow_not_found=True)
    except RelayCampaignError:
        logger.warning(
            "Could not read Relay status for event notice %s",
            external_key,
        )
        return None
