"""Resource / Deliverable / NextStep endpoints (issue #433).

These three plan-child collections share the same shape (``position``,
``done_at`` for two of three, plus a few free-text columns). Rather than
copy-paste three view modules, they live together with a common helper
that drives create / list / patch / delete from a small descriptor.
"""

from django.db import transaction
from django.db.models import F, Max
from django.http import JsonResponse
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt

from api.safety import error_response
from api.serializers.plans import (
    serialize_deliverable,
    serialize_next_step,
    serialize_resource,
)
from api.utils import parse_json_body, require_methods, token_or_session_required
from api.views._permissions import visible_plans_for
from plans.models import Deliverable, NextStep, Resource


def _coerce_datetime(value):
    """ISO string -> ``datetime``, passthrough for ``None`` / datetimes."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return parse_datetime(value)
    return value

# Descriptors per child type. ``required`` is the create-required field
# list; ``writable`` is the PATCH-allowed scalar fields (excluding
# ``position`` which has its own reorder logic).
ITEM_TYPES = {
    "resource": {
        "model": Resource,
        "related_name": "resources",
        "required": ["title"],
        "writable": ("title", "url", "note"),
        "has_done_at": False,
        "serialize": serialize_resource,
    },
    "deliverable": {
        "model": Deliverable,
        "related_name": "deliverables",
        "required": ["description"],
        "writable": ("description",),
        "has_done_at": True,
        "serialize": serialize_deliverable,
    },
    "next_step": {
        "model": NextStep,
        "related_name": "next_steps",
        "required": ["description"],
        "writable": ("description",),
        "has_done_at": True,
        "serialize": serialize_next_step,
    },
}


def _load_plan_for_write(user, plan_id):
    """Return ``(plan, error_response)``."""
    plan = visible_plans_for(user).filter(pk=plan_id).first()
    if plan is None:
        return None, error_response(
            "Plan not found",
            "unknown_plan",
            status=404,
        )
    return plan, None


def _load_item_for_write(user, item_type, item_id):
    """Return ``(row, descriptor, error_response)`` for an item lookup."""
    descriptor = ITEM_TYPES[item_type]
    row = (
        descriptor["model"].objects.select_related("plan")
        .filter(pk=item_id)
        .first()
    )
    if row is None:
        return None, descriptor, error_response(
            f"{item_type.capitalize()} not found",
            f"unknown_{item_type}",
            status=404,
        )
    if not visible_plans_for(user).filter(pk=row.plan_id).exists():
        return None, descriptor, error_response(
            f"{item_type.capitalize()} not found",
            f"unknown_{item_type}",
            status=404,
        )
    return row, descriptor, None


def _list_items(request, plan_id, item_type):
    plan, err = _load_plan_for_write(request.user, plan_id)
    if err is not None:
        return err
    descriptor = ITEM_TYPES[item_type]
    rows = list(
        getattr(plan, descriptor["related_name"]).all()
        .order_by("position", "id")
    )
    key = item_type + "s"
    if item_type == "next_step":
        key = "next_steps"
    return JsonResponse(
        {key: [descriptor["serialize"](r) for r in rows]},
        status=200,
    )


def _create_item(request, plan_id, item_type):
    plan, err = _load_plan_for_write(request.user, plan_id)
    if err is not None:
        return err
    descriptor = ITEM_TYPES[item_type]

    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return error_response(
            "Body must be a JSON object",
            "invalid_type",
            details={"field": "body", "expected": "object"},
        )

    for required in descriptor["required"]:
        if data.get(required) in (None, ""):
            return error_response(
                f"Missing required field: {required}",
                "missing_field",
                details={"field": required},
            )

    requested_position = data.get("position")
    if requested_position is not None and (
        not isinstance(requested_position, int) or requested_position < 0
    ):
        return error_response(
            "position must be a non-negative integer",
            "validation_error",
            status=422,
            details={"position": "Must be >= 0"},
        )

    Model = descriptor["model"]

    with transaction.atomic():
        if requested_position is None:
            max_pos = Model.objects.filter(plan=plan).aggregate(
                m=Max("position"),
            )["m"]
            position = 0 if max_pos is None else max_pos + 1
        else:
            position = requested_position
            Model.objects.filter(
                plan=plan, position__gte=position,
            ).update(position=F("position") + 1)

        kwargs = {field: (data.get(field) or "") for field in descriptor["writable"]}
        kwargs["plan"] = plan
        kwargs["position"] = position
        if descriptor["has_done_at"] and "done_at" in data:
            kwargs["done_at"] = _coerce_datetime(data["done_at"])
        row = Model.objects.create(**kwargs)

    return JsonResponse(descriptor["serialize"](row), status=201)


def _patch_item(request, item_type, item_id):
    row, descriptor, err = _load_item_for_write(request.user, item_type, item_id)
    if err is not None:
        return err

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
    for field in descriptor["writable"]:
        if field in data:
            value = data[field]
            if value is None:
                value = ""
            setattr(row, field, value)
            update_fields.append(field)

    if descriptor["has_done_at"] and "done_at" in data:
        row.done_at = _coerce_datetime(data["done_at"])
        update_fields.append("done_at")

    if "position" in data:
        position = data["position"]
        if not isinstance(position, int) or position < 0:
            return error_response(
                "position must be a non-negative integer",
                "validation_error",
                status=422,
                details={"position": "Must be >= 0"},
            )
        if position != row.position:
            old_position = row.position
            Model = descriptor["model"]
            with transaction.atomic():
                if position > old_position:
                    Model.objects.filter(
                        plan_id=row.plan_id,
                        position__gt=old_position,
                        position__lte=position,
                    ).exclude(pk=row.pk).update(position=F("position") - 1)
                else:
                    Model.objects.filter(
                        plan_id=row.plan_id,
                        position__gte=position,
                        position__lt=old_position,
                    ).exclude(pk=row.pk).update(position=F("position") + 1)
                row.position = position
                update_fields.append("position")
                row.save(
                    update_fields=list(set(update_fields)) + ["updated_at"],
                )
            return JsonResponse(descriptor["serialize"](row), status=200)

    if update_fields:
        row.save(update_fields=list(set(update_fields)) + ["updated_at"])

    return JsonResponse(descriptor["serialize"](row), status=200)


def _delete_item(request, item_type, item_id):
    row, descriptor, err = _load_item_for_write(request.user, item_type, item_id)
    if err is not None:
        return err
    Model = descriptor["model"]
    plan_id = row.plan_id
    old_position = row.position
    with transaction.atomic():
        row.delete()
        Model.objects.filter(
            plan_id=plan_id, position__gt=old_position,
        ).update(position=F("position") - 1)
    return JsonResponse({}, status=204)


# ---- public view callables (one per route) ----------------------------------


@token_or_session_required
@csrf_exempt
@require_methods("GET", "POST")
def plan_resources(request, plan_id):
    if request.method == "GET":
        return _list_items(request, plan_id, "resource")
    return _create_item(request, plan_id, "resource")


@token_or_session_required
@csrf_exempt
@require_methods("PATCH", "DELETE")
def resource_detail(request, item_id):
    if request.method == "PATCH":
        return _patch_item(request, "resource", item_id)
    return _delete_item(request, "resource", item_id)


@token_or_session_required
@csrf_exempt
@require_methods("GET", "POST")
def plan_deliverables(request, plan_id):
    if request.method == "GET":
        return _list_items(request, plan_id, "deliverable")
    return _create_item(request, plan_id, "deliverable")


@token_or_session_required
@csrf_exempt
@require_methods("PATCH", "DELETE")
def deliverable_detail(request, item_id):
    if request.method == "PATCH":
        return _patch_item(request, "deliverable", item_id)
    return _delete_item(request, "deliverable", item_id)


@token_or_session_required
@csrf_exempt
@require_methods("GET", "POST")
def plan_next_steps(request, plan_id):
    if request.method == "GET":
        return _list_items(request, plan_id, "next_step")
    return _create_item(request, plan_id, "next_step")


@token_or_session_required
@csrf_exempt
@require_methods("PATCH", "DELETE")
def next_step_detail(request, item_id):
    if request.method == "PATCH":
        return _patch_item(request, "next_step", item_id)
    return _delete_item(request, "next_step", item_id)
