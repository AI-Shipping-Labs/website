"""Shared stop guard for already queued campaign and resume tasks."""

from email_app.models import EmailCampaign


class CampaignBatchBlocked(ValueError):
    """A queued task belongs to a campaign the operator has stopped."""


def load_sendable_campaign(campaign_id: int) -> EmailCampaign:
    try:
        campaign = EmailCampaign.objects.get(pk=campaign_id)
    except EmailCampaign.DoesNotExist as exc:
        raise ValueError(f"Campaign {campaign_id} not found") from exc
    if campaign.status in {"paused", "cancelled"}:
        raise CampaignBatchBlocked(f"Campaign {campaign_id} is {campaign.status}; dispatch blocked")
    return campaign
