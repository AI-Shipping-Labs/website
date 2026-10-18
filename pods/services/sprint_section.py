"""The ``Pods`` section on the sprint detail page (issue #1921).

Shown to enrolled members (and staff) of a non-draft, non-cancelled sprint
with ``pods_enabled`` on: the viewer's own groups with a member count and
the next scheduled meeting in the viewer's timezone. Members are never
named by email.
"""

from django.db.models import Count
from django.utils import timezone

from pods.models import POD_STATUS_ARCHIVED, Pod, PodMembership
from pods.services.activity import sprint_activity
from pods.services.meeting_presentation import next_meeting_lines
from pods.services.meetings import zone_for
from pods.services.membership import pod_url
from pods.services.presentation import member_count_label, public_pod

NEXT_MEETING_PREFIX = "Next meeting:"


def _visible_activity(sprint, user):
    activity = sprint_activity(sprint)
    if not getattr(user, "is_authenticated", False):
        return None
    if not activity.member_pages_enabled() or not activity.is_visible_to_members():
        return None
    if not (user.is_staff or activity.is_participant(user)):
        return None
    return activity


def _own_pods(sprint, user):
    # A subquery, not ``memberships__user``: filtering on the same join
    # would make ``Count('memberships')`` count only the viewer.
    own_pod_ids = PodMembership.objects.filter(user=user, pod__sprint=sprint).values("pod_id")
    return list(
        Pod.objects.filter(sprint=sprint, pk__in=own_pod_ids)
        .exclude(status=POD_STATUS_ARCHIVED)
        .select_related("sprint")
        .annotate(member_total=Count("memberships", distinct=True))
        .order_by("-created_at", "-pk")
    )


def _pod_rows(pods, user, now):
    lines = next_meeting_lines(pods, zone_for(user), now)
    rows = []
    for pod in pods:
        url = pod_url(pod)
        line = lines.get(pod.pk, "")
        meeting_line = ""
        if line.startswith(NEXT_MEETING_PREFIX):
            meeting_line = line
        rows.append(
            {
                "pod": public_pod(pod),
                "url": url,
                "members_label": member_count_label(pod.member_total),
                # Only a scheduled meeting; proposal states stay on the pod page.
                "meeting_line": meeting_line,
            }
        )
    return rows


def sprint_pods_section(sprint, user, *, now=None):
    """Context for the sprint page ``Pods`` section, or ``None`` to hide it."""
    activity = _visible_activity(sprint, user)
    if activity is None:
        return None
    now = now or timezone.now()
    rows = _pod_rows(_own_pods(sprint, user), user, now)
    return {
        "browse_url": activity.list_url(),
        "subtitle": activity.copy["list_subtitle"],
        "rows": rows,
    }
