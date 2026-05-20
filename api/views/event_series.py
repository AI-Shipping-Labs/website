"""Staff token API for EventSeries + bulk occurrences (issue #678).

Source-of-truth contract:
- Series can be listed, created, fetched and patched.
- Hiding a series is a PATCH with ``{"is_active": false}`` — there is no
  ``DELETE`` exposed.
- Occurrences are ``Event`` rows. Bulk creation links them to the parent
  series with sequential ``series_position`` values and is atomic.
- Cancelling an occurrence is a PATCH with ``{"status": "cancelled"}`` —
  reuses the existing ``EVENT_STATUS_CHOICES`` value. There is no
  ``DELETE`` exposed.
- v1 cancellation notifications: the API does NOT auto-send attendee
  emails when an occurrence is cancelled. Reschedule notifications stay
  on the Studio path (#670); cancellation emails are a future issue. A
  ``notify=true`` query flag can be wired up later without breaking the
  current contract.
"""

import json as _json
from datetime import time as time_cls

from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils.dateparse import parse_datetime
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import parse_json_body, require_methods
from api.views.events import (
    READ_ONLY_FIELDS,
    _apply_event_values,
    _body_must_be_object_response,
    _collect_event_values,
    _save_event_or_error,
    _validation_response,
    serialize_event,
)
from events.models import Event, EventSeries
from events.models.event_series import EVENT_SERIES_CADENCE_CHOICES

SERIES_DELETE_NOT_AVAILABLE_MESSAGE = (
    "Event series deletion is not available through the API. "
    "Hide the series with PATCH is_active=false, or use Studio to delete it."
)
OCCURRENCE_DELETE_NOT_AVAILABLE_MESSAGE = (
    "Occurrence deletion is not available through the API. "
    "Cancel with PATCH status=\"cancelled\", or use Studio to delete it."
)

VALID_CADENCES = {value for value, _label in EVENT_SERIES_CADENCE_CHOICES}

_VALID_CADENCES_ENUM = sorted(VALID_CADENCES)

_EVENT_SERIES_EXAMPLE = {
    "id": 1,
    "name": "Weekly Office Hours",
    "slug": "weekly-office-hours",
    "description": "Open Q&A every Tuesday.",
    "cadence": "weekly",
    "cadence_weeks": 1,
    "day_of_week": 1,
    "start_time": "17:00:00",
    "timezone": "Europe/Berlin",
    "is_active": True,
    "event_count": 12,
    "published_event_count": 10,
    "created_at": "2026-04-15T12:00:00+00:00",
    "updated_at": "2026-04-15T12:00:00+00:00",
}

SERIES_WRITABLE_FIELDS = {
    "name",
    "slug",
    "description",
    "cadence",
    "cadence_weeks",
    "day_of_week",
    "start_time",
    "timezone",
    "is_active",
}


def _iso(value):
    return value.isoformat() if value is not None else None


def serialize_event_series(series):
    """Return the canonical event-series object for list/detail/create/update."""
    return {
        "id": series.id,
        "name": series.name,
        "slug": series.slug,
        "description": series.description,
        "cadence": series.cadence,
        "cadence_weeks": series.cadence_weeks,
        "day_of_week": series.day_of_week,
        "start_time": (
            series.start_time.isoformat() if series.start_time else None
        ),
        "timezone": series.timezone,
        "is_active": series.is_active,
        "event_count": series.event_count,
        "published_event_count": series.published_event_count,
        "created_at": _iso(series.created_at),
        "updated_at": _iso(series.updated_at),
    }


def _series_delete_not_available_response():
    return error_response(
        SERIES_DELETE_NOT_AVAILABLE_MESSAGE,
        "series_delete_not_available",
        status=405,
    )


def _occurrence_delete_not_available_response():
    return error_response(
        OCCURRENCE_DELETE_NOT_AVAILABLE_MESSAGE,
        "occurrence_delete_not_available",
        status=405,
    )


def _unknown_series_response():
    return error_response(
        "Event series not found",
        "unknown_series",
        status=404,
    )


def _unknown_occurrence_response():
    return error_response(
        "Event not found",
        "unknown_event",
        status=404,
    )


def _parse_bool_query(value):
    """Parse a query-string boolean. ``None`` means missing."""
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in ("true", "1", "yes"):
        return True
    if lowered in ("false", "0", "no"):
        return False
    return None


