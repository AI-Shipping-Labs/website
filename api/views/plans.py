"""Plan endpoints + bulk import for the plans API (issue #433).

Endpoints:

- ``GET /api/sprints/<slug>/plans/`` -- list plans in a sprint
- ``POST /api/sprints/<slug>/plans/`` -- create a plan (staff only)
- ``POST /api/sprints/<slug>/plans/bulk-import`` -- atomic bulk create (staff)
- ``GET /api/plans/<id>/`` -- nested detail
- ``PATCH /api/plans/<id>/`` -- update plan-level fields and/or reconcile
  nested children (issue #734)
- ``DELETE /api/plans/<id>/`` -- delete (staff)
"""

import json
import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required, token_required_any_user
from accounts.utils.tags import normalize_tags
from api.openapi import openapi_spec
from api.safety import error_response
from api.serializers.plans import (
    serialize_plan_detail,
    serialize_plan_flat,
)
from api.utils import parse_json_body, require_methods, token_or_session_required
from api.views._permissions import (
    bearer_is_admin,
    visible_plans_for,
)
from notifications.services.notification_service import NotificationService
from plans.models import (
    KIND_CHOICES,
    NEXT_STEP_KIND_CHOICES,
    NEXT_STEP_KIND_PRE_SPRINT,
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
from plans.services import (
    MoveUnfinishedItemsError,
    draft_next_sprint_plan,
    move_unfinished_items_to_sprint,
    send_partner_intro_emails,
    send_plan_ready_email_for_plan,
    send_plan_ready_emails,
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

VALID_VISIBILITIES = {choice for choice, _label in VISIBILITY_CHOICES}
VALID_KINDS = {choice for choice, _label in KIND_CHOICES}
VALID_NEXT_STEP_KINDS = {choice for choice, _label in NEXT_STEP_KIND_CHOICES}

_PLAN_FLAT_EXAMPLE = {
    "id": 5,
    "user_email": "alice@example.com",
    "sprint": "may-2026",
    "title": "Ship the LLM evaluation toolkit",
    "visibility": "private",
    "goal": "Ship the LLM evaluation toolkit",
    "shared_at": None,
    "created_at": "2026-04-15T12:00:00+00:00",
    "updated_at": "2026-04-15T12:00:00+00:00",
}

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


# Collection keys that PATCH may reconcile (issue #734). Order matters
# only for deterministic error reporting: weeks first, then the flat
# collections, then interview_notes.
RECONCILABLE_COLLECTIONS = (
    "weeks",
    "resources",
    "deliverables",
    "next_steps",
    "interview_notes",
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


# ---------------------------------------------------------------------------
# Shared validators (extracted so POST and PATCH cannot drift; issue #734).
# Each helper returns ``None`` on success or an ``error_response`` tuple.
# ---------------------------------------------------------------------------


def _with_index(details, index):
    """Attach a bulk-import row index to validation details if present."""
    if index is not None:
        details["index"] = index
    return details


def _validate_goal(value, *, index=None):
    if value is None:
        return None
    goal_max = Plan._meta.get_field("goal").max_length
    if not isinstance(value, str) or len(value) > goal_max:
        return error_response(
            "Invalid goal",
            "validation_error",
            status=422,
            details=_with_index(
                {
                    "goal": f"must be a string of {goal_max} characters or fewer",
                    "max_length": goal_max,
                },
                index,
            ),
        )
    return None


def _validate_title(value, *, index=None):
    if value is None:
        return "", None
    title_max = Plan._meta.get_field("title").max_length
    if not isinstance(value, str):
        return "", error_response(
            "Invalid title",
            "validation_error",
            status=422,
            details=_with_index({"title": "must be a string"}, index),
        )
    normalized = value.strip()
    if len(normalized) > title_max:
        return "", error_response(
            "Invalid title",
            "validation_error",
            status=422,
            details=_with_index(
                {
                    "title": f"must be {title_max} characters or fewer",
                    "max_length": title_max,
                },
                index,
            ),
        )
    return normalized, None


def _validate_list(value, field_name, *, index=None):
    """``value`` must be a list."""
    if not isinstance(value, list):
        return error_response(
            f"{field_name} must be a list",
            "invalid_type",
            details=_with_index(
                {"field": field_name, "expected": "list"}, index,
            ),
        )
    return None


def _validate_dict_row(row, field_name, *, index=None):
    """Single row inside a collection must be an object."""
    if not isinstance(row, dict):
        return error_response(
            f"{field_name} entries must be objects",
            "validation_error",
            status=422,
            details=_with_index(
                {field_name: "each entry must be an object"}, index,
            ),
        )
    return None


def _validate_visibility(value, *, index=None):
    if value not in VALID_VISIBILITIES:
        return error_response(
            "Invalid visibility",
            "validation_error",
            status=422,
            details=_with_index({"visibility": "Unknown visibility"}, index),
        )
    return None


def _validate_kind(value, *, index=None):
    if value not in VALID_KINDS:
        return error_response(
            "Invalid kind",
            "validation_error",
            status=422,
            details=_with_index({"kind": "Unknown kind"}, index),
        )
    return None


def _validate_note_source_metadata(note_data, *, index=None):
    if "source_metadata" not in note_data:
        return None
    if isinstance(note_data["source_metadata"], dict):
        return None
    return error_response(
        "interview_notes.source_metadata must be an object",
        "invalid_type",
        details=_with_index({
            "field": "interview_notes.source_metadata",
            "expected": "object",
        }, index),
    )


def _validate_note_tags(note_data, *, index=None):
    if "tags" not in note_data:
        return None
    if isinstance(note_data["tags"], list):
        return None
    return error_response(
        "interview_notes.tags must be a list",
        "invalid_type",
        details=_with_index({
            "field": "interview_notes.tags",
            "expected": "array",
        }, index),
    )


def _validate_note_source_type(note_data, *, index=None):
    if "source_type" not in note_data:
        return None
    if not isinstance(note_data["source_type"], str):
        return error_response(
            "interview_notes.source_type must be a string",
            "invalid_type",
            details=_with_index({
                "field": "interview_notes.source_type",
                "expected": "string",
            }, index),
        )
    if len(note_data["source_type"].strip()) <= 40:
        return None
    return error_response(
        "interview_notes.source_type is too long",
        "validation_error",
        status=422,
        details=_with_index({
            "field": "interview_notes.source_type",
            "max_length": 40,
        }, index),
    )


def _note_extra_kwargs(note_data):
    return {
        "tags": normalize_tags(note_data.get("tags", [])),
        "source_type": (note_data.get("source_type") or "").strip().lower(),
        "source_metadata": note_data.get("source_metadata") or {},
    }


def _validate_next_step_kind(value, field_path="kind", *, index=None):
    if value not in VALID_NEXT_STEP_KINDS:
        return error_response(
            "Invalid kind",
            "validation_error",
            status=422,
            details=_with_index(
                {
                    field_path: "Unknown kind",
                    "allowed": sorted(VALID_NEXT_STEP_KINDS),
                },
                index,
            ),
        )
    return None


def _validate_focus(focus_dict, *, index=None):
    """``focus`` must be a dict and ``supporting`` must be a list."""
    if not isinstance(focus_dict, dict):
        return None
    if "supporting" in focus_dict and not isinstance(
        focus_dict["supporting"], list,
    ):
        return error_response(
            "Invalid focus.supporting",
            "validation_error",
            status=422,
            details=_with_index(
                {"focus.supporting": "must be a list"}, index,
            ),
        )
    return None


# ---------------------------------------------------------------------------
# Plan-create payload helper (used by POST + bulk-import)
# ---------------------------------------------------------------------------


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
        return None, error_response(
            "Missing required field: user_email",
            "missing_field",
            details=_with_index({"field": "user_email"}, index),
        )
    member = User.objects.filter(email__iexact=user_email).first()
    if member is None:
        return None, error_response(
            "Unknown user",
            "unknown_user",
            status=422,
            details=_with_index({"user_email": "Unknown user"}, index),
        )

    if Plan.objects.filter(member=member, sprint=sprint).exists():
        return None, error_response(
            "Plan already exists for this user in this sprint",
            "duplicate_plan",
            status=409,
            details=_with_index({"user_email": user_email}, index),
        )

    # Issue #728: ``status`` is no longer a model field. If a client
    # sends it in the create payload, it is silently ignored — matching
    # the existing convention for unknown top-level keys on PATCH (see
    # ``test_patch_ignores_immutable_fields``).
    goal_value = plan_data.get("goal", "")
    if goal_value is None:
        goal_value = ""
    err = _validate_goal(goal_value, index=index)
    if err is not None:
        return None, err

    title_value, err = _validate_title(plan_data.get("title", ""), index=index)
    if err is not None:
        return None, err

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
        title=title_value,
        goal=goal_value,
        accountability=plan_data.get("accountability", "") or "",
    )

    _apply_summary(plan, summary)

    focus = plan_data.get("focus")
    if isinstance(focus, dict):
        err = _validate_focus(focus, index=index)
        if err is not None:
            return None, err
        _apply_focus(plan, focus)

    plan.save()

    # Nested children. Each block validates its own shape; failures here
    # roll back the outer ``transaction.atomic`` the caller should be
    # holding.
    weeks_payload = plan_data.get("weeks") or []
    err = _validate_list(weeks_payload, "weeks", index=index)
    if err is not None:
        return None, err
    for week_index, week_data in enumerate(weeks_payload):
        err = _validate_dict_row(week_data, "weeks", index=index)
        if err is not None:
            return None, err
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
        err = _validate_list(cps, "weeks.checkpoints", index=index)
        if err is not None:
            return None, err
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
        err = _validate_list(rows, collection_name, index=index)
        if err is not None:
            return None, err
        for row_index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            kwargs = {f: (row.get(f) or "") for f in fields}
            if collection_name == "next_steps":
                kind = row.get("kind", NEXT_STEP_KIND_PRE_SPRINT)
                err = _validate_next_step_kind(
                    kind,
                    f"{collection_name}[{row_index}].kind",
                    index=index,
                )
                if err is not None:
                    return None, err
                kwargs["kind"] = kind
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
    err = _validate_list(notes, "interview_notes", index=index)
    if err is not None:
        return None, err
    for note_data in notes:
        if not isinstance(note_data, dict):
            continue
        visibility = note_data.get("visibility", "external")
        err = _validate_visibility(visibility, index=index)
        if err is not None:
            return None, err
        kind = note_data.get("kind", "general")
        err = _validate_kind(kind, index=index)
        if err is not None:
            return None, err
        for validator in (
            _validate_note_tags,
            _validate_note_source_type,
            _validate_note_source_metadata,
        ):
            err = validator(note_data, index=index)
            if err is not None:
                return None, err
        InterviewNote.objects.create(
            plan=plan,
            member=member,
            visibility=visibility,
            kind=kind,
            body=note_data.get("body", "") or "",
            **_note_extra_kwargs(note_data),
        )

    return plan, None


# ---------------------------------------------------------------------------
# PATCH reconciliation helpers (issue #734)
# ---------------------------------------------------------------------------


def _reconcile_flat_collection(
    plan, payload_rows, manager, model, scalar_fields, *,
    collection_name, has_done_at=False,
):
    """Reconcile a flat plan-level collection (resources / deliverables /
    next_steps) against ``payload_rows``.

    Algorithm (id-presence based):

    - Row in payload with ``id`` matching an existing row -> UPDATE.
    - Row in payload with no ``id`` (or ``id`` is ``None``) -> CREATE.
    - Row in payload with an ``id`` that does NOT belong to this plan
      -> 422 ``validation_error``.
    - Existing row whose ``id`` is absent from the payload -> DELETE.

    ``payload_rows`` must already have been validated to be a list of
    dicts by the caller. Returns ``None`` on success or an
    ``error_response`` on validation failure (the caller is responsible
    for rolling back the surrounding transaction).
    """
    existing_by_id = {row.id: row for row in manager.all()}
    seen_ids = set()

    for row_index, row in enumerate(payload_rows):
        row_id = row.get("id")
        next_step_kind = None
        if model is NextStep and ("kind" in row or row_id is None):
            next_step_kind = row.get("kind", NEXT_STEP_KIND_PRE_SPRINT)
            err = _validate_next_step_kind(
                next_step_kind,
                f"{collection_name}[{row_index}].kind",
            )
            if err is not None:
                return err
        if row_id is not None:
            existing = existing_by_id.get(row_id)
            if existing is None:
                return error_response(
                    f"{collection_name}[{row_index}].id does not belong "
                    f"to this plan",
                    "validation_error",
                    status=422,
                    details={
                        "field": f"{collection_name}.id",
                        "value": row_id,
                    },
                )
            # UPDATE
            update_fields = []
            for field_name in scalar_fields:
                if field_name in row:
                    setattr(existing, field_name, row[field_name] or "")
                    update_fields.append(field_name)
            if "position" in row:
                existing.position = row["position"]
                update_fields.append("position")
            if has_done_at and "done_at" in row:
                existing.done_at = _coerce_datetime(row["done_at"])
                update_fields.append("done_at")
            if model is NextStep and next_step_kind is not None:
                existing.kind = next_step_kind
                update_fields.append("kind")
            if update_fields:
                existing.save(
                    update_fields=list(set(update_fields)) + ["updated_at"],
                )
            seen_ids.add(row_id)
        else:
            # CREATE
            kwargs = {
                f: (row.get(f) or "") for f in scalar_fields
            }
            kwargs["plan"] = plan
            kwargs["position"] = row.get("position", row_index)
            if has_done_at and "done_at" in row:
                kwargs["done_at"] = _coerce_datetime(row["done_at"])
            if model is NextStep:
                kwargs["kind"] = next_step_kind
            model.objects.create(**kwargs)

    # DELETE rows whose ids were not in the payload.
    for existing_id, existing_row in existing_by_id.items():
        if existing_id not in seen_ids:
            existing_row.delete()

    return None


def _reconcile_checkpoints(week, payload_rows, allowed_ids_per_week):
    """Reconcile checkpoints for a single ``week``.

    ``allowed_ids_per_week`` maps ``week_id`` -> ``set(checkpoint_id)``.
    A checkpoint id that belongs to a DIFFERENT week of this plan is
    rejected with 422 ``validation_error`` (cross-week move is OUT OF
    SCOPE per issue #734).
    """
    existing_by_id = {cp.id: cp for cp in week.checkpoints.all()}
    seen_ids = set()
    allowed_for_this_week = allowed_ids_per_week.get(week.id, set())
    # All checkpoint ids reachable on this plan (any week).
    all_plan_cp_ids = set()
    for ids in allowed_ids_per_week.values():
        all_plan_cp_ids |= ids

    for cp_index, cp in enumerate(payload_rows):
        cp_id = cp.get("id")
        if cp_id is not None:
            if cp_id not in all_plan_cp_ids:
                return error_response(
                    f"checkpoints[{cp_index}].id does not belong "
                    f"to this plan",
                    "validation_error",
                    status=422,
                    details={
                        "field": "checkpoints.id",
                        "value": cp_id,
                    },
                )
            if cp_id not in allowed_for_this_week:
                # The id is on this plan but under a different week.
                # Cross-week moves are explicitly out of scope (issue #734);
                # implementing them would require parent-tracking across
                # the reconcile pass.
                return error_response(
                    f"checkpoints[{cp_index}].id belongs to a different "
                    f"week (cross-week moves are not supported)",
                    "validation_error",
                    status=422,
                    details={
                        "field": "checkpoints.id",
                        "value": cp_id,
                        "current_week_id": _find_owning_week_id(
                            cp_id, allowed_ids_per_week,
                        ),
                        "requested_week_id": week.id,
                    },
                )
            existing = existing_by_id[cp_id]
            update_fields = []
            if "description" in cp:
                existing.description = cp["description"] or ""
                update_fields.append("description")
            if "position" in cp:
                existing.position = cp["position"]
                update_fields.append("position")
            if "done_at" in cp:
                existing.done_at = _coerce_datetime(cp["done_at"])
                update_fields.append("done_at")
            if update_fields:
                existing.save(
                    update_fields=list(set(update_fields)) + ["updated_at"],
                )
            seen_ids.add(cp_id)
        else:
            Checkpoint.objects.create(
                week=week,
                description=cp.get("description", "") or "",
                position=cp.get("position", cp_index),
                done_at=_coerce_datetime(cp.get("done_at")),
            )

    for existing_id, existing_row in existing_by_id.items():
        if existing_id not in seen_ids:
            existing_row.delete()

    return None


def _find_owning_week_id(cp_id, allowed_ids_per_week):
    """Helper: locate which week id currently owns ``cp_id``."""
    for week_id, ids in allowed_ids_per_week.items():
        if cp_id in ids:
            return week_id
    return None


def _reconcile_weeks(plan, weeks_payload):
    """Reconcile ``plan.weeks`` and each surviving week's checkpoints.

    Returns ``None`` on success or an ``error_response`` on validation
    failure. The caller is responsible for the surrounding
    ``transaction.atomic`` and rollback.
    """
    # Snapshot pre-existing week + checkpoint ids so we can validate
    # cross-week checkpoint moves.
    pre_existing_weeks = list(plan.weeks.all().prefetch_related("checkpoints"))
    allowed_ids_per_week = {
        w.id: {cp.id for cp in w.checkpoints.all()}
        for w in pre_existing_weeks
    }
    existing_weeks_by_id = {w.id: w for w in pre_existing_weeks}
    seen_week_ids = set()

    # First pass: validate every row's shape so we fail fast before any
    # writes. ``_validate_list`` was already invoked by the caller for
    # the outer list.
    for week_index, week_data in enumerate(weeks_payload):
        err = _validate_dict_row(week_data, "weeks")
        if err is not None:
            return err
        checkpoints = week_data.get("checkpoints")
        if checkpoints is not None:
            err = _validate_list(checkpoints, "weeks.checkpoints")
            if err is not None:
                return err
            for cp_index, cp in enumerate(checkpoints):
                err = _validate_dict_row(cp, "weeks.checkpoints")
                if err is not None:
                    return err
                description = cp.get("description")
                if description is not None and not isinstance(
                    description, str,
                ):
                    return error_response(
                        f"weeks[{week_index}].checkpoints[{cp_index}]"
                        f".description must be a string",
                        "validation_error",
                        status=422,
                        details={
                            "field": "checkpoints.description",
                        },
                    )

    # Second pass: apply CREATE / UPDATE per week, deferring checkpoint
    # reconciliation until the week row exists in the DB.
    week_to_checkpoints_payload = []  # list of (week_instance, cps_payload)
    for week_index, week_data in enumerate(weeks_payload):
        week_id = week_data.get("id")
        if week_id is not None:
            existing = existing_weeks_by_id.get(week_id)
            if existing is None:
                return error_response(
                    f"weeks[{week_index}].id does not belong to this plan",
                    "validation_error",
                    status=422,
                    details={"field": "weeks.id", "value": week_id},
                )
            update_fields = []
            if "week_number" in week_data:
                existing.week_number = week_data["week_number"]
                update_fields.append("week_number")
            if "theme" in week_data:
                existing.theme = week_data["theme"] or ""
                update_fields.append("theme")
            if "position" in week_data:
                existing.position = week_data["position"]
                update_fields.append("position")
            if update_fields:
                existing.save(
                    update_fields=list(set(update_fields)) + ["updated_at"],
                )
            seen_week_ids.add(week_id)
            week_obj = existing
        else:
            week_number = week_data.get("week_number", week_index + 1)
            week_obj = Week.objects.create(
                plan=plan,
                week_number=week_number,
                theme=week_data.get("theme", "") or "",
                position=week_data.get("position", week_index),
            )
        if "checkpoints" in week_data:
            week_to_checkpoints_payload.append(
                (week_obj, week_data.get("checkpoints") or []),
            )

    # DELETE weeks whose ids were not in the payload (checkpoints cascade
    # via the FK ``on_delete=CASCADE``).
    for existing_id, existing_week in existing_weeks_by_id.items():
        if existing_id not in seen_week_ids:
            existing_week.delete()
            # Remove from the cross-week map so a checkpoint that lived
            # under a deleted week cannot be referenced.
            allowed_ids_per_week.pop(existing_id, None)

    # Third pass: reconcile checkpoints under each surviving week.
    # We do this after the DELETE so cross-week move attempts that point
    # at a now-deleted week's checkpoints fail validation cleanly.
    for week_obj, cps_payload in week_to_checkpoints_payload:
        err = _reconcile_checkpoints(
            week_obj, cps_payload, allowed_ids_per_week,
        )
        if err is not None:
            return err

    return None


def _reconcile_children(plan, payload):
    """Top-level entry point: reconcile every collection key PRESENT in
    ``payload``. Keys not present are left untouched.

    Returns ``None`` on success or an ``error_response`` on validation
    failure. Caller MUST hold a ``transaction.atomic`` so a failure
    rolls back every write made by this function.
    """
    # Step 1: validate every collection's outer shape up front so we
    # never start mutating the DB before we know the request is coherent.
    for key in RECONCILABLE_COLLECTIONS:
        if key not in payload:
            continue
        value = payload[key]
        err = _validate_list(value, key)
        if err is not None:
            return err
        # Per-row shape check (interview_notes / weeks have additional
        # per-row validation handled by their reconcilers).
        for row in value:
            err = _validate_dict_row(row, key)
            if err is not None:
                return err

    # Step 2: weeks first (because deleting a week removes its
    # checkpoints; we want a deterministic order if a later collection
    # also fails).
    if "weeks" in payload:
        err = _reconcile_weeks(plan, payload["weeks"])
        if err is not None:
            return err

    flat_collections = (
        (
            "resources", plan.resources, Resource,
            ("title", "url", "note"), False,
        ),
        (
            "deliverables", plan.deliverables, Deliverable,
            ("description",), True,
        ),
        (
            "next_steps", plan.next_steps, NextStep,
            ("description",), True,
        ),
    )
    for collection_name, manager, model, fields, has_done_at in (
        flat_collections
    ):
        if collection_name not in payload:
            continue
        err = _reconcile_flat_collection(
            plan,
            payload[collection_name],
            manager,
            model,
            fields,
            collection_name=collection_name,
            has_done_at=has_done_at,
        )
        if err is not None:
            return err

    if "interview_notes" in payload:
        err = _reconcile_interview_notes(plan, payload["interview_notes"])
        if err is not None:
            return err

    return None


def _reconcile_interview_notes(plan, payload_rows):
    """Reconcile ``plan.interview_notes`` against ``payload_rows``.

    Validates ``visibility`` and ``kind`` enums on every row. CREATE
    uses the plan's member as the note's ``member`` (matches POST
    semantics).
    """
    existing_by_id = {n.id: n for n in plan.interview_notes.all()}
    seen_ids = set()

    for row_index, row in enumerate(payload_rows):
        # ``visibility`` / ``kind`` validated on every present-key path:
        # UPDATE with the key set, or CREATE with the default.
        if "visibility" in row:
            visibility = row.get("visibility")
            err = _validate_visibility(visibility)
            if err is not None:
                return err
        if "kind" in row:
            kind = row.get("kind")
            err = _validate_kind(kind)
            if err is not None:
                return err
        for validator in (
            _validate_note_tags,
            _validate_note_source_type,
            _validate_note_source_metadata,
        ):
            err = validator(row)
            if err is not None:
                return err

        row_id = row.get("id")
        if row_id is not None:
            existing = existing_by_id.get(row_id)
            if existing is None:
                return error_response(
                    f"interview_notes[{row_index}].id does not belong "
                    f"to this plan",
                    "validation_error",
                    status=422,
                    details={
                        "field": "interview_notes.id",
                        "value": row_id,
                    },
                )
            update_fields = []
            if "visibility" in row:
                existing.visibility = row["visibility"]
                update_fields.append("visibility")
            if "kind" in row:
                existing.kind = row["kind"]
                update_fields.append("kind")
            if "body" in row:
                existing.body = row["body"] or ""
                update_fields.append("body")
            if "tags" in row:
                existing.tags = normalize_tags(row["tags"])
                update_fields.append("tags")
            if "source_type" in row:
                existing.source_type = row["source_type"].strip().lower()
                update_fields.append("source_type")
            if "source_metadata" in row:
                existing.source_metadata = row["source_metadata"]
                update_fields.append("source_metadata")
            if update_fields:
                existing.save(
                    update_fields=list(set(update_fields)) + ["updated_at"],
                )
            seen_ids.add(row_id)
        else:
            InterviewNote.objects.create(
                plan=plan,
                member=plan.member,
                visibility=row.get("visibility", "external"),
                kind=row.get("kind", "general"),
                body=row.get("body", "") or "",
                **_note_extra_kwargs(row),
            )

    for existing_id, existing_row in existing_by_id.items():
        if existing_id not in seen_ids:
            existing_row.delete()

    return None


@token_required
@csrf_exempt
@require_methods("GET", "POST")
@openapi_spec(
    tag="Plans",
    summary="List sprint plans or create one",
    methods={
        "GET": {
            "summary": "List plans in a sprint",
            "description": (
                "Returns plans visible to the bearer (via "
                "``visible_plans_for``), ordered by ``-created_at``."
            ),
            "responses": {
                200: {
                    "description": "List of plans.",
                    "example": {"plans": [_PLAN_FLAT_EXAMPLE]},
                },
                404: {
                    "description": "Sprint not found.",
                    "example": {
                        "error": "Sprint not found",
                        "code": "unknown_sprint",
                    },
                },
            },
        },
        "POST": {
            "summary": "Create a plan (staff-only)",
            "description": (
                "Staff-only. Returns the nested plan detail shape."
            ),
            "request_body": {
                "required": ["user_email"],
                "properties": {
                    "user_email": {"type": "string", "format": "email"},
                    "title": {"type": "string", "maxLength": 280},
                    "goal": {"type": "string", "maxLength": 280},
                    "accountability": {"type": "string"},
                    "send_ready_email": {
                        "type": "boolean",
                        "description": (
                            "Optional. Defaults to false for compatibility. "
                            "When true, staff plan creation sends the "
                            "idempotent plan-ready transactional email."
                        ),
                    },
                    "weeks": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                },
                "example": {
                    "user_email": "alice@example.com",
                    "title": "Evaluation toolkit sprint",
                    "goal": "Ship the LLM evaluation toolkit",
                    "send_ready_email": False,
                },
            },
            "responses": {
                201: {
                    "description": "Plan created (nested detail shape).",
                    "example": {
                        **_PLAN_FLAT_EXAMPLE,
                        "ready_email": {
                            "requested": False,
                            "sent": False,
                            "skipped_already_sent": False,
                            "failed": False,
                            "error": "",
                        },
                    },
                },
                400: {"description": "Invalid JSON body."},
                403: {
                    "description": "Non-staff bearer.",
                    "example": {
                        "error": "Plan creation is staff-only",
                        "code": "forbidden_other_user_plan",
                    },
                },
                404: {"description": "Sprint not found."},
                409: {
                    "description": (
                        "User already has a plan in this sprint."
                    ),
                    "example": {
                        "error": "Plan already exists for this user in this sprint",
                        "code": "duplicate_plan",
                    },
                },
                422: {
                    "description": (
                        "Missing user_email, unknown user, bad goal "
                        "length, bad title length, non-boolean "
                        "send_ready_email, or unknown enum value."
                    ),
                },
            },
        },
    },
)
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

    send_ready_email = data.get("send_ready_email", False)
    if not isinstance(send_ready_email, bool):
        return error_response(
            "send_ready_email must be a boolean",
            "validation_error",
            status=422,
            details={"field": "send_ready_email", "expected": "boolean"},
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
            "weeks__notes__author",
            "resources",
            "deliverables",
            "next_steps",
            "interview_notes",
        )
        .get(pk=plan.pk)
    )
    if send_ready_email:
        ready_email = send_plan_ready_email_for_plan(plan, actor=request.user)
    else:
        ready_email = {
            "requested": False,
            "sent": False,
            "skipped_already_sent": False,
            "failed": False,
            "error": "",
        }
    body = serialize_plan_detail(plan, viewer=request.user)
    body["ready_email"] = ready_email
    return JsonResponse(
        body,
        status=201,
    )


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Plans",
    summary="Bulk-import plans into a sprint (staff-only)",
    methods={
        "POST": {
            "summary": "Bulk-import plans",
            "description": (
                "Atomic: any single plan validation failure rolls every "
                "row back so the caller never has to clean up partial "
                "state. Returns the list of created plan ids."
            ),
            "request_body": {
                "required": ["plans"],
                "properties": {
                    "plans": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                },
                "example": {
                    "plans": [
                        {
                            "user_email": "alice@example.com",
                            "title": "Evaluation toolkit sprint",
                            "goal": "Ship the toolkit",
                        },
                    ],
                },
            },
            "responses": {
                201: {
                    "description": "Plans created.",
                    "example": {"created": 1, "plan_ids": [5]},
                },
                400: {"description": "Invalid JSON or missing field."},
                403: {
                    "description": "Non-staff bearer.",
                    "example": {
                        "error": "Bulk import is staff-only",
                        "code": "forbidden_other_user_plan",
                    },
                },
                404: {"description": "Sprint not found."},
                409: {
                    "description": (
                        "One of the rows would create a duplicate plan."
                    ),
                },
                422: {"description": "Per-row validation error."},
            },
        },
    },
)
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


