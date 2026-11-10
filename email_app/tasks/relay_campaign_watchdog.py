"""Time bound on native Relay campaigns that stop progressing.

``relay_campaign_state`` stays ``queued`` after Relay accepts a send and the
campaign can already read ``sent`` locally, so only Relay can say whether a
send is really moving. Each attention alert is recorded per stalled Relay key,
which lets a later monitored wave that stalls alert again.
"""

import logging
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from email_app.models import CampaignDelivery, EmailCampaign
from email_app.services.relay_campaign_progress import (
    campaign_relay_keys,
    read_campaign_relay_progress,
    stored_relay_sent_total,
)
from email_app.services.relay_campaigns import (
    RelayCampaignApiError,
    RelayCampaignError,
    alert_relay_campaign_failure,
)
from email_app.tasks.send_campaign import refresh_campaign_status
from integrations.config import get_config

logger = logging.getLogger(__name__)

DEFAULT_STALL_TIMEOUT_MINUTES = 30
ALERT_OPERATION = "progress check"
UNASSIGNED_KEY = "(not assigned)"
ERROR_MAX_LENGTH = 2000

NO_PROGRESS_MESSAGE = (
    "No progress for {minutes} minutes. Last Relay status: {status}, {sent} sent. "
    "Check Relay campaign {key}. This clears automatically when sending resumes."
)
UNREADABLE_MESSAGE = (
    "Relay could not be read for {minutes} minutes. Check Relay campaign {key}. "
    "This clears automatically when sending resumes."
)
RELAY_FAILED_MESSAGE = "Relay reported campaign {key} as {status}. Check it in Relay."


def stall_timeout_minutes():
    raw = get_config("RELAY_CAMPAIGN_STALL_TIMEOUT_MINUTES", DEFAULT_STALL_TIMEOUT_MINUTES)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        logger.warning(
            "Invalid RELAY_CAMPAIGN_STALL_TIMEOUT_MINUTES; using default %s",
            DEFAULT_STALL_TIMEOUT_MINUTES,
        )
        return DEFAULT_STALL_TIMEOUT_MINUTES
    return value


def _watched_campaigns():
    released = Q(deliveries__wave__isnull=True) | Q(deliveries__wave__released_at__isnull=False)
    failed_with_work = Q(
        released,
        relay_campaign_state="failed",
        deliveries__state=CampaignDelivery.State.PENDING,
    )
    active = Q(relay_campaign_state__in=["pending", "queued", *EmailCampaign.RELAY_ATTENTION_STATES])
    return list(EmailCampaign.objects.filter(active | failed_with_work).distinct())


def _transition(campaign, **fields):
    """Apply ``fields`` only if the row is still in the state this run read."""
    updated = EmailCampaign.objects.filter(
        pk=campaign.pk,
        relay_campaign_state=campaign.relay_campaign_state,
    ).update(**fields)
    if not updated:
        return False
    for name, value in fields.items():
        setattr(campaign, name, value)
    return True


def _clock_expired(campaign, now, minutes):
    """True past the timeout; a row with no clock starts one without alarm."""
    if campaign.relay_campaign_progress_at is None:
        _transition(campaign, relay_campaign_progress_at=now)
        return False
    return campaign.relay_campaign_progress_at <= now - timedelta(minutes=minutes)


def _latest_key(campaign):
    keys = campaign_relay_keys(campaign)
    if keys:
        return keys[-1]
    return UNASSIGNED_KEY


def _stalled_key(campaign, read):
    """The newest unfinished key, stable across readable and unreadable runs."""
    if read is not None:
        for key, native in reversed(read.per_key):
            if native.get("status") != "sent":
                return key
    return _latest_key(campaign)


def _alert_stalled_key(campaign, key, now):
    """Post, then record: a key is only marked once a route accepted the alert."""
    alerted = list(campaign.relay_campaign_attention_alerted_keys or [])
    if key in alerted:
        return
    accepted = alert_relay_campaign_failure(
        campaign,
        ALERT_OPERATION,
        RelayCampaignApiError(campaign.relay_campaign_error),
        relay_key=key,
        bypass_cooldown=True,
    )
    if accepted:
        EmailCampaign.objects.filter(pk=campaign.pk).update(
            relay_campaign_attention_alerted_keys=[*alerted, key],
            relay_campaign_attention_alerted_at=now,
        )


