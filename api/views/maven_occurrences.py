"""Staff-token API for Maven enrollment occurrence diagnosis and retry."""

import logging

from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from accounts.services.email_resolution import resolve_user_by_email
from api.openapi import openapi_spec
from api.safety import error_response
from api.serializers.maven import serialize_maven_occurrence
from api.utils import require_methods
from community.models import CommunityAuditLog
from integrations.models import MavenEnrollmentEvent
from integrations.services.maven import STEP_NAMES, retry_occurrence_step
from integrations.services.maven_attention import (
    failed_occurrences,
    needs_attention_occurrences,
)

logger = logging.getLogger(__name__)

LIFECYCLES = tuple(value for value, _label in MavenEnrollmentEvent.LIFECYCLE_CHOICES)
STATUSES = ("all", "failed", "needs_attention")
DEFAULT_LIMIT = 50
MAX_LIMIT = 200

_ERROR_SCHEMA = {"$ref": "#/components/schemas/ErrorResponse"}
_NULLABLE_DATETIME = {"type": ["string", "null"], "format": "date-time"}
_USER_SCHEMA = {
    "type": ["object", "null"],
    "properties": {
        "id": {"type": "integer"},
        "email": {"type": "string", "format": "email"},
    },
}
_SUMMARY_PROPERTIES = {
    "id": {"type": "integer"},
    "user": _USER_SCHEMA,
    "occurrence_email": {"type": "string", "format": "email"},
    "course": {"type": "string"},
    "cohort": {"type": "string"},
    "course_key": {"type": "string"},
    "cohort_key": {"type": "string"},
    "event_type": {"type": "string"},
    "lifecycle": {"type": "string", "enum": list(LIFECYCLES)},
    "outcome": {"type": "string"},
    "failed_steps": {
        "type": "array",
        "items": {"type": "string", "enum": list(STEP_NAMES)},
    },
    "needs_attention_steps": {
        "type": "array",
        "items": {"type": "string", "enum": list(STEP_NAMES)},
    },
    "payload_redacted_at": _NULLABLE_DATETIME,
    "created_at": _NULLABLE_DATETIME,
    "updated_at": _NULLABLE_DATETIME,
}
_SUMMARY_SCHEMA = {
    "type": "object",
    "required": list(_SUMMARY_PROPERTIES),
    "properties": _SUMMARY_PROPERTIES,
}
_STEP_SCHEMA = {
    "type": "object",
    "required": [
        "name",
        "status",
        "attempts",
        "attempted_at",
        "completed_at",
        "needs_attention",
        "last_error",
        "error_redacted",
    ],
    "properties": {
        "name": {"type": "string", "enum": list(STEP_NAMES)},
        "status": {
            "type": "string",
            "enum": [value for value, _label in MavenEnrollmentEvent.STEP_CHOICES],
        },
        "attempts": {"type": "integer"},
        "attempted_at": _NULLABLE_DATETIME,
        "completed_at": _NULLABLE_DATETIME,
        "needs_attention": {"type": "boolean"},
        "last_error": {"type": "string"},
        "error_redacted": {"type": "boolean"},
    },
}
_DETAIL_PROPERTIES = {
    **_SUMMARY_PROPERTIES,
    "account_created": {"type": "boolean"},
    "welcome_eligible": {"type": "boolean"},
    "removed_at": _NULLABLE_DATETIME,
    "steps": {"type": "array", "items": _STEP_SCHEMA},
}
_DETAIL_SCHEMA = {
    "type": "object",
    "required": list(_DETAIL_PROPERTIES),
    "properties": _DETAIL_PROPERTIES,
}
_RETRY_SCHEMA = {
    "type": "object",
    "required": ["retry", "occurrence"],
    "properties": {
        "retry": {
            "type": "object",
            "required": ["step", "outcome", "attempted"],
            "properties": {
                "step": {"type": "string", "enum": list(STEP_NAMES)},
                "outcome": {
                    "type": "string",
                    "enum": ["succeeded", "failed", "skipped"],
                },
                "attempted": {"type": "boolean"},
            },
        },
        "occurrence": _DETAIL_SCHEMA,
    },
}


def _error_spec(description, code, *, details=None):
    example = {"error": description, "code": code}
    if details:
        example["details"] = details
    return {
        "description": description,
        "schema": _ERROR_SCHEMA,
        "example": example,
    }


_AUTH_ERROR_SPEC = _error_spec(
    "Missing or invalid staff-owned operator token.",
    "invalid_token",
)
_METHOD_ERROR_SPEC = _error_spec("Method not allowed.", "method_not_allowed")
_NOT_FOUND_SPEC = _error_spec(
    "Maven occurrence not found.",
    "maven_occurrence_not_found",
)


