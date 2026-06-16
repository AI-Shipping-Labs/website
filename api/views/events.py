"""Staff token API for Studio-origin events (issue #627).

Source-of-truth contract:
- GitHub-origin/synced events are inspectable through this API but read-only.
- Studio/API-origin events can be created and patched.
- Event deletion is intentionally unavailable through the API; operators must
  use Studio for manual deletion so ownership and sync rules stay explicit.
"""

import logging
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import parse_json_body, require_methods
from content.access import VISIBILITY_CHOICES
from events.models import Event, EventHost, Host
from events.models.event import (
    EVENT_KIND_CHOICES,
    EVENT_ORIGIN_CHOICES,
    EVENT_PLATFORM_CHOICES,
    EVENT_STATUS_CHOICES,
    EXTERNAL_HOST_CHOICES,
)
from events.services.host_invite import maybe_send_initial_host_invite
from integrations.services.banner_generator import (
    is_enabled as banner_generator_is_enabled,
)
from integrations.services.banner_generator.dispatch import enqueue_force
from integrations.services.banner_generator.resolve import effective_banner_url
from integrations.services.zoom import ZoomAPIError, create_meeting
from studio.utils import is_synced

logger = logging.getLogger(__name__)

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
    "status",
    "external_host",
    "published",
    "host_email",
}

VALID_KINDS = {value for value, _label in EVENT_KIND_CHOICES}
VALID_PLATFORMS = {value for value, _label in EVENT_PLATFORM_CHOICES}
VALID_STATUSES = {value for value, _label in EVENT_STATUS_CHOICES}
VALID_ORIGINS = {value for value, _label in EVENT_ORIGIN_CHOICES}
VALID_EXTERNAL_HOSTS = {value for value, _label in EXTERNAL_HOST_CHOICES}
VALID_REQUIRED_LEVELS = {value for value, _label in VISIBILITY_CHOICES}

_VALID_STATUSES_ENUM = sorted(VALID_STATUSES)
_VALID_ORIGINS_ENUM = sorted(VALID_ORIGINS)

_EVENT_EXAMPLE = {
    "id": 42,
    "slug": "office-hours-2026-05-05",
    "title": "Office Hours: May 5",
    "description": "Open Q&A.",
    "kind": "standard",
    "platform": "zoom",
    "start_datetime": "2026-05-05T17:00:00+02:00",
    "end_datetime": "2026-05-05T18:00:00+02:00",
    "timezone": "Europe/Berlin",
    "zoom_join_url": "https://zoom.us/j/123",
    "location": "",
    "tags": ["sprint:may-2026"],
    "required_level": 0,
    "status": "scheduled",
    "external_host": "",
    "published": True,
    "host_email": "host@example.com",
    "hosts": [
        {
            "id": 1,
            "name": "Alexey Grigorev",
            "slug": "alexey-grigorev",
            "photo_url": "/static/alexey.png",
            "email": "alexey@aishippinglabs.com",
        }
    ],
    "banner_url": "https://cdn.aishippinglabs.com/banners/event/office-hours.jpg",
    "origin": "studio",
    "source_repo": "",
    "source_path": "",
    "editable": True,
    "created_at": "2026-04-15T12:00:00+00:00",
    "updated_at": "2026-04-15T12:00:00+00:00",
}


def _iso(value):
    return value.isoformat() if value is not None else None


def _host_prefetch():
    return Prefetch(
        "event_host_links",
        queryset=EventHost.objects.select_related("host").order_by("position"),
    )


def _serialize_host(host):
    return {
        "id": host.id,
        "name": host.name,
        "slug": host.slug,
        "photo_url": host.display_photo_url,
        "email": host.email,
    }


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
        "status": event.status,
        "series_position": event.series_position,
        "external_host": event.external_host,
        "published": event.published,
        "host_email": event.host_email,
        "hosts": [_serialize_host(host) for host in event.ordered_hosts],
        "banner_url": effective_banner_url(event),
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

    if "host_email" in data:
        host_email = _coerce_optional_text(data["host_email"])
        if host_email:
            try:
                validate_email(host_email)
            except ValidationError:
                errors["host_email"] = "Must be a valid email address."
        values["host_email"] = host_email

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


