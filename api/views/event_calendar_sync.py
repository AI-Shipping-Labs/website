"""Staff token API action to force-sync an event's Google Calendar invite."""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import require_methods
from events.models import Event
from events.services.google_calendar import (
    google_calendar_event_id,
    sync_event_google_calendar,
)


@csrf_exempt
@token_required
@require_methods("POST")
@openapi_spec(
    tag="Events",
    summary="Force-sync an event's stored schedule to Google Calendar",
    methods={
        "POST": {
            "summary": "Force-sync an event's Google Calendar invite",
            "description": (
                "PATCHes the Google Calendar invite derived from the event's "
                "immutable calendar_uid with the event's current start/end "
                "datetimes and timezone using sendUpdates=none. Does not save "
                "the event or send attendee reschedule emails."
            ),
            "responses": {
                200: {
                    "description": "Google Calendar sync attempted.",
                    "example": {
                        "calendar_sync_status": "synced",
                        "google_event_id": "_clr6arjk5l...",
                    },
                },
                401: {"description": "Missing, invalid, or non-staff token."},
                404: {"description": "Event not found."},
                422: {"description": "Cancelled or unscheduled event."},
            },
        },
    },
)
def event_sync_calendar(request, slug):
    """POST ``/api/events/<slug>/sync-calendar`` deliberate idempotent retry."""
    event = Event.objects.filter(slug=slug).first()
    if event is None:
        return error_response("Event not found", "unknown_event", status=404)
    if event.status == "cancelled" or event.start_datetime is None:
        return error_response(
            "Event is cancelled or missing start_datetime",
            "event_not_syncable",
            status=422,
        )
    google_event_id = google_calendar_event_id(event)
    result = sync_event_google_calendar(event, send_updates="none")
    status_label = "synced" if result.get("id") else "skipped"
    return JsonResponse(
        {
            "calendar_sync_status": status_label,
            "google_event_id": google_event_id,
            "start": result.get("start"),
            "end": result.get("end"),
        },
        status=200,
    )
