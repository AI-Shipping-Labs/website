"""Relay campaign progress: the stall clock, stats reads, and queue transitions."""

from dataclasses import dataclass

from django.utils import timezone

from email_app.services.relay_campaign_alerts import alert_relay_campaign_failure

_AGGREGATE_FIELDS = (
    "sent_count", "delivered_count", "unique_open_count", "open_count",
    "unique_click_count", "click_count", "unsubscribe_count", "bounce_count",
    "complaint_count",
)
RELAY_TERMINAL_FAILURE_STATUSES = ("failed", "cancelled")
# Stored errors in these states explain the state, so a stats read must keep them.
_ERROR_OWNING_STATES = frozenset({"failed", "timed_out", "relay_failed"})


@dataclass(frozen=True)
class RelayProgressRead:
    """One successful read of every Relay key on a campaign, in key order."""

    totals: dict
    per_key: tuple

    @property
    def all_sent(self):
        return self.totals["status"] == "sent"

    def failed_key(self):
        """Return ``(key, status)`` for the first failed or cancelled key."""
        for key, native in self.per_key:
            if native.get("status") in RELAY_TERMINAL_FAILURE_STATUSES:
                return key, native.get("status")
        return None

    @property
    def paused(self):
        for _key, native in self.per_key:
            if native.get("send_halted") and native.get("send_halt_reason") == "paused":
                return True
        return False


def campaign_relay_keys(campaign):
    keys = list(campaign.relay_campaign_keys or [])
    if not keys and campaign.relay_campaign_key:
        keys = [campaign.relay_campaign_key]
    return keys


def stored_relay_sent_total(campaign):
    stats = campaign.relay_campaign_stats
    if isinstance(stats, dict):
        return stats.get("sent_count", 0)
    return 0


def _aggregate_status(native_statuses):
    if native_statuses and all(status == "sent" for status in native_statuses):
        return "sent"
    for terminal in RELAY_TERMINAL_FAILURE_STATUSES:
        if terminal in native_statuses:
            return terminal
    return "sending"


def read_campaign_relay_progress(campaign, *, session=None):
    """Read and persist aggregate metrics from all native Relay campaign waves.

    Returns ``None`` when the campaign has no Relay key. Any increase in the
    total sent count advances the stall clock, so page reads and the watchdog
    share one clock.
    """
    # Local import: relay_campaigns re-exports this module's refresh helpers.
    from email_app.services.relay_campaigns import get_native_campaign_stats

    keys = campaign_relay_keys(campaign)
    if not keys:
        return None
    previous_sent = stored_relay_sent_total(campaign)
    totals = dict.fromkeys(_AGGREGATE_FIELDS, 0)
    per_key = []
    for key in keys:
        native = get_native_campaign_stats(key, session=session)
        per_key.append((key, native))
        for field in _AGGREGATE_FIELDS:
            totals[field] += native[field]
    totals["status"] = _aggregate_status([native.get("status", "unknown") for _key, native in per_key])
    now = timezone.now()
    campaign.relay_campaign_stats = totals
    campaign.relay_campaign_stats_synced_at = now
    campaign.sent_count = totals["sent_count"]
    update_fields = ["relay_campaign_stats", "relay_campaign_stats_synced_at", "sent_count"]
    if totals["sent_count"] > previous_sent:
        campaign.relay_campaign_progress_at = now
        update_fields.append("relay_campaign_progress_at")
    campaign.save(update_fields=update_fields)
    return RelayProgressRead(totals=totals, per_key=tuple(per_key))


def refresh_campaign_relay_stats(campaign, *, session=None):
    """Read and persist aggregate metrics, returning the totals."""
    read = read_campaign_relay_progress(campaign, session=session)
    if read is None:
        return None
    return read.totals


def refresh_campaign_relay_stats_loud(campaign, *, session=None):
    """Refresh stats and persist/alert any failure before propagating it."""
    owns_error = campaign.relay_campaign_state in _ERROR_OWNING_STATES
    try:
        result = refresh_campaign_relay_stats(campaign, session=session)
    except Exception as exc:
        if not owns_error:
            campaign.relay_campaign_error = f"Relay stats refresh failed: {type(exc).__name__}: {exc}"[:2000]
            campaign.save(update_fields=["relay_campaign_error"])
        alert_relay_campaign_failure(campaign, "stats refresh", exc)
        raise
    if result is not None and campaign.relay_campaign_error and not owns_error:
        campaign.relay_campaign_error = ""
        campaign.save(update_fields=["relay_campaign_error"])
    return result


def begin_native_queue(campaign, external_key):
    """Record a queue attempt for ``external_key`` before calling Relay.

    The clock starts on a new key or on a finished send starting again. A retry
    of the same key leaves it alone, so a retry loop cannot hide a stall. An
    attention state and its message stay until a queue attempt succeeds.
    """
    keys = list(campaign.relay_campaign_keys or [])
    starts_clock = (
        external_key not in keys
        or campaign.relay_campaign_progress_at is None
        or campaign.relay_campaign_state == "sent"
    )
    campaign.relay_campaign_key = campaign.relay_campaign_key or external_key
    campaign.relay_campaign_keys = list(dict.fromkeys([*keys, external_key]))
    update_fields = ["relay_campaign_key", "relay_campaign_keys"]
    if starts_clock:
        campaign.relay_campaign_progress_at = timezone.now()
        update_fields.append("relay_campaign_progress_at")
    if not campaign.in_relay_attention:
        campaign.relay_campaign_state = "pending"
        campaign.relay_campaign_error = ""
        update_fields.extend(["relay_campaign_state", "relay_campaign_error"])
    campaign.save(update_fields=update_fields)


def record_native_queue_failure(campaign, exc):
    """Persist a failed queue attempt, keeping any attention state."""
    if campaign.in_relay_attention:
        return
    campaign.status = "needs_attention"
    campaign.relay_campaign_state = "failed"
    campaign.relay_campaign_error = f"{type(exc).__name__}: {exc}"[:2000]
    campaign.save(update_fields=[
        "status", "relay_campaign_state", "relay_campaign_error",
    ])


def mark_native_queue_accepted(campaign, *, already_queued):
    """Move to ``queued`` after Relay accepted the snapshot.

    A fresh queue that clears an attention state restarts the clock, so the
    next watchdog run does not time out a send that has only just been queued.
    """
    update_fields = [
        "relay_campaign_state", "relay_campaign_error", "relay_campaign_attention_sent_count",
    ]
    if campaign.in_relay_attention and not already_queued:
        campaign.relay_campaign_progress_at = timezone.now()
        campaign.relay_campaign_attention_alerted_at = None
        update_fields.extend([
            "relay_campaign_progress_at",
            "relay_campaign_attention_alerted_at",
        ])
    campaign.relay_campaign_state = "queued"
    campaign.relay_campaign_error = ""
    campaign.relay_campaign_attention_sent_count = None
    campaign.save(update_fields=update_fields)