def _validate_host_ids(data):
    """Return ``(host_ids_or_None, errors)`` for an optional host_ids payload."""
    if "host_ids" not in data:
        return None, {}

    raw_host_ids = data["host_ids"]
    if not isinstance(raw_host_ids, list):
        return None, {"host_ids": "Must be an array of host ids."}

    host_ids = []
    for value in raw_host_ids:
        if isinstance(value, bool) or not isinstance(value, int):
            return None, {"host_ids": "Must be an array of integer host ids."}
        host_ids.append(value)

    if len(set(host_ids)) != len(host_ids):
        return None, {"host_ids": "Duplicate host ids are not allowed."}

    existing_ids = set(
        Host.objects.filter(id__in=host_ids).values_list("id", flat=True)
    )
    unknown_ids = [host_id for host_id in host_ids if host_id not in existing_ids]
    if unknown_ids:
        return None, {
            "host_ids": (
                "Unknown host id"
                if len(unknown_ids) == 1
                else "Unknown host ids"
            )
            + f": {', '.join(str(host_id) for host_id in unknown_ids)}."
        }
    return host_ids, {}


def _set_event_hosts(event, host_ids):
    """Replace event hosts with the supplied ids, preserving list order."""
    if host_ids is None:
        return
    EventHost.objects.filter(event=event).delete()
    EventHost.objects.bulk_create([
        EventHost(event=event, host_id=host_id, position=position)
        for position, host_id in enumerate(host_ids)
    ])
    event._prefetched_objects_cache = {}


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


def _validate_create_zoom(data, *, platform):
    """Validate the write-only ``create_zoom`` action trigger.

    Returns ``(create_zoom_bool, error_response_or_None)``. The boolean is the
    parsed flag (defaulting to ``False`` when absent); the second item is a
    pre-save validation error response that must abort the request before any
    row is created/updated, or ``None`` when the request may proceed.
    """
    if "create_zoom" not in data:
        return False, None
    value = data["create_zoom"]
    if not isinstance(value, bool):
        return False, _validation_response({"create_zoom": "Must be a boolean."})
    if value and platform != "zoom":
        return value, _validation_response(
            {"create_zoom": "create_zoom is only valid when platform is 'zoom'."}
        )
    return value, None


def _maybe_create_zoom_meeting(event, create_zoom):
    """Provision a Zoom meeting after the event row is saved (fail-soft).

    Idempotent: a no-op when the event already carries a ``zoom_meeting_id``.
    On any failure the event is kept and a non-fatal message string is
    returned so the caller can surface it as ``zoom_error``.
    """
    if not create_zoom:
        return None
    if event.zoom_meeting_id:
        return None
    try:
        result = create_meeting(event)
    except ZoomAPIError as exc:
        logger.exception("create_zoom: Zoom meeting creation failed for %s", event.slug)
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - fail soft, never roll back the event
        logger.exception(
            "create_zoom: unexpected error creating Zoom meeting for %s", event.slug
        )
        return str(exc) or "Failed to create Zoom meeting."
    event.zoom_meeting_id = result["meeting_id"]
    event.zoom_join_url = result["join_url"]
    event.save(update_fields=["zoom_meeting_id", "zoom_join_url"])
    return None


BANNER_DISABLED_MESSAGE = (
    "Banner generator is not configured; no banner was generated."
)


def _validate_generate_banner(data, *, default):
    """Validate the write-only ``generate_banner`` action trigger (issue #995).

    ``generate_banner`` is an action trigger, never a stored model field. It is
    not in ``WRITABLE_FIELDS`` and never appears in ``serialize_event`` output.

    Returns ``(generate_banner_bool, error_response_or_None)``. The boolean is
    the parsed flag — ``default`` when the key is absent (``True`` on create so
    the API auto-generates the social image; ``False`` on update so an unrelated
    edit never silently re-renders). The second item is a pre-save validation
    error response that must abort the request before any row is mutated, or
    ``None`` when the request may proceed.
    """
    if "generate_banner" not in data:
        return default, None
    value = data["generate_banner"]
    if not isinstance(value, bool):
        return default, _validation_response(
            {"generate_banner": "Must be a boolean."}
        )
    return value, None


def _maybe_enqueue_banner(event, generate_banner):
    """Force-enqueue a banner render after the event row is saved (fail-soft).

    When ``generate_banner`` is true and the generator is configured, calls
    ``enqueue_force('event', event.pk)`` exactly once and returns
    ``(task_id, None)``. When the generator is not configured, returns
    ``(None, BANNER_DISABLED_MESSAGE)`` so the caller can surface a non-fatal
    ``banner_error`` key without rolling back the event. A no-op (``(None,
    None)``) when ``generate_banner`` is false.
    """
    if not generate_banner:
        return None, None
    if not banner_generator_is_enabled():
        return None, BANNER_DISABLED_MESSAGE
    task_id = enqueue_force("event", event.pk)
    return task_id, None


