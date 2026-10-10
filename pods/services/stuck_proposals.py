"""Stuck pod meeting proposals (issue #1935).

A proposal is stuck when it is a live proposal head (``proposed``, start
still in the future, not expired; for a weekly series only the first
meeting counts, because answers live there), its pod is not archived, and
it has waited more than ``PODS_STUCK_PROPOSAL_HOURS`` hours since
``proposal_waiting_since`` (the last move, else creation: a move resets the
answers, so the clock restarts).

Who a proposal waits on comes from ``members_without_answer``, the same
helper as the member-side ``Waiting on`` line (issue #1934), so Studio, the
staff API, the daily staff Slack alert and the member page never disagree.
Studio, the API and the alert in ``pods.services.stale_requests`` all read
the rule from here.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

from pods.models import (
    MEETING_RESPONSE_CANT,
    MEETING_STATUS_PROPOSED,
    POD_STATUS_ARCHIVED,
    PodMeeting,
    PodMeetingResponse,
    PodMembership,
)
from pods.services import config as pods_config
from pods.services.meeting_rules import meeting_state, members_without_answer, proposal_waiting_since

__all__ = [
    'ProposalWait',
    'is_stuck_since',
    'pods_with_stuck_proposal',
    'proposal_heads',
    'proposal_waits',
    'stuck_proposals',
]


@dataclass
class ProposalWait:
    """One live proposal head: who it waits on and whether it is stuck."""

    head: PodMeeting
    waiting: list
    cant: list
    waiting_since: object
    stuck: bool


def is_stuck_since(waiting_since, now, hours):
    """``True`` when a proposal waiting since ``waiting_since`` is stuck."""
    return waiting_since is not None and now - waiting_since > timedelta(hours=hours)


def proposal_heads(state):
    """Live proposal heads in a :class:`MeetingState`, in start order.

    A weekly proposal is answered on its first meeting, so only that one
    counts; the later meetings of the series are never heads.
    """
    heads = []
    seen_series = set()
    for meeting in state.open_proposals:
        if meeting.series_id:
            if meeting.series_id in seen_series:
                continue
            seen_series.add(meeting.series_id)
        heads.append(meeting)
    return heads


def proposal_waits(pod, state, members, responses, *, hours):
    """``{head_pk: ProposalWait}`` for every live proposal head of ``pod``.

    ``members`` are the pod's users (order kept); ``responses`` are
    ``PodMeetingResponse`` rows of the pod's meetings (any meeting; only the
    heads' rows are used).
    """
    by_meeting = defaultdict(list)
    for response in responses:
        by_meeting[response.meeting_id].append(response)
    archived = pod.status == POD_STATUS_ARCHIVED
    waits = {}
    for head in proposal_heads(state):
        head_responses = by_meeting.get(head.pk, [])
        cant_ids = {r.user_id for r in head_responses if r.response == MEETING_RESPONSE_CANT}
        since = proposal_waiting_since(head)
        waits[head.pk] = ProposalWait(
            head=head,
            waiting=members_without_answer(head, members, head_responses),
            cant=[user for user in members if user.pk in cant_ids],
            waiting_since=since,
            stuck=not archived and is_stuck_since(since, state.now, hours),
        )
    return waits


def _proposed_by_pod(now, pod_ids=None):
    """Proposed meetings of non-archived pods that have a future proposal.

    Every proposed meeting of such a pod is loaded (not only future ones),
    so a weekly series whose first meeting already passed is still seen as
    expired by ``meeting_state``.
    """
    pods_with_future = PodMeeting.objects.filter(status=MEETING_STATUS_PROPOSED, starts_at__gt=now)
    if pod_ids is not None:
        pods_with_future = pods_with_future.filter(pod_id__in=pod_ids)
    meetings = (
        PodMeeting.objects.filter(
            status=MEETING_STATUS_PROPOSED,
            pod_id__in=pods_with_future.values('pod_id'),
        )
        .exclude(pod__status=POD_STATUS_ARCHIVED)
        .select_related('pod__cohort__course', 'proposed_by', 'moved_by')
        .order_by('starts_at', 'pk')
    )
    grouped = defaultdict(list)
    for meeting in meetings:
        grouped[meeting.pod_id].append(meeting)
    return grouped


def pods_with_stuck_proposal(pod_ids, *, now=None, hours=None):
    """The ids among ``pod_ids`` with at least one stuck proposal (one query)."""
    now = now or timezone.now()
    hours = pods_config.stuck_proposal_hours() if hours is None else hours
    stuck = set()
    for pod_id, meetings in _proposed_by_pod(now, pod_ids=list(pod_ids)).items():
        state = meeting_state(meetings[0].pod, now, meetings=meetings)
        if any(is_stuck_since(proposal_waiting_since(h), now, hours) for h in proposal_heads(state)):
            stuck.add(pod_id)
    return stuck


def stuck_proposals(*, now=None, hours=None):
    """Every stuck proposal as a :class:`ProposalWait`, oldest first.

    Three queries: proposed meetings, pod memberships and head answers.
    """
    now = now or timezone.now()
    hours = pods_config.stuck_proposal_hours() if hours is None else hours
    grouped = _proposed_by_pod(now)
    states = {
        pod_id: meeting_state(meetings[0].pod, now, meetings=meetings)
        for pod_id, meetings in grouped.items()
    }
    candidates = {
        pod_id: [h for h in proposal_heads(state) if is_stuck_since(proposal_waiting_since(h), now, hours)]
        for pod_id, state in states.items()
    }
    candidates = {pod_id: heads for pod_id, heads in candidates.items() if heads}
    if not candidates:
        return []
    members = defaultdict(list)
    for membership in (
        PodMembership.objects.filter(pod_id__in=list(candidates))
        .select_related('user')
        .order_by('joined_at', 'pk')
    ):
        members[membership.pod_id].append(membership.user)
    head_ids = [h.pk for heads in candidates.values() for h in heads]
    responses = list(PodMeetingResponse.objects.filter(meeting_id__in=head_ids))
    result = []
    for pod_id in candidates:
        state = states[pod_id]
        waits = proposal_waits(state.meetings[0].pod, state, members[pod_id], responses, hours=hours)
        result.extend(wait for wait in waits.values() if wait.stuck)
    result.sort(key=lambda wait: (wait.waiting_since, wait.head.pk))
    return result
