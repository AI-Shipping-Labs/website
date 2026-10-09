"""Scheduled entry point for pod meeting reminders (issue #1919).

Registered in ``jobs.schedule_reconciliation.SCHEDULE_DEFINITIONS`` as
``pods-meeting-reminders`` (``0 * * * *``).
"""

from pods.services.meeting_reminders import send_meeting_reminders as _send


def send_meeting_reminders():
    """Bell reminders for scheduled pod meetings starting soon."""
    return _send()
