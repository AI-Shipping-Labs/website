"""Zoom meeting integration for 1:1 public bookings."""

import json
import logging
import urllib.parse
from datetime import datetime, timezone
import requests

from integrations.services.zoom import (
    ZOOM_API_BASE_URL,
    ZoomAPIError,
    get_access_token,
)

logger = logging.getLogger(__name__)


def _zoom_time(dt: datetime) -> str:
    """Format datetime as UTC ISO-8601 with Z."""
    utc_dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return utc_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_booking_zoom_meeting(
    *,
    topic: str,
    start_datetime: datetime,
    duration_minutes: int = 30,
    timezone_name: str = "UTC",
) -> dict:
    """Create a 1:1 Zoom meeting for a booking.

    Returns:
        dict: {'meeting_id': str, 'join_url': str}
    """
    try:
        token = get_access_token()
    except ZoomAPIError as exc:
        logger.warning("Zoom OAuth not configured or token unavailable: %s", exc)
        return {"meeting_id": "", "join_url": ""}

    url = urllib.parse.urljoin(ZOOM_API_BASE_URL, "users/me/meetings")
    payload = {
        "topic": topic[:200],
        "type": 2,  # Scheduled
        "start_time": _zoom_time(start_datetime),
        "duration": int(duration_minutes),
        "timezone": "UTC",
        "settings": {
            "join_before_host": True,
            "waiting_room": False,
            "approval_type": 2,  # No registration required
        },
    }

    try:
        resp = requests.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        if not (200 <= resp.status_code < 300):
            logger.warning("Zoom meeting creation returned %s: %s", resp.status_code, resp.text)
            return {"meeting_id": "", "join_url": ""}
        data = resp.json()
        return {
            "meeting_id": str(data.get("id", "")),
            "join_url": str(data.get("join_url", "")),
        }
    except Exception as exc:
        logger.error("Failed to connect to Zoom API: %s", exc)
        return {"meeting_id": "", "join_url": ""}


def update_booking_zoom_meeting(
    meeting_id: str,
    *,
    start_datetime: datetime,
    duration_minutes: int = 30,
) -> bool:
    """Update a Zoom meeting's schedule."""
    if not meeting_id:
        return False
    try:
        token = get_access_token()
    except ZoomAPIError:
        return False

    url = urllib.parse.urljoin(ZOOM_API_BASE_URL, f"meetings/{urllib.parse.quote(str(meeting_id))}")
    payload = {
        "start_time": _zoom_time(start_datetime),
        "duration": int(duration_minutes),
        "timezone": "UTC",
    }
    try:
        resp = requests.patch(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        return 200 <= resp.status_code < 300
    except Exception as exc:
        logger.error("Failed to update Zoom meeting %s: %s", meeting_id, exc)
        return False


def delete_booking_zoom_meeting(meeting_id: str) -> bool:
    """Delete a Zoom meeting."""
    if not meeting_id:
        return False
    try:
        token = get_access_token()
    except ZoomAPIError:
        return False

    url = urllib.parse.urljoin(ZOOM_API_BASE_URL, f"meetings/{urllib.parse.quote(str(meeting_id))}")
    try:
        resp = requests.delete(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        return 200 <= resp.status_code < 300 or resp.status_code == 404
    except Exception as exc:
        logger.error("Failed to delete Zoom meeting %s: %s", meeting_id, exc)
        return False
