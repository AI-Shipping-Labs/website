"""Safe staff resume for partial or stuck native Relay campaigns."""

from __future__ import annotations

from dataclasses import dataclass

import requests
from django.db import transaction
from django.utils import timezone

from email_app.models import CampaignDelivery, EmailCampaign, EmailLog
from email_app.services.campaign_batch_guard import load_sendable_campaign
from email_app.services.relay_campaign_progress import (
    campaign_relay_keys,
    mark_native_queue_accepted,
)
from email_app.services.relay_campaigns import (
    RELAY_CAMPAIGN_AUDIENCE,
    RELAY_CAMPAIGN_CLIENT,
    _campaign_api_request,
)
from email_app.tasks.campaign_delivery_recovery import live_batch_delivery_ids


class CampaignResumeConflict(Exception):
    """Raised when a campaign cannot be safely resumed."""

    def __init__(self, message: str, *, code: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class CampaignResumeResult:
    campaign: EmailCampaign
    pending_count: int
    external_key: str
    task_id: str | None


def _next_resume_key(campaign: EmailCampaign) -> str:
    used = set(campaign_relay_keys(campaign))
    base = f"aishl-campaign-{campaign.pk}"
    if base not in used:
        return base
    index = 1
    while f"{base}-resume-{index}" in used:
        index += 1
    return f"{base}-resume-{index}"


_CONFIRMED_SENT_STATUSES = frozenset({"sent", "delivered", "bounced", "complained", "unsubscribed"})


def _is_confirmed_sent_recipient(row: object) -> bool:
    """Return True only when Relay confirms the recipient was actually sent."""
    if not isinstance(row, dict) or not row.get("client_recipient_key"):
        return False
    if row.get("ses_message_id"):
        return True
    return row.get("status") in _CONFIRMED_SENT_STATUSES


def _confirmed_keys_for_relay_key(key: str, scope: dict, session: requests.Session) -> set[str]:
    existing = _campaign_api_request(
        "GET",
        f"/api/campaigns/{key}",
        params=scope,
        session=session,
        allow_not_found=True,
    )
    if not existing or not isinstance(existing.get("campaign"), dict):
        return set()
    if existing["campaign"].get("status") not in {"queued", "snapshotting", "sending", "sent"}:
        return set()
    rec_resp = _campaign_api_request(
        "GET",
        f"/api/campaigns/{key}/recipients",
        params=scope,
        session=session,
    )
    confirmed: set[str] = set()
    for row in rec_resp.get("recipients") or []:
        if _is_confirmed_sent_recipient(row):
            confirmed.add(str(row["client_recipient_key"]))
    return confirmed


def _confirmed_keys_from_prior_campaigns(campaign: EmailCampaign, exclude_key: str) -> set[str]:
    scope = {"audience": RELAY_CAMPAIGN_AUDIENCE, "client": RELAY_CAMPAIGN_CLIENT}
    session = requests.Session()
    confirmed: set[str] = set()
    for key in campaign_relay_keys(campaign):
        if key == exclude_key:
            continue
        confirmed.update(_confirmed_keys_for_relay_key(key, scope, session))
    return confirmed


def _mark_confirmed_deliveries_sent(campaign: EmailCampaign, delivery_ids: list[int], confirmed: set[str]):
    if not confirmed:
        return
    now = timezone.now()
    deliveries = CampaignDelivery.objects.filter(
        campaign=campaign,
        pk__in=delivery_ids,
        state=CampaignDelivery.State.PENDING,
    )
    for delivery in deliveries:
        if str(delivery.pk) not in confirmed:
            continue
        log, _created = EmailLog.objects.get_or_create(
            campaign=campaign,
            user_id=delivery.recipient_user_pk,
            defaults={
                "recipient_email": delivery.recipient_email,
                "email_type": "campaign",
                "subject": campaign.subject,
                "ses_message_id": "",
            },
        )
        delivery.state = CampaignDelivery.State.SENT
        delivery.email_log = log
        delivery.completed_at = now
        delivery.save(update_fields=["state", "email_log", "completed_at", "updated_at"])


def resume_campaign_batch(campaign_id: int, delivery_ids: list[int], external_key: str) -> dict:
    """Reconcile against prior Relay keys, then queue only unconfirmed pending deliveries."""
    from email_app.tasks.send_campaign import refresh_campaign_status, send_campaign_batch

    campaign = load_sendable_campaign(campaign_id)
    confirmed = _confirmed_keys_from_prior_campaigns(campaign, external_key)
    _mark_confirmed_deliveries_sent(campaign, delivery_ids, confirmed)
    remaining_ids = list(
        CampaignDelivery.objects.filter(
            campaign=campaign,
            pk__in=delivery_ids,
            state=CampaignDelivery.State.PENDING,
        )
        .order_by("recipient_user_pk", "pk")
        .values_list("pk", flat=True)
    )
    if not remaining_ids:
        mark_native_queue_accepted(campaign, already_queued=True)
        refresh_campaign_status(campaign.pk)
        return {
            "campaign_id": campaign.pk,
            "batch_size": 0,
            "sent_count": 0,
            "skipped_count": 0,
            "relay_campaign_key": external_key,
            "reconciled_only": True,
        }
    return send_campaign_batch(
        campaign.pk,
        delivery_ids=remaining_ids,
        external_key=external_key,
    )


def _validate_resumable_campaign(campaign: EmailCampaign) -> list[int]:
    if campaign.status == "cancelled":
        raise CampaignResumeConflict(
            "Campaign has been cancelled.",
            code="campaign_cancelled",
        )
    if campaign.status == "draft":
        raise CampaignResumeConflict(
            "Campaign is still a draft; use send instead.",
            code="campaign_is_draft",
        )
    if campaign.audience_snapshotted_at is None:
        raise CampaignResumeConflict(
            "Campaign audience has not been snapshotted.",
            code="campaign_not_snapshotted",
        )
    if campaign.waves.exists():
        raise CampaignResumeConflict(
            "Monitored-wave campaigns must be resumed per wave.",
            code="campaign_waves_unsupported",
        )
    pending_ids = list(
        campaign.deliveries.filter(state=CampaignDelivery.State.PENDING)
        .order_by("recipient_user_pk", "pk")
        .values_list("pk", flat=True)
    )
    if not pending_ids:
        raise CampaignResumeConflict(
            "Campaign has no pending deliveries to resume.",
            code="campaign_no_pending_deliveries",
        )
    if set(pending_ids) & live_batch_delivery_ids():
        raise CampaignResumeConflict(
            "Campaign already has a live batch queued for pending deliveries.",
            code="campaign_resume_in_progress",
        )
    return pending_ids


def _mark_campaign_resuming(campaign: EmailCampaign, external_key: str):
    keys = list(campaign.relay_campaign_keys or [])
    campaign.status = "sending"
    campaign.relay_campaign_state = "pending"
    campaign.relay_campaign_error = ""
    campaign.relay_campaign_key = campaign.relay_campaign_key or external_key
    campaign.relay_campaign_keys = list(dict.fromkeys([*keys, external_key]))
    campaign.relay_campaign_progress_at = timezone.now()
    campaign.relay_campaign_attention_sent_count = None
    campaign.relay_campaign_attention_alerted_at = None
    campaign.save(update_fields=[
        "status",
        "relay_campaign_state",
        "relay_campaign_error",
        "relay_campaign_key",
        "relay_campaign_keys",
        "relay_campaign_progress_at",
        "relay_campaign_attention_sent_count",
        "relay_campaign_attention_alerted_at",
    ])


def resume_pending_campaign(campaign_id: int, *, source: str = "API campaign resume") -> CampaignResumeResult:
    """Atomically enqueue a safe resume for PENDING deliveries only."""
    from email_app.services.relay_campaign_control import reconcile_native_campaign_deliveries
    from jobs.tasks import async_task, build_task_name

    pre_campaign = EmailCampaign.objects.get(pk=campaign_id)
    if campaign_relay_keys(pre_campaign):
        has_pending = pre_campaign.deliveries.filter(state=CampaignDelivery.State.PENDING).exists()
        has_dispatching = pre_campaign.deliveries.filter(state=CampaignDelivery.State.DISPATCHING).exists()
        if not has_pending or has_dispatching:
            reconcile_native_campaign_deliveries(pre_campaign, revert_unconfirmed=True)

    with transaction.atomic():
        campaign = EmailCampaign.objects.select_for_update().get(pk=campaign_id)
        pending_ids = _validate_resumable_campaign(campaign)
        external_key = _next_resume_key(campaign)
        _mark_campaign_resuming(campaign, external_key)
        task_id = async_task(
            "email_app.services.campaign_resume.resume_campaign_batch",
            campaign.pk,
            pending_ids,
            external_key,
            task_name=build_task_name(
                "Resume campaign",
                f"#{campaign.pk} {campaign.subject} ({len(pending_ids)} pending)",
                source,
            ),
        )
        task_id_str = None
        if task_id:
            task_id_str = str(task_id)
        return CampaignResumeResult(
            campaign=campaign,
            pending_count=len(pending_ids),
            external_key=external_key,
            task_id=task_id_str,
        )