@token_required
@csrf_exempt
@require_methods("GET", "POST", "DELETE")
@openapi_spec(
    tag="Events",
    summary="List, create, or attempt to delete events",
    methods={
        "GET": {
            "summary": "List events",
            "query": {
                "status": {
                    "type": "string",
                    "enum": _VALID_STATUSES_ENUM,
                    "required": False,
                },
                "origin": {
                    "type": "string",
                    "enum": _VALID_ORIGINS_ENUM,
                    "required": False,
                },
                "q": {
                    "type": "string",
                    "required": False,
                    "description": "icontains match on title.",
                },
            },
            "responses": {
                200: {
                    "description": "List of events.",
                    "example": {"events": [_EVENT_EXAMPLE]},
                },
                422: {"description": "Unknown filter value."},
            },
        },
        "POST": {
            "summary": "Create a Studio-origin event",
            "description": (
                "Studio/API-origin events can be created through this "
                "endpoint. GitHub-origin events are read-only here."
            ),
            "request_body": {
                "required": ["title", "start_datetime"],
                "properties": {
                    "title": {"type": "string"},
                    "slug": {"type": "string"},
                    "description": {"type": "string"},
                    "kind": {"type": "string"},
                    "platform": {"type": "string"},
                    "start_datetime": {
                        "type": "string",
                        "format": "date-time",
                    },
                    "end_datetime": {
                        "type": "string",
                        "format": "date-time",
                    },
                    "timezone": {"type": "string"},
                    "zoom_join_url": {"type": "string"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "required_level": {"type": "integer"},
                    "status": {"type": "string"},
                    "external_host": {"type": "string"},
                    "published": {"type": "boolean"},
                    "host_email": {
                        "type": "string",
                        "format": "email",
                        "description": (
                            "Optional. Email that receives the host "
                            "calendar invite with host-only management "
                            "links. Blank falls back to the operator "
                            "EVENTS_HOST_INVITE_EMAIL default; when both "
                            "are unset no invite is sent."
                        ),
                    },
                    "host_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": (
                            "Optional. Ordered event host ids. Empty array "
                            "clears all hosts."
                        ),
                    },
                    "create_zoom": {
                        "type": "boolean",
                        "writeOnly": True,
                        "description": (
                            "Write-only action trigger. When true and "
                            "platform is 'zoom', provisions a real Zoom "
                            "meeting and populates zoom_join_url / "
                            "zoom_meeting_id. Idempotent; never stored or "
                            "returned. On Zoom failure the event still "
                            "persists and the body carries a non-fatal "
                            "zoom_error key."
                        ),
                    },
                    "generate_banner": {
                        "type": "boolean",
                        "writeOnly": True,
                        "description": (
                            "Write-only action trigger. Defaults to true on "
                            "create: the API auto-renders the 1200x630 "
                            "social/banner image in the background. Set false "
                            "to skip. Never stored or returned. When enqueued "
                            "the response carries banner_task_id (poll "
                            "/api/worker/tasks/<id>); when the generator is "
                            "unconfigured the event still persists and the "
                            "body carries a non-fatal banner_error key."
                        ),
                    },
                },
                "example": {
                    "title": "Office Hours: May 5",
                    "start_datetime": "2026-05-05T17:00:00+02:00",
                    "host_email": "host@example.com",
                    "host_ids": [1],
                },
            },
            "responses": {
                201: {
                    "description": "Event created.",
                    "example": _EVENT_EXAMPLE,
                },
                400: {"description": "Invalid JSON body."},
                422: {
                    "description": (
                        "Validation error or attempt to write a "
                        "read-only origin field."
                    ),
                },
            },
        },
        "DELETE": {
            "summary": "DELETE is not available on this route",
            "description": (
                "Event deletion is intentionally unavailable through "
                "the API; operators must use Studio so ownership and "
                "sync rules stay explicit."
            ),
            "responses": {
                405: {
                    "description": "Event deletion is not available.",
                    "example": {
                        "error": DELETE_NOT_AVAILABLE_MESSAGE,
                        "code": "event_delete_not_available",
                    },
                },
            },
        },
    },
)
def events_collection(request):
    """GET/POST/DELETE ``/api/events``."""
    if request.method == "DELETE":
        return _delete_not_available_response()

    if request.method == "GET":
        qs = Event.objects.prefetch_related(_host_prefetch()).all()
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
    host_ids, host_errors = _validate_host_ids(data)
    errors.update(host_errors)
    if errors:
        return _validation_response(errors)

    effective_platform = values.get("platform", "zoom")
    create_zoom, zoom_error_response = _validate_create_zoom(
        data, platform=effective_platform
    )
    if zoom_error_response is not None:
        return zoom_error_response

    # ``generate_banner`` defaults ON for create — a missing social image is a
    # visible defect, so the API auto-renders unless the caller opts out.
    generate_banner, banner_error_response = _validate_generate_banner(
        data, default=True
    )
    if banner_error_response is not None:
        return banner_error_response

    with transaction.atomic():
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
            origin="studio",
            source_repo="",
        )
        _apply_event_values(event, values)
        save_error = _save_event_or_error(event)
        if save_error is not None:
            return save_error
        _set_event_hosts(event, host_ids)

    zoom_error = _maybe_create_zoom_meeting(event, create_zoom)
    # Best-effort host calendar invite (issue #993). Self-gating: skips
    # drafts, EmailLog-idempotent, resolves recipient via host_email /
    # EVENTS_HOST_INVITE_EMAIL, and swallows exceptions. Called after the
    # save and the create_zoom step so the invite carries zoom_join_url.
    maybe_send_initial_host_invite(event)
    # Banner enqueue runs LAST in the compose-time chain (zoom -> host invite ->
    # banner) so any populated state is current. Soft-fails independently with a
    # ``banner_error`` key; never rolls back the event (issue #995).
    banner_task_id, banner_error = _maybe_enqueue_banner(event, generate_banner)
    body = serialize_event(event)
    if zoom_error is not None:
        body["zoom_error"] = zoom_error
    if banner_error is not None:
        body["banner_error"] = banner_error
    elif generate_banner:
        body["banner_task_id"] = banner_task_id
    return JsonResponse(body, status=201)


