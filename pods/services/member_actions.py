"""Member-created study groups, direct joins, and departures."""

from django.db import transaction
from django.utils import timezone

from pods.models import (
    ACTIVE_MEETING_STATUSES,
    MEETING_STATUS_CANCELLED,
    MEMBERSHIP_SOURCE_CREATOR,
    OPEN_REQUEST_STATUSES,
    POD_SOURCE_MEMBER,
    POD_STATUS_ARCHIVED,
    POD_STATUS_OPEN,
    REQUEST_STATUS_CANCELLED,
    Pod,
    PodJoinRequest,
    PodMeeting,
    PodMeetingResponse,
    PodMembership,
)
from pods.services import config as pods_config
from pods.services.activity import activity_for_pod, as_activity, cohort_activity
from pods.services.membership_common import (
    MSG_NOT_A_MEMBER,
    MSG_REQUESTS_RETIRED,
    PodError,
    ensure_can_have_pods,
    join_member,
    lock_pod,
    msg_created_limit,
    msg_not_eligible,
)
from pods.services.pod_fields import clean_pod_fields


@transaction.atomic
def start_member_pod(user, activity, data):
    activity = as_activity(activity)
    ensure_can_have_pods(activity)
    if not activity.is_participant(user):
        raise PodError(msg_not_eligible(activity.noun), code="not_eligible")
    cleaned, errors = clean_pod_fields(data)
    if errors:
        first_field = next(iter(errors))
        raise PodError(errors[first_field], field=first_field)
    limit = pods_config.max_created_per_member()
    created = (
        Pod.objects.filter(
            created_by=user,
            source=POD_SOURCE_MEMBER,
            **activity.pod_filter(),
        )
        .exclude(status=POD_STATUS_ARCHIVED)
        .count()
    )
    if created >= limit:
        raise PodError(msg_created_limit(limit, activity.noun), code="pod_limit")
    return _create_member_pod(user, activity, cleaned)


def _create_member_pod(user, activity, cleaned):
    pod = Pod.objects.create(
        **activity.pod_filter(),
        owner=user,
        created_by=user,
        source=POD_SOURCE_MEMBER,
        **cleaned,
    )
    PodMembership.objects.create(
        pod=pod,
        user=user,
        added_by=user,
        source=MEMBERSHIP_SOURCE_CREATOR,
    )
    return pod


@transaction.atomic
def join_pod(pod, user):
    """Join an open activity group immediately and idempotently."""
    pod = lock_pod(pod)
    existing = pod.memberships.filter(user=user).first()
    if existing is not None:
        return existing, False
    if pod.status != POD_STATUS_OPEN:
        raise PodError("This study group is closed.", code="pod_not_open")
    activity = activity_for_pod(pod)
    if not activity.is_participant(user):
        raise PodError(msg_not_eligible(activity.noun), code="not_eligible")
    membership = join_member(pod, user)
    pod.join_requests.filter(user=user, status__in=OPEN_REQUEST_STATUSES).update(
        status=REQUEST_STATUS_CANCELLED,
        decided_at=timezone.now(),
    )
    return membership, True


@transaction.atomic
def request_to_join(pod, user, message=""):
    raise PodError(MSG_REQUESTS_RETIRED, code="gone")


@transaction.atomic
def approve_request(join_request, actor):
    raise PodError(MSG_REQUESTS_RETIRED, code="gone")


@transaction.atomic
def decline_request(join_request, actor):
    raise PodError(MSG_REQUESTS_RETIRED, code="gone")


@transaction.atomic
def withdraw_request(join_request, user):
    raise PodError(MSG_REQUESTS_RETIRED, code="gone")


@transaction.atomic
def remove_member(pod, user, *, actor=None):
    """Leave or remove a member, applying ownership and archive rules."""
    pod = lock_pod(pod)
    deleted, _ = pod.memberships.filter(user=user).delete()
    if not deleted:
        raise PodError(MSG_NOT_A_MEMBER, code="not_a_member")
    now = timezone.now()
    PodMeetingResponse.objects.filter(user=user, meeting__pod=pod, meeting__starts_at__gt=now).delete()
    remaining = list(pod.memberships.select_related("user").order_by("joined_at", "pk"))
    update_fields = []
    if pod.owner_id == user.pk:
        pod.owner = None
        if remaining:
            pod.owner = remaining[0].user
        update_fields.append("owner")
    if not remaining and pod.source == POD_SOURCE_MEMBER:
        _archive_empty_member_pod(pod, now, update_fields)
    if update_fields:
        update_fields.append("updated_at")
        pod.save(update_fields=update_fields)
    return pod


def _archive_empty_member_pod(pod, now, update_fields):
    pod.status = POD_STATUS_ARCHIVED
    update_fields.append("status")
    pod.join_requests.filter(status__in=OPEN_REQUEST_STATUSES).update(
        status=REQUEST_STATUS_CANCELLED,
        decided_at=now,
    )
    PodMeeting.objects.filter(
        pod=pod,
        status__in=ACTIVE_MEETING_STATUSES,
        starts_at__gt=now,
    ).update(status=MEETING_STATUS_CANCELLED, status_changed_at=now, updated_at=now)


def remove_user_from_activity(user, activity):
    activity = as_activity(activity)
    for pod in Pod.objects.filter(memberships__user=user, **activity.pod_filter()).distinct():
        remove_member(pod, user)
    PodJoinRequest.objects.filter(
        user=user,
        status__in=OPEN_REQUEST_STATUSES,
        **activity.pod_filter("pod__"),
    ).update(status=REQUEST_STATUS_CANCELLED, decided_at=timezone.now())


def remove_user_from_cohort(user, cohort):
    remove_user_from_activity(user, cohort_activity(cohort))
