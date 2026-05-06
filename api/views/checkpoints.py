"""Checkpoint endpoints for the plans API (issue #433).

Endpoints:

- ``POST /api/weeks/<id>/checkpoints/`` -- create (with optional position;
  insertion at a non-end position shifts later siblings).
- ``PATCH /api/checkpoints/<id>/`` -- update fields.
- ``DELETE /api/checkpoints/<id>/`` -- delete and re-pack.
- ``POST /api/checkpoints/<id>/move`` -- atomic cross-week move + reorder
  with ``select_for_update`` on both affected weeks ordered by id.

The move endpoint is the hot path for #434's drag-drop UI. It guarantees:

- A single ``transaction.atomic`` so a mid-flight failure leaves no
  partial state.
- Deadlock-safe locking by ordering the two weeks by id before the
  ``SELECT FOR UPDATE``.
- Idempotent no-op when source and destination are identical AND the
  position has not changed -- verified with ``assertNumQueries``.
- A reconciliation envelope ``{checkpoint, source_week.checkpoint_ids,
  destination_week.checkpoint_ids}`` so an optimistic client does not
  need a follow-up GET.
"""

from django.db import transaction
from django.db.models import F, Max
from django.http import JsonResponse
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.safety import error_response
from api.serializers.plans import serialize_checkpoint
from api.utils import parse_json_body, require_methods
from api.views._permissions import visible_plans_for
from plans.models import Checkpoint, Week


def _coerce_datetime(value):
    """Accept either an ISO string (parsed) or None. Returns the parsed
    datetime, ``None``, or the original value if it is already a
    ``datetime`` (callers from bulk import sometimes pre-parse).
    """
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return parse_datetime(value)
    return value


def _checkpoint_ids_for_week(week_id):
    """Ordered list of checkpoint ids for a week (used by move envelope)."""
    return list(
        Checkpoint.objects.filter(week_id=week_id)
        .order_by("position", "id")
        .values_list("id", flat=True)
    )


def _load_week_for_write(user, week_id):
    """Return ``(week, error_response)`` for a write to a week's plan."""
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


def _load_checkpoint_for_write(user, checkpoint_id):
    """Return ``(checkpoint, error_response)`` for a write."""
    cp = (
        Checkpoint.objects.select_related("week")
        .filter(pk=checkpoint_id)
        .first()
    )
    if cp is None:
        return None, error_response(
            "Checkpoint not found",
            "unknown_checkpoint",
            status=404,
        )
    if not visible_plans_for(user).filter(pk=cp.week.plan_id).exists():
        return None, error_response(
            "Checkpoint not found",
            "unknown_checkpoint",
            status=404,
        )
    return cp, None


