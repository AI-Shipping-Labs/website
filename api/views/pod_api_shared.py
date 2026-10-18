"""Shared wire contract, serializers, and dependencies for the Pods API."""

from zoneinfo import ZoneInfo

from django.db.models import Count
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from accounts.services.email_resolution import normalize_email, resolve_users_by_emails
from api.openapi import openapi_spec
from api.safety import error_response
from api.serializers.datetime import isoformat_or_none
from api.utils import body_must_be_object_response, parse_json_body, require_methods, validation_response
from api.views.pod_api_examples import (
    _ERR_LIMIT,
    _ERR_UNKNOWN_POD,
    _ERR_UNKNOWN_SPRINT,
    _MEETING_EXAMPLE,
    _POD_DETAIL_EXAMPLE,
    _POD_SUMMARY_EXAMPLE,
    _REQUEST_EXAMPLE,
    _RESULTS_EXAMPLE,
    _ROSTER_EXAMPLE,
)
from api.views.pod_api_serializers import (
    get_pod as _get_pod,
)
from api.views.pod_api_serializers import (
    pod_queryset as _pod_queryset,
)
from api.views.pod_api_serializers import (
    serialize_detail as _serialize_detail,
)
from api.views.pod_api_serializers import (
    serialize_meetings as _serialize_meetings,
)
from api.views.pod_api_serializers import (
    serialize_request as _serialize_request,
)
from api.views.pod_api_serializers import (
    serialize_summary as _serialize_summary,
)
from content.models import CohortEnrollment, Course
from content.services.course_cohorts import get_course_cohort_by_key
from crm.models import CRMRecord
from payments.models import Membership, TierOverride
from plans.models import Sprint, SprintEnrollment
from pods.models import (
    ARTIFACT_KIND_CHOICES,
    MEETING_STATUS_CANCELLED,
    MEETING_STATUS_CHOICES,
    POD_SOURCE_API,
    POD_STATUS_CHOICES,
    REQUEST_STATUS_CHOICES,
    AvailabilityProfile,
    Pod,
    PodArtifact,
    PodMeeting,
    PodMembership,
    PodMessage,
)
from pods.services import chat as chat_svc
from pods.services import config as pods_config
from pods.services import meetings as mtg
from pods.services import membership as svc
from pods.services.activity import cohort_activity, sprint_activity
from pods.services.availability import format_minute
from pods.services.meeting_rules import meeting_end, meeting_state
from pods.services.people import display_name
from pods.services.slack import clean_channel_url
from pods.services.stuck_proposals import proposal_waits
from pods.services.suggestions import load_member_availability, suggest_slots

TAG = "Pods"
DEFAULT_LIMIT = 200
MAX_LIMIT = 500
POD_LIST_DEFAULT_LIMIT = 50
POD_LIST_MAX_LIMIT = 200

_POD_WRITABLE = (
    "name",
    "purpose",
    "scheduling_note",
    "internal_notes",
    "max_members",
    "meeting_count",
    "meeting_minutes",
    "status",
    "owner_email",
    "slack_channel_url",
    "meeting_url",
)
_MEETING_STATUS_VALUES = [value for value, _label in MEETING_STATUS_CHOICES]
_MEETING_CREATE_FIELDS = ("starts_at", "timezone", "repeat_weekly", "count")
_MEETING_PATCH_FIELDS = ("starts_at", "timezone", "move_later", "status")
_MEETING_PATCH_STATUSES = ["scheduled", "held", "cancelled"]
_CONFLICT_CODES = ("request_not_open", mtg.CODE_LIMIT, mtg.CODE_NOT_MOVABLE, mtg.CODE_PROPOSAL_EXISTS)
_STATUS_VALUES = [value for value, _label in POD_STATUS_CHOICES]
_REQUEST_STATUS_VALUES = [value for value, _label in REQUEST_STATUS_CHOICES]


def _parse_limit(request, default, maximum):
    """Return ``(limit, offset, error_response)``."""
    try:
        limit = int(request.GET.get("limit", default))
        offset = int(request.GET.get("offset", 0))
    except (TypeError, ValueError):
        return (
            None,
            None,
            validation_response(
                {"field": "limit", "expected": "integer"},
                message="limit and offset must be integers",
            ),
        )
    if limit < 1 or limit > maximum or offset < 0:
        return (
            None,
            None,
            validation_response(
                {"field": "limit", "maximum": maximum},
                message=f"limit must be between 1 and {maximum}; offset must be 0 or more",
            ),
        )
    return limit, offset, None


def _unknown_pod():
    return error_response("Pod not found", "unknown_pod", status=404)


def _unknown_sprint():
    return error_response("Sprint not found", "unknown_sprint", status=404)


def _pod_error_response(exc):
    status = 422
    if exc.code in _CONFLICT_CODES:
        status = 409
    if exc.code == "not_a_member":
        status = 404
    details = None
    if exc.field:
        details = {"field": exc.field}
    return error_response(exc.message, exc.code, status=status, details=details)


__all__ = (
    "ZoneInfo",
    "Count",
    "HttpResponse",
    "JsonResponse",
    "timezone",
    "csrf_exempt",
    "token_required",
    "normalize_email",
    "resolve_users_by_emails",
    "openapi_spec",
    "error_response",
    "isoformat_or_none",
    "body_must_be_object_response",
    "parse_json_body",
    "require_methods",
    "validation_response",
    "CohortEnrollment",
    "Course",
    "get_course_cohort_by_key",
    "CRMRecord",
    "Membership",
    "TierOverride",
    "Sprint",
    "SprintEnrollment",
    "ARTIFACT_KIND_CHOICES",
    "MEETING_STATUS_CANCELLED",
    "MEETING_STATUS_CHOICES",
    "POD_SOURCE_API",
    "POD_STATUS_CHOICES",
    "REQUEST_STATUS_CHOICES",
    "AvailabilityProfile",
    "Pod",
    "PodArtifact",
    "PodMeeting",
    "PodMembership",
    "PodMessage",
    "chat_svc",
    "pods_config",
    "mtg",
    "svc",
    "cohort_activity",
    "sprint_activity",
    "format_minute",
    "meeting_end",
    "meeting_state",
    "display_name",
    "clean_channel_url",
    "proposal_waits",
    "load_member_availability",
    "suggest_slots",
    "TAG",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "POD_LIST_DEFAULT_LIMIT",
    "POD_LIST_MAX_LIMIT",
    "_POD_WRITABLE",
    "_MEETING_STATUS_VALUES",
    "_MEETING_CREATE_FIELDS",
    "_MEETING_PATCH_FIELDS",
    "_MEETING_PATCH_STATUSES",
    "_CONFLICT_CODES",
    "_STATUS_VALUES",
    "_REQUEST_STATUS_VALUES",
    "_RESULTS_EXAMPLE",
    "_POD_SUMMARY_EXAMPLE",
    "_MEETING_EXAMPLE",
    "_POD_DETAIL_EXAMPLE",
    "_REQUEST_EXAMPLE",
    "_ROSTER_EXAMPLE",
    "_ERR_UNKNOWN_POD",
    "_ERR_UNKNOWN_SPRINT",
    "_ERR_LIMIT",
    "_parse_limit",
    "_pod_queryset",
    "_get_pod",
    "_unknown_pod",
    "_unknown_sprint",
    "_serialize_summary",
    "_serialize_request",
    "_serialize_detail",
    "_serialize_meetings",
    "_pod_error_response",
)
