"""The ``Pods`` section on the sprint detail page (issue #1921).

Shown to enrolled members (and staff) of a non-draft, non-cancelled sprint
with ``pods_enabled`` on: the viewer's own pods with ``N of M members`` and
the next scheduled meeting in the viewer's timezone, plus ``N requests
waiting for you`` on the pods they own. Members are never named by email.
"""

from django.db.models import Count, Q
from django.utils import timezone

from pods.models import POD_STATUS_ARCHIVED, REQUEST_STATUS_PENDING, Pod, PodMembership
from pods.services.activity import sprint_activity
from pods.services.meeting_presentation import next_meeting_lines
from pods.services.meetings import zone_for
from pods.services.membership import pod_url

NEXT_MEETING_PREFIX = 'Next meeting:'


def requests_waiting_label(count):
    """``1 request waiting for you`` / ``3 requests waiting for you``."""
    return f'{count} request{"" if count == 1 else "s"} waiting for you'


def sprint_pods_section(sprint, user, *, now=None):
    """Context for the sprint page ``Pods`` section, or ``None`` to hide it."""
    activity = sprint_activity(sprint)
    if not getattr(user, 'is_authenticated', False):
        return None
    if not activity.member_pages_enabled() or not activity.is_visible_to_members():
        return None
    if not (user.is_staff or activity.is_participant(user)):
        return None
    now = now or timezone.now()
    # A subquery, not ``memberships__user``: filtering on the same join
    # would make ``Count('memberships')`` count only the viewer.
    own_pod_ids = PodMembership.objects.filter(user=user, pod__sprint=sprint).values('pod_id')
    pods = list(
        Pod.objects.filter(sprint=sprint, pk__in=own_pod_ids)
        .exclude(status=POD_STATUS_ARCHIVED)
        .select_related('sprint')
        .annotate(
            member_total=Count('memberships', distinct=True),
            pending_total=Count(
                'join_requests', filter=Q(join_requests__status=REQUEST_STATUS_PENDING), distinct=True,
            ),
        )
        .order_by('-created_at', '-pk')
    )
    lines = next_meeting_lines(pods, zone_for(user), now)
    rows = []
    for pod in pods:
        url = pod_url(pod)
        line = lines.get(pod.pk, '')
        waiting = pod.pending_total if pod.owner_id == user.pk else 0
        rows.append({
            'pod': pod,
            'url': url,
            'members_label': f'{pod.member_total} of {pod.max_members} members',
            # Only a scheduled meeting; proposal states stay on the pod page.
            'meeting_line': line if line.startswith(NEXT_MEETING_PREFIX) else '',
            'waiting_label': requests_waiting_label(waiting) if waiting else '',
            'waiting_url': f'{url}#requests' if waiting else '',
        })
    return {
        'browse_url': activity.list_url(),
        'subtitle': activity.copy['list_subtitle'],
        'rows': rows,
    }
