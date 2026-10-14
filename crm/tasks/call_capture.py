"""django-q task wrapper for booking-call capture (issue #1951).

The schedule entry (``booking-call-capture``, every 15 minutes) points
here; the logic lives in :mod:`crm.services.call_capture`. Expected
failures never raise, so a tick with an unconfigured Zoom or a
transient LLM error just logs its tally.
"""

import logging

logger = logging.getLogger(__name__)


def capture_finished_booking_calls():
    """Capture finished booked 1:1 calls; returns the tick tally."""
    from crm.services.call_capture import (
        capture_finished_booking_calls as run_capture,
    )

    tally = run_capture()
    logger.info('booking-call-capture tick: %s', tally)
    return tally
