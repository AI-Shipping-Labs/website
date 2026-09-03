"""Member event discovery, detail, and self-registration endpoints."""

import math

from django.db.models import Count, Prefetch, Q
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import member_api_key_required
from accounts.utils.activation import mark_activated
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import body_must_be_object_response, parse_json_body, require_methods
from content.access import get_user_level
from events.models import Event, EventHost, EventRegistration
from events.services.time_windows import past_public_events_queryset, upcoming_events_queryset
from events.views.api import (
    SCOPE_EVENT,
    SCOPE_SERIES,
    _create_registration_with_scope,
    _send_registration_emails,
    _series_response_fields,
)
from member_api.serializers.events import (
    annotate_member_registration_state,
    serialize_event_detail,
    serialize_event_summary,
)

PAGE_SIZE = 20
VALID_FILTERS = {"upcoming", "past"}
FORBIDDEN_TARGET_FIELDS = {"email", "user", "user_id", "attendee", "guest"}
ERROR_SCHEMA = {"$ref": "#/components/schemas/ErrorResponse"}

SERIES_SCHEMA = {
    "type": ["object", "null"],
    "properties": {
        "id": {"type": "integer"},
        "slug": {"type": "string"},
        "name": {"type": "string"},
        "url": {"type": "string"},
    },
    "required": ["id", "slug", "name", "url"],
}
EVENT_SUMMARY_PROPERTIES = {
    "id": {"type": "integer"},
    "slug": {"type": "string"},
    "title": {"type": "string"},
    "url": {"type": "string"},
    "kind": {
        "type": "string",
        "enum": ["standard", "workshop", "meetup", "q_and_a"],
    },
    "start_datetime": {"type": "string", "format": "date-time"},
    "end_datetime": {"type": ["string", "null"], "format": "date-time"},
    "effective_end_datetime": {"type": "string", "format": "date-time"},
    "timezone": {"type": "string"},
    "location": {"type": "string"},
    "tags": {"type": "array", "items": {"type": "string"}},
    "status": {
        "type": "string",
        "enum": ["draft", "upcoming", "completed", "cancelled"],
    },
    "time_status": {
        "type": "string",
        "enum": ["upcoming", "past", "draft"],
    },
    "required_level": {"type": "integer"},
    "required_tier_label": {"type": "string"},
    "series": SERIES_SCHEMA,
    "attendee_count": {"type": "integer", "minimum": 0},
    "external_registration_url": {"type": ["string", "null"]},
    "is_registered": {"type": "boolean"},
    "registration_source": {
        "type": "string",
        "enum": ["event", "series", "none"],
    },
    "registration_available": {"type": "boolean"},
    "registration_targets": {
        "type": "array",
        "items": {"type": "string", "enum": ["series", "event"]},
    },
    "member_endpoint_url": {"type": "string"},
}
EVENT_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": EVENT_SUMMARY_PROPERTIES,
    "required": list(EVENT_SUMMARY_PROPERTIES),
}
INSTRUCTOR_SCHEMA = {
    "type": "object",
    "properties": {
        "instructor_id": {"type": "string"},
        "name": {"type": "string"},
        "bio": {"type": "string"},
        "bio_html": {"type": "string"},
        "photo_url": {"type": "string"},
        "links": {"type": "array", "items": {"type": "object"}},
    },
    "required": [
        "instructor_id",
        "name",
        "bio",
        "bio_html",
        "photo_url",
        "links",
    ],
}
HOST_SCHEMA = {
    "type": "object",
    "properties": {
        "slug": {"type": "string"},
        "name": {"type": "string"},
        "title": {"type": "string"},
        "bio": {"type": "string"},
        "bio_html": {"type": "string"},
        "photo_url": {"type": "string"},
    },
    "required": ["slug", "name", "title", "bio", "bio_html", "photo_url"],
}
EVENT_DETAIL_PROPERTIES = {
    **EVENT_SUMMARY_PROPERTIES,
    "description": {"type": "string"},
    "description_html": {"type": "string"},
    "instructors": {"type": "array", "items": INSTRUCTOR_SCHEMA},
    "hosts": {"type": "array", "items": HOST_SCHEMA},
    "banner_url": {"type": ["string", "null"]},
    "join_url": {"type": ["string", "null"]},
}
EVENT_DETAIL_SCHEMA = {
    "type": "object",
    "properties": EVENT_DETAIL_PROPERTIES,
    "required": list(EVENT_DETAIL_PROPERTIES),
}
SERIES_REGISTRATION_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "total_occurrences": {"type": "integer"},
        "registered": {"type": "integer"},
        "skipped_already": {"type": "integer"},
        "skipped_no_access": {"type": "integer"},
        "skipped_opted_out": {"type": "integer"},
    },
}
EVENT_REGISTER_PROPERTIES = {
    **EVENT_DETAIL_PROPERTIES,
    "registration_status": {"type": "string", "enum": ["registered"]},
    "registered_at": {"type": "string", "format": "date-time"},
    "series_slug": {"type": "string"},
    "summary": SERIES_REGISTRATION_SUMMARY_SCHEMA,
}
EVENT_REGISTER_SCHEMA = {
    "type": "object",
    "properties": EVENT_REGISTER_PROPERTIES,
    "required": [
        *EVENT_DETAIL_PROPERTIES,
        "registration_status",
        "registered_at",
    ],
}
EVENT_LIST_SCHEMA = {
    "type": "object",
    "properties": {
        "events": {"type": "array", "items": EVENT_SUMMARY_SCHEMA},
        "pagination": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "minimum": 1},
                "page_size": {"type": "integer", "const": PAGE_SIZE},
                "total": {"type": "integer", "minimum": 0},
                "total_pages": {"type": "integer", "minimum": 0},
            },
            "required": ["page", "page_size", "total", "total_pages"],
        },
    },
    "required": ["events", "pagination"],
}