@token_required_any_user
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Plans",
    summary="Send plan-ready emails for a sprint",
    methods={
        "POST": {
            "summary": "Send plan-ready emails (staff-only)",
            "description": (
                "Staff-token-only. Uses the same idempotent bulk service "
                "as Studio. ``dry_run=true`` returns the preview audience "
                "without sending email, creating notifications, creating "
                "EmailLog rows, stamping ``shared_at``, or writing ready-email "
                "logs."
            ),
            "request_body": {
                "properties": {
                    "dry_run": {"type": "boolean"},
                },
                "example": {"dry_run": True},
            },
            "responses": {
                200: {
                    "description": "Preview or send summary.",
                    "example": {
                        "dry_run": True,
                        "total_plans": 2,
                        "eligible_count": 1,
                        "already_sent_count": 1,
                        "sent_count": 0,
                        "skipped_already_sent_count": 1,
                        "failed_count": 0,
                        "eligible": [
                            {
                                "plan_id": 5,
                                "member_email": "alice@example.com",
                                "sprint_slug": "may-2026",
                            },
                        ],
                        "sent": [],
                        "skipped_already_sent": [
                            {
                                "plan_id": 4,
                                "member_email": "bob@example.com",
                                "sprint_slug": "may-2026",
                                "sent_at": "2026-05-01T12:00:00+00:00",
                            },
                        ],
                        "failed": [],
                    },
                },
                400: {
                    "description": (
                        "Invalid JSON body or non-object JSON body."
                    ),
                    "example": {
                        "error": "Body must be a JSON object",
                        "code": "invalid_type",
                    },
                },
                403: {
                    "description": "Non-staff bearer token.",
                    "example": {
                        "error": "Plan-ready email send is staff-only",
                        "code": "forbidden_staff_only",
                    },
                },
                404: {
                    "description": "Sprint not found.",
                    "example": {
                        "error": "Sprint not found",
                        "code": "unknown_sprint",
                    },
                },
                422: {
                    "description": "Invalid dry_run value.",
                    "example": {
                        "error": "dry_run must be a boolean",
                        "code": "validation_error",
                    },
                },
            },
        },
    },
)
def sprint_plans_send_ready_emails(request, slug):
    """``POST /api/sprints/<slug>/plans/send-ready-emails``."""
    sprint = Sprint.objects.filter(slug=slug).first()
    if sprint is None:
        return error_response(
            "Sprint not found",
            "unknown_sprint",
            status=404,
        )

    if not bearer_is_admin(request.user):
        return error_response(
            "Plan-ready email send is staff-only",
            "forbidden_staff_only",
            status=403,
        )

    if request.body:
        data, parse_error = parse_json_body(request)
        if parse_error is not None:
            return parse_error
    else:
        data = {}
    if not isinstance(data, dict):
        return error_response(
            "Body must be a JSON object",
            "invalid_type",
            details={"field": "body", "expected": "object"},
        )

    dry_run = data.get("dry_run", False)
    if not isinstance(dry_run, bool):
        return error_response(
            "dry_run must be a boolean",
            "validation_error",
            status=422,
            details={"field": "dry_run", "expected": "boolean"},
        )

    summary = send_plan_ready_emails(
        sprint=sprint,
        actor=request.user,
        dry_run=dry_run,
    )
    return JsonResponse(summary, status=200)