@token_required
@csrf_exempt
@require_methods("POST")
def week_checkpoints_create(request, week_id):
    """``POST /api/weeks/<week_id>/checkpoints/``.

    Body: ``description`` (required), ``position`` (optional). Without
    ``position`` we append. With ``position`` at a non-end value, shift
    existing siblings down by 1 inside a transaction.
    """
    week, err = _load_week_for_write(request.user, week_id)
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
    description = data.get("description")
    if description is None:
        return error_response(
            "Missing required field: description",
            "missing_field",
            details={"field": "description"},
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

    with transaction.atomic():
        if requested_position is None:
            max_pos = week.checkpoints.aggregate(m=Max("position"))["m"]
            position = 0 if max_pos is None else max_pos + 1
        else:
            position = requested_position
            # Shift existing siblings at-or-after `position` down by 1.
            Checkpoint.objects.filter(
                week=week, position__gte=position,
            ).update(position=F("position") + 1)

        cp = Checkpoint.objects.create(
            week=week,
            description=description,
            position=position,
            done_at=_coerce_datetime(data.get("done_at")),
        )

    return JsonResponse(serialize_checkpoint(cp), status=201)


@token_required
@csrf_exempt
@require_methods("PATCH", "DELETE")
def checkpoint_detail(request, checkpoint_id):
    """``PATCH / DELETE /api/checkpoints/<checkpoint_id>/``."""
    cp, err = _load_checkpoint_for_write(request.user, checkpoint_id)
    if err is not None:
        return err

    if request.method == "DELETE":
        old_position = cp.position
        old_week_id = cp.week_id
        with transaction.atomic():
            cp.delete()
            # Re-pack siblings: anything above the deleted position shifts down 1.
            Checkpoint.objects.filter(
                week_id=old_week_id, position__gt=old_position,
            ).update(position=F("position") - 1)
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
    if "description" in data:
        cp.description = data["description"] or ""
        update_fields.append("description")
    if "done_at" in data:
        cp.done_at = _coerce_datetime(data["done_at"])
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
        if position != cp.position:
            old_position = cp.position
            with transaction.atomic():
                if position > old_position:
                    Checkpoint.objects.filter(
                        week_id=cp.week_id,
                        position__gt=old_position,
                        position__lte=position,
                    ).exclude(pk=cp.pk).update(position=F("position") - 1)
                else:
                    Checkpoint.objects.filter(
                        week_id=cp.week_id,
                        position__gte=position,
                        position__lt=old_position,
                    ).exclude(pk=cp.pk).update(position=F("position") + 1)
                cp.position = position
                update_fields.append("position")
                cp.save(update_fields=list(set(update_fields)) + ["updated_at"])
            return JsonResponse(serialize_checkpoint(cp), status=200)

    if update_fields:
        cp.save(update_fields=list(set(update_fields)) + ["updated_at"])

    return JsonResponse(serialize_checkpoint(cp), status=200)


@token_required
@csrf_exempt
@require_methods("POST")
def checkpoint_move(request, checkpoint_id):
    """``POST /api/checkpoints/<checkpoint_id>/move``.

    Atomic cross-week move + reorder. See module docstring for guarantees.
    """
    # Eager checkpoint load WITHOUT a write lock -- the lock comes after
    # we verify the body. We re-read the row inside the transaction so
    # the locked state is the one we mutate.
    cp_existing = (
        Checkpoint.objects.select_related("week")
        .filter(pk=checkpoint_id)
        .first()
    )
    if cp_existing is None:
        return error_response(
            "Checkpoint not found",
            "unknown_checkpoint",
            status=404,
        )
    if not visible_plans_for(request.user).filter(
        pk=cp_existing.week.plan_id,
    ).exists():
        # Spec: non-staff bearer on another user's plan -> 403, NOT 404.
        # We deliberately diverge from the leak-no-info pattern here
        # because the move endpoint is the only place where the spec
        # asks for an explicit forbidden code.
        return error_response(
            "Cannot move a checkpoint in another user's plan",
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

    if "week_id" not in data:
        return error_response(
            "Missing required field: week_id",
            "missing_field",
            details={"field": "week_id"},
        )
    target_week_id = data["week_id"]
    if not isinstance(target_week_id, int):
        return error_response(
            "week_id must be an integer",
            "invalid_type",
            details={"field": "week_id", "expected": "int"},
        )

    target_position = data.get("position", cp_existing.position)
    if not isinstance(target_position, int) or target_position < 0:
        return error_response(
            "position must be a non-negative integer",
            "validation_error",
            status=422,
            details={"position": "Must be >= 0"},
        )

    target_week = Week.objects.filter(pk=target_week_id).first()
    if target_week is None:
        return error_response(
            "Week not found",
            "unknown_week",
            status=404,
        )
    if target_week.plan_id != cp_existing.week.plan_id:
        return error_response(
            "Week belongs to a different plan",
            "validation_error",
            status=422,
            details={"week_id": "Week belongs to a different plan"},
        )

    source_week_id = cp_existing.week_id
    is_no_op = (
        target_week_id == source_week_id
        and target_position == cp_existing.position
    )

    if is_no_op:
        # Idempotent fast path: no writes. Spec requires that this case
        # touches no UPDATE statements (verified with ``assertNumQueries``)
        # so we deliberately do NOT enter ``transaction.atomic`` /
        # ``select_for_update`` here -- the caller's optimistic UI is
        # safe because the server state already matches the request.
        source_ids = _checkpoint_ids_for_week(source_week_id)
        return JsonResponse(
            {
                "checkpoint": serialize_checkpoint(cp_existing),
                "source_week": {
                    "id": source_week_id,
                    "checkpoint_ids": source_ids,
                },
                "destination_week": {
                    "id": target_week_id,
                    "checkpoint_ids": source_ids,
                },
            },
            status=200,
        )

    # Full path: lock both weeks ordered by id (deadlock-safe), then
    # mutate.
    locked_week_ids = sorted({source_week_id, target_week_id})

    with transaction.atomic():
        # Materialize the lock; the result is unused but the rows are
        # held for the duration of the transaction.
        list(
            Week.objects.select_for_update()
            .filter(pk__in=locked_week_ids)
            .order_by("pk")
        )

        # Re-read the checkpoint inside the lock so we never act on
        # stale ``position`` / ``week_id`` values.
        cp = Checkpoint.objects.get(pk=cp_existing.pk)
        old_position = cp.position
        old_week_id = cp.week_id

        if old_week_id == target_week_id:
            # Same-week reorder.
            if target_position > old_position:
                Checkpoint.objects.filter(
                    week_id=old_week_id,
                    position__gt=old_position,
                    position__lte=target_position,
                ).exclude(pk=cp.pk).update(position=F("position") - 1)
            else:
                Checkpoint.objects.filter(
                    week_id=old_week_id,
                    position__gte=target_position,
                    position__lt=old_position,
                ).exclude(pk=cp.pk).update(position=F("position") + 1)
            cp.position = target_position
            cp.save(update_fields=["position", "updated_at"])
        else:
            # Cross-week move. Decrement positions above the gap in the
            # source, then make room in the destination, then place.
            Checkpoint.objects.filter(
                week_id=old_week_id,
                position__gt=old_position,
            ).update(position=F("position") - 1)
            Checkpoint.objects.filter(
                week_id=target_week_id,
                position__gte=target_position,
            ).update(position=F("position") + 1)
            cp.week_id = target_week_id
            cp.position = target_position
            cp.save(update_fields=["week_id", "position", "updated_at"])

    cp.refresh_from_db()
    return JsonResponse(
        {
            "checkpoint": serialize_checkpoint(cp),
            "source_week": {
                "id": source_week_id,
                "checkpoint_ids": _checkpoint_ids_for_week(source_week_id),
            },
            "destination_week": {
                "id": target_week_id,
                "checkpoint_ids": _checkpoint_ids_for_week(target_week_id),
            },
        },
        status=200,
    )
