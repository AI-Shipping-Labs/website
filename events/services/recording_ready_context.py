"""Native Relay context for the event recording-ready notification."""

from django.core.exceptions import ObjectDoesNotExist

from events.services.display_time import format_event_time_range
from integrations.config import site_base_url


def build_recording_ready_context(event):
    studio_event_url = f"{site_base_url()}{event.get_studio_edit_url()}"
    event_datetime = format_event_time_range(
        event.start_datetime,
        event.end_datetime,
        event.timezone or "Europe/Berlin",
    )
    watch_url = _resolve_watch_url(event)
    is_available_to_watch = bool(event.published and watch_url)
    publish_state, publish_copy = _publish_copy(event, is_available_to_watch)
    campaign_url = (
        f"{site_base_url()}/studio/campaigns/new"
        f"?event={event.pk}&template=recording_available"
    )
    return {
        "event_title": event.title,
        "event_datetime": event_datetime,
        "publish_state": publish_state,
        "publish_copy": publish_copy,
        "is_available_to_watch": is_available_to_watch,
        "watch_url": watch_url,
        "studio_event_url": studio_event_url,
        "zoom_recording_url": (event.recording_url or "").strip(),
        "campaign_prefill_url": campaign_url,
    }


def _resolve_watch_url(event):
    try:
        workshop = event.workshop
    except ObjectDoesNotExist:
        return ""
    if workshop is None or getattr(workshop, "status", "") != "published":
        return ""
    return f"{site_base_url()}{workshop.get_absolute_url()}/video"


def _publish_copy(event, is_available_to_watch):
    if is_available_to_watch:
        return (
            "Published and available to watch",
            "The recording is now available to watch. Members with access can "
            "watch it right away on the workshop video page.",
        )
    if event.published:
        return (
            "Uploaded and currently published",
            "The event is currently published. Review the uploaded recording "
            "in Studio and confirm the member-facing page is still correct.",
        )
    return (
        "Ready for review/publishing",
        "The event is not public yet. Review the upload, then publish and send "
        "the attendee follow-up when ready.",
    )
