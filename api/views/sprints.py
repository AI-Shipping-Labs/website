"""Sprint endpoints for the plans API (issue #433).

Five endpoints:

- ``GET /api/sprints/`` -- list, optional ``status`` filter
- ``POST /api/sprints/`` -- create (staff-only)
- ``GET /api/sprints/<slug>/`` -- detail
- ``PATCH /api/sprints/<slug>/`` -- update (staff-only)
- ``DELETE /api/sprints/<slug>/`` -- delete (staff-only); 409 if plans attached
"""

from datetime import date

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.serializers.plans import serialize_sprint
from api.utils import parse_json_body, require_methods
from api.views._permissions import bearer_is_admin, visible_plans_for
from plans.models import SPRINT_STATUS_CHOICES, Sprint
from studio.views.sprints import _parse_event_series

VALID_SPRINT_STATUSES = {choice for choice, _label in SPRINT_STATUS_CHOICES}

# Issue #864 (human decision, 2026-06-13): sprint hard-delete is not available
# through the API. The DELETE method is accepted but returns 405 pointing the
# operator to Studio, matching the events/event_series guard pattern.
SPRINT_DELETE_NOT_AVAILABLE_MESSAGE = (
    "Sprint deletion is not available through the API. "
    "Go to Studio to delete this sprint manually."
)


def _sprint_delete_not_available_response():
    return error_response(
        SPRINT_DELETE_NOT_AVAILABLE_MESSAGE,
        "sprint_delete_not_available",
        status=405,
    )

# Sorted list for OpenAPI ``enum`` entries (deterministic output).
_SPRINT_STATUS_ENUM = sorted(VALID_SPRINT_STATUSES)

_SPRINT_EXAMPLE = {
    "slug": "may-2026",
    "name": "May 2026",
    "start_date": "2026-05-01",
    "duration_weeks": 6,
    "status": "active",
    "event_series": None,
    "created_at": "2026-04-15T12:00:00+00:00",
    "updated_at": "2026-04-15T12:00:00+00:00",
}

# Shared request-body documentation for the ``event_series`` link.
_EVENT_SERIES_PROPERTY = {
    "type": ["string", "integer", "null"],
    "nullable": True,
    "description": (
        "Link to an event series, resolved by id (integer or numeric "
        "string) or slug (non-numeric string). ``null`` or an empty "
        "string clears the link; an unknown id/slug returns 422 "
        "``unknown_series``."
    ),
}

# Shared 422 ``unknown_series`` response documentation.
_UNKNOWN_SERIES_RESPONSE = {
    "description": "Unknown ``event_series`` id or slug.",
    "example": {
        "error": "Unknown event series",
        "code": "unknown_series",
        "details": {"event_series": "Unknown event series"},
    },
}


def _resolve_event_series(raw):
    """Resolve a request-body ``event_series`` value to ``EventSeries|None``.

    Returns ``(series, error)`` where ``error`` is an ``error_response`` (or
    ``None`` on success). ``series`` is ``None`` for an explicit unlink
    (``None`` / ``""``). Resolution by id-or-slug is shared with Studio via
    ``_parse_event_series``.

    Wrong JSON types (list, dict, bool) are a 422 ``validation_error``;
    unknown id/slug is a 422 ``unknown_series``.
    """
    if raw in (None, ""):
        return None, None
    if not isinstance(raw, (int, str)) or isinstance(raw, bool):
        return None, error_response(
            "Invalid event_series",
            "validation_error",
            status=422,
            details={"event_series": "Must be an event series id or slug"},
        )
    series, parse_error = _parse_event_series(raw)
    if parse_error:
        return None, error_response(
            "Unknown event series",
            "unknown_series",
            status=422,
            details={"event_series": "Unknown event series"},
        )
    return series, None


