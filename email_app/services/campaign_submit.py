"""Submit a campaign to Relay as one request: content plus recipient list."""

import logging
from dataclasses import replace

from django.utils import timezone

from email_app.models import EmailCampaign, EmailLog
from email_app.services.campaign_message import prepare_campaign_message
from email_app.services.campaign_repermission import (
    REPERMISSION_COPY,
    REPERMISSION_EMAIL_TYPE,
    VERIFY_DEADLINE_CTA,
    VERIFY_DEADLINE_EMAIL_TYPE,
    confirmation_url_for_user,
    verify_deadline_url_for_user,
)
from email_app.services.relay_campaign_progress import (
    begin_native_queue,
    mark_native_queue_accepted,
    record_native_queue_failure,
)
from email_app.services.relay_campaigns import (
    RelayCampaignApiError,
    alert_relay_campaign_failure,
    get_relay_template_key,
    submit_native_campaign,
)

logger = logging.getLogger(__name__)


def campaign_external_key(campaign):
    return f"aishl-campaign-{campaign.pk}"


def _campaign_footer_note(campaign):
    if campaign.verify_deadline_days:
        return VERIFY_DEADLINE_CTA
    if campaign.audience_verification == EmailCampaign.AUDIENCE_VERIFICATION_UNVERIFIED_ONLY:
        return REPERMISSION_COPY
    return ""


def build_campaign_recipients(campaign):
    """Return ``(recipients, users)`` for the Relay submit; no local send state."""
    monitored = (
        campaign.audience_verification
        == EmailCampaign.AUDIENCE_VERIFICATION_UNVERIFIED_ONLY
    )
    email_type = "campaign"
    if monitored:
        email_type = (
            VERIFY_DEADLINE_EMAIL_TYPE
            if campaign.verify_deadline_days
            else REPERMISSION_EMAIL_TYPE
        )
    recipients = []
    users = []
    for user in campaign.get_eligible_recipients().order_by("pk"):
        prepared = prepare_campaign_message(
            user, campaign.subject, campaign.body, email_type=email_type,
        )
        if prepared.skip_reason:
            continue
        if monitored:
            url = (
                verify_deadline_url_for_user(user, campaign.verify_deadline_days)
                if campaign.verify_deadline_days
                else confirmation_url_for_user(user)
            )
            prepared = replace(prepared, verify_email_url=url)
        recipients.append({
            "source_object_key": f"website-user-{user.pk}",
            "email": user.email,
            "subscribe_if_missing": True,
            "metadata": {
                "relay_campaign_context": {
                    "verify_email_url": prepared.verify_email_url,
                    "unsubscribe_url": prepared.unsubscribe_url,
                    "client_recipient_key": str(user.pk),
                },
            },
        })
        users.append(user)
    return recipients, users


def _record_email_history(campaign, users):
    existing = set(
        EmailLog.objects.filter(campaign=campaign).values_list("user_id", flat=True)
    )
    EmailLog.objects.bulk_create(
        [
            EmailLog(
                campaign=campaign,
                user=user,
                recipient_email=user.email,
                email_type="campaign",
                subject=campaign.subject,
                ses_message_id="",
            )
            for user in users
            if user.pk not in existing
        ],
        batch_size=500,
        ignore_conflicts=True,
    )


def submit_campaign_to_relay(campaign, *, source="", actor=None):
    """Freeze nothing locally: build recipients, POST once, record the key."""
    key = campaign_external_key(campaign)
    begin_native_queue(campaign, key)
    try:
        recipients, users = build_campaign_recipients(campaign)
        if not recipients:
            campaign.status = "sent"
            campaign.sent_count = 0
            campaign.sent_at = timezone.now()
            campaign.relay_campaign_state = "sent"
            campaign.save(update_fields=[
                "status", "sent_count", "sent_at", "relay_campaign_state",
            ])
            return key
        result = submit_native_campaign(
            external_key=key,
            subject=campaign.subject,
            template_key=get_relay_template_key(),
            recipients=recipients,
            context={
                "subject": campaign.subject,
                "body_markdown": campaign.body,
                "footer_note": _campaign_footer_note(campaign),
                "verify_email_url": "",
                "unsubscribe_url": "",
            },
            allow_unverified=campaign.audience_verification != "verified_only",
            metadata={"website_campaign_id": campaign.pk},
        )
        if not isinstance(result, dict):
            raise RelayCampaignApiError("Relay submit returned no campaign response")
    except Exception as exc:
        record_native_queue_failure(campaign, exc)
        # Relay's per-campaign key is idempotent, so the operator may re-send.
        campaign.status = "draft"
        campaign.save(update_fields=["status"])
        alert_relay_campaign_failure(campaign, "submit", exc)
        logger.exception(
            "Relay campaign submit failed campaign=%s source=%s", campaign.pk, source,
        )
        raise
    _record_email_history(campaign, users)
    mark_native_queue_accepted(campaign, already_queued=False)
    logger.info(
        "Submitted campaign %s to Relay (%s recipients, source=%s)",
        campaign.pk, len(recipients), source,
    )
    return key