def _raise_attention(campaign, state, message, baseline, key, now):
    entered = _transition(
        campaign,
        relay_campaign_state=state,
        relay_campaign_error=message[:ERROR_MAX_LENGTH],
        relay_campaign_attention_sent_count=baseline,
        status="needs_attention",
    )
    if entered:
        _alert_stalled_key(campaign, key, now)


def _raise_relay_failed(campaign, read, now):
    key, status = read.failed_key()
    message = RELAY_FAILED_MESSAGE.format(key=key, status=status)
    baseline = campaign.relay_campaign_attention_sent_count
    if baseline is None:
        baseline = read.totals["sent_count"]
    _raise_attention(campaign, "relay_failed", message, baseline, key, now)


def _settle(campaign, state):
    """Leave attention or tracking and let deliveries decide the status again."""
    settled = _transition(
        campaign,
        relay_campaign_state=state,
        relay_campaign_error="",
        relay_campaign_attention_sent_count=None,
    )
    if settled:
        refresh_campaign_status(campaign.pk)


def _read_relay(campaign):
    try:
        return read_campaign_relay_progress(campaign)
    except RelayCampaignError as exc:
        logger.warning(
            "Relay campaign watchdog could not read campaign=%s: %s",
            campaign.pk,
            type(exc).__name__,
        )
        return None


def _check_unqueued(campaign, now, minutes):
    """A send Relay never accepted: judged by the clock, with no Relay call."""
    if not _clock_expired(campaign, now, minutes):
        return
    key = _latest_key(campaign)
    message = NO_PROGRESS_MESSAGE.format(minutes=minutes, status="not queued", sent=0, key=key)
    _raise_attention(campaign, "timed_out", message, stored_relay_sent_total(campaign), key, now)


def _timed_out_message(campaign, read, key, minutes):
    if read is None:
        return UNREADABLE_MESSAGE.format(minutes=minutes, key=key)
    status = "unknown"
    for candidate, native in read.per_key:
        if candidate == key:
            status = native.get("status", "unknown")
    return NO_PROGRESS_MESSAGE.format(
        minutes=minutes, status=status, sent=read.totals["sent_count"], key=key,
    )


def _check_queued(campaign, now, minutes):
    read = _read_relay(campaign)
    if read is not None and read.all_sent:
        _settle(campaign, "sent")
        return
    if read is not None and read.failed_key():
        _raise_relay_failed(campaign, read, now)
        return
    if read is not None and read.paused:
        _transition(campaign, relay_campaign_progress_at=now)
        return
    if not _clock_expired(campaign, now, minutes):
        return
    key = _stalled_key(campaign, read)
    message = _timed_out_message(campaign, read, key, minutes)
    _raise_attention(campaign, "timed_out", message, stored_relay_sent_total(campaign), key, now)


def _check_attention(campaign, now):
    baseline = campaign.relay_campaign_attention_sent_count
    if baseline is None:
        baseline = stored_relay_sent_total(campaign)
    read = _read_relay(campaign)
    if read is not None and read.all_sent:
        _settle(campaign, "sent")
        return
    if read is not None and read.failed_key():
        if campaign.relay_campaign_state == "timed_out":
            _raise_relay_failed(campaign, read, now)
            return
    elif read is not None and read.totals["sent_count"] > baseline:
        _settle(campaign, "queued")
        return
    _alert_stalled_key(campaign, _attention_key(campaign, read), now)


def _attention_key(campaign, read):
    if read is not None and read.failed_key():
        return read.failed_key()[0]
    return _stalled_key(campaign, read)


def _check_campaign(campaign, now, minutes):
    state = campaign.relay_campaign_state
    if state in ("pending", "failed"):
        _check_unqueued(campaign, now, minutes)
    elif state == "queued":
        _check_queued(campaign, now, minutes)
    else:
        _check_attention(campaign, now)


def run_relay_campaign_watchdog():
    """Check every in-flight Relay campaign; one failure cannot stop the rest."""
    now = timezone.now()
    minutes = stall_timeout_minutes()
    campaigns = _watched_campaigns()
    failures = 0
    for campaign in campaigns:
        try:
            _check_campaign(campaign, now, minutes)
        except Exception:
            # Broad on purpose: each campaign is an isolated external boundary.
            logger.exception("Relay campaign watchdog failed campaign=%s", campaign.pk)
            failures += 1
    return {"checked": len(campaigns), "failed": failures}
