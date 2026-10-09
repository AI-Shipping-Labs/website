"""Pure pod meeting state: expiry, numbering and counting (issue #1919).

No dependency on the membership or meeting services, so both (and the
presentation layer) can import it. Everything is computed in Python over
the pod's meetings: a pod plans at most 20 meetings, so one query per pod
is cheaper and clearer than layered annotations.

- A proposal is expired once its start passes while it is still
  ``proposed``. A proposed weekly series expires with its first meeting
  (the series is answered on that meeting, so it can no longer be agreed).
- Meeting number = 1-based position among the pod's meetings that are
  neither cancelled nor expired, ordered by start.
- Used meetings = scheduled + held + live (non-expired) proposals. This is
  what ``Pod.meeting_count`` limits.
"""

from dataclasses import dataclass, field
from datetime import timedelta

from django.utils import timezone

from pods.models import (
    MEETING_STATUS_CANCELLED,
    MEETING_STATUS_HELD,
    MEETING_STATUS_PROPOSED,
    MEETING_STATUS_SCHEDULED,
    PodMeeting,
)

__all__ = [
    'MeetingState',
    'meeting_end',
    'meeting_state',
    'used_meeting_count',
]


def meeting_end(meeting):
    return meeting.starts_at + timedelta(minutes=meeting.duration_minutes)


@dataclass
class MeetingState:
    """A pod's meetings at one instant, with the derived rule values."""

    now: object
    meetings: list = field(default_factory=list)
    expired: set = field(default_factory=set)
    numbers: dict = field(default_factory=dict)

    def is_expired(self, meeting):
        return meeting.pk in self.expired

    def is_live(self, meeting):
        """Takes up a slot on the calendar: not cancelled, not expired."""
        return meeting.status != MEETING_STATUS_CANCELLED and meeting.pk not in self.expired

    @property
    def used(self):
        return sum(1 for m in self.meetings if self.is_live(m))

    @property
    def held(self):
        return sum(1 for m in self.meetings if m.status == MEETING_STATUS_HELD)

    @property
    def open_proposals(self):
        return [m for m in self.meetings if m.status == MEETING_STATUS_PROPOSED and self.is_live(m)]

    @property
    def has_open_proposal(self):
        return bool(self.open_proposals)

    def series(self, series_id):
        return [m for m in self.meetings if series_id and m.series_id == series_id]

    def latest_agreed(self):
        """The latest scheduled or held meeting, or ``None``."""
        agreed = [m for m in self.meetings if m.status in (MEETING_STATUS_SCHEDULED, MEETING_STATUS_HELD)]
        return agreed[-1] if agreed else None


def _expired_ids(meetings, now):
    ids = set()
    expired_series = set()
    for meeting in meetings:
        if meeting.status == MEETING_STATUS_PROPOSED and meeting.starts_at <= now:
            ids.add(meeting.pk)
            if meeting.series_id:
                expired_series.add(meeting.series_id)
    for meeting in meetings:
        if meeting.status == MEETING_STATUS_PROPOSED and meeting.series_id in expired_series:
            ids.add(meeting.pk)
    return ids


def meeting_state(pod, now=None, meetings=None):
    """Build a :class:`MeetingState` for ``pod`` (one query unless given)."""
    now = now or timezone.now()
    if meetings is None:
        meetings = list(PodMeeting.objects.filter(pod_id=pod.pk).order_by('starts_at', 'pk'))
    else:
        meetings = sorted(meetings, key=lambda m: (m.starts_at, m.pk))
    state = MeetingState(now=now, meetings=meetings, expired=_expired_ids(meetings, now))
    number = 0
    for meeting in meetings:
        if state.is_live(meeting):
            number += 1
            state.numbers[meeting.pk] = number
    return state


def used_meeting_count(pod, now=None):
    return meeting_state(pod, now).used
