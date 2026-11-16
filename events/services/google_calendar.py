"""Fail-soft Google Calendar sync for community Event schedule updates."""

import base64
import logging

from booking.services.calendar import update_calendar_event

logger = logging.getLogger(__name__)


def google_calendar_event_id_from_uid(calendar_uid: str) -> str:
    """Return Google Calendar's deterministic event ID for an iCalendar UID."""
    uid = (calendar_uid or "").strip()
    if not uid:
        return ""
    encoded = base64.b32hexencode(uid.encode("utf-8")).decode("ascii")
    return "_" + encoded.rstrip("=").lower()


def google_calendar_event_id(event) -> str:
    """Return the Google Calendar event ID corresponding to ``event``."""
    if event is None:
        return ""
    return google_calendar_event_id_from_uid(getattr(event, "calendar_uid", ""))


def sync_event_google_calendar(event, *, send_updates: str = "none") -> dict:
    """Patch ``event`` start/end on Google Calendar when present.

    Degrades gracefully (returns ``{}``) when Dapier is unconfigured, the
    invite is absent on Google Calendar (HTTP 404), or Google Calendar is
    unreachable, so local event saves are never blocked.
    """
    if event is None or event.start_datetime is None:
        return {}
    if getattr(event, "status", "") == "cancelled":
        return {}

    gcal_id = google_calendar_event_id(event)
    if not gcal_id:
        return {}

    end_dt = event.effective_end_datetime
    if end_dt is None:
        return {}

    return update_calendar_event(
        gcal_id,
        start=event.start_datetime,
        end=end_dt,
        time_zone=getattr(event, "timezone", "") or None,
        send_updates=send_updates,
    )
