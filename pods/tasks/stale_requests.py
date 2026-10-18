"""Scheduled entry point for the stuck pod proposal alert.

Registered in ``jobs.schedule_reconciliation.SCHEDULE_DEFINITIONS`` as
``pods-stale-request-alert`` (``0 9 * * *``).
"""

from pods.services.stale_requests import send_stale_request_alert as _send


def send_stale_request_alert():
    """Post one staff Slack message about newly stuck meeting proposals."""
    return _send()
