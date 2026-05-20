"""Plan endpoints + bulk import for the plans API (issue #433).

Endpoints:

- ``GET /api/sprints/<slug>/plans/`` -- list plans in a sprint
- ``POST /api/sprints/<slug>/plans/`` -- create a plan (staff only)
- ``POST /api/sprints/<slug>/plans/bulk-import`` -- atomic bulk create (staff)
- ``GET /api/plans/<id>/`` -- nested detail
- ``PATCH /api/plans/<id>/`` -- update plan-level fields
- ``DELETE /api/plans/<id>/`` -- delete (staff)
"""

import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.safety import error_response
from api.serializers.plans import (
    serialize_plan_detail,
    serialize_plan_flat,
)
from api.utils import parse_json_body, require_methods
from api.views._permissions import (
    bearer_is_admin,
    visible_plans_for,
)
from notifications.services.notification_service import NotificationService
from plans.models import (
    KIND_CHOICES,
    PLAN_STATUS_CHOICES,
    VISIBILITY_CHOICES,
    Checkpoint,
    Deliverable,
    InterviewNote,
    NextStep,
    Plan,
    Resource,
    Sprint,
    Week,
)


def _coerce_datetime(value):
    """ISO string -> ``datetime``, passthrough for ``None`` / datetimes."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return parse_datetime(value)
    return value


logger = logging.getLogger(__name__)

User = get_user_model()

VALID_PLAN_STATUSES = {choice for choice, _label in PLAN_STATUS_CHOICES}
VALID_VISIBILITIES = {choice for choice, _label in VISIBILITY_CHOICES}
VALID_KINDS = {choice for choice, _label in KIND_CHOICES}

# Top-level plan fields the spec lets clients write directly. Used by both
# the create endpoint and PATCH so the contract is centralized.
SUMMARY_FIELDS = (
    "current_situation",
    "goal",
    "main_gap",
    "weekly_hours",
    "why_this_plan",
)

# Issue #725: the plans-API write surface has multiple ``max_length``-
# constrained CharField/URLField columns that previously slipped through
# without validation and triggered a 500 at DB time. The map below is the
# single source of truth used by ``_check_max_length`` and the create /
# PATCH / bulk-import paths. We pull the cap from ``Model._meta.get_field``
# rather than hardcoding integers so a future migration that widens (or
# narrows) the column stays in sync automatically.
#
# Only ``Plan.summary_weekly_hours`` is currently validated here at the
# *summary* level; the rest are validated inline at their nested call site
# (``weeks[].theme`` -> Week.theme, ``resources[].title/url`` -> Resource.*).
# ``Plan.goal`` keeps its own check but it now also reads from ``_meta`` so
# the cap can't drift between the migration and the view.


def _check_max_length(model, field_name, value, field_path, *, index=None):
    """Reject ``value`` if it exceeds the model column's ``max_length``.

    Returns ``(ok, error_response_or_None)``. ``ok`` is False only when a
    422 should be sent. ``field_path`` is the dotted/bracketed path the
    client sent (e.g. ``"summary.weekly_hours"`` or ``"weeks[0].theme"``);
    it becomes the key in ``details`` so the client can locate the offence.
    ``index`` (bulk import) is forwarded into ``details`` when present.
    """
    if not isinstance(value, str):
        return True, None
    max_length = model._meta.get_field(field_name).max_length
    if max_length is None or len(value) <= max_length:
        return True, None
    details = {
        field_path: f"must be {max_length} characters or fewer",
        "max_length": max_length,
    }
    if index is not None:
        details["index"] = index
    return False, error_response(
        f"Invalid {field_path}",
        "validation_error",
        status=422,
        details=details,
    )


def _apply_summary(plan, summary_dict):
    """Copy a ``{key: value}`` summary dict onto the plan instance.

    Returns the list of model field names that were updated so callers can
    pass them to ``save(update_fields=...)``.
    """
    fields = []
    if not isinstance(summary_dict, dict):
        return fields
    for key in SUMMARY_FIELDS:
        if key in summary_dict:
            setattr(plan, f"summary_{key}", summary_dict[key] or "")
            fields.append(f"summary_{key}")
    return fields


def _apply_focus(plan, focus_dict):
    """Apply a ``{"main": ..., "supporting": [...]}`` focus dict."""
    fields = []
    if not isinstance(focus_dict, dict):
        return fields
    if "main" in focus_dict:
        plan.focus_main = focus_dict["main"] or ""
        fields.append("focus_main")
    if "supporting" in focus_dict:
        supporting = focus_dict["supporting"]
        if not isinstance(supporting, list):
            return None  # signal validation error to caller
        plan.focus_supporting = supporting
        fields.append("focus_supporting")
    return fields


def _build_summary_from_payload(data):
    """Accept summary either nested as ``summary`` or as flat ``summary_*``
    fields for backfill ergonomics. Returns a single normalized dict.
    """
    summary = {}
    nested = data.get("summary")
    if isinstance(nested, dict):
        for key in SUMMARY_FIELDS:
            if key in nested:
                summary[key] = nested[key]
    for key in SUMMARY_FIELDS:
        flat_key = f"summary_{key}"
        if flat_key in data:
            summary[key] = data[flat_key]
    return summary


def _summary_field_path(data, key):
    """Return the field path the client used for summary key ``key``.

    Mirrors the input shape so error responses point the caller at exactly
    the JSON path they sent (``summary.weekly_hours`` for nested input,
    ``summary_weekly_hours`` for flat input). Flat takes precedence because
    ``_build_summary_from_payload`` lets flat override nested.
    """
    flat_key = f"summary_{key}"
    if flat_key in data:
        return flat_key
    return f"summary.{key}"


def _create_plan_from_payload(plan_data, sprint, *, index=None):
    """Create a Plan + nested children from a single payload dict.

    Used by both the single-create endpoint and bulk-import. Returns
    ``(plan, error_response)``; exactly one is non-None. The caller is
    responsible for wrapping this in a transaction.

    ``index`` is included in error ``details`` when present (bulk import
    needs to tell the caller which array element failed).
    """
    user_email = plan_data.get("user_email")
    if not user_email:
        details = {"field": "user_email"}
        if index is not None:
            details["index"] = index
        return None, error_response(
            "Missing required field: user_email",
            "missing_field",
            details=details,
        )
    member = User.objects.filter(email__iexact=user_email).first()
    if member is None:
        details = {"user_email": "Unknown user"}
        if index is not None:
            details["index"] = index
        return None, error_response(
            "Unknown user",
            "unknown_user",
            status=422,
            details=details,
        )

    if Plan.objects.filter(member=member, sprint=sprint).exists():
        details = {"user_email": user_email}
        if index is not None:
            details["index"] = index
        return None, error_response(
            "Plan already exists for this user in this sprint",
            "duplicate_plan",
            status=409,
            details=details,
        )

    status_value = plan_data.get("status", "draft")
    if status_value not in VALID_PLAN_STATUSES:
        details = {"status": "Unknown status"}
        if index is not None:
            details["index"] = index
        return None, error_response(
            "Invalid status",
            "validation_error",
            status=422,
            details=details,
        )

    goal_value = plan_data.get("goal", "")
    if goal_value is None:
        goal_value = ""
    goal_max = Plan._meta.get_field("goal").max_length
    if not isinstance(goal_value, str) or len(goal_value) > goal_max:
        details = {
            "goal": f"must be a string of {goal_max} characters or fewer",
            "max_length": goal_max,
        }
        if index is not None:
            details["index"] = index
        return None, error_response(
            "Invalid goal",
            "validation_error",
            status=422,
            details=details,
        )

    # Issue #725: validate every ``max_length``-constrained summary field
    # *before* save. ``summary_weekly_hours`` is the field that motivated
    # the bug; the same check applies to any other summary key that maps
    # to a CharField with a cap. Field path mirrors the input shape so the
    # client sees the exact JSON pointer they sent.
    summary = _build_summary_from_payload(plan_data)
    for key, value in summary.items():
        model_field = f"summary_{key}"
        if Plan._meta.get_field(model_field).max_length is None:
            continue
        ok, err = _check_max_length(
            Plan, model_field, value or "",
            _summary_field_path(plan_data, key),
            index=index,
        )
        if not ok:
            return None, err

    plan = Plan(
        member=member,
        sprint=sprint,
        status=status_value,
        goal=goal_value,
        accountability=plan_data.get("accountability", "") or "",
    )

    _apply_summary(plan, summary)

    focus = plan_data.get("focus")
    if isinstance(focus, dict):
        focus_result = _apply_focus(plan, focus)
        if focus_result is None:
            details = {"focus.supporting": "must be a list"}
            if index is not None:
                details["index"] = index
            return None, error_response(
                "Invalid focus.supporting",
                "validation_error",
                status=422,
                details=details,
            )

    plan.save()

    # Nested children. Each block validates its own shape; failures here
    # roll back the outer ``transaction.atomic`` the caller should be
    # holding.
    weeks_payload = plan_data.get("weeks") or []
    if not isinstance(weeks_payload, list):
        details = {"field": "weeks", "expected": "list"}
        if index is not None:
            details["index"] = index
        return None, error_response(
            "weeks must be a list",
            "invalid_type",
            details=details,
        )
    for week_index, week_data in enumerate(weeks_payload):
        if not isinstance(week_data, dict):
            details = {"weeks": "each entry must be an object"}
            if index is not None:
                details["index"] = index
            return None, error_response(
                "weeks entries must be objects",
                "validation_error",
                status=422,
                details=details,
            )
        week_number = week_data.get("week_number", week_index + 1)
        theme_value = week_data.get("theme", "") or ""
        # Issue #725: ``Week.theme`` has ``max_length=200``; without this
        # the DB driver raises a 500 instead of a structured 422.
        ok, err = _check_max_length(
            Week, "theme", theme_value,
            f"weeks[{week_index}].theme",
            index=index,
        )
        if not ok:
            return None, err
        week = Week.objects.create(
            plan=plan,
            week_number=week_number,
            theme=theme_value,
            position=week_data.get("position", week_index),
        )
        cps = week_data.get("checkpoints") or []
        if not isinstance(cps, list):
            details = {"weeks.checkpoints": "must be a list"}
            if index is not None:
                details["index"] = index
            return None, error_response(
                "checkpoints must be a list",
                "invalid_type",
                details=details,
            )
        for cp_index, cp_data in enumerate(cps):
            if not isinstance(cp_data, dict):
                continue
            Checkpoint.objects.create(
                week=week,
                description=cp_data.get("description", "") or "",
                position=cp_data.get("position", cp_index),
                done_at=_coerce_datetime(cp_data.get("done_at")),
            )

    for collection_name, model, fields in (
        ("resources", Resource, ("title", "url", "note")),
        ("deliverables", Deliverable, ("description",)),
        ("next_steps", NextStep, ("description",)),
    ):
        rows = plan_data.get(collection_name) or []
        if not isinstance(rows, list):
            details = {"field": collection_name, "expected": "list"}
            if index is not None:
                details["index"] = index
            return None, error_response(
                f"{collection_name} must be a list",
                "invalid_type",
                details=details,
            )
        for row_index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            kwargs = {f: (row.get(f) or "") for f in fields}
            # Issue #725: validate any ``max_length`` fields on this model
            # before insert. Only Resource currently has caps (title=300,
            # url=600); Deliverable/NextStep use TextField (no cap). The
            # loop is generic so it picks up future caps automatically.
            for f in fields:
                if model._meta.get_field(f).max_length is None:
                    continue
                ok, err = _check_max_length(
                    model, f, kwargs[f],
                    f"{collection_name}[{row_index}].{f}",
                    index=index,
                )
                if not ok:
                    return None, err
            kwargs["plan"] = plan
            kwargs["position"] = row.get("position", row_index)
            if "done_at" in row:
                kwargs["done_at"] = _coerce_datetime(row["done_at"])
            model.objects.create(**kwargs)

    notes = plan_data.get("interview_notes") or []
    if not isinstance(notes, list):
        details = {"field": "interview_notes", "expected": "list"}
        if index is not None:
            details["index"] = index
        return None, error_response(
            "interview_notes must be a list",
            "invalid_type",
            details=details,
        )
    for note_data in notes:
        if not isinstance(note_data, dict):
            continue
        visibility = note_data.get("visibility", "external")
        if visibility not in VALID_VISIBILITIES:
            details = {"visibility": "Unknown visibility"}
            if index is not None:
                details["index"] = index
            return None, error_response(
                "Invalid visibility",
                "validation_error",
                status=422,
                details=details,
            )
        kind = note_data.get("kind", "general")
        if kind not in VALID_KINDS:
            details = {"kind": "Unknown kind"}
            if index is not None:
                details["index"] = index
            return None, error_response(
                "Invalid kind",
                "validation_error",
                status=422,
                details=details,
            )
        InterviewNote.objects.create(
            plan=plan,
            member=member,
            visibility=visibility,
            kind=kind,
            body=note_data.get("body", "") or "",
        )

    return plan, None


@token_required
@csrf_exempt
@require_methods("GET", "POST")
def sprint_plans_collection(request, slug):
    """``GET / POST /api/sprints/<slug>/plans/``."""
    sprint = Sprint.objects.filter(slug=slug).first()
    if sprint is None:
        return error_response(
            "Sprint not found",
            "unknown_sprint",
            status=404,
        )

    if request.method == "GET":
        qs = visible_plans_for(request.user).filter(
            sprint=sprint,
        ).select_related("member", "sprint").order_by("-created_at")
        return JsonResponse(
            {"plans": [serialize_plan_flat(p) for p in qs]},
            status=200,
        )

    # POST -- staff only
    if not bearer_is_admin(request.user):
        return error_response(
            "Plan creation is staff-only",
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

    with transaction.atomic():
        plan, err = _create_plan_from_payload(data, sprint)
        if err is not None:
            transaction.set_rollback(True)
            return err

    plan = (
        Plan.objects
        .select_related("member", "sprint")
        .prefetch_related(
            "weeks__checkpoints",
            "resources",
            "deliverables",
            "next_steps",
        )
        .get(pk=plan.pk)
    )
    return JsonResponse(serialize_plan_detail(plan), status=201)


@token_required
@csrf_exempt
@require_methods("POST")
def sprint_plans_bulk_import(request, slug):
    """``POST /api/sprints/<slug>/plans/bulk-import``.

    Atomic create of N plans. Any failure rolls every row back so the
    caller never has to clean up partial state.
    """
    sprint = Sprint.objects.filter(slug=slug).first()
    if sprint is None:
        return error_response(
            "Sprint not found",
            "unknown_sprint",
            status=404,
        )

    if not bearer_is_admin(request.user):
        return error_response(
            "Bulk import is staff-only",
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

    plans_payload = data.get("plans")
    if plans_payload is None:
        return error_response(
            "Missing required field: plans",
            "missing_field",
            details={"field": "plans"},
        )
    if not isinstance(plans_payload, list):
        return error_response(
            "plans must be a list",
            "invalid_type",
            details={"field": "plans", "expected": "list"},
        )

    plan_ids = []
    with transaction.atomic():
        for index, plan_data in enumerate(plans_payload):
            if not isinstance(plan_data, dict):
                transaction.set_rollback(True)
                return error_response(
                    "plans entries must be objects",
                    "validation_error",
                    status=422,
                    details={"index": index},
                )
            plan, err = _create_plan_from_payload(
                plan_data, sprint, index=index,
            )
            if err is not None:
                transaction.set_rollback(True)
                return err
            plan_ids.append(plan.pk)

    return JsonResponse(
        {"created": len(plan_ids), "plan_ids": plan_ids},
        status=201,
    )


@token_required
@csrf_exempt
@require_methods("GET", "PATCH", "DELETE")
def plan_detail(request, plan_id):
    """``GET / PATCH / DELETE /api/plans/<id>/``."""
    plan = (
        visible_plans_for(request.user)
        .select_related("member", "sprint")
        .prefetch_related(
            "weeks__checkpoints",
            "resources",
            "deliverables",
            "next_steps",
        )
        .filter(pk=plan_id)
        .first()
    )
    if plan is None:
        return error_response(
            "Plan not found",
            "unknown_plan",
            status=404,
        )

    if request.method == "GET":
        return JsonResponse(serialize_plan_detail(plan), status=200)

    if request.method == "DELETE":
        if not bearer_is_admin(request.user):
            return error_response(
                "Plan delete is staff-only",
                "forbidden_other_user_plan",
                status=403,
            )
        plan.delete()
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
    if "status" in data:
        if data["status"] not in VALID_PLAN_STATUSES:
            return error_response(
                "Invalid status",
                "validation_error",
                status=422,
                details={"status": "Unknown status"},
            )
        plan.status = data["status"]
        update_fields.append("status")

    if "accountability" in data:
        plan.accountability = data["accountability"] or ""
        update_fields.append("accountability")

    if "goal" in data:
        goal = data["goal"]
        if goal is None:
            goal = ""
        if not isinstance(goal, str):
            return error_response(
                "Invalid goal",
                "validation_error",
                status=422,
                details={"goal": "must be a string"},
            )
        # Issue #725: cap pulled from ``_meta`` so a migration that
        # widens ``Plan.goal`` does not require touching the view.
        goal_max = Plan._meta.get_field("goal").max_length
        if len(goal) > goal_max:
            return error_response(
                "Invalid goal",
                "validation_error",
                status=422,
                details={
                    "goal": f"must be {goal_max} characters or fewer",
                    "max_length": goal_max,
                },
            )
        plan.goal = goal
        update_fields.append("goal")

    summary = _build_summary_from_payload(data)
    # Issue #725: enforce ``max_length`` on PATCH the same way the
    # create path does; without this PATCH would 500 on overflow.
    for key, value in summary.items():
        model_field = f"summary_{key}"
        if Plan._meta.get_field(model_field).max_length is None:
            continue
        ok, err = _check_max_length(
            Plan, model_field, value or "",
            _summary_field_path(data, key),
        )
        if not ok:
            return err
    if summary:
        update_fields.extend(_apply_summary(plan, summary))

    if "focus" in data and isinstance(data["focus"], dict):
        focus_fields = _apply_focus(plan, data["focus"])
        if focus_fields is None:
            return error_response(
                "Invalid focus.supporting",
                "validation_error",
                status=422,
                details={"focus.supporting": "must be a list"},
            )
        update_fields.extend(focus_fields)

    # Issue #732: ``shared_at`` is the explicit share trigger. Non-null
    # values are server-clamped to ``timezone.now()`` (we don't trust
    # client clocks for the share moment). ``null`` clears the
    # timestamp WITHOUT firing notifications — operator un-share is
    # silent by design. Staff-only, same gate as DELETE.
    fire_plan_shared = False
    if "shared_at" in data:
        if not bearer_is_admin(request.user):
            return error_response(
                "Plan share is staff-only",
                "forbidden_other_user_plan",
                status=403,
            )
        if data["shared_at"] is None:
            plan.shared_at = None
        else:
            plan.shared_at = timezone.now()
            fire_plan_shared = True
        update_fields.append("shared_at")

    if update_fields:
        # Always touch updated_at so the API contract stays consistent
        # with `auto_now=True` semantics on direct model save.
        plan.save(update_fields=list(set(update_fields)) + ["updated_at"])

    # Issue #732: fire bell + email AFTER the save so the timestamp is
    # already committed. A failure in the notification helper must not
    # roll back the PATCH; the helper itself logs SES exceptions.
    if fire_plan_shared:
        try:
            NotificationService.create_plan_shared(plan)
        except Exception:
            logger.exception(
                'Failed to fire plan_shared notification for plan %s',
                plan.pk,
            )

    plan.refresh_from_db()
    plan = (
        Plan.objects
        .select_related("member", "sprint")
        .prefetch_related(
            "weeks__checkpoints",
            "resources",
            "deliverables",
            "next_steps",
        )
        .get(pk=plan.pk)
    )
    return JsonResponse(serialize_plan_detail(plan), status=200)
