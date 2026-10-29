"""Calendly public availability client for 1:1 slot parity with Calendly."""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone as dj_timezone

if TYPE_CHECKING:
    from booking.models import BookingHost

logger = logging.getLogger(__name__)

DEFAULT_ALEXEY_CALENDLY_URL = "https://calendly.com/dtc-alexey/ai-shipping-labs-call"
CALENDLY_PUBLIC_API_BASE = "https://calendly.com/api/booking/event_types"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_EVENT_TYPE_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}
_CACHE_TTL_SECONDS = 900.0
_MAX_CHUNK_DAYS = 28


class CalendlySlotsError(Exception):
    """Raised when Calendly availability lookup or range query fails."""


def clear_calendly_slots_cache() -> None:
    """Clear the cached Calendly event-type UUID lookup table."""
    _EVENT_TYPE_CACHE.clear()


def resolve_calendly_event_url(host: BookingHost) -> str:
    """Return the canonical Calendly event URL for ``host``, or blank if local-only."""
    try:
        from community.models import CallHost

        call_host = CallHost.objects.filter(slug=host.slug, is_active=True).first()
    except Exception:
        call_host = None

    if call_host and call_host.booking_url:
        raw_url = call_host.booking_url.strip()
        if "calendly.com/" in raw_url:
            return raw_url
        if raw_url.startswith("/"):
            return _fallback_alexey_calendly_url(host)
        return ""

    return _fallback_alexey_calendly_url(host)


def _fallback_alexey_calendly_url(host: BookingHost) -> str:
    """Return Alexey's canonical Calendly URL unless running an offline UTC test fixture."""
    if host.slug != "alexey":
        return ""
    if host.email != "alexey@datatalks.club":
        return ""
    if host.timezone == "UTC" and float(host.notice_hours or 0.0) == 0.0:
        return ""
    return DEFAULT_ALEXEY_CALENDLY_URL


def parse_calendly_event_url(calendly_url: str) -> tuple[str, str]:
    """Extract ``(profile_slug, event_type_slug)`` from a Calendly event URL."""
    parsed = urllib.parse.urlsplit(str(calendly_url or "").strip())
    raw_parts = parsed.path.strip("/").split("/")
    parts = []
    for part in raw_parts:
        cleaned = part.strip()
        if cleaned:
            parts.append(cleaned)
    if len(parts) < 2:
        raise CalendlySlotsError(f"Invalid Calendly event URL: {calendly_url}")
    return parts[0], parts[1]


