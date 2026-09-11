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
