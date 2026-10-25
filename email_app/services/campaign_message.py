"""Recipient policy and Relay context for operator-authored campaigns."""

from dataclasses import dataclass

from accounts.utils.tokens import generate_user_action_token
from email_app.services.email_classification import (
    EMAIL_KIND_PROMOTIONAL,
    classify_email_type,
)
from email_app.services.footer_context import (
    EMAIL_TYPES_WITHOUT_VERIFY_FOOTER,
    VERIFY_FOOTER_TOKEN_EXPIRY_HOURS,
)
from integrations.config import site_base_url

UNSUBSCRIBED_AT_SEND = "unsubscribed_at_send"


@dataclass(frozen=True, slots=True)
class CampaignMessage:
    recipient: str = ""
    subject: str = ""
    body_markdown: str = ""
    footer_note: str = ""
    unsubscribe_url: str = ""
    verify_email_url: str = ""
    skip_reason: str = ""


def prepare_campaign_message(user, subject, body_markdown, *, email_type, footer_note=""):
    """Resolve consent and footer actions without rendering campaign copy."""
    if getattr(user, "pk", None):
        user.refresh_from_db()
    kind = classify_email_type(email_type)
    if kind == EMAIL_KIND_PROMOTIONAL and getattr(user, "unsubscribed", False):
        return CampaignMessage(skip_reason=UNSUBSCRIBED_AT_SEND)
    unsubscribe_url = ""
    if kind == EMAIL_KIND_PROMOTIONAL:
        unsubscribe_url = _unsubscribe_url(user)
    verify_email_url = ""
    if _needs_verify_footer(user, email_type):
        verify_email_url = _verify_email_url(user)
    return CampaignMessage(
        recipient=user.email,
        subject=subject,
        body_markdown=body_markdown,
        footer_note=footer_note or "",
        unsubscribe_url=unsubscribe_url,
        verify_email_url=verify_email_url,
    )


def campaign_relay_context(message):
    return {
        "subject": message.subject,
        "body_markdown": message.body_markdown,
        "footer_note": message.footer_note,
        "verify_email_url": message.verify_email_url,
        "unsubscribe_url": message.unsubscribe_url,
    }


def _unsubscribe_url(user):
    token = generate_user_action_token(user.pk, "unsubscribe")
    return f"{site_base_url()}/api/unsubscribe?token={token}"


def _needs_verify_footer(user, email_type):
    return (
        not getattr(user, "email_verified", True)
        and email_type not in EMAIL_TYPES_WITHOUT_VERIFY_FOOTER
    )


def _verify_email_url(user):
    from accounts.views.auth import _generate_verification_token  # noqa: PLC0415

    token = _generate_verification_token(
        user.pk,
        expiry_hours=VERIFY_FOOTER_TOKEN_EXPIRY_HOURS,
    )
    return f"{site_base_url()}/api/verify-email?token={token}"