@token_required(structured_errors=True)
@csrf_exempt
@require_methods("GET", structured_errors=True)
@openapi_spec(
    tag="Maven Integrations",
    summary="List Maven enrollment occurrences",
    methods={
        "GET": {
            "description": (
                "Return a deterministic newest-first page from the Maven "
                "enrollment ledger. Filters combine with AND. Email lookup "
                "is exact and alias-aware; redacted occurrence email is never "
                "reconstructed from the linked account."
            ),
            "query": {
                "email": {
                    "type": "string",
                    "format": "email",
                    "description": "Exact occurrence, primary, or alias email.",
                },
                "course": {
                    "type": "string",
                    "description": "Course label substring or exact course key.",
                },
                "cohort": {
                    "type": "string",
                    "description": "Cohort label substring or exact cohort key.",
                },
                "lifecycle": {"type": "string", "enum": list(LIFECYCLES)},
                "status": {
                    "type": "string",
                    "enum": list(STATUSES),
                    "default": "all",
                },
                "failed_step": {"type": "string", "enum": list(STEP_NAMES)},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_LIMIT,
                    "default": DEFAULT_LIMIT,
                },
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
            "responses": {
                200: {
                    "description": "Filtered Maven occurrence page.",
                    "schema": {
                        "type": "object",
                        "required": [
                            "occurrences",
                            "count",
                            "total_count",
                            "limit",
                            "offset",
                        ],
                        "properties": {
                            "occurrences": {
                                "type": "array",
                                "items": _SUMMARY_SCHEMA,
                            },
                            "count": {"type": "integer"},
                            "total_count": {"type": "integer"},
                            "limit": {"type": "integer"},
                            "offset": {"type": "integer"},
                        },
                    },
                },
                401: _AUTH_ERROR_SPEC,
                405: _METHOD_ERROR_SPEC,
                422: _error_spec(
                    "Invalid filter or pagination value.",
                    "validation_error",
                    details={"field": "status"},
                ),
            },
        }
    },
)
def maven_occurrences_collection(request):
    """GET ``/api/integrations/maven/occurrences``."""
    parsed, error = _parse_list_filters(request)
    if error is not None:
        return error

    now = timezone.now()
    queryset = MavenEnrollmentEvent.objects.select_related("user")
    email = parsed["email"]
    if email:
        email_match = Q(email__iexact=email, payload_redacted_at__isnull=True)
        user = resolve_user_by_email(email)
        if user is not None:
            email_match |= Q(user_id=user.pk)
        queryset = queryset.filter(email_match)
    if parsed["course"]:
        queryset = queryset.filter(
            Q(course__icontains=parsed["course"])
            | Q(course_key__iexact=parsed["course"])
        )
    if parsed["cohort"]:
        queryset = queryset.filter(
            Q(cohort__icontains=parsed["cohort"])
            | Q(cohort_key__iexact=parsed["cohort"])
        )
    if parsed["lifecycle"]:
        queryset = queryset.filter(lifecycle=parsed["lifecycle"])
    if parsed["status"] == "failed":
        queryset = failed_occurrences(queryset)
    elif parsed["status"] == "needs_attention":
        queryset = needs_attention_occurrences(queryset, now=now)
    if parsed["failed_step"]:
        queryset = queryset.filter(
            **{
                f"{parsed['failed_step']}_status": MavenEnrollmentEvent.STEP_FAILED
            }
        )

    queryset = queryset.order_by("-created_at", "-id")
    total_count = queryset.count()
    limit = parsed["limit"]
    offset = parsed["offset"]
    page = list(queryset[offset : offset + limit])
    return JsonResponse(
        {
            "occurrences": [
                serialize_maven_occurrence(occurrence, now=now)
                for occurrence in page
            ],
            "count": len(page),
            "total_count": total_count,
            "limit": limit,
            "offset": offset,
        }
    )


@token_required(structured_errors=True)
@csrf_exempt
@require_methods("GET", structured_errors=True)
@openapi_spec(
    tag="Maven Integrations",
    summary="Inspect one Maven enrollment occurrence",
    methods={
        "GET": {
            "description": (
                "Return the current persisted occurrence and all five ledger "
                "steps. This operation is read-only and makes no provider calls."
            ),
            "responses": {
                200: {"description": "Full Maven occurrence detail.", "schema": _DETAIL_SCHEMA},
                401: _AUTH_ERROR_SPEC,
                404: _NOT_FOUND_SPEC,
                405: _METHOD_ERROR_SPEC,
            },
        }
    },
)
def maven_occurrence_detail(request, occurrence_id):
    """GET ``/api/integrations/maven/occurrences/<id>``."""
    occurrence = (
        MavenEnrollmentEvent.objects.select_related("user")
        .filter(pk=occurrence_id)
        .first()
    )
    if occurrence is None:
        return _occurrence_not_found()
    return JsonResponse(serialize_maven_occurrence(occurrence, detail=True))


