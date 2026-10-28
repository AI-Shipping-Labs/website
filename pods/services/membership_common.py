"""Shared study-group membership errors, URLs, and lock helpers."""

from urllib.parse import urlencode

from django.urls import reverse
from django.utils import timezone

from pods.models import (
    MEMBERSHIP_SOURCE_MEMBER,
    MEMBERSHIP_SOURCE_STAFF,
    OPEN_REQUEST_STATUSES,
    POD_SOURCE_STUDIO,
    REQUEST_STATUS_CANCELLED,
    Pod,
    PodMembership,
)
from pods.services.activity import activity_for_pod

MSG_SELF_PACED = "Pods need a dated cohort. A self-paced cohort cannot have pods."
MSG_NOT_ELIGIBLE = "Only members of this cohort can join its pods."
MSG_NOT_ELIGIBLE_SPRINT = "Only members of this sprint can join its pods."
MSG_NOT_A_MEMBER = "That person is not a member of this pod."
MSG_REQUESTS_RETIRED = "Study group join requests are no longer available."
MSG_MAX_MEMBERS_RETIRED = "Study groups no longer have a size limit."


class PodError(Exception):
    """A rule violation with a member-facing message and a stable code."""

    def __init__(self, message, code="validation_error", field=""):
        super().__init__(message)
        self.message = message
        self.code = code
        self.field = field


def msg_not_eligible(noun="cohort"):
    if noun == "sprint":
        return MSG_NOT_ELIGIBLE_SPRINT
    return MSG_NOT_ELIGIBLE


def msg_created_limit(limit, noun="cohort"):
    return f"You can start up to {limit} pods in this {noun}."


def msg_owner_not_enrolled(email, noun="cohort"):
    return f"{email} is not enrolled in this {noun}."


def msg_count_below_used(used):
    return f"This pod already has {used} meetings planned or held. Cancel a meeting before lowering the number."


def pod_url(pod):
    return activity_for_pod(pod).pod_url(pod)


def pods_tab_url(cohort):
    url = reverse("course_pods", kwargs={"slug": cohort.course.slug})
    if cohort.external_key:
        return f"{url}?{urlencode({'cohort': cohort.external_key})}"
    return url


def pods_list_url(pod):
    return activity_for_pod(pod).list_url()


def member_count(pod):
    return pod.memberships.count()


def is_member(pod, user):
    if not getattr(user, "is_authenticated", False):
        return False
    return pod.memberships.filter(user=user).exists()


def lock_pod(pod):
    """Lock only the pod row; nullable joins cannot be locked on PostgreSQL."""
    return (
        Pod.objects.select_for_update(of=("self",)).select_related("cohort__course", "sprint", "owner").get(pk=pod.pk)
    )


def ensure_can_have_pods(activity):
    if not activity.can_have_pods():
        raise PodError(MSG_SELF_PACED, code="validation_error", field="cohort")


def membership_source(source):
    if source == POD_SOURCE_STUDIO:
        return MEMBERSHIP_SOURCE_STAFF
    return source


def add_member(pod, user, *, actor, source):
    added_by = None
    if getattr(actor, "is_authenticated", False):
        added_by = actor
    membership = PodMembership.objects.create(
        pod=pod,
        user=user,
        added_by=added_by,
        source=membership_source(source),
    )
    pod.join_requests.filter(user=user, status__in=OPEN_REQUEST_STATUSES).update(
        status=REQUEST_STATUS_CANCELLED,
        decided_at=timezone.now(),
    )
    return membership


def join_member(pod, user):
    return PodMembership.objects.create(
        pod=pod,
        user=user,
        added_by=user,
        source=MEMBERSHIP_SOURCE_MEMBER,
    )
