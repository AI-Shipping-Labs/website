"""Owner-only member plan endpoints."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import member_api_key_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import body_must_be_object_response, parse_json_body, require_methods
from member_api.serializers.plans import (
    serialize_member_checkpoint,
    serialize_member_deliverable,
    serialize_member_next_step,
    serialize_member_plan_detail,
    serialize_member_plan_summary,
    serialize_member_resource,
    serialize_member_week,
    serialize_member_week_note,
)
from plans.markdown_export import markdown_filename_for_plan, render_plan_markdown_export
from plans.models import (
    NEXT_STEP_KIND_CHOICES,
    NEXT_STEP_KIND_PRE_SPRINT,
    PLAN_TITLE_MAX_LENGTH,
    PLAN_VISIBILITY_CHOICES,
    Checkpoint,
    Deliverable,
    NextStep,
    Plan,
    Resource,
    Week,
    WeekNote,
)


def _owned_plans_for(user):
    return (
        Plan.objects.filter(member=user)
        .select_related("sprint", "member")
        .prefetch_related(
            "weeks",
            "weeks__checkpoints",
            "weeks__notes",
            "resources",
            "deliverables",
            "next_steps",
        )
        .order_by("-created_at", "-id")
    )


def _owned_plan_or_404(user, plan_id):
    try:
        return _owned_plans_for(user).get(pk=plan_id)
    except Plan.DoesNotExist:
        return None


@csrf_exempt
@member_api_key_required("plans:read")
@require_methods("GET")
@openapi_spec(
    tag="Plans",
    methods={
        "GET": {
            "summary": "List owned plans",
            "description": (
                "Lists plans owned by the authenticated member API key owner, "
                "newest first. Never returns other members' plans."
            ),
            "responses": {
                200: {
                    "description": "Owned plan list.",
                    "example": {
                        "plans": [
                            {
                                "id": 12,
                                "sprint": {"slug": "may-2026", "name": "May 2026"},
                                "member": {"display_name": "Alice"},
                                "title": "Ship an eval toolkit",
                                "visibility": "private",
                                "progress": {
                                    "checkpoints_done": 1,
                                    "checkpoints_total": 4,
                                },
                                "shared_at": None,
                                "created_at": "2026-05-01T10:00:00+00:00",
                                "updated_at": "2026-05-01T10:00:00+00:00",
                            }
                        ]
                    },
                },
                401: {
                    "description": "Missing or invalid member API key.",
                    "schema": {"$ref": "#/components/schemas/ErrorResponse"},
                },
            },
        },
    },
)
def plans_collection(request):
    plans = [serialize_member_plan_summary(plan) for plan in _owned_plans_for(request.user)]
    return JsonResponse({"plans": plans})


@csrf_exempt
@member_api_key_required()
@require_methods("GET", "PATCH")
@openapi_spec(
    tag="Plans",
    methods={
        "GET": {
            "summary": "Get owned plan detail",
            "description": (
                "Returns one owned plan with member-safe nested content. "
                "Cohort visibility does not grant member API read access. "
                "Requires the ``plans:read`` scope."
            ),
            "responses": {
                200: {"description": "Owned nested plan detail."},
                401: {
                    "description": "Missing key or missing ``plans:read`` scope.",
                    "schema": {"$ref": "#/components/schemas/ErrorResponse"},
                },
                404: {
                    "description": "Plan not found for this member.",
                    "schema": {"$ref": "#/components/schemas/ErrorResponse"},
                },
            },
        },
        "PATCH": {
            "summary": "Edit owned plan narrative fields",
            "description": (
                "Partially updates plan-level fields (title, goal, summary.*, "
                "focus.*, accountability, visibility). Only supplied keys "
                "change. ``visibility`` accepts ``private`` or ``cohort``; "
                "``public`` is reserved and rejected. Requires the "
                "``plans:write`` scope. Returns the full plan detail."
            ),
            "request_body": {
                "properties": {
                    "title": {"type": "string", "maxLength": PLAN_TITLE_MAX_LENGTH},
                    "goal": {"type": "string", "maxLength": 280},
                    "visibility": {"type": "string", "enum": ["private", "cohort"]},
                    "summary": {"type": "object"},
                    "focus": {"type": "object"},
                    "accountability": {"type": "string"},
                },
                "example": {
                    "title": "Ship an eval harness",
                    "summary": {"goal": "A reusable eval harness for my agent"},
                    "focus": {"main": "Evaluation", "supporting": ["Tracing"]},
                    "visibility": "cohort",
                },
            },
            "responses": {
                200: {"description": "Updated owned plan detail."},
                401: {
                    "description": "Missing key or missing ``plans:write`` scope.",
                    "schema": {"$ref": "#/components/schemas/ErrorResponse"},
                },
                404: {
                    "description": "Plan not found for this member.",
                    "schema": {"$ref": "#/components/schemas/ErrorResponse"},
                },
                422: {
                    "description": "Validation error; no partial writes.",
                    "schema": {"$ref": "#/components/schemas/ErrorResponse"},
                },
            },
        },
    },
)
def plan_detail(request, plan_id):
    if request.method == "GET":
        denied = _require_scope(request, "plans:read")
        if denied is not None:
            return denied
        plan = _owned_plan_or_404(request.user, plan_id)
        if plan is None:
            return error_response("Plan not found", "plan_not_found", status=404)
        return JsonResponse(serialize_member_plan_detail(plan))

    denied = _require_scope(request, "plans:write")
    if denied is not None:
        return denied
    plan = _owned_plan_or_404(request.user, plan_id)
    if plan is None:
        return error_response("Plan not found", "plan_not_found", status=404)

    payload, body_error = _json_object_body(request)
    if body_error is not None:
        return body_error

    apply_error = _apply_plan_patch(plan, payload)
    if apply_error is not None:
        return apply_error

    plan = _owned_plan_or_404(request.user, plan_id)
    return JsonResponse(serialize_member_plan_detail(plan))


@csrf_exempt
@member_api_key_required("plans:read")
@require_methods("GET")
@openapi_spec(
    tag="Plans",
    methods={
        "GET": {
            "summary": "Download owned plan Markdown",
            "description": (
                "Returns the same member-safe Markdown export used by the "
                "browser owner download."
            ),
            "responses": {
                200: {"description": "Markdown attachment."},
                401: {
                    "description": "Missing or invalid member API key.",
                    "schema": {"$ref": "#/components/schemas/ErrorResponse"},
                },
                404: {
                    "description": "Plan not found for this member.",
                    "schema": {"$ref": "#/components/schemas/ErrorResponse"},
                },
            },
        },
    },
)
def plan_markdown(request, plan_id):
    plan = _owned_plan_or_404(request.user, plan_id)
    if plan is None:
        return error_response("Plan not found", "plan_not_found", status=404)
    response = HttpResponse(
        render_plan_markdown_export(plan),
        content_type="text/markdown; charset=utf-8",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="{markdown_filename_for_plan(plan)}"'
    )
    return response


@csrf_exempt
@member_api_key_required("plans:write_progress")
@require_methods("PATCH")
@openapi_spec(
    tag="Plans",
    methods={
        "PATCH": {
            "summary": "Update owned plan progress",
            "description": (
                "Atomically toggles existing checkpoint, deliverable, and "
                "next-step completion state. Does not create, delete, reorder, "
                "rename, move, or edit descriptions."
            ),
            "request_body": {
                "properties": {
                    "checkpoints": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                    "deliverables": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                    "next_steps": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                },
                "example": {
                    "checkpoints": [{"id": 123, "done": True}],
                    "deliverables": [{"id": 456, "done": False}],
                    "next_steps": [{"id": 789, "done": True}],
                },
            },
            "responses": {
                200: {"description": "Updated owned plan progress summary."},
                401: {
                    "description": "Missing or invalid member API key.",
                    "schema": {"$ref": "#/components/schemas/ErrorResponse"},
                },
                404: {
                    "description": "Plan not found for this member.",
                    "schema": {"$ref": "#/components/schemas/ErrorResponse"},
                },
                422: {
                    "description": "Validation error; no partial writes.",
                    "schema": {"$ref": "#/components/schemas/ErrorResponse"},
                },
            },
        },
    },
)
def plan_progress(request, plan_id):
    plan = _owned_plan_or_404(request.user, plan_id)
    if plan is None:
        return error_response("Plan not found", "plan_not_found", status=404)

    payload, parse_error = parse_json_body(request)
    if parse_error is not None:
        return error_response("Invalid JSON", "invalid_json", status=400)
    if not isinstance(payload, dict):
        return body_must_be_object_response()

    operations, validation_error = _validate_progress_payload(plan, payload)
    if validation_error is not None:
        return validation_error

    with transaction.atomic():
        now = timezone.now()
        for item, done in operations:
            if done and item.done_at is None:
                item.done_at = now
                item.save(update_fields=["done_at", "updated_at"])
            elif not done and item.done_at is not None:
                item.done_at = None
                item.save(update_fields=["done_at", "updated_at"])

    plan = _owned_plan_or_404(request.user, plan_id)
    return JsonResponse(serialize_member_plan_summary(plan))


_PROGRESS_COLLECTIONS = {
    "checkpoints": Checkpoint,
    "deliverables": Deliverable,
    "next_steps": NextStep,
}


def _validate_progress_payload(plan, payload):
    unknown = sorted(set(payload) - set(_PROGRESS_COLLECTIONS))
    if unknown:
        return None, error_response(
            "Unknown progress collection",
            "unknown_collection",
            status=422,
            details={"collections": unknown},
        )

    operations = []
    for collection, model in _PROGRESS_COLLECTIONS.items():
        if collection not in payload:
            continue
        rows = payload[collection]
        if not isinstance(rows, list):
            return None, error_response(
                f"{collection} must be a list",
                "invalid_type",
                status=422,
                details={"field": collection, "expected": "array"},
            )
        error = _validate_progress_rows(plan, collection, model, rows, operations)
        if error is not None:
            return None, error
    return operations, None


def _validate_progress_rows(plan, collection, model, rows, operations):
    ids = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            return error_response(
                f"{collection} entries must be objects",
                "validation_error",
                status=422,
                details={"field": f"{collection}[{index}]"},
            )
        if not isinstance(row.get("id"), int):
            return error_response(
                "Progress item id must be an integer",
                "validation_error",
                status=422,
                details={"field": f"{collection}[{index}].id"},
            )
        if not isinstance(row.get("done"), bool):
            return error_response(
                "Progress item done must be boolean",
                "validation_error",
                status=422,
                details={"field": f"{collection}[{index}].done"},
            )
        ids.append(row["id"])

    duplicates = sorted({item_id for item_id in ids if ids.count(item_id) > 1})
    if duplicates:
        return error_response(
            "Duplicate progress item id",
            "duplicate_id",
            status=422,
            details={"field": f"{collection}.id", "ids": duplicates},
        )

    existing = _owned_items_by_id(plan, collection, model, ids)
    missing = sorted(set(ids) - set(existing))
    if missing:
        return error_response(
            "Progress item does not belong to this plan",
            "item_not_found",
            status=422,
            details={"field": f"{collection}.id", "ids": missing},
        )

    for row in rows:
        operations.append((existing[row["id"]], row["done"]))
    return None


def _owned_items_by_id(plan, collection, model, ids):
    if not ids:
        return {}
    if collection == "checkpoints":
        queryset = model.objects.filter(week__plan=plan, id__in=ids)
    else:
        queryset = model.objects.filter(plan=plan, id__in=ids)
    return {item.id: item for item in queryset}


# ---------------------------------------------------------------------------
# Content-editing endpoints (scope: ``plans:write``)
#
# These let the plan owner reshape their own plan structure and narrative.
# Every write reuses ``_owned_plan_or_404`` so a key can never touch another
# member's plan, validates before writing, and is wrapped in
# ``transaction.atomic`` so a validation error leaves the DB unchanged.
# ---------------------------------------------------------------------------

# Sentinel distinguishing "key absent from payload" from an explicit ``None``.
_ABSENT = object()

_EDITABLE_VISIBILITIES = frozenset(value for value, _label in PLAN_VISIBILITY_CHOICES)
_NEXT_STEP_KINDS = frozenset(value for value, _label in NEXT_STEP_KIND_CHOICES)
_URL_VALIDATOR = URLValidator()


def _require_scope(request, scope):
    """Return a 401 response if the authenticated key lacks ``scope``."""
    if scope in getattr(request, "member_api_scopes", set()):
        return None
    return error_response(
        "Member API key is missing the required scope",
        "insufficient_scope",
        status=401,
        details={"required_scope": scope},
    )


def _json_object_body(request):
    """Parse the request body, returning ``(payload_dict, error_response)``."""
    payload, parse_error = parse_json_body(request)
    if parse_error is not None:
        return None, error_response("Invalid JSON", "invalid_json", status=400)
    if not isinstance(payload, dict):
        return None, body_must_be_object_response()
    return payload, None


def _validation_error(message, *, field=None, code="validation_error", extra=None):
    details = {}
    if field:
        details["field"] = field
    if extra:
        details.update(extra)
    return error_response(message, code, status=422, details=details or None)


def _unknown_fields_error(unknown, *, prefix=None):
    fields = sorted(unknown)
    if prefix:
        fields = [f"{prefix}.{name}" for name in fields]
    return error_response(
        "Unknown field",
        "unknown_field",
        status=422,
        details={"fields": fields},
    )


def _text_field(payload, key, *, field, required=False, max_length=None, allow_blank=True):
    """Extract and validate a text field.

    Returns ``(value, error)``. ``value`` is ``_ABSENT`` when ``key`` is not
    present and not required; otherwise a stripped string.
    """
    if key not in payload:
        if required:
            return None, _validation_error(f"{field} is required", field=field)
        return _ABSENT, None
    raw = payload[key]
    if raw is None:
        if required:
            return None, _validation_error(f"{field} is required", field=field)
        value = ""
    elif isinstance(raw, str):
        value = raw.strip()
    else:
        return None, _validation_error(f"{field} must be a string", field=field)
    if not allow_blank and not value:
        return None, _validation_error(f"{field} must not be empty", field=field)
    if max_length is not None and len(value) > max_length:
        return None, _validation_error(
            f"{field} must be at most {max_length} characters",
            field=field,
        )
    return value, None


def _int_field(payload, key, *, field, required=False, minimum=None):
    if key not in payload:
        if required:
            return None, _validation_error(f"{field} is required", field=field)
        return _ABSENT, None
    raw = payload[key]
    # ``bool`` is a subclass of ``int`` -- reject it so ``true`` is not a 1.
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None, _validation_error(f"{field} must be an integer", field=field)
    if minimum is not None and raw < minimum:
        return None, _validation_error(
            f"{field} must be >= {minimum}",
            field=field,
        )
    return raw, None


def _bool_field(payload, key, *, field):
    if key not in payload:
        return _ABSENT, None
    raw = payload[key]
    if not isinstance(raw, bool):
        return None, _validation_error(f"{field} must be a boolean", field=field)
    return raw, None


def _validate_visibility(value):
    if not isinstance(value, str) or value not in _EDITABLE_VISIBILITIES:
        return None, error_response(
            "Unsupported visibility value",
            "invalid_visibility",
            status=422,
            details={
                "field": "visibility",
                "allowed": sorted(_EDITABLE_VISIBILITIES),
            },
        )
    return value, None


def _validate_url_value(value):
    """Validate an already-stripped URL string. Empty string is allowed."""
    if value == "":
        return "", None
    try:
        _URL_VALIDATOR(value)
    except ValidationError:
        return None, error_response(
            "Invalid URL",
            "invalid_url",
            status=422,
            details={"field": "url"},
        )
    return value, None


def _apply_done(item, done, now):
    """Toggle ``done_at`` in place based on a desired ``done`` boolean."""
    if done is _ABSENT:
        return
    if done and item.done_at is None:
        item.done_at = now
    elif not done and item.done_at is not None:
        item.done_at = None


def _owned_week(plan, week_id):
    return Week.objects.filter(plan=plan, pk=week_id).first()


def _owned_week_or_error(plan, week_id, *, field="week_id"):
    week = _owned_week(plan, week_id)
    if week is None:
        return None, _validation_error(
            "Week does not belong to this plan",
            field=field,
            code="week_not_found",
        )
    return week, None


# ---- Plan-level PATCH -----------------------------------------------------

_SUMMARY_FIELDS = {
    "current_situation": ("summary_current_situation", None),
    "goal": ("summary_goal", None),
    "main_gap": ("summary_main_gap", None),
    "weekly_hours": ("summary_weekly_hours", 120),
    "why_this_plan": ("summary_why_this_plan", None),
}


def _apply_plan_summary(summary, updates):
    if not isinstance(summary, dict):
        return _validation_error("summary must be an object", field="summary")
    unknown = set(summary) - set(_SUMMARY_FIELDS)
    if unknown:
        return _unknown_fields_error(unknown, prefix="summary")
    for key, (model_field, max_length) in _SUMMARY_FIELDS.items():
        value, error = _text_field(
            summary, key, field=f"summary.{key}", max_length=max_length,
        )
        if error is not None:
            return error
        if value is not _ABSENT:
            updates[model_field] = value
    return None


def _apply_plan_focus(focus, updates):
    if not isinstance(focus, dict):
        return _validation_error("focus must be an object", field="focus")
    unknown = set(focus) - {"main", "supporting"}
    if unknown:
        return _unknown_fields_error(unknown, prefix="focus")
    main, error = _text_field(focus, "main", field="focus.main")
    if error is not None:
        return error
    if main is not _ABSENT:
        updates["focus_main"] = main
    if "supporting" in focus:
        supporting = focus["supporting"]
        if not isinstance(supporting, list):
            return _validation_error(
                "focus.supporting must be a list", field="focus.supporting",
            )
        cleaned = []
        for index, item in enumerate(supporting):
            if not isinstance(item, str):
                return _validation_error(
                    "focus.supporting entries must be strings",
                    field=f"focus.supporting[{index}]",
                )
            cleaned.append(item.strip())
        updates["focus_supporting"] = cleaned
    return None


def _apply_plan_patch(plan, payload):
    allowed = {"title", "goal", "visibility", "summary", "focus", "accountability"}
    unknown = set(payload) - allowed
    if unknown:
        return _unknown_fields_error(unknown)

    updates = {}

    title, error = _text_field(
        payload, "title", field="title", max_length=PLAN_TITLE_MAX_LENGTH,
    )
    if error is not None:
        return error
    if title is not _ABSENT:
        updates["title"] = title

    goal, error = _text_field(payload, "goal", field="goal", max_length=280)
    if error is not None:
        return error
    if goal is not _ABSENT:
        updates["goal"] = goal

    accountability, error = _text_field(
        payload, "accountability", field="accountability",
    )
    if error is not None:
        return error
    if accountability is not _ABSENT:
        updates["accountability"] = accountability

    if "visibility" in payload:
        visibility, error = _validate_visibility(payload["visibility"])
        if error is not None:
            return error
        updates["visibility"] = visibility

    if "summary" in payload:
        error = _apply_plan_summary(payload["summary"], updates)
        if error is not None:
            return error

    if "focus" in payload:
        error = _apply_plan_focus(payload["focus"], updates)
        if error is not None:
            return error

    with transaction.atomic():
        for model_field, value in updates.items():
            setattr(plan, model_field, value)
        plan.save()
    return None


# ---- Weeks ----------------------------------------------------------------

_WEEK_OPENAPI = {
    "POST": {
        "summary": "Create a week",
        "description": (
            "Adds a week to the owned plan. ``week_number`` is required, a "
            "positive integer, and unique per plan. Requires ``plans:write``."
        ),
        "request_body": {
            "required": ["week_number"],
            "properties": {
                "week_number": {"type": "integer", "minimum": 1},
                "theme": {"type": "string", "maxLength": 200},
                "position": {"type": "integer", "minimum": 0},
            },
            "example": {"week_number": 1, "theme": "Build the harness"},
        },
        "responses": {
            201: {"description": "Created week."},
            401: {
                "description": "Missing key or missing ``plans:write`` scope.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            404: {
                "description": "Plan not found for this member.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            422: {
                "description": "Validation error (e.g. duplicate week number).",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
        },
    },
}


@csrf_exempt
@member_api_key_required("plans:write")
@require_methods("POST")
@openapi_spec(tag="Plans", methods=_WEEK_OPENAPI)
def week_collection(request, plan_id):
    plan = _owned_plan_or_404(request.user, plan_id)
    if plan is None:
        return error_response("Plan not found", "plan_not_found", status=404)

    payload, body_error = _json_object_body(request)
    if body_error is not None:
        return body_error
    unknown = set(payload) - {"week_number", "theme", "position"}
    if unknown:
        return _unknown_fields_error(unknown)

    week_number, error = _int_field(
        payload, "week_number", field="week_number", required=True, minimum=1,
    )
    if error is not None:
        return error
    theme, error = _text_field(payload, "theme", field="theme", max_length=200)
    if error is not None:
        return error
    position, error = _int_field(payload, "position", field="position", minimum=0)
    if error is not None:
        return error

    if Week.objects.filter(plan=plan, week_number=week_number).exists():
        return error_response(
            "Week number already exists for this plan",
            "duplicate_week_number",
            status=422,
            details={"field": "week_number", "week_number": week_number},
        )

    with transaction.atomic():
        week = Week(plan=plan, week_number=week_number)
        if theme is not _ABSENT:
            week.theme = theme
        if position is not _ABSENT:
            week.position = position
        week.save()

    return JsonResponse(serialize_member_week(week), status=201)


_WEEK_DETAIL_OPENAPI = {
    "PATCH": {
        "summary": "Update a week",
        "description": (
            "Updates ``week_number`` / ``theme`` / ``position`` on an owned "
            "week. Requires ``plans:write``."
        ),
        "request_body": {
            "properties": {
                "week_number": {"type": "integer", "minimum": 1},
                "theme": {"type": "string", "maxLength": 200},
                "position": {"type": "integer", "minimum": 0},
            },
            "example": {"theme": "Ship v1", "position": 2},
        },
        "responses": {
            200: {"description": "Updated week."},
            401: {
                "description": "Missing key or missing ``plans:write`` scope.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            404: {
                "description": "Plan not found for this member.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            422: {
                "description": "Validation error or week not in this plan.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
        },
    },
    "DELETE": {
        "summary": "Delete a week",
        "description": (
            "Deletes an owned week and cascades its checkpoints and week "
            "note. Requires ``plans:write``."
        ),
        "responses": {
            200: {"description": "Week deleted."},
            401: {
                "description": "Missing key or missing ``plans:write`` scope.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            404: {
                "description": "Plan not found for this member.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            422: {
                "description": "Week does not belong to this plan.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
        },
    },
}


@csrf_exempt
@member_api_key_required("plans:write")
@require_methods("PATCH", "DELETE")
@openapi_spec(tag="Plans", methods=_WEEK_DETAIL_OPENAPI)
def week_detail(request, plan_id, week_id):
    plan = _owned_plan_or_404(request.user, plan_id)
    if plan is None:
        return error_response("Plan not found", "plan_not_found", status=404)
    week, week_error = _owned_week_or_error(plan, week_id)
    if week_error is not None:
        return week_error

    if request.method == "DELETE":
        with transaction.atomic():
            week.delete()
        return JsonResponse({"deleted": True, "id": week_id})

    payload, body_error = _json_object_body(request)
    if body_error is not None:
        return body_error
    unknown = set(payload) - {"week_number", "theme", "position"}
    if unknown:
        return _unknown_fields_error(unknown)

    week_number, error = _int_field(
        payload, "week_number", field="week_number", minimum=1,
    )
    if error is not None:
        return error
    theme, error = _text_field(payload, "theme", field="theme", max_length=200)
    if error is not None:
        return error
    position, error = _int_field(payload, "position", field="position", minimum=0)
    if error is not None:
        return error

    if week_number is not _ABSENT and week_number != week.week_number and (
        Week.objects.filter(plan=plan, week_number=week_number)
        .exclude(pk=week.pk)
        .exists()
    ):
        return error_response(
            "Week number already exists for this plan",
            "duplicate_week_number",
            status=422,
            details={"field": "week_number", "week_number": week_number},
        )

    with transaction.atomic():
        if week_number is not _ABSENT:
            week.week_number = week_number
        if theme is not _ABSENT:
            week.theme = theme
        if position is not _ABSENT:
            week.position = position
        week.save()

    return JsonResponse(serialize_member_week(week))


# ---- Checkpoints ----------------------------------------------------------

_CHECKPOINT_COLLECTION_OPENAPI = {
    "POST": {
        "summary": "Create a checkpoint in a week",
        "description": (
            "Adds a checkpoint to an owned week. ``description`` is required. "
            "An optional ``done`` sets initial completion. Requires "
            "``plans:write``."
        ),
        "request_body": {
            "required": ["description"],
            "properties": {
                "description": {"type": "string"},
                "position": {"type": "integer", "minimum": 0},
                "done": {"type": "boolean"},
            },
            "example": {"description": "Draft the eval rubric", "position": 0},
        },
        "responses": {
            201: {"description": "Created checkpoint."},
            401: {
                "description": "Missing key or missing ``plans:write`` scope.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            404: {
                "description": "Plan not found for this member.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            422: {
                "description": "Validation error or week not in this plan.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
        },
    },
}


@csrf_exempt
@member_api_key_required("plans:write")
@require_methods("POST")
@openapi_spec(tag="Plans", methods=_CHECKPOINT_COLLECTION_OPENAPI)
def checkpoint_collection(request, plan_id, week_id):
    plan = _owned_plan_or_404(request.user, plan_id)
    if plan is None:
        return error_response("Plan not found", "plan_not_found", status=404)
    week, week_error = _owned_week_or_error(plan, week_id)
    if week_error is not None:
        return week_error

    payload, body_error = _json_object_body(request)
    if body_error is not None:
        return body_error
    unknown = set(payload) - {"description", "position", "done"}
    if unknown:
        return _unknown_fields_error(unknown)

    description, error = _text_field(
        payload, "description", field="description", required=True, allow_blank=False,
    )
    if error is not None:
        return error
    position, error = _int_field(payload, "position", field="position", minimum=0)
    if error is not None:
        return error
    done, error = _bool_field(payload, "done", field="done")
    if error is not None:
        return error

    with transaction.atomic():
        checkpoint = Checkpoint(week=week, description=description)
        if position is not _ABSENT:
            checkpoint.position = position
        _apply_done(checkpoint, done, timezone.now())
        checkpoint.save()

    return JsonResponse(serialize_member_checkpoint(checkpoint), status=201)


_CHECKPOINT_DETAIL_OPENAPI = {
    "PATCH": {
        "summary": "Update or move a checkpoint",
        "description": (
            "Updates ``description`` / ``position`` / ``done`` on an owned "
            "checkpoint. Supplying ``week_id`` (a week of the same plan) "
            "moves it to that week. Requires ``plans:write``."
        ),
        "request_body": {
            "properties": {
                "description": {"type": "string"},
                "position": {"type": "integer", "minimum": 0},
                "done": {"type": "boolean"},
                "week_id": {"type": "integer"},
            },
            "example": {"description": "Refine the eval rubric", "done": True},
        },
        "responses": {
            200: {"description": "Updated checkpoint."},
            401: {
                "description": "Missing key or missing ``plans:write`` scope.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            404: {
                "description": "Plan not found for this member.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            422: {
                "description": "Validation error or checkpoint/week not in plan.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
        },
    },
    "DELETE": {
        "summary": "Delete a checkpoint",
        "description": "Deletes an owned checkpoint. Requires ``plans:write``.",
        "responses": {
            200: {"description": "Checkpoint deleted."},
            401: {
                "description": "Missing key or missing ``plans:write`` scope.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            404: {
                "description": "Plan not found for this member.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            422: {
                "description": "Checkpoint does not belong to this plan.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
        },
    },
}


@csrf_exempt
@member_api_key_required("plans:write")
@require_methods("PATCH", "DELETE")
@openapi_spec(tag="Plans", methods=_CHECKPOINT_DETAIL_OPENAPI)
def checkpoint_detail(request, plan_id, checkpoint_id):
    plan = _owned_plan_or_404(request.user, plan_id)
    if plan is None:
        return error_response("Plan not found", "plan_not_found", status=404)
    checkpoint = Checkpoint.objects.filter(
        week__plan=plan, pk=checkpoint_id,
    ).first()
    if checkpoint is None:
        return _validation_error(
            "Checkpoint does not belong to this plan",
            field="checkpoint_id",
            code="checkpoint_not_found",
        )

    if request.method == "DELETE":
        with transaction.atomic():
            checkpoint.delete()
        return JsonResponse({"deleted": True, "id": checkpoint_id})

    payload, body_error = _json_object_body(request)
    if body_error is not None:
        return body_error
    unknown = set(payload) - {"description", "position", "done", "week_id"}
    if unknown:
        return _unknown_fields_error(unknown)

    description, error = _text_field(
        payload, "description", field="description", allow_blank=False,
    )
    if error is not None:
        return error
    position, error = _int_field(payload, "position", field="position", minimum=0)
    if error is not None:
        return error
    done, error = _bool_field(payload, "done", field="done")
    if error is not None:
        return error

    target_week = _ABSENT
    if "week_id" in payload:
        week_id_value, error = _int_field(payload, "week_id", field="week_id")
        if error is not None:
            return error
        target_week, week_error = _owned_week_or_error(plan, week_id_value)
        if week_error is not None:
            return week_error

    with transaction.atomic():
        if description is not _ABSENT:
            checkpoint.description = description
        if position is not _ABSENT:
            checkpoint.position = position
        if target_week is not _ABSENT:
            checkpoint.week = target_week
        _apply_done(checkpoint, done, timezone.now())
        checkpoint.save()

    return JsonResponse(serialize_member_checkpoint(checkpoint))


# ---- Plan-level collections (deliverables / next-steps / resources) -------


def _plan_child_detail(plan, model, child_id, *, field, code):
    child = model.objects.filter(plan=plan, pk=child_id).first()
    if child is None:
        return None, _validation_error(
            f"{field.replace('_id', '')} does not belong to this plan",
            field=field,
            code=code,
        )
    return child, None


_DELIVERABLE_COLLECTION_OPENAPI = {
    "POST": {
        "summary": "Create a deliverable",
        "description": (
            "Adds a plan-level deliverable. ``description`` is required. "
            "Requires ``plans:write``."
        ),
        "request_body": {
            "required": ["description"],
            "properties": {
                "description": {"type": "string"},
                "position": {"type": "integer", "minimum": 0},
                "done": {"type": "boolean"},
            },
            "example": {"description": "A working eval harness"},
        },
        "responses": {
            201: {"description": "Created deliverable."},
            401: {
                "description": "Missing key or missing ``plans:write`` scope.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            404: {
                "description": "Plan not found for this member.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            422: {
                "description": "Validation error.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
        },
    },
}

_DELIVERABLE_DETAIL_OPENAPI = {
    "PATCH": {
        "summary": "Update a deliverable",
        "description": (
            "Updates ``description`` / ``position`` / ``done`` on an owned "
            "deliverable. Requires ``plans:write``."
        ),
        "request_body": {
            "properties": {
                "description": {"type": "string"},
                "position": {"type": "integer", "minimum": 0},
                "done": {"type": "boolean"},
            },
            "example": {"done": True},
        },
        "responses": {
            200: {"description": "Updated deliverable."},
            401: {
                "description": "Missing key or missing ``plans:write`` scope.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            404: {
                "description": "Plan not found for this member.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            422: {
                "description": "Validation error or deliverable not in plan.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
        },
    },
    "DELETE": {
        "summary": "Delete a deliverable",
        "description": "Deletes an owned deliverable. Requires ``plans:write``.",
        "responses": {
            200: {"description": "Deliverable deleted."},
            401: {
                "description": "Missing key or missing ``plans:write`` scope.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            404: {
                "description": "Plan not found for this member.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            422: {
                "description": "Deliverable does not belong to this plan.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
        },
    },
}


@csrf_exempt
@member_api_key_required("plans:write")
@require_methods("POST")
@openapi_spec(tag="Plans", methods=_DELIVERABLE_COLLECTION_OPENAPI)
def deliverable_collection(request, plan_id):
    plan = _owned_plan_or_404(request.user, plan_id)
    if plan is None:
        return error_response("Plan not found", "plan_not_found", status=404)

    payload, body_error = _json_object_body(request)
    if body_error is not None:
        return body_error
    unknown = set(payload) - {"description", "position", "done"}
    if unknown:
        return _unknown_fields_error(unknown)

    description, error = _text_field(
        payload, "description", field="description", required=True, allow_blank=False,
    )
    if error is not None:
        return error
    position, error = _int_field(payload, "position", field="position", minimum=0)
    if error is not None:
        return error
    done, error = _bool_field(payload, "done", field="done")
    if error is not None:
        return error

    with transaction.atomic():
        deliverable = Deliverable(plan=plan, description=description)
        if position is not _ABSENT:
            deliverable.position = position
        _apply_done(deliverable, done, timezone.now())
        deliverable.save()

    return JsonResponse(serialize_member_deliverable(deliverable), status=201)


@csrf_exempt
@member_api_key_required("plans:write")
@require_methods("PATCH", "DELETE")
@openapi_spec(tag="Plans", methods=_DELIVERABLE_DETAIL_OPENAPI)
def deliverable_detail(request, plan_id, deliverable_id):
    plan = _owned_plan_or_404(request.user, plan_id)
    if plan is None:
        return error_response("Plan not found", "plan_not_found", status=404)
    deliverable, child_error = _plan_child_detail(
        plan, Deliverable, deliverable_id,
        field="deliverable_id", code="deliverable_not_found",
    )
    if child_error is not None:
        return child_error

    if request.method == "DELETE":
        with transaction.atomic():
            deliverable.delete()
        return JsonResponse({"deleted": True, "id": deliverable_id})

    payload, body_error = _json_object_body(request)
    if body_error is not None:
        return body_error
    unknown = set(payload) - {"description", "position", "done"}
    if unknown:
        return _unknown_fields_error(unknown)

    description, error = _text_field(
        payload, "description", field="description", allow_blank=False,
    )
    if error is not None:
        return error
    position, error = _int_field(payload, "position", field="position", minimum=0)
    if error is not None:
        return error
    done, error = _bool_field(payload, "done", field="done")
    if error is not None:
        return error

    with transaction.atomic():
        if description is not _ABSENT:
            deliverable.description = description
        if position is not _ABSENT:
            deliverable.position = position
        _apply_done(deliverable, done, timezone.now())
        deliverable.save()

    return JsonResponse(serialize_member_deliverable(deliverable))


_NEXT_STEP_COLLECTION_OPENAPI = {
    "POST": {
        "summary": "Create a next step",
        "description": (
            "Adds a plan-level next step. ``description`` is required. "
            "``kind`` is ``pre_sprint`` (default) or ``next_step``. Requires "
            "``plans:write``."
        ),
        "request_body": {
            "required": ["description"],
            "properties": {
                "description": {"type": "string"},
                "kind": {"type": "string", "enum": ["pre_sprint", "next_step"]},
                "position": {"type": "integer", "minimum": 0},
                "done": {"type": "boolean"},
            },
            "example": {"description": "Read the tracing docs", "kind": "pre_sprint"},
        },
        "responses": {
            201: {"description": "Created next step."},
            401: {
                "description": "Missing key or missing ``plans:write`` scope.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            404: {
                "description": "Plan not found for this member.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            422: {
                "description": "Validation error (e.g. invalid kind).",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
        },
    },
}

_NEXT_STEP_DETAIL_OPENAPI = {
    "PATCH": {
        "summary": "Update a next step",
        "description": (
            "Updates ``description`` / ``kind`` / ``position`` / ``done`` on "
            "an owned next step. Requires ``plans:write``."
        ),
        "request_body": {
            "properties": {
                "description": {"type": "string"},
                "kind": {"type": "string", "enum": ["pre_sprint", "next_step"]},
                "position": {"type": "integer", "minimum": 0},
                "done": {"type": "boolean"},
            },
            "example": {"kind": "next_step", "done": True},
        },
        "responses": {
            200: {"description": "Updated next step."},
            401: {
                "description": "Missing key or missing ``plans:write`` scope.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            404: {
                "description": "Plan not found for this member.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            422: {
                "description": "Validation error or next step not in plan.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
        },
    },
    "DELETE": {
        "summary": "Delete a next step",
        "description": "Deletes an owned next step. Requires ``plans:write``.",
        "responses": {
            200: {"description": "Next step deleted."},
            401: {
                "description": "Missing key or missing ``plans:write`` scope.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            404: {
                "description": "Plan not found for this member.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            422: {
                "description": "Next step does not belong to this plan.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
        },
    },
}


def _validate_next_step_kind(payload):
    if "kind" not in payload:
        return _ABSENT, None
    raw = payload["kind"]
    if not isinstance(raw, str) or raw not in _NEXT_STEP_KINDS:
        return None, error_response(
            "Unsupported next step kind",
            "invalid_kind",
            status=422,
            details={"field": "kind", "allowed": sorted(_NEXT_STEP_KINDS)},
        )
    return raw, None


@csrf_exempt
@member_api_key_required("plans:write")
@require_methods("POST")
@openapi_spec(tag="Plans", methods=_NEXT_STEP_COLLECTION_OPENAPI)
def next_step_collection(request, plan_id):
    plan = _owned_plan_or_404(request.user, plan_id)
    if plan is None:
        return error_response("Plan not found", "plan_not_found", status=404)

    payload, body_error = _json_object_body(request)
    if body_error is not None:
        return body_error
    unknown = set(payload) - {"description", "kind", "position", "done"}
    if unknown:
        return _unknown_fields_error(unknown)

    description, error = _text_field(
        payload, "description", field="description", required=True, allow_blank=False,
    )
    if error is not None:
        return error
    kind, error = _validate_next_step_kind(payload)
    if error is not None:
        return error
    position, error = _int_field(payload, "position", field="position", minimum=0)
    if error is not None:
        return error
    done, error = _bool_field(payload, "done", field="done")
    if error is not None:
        return error

    with transaction.atomic():
        next_step = NextStep(
            plan=plan,
            description=description,
            kind=kind if kind is not _ABSENT else NEXT_STEP_KIND_PRE_SPRINT,
        )
        if position is not _ABSENT:
            next_step.position = position
        _apply_done(next_step, done, timezone.now())
        next_step.save()

    return JsonResponse(serialize_member_next_step(next_step), status=201)


@csrf_exempt
@member_api_key_required("plans:write")
@require_methods("PATCH", "DELETE")
@openapi_spec(tag="Plans", methods=_NEXT_STEP_DETAIL_OPENAPI)
def next_step_detail(request, plan_id, next_step_id):
    plan = _owned_plan_or_404(request.user, plan_id)
    if plan is None:
        return error_response("Plan not found", "plan_not_found", status=404)
    next_step, child_error = _plan_child_detail(
        plan, NextStep, next_step_id,
        field="next_step_id", code="next_step_not_found",
    )
    if child_error is not None:
        return child_error

    if request.method == "DELETE":
        with transaction.atomic():
            next_step.delete()
        return JsonResponse({"deleted": True, "id": next_step_id})

    payload, body_error = _json_object_body(request)
    if body_error is not None:
        return body_error
    unknown = set(payload) - {"description", "kind", "position", "done"}
    if unknown:
        return _unknown_fields_error(unknown)

    description, error = _text_field(
        payload, "description", field="description", allow_blank=False,
    )
    if error is not None:
        return error
    kind, error = _validate_next_step_kind(payload)
    if error is not None:
        return error
    position, error = _int_field(payload, "position", field="position", minimum=0)
    if error is not None:
        return error
    done, error = _bool_field(payload, "done", field="done")
    if error is not None:
        return error

    with transaction.atomic():
        if description is not _ABSENT:
            next_step.description = description
        if kind is not _ABSENT:
            next_step.kind = kind
        if position is not _ABSENT:
            next_step.position = position
        _apply_done(next_step, done, timezone.now())
        next_step.save()

    return JsonResponse(serialize_member_next_step(next_step))


_RESOURCE_COLLECTION_OPENAPI = {
    "POST": {
        "summary": "Create a resource",
        "description": (
            "Adds a plan-level resource link. ``title`` is required. ``url`` "
            "is optional but must be a valid URL. Requires ``plans:write``."
        ),
        "request_body": {
            "required": ["title"],
            "properties": {
                "title": {"type": "string", "maxLength": 300},
                "url": {"type": "string", "maxLength": 600},
                "note": {"type": "string"},
                "position": {"type": "integer", "minimum": 0},
            },
            "example": {
                "title": "Tracing docs",
                "url": "https://example.com/tracing",
            },
        },
        "responses": {
            201: {"description": "Created resource."},
            401: {
                "description": "Missing key or missing ``plans:write`` scope.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            404: {
                "description": "Plan not found for this member.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            422: {
                "description": "Validation error (e.g. invalid url).",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
        },
    },
}

_RESOURCE_DETAIL_OPENAPI = {
    "PATCH": {
        "summary": "Update a resource",
        "description": (
            "Updates ``title`` / ``url`` / ``note`` / ``position`` on an "
            "owned resource. Requires ``plans:write``."
        ),
        "request_body": {
            "properties": {
                "title": {"type": "string", "maxLength": 300},
                "url": {"type": "string", "maxLength": 600},
                "note": {"type": "string"},
                "position": {"type": "integer", "minimum": 0},
            },
            "example": {"note": "Skim sections 2-4"},
        },
        "responses": {
            200: {"description": "Updated resource."},
            401: {
                "description": "Missing key or missing ``plans:write`` scope.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            404: {
                "description": "Plan not found for this member.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            422: {
                "description": "Validation error or resource not in plan.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
        },
    },
    "DELETE": {
        "summary": "Delete a resource",
        "description": "Deletes an owned resource. Requires ``plans:write``.",
        "responses": {
            200: {"description": "Resource deleted."},
            401: {
                "description": "Missing key or missing ``plans:write`` scope.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            404: {
                "description": "Plan not found for this member.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            422: {
                "description": "Resource does not belong to this plan.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
        },
    },
}


@csrf_exempt
@member_api_key_required("plans:write")
@require_methods("POST")
@openapi_spec(tag="Plans", methods=_RESOURCE_COLLECTION_OPENAPI)
def resource_collection(request, plan_id):
    plan = _owned_plan_or_404(request.user, plan_id)
    if plan is None:
        return error_response("Plan not found", "plan_not_found", status=404)

    payload, body_error = _json_object_body(request)
    if body_error is not None:
        return body_error
    unknown = set(payload) - {"title", "url", "note", "position"}
    if unknown:
        return _unknown_fields_error(unknown)

    title, error = _text_field(
        payload, "title", field="title", required=True, max_length=300, allow_blank=False,
    )
    if error is not None:
        return error
    url_raw, error = _text_field(payload, "url", field="url", max_length=600)
    if error is not None:
        return error
    note, error = _text_field(payload, "note", field="note")
    if error is not None:
        return error
    position, error = _int_field(payload, "position", field="position", minimum=0)
    if error is not None:
        return error

    url_value = _ABSENT
    if url_raw is not _ABSENT:
        url_value, error = _validate_url_value(url_raw)
        if error is not None:
            return error

    with transaction.atomic():
        resource = Resource(plan=plan, title=title)
        if url_value is not _ABSENT:
            resource.url = url_value
        if note is not _ABSENT:
            resource.note = note
        if position is not _ABSENT:
            resource.position = position
        resource.save()

    return JsonResponse(serialize_member_resource(resource), status=201)


@csrf_exempt
@member_api_key_required("plans:write")
@require_methods("PATCH", "DELETE")
@openapi_spec(tag="Plans", methods=_RESOURCE_DETAIL_OPENAPI)
def resource_detail(request, plan_id, resource_id):
    plan = _owned_plan_or_404(request.user, plan_id)
    if plan is None:
        return error_response("Plan not found", "plan_not_found", status=404)
    resource, child_error = _plan_child_detail(
        plan, Resource, resource_id,
        field="resource_id", code="resource_not_found",
    )
    if child_error is not None:
        return child_error

    if request.method == "DELETE":
        with transaction.atomic():
            resource.delete()
        return JsonResponse({"deleted": True, "id": resource_id})

    payload, body_error = _json_object_body(request)
    if body_error is not None:
        return body_error
    unknown = set(payload) - {"title", "url", "note", "position"}
    if unknown:
        return _unknown_fields_error(unknown)

    title, error = _text_field(
        payload, "title", field="title", max_length=300, allow_blank=False,
    )
    if error is not None:
        return error
    url_raw, error = _text_field(payload, "url", field="url", max_length=600)
    if error is not None:
        return error
    note, error = _text_field(payload, "note", field="note")
    if error is not None:
        return error
    position, error = _int_field(payload, "position", field="position", minimum=0)
    if error is not None:
        return error

    url_value = _ABSENT
    if url_raw is not _ABSENT:
        url_value, error = _validate_url_value(url_raw)
        if error is not None:
            return error

    with transaction.atomic():
        if title is not _ABSENT:
            resource.title = title
        if url_value is not _ABSENT:
            resource.url = url_value
        if note is not _ABSENT:
            resource.note = note
        if position is not _ABSENT:
            resource.position = position
        resource.save()

    return JsonResponse(serialize_member_resource(resource))


# ---- Week notes -----------------------------------------------------------

_WEEK_NOTE_OPENAPI = {
    "PUT": {
        "summary": "Upsert the week note",
        "description": (
            "Creates or replaces the singleton note for an owned week and "
            "sets the key owner as author. ``body`` is required. Requires "
            "``plans:write``."
        ),
        "request_body": {
            "required": ["body"],
            "properties": {"body": {"type": "string"}},
            "example": {"body": "Shipped the harness; blocked on flaky evals."},
        },
        "responses": {
            200: {"description": "Upserted week note."},
            401: {
                "description": "Missing key or missing ``plans:write`` scope.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            404: {
                "description": "Plan not found for this member.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            422: {
                "description": "Validation error or week not in this plan.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
        },
    },
    "DELETE": {
        "summary": "Delete the week note",
        "description": "Removes the week's note if present. Requires ``plans:write``.",
        "responses": {
            200: {"description": "Week note deleted (idempotent)."},
            401: {
                "description": "Missing key or missing ``plans:write`` scope.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            404: {
                "description": "Plan not found for this member.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
            422: {
                "description": "Week does not belong to this plan.",
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            },
        },
    },
}


@csrf_exempt
@member_api_key_required("plans:write")
@require_methods("PUT", "DELETE")
@openapi_spec(tag="Plans", methods=_WEEK_NOTE_OPENAPI)
def week_note_detail(request, plan_id, week_id):
    plan = _owned_plan_or_404(request.user, plan_id)
    if plan is None:
        return error_response("Plan not found", "plan_not_found", status=404)
    week, week_error = _owned_week_or_error(plan, week_id)
    if week_error is not None:
        return week_error

    if request.method == "DELETE":
        with transaction.atomic():
            WeekNote.objects.filter(week=week).delete()
        return JsonResponse({"deleted": True, "week_id": week_id})

    payload, body_error = _json_object_body(request)
    if body_error is not None:
        return body_error
    unknown = set(payload) - {"body"}
    if unknown:
        return _unknown_fields_error(unknown)

    body, error = _text_field(
        payload, "body", field="body", required=True, allow_blank=False,
    )
    if error is not None:
        return error

    with transaction.atomic():
        note, _created = WeekNote.objects.update_or_create(
            week=week,
            defaults={"body": body, "author": request.user},
        )

    return JsonResponse(serialize_member_week_note(note))
