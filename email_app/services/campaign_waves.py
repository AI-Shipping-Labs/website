"""Fail-closed monitoring and release decisions for re-permission waves."""

from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from email_app.models import CampaignDelivery, CampaignWave, EmailCampaign

PILOT_WAVE_SIZE = 100
FOLLOWUP_WAVE_SIZE = 250
OBSERVATION_WINDOW = timedelta(hours=24)
BOUNCE_STOP_PERCENT = 2.0
COMPLAINT_STOP_COUNT = 1

UNRESOLVED_STATES = {
    CampaignDelivery.State.FAILED,
    CampaignDelivery.State.AMBIGUOUS,
}


@dataclass(frozen=True)
class WaveReleaseResult:
    released: bool
    campaign: EmailCampaign
    wave: CampaignWave | None = None
    blocking_reason: str = ""


def bounce_threshold_breached(bounces, confirmed_sent):
    """Compare the 2% boundary without floating-point rounding."""
    return confirmed_sent > 0 and bounces * 100 >= confirmed_sent * BOUNCE_STOP_PERCENT


def _counts_for_deliveries(deliveries, *, now):
    states = {row["state"]: row["count"] for row in deliveries.values("state").annotate(count=Count("id"))}
    confirmed = deliveries.filter(
        state=CampaignDelivery.State.SENT,
        email_log__isnull=False,
    )
    sent = confirmed.count()
    bounces = confirmed.filter(email_log__bounced_at__isnull=False).count()
    complaints = confirmed.filter(email_log__complained_at__isnull=False).count()
    expired_dispatching = deliveries.filter(
        state=CampaignDelivery.State.DISPATCHING,
        claim_expires_at__lte=now,
    ).count()
    unresolved = sum(states.get(state, 0) for state in UNRESOLVED_STATES)
    unresolved += expired_dispatching
    live = states.get(CampaignDelivery.State.PENDING, 0)
    live += deliveries.filter(
        Q(state=CampaignDelivery.State.DISPATCHING),
        Q(claim_expires_at__isnull=True) | Q(claim_expires_at__gt=now),
    ).count()
    return {
        "recipient_count": sum(states.values()),
        "confirmed_sent": sent,
        "skipped": states.get(CampaignDelivery.State.SKIPPED, 0),
        "failed_ambiguous": unresolved,
        "bounces": bounces,
        "bounce_rate": (bounces * 100 / sent) if sent else 0.0,
        "complaints": complaints,
        "complaint_rate": (complaints * 100 / sent) if sent else 0.0,
        "live": live,
        "unresolved": unresolved,
    }


def _released_deliveries(campaign):
    return campaign.deliveries.filter(wave__released_at__isnull=False)


def _current_wave(campaign):
    return campaign.waves.filter(released_at__isnull=False).order_by("-number").first()


def _gate(campaign, wave, *, now):
    if wave is None:
        return False, "no_released_wave", None, None
    metrics = _counts_for_deliveries(wave.deliveries.all(), now=now)
    cumulative = _counts_for_deliveries(_released_deliveries(campaign), now=now)
    if metrics["complaints"] >= COMPLAINT_STOP_COUNT or cumulative["complaints"] >= COMPLAINT_STOP_COUNT:
        return False, "complaint_threshold", metrics, cumulative
    if bounce_threshold_breached(
        metrics["bounces"],
        metrics["confirmed_sent"],
    ) or bounce_threshold_breached(
        cumulative["bounces"],
        cumulative["confirmed_sent"],
    ):
        return False, "bounce_threshold", metrics, cumulative
    if metrics["live"]:
        return False, "wave_in_progress", metrics, cumulative
    if metrics["unresolved"]:
        return False, "unresolved_deliveries", metrics, cumulative
    if cumulative["confirmed_sent"] == 0:
        return False, "no_confirmed_sends", metrics, cumulative
    if wave.monitoring_started_at is None:
        return False, "monitoring_not_started", metrics, cumulative
    earliest = wave.monitoring_started_at + OBSERVATION_WINDOW
    if now < earliest:
        return False, "observation_window", metrics, cumulative
    if not campaign.waves.filter(number__gt=wave.number).exists():
        return False, "campaign_complete", metrics, cumulative
    return True, "ready", metrics, cumulative


