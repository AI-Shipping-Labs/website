"""Consent-bearing message content for monitored re-permission campaigns."""

from accounts.utils.tokens import generate_user_action_token
from integrations.config import site_base_url

REPERMISSION_EMAIL_TYPE = "campaign_repermission"
REPERMISSION_TOKEN_EXPIRY_HOURS = 24 * 30
REPERMISSION_COPY = (
    "To keep receiving AI Shipping Labs newsletters and other marketing emails, "
    "confirm your email within 30 days. If you do nothing, marketing emails will "
    "stop. Essential account, billing, and course emails are not affected."
)
REPERMISSION_CTA = "Confirm email and keep me subscribed"
PREVIEW_CONFIRMATION_URL = "https://aishippinglabs.com/api/verify-and-subscribe?token=preview-only"


def confirmation_url_for_user(user):
    token = generate_user_action_token(
        user.pk,
        "verify_and_subscribe",
        expiry_hours=REPERMISSION_TOKEN_EXPIRY_HOURS,
    )
    return f"{site_base_url().rstrip('/')}/api/verify-and-subscribe?token={token}"


def repermission_body(body_markdown, *, user=None, preview=False):
    """Append the fixed consent block and its sole verification action."""
    if user is None and not preview:
        raise ValueError("A persisted user is required for a re-permission message.")
    confirmation_url = (
        PREVIEW_CONFIRMATION_URL if preview else confirmation_url_for_user(user)
    )
    authored = (body_markdown or "").rstrip()
    consent = f"{REPERMISSION_COPY}\n\n[{REPERMISSION_CTA}]({confirmation_url})"
    return f"{authored}\n\n---\n\n{consent}" if authored else consent


# Verify-or-delete campaigns (``EmailCampaign.verify_deadline_days``): the
# message promises deletion, so it carries a plain ``verify_email`` link (a
# verification, not a marketing opt-in) whose token outlives the deadline.
VERIFY_DEADLINE_EMAIL_TYPE = "campaign_verify_deadline"
VERIFY_DEADLINE_TOKEN_GRACE_DAYS = 7
VERIFY_DEADLINE_CTA = "Confirm my email"


def verify_deadline_copy(days):
    return (
        f"Please confirm your email within {days} days. If you don't, your "
        f"AI Shipping Labs account will be deleted {days} days after this "
        "message was sent. One click is all it takes, and nothing changes "
        "for you after that."
    )


def verify_deadline_url_for_user(user, days):
    token = generate_user_action_token(
        user.pk,
        "verify_email",
        expiry_hours=24 * (days + VERIFY_DEADLINE_TOKEN_GRACE_DAYS),
    )
    return f"{site_base_url().rstrip('/')}/api/verify-email?token={token}"


def verify_deadline_body(body_markdown, days, *, user=None, preview=False):
    if user is None and not preview:
        raise ValueError("A persisted user is required for a verify message.")
    url = (
        "https://aishippinglabs.com/api/verify-email?token=preview-only"
        if preview
        else verify_deadline_url_for_user(user, days)
    )
    authored = (body_markdown or "").rstrip()
    block = f"{verify_deadline_copy(days)}\n\n[{VERIFY_DEADLINE_CTA}]({url})"
    return f"{authored}\n\n---\n\n{block}" if authored else block


def monitored_message_parts(campaign, *, user=None, preview=False):
    """Return ``(body_markdown, email_type)`` for an ``unverified_only`` send."""
    days = campaign.verify_deadline_days
    if days:
        return (
            verify_deadline_body(campaign.body, days, user=user, preview=preview),
            VERIFY_DEADLINE_EMAIL_TYPE,
        )
    return (
        repermission_body(campaign.body, user=user, preview=preview),
        REPERMISSION_EMAIL_TYPE,
    )