def _collect_series_values(data, *, existing=None):
    """Validate the series payload and return ``(values, errors)``."""
    errors = {}
    values = {}

    if "name" in data:
        name = data["name"]
        if name is None:
            name = ""
        if not isinstance(name, str) or not name.strip():
            errors["name"] = "Name is required."
        else:
            values["name"] = name.strip()
    elif existing is None:
        errors["name"] = "Name is required."

    if "slug" in data:
        raw_slug = data["slug"]
        if raw_slug is None:
            raw_slug = ""
        if not isinstance(raw_slug, str):
            errors["slug"] = "Must be a string."
        else:
            values["slug"] = raw_slug.strip()

    if "description" in data:
        desc = data["description"]
        if desc is None:
            desc = ""
        if not isinstance(desc, str):
            errors["description"] = "Must be a string."
        else:
            values["description"] = desc

    if "cadence" in data:
        cadence = data["cadence"]
        if cadence not in VALID_CADENCES:
            errors["cadence"] = "Unknown choice."
        else:
            values["cadence"] = cadence

    if "cadence_weeks" in data:
        try:
            cadence_weeks = int(data["cadence_weeks"])
        except (TypeError, ValueError):
            cadence_weeks = None
        if cadence_weeks is None or cadence_weeks < 1:
            errors["cadence_weeks"] = "Must be a positive integer."
        else:
            values["cadence_weeks"] = cadence_weeks

    if "day_of_week" in data:
        try:
            day_of_week = int(data["day_of_week"])
        except (TypeError, ValueError):
            day_of_week = None
        if day_of_week is None or day_of_week < 0 or day_of_week > 6:
            errors["day_of_week"] = "Must be an integer between 0 and 6."
        else:
            values["day_of_week"] = day_of_week
    elif existing is None:
        errors["day_of_week"] = "Day of week is required."

    if "start_time" in data:
        start_time = data["start_time"]
        if not isinstance(start_time, str):
            errors["start_time"] = "Must be a HH:MM or HH:MM:SS string."
        else:
            parsed = _parse_time_string(start_time)
            if parsed is None:
                errors["start_time"] = "Must be a HH:MM or HH:MM:SS string."
            else:
                values["start_time"] = parsed
    elif existing is None:
        errors["start_time"] = "Start time is required."

    if "timezone" in data:
        tz_value = data["timezone"]
        if tz_value is None:
            tz_value = ""
        if not isinstance(tz_value, str):
            errors["timezone"] = "Must be a string."
        else:
            values["timezone"] = tz_value.strip() or "Europe/Berlin"

    if "is_active" in data:
        if not isinstance(data["is_active"], bool):
            errors["is_active"] = "Must be a boolean."
        else:
            values["is_active"] = data["is_active"]

    name_for_slug = values.get(
        "name", existing.name if existing is not None else "",
    )
    if "slug" in values and not values["slug"]:
        values["slug"] = slugify(name_for_slug)
    if existing is None and not values.get("slug") and name_for_slug:
        values["slug"] = slugify(name_for_slug)

    if "slug" in values and values["slug"]:
        duplicate_qs = EventSeries.objects.filter(slug=values["slug"])
        if existing is not None:
            duplicate_qs = duplicate_qs.exclude(pk=existing.pk)
        if duplicate_qs.exists():
            errors["slug"] = "Slug already in use."

    return values, errors


def _parse_time_string(value):
    """Parse a HH:MM or HH:MM:SS string into a ``datetime.time``."""
    parts = value.strip().split(":")
    if len(parts) not in (2, 3):
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
    except ValueError:
        return None
    if not (0 <= hour < 24 and 0 <= minute < 60 and 0 <= second < 60):
        return None
    return time_cls(hour, minute, second)


def _apply_series_values(series, values):
    for field, value in values.items():
        if field in SERIES_WRITABLE_FIELDS:
            setattr(series, field, value)


def _save_series_or_error(series):
    try:
        series.save()
    except IntegrityError:
        return _validation_response({"slug": "Slug already in use."})
    return None


