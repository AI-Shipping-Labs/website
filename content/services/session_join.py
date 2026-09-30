"""Join state for an upcoming course session unit.

The unit shows one of four join states before the session ends:

- ``open``: inside the join window, the viewer may join. The unit renders
  the event page's join partial (``events/_event_join_now.html``).
- ``waiting``: the viewer may join but the window has not opened yet. The
  unit renders a muted note saying when the join link appears, plus the
  email-reminder sentence when the reminder emails will reach them.
- ``external``: a partner-hosted event for a linked cohort member; the unit
  links straight to the partner's join URL.
- ``none``: the viewer is not in the event's join audience; the unit points
  them at the event page.
"""

from events.models.event import EVENT_JOIN_WINDOW_MINUTES
from events.services.event_audience import (
    is_series_cohort_member,
    viewer_receives_event_reminders,
)
from events.services.event_join import user_can_join_event

JOIN_STATE_OPEN = 'open'
JOIN_STATE_WAITING = 'waiting'
JOIN_STATE_EXTERNAL = 'external'
JOIN_STATE_NONE = 'none'


def _join_state(event, user):
    if event.is_past:
        return JOIN_STATE_NONE
    if event.is_external:
        if (
            event.zoom_join_url
            and getattr(user, 'is_authenticated', False)
            and is_series_cohort_member(event, user.pk)
        ):
            return JOIN_STATE_EXTERNAL
        return JOIN_STATE_NONE
    if not user_can_join_event(user, event):
        return JOIN_STATE_NONE
    if event.can_show_zoom_link():
        return JOIN_STATE_OPEN
    return JOIN_STATE_WAITING


def build_session_join_context(event, user):
    """Return the join keys merged into the session unit's entry."""
    join_state = _join_state(event, user)
    shows_reminder_note = False
    if join_state == JOIN_STATE_WAITING:
        shows_reminder_note = viewer_receives_event_reminders(event, user)
    return {
        'join_state': join_state,
        'join_window_minutes': EVENT_JOIN_WINDOW_MINUTES,
        'shows_reminder_note': shows_reminder_note,
    }