def _base_queryset():
    return (
        Event.objects.select_related("event_series")
        .prefetch_related(
            "instructors",
            Prefetch(
                "event_host_links",
                queryset=EventHost.objects.select_related("host").order_by("position"),
            ),
        )
        .annotate(_attendee_count=Count("registrations", distinct=True))
    )


def _member_can_access(user, event):
    return event.is_external or get_user_level(user) >= event.required_level


def _visible_events(user, event_filter):
    queryset = _base_queryset()
    if event_filter == "upcoming":
        queryset = upcoming_events_queryset(queryset).order_by("start_datetime", "id")
    else:
        queryset = past_public_events_queryset(queryset).order_by("-start_datetime", "-id")
    level = get_user_level(user)
    return queryset.filter(Q(external_host__gt="") | Q(required_level__lte=level))


def _serialized_event_queryset(user):
    return annotate_member_registration_state(_base_queryset(), user)


def _event_or_error(event_id, user):
    event = _serialized_event_queryset(user).filter(pk=event_id).first()
    if event is None or event.status == "draft":
        return None, error_response("Event not found", "event_not_found", status=404)
    if event.status == "cancelled" and not event.published:
        return None, error_response("Event not found", "event_not_found", status=404)
    return event, None


EVENTS_OPENAPI = {
    "GET": {
        "summary": "List accessible events",
        "description": (
            "Lists event sessions the key owner can access. Use filter=upcoming "
            "(default) or filter=past. External events remain discoverable."
        ),
        "query": {
            "filter": {
                "type": "string",
                "enum": ["upcoming", "past"],
                "default": "upcoming",
            },
            "page": {"type": "integer", "minimum": 1, "default": 1},
        },
        "responses": {
            200: {
                "description": "A page of accessible event sessions.",
                "schema": EVENT_LIST_SCHEMA,
            },
            401: {
                "description": "Missing or invalid member API key.",
                "schema": ERROR_SCHEMA,
            },
            422: {
                "description": "Invalid filter or page.",
                "schema": ERROR_SCHEMA,
            },
        },
    },
}


@csrf_exempt
@member_api_key_required("events:read")
@require_methods("GET")
@openapi_spec(tag="Events", methods=EVENTS_OPENAPI)
def events_collection(request):
    event_filter = request.GET.get("filter", "upcoming")
    if event_filter not in VALID_FILTERS:
        return error_response(
            "Invalid event filter",
            "validation_error",
            status=422,
            details={"filter": "Use 'upcoming' or 'past'."},
        )
    try:
        page = int(request.GET.get("page", "1"))
    except (TypeError, ValueError):
        page = 0
    if page < 1:
        return error_response(
            "Invalid page",
            "validation_error",
            status=422,
            details={"page": "Use a positive integer."},
        )

    queryset = _visible_events(request.user, event_filter)
    total = queryset.count()
    start = (page - 1) * PAGE_SIZE
    events = list(
        annotate_member_registration_state(queryset, request.user)[start:start + PAGE_SIZE]
    )
    return JsonResponse({
        "events": [serialize_event_summary(event, request.user) for event in events],
        "pagination": {
            "page": page,
            "page_size": PAGE_SIZE,
            "total": total,
            "total_pages": math.ceil(total / PAGE_SIZE),
        },
    })


