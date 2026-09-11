"""Background task entry point for recap auto-drafting (issue #1597).

Thin django-q2 wrapper around
:func:`events.services.recap_draft.draft_event_recap`, which owns the
gates, the prompt guardrails, and the failure contract (expected failures
log and return a status dict; only unexpected errors propagate so
django-q2 can retry them).
"""

import logging

logger = logging.getLogger(__name__)


def draft_event_recap(event_id, force=False):
    """Draft the recap for one event from its stored transcript.

    Args:
        event_id: ID of the Event model instance.
        force: When True, regenerate even if ``recap_notes`` is non-empty
            (explicit operator opt-in via the sync-transcript surfaces).

    Returns:
        dict with ``status`` (``drafted`` / ``skipped`` / ``error``).
    """
    from events.models import Event
    from events.services.recap_draft import draft_event_recap as draft

    try:
        event = Event.objects.get(id=event_id)
    except Event.DoesNotExist:
        logger.error('Event %s not found, skipping recap draft', event_id)
        return {'status': 'error', 'message': f'Event {event_id} not found'}

    return draft(event, force=force)
