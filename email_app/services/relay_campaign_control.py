"""Stop and reconcile native Relay campaigns against Relay's recipient ledger."""

from __future__ import annotations

from dataclasses import dataclass

import requests
from django.db import transaction
from django.utils import timezone

from email_app.models import CampaignDelivery, EmailCampaign, EmailLog
from email_app.services.relay_campaign_progress import (
    campaign_relay_keys,
    read_campaign_relay_progress,
)
from email_app.services.relay_campaigns import (
    RELAY_CAMPAIGN_AUDIENCE,
    RELAY_CAMPAIGN_CLIENT,
    _campaign_api_request,
    get_native_campaign_stats,
)

_RECONCILABLE_RELAY_STATUSES = frozenset({
    "queued", "snapshotting", "sending", "sent", "cancelled", "failed",
})
_HALTABLE_RELAY_STATUSES = frozenset({"queued", "snapshotting", "sending"})


class CampaignStopConflict(Exception):
    """Raised when a campaign cannot be stopped in its current state."""

    def __init__(self, message: str, *, code: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class CampaignStopResult:
    campaign: EmailCampaign
    stopped_keys: tuple[str, ...]
    sent_count: int
    pending_count: int


def stop_native_campaign(external_key: str, *, session=None) -> dict:
    """Ask Relay to permanently halt a queued or sending campaign."""
    scope = {"audience": RELAY_CAMPAIGN_AUDIENCE, "client": RELAY_CAMPAIGN_CLIENT}
    return _campaign_api_request(
        "POST",
        f"/api/campaigns/{external_key}/stop",
        payload=scope,
        session=session,
    )


def _statuses_for_key(key: str, scope: dict, session: requests.Session) -> tuple[dict[str, str], set[str]]:
    existing = _campaign_api_request(
        "GET",
        f"/api/campaigns/{key}",
        params=scope,
        session=session,
        allow_not_found=True,
    )
    if not existing or not isinstance(existing.get("campaign"), dict):
        return {}, set()
    if existing["campaign"].get("status") not in _RECONCILABLE_RELAY_STATUSES:
        return {}, set()
    rec_resp = _campaign_api_request(
        "GET",
        f"/api/campaigns/{key}/recipients",
        params=scope,
        session=session,
    )
    sent: dict[str, str] = {}
    skipped: set[str] = set()
    for row in rec_resp.get("recipients") or []:
        if not isinstance(row, dict) or not row.get("client_recipient_key"):
            continue
        rkey = str(row["client_recipient_key"])
        if row.get("ses_message_id"):
            sent[rkey] = str(row["ses_message_id"])
        elif row.get("status") == "skipped":
            skipped.add(rkey)
    return sent, skipped


def fetch_relay_recipient_statuses(
    campaign: EmailCampaign, *, session=None,
) -> tuple[dict[str, str], set[str]]:
    """Return provider IDs by delivery key and skipped keys across Relay waves."""
    scope = {"audience": RELAY_CAMPAIGN_AUDIENCE, "client": RELAY_CAMPAIGN_CLIENT}
    if session is None:
        session = requests.Session()
    sent_keys: dict[str, str] = {}
    skipped_keys: set[str] = set()
    for key in campaign_relay_keys(campaign):
        key_sent, key_skipped = _statuses_for_key(key, scope, session)
        sent_keys.update(key_sent)
        skipped_keys.update(key_skipped)
    skipped_keys.difference_update(sent_keys)
    return sent_keys, skipped_keys


def _mark_delivery_sent(campaign: EmailCampaign, delivery: CampaignDelivery, now, provider_id: str) -> None:
    log, _created = EmailLog.objects.get_or_create(
        campaign=campaign,
        user_id=delivery.recipient_user_pk,
        defaults={
            "recipient_email": delivery.recipient_email,
            "email_type": "campaign",
            "subject": campaign.subject,
            "ses_message_id": provider_id,
        },
    )
    if log.ses_message_id != provider_id:
        log.ses_message_id = provider_id
        log.save(update_fields=["ses_message_id"])
    delivery.state = CampaignDelivery.State.SENT
    delivery.email_log = log
    delivery.ses_message_id = provider_id
    delivery.resolution = ""
    delivery.resolved_at = None
    if delivery.completed_at is None:
        delivery.completed_at = now
    delivery.save(update_fields=[
        "state", "email_log", "ses_message_id", "resolution", "resolved_at", "completed_at", "updated_at",
    ])


def _revert_delivery_to_pending(delivery: CampaignDelivery) -> None:
    orphan_log = delivery.email_log
    delivery.state = CampaignDelivery.State.PENDING
    delivery.email_log = None
    delivery.completed_at = None
    delivery.save(update_fields=["state", "email_log", "completed_at", "updated_at"])
    if orphan_log is not None and not orphan_log.ses_message_id:
        orphan_log.delete()


def _reconcile_one_delivery(
    campaign: EmailCampaign,
    delivery: CampaignDelivery,
    sent_keys: dict[str, str],
    skipped_keys: set[str],
    revert_unconfirmed: bool,
    now,
) -> str | None:
    rkey = str(delivery.pk)
    if rkey in sent_keys:
        _mark_delivery_sent(campaign, delivery, now, sent_keys[rkey])
        return "sent"
    if rkey in skipped_keys:
        if delivery.state == CampaignDelivery.State.PENDING or _is_unconfirmed(delivery):
            _revert_delivery_to_pending(delivery)
            delivery.state = CampaignDelivery.State.SKIPPED
            delivery.skip_reason = delivery.skip_reason or "skipped_at_relay"
            delivery.completed_at = now
            delivery.save(update_fields=["state", "skip_reason", "completed_at", "updated_at"])
            return "skipped"
        return None
    if revert_unconfirmed and _is_unconfirmed(delivery):
        _revert_delivery_to_pending(delivery)
        return "reverted"
    return None


def reconcile_native_campaign_deliveries(
    campaign: EmailCampaign, *, revert_unconfirmed: bool = False, session=None,
) -> dict[str, int]:
    """Align local ``CampaignDelivery`` rows with Relay's recipient ledger."""
    sent_keys, skipped_keys = fetch_relay_recipient_statuses(campaign, session=session)
    now = timezone.now()
    counts = {"sent": 0, "skipped": 0, "reverted": 0}
    with transaction.atomic():
        deliveries = campaign.deliveries.select_for_update(of=("self",)).select_related("email_log")
        for delivery in deliveries:
            outcome = _reconcile_one_delivery(
                campaign, delivery, sent_keys, skipped_keys, revert_unconfirmed, now,
            )
            if outcome:
                counts[outcome] += 1
    return counts


def _is_unconfirmed(delivery: CampaignDelivery) -> bool:
    if delivery.ses_message_id:
        return False
    if delivery.state == CampaignDelivery.State.DISPATCHING:
        return True
    if delivery.state == CampaignDelivery.State.SENT:
        return delivery.email_log_id is None or not delivery.email_log.ses_message_id
    return False


def _halt_active_relay_keys(keys: list[str], *, session: requests.Session) -> list[str]:
    stopped_keys: list[str] = []
    for key in keys:
        native = get_native_campaign_stats(key, session=session, allow_not_found=True)
        if native and native.get("status") in _HALTABLE_RELAY_STATUSES:
            stop_native_campaign(key, session=session)
            stopped_keys.append(key)
    return stopped_keys


def _halt_and_reconcile(
    campaign: EmailCampaign, *, target_status: str, label: str, session: requests.Session,
) -> CampaignStopResult:
    keys = campaign_relay_keys(campaign)
    stopped_keys: list[str] = []
    if keys:
        stopped_keys = _halt_active_relay_keys(keys, session=session)
        read_campaign_relay_progress(campaign, session=session)
        reconcile_native_campaign_deliveries(
            campaign, revert_unconfirmed=True, session=session,
        )
        campaign.refresh_from_db()
        campaign.relay_campaign_state = "cancelled"
    if stopped_keys:
        message = f"{label} via AISHL ({', '.join(stopped_keys)})."
    else:
        message = f"{label} via AISHL."
    campaign.status = target_status
    campaign.relay_campaign_error = message
    campaign.sent_at = None
    campaign.scheduled_at = None
    campaign.scheduled_timezone = ""
    campaign.save(update_fields=[
        "status",
        "relay_campaign_state",
        "relay_campaign_error",
        "sent_at",
        "scheduled_at",
        "scheduled_timezone",
    ])
    pending_count = campaign.deliveries.filter(state=CampaignDelivery.State.PENDING).count()
    return CampaignStopResult(
        campaign=campaign,
        stopped_keys=tuple(stopped_keys),
        sent_count=campaign.sent_count,
        pending_count=pending_count,
    )


def stop_campaign(campaign_id: int, *, session=None) -> CampaignStopResult:
    """Stop active Relay waves for a campaign and reconcile local deliveries."""
    campaign = EmailCampaign.objects.get(pk=campaign_id)
    if not campaign_relay_keys(campaign):
        raise CampaignStopConflict(
            "Campaign has no Relay campaign key to stop.",
            code="campaign_no_relay_key",
        )
    if session is None:
        session = requests.Session()
    return _halt_and_reconcile(
        campaign, target_status="paused", label="Stopped", session=session,
    )


def cancel_campaign(campaign_id: int, *, session=None) -> CampaignStopResult:
    """Permanently cancel a campaign and halt any active Relay waves."""
    campaign = EmailCampaign.objects.get(pk=campaign_id)
    if campaign.status == "sent":
        raise CampaignStopConflict(
            "Campaign has already been sent.",
            code="campaign_already_sent",
        )
    # Commit the local stop before fallible provider calls or reconciliation.
    # A Relay outage must never leave queued recovery able to send this campaign.
    campaign.status = "cancelled"
    campaign.scheduled_at = None
    campaign.scheduled_timezone = ""
    campaign.save(update_fields=["status", "scheduled_at", "scheduled_timezone"])
    if session is None:
        session = requests.Session()
    return _halt_and_reconcile(
        campaign, target_status="cancelled", label="Cancelled", session=session,
    )