@token_required_any_user
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Sprints",
    summary="Send partner intro emails for a sprint",
    methods={
        "POST": {
            "summary": "Send partner intro emails (staff-only)",
            "description": (
                "Staff-token-only. Uses the same idempotent partner-intro "
                "email service as Studio. ``dry_run=true`` returns readiness "
                "counts, blockers, warnings, and recipient rows without "
                "sending email or writing partner-intro logs."
            ),
            "request_body": {
                "properties": {
                    "dry_run": {"type": "boolean"},
                },
                "example": {"dry_run": True},
            },
            "responses": {
                200: {
                    "description": "Preview or successful send summary.",
                    "example": {
                        "dry_run": True,
                        "send_ready": True,
                        "total_enrolled": 2,
                        "eligible_count": 2,
                        "already_sent_count": 0,
                        "missing_plan_count": 0,
                        "missing_partner_count": 0,
                        "missing_slack_link_count": 1,
                        "sent_count": 0,
                        "skipped_already_sent_count": 0,
                        "failed_count": 0,
                        "blockers": [],
                        "eligible": [
                            {
                                "member_email": "alice@example.com",
                                "partners": [
                                    {
                                        "name": "Bob",
                                        "email": "bob@example.com",
                                        "slack_identity": "UBOB",
                                        "slack_profile_url": "",
                                    },
                                ],
                            },
                        ],
                    },
                },
                400: {
                    "description": (
                        "Invalid JSON body or non-object JSON body."
                    ),
                    "example": {
                        "error": "Body must be a JSON object",
                        "code": "invalid_type",
                    },
                },
                403: {
                    "description": "Non-staff bearer token.",
                    "example": {
                        "error": "Partner intro email send is staff-only",
                        "code": "forbidden_staff_only",
                    },
                },
                404: {
                    "description": "Sprint not found.",
                    "example": {
                        "error": "Sprint not found",
                        "code": "unknown_sprint",
                    },
                },
                422: {
                    "description": "Invalid dry_run value or sprint not ready.",
                    "example": {
                        "error": "Sprint is not ready for partner intro emails",
                        "code": "validation_error",
                    },
                },
            },
        },
    },
)
def sprint_partner_intro_emails(request, slug):
    """``POST /api/sprints/<slug>/partner-intro-emails``."""
    sprint = Sprint.objects.filter(slug=slug).first()
    if sprint is None:
        return error_response(
            "Sprint not found",
            "unknown_sprint",
            status=404,
        )

    if not bearer_is_admin(request.user):
        return error_response(
            "Partner intro email send is staff-only",
            "forbidden_staff_only",
            status=403,
        )

    if request.body:
        data, parse_error = parse_json_body(request)
        if parse_error is not None:
            return parse_error
    else:
        data = {}
    if not isinstance(data, dict):
        return error_response(
            "Body must be a JSON object",
            "invalid_type",
            details={"field": "body", "expected": "object"},
        )

    dry_run = data.get("dry_run", False)
    if not isinstance(dry_run, bool):
        return error_response(
            "dry_run must be a boolean",
            "validation_error",
            status=422,
            details={"field": "dry_run", "expected": "boolean"},
        )

    summary = send_partner_intro_emails(
        sprint=sprint,
        actor=request.user,
        dry_run=dry_run,
    )
    if not dry_run and not summary["send_ready"]:
        return error_response(
            "Sprint is not ready for partner intro emails",
            "validation_error",
            status=422,
            details={"summary": summary},
        )
    return JsonResponse(summary, status=200)