@token_required
@csrf_exempt
@require_methods("GET", "PATCH", "DELETE")
@openapi_spec(
    tag="Events",
    summary="Retrieve, update, or attempt to delete an event",
    methods={
        "GET": {
            "summary": "Retrieve an event",
            "responses": {
                200: {
                    "description": "Event detail.",
                    "example": _EVENT_EXAMPLE,
                },
                404: {
                    "description": "Event not found.",
                    "example": {
                        "error": "Event not found",
                        "code": "unknown_event",
                    },
                },
            },
        },
        "PATCH": {
            "summary": "Update an event",
            "description": (
                "Synced GitHub events are read-only through this API "
                "and return 409 ``synced_event_read_only``."
            ),
            "request_body": {
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "status": {"type": "string"},
                    "published": {"type": "boolean"},
                    "host_email": {
                        "type": "string",
                        "format": "email",
                        "description": (
                            "Optional. Email that receives the host "
                            "calendar invite with host-only management "
                            "links. Adding it to a not-yet-invited "
                            "published event sends the one-time invite. "
                            "Empty string clears the field."
                        ),
                    },
                    "host_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": (
                            "Optional. Ordered event host ids. Empty array "
                            "clears all hosts."
                        ),
                    },
                    "create_zoom": {
                        "type": "boolean",
                        "writeOnly": True,
                        "description": (
                            "Write-only action trigger. When true and the "
                            "event's platform is 'zoom' with no existing "
                            "meeting, provisions a Zoom meeting and populates "
                            "zoom_join_url / zoom_meeting_id. Idempotent; "
                            "never stored or returned. On Zoom failure the "
                            "event still persists and the body carries a "
                            "non-fatal zoom_error key."
                        ),
                    },
                    "generate_banner": {
                        "type": "boolean",
                        "writeOnly": True,
                        "description": (
                            "Write-only action trigger. Explicit-only on "
                            "PATCH (default off): only an explicit true "
                            "re-enqueues a forced banner render after the "
                            "update is saved, so an unrelated edit never "
                            "silently re-renders. Never stored or returned."
                        ),
                    },
                },
                "example": {"status": "scheduled", "published": True},
            },
            "responses": {
                200: {
                    "description": "Event updated.",
                    "example": _EVENT_EXAMPLE,
                },
                400: {"description": "Invalid JSON body."},
                404: {"description": "Event not found."},
                409: {
                    "description": "Synced GitHub event cannot be edited.",
                    "example": {
                        "error": "Synced GitHub events are read-only through this API",
                        "code": "synced_event_read_only",
                    },
                },
                422: {
                    "description": (
                        "Validation error or attempt to write a "
                        "read-only origin field."
                    ),
                },
            },
        },
        "DELETE": {
            "summary": "DELETE is not available on this route",
            "responses": {
                405: {
                    "description": "Event deletion is not available.",
                    "example": {
                        "error": DELETE_NOT_AVAILABLE_MESSAGE,
                        "code": "event_delete_not_available",
                    },
                },
            },
        },
    },
)
def event_detail(request, slug):
    """GET/PATCH/DELETE ``/api/events/<slug>``."""
    if request.method == "DELETE":
        return _delete_not_available_response()

    event = (
        Event.objects
        .prefetch_related(_host_prefetch())
        .filter(slug=slug)
        .first()
    )
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
    host_ids, host_errors = _validate_host_ids(data)
    errors.update(host_errors)
    if errors:
        return _validation_response(errors)

    effective_platform = values.get("platform", event.platform)
    create_zoom, zoom_error_response = _validate_create_zoom(
        data, platform=effective_platform
    )
    if zoom_error_response is not None:
        return zoom_error_response

    # On PATCH ``generate_banner`` is explicit-only (default off): an unrelated
    # edit must not silently re-render. Only an explicit ``true`` re-enqueues.
    generate_banner, banner_error_response = _validate_generate_banner(
        data, default=False
    )
    if banner_error_response is not None:
        return banner_error_response

    with transaction.atomic():
        _apply_event_values(event, values)
        save_error = _save_event_or_error(event)
        if save_error is not None:
            return save_error
        _set_event_hosts(event, host_ids)

    zoom_error = _maybe_create_zoom_meeting(event, create_zoom)
    # Best-effort host calendar invite (issue #993). Self-gating and
    # EmailLog-idempotent, so a plain re-save never re-sends. A PATCH that
    # flips a draft to a published status, or adds a host_email to a
    # not-yet-invited event, triggers the one-time invite.
    maybe_send_initial_host_invite(event)
    # Banner enqueue runs after the save + zoom + host-invite steps so a forced
    # re-render reflects any populated state (issue #995). Explicit-only here.
    banner_task_id, banner_error = _maybe_enqueue_banner(event, generate_banner)
    body = serialize_event(event)
    if zoom_error is not None:
        body["zoom_error"] = zoom_error
    if banner_error is not None:
        body["banner_error"] = banner_error
    elif generate_banner:
        body["banner_task_id"] = banner_task_id
    return JsonResponse(body, status=200)


