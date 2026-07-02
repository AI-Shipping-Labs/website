"""Owner-only member plan endpoints."""

from __future__ import annotations

from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import member_api_key_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import body_must_be_object_response, parse_json_body, require_methods
from member_api.serializers.plans import (
    serialize_member_plan_detail,
    serialize_member_plan_summary,
)
from plans.markdown_export import markdown_filename_for_plan, render_plan_markdown_export
from plans.models import Checkpoint, Deliverable, NextStep, Plan


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
@member_api_key_required("plans:read")
@require_methods("GET")
@openapi_spec(
    tag="Plans",
    methods={
        "GET": {
            "summary": "Get owned plan detail",
            "description": (
                "Returns one owned plan with member-safe nested content. "
                "Cohort visibility does not grant member API read access."
            ),
            "responses": {
                200: {"description": "Owned nested plan detail."},
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
def plan_detail(request, plan_id):
    plan = _owned_plan_or_404(request.user, plan_id)
    if plan is None:
        return error_response("Plan not found", "plan_not_found", status=404)
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
