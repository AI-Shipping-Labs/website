"""Google Calendar client consuming tokens delivered by Dapier."""

import json
import logging
import urllib.parse
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from booking.services.dapier import DapierError, get_calendar_access_token
from integrations.config import get_config

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


def _google_request(
    method: str, path: str, payload: dict | None = None, params: dict | None = None, timeout: int = 15
) -> dict:
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


def _ensure_utc(dt: datetime) -> datetime:
    """Return a timezone-aware datetime in UTC."""
    if dt.tzinfo:
        return dt.astimezone(timezone.utc)
    return dt.replace(tzinfo=timezone.utc)


def _build_attendees(attendee_email: str, host_email: str = "") -> list[dict]:
    """Build the Google Calendar attendees list including invitee and host."""
    attendees: list[dict] = []
    primary = (attendee_email or "").strip()
    if primary:
        attendees.append({"email": primary})
    host_clean = (host_email or "").strip()
    if host_clean and host_clean.lower() != primary.lower():
        attendees.append({"email": host_clean})
    return attendees


def create_calendar_event(
    *,
    summary: str,
    description: str,
    start: datetime,
    end: datetime,
    attendee_email: str,
    location: str = "",
    calendar_id: str | None = None,
    host_email: str = "",
) -> dict:
    """Create an event on Google Calendar and invite both the attendee and host."""
    cal_id = calendar_id or get_selected_calendar_id()
    start_utc = _ensure_utc(start)
    end_utc = _ensure_utc(end)

    payload = {
        "summary": summary,
        "description": description,
        "location": location,
        "start": {"dateTime": start_utc.isoformat()},
        "end": {"dateTime": end_utc.isoformat()},
        "attendees": _build_attendees(attendee_email, host_email),
    }

    quoted_cal = urllib.parse.quote(cal_id, safe="")
    try:
        return _google_request(
            "POST", f"/calendars/{quoted_cal}/events", payload=payload, params={"sendUpdates": "all"}
        )
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
        return _google_request(
            "PATCH", f"/calendars/{quoted_cal}/events/{quoted_event}", payload=payload, params={"sendUpdates": "all"}
        )
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


def _build_failure_alert_body(booking, reason: str) -> str:
    """Build the plaintext alert message for a failed calendar event creation."""
    lines = [
        "Google Calendar event creation failed for a confirmed 1:1 booking.",
        "An .ics calendar invitation is attached so you can add it to your calendar.",
        "",
        f"Invitee: {booking.invitee_name} ({booking.invitee_email})",
        f"Start (UTC): {booking.start_datetime.isoformat()}",
        f"End (UTC): {booking.end_datetime.isoformat()}",
        f"Topic: {booking.discussion_topic}",
    ]
    if booking.discussion_notes:
        lines.append(f"Notes: {booking.discussion_notes}")
    if booking.zoom_join_url:
        lines.append(f"Zoom Join URL: {booking.zoom_join_url}")
    if reason:
        lines.append(f"Error: {reason}")
    return "\n".join(lines)


def send_calendar_invite_failure_alert(booking, ics_content: str = "", reason: str = "") -> bool:
    """Alert the booking host by email (with .ics attachment) when Google Calendar write fails."""
    from django.conf import settings
    from django.core.mail import EmailMessage

    host_email = ""
    if booking.host and booking.host.email:
        host_email = booking.host.email.strip()
    recipient = host_email or "alexey@datatalks.club"
    default_from = getattr(settings, "DEFAULT_FROM_EMAIL", "community@aishippinglabs.com")
    from_email = get_config("RELAY_TRANSACTIONAL_FROM_EMAIL", default_from)
    slot_str = booking.start_datetime.strftime("%Y-%m-%d %H:%M UTC")
    subject = f"[Alert] Calendar invite failed for 1:1 call with {booking.invitee_name} ({slot_str})"
    body = _build_failure_alert_body(booking, reason)
    msg = EmailMessage(subject=subject, body=body, from_email=from_email, to=[recipient])
    if ics_content:
        msg.attach("booking.ics", ics_content, "text/calendar; method=REQUEST; charset=utf-8")
    try:
        msg.send(fail_silently=False)
        return True
    except Exception:
        logger.exception("Failed to send calendar failure alert email for booking %s", booking.pk)
        return False