@token_required
@csrf_exempt
@require_methods("GET", "POST", "DELETE")
@openapi_spec(
    tag="Event Series",
    summary="List, create, or attempt to delete event series",
    methods={
        "GET": {
            "summary": "List event series",
            "query": {
                "is_active": {
                    "type": "string",
                    "enum": ["true", "false"],
                    "required": False,
                    "description": "Filter on the hide flag.",
                },
                "q": {
                    "type": "string",
                    "required": False,
                    "description": "icontains match on name.",
                },
            },
            "responses": {
                200: {
                    "description": "List of event series.",
                    "example": {
                        "event_series": [_EVENT_SERIES_EXAMPLE],
                    },
                },
            },
        },
        "POST": {
            "summary": "Create an event series",
            "request_body": {
                "required": ["name", "day_of_week", "start_time"],
                "properties": {
                    "name": {"type": "string"},
                    "slug": {"type": "string"},
                    "description": {"type": "string"},
                    "cadence": {
                        "type": "string",
                        "enum": _VALID_CADENCES_ENUM,
                    },
                    "cadence_weeks": {"type": "integer", "minimum": 1},
                    "day_of_week": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 6,
                    },
                    "start_time": {
                        "type": "string",
                        "description": "HH:MM or HH:MM:SS.",
                    },
                    "timezone": {"type": "string"},
                    "is_active": {"type": "boolean"},
                },
                "example": {
                    "name": "Weekly Office Hours",
                    "day_of_week": 1,
                    "start_time": "17:00",
                },
            },
            "responses": {
                201: {
                    "description": "Event series created.",
                    "example": _EVENT_SERIES_EXAMPLE,
                },
                400: {"description": "Invalid JSON body."},
                422: {"description": "Validation error (missing name, bad cadence, slug collision, etc.)."},
            },
        },
        "DELETE": {
            "summary": "DELETE is not available on this route",
            "description": (
                "DELETE returns 405 with a structured error -- "
                "deletion is not exposed through the API. Hide the "
                "series with ``PATCH is_active=false``."
            ),
            "responses": {
                405: {
                    "description": "Series deletion is not available.",
                    "example": {
                        "error": SERIES_DELETE_NOT_AVAILABLE_MESSAGE,
                        "code": "series_delete_not_available",
                    },
                },
            },
        },
    },
)
def event_series_collection(request):
    """GET/POST ``/api/event-series``.

    Optional GET query params:
    - ``is_active`` -- ``true`` / ``false`` filter on the hide flag.
    - ``q`` -- icontains match on ``name``.
    """
    if request.method == "DELETE":
        return _series_delete_not_available_response()

    if request.method == "GET":
        qs = EventSeries.objects.all()
        is_active = _parse_bool_query(request.GET.get("is_active"))
        if is_active is not None:
            qs = qs.filter(is_active=is_active)
        query = request.GET.get("q")
        if query:
            qs = qs.filter(name__icontains=query)
        return JsonResponse(
            {"event_series": [serialize_event_series(s) for s in qs]},
            status=200,
        )

    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return _body_must_be_object_response()

    values, errors = _collect_series_values(data, existing=None)
    if errors:
        return _validation_response(errors)

    series = EventSeries(
        cadence="weekly",
        cadence_weeks=1,
        timezone="Europe/Berlin",
        is_active=True,
    )
    _apply_series_values(series, values)
    save_error = _save_series_or_error(series)
    if save_error is not None:
        return save_error
    return JsonResponse(serialize_event_series(series), status=201)