@token_required
@csrf_exempt
@require_methods("POST")
@openapi_spec(
    tag="Events",
    summary="Force-regenerate an event's social/banner image",
    methods={
        "POST": {
            "summary": "Force-regenerate an event banner",
            "description": (
                "Force-enqueues a banner-generator render for an existing "
                "event and returns the django-q task id. The render runs as a "
                "background task; the resolved banner_url stays empty until the "
                "worker finishes, so poll GET /api/worker/tasks/<task_id> (or "
                "re-GET the event for banner_url). Regeneration is ALLOWED for "
                "synced/GitHub-origin events: the PATCH read-only rule protects "
                "content fields owned by the source repo, but the auto-banner is "
                "derived presentation state the platform owns regardless of "
                "origin (the sync pipeline itself enqueues renders for synced "
                "records), so there is no 409 here. Returns 422 "
                "banner_generator_not_configured (no enqueue) when the generator "
                "is not configured."
            ),
            "responses": {
                202: {
                    "description": "Render queued.",
                    "example": {
                        "status": "queued",
                        "event_id": 42,
                        "slug": "office-hours-2026-05-05",
                        "task_id": "a1b2c3d4e5f6",
                    },
                },
                404: {
                    "description": "Event not found.",
                    "example": {
                        "error": "Event not found",
                        "code": "unknown_event",
                    },
                },
                422: {
                    "description": "Banner generator is not configured.",
                    "example": {
                        "error": (
                            "Banner generator is not configured. Add the "
                            "function URL and bearer token under Studio "
                            "Settings first."
                        ),
                        "code": "banner_generator_not_configured",
                    },
                },
            },
        },
    },
)
def event_regenerate_banner(request, slug):
    """POST ``/api/events/<slug>/regenerate-banner`` (issue #995)."""
    event = Event.objects.filter(slug=slug).first()
    if event is None:
        return error_response(
            "Event not found",
            "unknown_event",
            status=404,
        )

    if not banner_generator_is_enabled():
        return error_response(
            "Banner generator is not configured. Add the function URL and "
            "bearer token under Studio Settings first.",
            "banner_generator_not_configured",
            status=422,
        )

    # ``enqueue_force`` may return ``None`` even when enabled (e.g. a race where
    # the row vanished). That is a swallowed enqueue, not a server error: still
    # return 202 with ``task_id: null`` so the operator can re-check via Studio.
    task_id = enqueue_force("event", event.pk)
    return JsonResponse(
        {
            "status": "queued",
            "event_id": event.pk,
            "slug": event.slug,
            "task_id": task_id,
        },
        status=202,
    )