def _refetch_plan_detail(plan_id):
    """Refetch a plan with the full prefetch chain so the serializer
    avoids N+1 reads on the nested children."""
    return (
        Plan.objects
        .select_related("member", "sprint")
        .prefetch_related(
            "weeks__checkpoints",
            "weeks__notes__author",
            "resources",
            "deliverables",
            "next_steps",
            "interview_notes",
        )
        .get(pk=plan_id)
    )


@token_or_session_required
@csrf_exempt
@require_methods("GET", "PATCH", "DELETE")
@openapi_spec(
    tag="Plans",
    summary="Retrieve, update, or delete a plan",
    methods={
        "GET": {
            "summary": "Retrieve a plan (nested detail)",
            "responses": {
                200: {
                    "description": (
                        "Plan with nested weeks, checkpoints, "
                        "resources, deliverables, and next steps."
                    ),
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
        "PATCH": {
            "summary": "Update plan-level fields",
            "description": (
                "Supports partial updates including ``summary`` and "
                "``focus`` nested dicts plus ``summary_*`` flat fields. "
                "Setting ``shared_at`` to a non-null value is "
                "staff-only and fires a ``plan_shared`` notification; "
                "setting it to ``null`` clears the timestamp silently."
            ),
            "request_body": {
                "properties": {
                    "title": {"type": "string", "maxLength": 280},
                    "goal": {"type": "string", "maxLength": 280},
                    "accountability": {"type": "string"},
                    "summary": {"type": "object"},
                    "focus": {"type": "object"},
                    "shared_at": {
                        "type": "string",
                        "format": "date-time",
                        "nullable": True,
                    },
                },
                "example": {
                    "title": "Evaluation toolkit sprint",
                    "goal": "Ship the LLM evaluation toolkit",
                },
            },
            "responses": {
                200: {"description": "Plan updated (nested detail shape)."},
                400: {"description": "Invalid JSON body."},
                403: {
                    "description": (
                        "Non-staff bearer touched a staff-only field "
                        "(``shared_at`` to non-null)."
                    ),
                    "example": {
                        "error": "Plan share is staff-only",
                        "code": "forbidden_other_user_plan",
                    },
                },
                404: {"description": "Plan not found."},
                422: {
                    "description": (
                        "Bad title/goal length or invalid focus.supporting type."
                    ),
                },
            },
        },
        "DELETE": {
            "summary": "Delete a plan (staff-only)",
            "responses": {
                204: {"description": "Plan deleted (empty body)."},
                403: {
                    "description": "Non-staff bearer.",
                    "example": {
                        "error": "Plan delete is staff-only",
                        "code": "forbidden_other_user_plan",
                    },
                },
                404: {"description": "Plan not found."},
            },
        },
    },
)
def plan_detail(request, plan_id):
    """``GET / PATCH / DELETE /api/plans/<id>/``."""
    plan = (
        visible_plans_for(request.user)
        .select_related("member", "sprint")
        .prefetch_related(
            "weeks__checkpoints",
            "weeks__notes__author",
            "resources",
            "deliverables",
            "next_steps",
            "interview_notes",
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
        return JsonResponse(
            serialize_plan_detail(plan, viewer=request.user),
            status=200,
        )

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

    # Issue #732: ``shared_at`` is the explicit share trigger. Non-null
    # values are server-clamped to ``timezone.now()`` (we don't trust
    # client clocks for the share moment). ``null`` clears the
    # timestamp WITHOUT firing notifications — operator un-share is
    # silent by design. Staff-only, same gate as DELETE.
    if "shared_at" in data and not bearer_is_admin(request.user):
        return error_response(
            "Plan share is staff-only",
            "forbidden_other_user_plan",
            status=403,
        )

    # Detect whether the request is asking us to reconcile any nested
    # collection. If yes, we wrap everything in an atomic block; if no,
    # we take the legacy top-level-only path so simple PATCH callers
    # see the same behaviour they did before issue #734.
    reconciles_children = any(
        key in data for key in RECONCILABLE_COLLECTIONS
    )

    fire_plan_shared = False

    if reconciles_children:
        # Atomic path: any validation failure rolls back ALL writes
        # (top-level field updates AND nested child mutations). The
        # share-notification fire is deferred until after this block
        # commits — that path is unchanged by issue #734 and is the
        # contract from #732.
        with transaction.atomic():
            err, fire_plan_shared = _apply_top_level_fields(plan, data)
            if err is not None:
                transaction.set_rollback(True)
                return err
            err = _reconcile_children(plan, data)
            if err is not None:
                transaction.set_rollback(True)
                return err
    else:
        # Legacy path: no atomic block needed (top-level fields are a
        # single ``save`` call). This preserves the pre-#734 behaviour
        # for callers that only PATCH ``status`` / ``goal`` /
        # ``accountability`` / ``summary`` / ``focus`` / ``shared_at``.
        err, fire_plan_shared = _apply_top_level_fields(plan, data)
        if err is not None:
            return err

    # Issue #732: fire bell + email AFTER the save so the timestamp is
    # already committed. A failure in the notification helper must not
    # roll back the PATCH; the helper itself logs SES exceptions. This
    # is OUTSIDE the atomic block on purpose.
    if fire_plan_shared:
        try:
            NotificationService.create_plan_shared(plan)
        except Exception:
            logger.exception(
                'Failed to fire plan_shared notification for plan %s',
                plan.pk,
            )

    plan = _refetch_plan_detail(plan.pk)
    return JsonResponse(
        serialize_plan_detail(plan, viewer=request.user),
        status=200,
    )


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Plans",
    summary="Move unfinished plan items to another sprint",
    methods={
        "POST": {
            "summary": "Move unfinished items (staff-only)",
            "description": (
                "Staff-token-only. Atomically moves unfinished Checkpoint, "
                "Deliverable, and NextStep rows from the source plan into "
                "the same member's later target sprint. Completed items and "
                "all non-work-item plan content remain on the source plan. "
                "If the target plan does not exist, it is created through "
                "the sprint enrollment helper."
            ),
            "request_body": {
                "required": ["target_sprint_slug"],
                "properties": {
                    "target_sprint_slug": {"type": "string"},
                },
                "example": {"target_sprint_slug": "june-2026"},
            },
            "responses": {
                200: {
                    "description": "Move completed.",
                    "example": {
                        "source_plan_id": 91,
                        "source_sprint_slug": "may-2026",
                        "target_plan_id": 118,
                        "target_sprint_slug": "june-2026",
                        "created_target_plan": True,
                        "moved": {
                            "checkpoints": 2,
                            "deliverables": 1,
                            "next_steps": 0,
                            "total": 3,
                        },
                    },
                },
                400: {
                    "description": (
                        "Invalid JSON, non-object body, or missing "
                        "target_sprint_slug."
                    ),
                    "example": {
                        "error": "Missing required field: target_sprint_slug",
                        "code": "missing_field",
                    },
                },
                403: {
                    "description": "Missing/invalid/non-staff token.",
                    "example": {
                        "error": "Moving unfinished items is staff-only",
                        "code": "forbidden_other_user_plan",
                    },
                },
                404: {
                    "description": "Unknown source plan or target sprint.",
                    "example": {
                        "error": "Target sprint not found",
                        "code": "unknown_target_sprint",
                    },
                },
                422: {
                    "description": (
                        "Cancelled target, same/earlier target, source plan "
                        "with zero unfinished items, or target plan without "
                        "weeks."
                    ),
                    "example": {
                        "error": (
                            "Target sprint must be later than the source sprint"
                        ),
                        "code": "target_sprint_not_later",
                    },
                },
            },
        },
    },
)
def plan_move_unfinished(request, plan_id):
    """``POST /api/plans/<id>/move-unfinished`` (issue #1042)."""
    if not bearer_is_admin(request.user):
        return error_response(
            "Moving unfinished items is staff-only",
            "forbidden_other_user_plan",
            status=403,
        )

    source_plan = (
        Plan.objects
        .select_related("member", "sprint")
        .filter(pk=plan_id)
        .first()
    )
    if source_plan is None:
        return error_response(
            "Plan not found",
            "unknown_plan",
            status=404,
        )

    try:
        data = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, ValueError):
        return error_response("Invalid JSON", "invalid_json", status=400)
    if not isinstance(data, dict):
        return error_response(
            "Body must be a JSON object",
            "invalid_type",
            details={"field": "body", "expected": "object"},
        )

    target_slug = data.get("target_sprint_slug")
    if target_slug is None:
        return error_response(
            "Missing required field: target_sprint_slug",
            "missing_field",
            details={"field": "target_sprint_slug"},
        )
    if not isinstance(target_slug, str) or not target_slug.strip():
        return error_response(
            "Invalid target_sprint_slug",
            "validation_error",
            status=422,
            details={"target_sprint_slug": "must be a non-empty string"},
        )
    target_slug = target_slug.strip()

    target_sprint = Sprint.objects.filter(slug=target_slug).first()
    if target_sprint is None:
        return error_response(
            "Target sprint not found",
            "unknown_target_sprint",
            status=404,
            details={"target_sprint_slug": target_slug},
        )

    try:
        summary = move_unfinished_items_to_sprint(
            source_plan=source_plan,
            target_sprint=target_sprint,
            actor=request.user,
        )
    except MoveUnfinishedItemsError as exc:
        return error_response(
            exc.message,
            exc.code,
            status=exc.status,
            details=exc.details,
        )

    return JsonResponse(summary, status=200)


def _apply_top_level_fields(plan, data):
    """Apply every top-level PATCH field on ``plan`` and persist.

    Returns ``(error_response_or_None, fire_plan_shared_bool)``. The
    boolean signals whether the caller should fire the share
    notification AFTER the surrounding transaction commits.
    """
    update_fields = []

    # Issue #728: ``status`` is no longer a model field. PATCH silently
    # ignores it for backwards compatibility (matches the existing
    # convention for ``id`` / ``user_email`` / ``sprint`` on PATCH).

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
            ), False
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
            ), False
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
            return err, False
    if summary:
        update_fields.extend(_apply_summary(plan, summary))

    if "focus" in data and isinstance(data["focus"], dict):
        err = _validate_focus(data["focus"])
        if err is not None:
            return err, False
        focus_fields = _apply_focus(plan, data["focus"])
        update_fields.extend(focus_fields)

    if "title" in data:
        title, err = _validate_title(data["title"])
        if err is not None:
            return err, False
        plan.title = title or plan.fallback_title()
        update_fields.append("title")

    fire_plan_shared = False
    if "shared_at" in data:
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

    return None, fire_plan_shared


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Plans",
    summary="Draft a member's next-sprint plan (carry-over + AI draft)",
    methods={
        "POST": {
            "summary": "Draft next-sprint plan",
            "description": (
                "Staff-only. Runs the same shared service as the Studio "
                "\"Draft next sprint plan\" button: carry-over of unfinished "
                "tasks always runs first; an LLM draft of the next-sprint "
                "narrative is produced and stored aside ONLY when the LLM "
                "service is enabled. The draft is never written into the "
                "plan's live fields. With the LLM off this is a 200 with "
                "``draft: null`` (NOT an error) and carry-over still ran."
            ),
            "responses": {
                200: {
                    "description": "Carry-over (and optionally a draft) ran.",
                    "example": {
                        "carried_over": 3,
                        "llm_enabled": True,
                        "source_plan_id": 12,
                        "draft": {
                            "summary_current_situation": "...",
                            "summary_goal": "...",
                            "summary_main_gap": "...",
                            "summary_weekly_hours": "~6 hours/week",
                            "goal": "Ship a working RAG prototype",
                            "suggested_next_steps": ["..."],
                            "rationale": "...",
                        },
                    },
                },
                403: {"description": "Non-staff bearer."},
                404: {"description": "Plan not found."},
            },
        },
    },
)
def plan_draft_next_sprint(request, plan_id):
    """``POST /api/plans/<id>/draft-next-sprint`` (issue #891, Phase 3).

    Staff-only. Single shared code path with the Studio view via
    :func:`plans.services.draft_next_sprint_plan`. Returns
    ``{carried_over, draft, llm_enabled, source_plan_id}``. The disabled-LLM
    case is a 200 with ``draft: null`` — only a missing plan or a non-staff
    bearer is a 4xx.
    """
    if not bearer_is_admin(request.user):
        return error_response(
            "Drafting a next-sprint plan is staff-only",
            "forbidden_other_user_plan",
            status=403,
        )

    plan = (
        Plan.objects
        .select_related("member", "sprint")
        .prefetch_related(
            "weeks__checkpoints",
            "weeks__notes__author",
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

    outcome = draft_next_sprint_plan(destination_plan=plan, actor=request.user)
    draft_result = outcome["draft_result"]
    source_plan = outcome["source_plan"]

    return JsonResponse(
        {
            "carried_over": outcome["carried_over"],
            "llm_enabled": outcome["llm_enabled"],
            "source_plan_id": source_plan.pk if source_plan is not None else None,
            "draft": draft_result.model_dump() if draft_result is not None else None,
        },
        status=200,
    )