EVENT_DETAIL_OPENAPI = {
    "GET": {
        "summary": "Get accessible event detail",
        "description": (
            "Returns website-visible event detail and the key owner's registration "
            "state. It never returns a roster, email address, meeting id, or raw "
            "community-event provider URL."
        ),
        "responses": {
            200: {
                "description": "Member-safe event detail.",
                "schema": EVENT_DETAIL_SCHEMA,
            },
            401: {
                "description": "Missing or invalid member API key.",
                "schema": ERROR_SCHEMA,
            },
            403: {
                "description": "The key owner's tier cannot access this event.",
                "schema": ERROR_SCHEMA,
            },
            404: {
                "description": "Unknown, draft, or retired event.",
                "schema": ERROR_SCHEMA,
            },
        },
    },
}


@csrf_exempt
@member_api_key_required("events:read")
@require_methods("GET")
@openapi_spec(tag="Events", methods=EVENT_DETAIL_OPENAPI)
def event_detail(request, event_id):
    event, event_error = _event_or_error(event_id, request.user)
    if event_error is not None:
        return event_error
    if not _member_can_access(request.user, event):
        return error_response(
            "Your tier does not grant access to this event",
            "event_access_denied",
            status=403,
        )
    return JsonResponse(serialize_event_detail(event, request.user))


EVENT_REGISTER_OPENAPI = {
    "POST": {
        "summary": "Register yourself for an event",
        "description": (
            "Registers only the authenticated key owner. The optional request "
            "field scope chooses the registration target: series (the default for "
            "a series session) or event (only this session). It is not a key permission."
        ),
        "request_body": {
            "body_required": False,
            "properties": {"scope": {"type": "string", "enum": ["series", "event"]}},
            "example": {"scope": "event"},
        },
        "responses": {
            201: {
                "description": "Registration created.",
                "schema": EVENT_REGISTER_SCHEMA,
            },
            401: {
                "description": "Missing or invalid member API key.",
                "schema": ERROR_SCHEMA,
            },
            403: {
                "description": "The key owner's tier cannot access this event.",
                "schema": ERROR_SCHEMA,
            },
            404: {
                "description": "Unknown, draft, or retired event.",
                "schema": ERROR_SCHEMA,
            },
            409: {
                "description": "Closed, duplicate, or externally hosted event.",
                "schema": ERROR_SCHEMA,
            },
            422: {
                "description": "Invalid body or unsupported target field.",
                "schema": ERROR_SCHEMA,
            },
        },
    },
}


@csrf_exempt
@member_api_key_required("events:register")
@require_methods("POST")
@openapi_spec(tag="Events", methods=EVENT_REGISTER_OPENAPI)
def event_register(request, event_id):
    event, event_error = _event_or_error(event_id, request.user)
    if event_error is not None:
        return event_error
    if not _member_can_access(request.user, event):
        return error_response(
            "Your tier does not grant access to this event",
            "event_access_denied",
            status=403,
        )

    if request.body:
        payload, parse_error = parse_json_body(request)
        if parse_error is not None:
            return error_response("Invalid JSON", "validation_error", status=422)
    else:
        payload = {}
    if not isinstance(payload, dict):
        return body_must_be_object_response(status=422)
    unsupported = sorted(set(payload) - {"scope"})
    if unsupported:
        forbidden = sorted(set(unsupported) & FORBIDDEN_TARGET_FIELDS)
        return error_response(
            "Registration accepts only the optional scope field",
            "validation_error",
            status=422,
            details={"unsupported_fields": unsupported, "target_fields": forbidden},
        )
    scope = payload.get("scope")
    if scope in (None, ""):
        scope = SCOPE_SERIES if event.event_series_id else SCOPE_EVENT
    if scope not in (SCOPE_SERIES, SCOPE_EVENT):
        return error_response(
            "Invalid registration target",
            "validation_error",
            status=422,
            details={"scope": "Use 'series' or 'event'."},
        )

    if event.is_external:
        return error_response(
            "Register on the host platform",
            "external_registration_required",
            status=409,
            details={"registration_url": event.zoom_join_url or None},
        )
    if not event.is_upcoming:
        return error_response(
            "Event is not open for registration",
            "event_registration_closed",
            status=409,
        )
    if EventRegistration.objects.filter(event=event, user=request.user).exists():
        return error_response("Already registered", "already_registered", status=409)

    registration, series_summary = _create_registration_with_scope(
        request.user,
        event,
        scope,
    )
    mark_activated(request.user)
    _send_registration_emails(request.user, event, registration, series_summary)
    event = _serialized_event_queryset(request.user).get(pk=event.pk)
    response = serialize_event_detail(event, request.user)
    response.update({
        "registration_status": "registered",
        "registered_at": registration.registered_at.isoformat(),
        **_series_response_fields(event, series_summary),
    })
    return JsonResponse(response, status=201)
