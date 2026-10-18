"""Stable public API for study-group membership services."""

from pods.models import POD_STATUS_CLOSED
from pods.services.member_actions import (
    approve_request,
    decline_request,
    join_pod,
    remove_member,
    remove_user_from_activity,
    remove_user_from_cohort,
    request_to_join,
    start_member_pod,
    withdraw_request,
)
from pods.services.membership_common import (
    MSG_MAX_MEMBERS_RETIRED,
    MSG_NOT_A_MEMBER,
    MSG_NOT_ELIGIBLE,
    MSG_REQUESTS_RETIRED,
    MSG_SELF_PACED,
    PodError,
    is_member,
    lock_pod,
    member_count,
    msg_count_below_used,
    pod_url,
    pods_list_url,
    pods_tab_url,
)
from pods.services.pod_fields import clean_pod_fields
from pods.services.staff_membership import AddResults, add_members_by_email, can_manage, create_staff_pod, update_pod

# Retain the private name for the existing PostgreSQL lock regression.
_lock = lock_pod

__all__ = [
    "AddResults",
    "MSG_MAX_MEMBERS_RETIRED",
    "MSG_NOT_A_MEMBER",
    "MSG_NOT_ELIGIBLE",
    "MSG_REQUESTS_RETIRED",
    "MSG_SELF_PACED",
    "POD_STATUS_CLOSED",
    "PodError",
    "add_members_by_email",
    "approve_request",
    "can_manage",
    "clean_pod_fields",
    "create_staff_pod",
    "decline_request",
    "is_member",
    "join_pod",
    "member_count",
    "msg_count_below_used",
    "pod_url",
    "pods_list_url",
    "pods_tab_url",
    "remove_member",
    "remove_user_from_activity",
    "remove_user_from_cohort",
    "request_to_join",
    "start_member_pod",
    "update_pod",
    "withdraw_request",
]