def _refresh_locked(campaign, *, now):
    current = _current_wave(campaign)
    if current is None:
        return
    metrics = _counts_for_deliveries(current.deliveries.all(), now=now)
    updates = []
    if metrics["live"] and current.state != CampaignWave.State.SENDING:
        current.state = CampaignWave.State.SENDING
        current.monitoring_started_at = None
        updates.extend(["state", "monitoring_started_at"])
    elif not metrics["live"] and current.monitoring_started_at is None:
        current.monitoring_started_at = now
        current.state = CampaignWave.State.MONITORING
        updates.extend(["monitoring_started_at", "state"])
    if updates:
        current.save(update_fields=updates)

    can_release, reason, _metrics, cumulative = _gate(campaign, current, now=now)
    campaign_updates = []
    confirmed = cumulative["confirmed_sent"] if cumulative else 0
    if campaign.sent_count != confirmed:
        campaign.sent_count = confirmed
        campaign_updates.append("sent_count")

    if reason in {"complaint_threshold", "bounce_threshold"}:
        if current.state != CampaignWave.State.PAUSED:
            current.state = CampaignWave.State.PAUSED
            current.save(update_fields=["state"])
        target_status = "paused"
    elif reason == "unresolved_deliveries":
        target_status = "needs_attention"
    elif reason == "campaign_complete":
        if current.state != CampaignWave.State.COMPLETE:
            current.state = CampaignWave.State.COMPLETE
            current.completed_at = now
            current.save(update_fields=["state", "completed_at"])
        target_status = "sent"
    else:
        target_status = "sending"

    if campaign.status != target_status:
        campaign.status = target_status
        campaign_updates.append("status")
    if target_status == "sent" and campaign.sent_at is None:
        campaign.sent_at = now
        campaign_updates.append("sent_at")
    elif target_status != "sent" and campaign.sent_at is not None:
        campaign.sent_at = None
        campaign_updates.append("sent_at")
    if campaign_updates:
        campaign.save(update_fields=campaign_updates)


