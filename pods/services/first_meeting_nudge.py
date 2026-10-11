"""Dashboard ``Plan your first meeting`` rows (issue #1928).

A pod that has had two members for ``PODS_FIRST_MEETING_NUDGE_DAYS`` days
without a live meeting or a Slack channel gets one row in each member's
dashboard ``Your week`` list, linking to the pod's suggested times. On-site
only: no email, Slack message or bell notification.
"""

from datetime import timedelta

from django.db.models import Prefetch

from pods.models import POD_STATUS_ARCHIVED, Pod, PodMeeting, PodMembership
from pods.services import config as pods_config
from pods.services.meeting_rules import meeting_state
from pods.services.membership import pod_url

NUDGE_BODY = "No meeting planned yet. Pick a time from your pod's suggested times."


def nudge_title(pod):
    return f'Plan your first meeting with {pod.name}'


def dashboard_pod_nudges(user, now):
    """``Plan your first meeting`` rows for ``user``'s eligible pods.

    Eligible: the user is a current member; the pod is not archived and its
    course has pods switched on; it has 2+ members and the second earliest
    member joined at least ``PODS_FIRST_MEETING_NUDGE_DAYS`` days ago; it has
    no Slack channel link; and it has no live meeting (cancelled meetings
    and expired proposals do not count). Rows are ordered by that second
    join, oldest first. Three queries however many pods the user is in.
    """
    slugs = pods_config.enabled_course_slugs()
    if not slugs or not getattr(user, 'is_authenticated', False):
        return []
    cutoff = now - timedelta(days=pods_config.first_meeting_nudge_days())
    pods = list(
        Pod.objects.filter(memberships__user=user, cohort__course__slug__in=slugs, slack_channel_url='')
        .exclude(status=POD_STATUS_ARCHIVED)
        .select_related('cohort__course')
        .prefetch_related(Prefetch(
            'memberships',
            queryset=PodMembership.objects.only('pk', 'pod_id', 'joined_at').order_by('joined_at', 'pk'),
            to_attr='nudge_memberships',
        ))
        .distinct()
    )
    candidates = []
    for pod in pods:
        memberships = pod.nudge_memberships
        if len(memberships) < 2:
            continue
        second_joined = memberships[1].joined_at
        if second_joined > cutoff:
            continue
        candidates.append((second_joined, pod))
    if not candidates:
        return []
    meetings = {}
    for meeting in PodMeeting.objects.filter(pod__in=[pod for _joined, pod in candidates]).order_by('starts_at', 'pk'):
        meetings.setdefault(meeting.pod_id, []).append(meeting)
    rows = []
    for second_joined, pod in sorted(candidates, key=lambda item: (item[0], item[1].pk)):
        if meeting_state(pod, now, meetings=meetings.get(pod.pk, [])).used:
            continue
        rows.append({
            'pod_id': pod.pk,
            'title': nudge_title(pod),
            'body': NUDGE_BODY,
            'url': f'{pod_url(pod)}#suggested-times',
            'second_joined_at': second_joined,
        })
    return rows
