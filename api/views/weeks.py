"""Week endpoints for the plans API (issue #433).

Endpoints:

- ``GET /api/plans/<id>/weeks/`` -- list
- ``POST /api/plans/<id>/weeks/`` -- create
- ``PATCH /api/weeks/<id>/`` -- update
- ``DELETE /api/weeks/<id>/`` -- delete
"""

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.serializers.plans import serialize_week
from api.utils import parse_json_body, require_methods
from api.views._permissions import visible_plans_for
from plans.models import Week

_WEEK_EXAMPLE = {
    "id": 11,
    "plan_id": 5,
    "week_number": 1,
    "theme": "Discovery",
    "position": 0,
    "created_at": "2026-04-15T12:00:00+00:00",
    "updated_at": "2026-04-15T12:00:00+00:00",
}


def _load_plan_for_write(user, plan_id):
    """Return the plan if the bearer can write to it, else an error response."""
    plan = visible_plans_for(user).filter(pk=plan_id).first()
    if plan is None:
        return None, error_response(
            "Plan not found",
            "unknown_plan",
            status=404,
        )
    return plan, None


def _load_week_for_write(user, week_id):
    """Return the week if the bearer can write to its plan, else an error."""
    week = (
        Week.objects.select_related("plan")
        .filter(pk=week_id)
        .first()
    )
    if week is None:
        return None, error_response(
            "Week not found",
            "unknown_week",
            status=404,
        )
    if not visible_plans_for(user).filter(pk=week.plan_id).exists():
        return None, error_response(
            "Week not found",
            "unknown_week",
            status=404,
        )
    return week, None


@token_required
@csrf_exempt
@require_methods("GET", "POST")
@openapi_spec(
    tag="Weeks",
    summary="List or create weeks in a plan",
    methods={
        "GET": {
            "summary": "List weeks in a plan",
            "responses": {
                200: {
                    "description": "List of weeks.",
                    "example": {"weeks": [_WEEK_EXAMPLE]},
                },
                404: {
                    "description": "Plan not found or not visible.",
                    "example": {
                        "error": "Plan not found",
                        "code": "unknown_plan",
                    },
                },
            },
        },
        "POST": {
            "summary": "Create a week",
            "request_body": {
                "required": ["week_number"],
                "properties": {
                    "week_number": {"type": "integer", "minimum": 1},
                    "theme": {"type": "string"},
                    "position": {"type": "integer", "minimum": 0},
                },
                "example": {"week_number": 1, "theme": "Discovery"},
            },
            "responses": {
                201: {
                    "description": "Week created.",
                    "example": _WEEK_EXAMPLE,
                },
                400: {"description": "Invalid JSON or missing field."},
                404: {"description": "Plan not found."},
                409: {
                    "description": (
                        "A week with the same week_number already "
                        "exists for this plan."
                    ),
                    "example": {
                        "error": "Week number already exists for this plan",
                        "code": "duplicate_week_number",
                    },
                },
                422: {"description": "Invalid position or type."},
            },
        },
    },
)
def plan_weeks_collection(request, plan_id):
    """``GET / POST /api/plans/<plan_id>/weeks/``."""
    plan, err = _load_plan_for_write(request.user, plan_id)
    if err is not None:
        return err

    if request.method == "GET":
        weeks = list(
            plan.weeks.all()
            .order_by("position", "week_number")
            .prefetch_related("checkpoints")
        )
        return JsonResponse(
            {"weeks": [serialize_week(w, with_checkpoints=False) for w in weeks]},
            status=200,
        )

    # POST
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return error_response(
            "Body must be a JSON object",
            "invalid_type",
            details={"field": "body", "expected": "object"},
        )
    week_number = data.get("week_number")
    if week_number is None:
        return error_response(
            "Missing required field: week_number",
            "missing_field",
            details={"field": "week_number"},
        )
    if not isinstance(week_number, int):
        return error_response(
            "week_number must be an integer",
            "invalid_type",
            details={"field": "week_number", "expected": "int"},
        )

    theme = data.get("theme", "") or ""
    if "position" in data:
        position = data["position"]
        if not isinstance(position, int) or position < 0:
            return error_response(
                "position must be a non-negative integer",
                "validation_error",
                status=422,
                details={"position": "Must be >= 0"},
            )
    else:
        max_pos = plan.weeks.aggregate(m=Max("position"))["m"]
        position = 0 if max_pos is None else max_pos + 1

    try:
        with transaction.atomic():
            week = Week.objects.create(
                plan=plan,
                week_number=week_number,
                theme=theme,
                position=position,
            )
    except IntegrityError:
        return error_response(
            "Week number already exists for this plan",
            "duplicate_week_number",
            status=409,
        )

    return JsonResponse(
        serialize_week(week, with_checkpoints=False),
        status=201,
    )


@token_required
@csrf_exempt
@require_methods("PATCH", "DELETE")
@openapi_spec(
    tag="Weeks",
    summary="Update or delete a week",
    methods={
        "PATCH": {
            "summary": "Update a week",
            "request_body": {
                "properties": {
                    "theme": {"type": "string"},
                    "position": {"type": "integer", "minimum": 0},
                },
                "example": {"theme": "Discovery"},
            },
            "responses": {
                200: {
                    "description": "Week updated.",
                    "example": _WEEK_EXAMPLE,
                },
                400: {"description": "Invalid JSON body."},
                404: {
                    "description": "Week not found.",
                    "example": {
                        "error": "Week not found",
                        "code": "unknown_week",
                    },
                },
                422: {"description": "Invalid position."},
            },
        },
        "DELETE": {
            "summary": "Delete a week",
            "responses": {
                204: {"description": "Week deleted (empty body)."},
                404: {"description": "Week not found."},
            },
        },
    },
)
def week_detail(request, week_id):
    """``PATCH / DELETE /api/weeks/<week_id>/``."""
    week, err = _load_week_for_write(request.user, week_id)
    if err is not None:
        return err

    if request.method == "DELETE":
        week.delete()
        return JsonResponse({}, status=204)

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
    if "theme" in data:
        week.theme = data["theme"] or ""
        update_fields.append("theme")
    if "position" in data:
        position = data["position"]
        if not isinstance(position, int) or position < 0:
            return error_response(
                "position must be a non-negative integer",
                "validation_error",
                status=422,
                details={"position": "Must be >= 0"},
            )
        week.position = position
        update_fields.append("position")

    if update_fields:
        week.save(update_fields=update_fields + ["updated_at"])

    return JsonResponse(
        serialize_week(week, with_checkpoints=False),
        status=200,
    )