def refresh_campaign_waves(campaign_id, *, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        campaign = EmailCampaign.objects.select_for_update().get(pk=campaign_id)
        if campaign.audience_verification != "unverified_only":
            return campaign
        _refresh_locked(campaign, now=now)
        return campaign


def _schedule_wave(wave, *, source, now):
    from django_q.models import Schedule

    from jobs.tasks import build_task_name

    if wave.deliveries.exclude(campaign_id=wave.campaign_id).exists():
        raise ValueError("Wave contains a delivery from another campaign.")
    delivery_ids = list(
        wave.deliveries.filter(state=CampaignDelivery.State.PENDING)
        # ``send_campaign`` bulk-creates deliveries in the immutable
        # ``date_joined, pk`` audience order. Delivery PK order therefore
        # preserves that snapshot order even when account PKs point the other
        # way (or an account is later merged).
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    task_name = build_task_name(
        "Send campaign wave",
        f"#{wave.campaign_id} wave {wave.number}",
        source,
    )
    Schedule.objects.create(
        name=task_name,
        func="email_app.tasks.send_campaign.send_campaign_batch",
        schedule_type=Schedule.ONCE,
        repeats=1,
        next_run=now,
        kwargs={
            "campaign_id": wave.campaign_id,
            "delivery_ids": delivery_ids,
            "q_options": {"task_name": task_name},
        },
    )
    return delivery_ids


def release_initial_wave(wave, *, actor_id=None, source, now=None):
    now = now or timezone.now()
    wave.state = CampaignWave.State.SENDING
    wave.released_at = now
    wave.released_by_id = actor_id
    wave.save(update_fields=["state", "released_at", "released_by"])
    return _schedule_wave(wave, source=source, now=now)


def release_next_wave(campaign_id, *, actor, source, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        campaign = EmailCampaign.objects.select_for_update().get(pk=campaign_id)
        if campaign.audience_verification != "unverified_only":
            return WaveReleaseResult(False, campaign, blocking_reason="not_monitored")
        list(campaign.waves.select_for_update().order_by("number"))
        _refresh_locked(campaign, now=now)
        current = _current_wave(campaign)
        can_release, reason, _metrics, _cumulative = _gate(
            campaign,
            current,
            now=now,
        )
        if not can_release:
            return WaveReleaseResult(False, campaign, blocking_reason=reason)

        next_wave = (
            campaign.waves.filter(
                number__gt=current.number,
                released_at__isnull=True,
            )
            .order_by("number")
            .first()
        )
        if next_wave is None:
            return WaveReleaseResult(False, campaign, blocking_reason="campaign_complete")

        current.state = CampaignWave.State.COMPLETE
        current.completed_at = now
        current.save(update_fields=["state", "completed_at"])
        release_initial_wave(
            next_wave,
            actor_id=actor.pk,
            source=source,
            now=now,
        )
        campaign.status = "sending"
        campaign.save(update_fields=["status"])
        return WaveReleaseResult(True, campaign, wave=next_wave)


def campaign_wave_summary(campaign, *, now=None, refresh=True):
    now = now or timezone.now()
    if refresh and campaign.pk and campaign.audience_verification == "unverified_only":
        campaign = refresh_campaign_waves(campaign.pk, now=now)
    waves = list(campaign.waves.order_by("number"))
    current = next((wave for wave in reversed(waves) if wave.released_at), None)
    can_release, reason, _metrics, cumulative = _gate(campaign, current, now=now)
    rows = []
    for wave in waves:
        metrics = _counts_for_deliveries(wave.deliveries.all(), now=now)
        rows.append(
            {
                "id": wave.pk,
                "number": wave.number,
                "state": wave.state,
                **{
                    key: metrics[key]
                    for key in (
                        "recipient_count",
                        "confirmed_sent",
                        "skipped",
                        "failed_ambiguous",
                        "bounces",
                        "bounce_rate",
                        "complaints",
                        "complaint_rate",
                    )
                },
                "released_at": wave.released_at,
                "monitoring_started_at": wave.monitoring_started_at,
                "completed_at": wave.completed_at,
                "earliest_next_release": (
                    wave.monitoring_started_at + OBSERVATION_WINDOW if wave.monitoring_started_at else None
                ),
            }
        )
    cumulative = cumulative or _counts_for_deliveries(
        _released_deliveries(campaign),
        now=now,
    )
    blocking_messages = {
        "no_released_wave": "No wave has been released.",
        "wave_in_progress": "The current wave is still sending.",
        "unresolved_deliveries": "Resolve every failed or ambiguous delivery.",
        "no_confirmed_sends": "At least one confirmed send is required.",
        "complaint_threshold": "A complaint triggered the hard stop.",
        "bounce_threshold": "The bounce rate reached the 2% hard stop.",
        "monitoring_not_started": "Monitoring has not started.",
        "observation_window": "The 24-hour observation window is still open.",
        "campaign_complete": "The final wave passed monitoring.",
        "ready": "The next wave can be released.",
    }
    return {
        "campaign_id": campaign.pk,
        "waves": rows,
        "cumulative": {
            key: cumulative[key]
            for key in (
                "confirmed_sent",
                "skipped",
                "failed_ambiguous",
                "bounces",
                "bounce_rate",
                "complaints",
                "complaint_rate",
            )
        },
        "thresholds": {
            "observation_hours": 24,
            "bounce_stop_percent": BOUNCE_STOP_PERCENT,
            "complaint_stop_count": COMPLAINT_STOP_COUNT,
        },
        "can_release_next": can_release,
        "blocking_reason": reason,
        "blocking_message": blocking_messages[reason],
    }
