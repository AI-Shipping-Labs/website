"""django-q entry point for the automatic recap-ready notice.

The logic lives in ``events.services.event_recap_notification``; this module
gives the worker a stable dotted path that the task history can map back to
the event (``jobs.task_entities.EVENT_FUNCS``).
"""

from events.services.event_recap_notification import (
    send_recap_auto_notify as _send_recap_auto_notify,
)


def send_recap_auto_notify(event_id):
    """Announce the event's recap once to its whole audience."""
    return _send_recap_auto_notify(event_id)
