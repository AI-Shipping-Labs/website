"""Loud operator alerts for unalerted needs_attention and stuck campaigns.

``relay_campaign_watchdog`` tracks native Relay campaigns via
``relay_campaign_state``. Legacy, transactional-fallback, or wave-blocked
campaigns can enter ``status='needs_attention'`` (or stall in
``status='sending'``) with ``relay_campaign_state`` empty or ``not_queued``,
which previously bypassed the watchdog without raising any alert.
"""

import logging
from datetime import timedelta

from django.db.models import Count

from email_app.models import CampaignDelivery, EmailCampaign
from email_app.services.relay_campaign_alerts import alert_relay_campaign_failure
from email_app.services.relay_campaigns import RelayCampaignApiError

logger = logging.getLogger(__name__)

_NATIVE_WATCHED_STATES = ("pending", "queued", *EmailCampaign.RELAY_ATTENTION_STATES)
_ACTIVE_DELIVERY_STATES = (
    CampaignDelivery.State.PENDING,
    CampaignDelivery.State.DISPATCHING,
)


def _delivery_summary(campaign):
    rows = campaign.deliveries.values("state").annotate(count=Count("id"))
    counts = {}
    for row in rows:
        counts[row["state"]] = row["count"]
    parts = []
    for state in ("ambiguous", "failed", "pending", "dispatching", "sent"):
        value = counts.get(state, 0)
        if value:
            parts.append(f"{state}={value}")
    if parts:
        return ", ".join(parts)
    return "no deliveries recorded"


def _attention_message(campaign):
    if campaign.relay_campaign_error:
        return campaign.relay_campaign_error
    summary = _delivery_summary(campaign)
    return f"Campaign status is needs_attention ({summary})."


def _alert_once(campaign, operation, message, now):
    accepted = alert_relay_campaign_failure(
        campaign,
        operation,
        RelayCampaignApiError(message),
        bypass_cooldown=True,
    )
    if accepted:
        EmailCampaign.objects.filter(
            pk=campaign.pk,
            relay_campaign_attention_alerted_at__isnull=True,
        ).update(relay_campaign_attention_alerted_at=now)
    return accepted


def _check_unalerted_needs_attention(now):
    campaigns = list(
        EmailCampaign.objects.filter(
            is_archived=False,
            status="needs_attention",
            relay_campaign_attention_alerted_at__isnull=True,
        )
        .exclude(relay_campaign_state__in=_NATIVE_WATCHED_STATES)
        .order_by("pk")
    )
    failures = 0
    for campaign in campaigns:
        try:
            _alert_once(campaign, "needs_attention", _attention_message(campaign), now)
        except Exception:
            # Broad on purpose: one campaign alert failure must not block others.
            logger.exception("Needs-attention alert failed campaign=%s", campaign.pk)
            failures += 1
    return len(campaigns), failures


def _has_active_work(campaign):
    if campaign.audience_snapshotted_at is None:
        return True
    return campaign.deliveries.filter(state__in=_ACTIVE_DELIVERY_STATES).exists()


def _check_stuck_sending_one(campaign, now, stall_minutes):
    if not _has_active_work(campaign):
        return False
    cutoff = now - timedelta(minutes=stall_minutes)
    recent_progress = campaign.deliveries.filter(updated_at__gt=cutoff).exists()
    if recent_progress or campaign.relay_campaign_progress_at is None:
        EmailCampaign.objects.filter(pk=campaign.pk).update(
            relay_campaign_progress_at=now,
        )
        return False
    if campaign.relay_campaign_progress_at > cutoff:
        return False
    message = (
        f"Campaign stuck in sending for {stall_minutes} minutes with no delivery "
        f"progress ({_delivery_summary(campaign)})."
    )
    _alert_once(campaign, "stuck sending", message, now)
    return True


def _check_stuck_sending(now, stall_minutes):
    campaigns = list(
        EmailCampaign.objects.filter(
            is_archived=False,
            status="sending",
            relay_campaign_state__in=("", "not_queued"),
            relay_campaign_attention_alerted_at__isnull=True,
        ).order_by("pk")
    )
    checked = 0
    failures = 0
    for campaign in campaigns:
        try:
            if _check_stuck_sending_one(campaign, now, stall_minutes):
                checked += 1
        except Exception:
            # Broad on purpose: one campaign check failure must not block others.
            logger.exception("Stuck-sending check failed campaign=%s", campaign.pk)
            failures += 1
    return checked, failures


def alert_unalerted_attention_and_stuck_campaigns(*, now, stall_minutes):
    """Alert once on unalerted needs_attention and stuck sending campaigns."""
    att_checked, att_failed = _check_unalerted_needs_attention(now)
    stuck_checked, stuck_failed = _check_stuck_sending(now, stall_minutes)
    return {
        "checked": att_checked + stuck_checked,
        "failed": att_failed + stuck_failed,
    }
