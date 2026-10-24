"""Shared recipient rendering for explicit Studio campaign test sends."""

from accounts.models import User
from email_app.models import EmailCampaign
from email_app.services.campaign_repermission import (
    REPERMISSION_EMAIL_TYPE,
    repermission_body,
)

TEST_EMAIL_FOOTER_NOTE = (
    "Test send only. This address is not linked to a subscriber record, so no unsubscribe link is included."
)


class CampaignTestRecipientError(ValueError):
    """A controlled recipient does not meet a re-permission test's policy."""


def _repermission_parts(service, campaign, user, subject):
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
    prepared = service.prepare_rendered(
        user, subject, repermission_body(campaign.body, user=user),
        email_type=REPERMISSION_EMAIL_TYPE, campaign_id=campaign.pk,
    )
    return prepared.full_html, prepared.plain_text, prepared.unsubscribe_url


def _test_parts(service, campaign, user, subject):
    if campaign.audience_verification == EmailCampaign.AUDIENCE_VERIFICATION_UNVERIFIED_ONLY:
        return _repermission_parts(service, campaign, user, subject)
    unsubscribe_url = None
    footer_note = TEST_EMAIL_FOOTER_NOTE
    if user is not None:
        unsubscribe_url = service._build_unsubscribe_url(user)
        footer_note = None
    html = service.render_markdown_email(
        subject, campaign.body, unsubscribe_url=unsubscribe_url, footer_note=footer_note,
    )
    text = service.render_plain_text_email(
        campaign.body, unsubscribe_url=unsubscribe_url, footer_note=footer_note,
    )
    return html, text, unsubscribe_url


def send_campaign_test_recipient(campaign, recipient, *, service, send):
    """Render exactly one explicit test recipient without changing the campaign."""
    user = User.objects.filter(email__iexact=recipient).first()
    subject = f"[TEST] {campaign.subject}"
    html, text, unsubscribe_url = _test_parts(service, campaign, user, subject)
    return send(
        recipient=recipient, subject=subject, html_body=html, text_body=text,
        campaign_id=campaign.pk, unsubscribe_url=unsubscribe_url,
    )
