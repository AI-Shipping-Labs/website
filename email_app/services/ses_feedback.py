"""Atomic persistence for inbound SES bounce and complaint feedback."""

import logging
from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from accounts.utils.bounce import (
    SOFT_BOUNCE_THRESHOLD,
    mark_permanent_bounce,
    record_soft_bounce,
)
from email_app.models import EmailLog, SesEvent
from email_app.services.ses_identity import resolve_recipient_identity

logger = logging.getLogger(__name__)
User = get_user_model()


@dataclass(frozen=True)
class FeedbackEvent:
    """Parsed fields shared by bounce and complaint persistence."""

    message_id: str
    event_type: str
    raw_payload: dict
    addresses: tuple[str, ...]
    email_log: EmailLog | None
    diagnostic: str = ""
    bounce_type: str = ""
    bounce_subtype: str = ""


def process_bounce_event(
    event,
    *,
    event_timestamp,
    permanent_override=False,
    override_status="",
):
    """Claim and apply one bounce, returning false for a duplicate claim."""
    try:
        with transaction.atomic():
            claim = _claim_event(event)
            identities = _resolve_identities(event)
            locked_users = _lock_users(identities)
            locked_log = _lock_email_log(event.email_log)
            actions = _bounce_actions(
                identities,
                locked_users,
                event,
                permanent_override,
                override_status,
            )
            if locked_log is not None:
                _stamp_email_log_bounce(locked_log, event, event_timestamp)
            _finalize_claim(claim, identities, locked_users, locked_log, actions)
        return True
    except IntegrityError as error:
        return _duplicate_or_raise(event.message_id, error)


def process_complaint_event(event, *, event_timestamp):
    """Claim and apply one complaint, returning false for a duplicate claim."""
    try:
        with transaction.atomic():
            claim = _claim_event(event)
            identities = _resolve_identities(event)
            locked_users = _lock_users(identities)
            locked_log = _lock_email_log(event.email_log)
            actions = _complaint_actions(identities, locked_users)
            if locked_log is not None:
                _stamp_email_log_complaint(locked_log, event_timestamp)
            _finalize_claim(claim, identities, locked_users, locked_log, actions)
        return True
    except IntegrityError as error:
        return _duplicate_or_raise(event.message_id, error)


def _claim_event(event):
    """Acquire database-backed ownership before any related mutation."""
    return SesEvent.objects.create(
        message_id=event.message_id,
        event_type=event.event_type,
        raw_payload=event.raw_payload,
        recipient_email="",
        action_taken="",
        bounce_type=event.bounce_type,
        bounce_subtype=event.bounce_subtype,
        diagnostic_code=event.diagnostic,
    )


def _duplicate_or_raise(message_id, error):
    if not SesEvent.objects.filter(message_id=message_id).exists():
        raise error
    logger.info("Duplicate SesEvent claim for MessageId=%s", message_id)
    return False


def _resolve_identities(event):
    return [resolve_recipient_identity(address, email_log=event.email_log) for address in event.addresses]


def _lock_users(identities):
    user_ids = set()
    for identity in identities:
        if identity.user is not None:
            user_ids.add(identity.user.pk)
    locked = User.objects.select_for_update().filter(pk__in=sorted(user_ids)).order_by("pk")
    return {user.pk: user for user in locked}


def _lock_email_log(email_log):
    if email_log is None:
        return None
    return EmailLog.objects.select_for_update().get(pk=email_log.pk)


def _bounce_actions(
    identities,
    locked_users,
    event,
    permanent_override,
    override_status,
):
    actions = []
    mutated_user_ids = set()
    for identity in identities:
        if identity.user is None:
            actions.append(_unmatched_action(identity))
            continue
        if identity.user.pk in mutated_user_ids:
            continue
        mutated_user_ids.add(identity.user.pk)
        actions.append(
            _bounce_user_action(
                locked_users[identity.user.pk],
                event,
                permanent_override,
                override_status,
            )
        )
    return actions


def _bounce_user_action(user, event, permanent_override, override_status):
    if permanent_override:
        mark_permanent_bounce(user, diagnostic=event.diagnostic)
        return f"SES mislabeled Transient->permanent (status={override_status}); unsubscribed after permanent bounce"
    if event.bounce_type == "Permanent":
        mark_permanent_bounce(user, diagnostic=event.diagnostic)
        return "Unsubscribed after permanent bounce"
    if event.bounce_type != "Transient":
        return "No account action taken"
    new_count, flipped = record_soft_bounce(user, diagnostic=event.diagnostic)
    if flipped:
        return f"Unsubscribed after {SOFT_BOUNCE_THRESHOLD} soft bounces"
    return f"Soft bounce recorded ({new_count} of {SOFT_BOUNCE_THRESHOLD})"


def _complaint_actions(identities, locked_users):
    actions = []
    mutated_user_ids = set()
    for identity in identities:
        if identity.user is None:
            actions.append(_unmatched_action(identity))
            continue
        if identity.user.pk in mutated_user_ids:
            continue
        mutated_user_ids.add(identity.user.pk)
        _mark_complaint(locked_users[identity.user.pk])
        actions.append("Unsubscribed after complaint")
    return actions


def _unmatched_action(identity):
    if identity.match_status == SesEvent.MATCH_STATUS_IDENTITY_CONFLICT:
        return f"Needs reconciliation: {identity.detail}"
    return "No account action taken"


def _finalize_claim(claim, identities, locked_users, email_log, actions):
    recipient_email = ""
    user = None
    match_status = SesEvent.MATCH_STATUS_UNMATCHED_RECIPIENT
    if identities:
        first = identities[0]
        recipient_email = first.recipient_email
        match_status = first.match_status
        if first.user is not None:
            user = locked_users[first.user.pk]
    action_taken = "; ".join(actions) or "no recipients in payload; logged only"
    claim.recipient_email = recipient_email
    claim.user = user
    claim.match_status = match_status
    claim.action_taken = action_taken[:255]
    claim.email_log = email_log
    claim.save(
        update_fields=[
            "recipient_email",
            "user",
            "match_status",
            "action_taken",
            "email_log",
        ]
    )


def _stamp_email_log_bounce(email_log, event, event_timestamp):
    update_fields = []
    if email_log.bounced_at is None:
        email_log.bounced_at = event_timestamp
        update_fields.append("bounced_at")
    for field, value in (
        ("bounce_type", event.bounce_type),
        ("bounce_subtype", event.bounce_subtype),
    ):
        if getattr(email_log, field) != value:
            setattr(email_log, field, value)
            update_fields.append(field)
    if event.diagnostic and email_log.bounce_diagnostic != event.diagnostic:
        email_log.bounce_diagnostic = event.diagnostic
        update_fields.append("bounce_diagnostic")
    if update_fields:
        email_log.save(update_fields=update_fields)


def _stamp_email_log_complaint(email_log, event_timestamp):
    if email_log.complained_at is None:
        email_log.complained_at = event_timestamp
        email_log.save(update_fields=["complained_at"])


def _mark_complaint(user):
    if not user.unsubscribed:
        user.unsubscribed = True
        user.save(update_fields=["unsubscribed"])
