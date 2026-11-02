"""Recipient context for explicit Studio campaign test sends."""

import uuid

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


def send_test_campaign_delivery(message, campaign_id):
    """Deliver one prepared test message through Relay v2.

    Shared transport for the Studio and API test-send surfaces. Each call
    gets a fresh idempotency key so repeated operator probes are never
    deduplicated away by Relay.
    """
    from email_app.services.relay_campaigns import send_campaign_delivery_to_relay

    idempotency_key = f"campaign-test-{campaign_id}-{message.recipient}-{uuid.uuid4().hex}"
    metadata = {
        "campaign_id": campaign_id,
        "is_test_send": True,
        "recipient": message.recipient,
    }
    return send_campaign_delivery_to_relay(
        recipient_email=message.recipient,
        subject=message.subject,
        body_markdown=message.body_markdown,
        idempotency_key=idempotency_key,
        unsubscribe_url=message.unsubscribe_url,
        verify_email_url=message.verify_email_url,
        footer_note=message.footer_note,
        metadata=metadata,
    )


def _repermission_message(campaign, user, subject):
    if user is None:
        raise CampaignTestRecipientError(
            "Re-permission test sends require an existing controlled user."
        )
    # A verify-or-delete test only previews the message and link, so an
    # already-verified operator account may receive it (the link is a no-op
    # for them). A re-permission test keeps requiring an unverified user.
    already_verified = user.email_verified and not campaign.verify_deadline_days
    if (
        not user.is_active or user.unsubscribed or already_verified
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