@token_required
@csrf_exempt
@require_methods("GET", "PATCH", "DELETE")
@openapi_spec(
    tag="Event Series",
    summary="Retrieve, update, or attempt to delete an event series",
    methods={
        "GET": {
            "summary": "Retrieve an event series",
            "description": (
                "Returns the series plus an inlined ``occurrences`` "
                "array using the same shape as the events API."
            ),
            "responses": {
                200: {
                    "description": "Event series with occurrences.",
                    "example": {
                        **_EVENT_SERIES_EXAMPLE,
                        "occurrences": [],
                    },
                },
                404: {
                    "description": "Event series not found.",
                    "example": {
                        "error": "Event series not found",
                        "code": "unknown_series",
                    },
                },
            },
        },
        "PATCH": {
            "summary": "Update an event series",
            "description": (
                "Partial update. Setting ``is_active=false`` hides the "
                "series without touching its occurrences."
            ),
            "request_body": {
                "properties": {
                    "name": {"type": "string"},
                    "slug": {"type": "string"},
                    "description": {"type": "string"},
                    "cadence": {
                        "type": "string",
                        "enum": _VALID_CADENCES_ENUM,
                    },
                    "cadence_weeks": {"type": "integer", "minimum": 1},
                    "day_of_week": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 6,
                    },
                    "start_time": {"type": "string"},
                    "timezone": {"type": "string"},
                    "is_active": {"type": "boolean"},
                },
                "example": {"is_active": False},
            },
            "responses": {
                200: {
                    "description": "Event series updated.",
                    "example": _EVENT_SERIES_EXAMPLE,
                },
                400: {"description": "Invalid JSON body."},
                404: {"description": "Event series not found."},
                422: {"description": "Validation error."},
            },
        },
        "DELETE": {
            "summary": "DELETE is not available on this route",
            "responses": {
                405: {
                    "description": "Series deletion is not available.",
                    "example": {
                        "error": SERIES_DELETE_NOT_AVAILABLE_MESSAGE,
                        "code": "series_delete_not_available",
                    },
                },
            },
        },
    },
)
def event_series_detail(request, series_id):
    """GET/PATCH ``/api/event-series/<series_id>``.

    GET returns the series plus an inlined ``occurrences`` array using
    ``serialize_event`` so the shape matches ``/api/events``.

    PATCH updates writable fields, including ``is_active``. Setting
    ``is_active=false`` hides the series without touching its occurrences.
    """
    if request.method == "DELETE":
        return _series_delete_not_available_response()

    series = EventSeries.objects.filter(pk=series_id).first()
    if series is None:
        return _unknown_series_response()

    if request.method == "GET":
        body = serialize_event_series(series)
        body["occurrences"] = [
            serialize_event(event)
            for event in series.events.all().order_by(
                "series_position", "start_datetime",
            )
        ]
        return JsonResponse(body, status=200)

    # PATCH
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return _body_must_be_object_response()

    values, errors = _collect_series_values(data, existing=series)
    if errors:
        return _validation_response(errors)

    _apply_series_values(series, values)
    save_error = _save_series_or_error(series)
    if save_error is not None:
        return save_error
    return JsonResponse(serialize_event_series(series), status=200)