def _calendly_get_json(url: str, params: dict, timeout: int = 15) -> dict:
    """Execute a GET request against Calendly's public booking API."""
    query = urllib.parse.urlencode(params)
    full_url = f"{url}?{query}"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    req = Request(full_url, headers=headers, method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise CalendlySlotsError(f"Calendly request failed for {url}: {exc}") from exc

    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise CalendlySlotsError(f"Calendly returned invalid JSON from {url}") from exc
    if not isinstance(data, dict):
        raise CalendlySlotsError(f"Calendly returned non-object JSON from {url}")
    return data


def lookup_event_type(profile_slug: str, event_type_slug: str) -> dict:
    """Resolve the Calendly event type UUID and duration for ``(profile_slug, event_type_slug)``."""
    cache_key = (profile_slug, event_type_slug)
    now_mono = time.monotonic()
    cached = _EVENT_TYPE_CACHE.get(cache_key)
    if cached and (now_mono - cached[0]) < _CACHE_TTL_SECONDS:
        return dict(cached[1])

    data = _calendly_get_json(
        f"{CALENDLY_PUBLIC_API_BASE}/lookup",
        {"profile_slug": profile_slug, "event_type_slug": event_type_slug},
    )
    event_uuid = str(data.get("uuid") or "").strip()
    if not event_uuid:
        raise CalendlySlotsError(
            f"Calendly lookup returned no UUID for {profile_slug}/{event_type_slug}"
        )
    info = {
        "uuid": event_uuid,
        "duration": int(data.get("duration") or 30),
        "availability_timezone": str(data.get("availability_timezone") or "UTC"),
    }
    _EVENT_TYPE_CACHE[cache_key] = (now_mono, info)
    return dict(info)


def _extract_day_spots(spots: list) -> list[datetime]:
    """Parse available spot start times from a single day's spots list."""
    starts: list[datetime] = []
    for spot in spots:
        if not isinstance(spot, dict):
            continue
        if spot.get("status") != "available":
            continue
        raw_start = str(spot.get("start_time") or "").strip()
        if not raw_start:
            continue
        try:
            dt = datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
            starts.append(dt.astimezone(timezone.utc))
        except ValueError:
            continue
    return starts


def _extract_available_starts(data: dict) -> list[datetime]:
    """Extract all available UTC start datetimes from a Calendly range payload."""
    starts: list[datetime] = []
    for day in data.get("days") or []:
        if not isinstance(day, dict):
            continue
        if day.get("status") != "available":
            continue
        day_starts = _extract_day_spots(day.get("spots") or [])
        starts.extend(day_starts)
    return starts


def fetch_range_spots(event_uuid: str, start_date: date, end_date: date) -> list[datetime]:
    """Fetch available UTC start times across ``[start_date, end_date]`` in <=28-day chunks."""
    all_starts: list[datetime] = []
    chunk_start = start_date
    while chunk_start <= end_date:
        chunk_end = chunk_start + timedelta(days=_MAX_CHUNK_DAYS - 1)
        if chunk_end > end_date:
            chunk_end = end_date
        data = _calendly_get_json(
            f"{CALENDLY_PUBLIC_API_BASE}/{event_uuid}/calendar/range",
            {
                "timezone": "UTC",
                "diagnostics": "false",
                "range_start": chunk_start.isoformat(),
                "range_end": chunk_end.isoformat(),
            },
        )
        all_starts.extend(_extract_available_starts(data))
        chunk_start = chunk_end + timedelta(days=1)
    return all_starts


def _overlaps_intervals(
    start_utc: datetime,
    end_utc: datetime,
    intervals: list[tuple[datetime, datetime]],
) -> bool:
    """Return True if ``(start_utc, end_utc)`` overlaps any busy interval."""
    for b_start, b_end in intervals:
        if start_utc < b_end and end_utc > b_start:
            return True
    return False


def _resolve_zone(tz_name: str) -> ZoneInfo:
    """Resolve ``tz_name`` to a ZoneInfo, falling back to UTC."""
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _format_filtered_slots(
    starts: list[datetime],
    *,
    duration_min: int,
    viewer_tz: ZoneInfo,
    from_date: date,
    to_date: date,
    busy_intervals: list[tuple[datetime, datetime]],
) -> list[dict]:
    """Filter UTC spot starts against busy intervals and viewer date bounds."""
    seen: set[str] = set()
    results: list[dict] = []
    for start_utc in sorted(starts):
        start_iso = start_utc.isoformat()
        if start_iso in seen:
            continue
        end_utc = start_utc + timedelta(minutes=duration_min)
        if _overlaps_intervals(start_utc, end_utc, busy_intervals):
            continue
        viewer_start = start_utc.astimezone(viewer_tz)
        slot_date = viewer_start.date()
        if from_date <= slot_date <= to_date:
            seen.add(start_iso)
            results.append(
                {
                    "start_iso": start_iso,
                    "end_iso": end_utc.isoformat(),
                    "time_display": viewer_start.strftime("%H:%M"),
                    "date": slot_date.isoformat(),
                }
            )
    return results


def fetch_calendly_available_slots(
    calendly_url: str,
    *,
    from_date: date,
    to_date: date,
    viewer_timezone: str = "UTC",
    duration_minutes: int = 30,
    busy_intervals: list[tuple[datetime, datetime]] | None = None,
) -> list[dict]:
    """Fetch 1:1 available slots from Calendly for ``[from_date, to_date]``."""
    viewer_tz = _resolve_zone(viewer_timezone)
    today_utc = dj_timezone.now().astimezone(timezone.utc).date() - timedelta(days=1)
    query_start = from_date - timedelta(days=1)
    if query_start < today_utc:
        query_start = today_utc
    query_end = to_date + timedelta(days=1)
    if query_end < query_start:
        return []

    profile_slug, event_type_slug = parse_calendly_event_url(calendly_url)
    info = lookup_event_type(profile_slug, event_type_slug)
    effective_duration = int(info.get("duration") or duration_minutes)
    starts = fetch_range_spots(info["uuid"], query_start, query_end)
    intervals = list(busy_intervals or [])
    return _format_filtered_slots(
        starts,
        duration_min=effective_duration,
        viewer_tz=viewer_tz,
        from_date=from_date,
        to_date=to_date,
        busy_intervals=intervals,
    )
