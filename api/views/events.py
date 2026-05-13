"""Staff token API for Studio-origin events (issue #627).

Source-of-truth contract:
- GitHub-origin/synced events are inspectable through this API but read-only.
- Studio/API-origin events can be created and patched.
- Event deletion is intentionally unavailable through the API; operators must
  use Studio for manual deletion so ownership and sync rules stay explicit.
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.safety import error_response
from api.utils import parse_json_body, require_methods
from content.access import VISIBILITY_CHOICES
from events.models import Event
from events.models.event import (
    EVENT_KIND_CHOICES,
    EVENT_ORIGIN_CHOICES,
    EVENT_PLATFORM_CHOICES,
    EVENT_STATUS_CHOICES,
    EXTERNAL_HOST_CHOICES,
)
from studio.utils import is_synced

DELETE_NOT_AVAILABLE_MESSAGE = (
    "Event deletion is not available through the API. "
    "Go to Studio to delete this event manually."
)

READ_ONLY_FIELDS = {
    "origin",
    "source_repo",
    "source_path",
    "source_commit",
    "content_id",
}

WRITABLE_FIELDS = {
    "title",
    "slug",
    "description",
    "kind",
    "platform",
    "start_datetime",
    "end_datetime",
    "timezone",
    "zoom_join_url",
    "location",
    "tags",
    "required_level",
    "max_participants",
    "status",
    "external_host",
    "published",
}

VALID_KINDS = {value for value, _label in EVENT_KIND_CHOICES}
VALID_PLATFORMS = {value for value, _label in EVENT_PLATFORM_CHOICES}
VALID_STATUSES = {value for value, _label in EVENT_STATUS_CHOICES}
VALID_ORIGINS = {value for value, _label in EVENT_ORIGIN_CHOICES}
VALID_EXTERNAL_HOSTS = {value for value, _label in EXTERNAL_HOST_CHOICES}
VALID_REQUIRED_LEVELS = {value for value, _label in VISIBILITY_CHOICES}


def _iso(value):
    return value.isoformat() if value is not None else None


def serialize_event(event):
    """Return the canonical event object for list/detail/create/update."""
    return {
        "id": event.id,
        "slug": event.slug,
        "title": event.title,
        "description": event.description,
        "kind": event.kind,
        "platform": event.platform,
        "start_datetime": _iso(event.start_datetime),
        "end_datetime": _iso(event.end_datetime),
        "timezone": event.timezone,
        "zoom_join_url": event.zoom_join_url,
        "location": event.location,
        "tags": event.tags or [],
        "required_level": event.required_level,
        "max_participants": event.max_participants,
        "status": event.status,
        "external_host": event.external_host,
        "published": event.published,
        "origin": event.origin,
        "source_repo": event.source_repo or "",
        "source_path": event.source_path or "",
        "editable": not is_synced(event),
        "created_at": _iso(event.created_at),
        "updated_at": _iso(event.updated_at),
    }


def _delete_not_available_response():
    return error_response(
        DELETE_NOT_AVAILABLE_MESSAGE,
        "event_delete_not_available",
        status=405,
    )


def _read_only_field_response(field):
    return error_response(
        f"{field} is read-only",
        "read_only_field",
        status=422,
        details={"field": field},
    )


def _body_must_be_object_response():
    return error_response(
        "Body must be a JSON object",
        "invalid_type",
        details={"field": "body", "expected": "object"},
    )


def _validation_response(details, message="Validation error"):
    return error_response(
        message,
        "validation_error",
        status=422,
        details=details,
    )


def _parse_iso_datetime(value):
    if value in (None, ""):
        return None, None
    if not isinstance(value, str):
        return None, "Must be an ISO 8601 datetime."
    parsed = parse_datetime(value)
    if parsed is None:
        return None, "Must be an ISO 8601 datetime."
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed, None


def _coerce_optional_text(value):
    if value is None:
        return ""
    return str(value).strip()


def _collect_event_values(data, *, existing=None):
    """Validate API payload and return model field values or error details."""
    errors = {}
    values = {}

    if "title" in data:
        title = _coerce_optional_text(data["title"])
        if not title:
            errors["title"] = "Title is required."
        values["title"] = title
    elif existing is None:
        errors["title"] = "Title is required."

    if "slug" in data:
        raw_slug = _coerce_optional_text(data["slug"])
        values["slug"] = raw_slug
    elif existing is None:
        values["slug"] = ""

    for field in ("description", "timezone", "zoom_join_url", "location"):
        if field in data:
            values[field] = _coerce_optional_text(data[field])

    for field, valid_values in (
        ("kind", VALID_KINDS),
        ("platform", VALID_PLATFORMS),
        ("status", VALID_STATUSES),
        ("external_host", VALID_EXTERNAL_HOSTS),
    ):
        if field in data:
            value = _coerce_optional_text(data[field])
            if value not in valid_values:
                errors[field] = "Unknown choice."
            values[field] = value

    if "required_level" in data:
        try:
            required_level = int(data["required_level"])
        except (TypeError, ValueError):
            required_level = None
        if required_level not in VALID_REQUIRED_LEVELS:
            errors["required_level"] = "Unknown tier level."
        values["required_level"] = required_level

    if "max_participants" in data:
        raw = data["max_participants"]
        if raw in (None, ""):
            values["max_participants"] = None
        else:
            try:
                max_participants = int(raw)
            except (TypeError, ValueError):
                max_participants = None
            if max_participants is None or max_participants <= 0:
                errors["max_participants"] = "Must be a positive integer or null."
            values["max_participants"] = max_participants

    if "tags" in data:
        tags = data["tags"]
        if not isinstance(tags, list) or not all(
            isinstance(tag, str) for tag in tags
        ):
            errors["tags"] = "Must be an array of strings."
        else:
            values["tags"] = tags

    if "published" in data:
        if not isinstance(data["published"], bool):
            errors["published"] = "Must be a boolean."
        values["published"] = data["published"]

    start_supplied = "start_datetime" in data
    end_supplied = "end_datetime" in data

    if start_supplied:
        start_datetime, error = _parse_iso_datetime(data["start_datetime"])
        if error:
            errors["start_datetime"] = error
        values["start_datetime"] = start_datetime
    elif existing is None:
        errors["start_datetime"] = "Start datetime is required."

    if end_supplied:
        end_datetime, error = _parse_iso_datetime(data["end_datetime"])
        if error:
            errors["end_datetime"] = error
        values["end_datetime"] = end_datetime

    candidate_start = values.get(
        "start_datetime",
        existing.start_datetime if existing is not None else None,
    )
    if existing is None and not end_supplied and candidate_start is not None:
        values["end_datetime"] = candidate_start + timedelta(hours=1)
    candidate_end = values.get(
        "end_datetime",
        existing.end_datetime if existing is not None else None,
    )
    if candidate_start is not None and candidate_end is not None:
        if candidate_end <= candidate_start:
            errors["end_datetime"] = "Must be after start_datetime."

    title_for_slug = values.get(
        "title",
        existing.title if existing is not None else "",
    )
    if "slug" in values and not values["slug"]:
        values["slug"] = slugify(title_for_slug)
    if existing is None and not values.get("slug") and title_for_slug:
        values["slug"] = slugify(title_for_slug)

    if "slug" in values and values["slug"]:
        duplicate_qs = Event.objects.filter(slug=values["slug"])
        if existing is not None:
            duplicate_qs = duplicate_qs.exclude(pk=existing.pk)
        if duplicate_qs.exists():
            errors["slug"] = "Slug already in use."

    return values, errors


def _apply_event_values(event, values):
    for field, value in values.items():
        if field in WRITABLE_FIELDS:
            setattr(event, field, value)
    if event.platform == "custom":
        event.zoom_meeting_id = ""


def _save_event_or_error(event):
    try:
        event.save()
    except ValidationError as exc:
        message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        return _validation_response({"event": message})
    except IntegrityError:
        return _validation_response({"slug": "Slug already in use."})
    return None


@token_required
@csrf_exempt
@require_methods("GET", "POST", "DELETE")
def events_collection(request):
    """GET/POST/DELETE ``/api/events``."""
    if request.method == "DELETE":
        return _delete_not_available_response()

    if request.method == "GET":
        qs = Event.objects.all()
        status_filter = request.GET.get("status")
        if status_filter:
            if status_filter not in VALID_STATUSES:
                return _validation_response({"status": "Unknown status."})
            qs = qs.filter(status=status_filter)

        origin_filter = request.GET.get("origin")
        if origin_filter:
            if origin_filter not in VALID_ORIGINS:
                return _validation_response({"origin": "Unknown origin."})
            qs = qs.filter(origin=origin_filter)

        query = request.GET.get("q")
        if query:
            qs = qs.filter(title__icontains=query)

        return JsonResponse(
            {"events": [serialize_event(event) for event in qs]},
            status=200,
        )

    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return _body_must_be_object_response()

    for field in sorted(READ_ONLY_FIELDS):
        if field in data:
            return _read_only_field_response(field)

    values, errors = _collect_event_values(data, existing=None)
    if errors:
        return _validation_response(errors)

    event = Event(
        kind="standard",
        platform="zoom",
        timezone="Europe/Berlin",
        status="draft",
        required_level=0,
        published=True,
        tags=[],
        location="",
        external_host="",
        max_participants=None,
        origin="studio",
        source_repo="",
    )
    _apply_event_values(event, values)
    save_error = _save_event_or_error(event)
    if save_error is not None:
        return save_error
    return JsonResponse(serialize_event(event), status=201)


@token_required
@csrf_exempt
@require_methods("GET", "PATCH", "DELETE")
def event_detail(request, slug):
    """GET/PATCH/DELETE ``/api/events/<slug>``."""
    if request.method == "DELETE":
        return _delete_not_available_response()

    event = Event.objects.filter(slug=slug).first()
    if event is None:
        return error_response(
            "Event not found",
            "unknown_event",
            status=404,
        )

    if request.method == "GET":
        return JsonResponse(serialize_event(event), status=200)

    if event.origin == "github" or is_synced(event):
        return error_response(
            "Synced GitHub events are read-only through this API",
            "synced_event_read_only",
            status=409,
        )

    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return _body_must_be_object_response()

    for field in sorted(READ_ONLY_FIELDS):
        if field in data:
            return _read_only_field_response(field)

    values, errors = _collect_event_values(data, existing=event)
    if errors:
        return _validation_response(errors)

    _apply_event_values(event, values)
    save_error = _save_event_or_error(event)
    if save_error is not None:
        return save_error
    return JsonResponse(serialize_event(event), status=200)