def _dedup_key(start_datetime):
    """Round to the minute for the dedup key."""
    if start_datetime is None:
        return None
    return start_datetime.replace(second=0, microsecond=0)


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Event Series",
    summary="Bulk-create occurrences for an event series",
    methods={
        "POST": {
            "summary": "Bulk create occurrences",
            "description": (
                "Atomic create of N events linked to ``series_id``. "
                "Any per-row validation failure rolls the whole batch "
                "back; the response includes the failing index. "
                "Dedup key is ``(event_series_id, start_datetime)`` "
                "rounded to the minute -- in-batch duplicates return "
                "422 ``duplicate_in_batch``, and rows matching existing "
                "series events are silently counted as "
                "``skipped_existing`` for idempotent retries."
            ),
            "request_body": {
                "required": ["occurrences"],
                "properties": {
                    "occurrences": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "start_datetime": {
                                    "type": "string",
                                    "format": "date-time",
                                },
                                "title": {"type": "string"},
                                "slug": {"type": "string"},
                            },
                            "required": ["start_datetime"],
                        },
                    },
                },
                "example": {
                    "occurrences": [
                        {"start_datetime": "2026-05-05T17:00:00+02:00"},
                        {"start_datetime": "2026-05-12T17:00:00+02:00"},
                    ],
                },
            },
            "responses": {
                201: {
                    "description": "Bulk create summary.",
                    "example": {
                        "created": 2,
                        "skipped_existing": 0,
                        "occurrence_ids": [42, 43],
                    },
                },
                400: {
                    "description": "Invalid JSON or missing field.",
                    "example": {
                        "error": "Missing required field: start_datetime",
                        "code": "missing_field",
                        "details": {"index": 0, "field": "start_datetime"},
                    },
                },
                404: {"description": "Event series not found."},
                422: {
                    "description": (
                        "Validation error or in-batch duplicate "
                        "start_datetime."
                    ),
                    "example": {
                        "error": "Duplicate start_datetime within the batch",
                        "code": "duplicate_in_batch",
                        "details": {
                            "indexes": [0, 1],
                            "start_datetime": "2026-05-05T17:00:00",
                        },
                    },
                },
            },
        },
    },
)
def event_series_occurrences_bulk(request, series_id):
    """``POST /api/event-series/<series_id>/occurrences/bulk``.

    Atomic create of N events linked to ``series_id``. Any per-row
    validation failure rolls the whole batch back; the response includes
    the failing index.

    Dedup key: ``(event_series_id, start_datetime)`` rounded to the
    minute. In-batch duplicates return 422 ``duplicate_in_batch``. Rows
    matching an existing event already linked to the series are silently
    counted as ``skipped_existing`` so resubmitting the same payload is
    idempotent.
    """
    series = EventSeries.objects.filter(pk=series_id).first()
    if series is None:
        return _unknown_series_response()

    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return _body_must_be_object_response()

    payload = data.get("occurrences")
    if payload is None:
        return error_response(
            "Missing required field: occurrences",
            "missing_field",
            details={"field": "occurrences"},
        )
    if not isinstance(payload, list):
        return error_response(
            "occurrences must be a list",
            "invalid_type",
            details={"field": "occurrences", "expected": "list"},
        )

    # Pre-pass: validate per-row payloads + collect dedup keys. This lets
    # us detect in-batch duplicates and prepare DB existence checks
    # before opening the transaction.
    parsed_rows = []  # list of (index, payload_dict, parsed_start)
    for index, row in enumerate(payload):
        if not isinstance(row, dict):
            return error_response(
                "occurrences entries must be objects",
                "validation_error",
                status=422,
                details={"index": index},
            )
        raw_start = row.get("start_datetime")
        if not raw_start:
            return error_response(
                "Missing required field: start_datetime",
                "missing_field",
                status=400,
                details={"index": index, "field": "start_datetime"},
            )
        if not isinstance(raw_start, str):
            return error_response(
                "start_datetime must be an ISO 8601 datetime string",
                "validation_error",
                status=422,
                details={
                    "index": index,
                    "start_datetime": "Must be an ISO 8601 datetime.",
                },
            )
        parsed_start = parse_datetime(raw_start)
        if parsed_start is None:
            return error_response(
                "start_datetime must be an ISO 8601 datetime string",
                "validation_error",
                status=422,
                details={
                    "index": index,
                    "start_datetime": "Must be an ISO 8601 datetime.",
                },
            )
        parsed_rows.append((index, row, parsed_start))

    # In-batch dedup. Two rows that round to the same minute collide.
    seen_keys = {}
    for index, _row, parsed_start in parsed_rows:
        key = _dedup_key(parsed_start)
        if key in seen_keys:
            return error_response(
                "Duplicate start_datetime within the batch",
                "duplicate_in_batch",
                status=422,
                details={
                    "indexes": [seen_keys[key], index],
                    "start_datetime": parsed_start.isoformat(),
                },
            )
        seen_keys[key] = index

    # DB dedup: existing rows already linked to this series sharing the
    # minute-rounded key are silently skipped. The minute-rounding is
    # done in Python because SQLite's strftime is not 100% portable for
    # microsecond stripping.
    existing_starts = set()
    series_event_starts = (
        Event.objects.filter(event_series=series)
        .values_list("start_datetime", flat=True)
    )
    for value in series_event_starts:
        rounded = _dedup_key(value)
        if rounded is not None:
            existing_starts.add(rounded)

    occurrence_ids = []
    skipped_existing = 0

    with transaction.atomic():
        max_pos = (
            Event.objects.filter(event_series=series)
            .exclude(series_position__isnull=True)
            .order_by("-series_position")
            .values_list("series_position", flat=True)
            .first()
        )
        next_position = (max_pos or 0) + 1

        for index, row, parsed_start in parsed_rows:
            key = _dedup_key(parsed_start)
            if key in existing_starts:
                skipped_existing += 1
                continue

            # Build the per-occurrence payload that ``_collect_event_values``
            # expects. Inject series defaults where the row leaves a field
            # blank so the operator can pass just ``start_datetime``.
            row_payload = dict(row)
            if "timezone" not in row_payload:
                row_payload["timezone"] = series.timezone
            if "title" not in row_payload or not str(
                row_payload.get("title") or "",
            ).strip():
                row_payload["title"] = (
                    f"{series.name} — Session {next_position}"
                )
            if "slug" not in row_payload or not str(
                row_payload.get("slug") or "",
            ).strip():
                row_payload["slug"] = (
                    f"{series.slug}-session-{next_position}"
                )

            values, errors = _collect_event_values(
                row_payload, existing=None,
            )
            if errors:
                transaction.set_rollback(True)
                details = {"index": index}
                details.update(errors)
                return error_response(
                    "Validation error",
                    "validation_error",
                    status=422,
                    details=details,
                )

            event = Event(
                kind="standard",
                platform="zoom",
                timezone=series.timezone,
                status="draft",
                required_level=0,
                published=True,
                tags=[],
                location="",
                external_host="",
                max_participants=None,
                origin="studio",
                source_repo="",
                event_series=series,
                series_position=next_position,
            )
            _apply_event_values(event, values)
            save_error = _save_event_or_error(event)
            if save_error is not None:
                transaction.set_rollback(True)
                # Bubble the validation error code/body but annotate the
                # offending index for the caller.
                try:
                    body = save_error.content
                    decoded = _json.loads(body.decode("utf-8"))
                    decoded.setdefault("details", {})
                    decoded["details"]["index"] = index
                    return JsonResponse(decoded, status=save_error.status_code)
                except (ValueError, AttributeError):
                    return save_error

            occurrence_ids.append(event.pk)
            # Mark this dedup key as seen so a transient duplicate in the
            # payload after a DB write is still treated correctly even
            # though the in-batch check above already covers it.
            existing_starts.add(key)
            next_position += 1

    return JsonResponse(
        {
            "created": len(occurrence_ids),
            "skipped_existing": skipped_existing,
            "occurrence_ids": occurrence_ids,
        },
        status=201,
    )


