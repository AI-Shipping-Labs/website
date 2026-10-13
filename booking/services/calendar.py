"""Google Calendar client consuming tokens delivered by Dapier."""

import json
import logging
import urllib.parse
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from integrations.config import get_config
from booking.services.dapier import DapierError, get_calendar_access_token

logger = logging.getLogger(__name__)

CALENDAR_API_URL = "https://www.googleapis.com/calendar/v3"


class CalendarError(Exception):
    pass


class CalendarAuthLost(CalendarError):
    pass


class CalendarUnavailable(CalendarError):
    pass


def get_selected_calendar_id() -> str:
    """Configured target Google Calendar ID (default: 'primary')."""
    return get_config("GOOGLE_CALENDAR_ID", "primary")


def _google_request(method: str, path: str, payload: dict | None = None, params: dict | None = None, timeout: int = 15) -> dict:
    """Execute authenticated Google Calendar API HTTP request."""
    try:
        token = get_calendar_access_token()
    except DapierError as exc:
        raise CalendarUnavailable(f"Cannot obtain calendar token from Dapier: {exc}") from exc

    url = f"{CALENDAR_API_URL}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    req = Request(url, data=data, method=method, headers=headers)

    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        if exc.code in (401, 403):
            logger.warning("Google Calendar authorization lost: HTTP %s", exc.code)
            raise CalendarAuthLost(f"Google Calendar refused authorization ({exc.code})") from exc
        if exc.code == 404:
            return {}
        logger.warning("Google Calendar error: HTTP %s", exc.code)
        raise CalendarUnavailable(f"Google Calendar returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError) as exc:
        logger.warning("Google Calendar unreachable: %s", exc)
        raise CalendarUnavailable(f"Google Calendar unreachable: {exc}") from exc


def query_busy(start: datetime, end: datetime, calendar_id: str | None = None) -> list[tuple[datetime, datetime]]:
    """Query busy spans for the specified calendar between start and end.

    Returns a list of (start_utc, end_utc) datetime tuples.
    If the calendar integration is not configured or unavailable, returns []
    so booking can proceed or degrade gracefully.
    """
    cal_id = calendar_id or get_selected_calendar_id()
    start_utc = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
    end_utc = end if end.tzinfo else end.replace(tzinfo=timezone.utc)

    payload = {
        "timeMin": start_utc.isoformat(),
        "timeMax": end_utc.isoformat(),
        "items": [{"id": cal_id}],
    }

    try:
        data = _google_request("POST", "/freeBusy", payload=payload)
    except CalendarError as exc:
        logger.warning("Could not query Google Calendar freeBusy, ignoring: %s", exc)
        return []

    calendars = (data.get("calendars") or {}).get(cal_id, {})
    busy_spans = []
    for span in calendars.get("busy", []):
        try:
            b_start = datetime.fromisoformat(span["start"].replace("Z", "+00:00"))
            b_end = datetime.fromisoformat(span["end"].replace("Z", "+00:00"))
            busy_spans.append((b_start, b_end))
        except (KeyError, ValueError):
            continue

    return busy_spans


def create_calendar_event(
    *,
    summary: str,
    description: str,
    start: datetime,
    end: datetime,
    attendee_email: str,
    location: str = "",
    calendar_id: str | None = None,
) -> dict:
    """Create an event on Google Calendar and invite the attendee."""
    cal_id = calendar_id or get_selected_calendar_id()
    start_utc = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
    end_utc = end if end.tzinfo else end.replace(tzinfo=timezone.utc)

    payload = {
        "summary": summary,
        "description": description,
        "location": location,
        "start": {"dateTime": start_utc.isoformat()},
        "end": {"dateTime": end_utc.isoformat()},
        "attendees": [{"email": attendee_email}],
    }

    quoted_cal = urllib.parse.quote(cal_id, safe="")
    try:
        return _google_request("POST", f"/calendars/{quoted_cal}/events", payload=payload, params={"sendUpdates": "all"})
    except CalendarError as exc:
        logger.error("Failed to create Google Calendar event: %s", exc)
        return {}


def update_calendar_event(
    event_id: str,
    *,
    start: datetime,
    end: datetime,
    summary: str | None = None,
    description: str | None = None,
    location: str | None = None,
    calendar_id: str | None = None,
) -> dict:
    """Update event start/end times and optional metadata on Google Calendar."""
    if not event_id:
        return {}
    cal_id = calendar_id or get_selected_calendar_id()
    start_utc = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
    end_utc = end if end.tzinfo else end.replace(tzinfo=timezone.utc)

    payload: dict = {
        "start": {"dateTime": start_utc.isoformat()},
        "end": {"dateTime": end_utc.isoformat()},
    }
    if summary is not None:
        payload["summary"] = summary
    if description is not None:
        payload["description"] = description
    if location is not None:
        payload["location"] = location

    quoted_cal = urllib.parse.quote(cal_id, safe="")
    quoted_event = urllib.parse.quote(event_id, safe="")
    try:
        return _google_request("PATCH", f"/calendars/{quoted_cal}/events/{quoted_event}", payload=payload, params={"sendUpdates": "all"})
    except CalendarError as exc:
        logger.error("Failed to update Google Calendar event %s: %s", event_id, exc)
        return {}


def delete_calendar_event(event_id: str, calendar_id: str | None = None) -> bool:
    """Delete an event from Google Calendar."""
    if not event_id:
        return False
    cal_id = calendar_id or get_selected_calendar_id()
    quoted_cal = urllib.parse.quote(cal_id, safe="")
    quoted_event = urllib.parse.quote(event_id, safe="")
    try:
        _google_request("DELETE", f"/calendars/{quoted_cal}/events/{quoted_event}", params={"sendUpdates": "all"})
        return True
    except CalendarError as exc:
        logger.error("Failed to delete Google Calendar event %s: %s", event_id, exc)
        return False
