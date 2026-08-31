"""Canonical recipient identity handling for inbound SES feedback."""

from dataclasses import dataclass
from email.utils import getaddresses

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from accounts.services.email_resolution import normalize_email, resolve_user_by_email
from email_app.models import SesEvent


@dataclass(frozen=True)
class RecipientIdentity:
    """One normalized SES recipient and the safe canonical-account match."""

    recipient_email: str
    user: object | None
    match_status: str
    detail: str = ""


@dataclass(frozen=True)
class EventIdentitySummary:
    """Identity information rendered by Studio and serialized by staff APIs."""

    user: object | None
    match_status: str
    match_label: str
    action_label: str
    historical_action: str = ""


_MATCH_LABELS = dict(SesEvent.MATCH_STATUS_CHOICES)
_FEEDBACK_EVENT_TYPES = {
    SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
    SesEvent.EVENT_TYPE_BOUNCE_TRANSIENT,
    SesEvent.EVENT_TYPE_BOUNCE_OTHER,
    SesEvent.EVENT_TYPE_COMPLAINT,
}


def parse_recipient_mailbox(raw_value):
    """Return one normalized mailbox from an RFC-style address or ``""``.

    Display names are accepted, while ambiguous lists and syntactically invalid
    values fail closed. The original value remains available only in the raw
    SNS payload.
    """
    value = str(raw_value or "").strip()
    if not value:
        return ""
    parsed = getaddresses([value])
    if len(parsed) != 1:
        return ""
    mailbox = normalize_email(parsed[0][1])
    if not mailbox:
        return ""
    try:
        validate_email(mailbox)
    except ValidationError:
        return ""
    return mailbox


def _user_addresses(user):
    if user is None:
        return set()
    return {
        address
        for address in (
            normalize_email(user.email),
            *(normalize_email(alias.email) for alias in user.email_aliases.all()),
        )
        if address
    }


def _send_log_agrees(email_log, recipient_email):
    if email_log is None or email_log.user_id is None or not recipient_email:
        return False
    snapshot = normalize_email(email_log.recipient_email)
    return (
        bool(snapshot and snapshot == recipient_email)
        or recipient_email in _user_addresses(email_log.user)
    )


def resolve_recipient_identity(raw_value, *, email_log=None):
    """Resolve one SES value through primary, alias, then safe send-log match."""
    recipient_email = parse_recipient_mailbox(raw_value)
    if not recipient_email:
        return RecipientIdentity(
            recipient_email="",
            user=None,
            match_status=SesEvent.MATCH_STATUS_UNMATCHED_RECIPIENT,
        )

    direct_user = resolve_user_by_email(recipient_email)
    log_user = email_log.user if email_log is not None else None
    if (
        direct_user is not None
        and log_user is not None
        and direct_user.pk != log_user.pk
    ):
        return RecipientIdentity(
            recipient_email=recipient_email,
            user=None,
            match_status=SesEvent.MATCH_STATUS_IDENTITY_CONFLICT,
            detail=(
                f"recipient user #{direct_user.pk} conflicts with "
                f"send-log user #{log_user.pk}"
            ),
        )

    if direct_user is not None:
        match_status = (
            SesEvent.MATCH_STATUS_PRIMARY_EMAIL
            if normalize_email(direct_user.email) == recipient_email
            else SesEvent.MATCH_STATUS_EMAIL_ALIAS
        )
        return RecipientIdentity(recipient_email, direct_user, match_status)

    if _send_log_agrees(email_log, recipient_email):
        return RecipientIdentity(
            recipient_email,
            log_user,
            SesEvent.MATCH_STATUS_EMAIL_LOG,
        )

    if log_user is not None:
        return RecipientIdentity(
            recipient_email,
            None,
            SesEvent.MATCH_STATUS_UNMATCHED_RECIPIENT,
            detail="send-log user does not agree with recipient mailbox",
        )

    return RecipientIdentity(
        recipient_email,
        None,
        SesEvent.MATCH_STATUS_NO_PLATFORM_ACCOUNT,
    )


def _legacy_no_match_action(event):
    return "no matching user" in (event.action_taken or "").lower()


def _historical_user(event, recipient_email):
    if event.user_id is not None:
        return event.user
    if _legacy_no_match_action(event) and _send_log_agrees(
        event.email_log, recipient_email,
    ):
        return event.email_log.user
    return None


def event_identity_summary(event):
    """Return stable identity/action copy, including historical contradictions."""
    if event.event_type not in _FEEDBACK_EVENT_TYPES and not event.match_status:
        return EventIdentitySummary(None, "", "Not evaluated", event.action_taken or "")

    recipient_email = parse_recipient_mailbox(event.recipient_email)
    historical_user = _historical_user(event, recipient_email)
    if _legacy_no_match_action(event) and historical_user is not None:
        status = SesEvent.MATCH_STATUS_NEEDS_RECONCILIATION
        return EventIdentitySummary(
            historical_user,
            status,
            _MATCH_LABELS[status],
            "Needs reconciliation",
            event.action_taken,
        )

    status = event.match_status
    user = event.user if event.user_id is not None else None
    if not status:
        if user is not None:
            if recipient_email == normalize_email(user.email):
                status = SesEvent.MATCH_STATUS_PRIMARY_EMAIL
            elif event.email_log_id and event.email_log.user_id == user.pk:
                status = SesEvent.MATCH_STATUS_EMAIL_LOG
            else:
                status = SesEvent.MATCH_STATUS_EMAIL_ALIAS
        elif recipient_email:
            status = SesEvent.MATCH_STATUS_NO_PLATFORM_ACCOUNT
        else:
            status = SesEvent.MATCH_STATUS_UNMATCHED_RECIPIENT

    action = event.action_taken or ""
    if status in {
        SesEvent.MATCH_STATUS_IDENTITY_CONFLICT,
        SesEvent.MATCH_STATUS_NEEDS_RECONCILIATION,
    }:
        action = "Needs reconciliation"
    elif status in {
        SesEvent.MATCH_STATUS_NO_PLATFORM_ACCOUNT,
        SesEvent.MATCH_STATUS_UNMATCHED_RECIPIENT,
    } and (_legacy_no_match_action(event) or not action):
        action = "No account action taken"

    return EventIdentitySummary(
        user,
        status,
        _MATCH_LABELS.get(status, "Not evaluated"),
        action,
    )
