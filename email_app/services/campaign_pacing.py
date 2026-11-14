"""Stagger scheduled campaign recovery batches."""

import math

from integrations.config import get_config

DEFAULT_SEND_DELAY = 1.5
DEFAULT_BATCH_INTERVAL_SECONDS = 300


def get_campaign_batch_interval_seconds(batch_size):
    """Keep scheduled batches far enough apart for their per-send pacing."""
    raw = get_config(
        "CAMPAIGN_BATCH_INTERVAL_SECONDS",
        DEFAULT_BATCH_INTERVAL_SECONDS,
    )
    try:
        configured_interval = int(raw)
    except (TypeError, ValueError):
        configured_interval = DEFAULT_BATCH_INTERVAL_SECONDS
    safe_batch_duration = math.ceil(max(batch_size, 1) * DEFAULT_SEND_DELAY)
    return max(
        configured_interval,
        DEFAULT_BATCH_INTERVAL_SECONDS,
        safe_batch_duration,
    )
