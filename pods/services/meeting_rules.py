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
- A live proposal waits on the pod members with no answer on its head
  meeting (issue #1934), counted from ``moved_at`` (a move resets the
  answers) or else ``created_at``.
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
    PodMeetingResponse,
)

__all__ = [
    'MeetingState',
    'meeting_end',
    'meeting_state',
    'members_without_answer',
    'proposal_waiting_since',
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


def members_without_answer(head, members, responses=None):
    """The ``members`` (users, order kept) with no answer on ``head``.

    ``head`` is a proposal's head meeting: answers to a weekly proposal are
    stored on its first meeting. ``responses`` are the head's
    ``PodMeetingResponse`` rows when the caller already loaded them;
    otherwise one query. Any answer counts (``going`` or ``Can't make
    it``), so the result is who the proposal is still waiting on.
    """
    if responses is None:
        answered = set(PodMeetingResponse.objects.filter(meeting=head).values_list('user_id', flat=True))
    else:
        answered = {r.user_id for r in responses}
    return [user for user in members if user.pk not in answered]


def proposal_waiting_since(head):
    """When ``head``'s current answers started: the last move, else creation."""
    return head.moved_at or head.created_at
