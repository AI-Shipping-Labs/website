"""Public study-group summaries and list-row presentation."""

from dataclasses import dataclass
from types import SimpleNamespace

from django.utils import timezone

from pods.models import POD_STATUS_ARCHIVED, POD_STATUS_OPEN, Pod
from pods.services.activity import activity_for_pod, as_activity, cohort_activity
from pods.services.membership import pod_url
from pods.services.people import offset_range_label
from pods.services.suggestions import load_member_availability

STATE_MEMBER = "member"
STATE_OPEN = "open"
STATE_CLOSED = "closed"


@dataclass
class ViewerState:
    key: str
    badge: str
    tone: str
    action_label: str
    action_url: str


def public_pod(pod):
    """Return the member-safe fields and omit staff-only internal notes."""
    return SimpleNamespace(
        pk=pod.pk,
        name=pod.name,
        purpose=pod.purpose,
        scheduling_note=pod.scheduling_note,
        status=pod.status,
        meeting_count=pod.meeting_count,
        meeting_minutes=pod.meeting_minutes,
        owner_id=pod.owner_id,
        cohort=pod.cohort,
        cohort_id=pod.cohort_id,
        sprint=pod.sprint,
        sprint_id=pod.sprint_id,
        created_at=pod.created_at,
    )


def member_count_label(count):
    if count == 1:
        return "1 member"
    return f"{count} members"


def meta_line(pod, count):
    if pod.meeting_count == 1:
        meeting_label = f"Plan: 1 meeting × {pod.meeting_minutes} min"
    else:
        meeting_label = f"Plan: {pod.meeting_count} meetings × {pod.meeting_minutes} min each"
    return f"{member_count_label(count)} · {meeting_label}"


def pods_url(course, cohort):
    """The course Study groups tab for ``cohort``."""
    return cohort_activity(cohort).list_url()


def viewer_state(pod, viewer, *, is_member, can_join):
    activity = activity_for_pod(pod)
    if is_member:
        return ViewerState(STATE_MEMBER, "Member", "success", "Leave", activity.action_url(pod, "pod_leave"))
    if pod.status != POD_STATUS_OPEN:
        return ViewerState(STATE_CLOSED, "Closed", "muted", "Closed", "")
    action_label = "Open"
    action_url = pod_url(pod)
    if can_join:
        action_label = "Join"
        action_url = activity.action_url(pod, "pod_join")
    return ViewerState(STATE_OPEN, "Open", "neutral", action_label, action_url)


def _timezone_label(zones):
    if not zones:
        return "Time zones not set"
    return zones.removeprefix("Members in ")


def fit_line(hours):
    if hours is None:
        return ""
    if hours <= 0:
        return "No overlap with you yet"
    return f"Overlaps with you {hours} h a week"


def _pods_for_activity(activity):
    return list(
        Pod.objects.filter(**activity.pod_filter())
        .exclude(status=POD_STATUS_ARCHIVED)
        .select_related("owner", "cohort__course", "sprint")
        .prefetch_related("memberships__user")
        .order_by("-created_at", "-pk")
    )


def _availability_for_pods(pods, viewer):
    users = {viewer.pk: viewer}
    for pod in pods:
        for membership in pod.memberships.all():
            users.setdefault(membership.user_id, membership.user)
    return load_member_availability(users.values())


def _pod_row(pod, viewer, availability, can_join, now):
    members = [membership.user for membership in pod.memberships.all()]
    member_ids = {user.pk for user in members}
    count = len(members)
    state = viewer_state(pod, viewer, is_member=viewer.pk in member_ids, can_join=can_join)
    zones = offset_range_label([availability[user.pk].timezone_name for user in members], now)
    return {
        "pod": public_pod(pod),
        "count": count,
        "detail_url": pod_url(pod),
        "member_label": member_count_label(count),
        "timezone_label": _timezone_label(zones),
        "state": state,
    }


def _row_sort_key(row):
    group = 1
    if row["state"].key == STATE_MEMBER:
        group = 0
    elif row["pod"].status != POD_STATUS_OPEN:
        group = 2
    return group, -row["pod"].created_at.timestamp(), -row["pod"].pk


def build_pod_rows(activity, viewer, *, now=None):
    """Rows ordered by memberships, open groups, then closed groups."""
    if now is None:
        now = timezone.now()
    activity = as_activity(activity)
    pods = _pods_for_activity(activity)
    availability = _availability_for_pods(pods, viewer)
    can_join = activity.is_participant(viewer)
    rows = []
    for pod in pods:
        rows.append(_pod_row(pod, viewer, availability, can_join, now))
    rows.sort(key=_row_sort_key)
    return rows, availability.get(viewer.pk)

