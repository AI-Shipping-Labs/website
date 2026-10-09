"""Scheduled entry point for the daily stale pod request alert (issue #1927).

Registered in ``jobs.schedule_reconciliation.SCHEDULE_DEFINITIONS`` as
``pods-stale-request-alert`` (``0 9 * * *``).
"""

from pods.services.stale_requests import send_stale_request_alert as _send


def send_stale_request_alert():
    """Post one staff Slack message about newly stale pod requests."""
    return _send()
