"""Host/operator notification for recordings uploaded from Zoom to S3."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from hashlib import sha256
from types import SimpleNamespace

from django.db import transaction
from django.db.models import Q

from accounts.services.email_resolution import resolve_user_by_email
from email_app.relay_transactional import recipient_template_context
from email_app.services.relay_templates import relay_template_subject
from events.services.recording_ready_context import build_recording_ready_context
from events.services.relay_event_notice import (
    build_relay_recipient,
    submit_event_notice_campaign,
)
from integrations.config import get_config

logger = logging.getLogger(__name__)

EMAIL_TYPE = "event_recording_ready"


@dataclass(frozen=True)
class RecordingReadyRecipient:
    email: str
    source: str
    user: object | None = None


def resolve_recording_ready_recipients(event):
    """Return host/operator recipients in the issue-defined order."""
    recipients = []
    seen = set()

    def add(raw_email, source):
        email = (raw_email or "").strip()
        if not email:
            return
        key = email.lower()
        if key in seen:
            return
        seen.add(key)
        recipients.append(
            RecordingReadyRecipient(
                email=email,
                source=source,
                user=resolve_user_by_email(email),
            ),
        )

    add(getattr(event, "host_email", ""), "event.host_email")
    host_links = (
        event.event_host_links.select_related("host")
        .filter(host__is_active=True)
        .order_by("position")
    )
    for link in host_links:
        add(link.host.email, "event.hosts")

    if recipients:
        return recipients

    fallback = (get_config("STAFF_SIGNUP_NOTIFY_EMAIL", "") or "").strip()
    add(fallback, "staff_fallback")
    return recipients


def notify_recording_ready(event):
    """Submit the recording-ready notification as one Relay campaign."""
    if not (getattr(event, "recording_s3_url", "") or "").strip():
        logger.warning("Skipping %s for event %s: recording_s3_url is empty", EMAIL_TYPE, event.pk)
        return _skipped_result("no_recording_s3_url")

    recipients = resolve_recording_ready_recipients(event)
    if not recipients:
        logger.warning("Skipping %s for event %s: no host or staff fallback recipient", EMAIL_TYPE, event.pk)
        return _skipped_result("no_recipient")

    results = []
    pending = []
    for recipient in recipients:
        existing = _existing_log(event, recipient)
        if existing is not None:
            results.append(_already_sent_result(recipient, existing))
        else:
            pending.append(recipient)

    if not pending:
        existing_ids = [item["email_log_id"] for item in results if item.get("email_log_id")]
        return _build_summary(results, existing_ids)

    return _submit_recording_ready_batch(event, pending, results)


def _submit_recording_ready_batch(event, pending, results):
    base_context = build_recording_ready_context(event)
    members = []
    subjects = []
    for recipient in pending:
        user = _render_user(recipient)
        ctx = recipient_template_context(user, base_context)
        subjects.append(relay_template_subject(EMAIL_TYPE, ctx))
        members.append(build_relay_recipient(recipient.email, _recipient_identity(recipient), ctx))

    external_key = f"event-recording-ready-{event.pk}"
    try:
        submit_event_notice_campaign(
            external_key=external_key,
            subject=subjects[0],
            template_key=EMAIL_TYPE,
            recipients=members,
            context=base_context,
            metadata={"event_id": event.pk, "email_type": EMAIL_TYPE},
        )
    except Exception as exc:
        logger.exception("Failed to submit %s for event %s", EMAIL_TYPE, event.pk)
        for recipient in pending:
            results.append({
                "email": recipient.email,
                "source": recipient.source,
                "status": "error",
                "reason": exc.__class__.__name__,
            })
        return _build_summary(results, [])

    email_log_ids = []
    for recipient, subject in zip(pending, subjects, strict=True):
        outcome = _record_sent(event, recipient, subject, external_key)
        results.append(outcome)
        if outcome.get("email_log_id"):
            email_log_ids.append(outcome["email_log_id"])
    return _build_summary(results, email_log_ids)


def _build_summary(results, email_log_ids):
    sent_count = sum(1 for item in results if item["status"] == "sent")
    error_count = sum(1 for item in results if item["status"] == "error")
    skipped_count = sum(1 for item in results if item["status"] == "skipped")

    if sent_count and error_count:
        status = "partial"
    elif sent_count:
        status = "sent"
    elif error_count:
        status = "error"
    else:
        status = "skipped"

    skipped_reason = ""
    if status == "skipped" and skipped_count == len(results):
        reasons = {item.get("reason", "") for item in results}
        skipped_reason = reasons.pop() if len(reasons) == 1 else "all_skipped"

    return {
        "status": status,
        "recipient_count": sent_count,
        "attempted_recipient_count": len(results),
        "skipped_reason": skipped_reason,
        "email_log_ids": email_log_ids,
        "results": results,
    }


def _record_sent(event, recipient, subject, external_key):
    from email_app.models import EmailLog

    dedupe_key = f"{EMAIL_TYPE}:{event.pk}:{_recipient_identity(recipient)}"
    with transaction.atomic():
        existing = _existing_log(event, recipient, for_update=True)
        if existing is not None:
            return _already_sent_result(recipient, existing)
        email_log = EmailLog.objects.create(
            event=event,
            user=recipient.user,
            recipient_email=recipient.email,
            email_type=EMAIL_TYPE,
            subject=subject,
            ses_message_id=external_key,
            dedupe_key=dedupe_key,
        )
    logger.info('Sent %s to %s for event "%s" (EmailLog %s, Relay %s)', EMAIL_TYPE, recipient.email, event.title, email_log.pk, external_key)
    return {"email": recipient.email, "source": recipient.source, "status": "sent", "email_log_id": email_log.pk}


def _already_sent_result(recipient, email_log):
    return {
        "email": recipient.email,
        "source": recipient.source,
        "status": "skipped",
        "reason": "already_sent",
        "email_log_id": email_log.pk,
    }


def _recipient_identity(recipient):
    if recipient.user is not None:
        return f"user-{recipient.user.pk}"
    digest = sha256(recipient.email.strip().lower().encode("utf-8")).hexdigest()
    return f"email-sha256-{digest}"


def _existing_log(event, recipient, *, for_update=False):
    from email_app.models import EmailLog

    query = Q(recipient_email__iexact=recipient.email)
    if recipient.user is not None:
        query |= Q(user=recipient.user)
    queryset = EmailLog.objects.filter(event=event, email_type=EMAIL_TYPE)
    if for_update:
        queryset = queryset.select_for_update()
    return queryset.filter(query).first()


def _render_user(recipient):
    if recipient.user is not None:
        return recipient.user
    return SimpleNamespace(
        email=recipient.email,
        first_name="",
        last_name="",
        email_verified=True,
    )


def _skipped_result(reason):
    return {
        "status": "skipped",
        "recipient_count": 0,
        "attempted_recipient_count": 0,
        "skipped_reason": reason,
        "email_log_ids": [],
        "results": [],
    }