@token_required
@csrf_exempt
@require_methods("GET", "PATCH", "DELETE")
@openapi_spec(
    tag="Event Series",
    summary="Retrieve, update, or attempt to delete a series occurrence",
    methods={
        "GET": {
            "summary": "Retrieve an occurrence",
            "responses": {
                200: {"description": "Occurrence detail."},
                404: {
                    "description": (
                        "Event series or occurrence not found."
                    ),
                    "example": {
                        "error": "Event not found",
                        "code": "unknown_event",
                    },
                },
            },
        },
        "PATCH": {
            "summary": "Update an occurrence",
            "description": (
                "Cancel with ``{\"status\": \"cancelled\"}``. The API "
                "does NOT auto-send attendee notifications in v1."
            ),
            "request_body": {
                "properties": {
                    "title": {"type": "string"},
                    "status": {"type": "string"},
                    "start_datetime": {
                        "type": "string",
                        "format": "date-time",
                    },
                    "end_datetime": {
                        "type": "string",
                        "format": "date-time",
                    },
                },
                "example": {"status": "cancelled"},
            },
            "responses": {
                200: {"description": "Occurrence updated."},
                400: {"description": "Invalid JSON body."},
                404: {"description": "Series or occurrence not found."},
                422: {
                    "description": (
                        "Validation error or attempt to write a "
                        "read-only field."
                    ),
                },
            },
        },
        "DELETE": {
            "summary": "DELETE is not available on this route",
            "description": (
                "Cancel an occurrence with ``PATCH status=cancelled`` "
                "instead. DELETE returns a structured 405."
            ),
            "responses": {
                405: {
                    "description": "Occurrence deletion is not available.",
                    "example": {
                        "error": OCCURRENCE_DELETE_NOT_AVAILABLE_MESSAGE,
                        "code": "occurrence_delete_not_available",
                    },
                },
            },
        },
    },
)
def event_series_occurrence_detail(request, series_id, occurrence_id):
    """GET/PATCH ``/api/event-series/<series_id>/occurrences/<occurrence_id>``.

    Cancel an occurrence with ``{"status": "cancelled"}``. The API does
    NOT auto-send attendee notifications in v1 (see module docstring);
    cancellation emails stay a Studio-path concern until a future issue
    threads through a ``notify=true`` flag and a dedicated template.

    NO DELETE — cancel via PATCH only.
    """
    if request.method == "DELETE":
        return _occurrence_delete_not_available_response()

    series = EventSeries.objects.filter(pk=series_id).first()
    if series is None:
        return _unknown_series_response()

    event = Event.objects.filter(
        pk=occurrence_id, event_series=series,
    ).first()
    if event is None:
        return _unknown_occurrence_response()

    if request.method == "GET":
        return JsonResponse(serialize_event(event), status=200)

    # PATCH
    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return _body_must_be_object_response()

    # Read-only check matches ``api/views/events.py``.
    for field in sorted(READ_ONLY_FIELDS):
        if field in data:
            return error_response(
                f"{field} is read-only",
                "read_only_field",
                status=422,
                details={"field": field},
            )

    values, errors = _collect_event_values(data, existing=event)
    if errors:
        return _validation_response(errors)

    _apply_event_values(event, values)
    save_error = _save_event_or_error(event)
    if save_error is not None:
        return save_error
    return JsonResponse(serialize_event(event), status=200)
