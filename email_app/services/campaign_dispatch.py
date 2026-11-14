"""Claim a draft campaign and hand it to Relay in one request.

AISL does not send, pace, queue or track recipients. It submits the campaign
(content plus recipient list) to Relay and reads status back from Relay's
campaign status API.
"""

from dataclasses import dataclass

from django.db import transaction

from email_app.models import EmailCampaign


@dataclass(frozen=True)
class CampaignClaimResult:
    claimed: bool
    campaign: EmailCampaign
    relay_campaign_key: str | None = None


def _campaign_can_be_claimed(campaign, scheduled_due_by):
    if campaign.status != 'draft':
        return False
    if scheduled_due_by is None:
        return True
    if campaign.scheduled_at is None:
        return False
    return campaign.scheduled_at <= scheduled_due_by


def _mark_campaign_sending(campaign):
    campaign.status = 'sending'
    campaign.sent_count = 0
    campaign.sent_at = None
    # Claiming consumes the schedule. Manual send beats the dispatcher.
    campaign.scheduled_at = None
    campaign.scheduled_timezone = None
    campaign.save(update_fields=[
        'status', 'sent_count', 'sent_at', 'scheduled_at',
        'scheduled_timezone',
    ])


def claim_and_submit_campaign(
    campaign_id, *, source, actor=None, scheduled_due_by=None,
):
    """Claim a draft exactly once, then submit it to Relay in one request.

    ``source`` and ``actor`` are kept for caller attribution in logs.
    A failed submit puts the draft back with the error stored and alerted;
    Relay's per-campaign key makes re-submitting safe.
    """
    from email_app.services.campaign_submit import submit_campaign_to_relay

    with transaction.atomic():
        campaign = EmailCampaign.objects.select_for_update().get(pk=campaign_id)
        if not _campaign_can_be_claimed(campaign, scheduled_due_by):
            return CampaignClaimResult(False, campaign)
        _mark_campaign_sending(campaign)
    key = submit_campaign_to_relay(campaign, source=source, actor=actor)
    return CampaignClaimResult(True, campaign, key)
