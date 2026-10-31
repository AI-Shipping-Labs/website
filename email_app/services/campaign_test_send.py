"""Recipient context for explicit Studio campaign test sends."""

from accounts.models import User
from email_app.models import EmailCampaign
from email_app.services.campaign_message import (
    CampaignMessage,
    prepare_campaign_message,
)
from email_app.services.campaign_repermission import monitored_message_parts

TEST_EMAIL_FOOTER_NOTE = (
    "Test send only. This address is not linked to a subscriber record, "
    "so no unsubscribe link is included."
)


class CampaignTestRecipientError(ValueError):
    """A controlled recipient does not meet a re-permission test's policy."""


def _repermission_message(campaign, user, subject):
    if user is None:
        raise CampaignTestRecipientError(
            "Re-permission test sends require an existing controlled user."
        )
    if (
        not user.is_active or user.unsubscribed or user.email_verified
        or user.bounce_state == User.BounceState.PERMANENT
    ):
        raise CampaignTestRecipientError(
            "Re-permission test sends require an active subscribed user."
        )
    body, email_type = monitored_message_parts(campaign, user=user)
    return prepare_campaign_message(
        user, subject, body, email_type=email_type,
    )


def _test_message(campaign, user, recipient, subject):
    if campaign.audience_verification == EmailCampaign.AUDIENCE_VERIFICATION_UNVERIFIED_ONLY:
        return _repermission_message(campaign, user, subject)
    if user is not None:
        message = prepare_campaign_message(
            user, subject, campaign.body, email_type="campaign",
        )
        if message.skip_reason is None:
            return message
        # An explicit operator test is allowed to target a controlled
        # unsubscribed account. Keep it detached from subscription actions so
        # the probe neither advertises nor exercises an unsubscribe URL.
        return CampaignMessage(
            recipient=recipient,
            subject=subject,
            body_markdown=campaign.body,
        )
    return CampaignMessage(
        recipient=recipient,
        subject=subject,
        body_markdown=campaign.body,
        footer_note=TEST_EMAIL_FOOTER_NOTE,
    )


def send_campaign_test_recipient(campaign, recipient, *, send):
    """Build and send one explicit test recipient without changing the draft."""
    user = User.objects.filter(email__iexact=recipient).first()
    message = _test_message(campaign, user, recipient, f"[TEST] {campaign.subject}")
    return send(message=message, campaign_id=campaign.pk)