def _parse_iso_date(value):
    """Parse a YYYY-MM-DD string into a ``date``. Returns None on failure."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _sprint_visible_to(user, sprint):
    """Non-staff bearers can only see sprints they have a plan in.

    Returns True for staff, or for non-staff with at least one plan in
    this sprint. The detail endpoint uses this to decide between 200 and
    a 404 ``unknown_sprint`` (we deliberately return 404 rather than
    403 so we don't leak existence information).
    """
    if bearer_is_admin(user):
        return True
    return visible_plans_for(user).filter(sprint=sprint).exists()


@token_required
@csrf_exempt
@require_methods("GET", "POST")
@openapi_spec(
    tag="Sprints",
    summary="List or create sprints",
    methods={
        "GET": {
            "summary": "List sprints",
            "description": (
                "Returns every sprint visible to the bearer token. "
                "Non-staff tokens see every sprint row -- visibility "
                "narrowing happens on the per-sprint detail endpoint, "
                "not on the collection."
            ),
            "query": {
                "status": {
                    "type": "string",
                    "enum": _SPRINT_STATUS_ENUM,
                    "required": False,
                    "description": "Filter to sprints with the given status.",
                },
            },
            "responses": {
                200: {
                    "description": "List of sprints.",
                    "example": {"sprints": [_SPRINT_EXAMPLE]},
                },
                401: {"description": "Missing or invalid token."},
                422: {"description": "Unknown ``status`` filter value."},
            },
        },
        "POST": {
            "summary": "Create a sprint (staff-only)",
            "description": (
                "Staff-only. Non-staff tokens get 403 with code "
                "``forbidden_other_user_plan``. Slug uniqueness is "
                "enforced at the API layer (422) before hitting the DB."
            ),
            "request_body": {
                "required": ["name", "slug", "start_date", "duration_weeks"],
                "properties": {
                    "name": {"type": "string"},
                    "slug": {"type": "string"},
                    "start_date": {"type": "string", "format": "date"},
                    "duration_weeks": {"type": "integer"},
                    "status": {
                        "type": "string",
                        "enum": _SPRINT_STATUS_ENUM,
                        "default": "draft",
                    },
                    "event_series": _EVENT_SERIES_PROPERTY,
                },
                "example": {
                    "name": "May 2026",
                    "slug": "may-2026",
                    "start_date": "2026-05-01",
                    "duration_weeks": 6,
                    "status": "draft",
                    "event_series": "may-2026-community-sprint",
                },
            },
            "responses": {
                201: {
                    "description": "Sprint created.",
                    "example": _SPRINT_EXAMPLE,
                },
                400: {"description": "Invalid JSON body."},
                403: {
                    "description": "Non-staff token attempted to create.",
                    "example": {
                        "error": "Sprint creation is staff-only",
                        "code": "forbidden_other_user_plan",
                    },
                },
                422: {
                    "description": "Validation error (missing field, "
                                   "bad date, duplicate slug, or unknown "
                                   "``event_series``).",
                    "example": _UNKNOWN_SERIES_RESPONSE["example"],
                },
            },
        },
    },
)
def sprints_collection(request):
    """``GET /api/sprints/`` and ``POST /api/sprints/``."""
    if request.method == "GET":
        qs = Sprint.objects.select_related("event_series").all()
        status_filter = request.GET.get("status")
        if status_filter:
            if status_filter not in VALID_SPRINT_STATUSES:
                return error_response(
                    "Invalid status",
                    "validation_error",
                    status=422,
                    details={"status": "Unknown status"},
                )
            qs = qs.filter(status=status_filter)
        return JsonResponse(
            {"sprints": [serialize_sprint(s) for s in qs]},
            status=200,
        )

    # POST -- staff only
    if not bearer_is_admin(request.user):
        return error_response(
            "Sprint creation is staff-only",
            "forbidden_other_user_plan",
            status=403,
        )

    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return error_response(
            "Body must be a JSON object",
            "invalid_type",
            details={"field": "body", "expected": "object"},
        )

    for required in ("name", "slug", "start_date", "duration_weeks"):
        if data.get(required) in (None, ""):
            return error_response(
                f"Missing required field: {required}",
                "missing_field",
                details={"field": required},
            )

    start_date = _parse_iso_date(data["start_date"])
    if start_date is None:
        return error_response(
            "start_date must be ISO 8601 (YYYY-MM-DD)",
            "validation_error",
            status=422,
            details={"start_date": "Invalid date format"},
        )

    if not isinstance(data["duration_weeks"], int):
        return error_response(
            "duration_weeks must be an integer",
            "invalid_type",
            details={"field": "duration_weeks", "expected": "int"},
        )

    status_value = data.get("status", "draft")
    if status_value not in VALID_SPRINT_STATUSES:
        return error_response(
            "Invalid status",
            "validation_error",
            status=422,
            details={"status": "Unknown status"},
        )

    if Sprint.objects.filter(slug=data["slug"]).exists():
        return error_response(
            "Slug already exists",
            "validation_error",
            status=422,
            details={"slug": "Slug already in use"},
        )

    # Resolve the linked event series (if supplied) before any write, so an
    # unknown id/slug or wrong type fails with 422 and no sprint is created.
    event_series = None
    if "event_series" in data:
        event_series, series_error = _resolve_event_series(
            data["event_series"]
        )
        if series_error is not None:
            return series_error

    sprint = Sprint.objects.create(
        name=data["name"],
        slug=data["slug"],
        start_date=start_date,
        duration_weeks=data["duration_weeks"],
        status=status_value,
        event_series=event_series,
    )
    return JsonResponse(serialize_sprint(sprint), status=201)


@token_required
@csrf_exempt
@require_methods("GET", "PATCH", "DELETE")
@openapi_spec(
    tag="Sprints",
    summary="Retrieve, update, or delete a sprint",
    methods={
        "GET": {
            "summary": "Retrieve a sprint",
            "description": (
                "Non-staff tokens see a sprint only if they have at "
                "least one plan in it; otherwise the endpoint returns "
                "404 ``unknown_sprint`` (deliberately, to avoid leaking "
                "existence information)."
            ),
            "responses": {
                200: {
                    "description": "Sprint detail.",
                    "example": _SPRINT_EXAMPLE,
                },
                401: {"description": "Missing or invalid token."},
                404: {
                    "description": "Sprint not found or not visible.",
                    "example": {
                        "error": "Sprint not found",
                        "code": "unknown_sprint",
                    },
                },
            },
        },
        "PATCH": {
            "summary": "Update a sprint (staff-only)",
            "request_body": {
                "properties": {
                    "name": {"type": "string"},
                    "start_date": {"type": "string", "format": "date"},
                    "duration_weeks": {"type": "integer"},
                    "status": {
                        "type": "string",
                        "enum": _SPRINT_STATUS_ENUM,
                    },
                    "event_series": _EVENT_SERIES_PROPERTY,
                },
                "example": {"event_series": 2},
            },
            "responses": {
                200: {
                    "description": "Sprint updated.",
                    "example": _SPRINT_EXAMPLE,
                },
                400: {"description": "Invalid JSON body."},
                403: {"description": "Non-staff token."},
                404: {"description": "Sprint not found."},
                422: {
                    "description": "Validation error (bad date, bad "
                                   "status, or unknown ``event_series``).",
                    "example": _UNKNOWN_SERIES_RESPONSE["example"],
                },
            },
        },
        "DELETE": {
            "summary": "DELETE is not available on this route",
            "description": (
                "Sprint deletion is not available through the API "
                "(issue #864); operators must use Studio. DELETE returns "
                "a structured 405. Update sprint state with "
                "``PATCH status=...`` instead."
            ),
            "responses": {
                405: {
                    "description": "Sprint deletion is not available.",
                    "example": {
                        "error": SPRINT_DELETE_NOT_AVAILABLE_MESSAGE,
                        "code": "sprint_delete_not_available",
                    },
                },
            },
        },
    },
)
def sprint_detail(request, slug):
    """``GET / PATCH /api/sprints/<slug>/``.

    DELETE is intentionally unavailable (issue #864): it returns 405 with a
    Studio pointer before any lookup. Update sprint state via PATCH instead.
    """
    if request.method == "DELETE":
        return _sprint_delete_not_available_response()

    sprint = (
        Sprint.objects.select_related("event_series")
        .filter(slug=slug)
        .first()
    )
    if sprint is None:
        return error_response(
            "Sprint not found",
            "unknown_sprint",
            status=404,
        )

    if request.method == "GET":
        if not _sprint_visible_to(request.user, sprint):
            return error_response(
                "Sprint not found",
                "unknown_sprint",
                status=404,
            )
        return JsonResponse(serialize_sprint(sprint), status=200)

    # PATCH is staff-only.
    if not bearer_is_admin(request.user):
        return error_response(
            "Staff-only endpoint",
            "forbidden_other_user_plan",
            status=403,
        )

    # PATCH
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return error_response(
            "Body must be a JSON object",
            "invalid_type",
            details={"field": "body", "expected": "object"},
        )

    update_fields = []
    if "name" in data:
        sprint.name = data["name"]
        update_fields.append("name")
    if "start_date" in data:
        new_date = _parse_iso_date(data["start_date"])
        if new_date is None:
            return error_response(
                "start_date must be ISO 8601 (YYYY-MM-DD)",
                "validation_error",
                status=422,
                details={"start_date": "Invalid date format"},
            )
        sprint.start_date = new_date
        update_fields.append("start_date")
    if "duration_weeks" in data:
        if not isinstance(data["duration_weeks"], int):
            return error_response(
                "duration_weeks must be an integer",
                "invalid_type",
                details={"field": "duration_weeks", "expected": "int"},
            )
        sprint.duration_weeks = data["duration_weeks"]
        update_fields.append("duration_weeks")
    if "status" in data:
        if data["status"] not in VALID_SPRINT_STATUSES:
            return error_response(
                "Invalid status",
                "validation_error",
                status=422,
                details={"status": "Unknown status"},
            )
        sprint.status = data["status"]
        update_fields.append("status")
    if "event_series" in data:
        new_series, series_error = _resolve_event_series(data["event_series"])
        if series_error is not None:
            return series_error
        new_series_id = new_series.id if new_series else None
        if new_series_id != sprint.event_series_id:
            sprint.event_series = new_series
            update_fields.append("event_series")

    if update_fields:
        sprint.save(update_fields=update_fields + ["updated_at"])

    return JsonResponse(serialize_sprint(sprint), status=200)