@token_required(structured_errors=True)
@csrf_exempt
@require_methods("POST", structured_errors=True)
@openapi_spec(
    tag="Maven Integrations",
    summary="Retry one Maven enrollment step",
    methods={
        "POST": {
            "description": (
                "Force one incomplete step through the shared durable Maven "
                "retry service. The URL fully identifies the operation; no "
                "request body or caller-controlled retry options are accepted. "
                "A caught provider failure is a completed request with "
                "``retry.outcome=failed``."
            ),
            "responses": {
                200: {
                    "description": "Selected attempt completed with persisted outcome.",
                    "schema": _RETRY_SCHEMA,
                },
                401: _AUTH_ERROR_SPEC,
                404: _NOT_FOUND_SPEC,
                405: _METHOD_ERROR_SPEC,
                409: {
                    "description": (
                        "``maven_step_in_progress`` for a fresh running lease; "
                        "``maven_step_not_retryable`` for a step already "
                        "persisted as succeeded or skipped."
                    ),
                    "schema": _ERROR_SCHEMA,
                    "example": {
                        "error": "Maven step is already in progress",
                        "code": "maven_step_in_progress",
                        "details": {
                            "step": "welcome",
                            "status": "running",
                            "attempts": 2,
                        },
                    },
                },
                422: _error_spec(
                    "Unknown Maven step.",
                    "invalid_maven_step",
                    details={"allowed": sorted(STEP_NAMES)},
                ),
                500: _error_spec(
                    "Maven step retry failed before an outcome was persisted.",
                    "maven_step_retry_failed",
                ),
            },
        }
    },
)
def maven_occurrence_step_retry(request, occurrence_id, step):
    """POST one force-retry identified entirely by the URL."""
    if step not in STEP_NAMES:
        return error_response(
            "Unknown Maven step",
            "invalid_maven_step",
            status=422,
            details={"allowed": sorted(STEP_NAMES)},
        )

    occurrence = (
        MavenEnrollmentEvent.objects.select_related("user")
        .filter(pk=occurrence_id)
        .first()
    )
    if occurrence is None:
        return _occurrence_not_found()

    try:
        result = retry_occurrence_step(occurrence, step)
    except Exception:
        logger.error(
            "Maven API retry failed before a persisted outcome occurrence=%s step=%s",
            occurrence.pk,
            step,
        )
        _write_retry_audit(request, occurrence, step, "unexpected_error")
        return error_response(
            "Maven step retry failed",
            "maven_step_retry_failed",
            status=500,
        )

    occurrence.refresh_from_db()
    audit_outcome = result.reason or result.outcome
    _write_retry_audit(request, occurrence, step, audit_outcome)

    if result.reason == "in_progress":
        return _retry_conflict(
            occurrence,
            step,
            code="maven_step_in_progress",
            message="Maven step is already in progress",
        )
    if result.reason == "not_retryable":
        return _retry_conflict(
            occurrence,
            step,
            code="maven_step_not_retryable",
            message="Maven step is already complete and cannot be retried",
        )

    return JsonResponse(
        {
            "retry": {
                "step": result.step,
                "outcome": result.outcome,
                "attempted": result.attempted,
            },
            "occurrence": serialize_maven_occurrence(occurrence, detail=True),
        }
    )


def _parse_list_filters(request):
    values = {
        "email": (request.GET.get("email") or "").strip(),
        "course": (request.GET.get("course") or "").strip(),
        "cohort": (request.GET.get("cohort") or "").strip(),
        "lifecycle": (request.GET.get("lifecycle") or "").strip(),
        "status": (request.GET.get("status") or "all").strip(),
        "failed_step": (request.GET.get("failed_step") or "").strip(),
    }
    for field, allowed in (
        ("lifecycle", LIFECYCLES),
        ("status", STATUSES),
        ("failed_step", STEP_NAMES),
    ):
        if values[field] and values[field] not in allowed:
            return None, error_response(
                f"Invalid {field}",
                "validation_error",
                status=422,
                details={"field": field, "allowed": list(allowed)},
            )

    limit, error = _parse_integer_query(
        request.GET.get("limit"),
        field="limit",
        default=DEFAULT_LIMIT,
        minimum=1,
    )
    if error is not None:
        return None, error
    offset, error = _parse_integer_query(
        request.GET.get("offset"), field="offset", default=0, minimum=0
    )
    if error is not None:
        return None, error
    values["limit"] = min(limit, MAX_LIMIT)
    values["offset"] = offset
    return values, None


def _parse_integer_query(raw, *, field, default, minimum):
    if raw is None or raw == "":
        return default, None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = None
    if value is None or value < minimum:
        return None, error_response(
            f"Invalid {field}",
            "validation_error",
            status=422,
            details={"field": field},
        )
    return value, None


def _occurrence_not_found():
    return error_response(
        "Maven occurrence not found",
        "maven_occurrence_not_found",
        status=404,
    )


def _retry_conflict(occurrence, step, *, code, message):
    return error_response(
        message,
        code,
        status=409,
        details={
            "step": step,
            "status": getattr(occurrence, f"{step}_status"),
            "attempts": getattr(occurrence, f"{step}_attempts"),
        },
    )


def _write_retry_audit(request, occurrence, step, outcome):
    token = request.auth_token
    actor = (token.name or "").strip() or token.key_prefix
    subject_id = occurrence.user_id or "unknown"
    CommunityAuditLog.objects.create(
        user=occurrence.user or request.user,
        action="maven_step_retry",
        details=(
            f"occurrence_id={occurrence.pk} step={step} outcome={outcome} "
            f"subject_user_id={subject_id} actor_token={actor}"
        ),
    )
