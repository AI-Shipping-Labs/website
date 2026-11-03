"""Rate-limit campaign transport calls and stagger their scheduled batches."""

import math
import time
from collections.abc import Callable

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


def paced_campaign_send(send: Callable, *, delay_seconds, **kwargs):
    """Apply the per-send gap after success or any transport failure."""
    try:
        return send(**kwargs)
    finally:
        if delay_seconds > 0:
            time.sleep(delay_seconds)
