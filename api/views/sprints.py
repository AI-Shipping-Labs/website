"""Sprint endpoints for the plans API (issue #433).

Six endpoints:

- ``GET /api/sprints/`` -- list, optional ``status`` filter
- ``POST /api/sprints/`` -- create (staff-only)
- ``GET /api/sprints/<slug>/`` -- detail
- ``PATCH /api/sprints/<slug>/`` -- update (staff-only)
- ``DELETE /api/sprints/<slug>/`` -- delete (staff-only); 409 if plans attached
- ``GET /api/sprints/<slug>/progress-evidence`` -- staff progress evidence
"""

from datetime import date

from django.db.models import Prefetch
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from accounts.utils.display import display_name
from api.openapi import openapi_spec
from api.safety import error_response
from api.serializers.plans import serialize_sprint
from api.utils import parse_json_body, require_methods
from api.views._permissions import bearer_is_admin, visible_plans_for
from crm.models import SlackMessage, SlackThread
from plans.models import (
    SPRINT_STATUS_CHOICES,
    Checkpoint,
    Deliverable,
    NextStep,
    Plan,
    Sprint,
    SprintEnrollment,
)
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

_PROGRESS_EVIDENCE_EXAMPLE = {
    "source_sprint": {
        "slug": "may-2026",
        "name": "May 2026",
        "start_date": "2026-05-01",
        "end_date": "2026-06-12",
        "duration_weeks": 6,
        "status": "active",
    },
    "target_sprint": None,
    "totals": {
        "members": 1,
        "app_progress": 1,
        "crm_update_progress": 0,
        "both": 0,
        "none": 0,
        "target_enrolled": 0,
        "target_plan_exists": 0,
    },
    "members": [
        {
            "member": {
                "id": 42,
                "email": "member@example.com",
                "display_name": "Member Example",
            },
            "source_enrollment": {
                "id": 12,
                "enrolled_at": "2026-05-01T12:00:00+00:00",
            },
            "source_plan": {
                "id": 88,
                "goal": "Ship the first agent workflow",
                "shared_at": "2026-05-02T12:00:00+00:00",
            },
            "target": None,
            "app_progress": {
                "total_done": 1,
                "checkpoints_done": 1,
                "deliverables_done": 0,
                "next_steps_done": 0,
                "latest_done_at": "2026-05-08T12:00:00+00:00",
                "evidence": [
                    {
                        "kind": "checkpoint",
                        "id": 501,
                        "done_at": "2026-05-08T12:00:00+00:00",
                        "description": "Publish a working demo",
                    },
                ],
            },
            "crm_progress": {
                "threads_count": 0,
                "parsed_events_count": 0,
                "applied_changes_count": 0,
                "latest_thread_posted_at": None,
                "latest_event_applied_at": None,
                "threads": [],
            },
            "evidence_status": "app_progress",
            "evidence_reasons": ["app_progress"],
        },
    ],
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


def _isoformat_or_none(value):
    if value is None:
        return None
    return value.isoformat()


def _snippet(value, *, limit=180):
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def _serialize_sprint_summary(sprint):
    return {
        "slug": sprint.slug,
        "name": sprint.name,
        "start_date": (
            sprint.start_date.isoformat() if sprint.start_date else None
        ),
        "end_date": (
            sprint.end_date.isoformat() if sprint.end_date else None
        ),
        "duration_weeks": sprint.duration_weeks,
        "status": sprint.status,
    }


def _serialize_plan_summary(plan):
    if plan is None:
        return None
    return {
        "id": plan.id,
        "goal": plan.goal,
        "shared_at": _isoformat_or_none(plan.shared_at),
    }


def _progress_item(kind, item):
    return {
        "kind": kind,
        "id": item.id,
        "done_at": _isoformat_or_none(item.done_at),
        "description": _snippet(item.description),
    }


def _app_progress_for(plan, progress_by_plan):
    if plan is None:
        return {
            "total_done": 0,
            "checkpoints_done": 0,
            "deliverables_done": 0,
            "next_steps_done": 0,
            "latest_done_at": None,
            "evidence": [],
        }

    progress = progress_by_plan.get(
        plan.id,
        {"checkpoint": [], "deliverable": [], "next_step": []},
    )
    evidence = []
    for kind, items in progress.items():
        evidence.extend(_progress_item(kind, item) for item in items)
    evidence.sort(key=lambda row: (row["done_at"] or "", row["kind"], row["id"]))
    latest_done_at = max(
        (row["done_at"] for row in evidence if row["done_at"]),
        default=None,
    )
    return {
        "total_done": len(evidence),
        "checkpoints_done": len(progress["checkpoint"]),
        "deliverables_done": len(progress["deliverable"]),
        "next_steps_done": len(progress["next_step"]),
        "latest_done_at": latest_done_at,
        "evidence": evidence,
    }


def _serialize_progress_change(change):
    item = change.item
    item_id = item.id if item is not None else None
    return {
        "id": change.id,
        "item_kind": change.item_kind,
        "item_id": item_id,
        "item_description": _snippet(change.item_description),
        "applied_at": _isoformat_or_none(change.applied_at),
    }


def _serialize_progress_event(event):
    if event is None:
        return None
    changes = list(event.changes.all())
    return {
        "id": event.id,
        "applied_at": _isoformat_or_none(event.applied_at),
        "summary": event.summary,
        "blockers": event.blockers or [],
        "model_name": event.model_name,
        "source_message_ts": event.source_message_ts,
        "changes": [_serialize_progress_change(change) for change in changes],
    }


def _serialize_slack_message(message):
    return {
        "id": message.id,
        "ts": message.ts,
        "author_display": message.author_display,
        "posted_at": _isoformat_or_none(message.posted_at),
        "is_root": message.is_root,
        "text": message.text,
    }


def _thread_progress_event(thread):
    events = list(thread.progress_events.all())
    return events[0] if events else None


def _serialize_thread(thread):
    messages = list(thread.messages.all())
    root = next((message for message in messages if message.is_root), None)
    if root is None and messages:
        root = messages[0]
    event = _thread_progress_event(thread)
    return {
        "id": thread.id,
        "channel_id": thread.channel_id,
        "thread_ts": thread.thread_ts,
        "posted_at": _isoformat_or_none(thread.posted_at),
        "permalink": thread.permalink,
        "reply_count": thread.reply_count,
        "root_message": _snippet(root.text if root else ""),
        "messages": [_serialize_slack_message(message) for message in messages],
        "progress_event": _serialize_progress_event(event),
    }


def _crm_progress_for(plan, threads_by_plan):
    threads = threads_by_plan.get(plan.id, []) if plan is not None else []
    events = [
        event
        for thread in threads
        for event in list(thread.progress_events.all())
    ]
    changes = [
        change
        for event in events
        for change in list(event.changes.all())
    ]
    return {
        "threads_count": len(threads),
        "parsed_events_count": len(events),
        "applied_changes_count": len(changes),
        "latest_thread_posted_at": _isoformat_or_none(
            max((thread.posted_at for thread in threads), default=None),
        ),
        "latest_event_applied_at": _isoformat_or_none(
            max((event.applied_at for event in events), default=None),
        ),
        "threads": [_serialize_thread(thread) for thread in threads],
    }


def _evidence_status(app_progress, crm_progress):
    app_has_progress = app_progress["total_done"] > 0
    crm_has_progress = crm_progress["threads_count"] > 0
    if app_has_progress and crm_has_progress:
        return "both", ["app_progress", "crm_update_progress"]
    if app_has_progress:
        return "app_progress", ["app_progress"]
    if crm_has_progress:
        return "crm_update_progress", ["crm_update_progress"]
    return "none", []


def _collect_progress_by_plan(plan_ids):
    progress_by_plan = {
        plan_id: {"checkpoint": [], "deliverable": [], "next_step": []}
        for plan_id in plan_ids
    }
    checkpoints = Checkpoint.objects.filter(
        week__plan_id__in=plan_ids,
        done_at__isnull=False,
    ).select_related("week").order_by("done_at", "id")
    for checkpoint in checkpoints:
        progress_by_plan[checkpoint.week.plan_id]["checkpoint"].append(
            checkpoint
        )

    deliverables = Deliverable.objects.filter(
        plan_id__in=plan_ids,
        done_at__isnull=False,
    ).order_by("done_at", "id")
    for deliverable in deliverables:
        progress_by_plan[deliverable.plan_id]["deliverable"].append(
            deliverable
        )

    next_steps = NextStep.objects.filter(
        plan_id__in=plan_ids,
        done_at__isnull=False,
    ).order_by("done_at", "id")
    for next_step in next_steps:
        progress_by_plan[next_step.plan_id]["next_step"].append(next_step)
    return progress_by_plan


def _collect_threads_by_plan(plan_ids):
    messages_qs = SlackMessage.objects.order_by("posted_at", "id")
    threads = (
        SlackThread.objects.filter(plan_id__in=plan_ids)
        .prefetch_related(
            Prefetch("messages", queryset=messages_qs),
            "progress_events__changes__checkpoint",
            "progress_events__changes__deliverable",
            "progress_events__changes__next_step",
        )
        .order_by("posted_at", "id")
    )
    threads_by_plan = {plan_id: [] for plan_id in plan_ids}
    for thread in threads:
        threads_by_plan[thread.plan_id].append(thread)
    return threads_by_plan


def _target_row(target_sprint, member, target_enrollments, target_plans):
    if target_sprint is None:
        return None
    enrollment = target_enrollments.get(member.id)
    plan = target_plans.get(member.id)
    return {
        "sprint_slug": target_sprint.slug,
        "enrollment_id": enrollment.id if enrollment else None,
        "enrolled": enrollment is not None,
        "plan_id": plan.id if plan else None,
        "plan_exists": plan is not None,
    }


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


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Sprints",
    summary="Report sprint progress evidence",
    methods={
        "GET": {
            "summary": "Report source-sprint member progress evidence",
            "description": (
                "Staff-token-only read API for next-sprint operations. "
                "The member set comes from the source sprint's enrollments. "
                "Evidence combines app-recorded source-plan done_at progress "
                "with CRM-held #plan-sprints Slack threads linked to the "
                "same source plan. The endpoint does not call Slack, enqueue "
                "ingest, parse messages, or mutate progress rows."
            ),
            "query": {
                "target_sprint": {
                    "type": "string",
                    "required": False,
                    "description": (
                        "Optional target sprint slug. When supplied, each "
                        "member row includes target enrollment and target plan "
                        "state for duplicate checks."
                    ),
                },
            },
            "responses": {
                200: {
                    "description": "Member-level progress evidence report.",
                    "example": _PROGRESS_EVIDENCE_EXAMPLE,
                },
                401: {"description": "Missing or invalid staff token."},
                404: {
                    "description": "Source sprint not found.",
                    "example": {
                        "error": "Sprint not found",
                        "code": "unknown_sprint",
                    },
                },
                422: {
                    "description": "Target sprint not found.",
                    "example": {
                        "error": "Target sprint not found",
                        "code": "unknown_target_sprint",
                    },
                },
            },
        },
    },
)
def sprint_progress_evidence(request, slug):
    """``GET /api/sprints/<slug>/progress-evidence``."""
    source_sprint = Sprint.objects.filter(slug=slug).first()
    if source_sprint is None:
        return error_response(
            "Sprint not found",
            "unknown_sprint",
            status=404,
        )

    target_sprint_slug = request.GET.get("target_sprint")
    target_sprint = None
    if target_sprint_slug:
        target_sprint = Sprint.objects.filter(slug=target_sprint_slug).first()
        if target_sprint is None:
            return error_response(
                "Target sprint not found",
                "unknown_target_sprint",
                status=422,
            )

    enrollments = list(
        SprintEnrollment.objects.filter(sprint=source_sprint)
        .select_related("user")
        .order_by("user__email", "id")
    )
    member_ids = [enrollment.user_id for enrollment in enrollments]

    source_plans = {
        plan.member_id: plan
        for plan in Plan.objects.filter(
            sprint=source_sprint,
            member_id__in=member_ids,
        )
    }
    source_plan_ids = [plan.id for plan in source_plans.values()]
    progress_by_plan = _collect_progress_by_plan(source_plan_ids)
    threads_by_plan = _collect_threads_by_plan(source_plan_ids)

    target_enrollments = {}
    target_plans = {}
    if target_sprint is not None:
        target_enrollments = {
            enrollment.user_id: enrollment
            for enrollment in SprintEnrollment.objects.filter(
                sprint=target_sprint,
                user_id__in=member_ids,
            )
        }
        target_plans = {
            plan.member_id: plan
            for plan in Plan.objects.filter(
                sprint=target_sprint,
                member_id__in=member_ids,
            )
        }

    totals = {
        "members": len(enrollments),
        "app_progress": 0,
        "crm_update_progress": 0,
        "both": 0,
        "none": 0,
        "target_enrolled": 0,
        "target_plan_exists": 0,
    }
    rows = []
    for enrollment in enrollments:
        member = enrollment.user
        source_plan = source_plans.get(member.id)
        app_progress = _app_progress_for(source_plan, progress_by_plan)
        crm_progress = _crm_progress_for(source_plan, threads_by_plan)
        evidence_status, evidence_reasons = _evidence_status(
            app_progress,
            crm_progress,
        )
        target = _target_row(
            target_sprint,
            member,
            target_enrollments,
            target_plans,
        )
        totals[evidence_status] += 1
        if target is not None:
            totals["target_enrolled"] += int(target["enrolled"])
            totals["target_plan_exists"] += int(target["plan_exists"])

        rows.append({
            "member": {
                "id": member.id,
                "email": member.email,
                "display_name": display_name(member),
            },
            "source_enrollment": {
                "id": enrollment.id,
                "enrolled_at": _isoformat_or_none(enrollment.enrolled_at),
            },
            "source_plan": _serialize_plan_summary(source_plan),
            "target": target,
            "app_progress": app_progress,
            "crm_progress": crm_progress,
            "evidence_status": evidence_status,
            "evidence_reasons": evidence_reasons,
        })

    return JsonResponse(
        {
            "source_sprint": _serialize_sprint_summary(source_sprint),
            "target_sprint": (
                _serialize_sprint_summary(target_sprint)
                if target_sprint is not None else None
            ),
            "totals": totals,
            "members": rows,
        },
        status=200,
    )
