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
from api.safety import error_response
from api.serializers.plans import serialize_week
from api.utils import parse_json_body, require_methods
from api.views._permissions import visible_plans_for
from plans.models import Week


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
