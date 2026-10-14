"""Booking service orchestrating availability, reservations, Zoom, and Google Calendar."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone as dj_timezone

if TYPE_CHECKING:
    from accounts.models import User

from booking.models import (
    STATUS_CANCELED,
    STATUS_CONFIRMED,
    Booking,
    BookingHost,
)
from booking.services.availability import (
    Policy,
    expand_viewer_range,
    generate_slots,
)
from booking.services.calendar import (
    create_calendar_event,
    delete_calendar_event,
    query_busy,
    update_calendar_event,
)
from booking.services.zoom import (
    create_booking_zoom_meeting,
    delete_booking_zoom_meeting,
    update_booking_zoom_meeting,
)

logger = logging.getLogger(__name__)


def _site_base_url() -> str:
    """Canonical base URL for links in external calendar and invitations."""
    return getattr(settings, "SITE_URL", "https://aishippinglabs.com").rstrip("/")


def get_host_by_slug(slug: str) -> BookingHost | None:
    return BookingHost.objects.filter(slug=slug, is_active=True).first()


def host_schedule_dict(host: BookingHost) -> dict:
    """Build the schedule dictionary required by the availability engine."""
    weekly = {}
    for window in host.weekly_windows.filter(is_active=True):
        windows_list = weekly.setdefault(window.weekday, [])
        windows_list.append((window.start_time.strftime("%H:%M"), window.end_time.strftime("%H:%M")))

    overrides = {}
    for override in host.date_overrides.all():
        overrides[override.date.isoformat()] = {
            "is_unavailable": override.is_unavailable,
            "start_time": override.start_time.strftime("%H:%M") if override.start_time else "",
            "end_time": override.end_time.strftime("%H:%M") if override.end_time else "",
        }

    return {
        "timezone": host.timezone,
        "weekly_windows": weekly,
        "overrides": overrides,
    }


def host_policy(host: BookingHost) -> Policy:
    """Build the Policy object from host configurations."""
    return Policy(
        grid_min=host.slot_duration_minutes,
        notice_hours=host.notice_hours,
        horizon_days=host.horizon_days,
        pre_buffer_min=host.buffer_before_minutes,
        post_buffer_min=host.buffer_after_minutes,
        daily_count_limit=host.daily_count_limit,
        daily_minutes_limit=host.daily_minutes_limit,
    )


def get_available_slots(
    host: BookingHost,
    from_date: date,
    to_date: date,
    viewer_timezone: str = "UTC",
    exclude_booking_id: int | None = None,
) -> list[dict]:
    """Calculate and return available slots for a date range in the viewer's timezone."""
    try:
        viewer_tz = ZoneInfo(viewer_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        viewer_tz = ZoneInfo("UTC")

    schedule = host_schedule_dict(host)
    policy = host_policy(host)

    host_from, host_to = expand_viewer_range(from_date, to_date, str(viewer_tz), host.timezone)

    try:
        host_tz = ZoneInfo(host.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        host_tz = ZoneInfo("UTC")

    span_start = datetime(host_from.year, host_from.month, host_from.day, tzinfo=host_tz).astimezone(timezone.utc)
    span_end = (datetime(host_to.year, host_to.month, host_to.day, tzinfo=host_tz) + timedelta(days=1)).astimezone(
        timezone.utc
    )

    # 1. Query busy times from Google Calendar via Dapier
    busy_spans = query_busy(span_start, span_end)

    # 2. Query existing active bookings for this host
    active_bookings = host.bookings.filter(
        status=STATUS_CONFIRMED,
        start_datetime__gte=span_start,
        start_datetime__lte=span_end,
    )
    existing_bookings_data = [
        {
            "id": b.id,
            "start": b.start_datetime,
            "duration_min": b.duration_minutes,
            "status": b.status,
        }
        for b in active_bookings
    ]

    local_protected = [
        (b.start_datetime, b.end_datetime)
        for b in active_bookings
        if not (exclude_booking_id and str(b.id) == str(exclude_booking_id))
    ]

    now_utc = dj_timezone.now()
    offers = generate_slots(
        schedule=schedule,
        policy=policy,
        duration_min=host.slot_duration_minutes,
        busy=busy_spans,
        local_protected=local_protected,
        existing_bookings=existing_bookings_data,
        now_utc=now_utc,
        exclude_booking_id=exclude_booking_id or "",
        from_date=host_from,
        to_date=host_to,
    )

    results = []
    for offer in offers["slots"]:
        viewer_start = offer.start.astimezone(viewer_tz)
        slot_date = viewer_start.date()

        if from_date <= slot_date <= to_date:
            results.append(
                {
                    "start_iso": offer.start.isoformat(),
                    "end_iso": offer.end.isoformat(),
                    "time_display": viewer_start.strftime("%H:%M"),
                    "date": slot_date.isoformat(),
                }
            )

    return results


class SlotUnavailableError(Exception):
    pass


@transaction.atomic
def create_booking(
    *,
    host: BookingHost,
    invitee_name: str,
    invitee_email: str,
    start_datetime: datetime,
    invitee_timezone: str = "UTC",
    discussion_topic: str = "",
    discussion_notes: str = "",
    company: str = "",
    member: User | None = None,
) -> Booking:
    """Create a verified booking, provisioning Zoom and Google Calendar entries."""
    start_utc = start_datetime if start_datetime.tzinfo else start_datetime.replace(tzinfo=timezone.utc)
    start_utc = start_utc.astimezone(timezone.utc)
    duration_min = host.slot_duration_minutes
    end_utc = start_utc + timedelta(minutes=duration_min)

    # Verify slot availability
    available = get_available_slots(
        host,
        from_date=start_utc.date() - timedelta(days=1),
        to_date=start_utc.date() + timedelta(days=1),
        viewer_timezone="UTC",
    )
    slot_matches = any(slot["start_iso"] == start_utc.isoformat() for slot in available)
    if not slot_matches:
        raise SlotUnavailableError("Selected time slot is no longer available.")

    # Match registered member user by email if not provided
    if member is None:
        User = get_user_model()
        member = User.objects.filter(email__iexact=invitee_email.strip()).first()

    booking = Booking.objects.create(
        host=host,
        invitee_name=invitee_name.strip(),
        invitee_email=invitee_email.strip().lower(),
        invitee_timezone=invitee_timezone,
        start_datetime=start_utc,
        end_datetime=end_utc,
        duration_minutes=duration_min,
        status=STATUS_CONFIRMED,
        discussion_topic=discussion_topic.strip(),
        discussion_notes=discussion_notes.strip(),
        company=company.strip(),
        member=member,
    )

    # Create Zoom meeting
    zoom_topic = f"30-min meeting: {booking.invitee_name} and {host.name}"
    zoom_data = create_booking_zoom_meeting(
        topic=zoom_topic,
        start_datetime=start_utc,
        duration_minutes=duration_min,
        timezone_name=host.timezone,
    )
    booking.zoom_meeting_id = zoom_data.get("meeting_id", "")
    booking.zoom_join_url = zoom_data.get("join_url", "")

    # Management links
    site_base = _site_base_url()
    reschedule_url = f"{site_base}/book/reschedule/{booking.manage_token}"
    cancel_url = f"{site_base}/book/cancel/{booking.manage_token}"

    # Create Google Calendar event
    desc_lines = [
        f"30-minute meeting with {booking.invitee_name}",
        "",
    ]
    if booking.discussion_topic:
        desc_lines.append(f"Topic: {booking.discussion_topic}")
    if booking.discussion_notes:
        desc_lines.append(f"Notes: {booking.discussion_notes}")
    if booking.company:
        desc_lines.append(f"Company: {booking.company}")
    if booking.zoom_join_url:
        desc_lines.append(f"\nZoom Meeting: {booking.zoom_join_url}")
    desc_lines.append(f"\nReschedule: {reschedule_url}")
    desc_lines.append(f"Cancel: {cancel_url}")
    description = "\n".join(desc_lines)

    gcal_event = create_calendar_event(
        summary=zoom_topic,
        description=description,
        start=start_utc,
        end=end_utc,
        attendee_email=booking.invitee_email,
        location=booking.zoom_join_url,
    )
    booking.google_event_id = gcal_event.get("id", "")
    booking.save(update_fields=["zoom_meeting_id", "zoom_join_url", "google_event_id"])

    # Record CRM BookedCall in community
    _record_crm_booked_call(booking, reschedule_url, cancel_url)

    return booking


def _record_crm_booked_call(booking: Booking, reschedule_url: str, cancel_url: str):
    """Upsert BookedCall record in community so it displays on CRM."""
    try:
        from community.models import BookedCall, CallHost

        call_host = CallHost.objects.filter(slug=booking.host.slug).first()
        if not call_host:
            # Fallback to any active CallHost or create a matching one
            call_host = CallHost.objects.filter(is_active=True).first()

        if call_host:
            event_uri = f"aisl:booking:{booking.manage_token}"
            booked_call, _ = BookedCall.objects.update_or_create(
                calendly_event_uri=event_uri,
                defaults={
                    "host": call_host,
                    "member": booking.member,
                    "invitee_email": booking.invitee_email,
                    "invitee_name": booking.invitee_name,
                    "scheduled_at": booking.start_datetime,
                    "status": "booked",
                    "calendly_invitee_uri": f"aisl:invitee:{booking.manage_token}",
                    "reschedule_url": reschedule_url,
                    "cancel_url": cancel_url,
                },
            )
            booking.booked_call = booked_call
            booking.save(update_fields=["booked_call"])
    except Exception as exc:
        logger.exception("Failed to record BookedCall for booking %s: %s", booking.id, exc)

    # CRM call integration seam (issue #1949). The canonical
    # record_call(*, email, occurred_at, summary, source, source_ref)
    # contract upserts on (source, source_ref), so booking retries never
    # duplicate the CRM call. Failures must not break the booking
    # confirmation, but they are logged, never swallowed silently.
    try:
        from crm.services.calls import record_call

        summary = booking.discussion_topic or f"30-min meeting with {booking.host.name}"
        if booking.discussion_notes:
            summary = f"{summary}\n{booking.discussion_notes}"
        record_call(
            email=booking.invitee_email,
            occurred_at=booking.start_datetime,
            summary=summary,
            source="booking",
            source_ref=str(booking.id),
        )
    except Exception as exc:
        logger.exception(
            "Failed to record CRM Call for booking %s: %s", booking.id, exc,
        )


@transaction.atomic
def reschedule_booking(booking: Booking, new_start_datetime: datetime) -> Booking:
    """Reschedule an existing booking to a new start time."""
    start_utc = new_start_datetime if new_start_datetime.tzinfo else new_start_datetime.replace(tzinfo=timezone.utc)
    start_utc = start_utc.astimezone(timezone.utc)
    end_utc = start_utc + timedelta(minutes=booking.duration_minutes)

    # Check slot availability excluding this booking itself
    available = get_available_slots(
        booking.host,
        from_date=start_utc.date() - timedelta(days=1),
        to_date=start_utc.date() + timedelta(days=1),
        viewer_timezone="UTC",
        exclude_booking_id=booking.id,
    )
    if not any(slot["start_iso"] == start_utc.isoformat() for slot in available):
        raise SlotUnavailableError("Selected reschedule slot is not available.")

    booking.start_datetime = start_utc
    booking.end_datetime = end_utc
    booking.status = STATUS_CONFIRMED
    booking.save(update_fields=["start_datetime", "end_datetime", "status", "updated_at"])

    # Update Zoom
    if booking.zoom_meeting_id:
        update_booking_zoom_meeting(
            booking.zoom_meeting_id,
            start_datetime=start_utc,
            duration_minutes=booking.duration_minutes,
        )

    # Update Google Calendar
    if booking.google_event_id:
        update_calendar_event(
            booking.google_event_id,
            start=start_utc,
            end=end_utc,
        )

    # Update CRM BookedCall
    if booking.booked_call:
        booking.booked_call.scheduled_at = start_utc
        booking.booked_call.status = "booked"
        booking.booked_call.save(update_fields=["scheduled_at", "status", "updated_at"])

    return booking


@transaction.atomic
def cancel_booking(booking: Booking) -> Booking:
    """Cancel an active booking."""
    booking.status = STATUS_CANCELED
    booking.canceled_at = dj_timezone.now()
    booking.save(update_fields=["status", "canceled_at", "updated_at"])

    # Delete Zoom meeting
    if booking.zoom_meeting_id:
        delete_booking_zoom_meeting(booking.zoom_meeting_id)

    # Delete Google Calendar event
    if booking.google_event_id:
        delete_calendar_event(booking.google_event_id)

    # Update CRM BookedCall
    if booking.booked_call:
        booking.booked_call.status = "canceled"
        booking.booked_call.canceled_at = dj_timezone.now()
        booking.booked_call.save(update_fields=["status", "canceled_at", "updated_at"])

    return booking


def generate_ics(booking: Booking) -> str:
    """Generate an RFC 5545 iCalendar string for the booking."""
    dt_stamp = dj_timezone.now().strftime("%Y%m%dT%H%M%SZ")
    dt_start = booking.start_datetime.strftime("%Y%m%dT%H%M%SZ")
    dt_end = booking.end_datetime.strftime("%Y%m%dT%H%M%SZ")
    uid = f"booking-{booking.manage_token}@aishippinglabs.com"
    summary = f"30-min meeting: {booking.invitee_name} and {booking.host.name}"
    description = f"Topic: {booking.discussion_topic}\\nZoom Link: {booking.zoom_join_url}\\n"

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//AI Shipping Labs//Booking//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{dt_stamp}",
        f"DTSTART:{dt_start}",
        f"DTEND:{dt_end}",
        f"SUMMARY:{summary}",
        f"DESCRIPTION:{description}",
        f"LOCATION:{booking.zoom_join_url}",
        f"ORGANIZER;CN={booking.host.name}:mailto:{booking.host.email}",
        f"ATTENDEE;CN={booking.invitee_name};ROLE=REQ-PARTICIPANT:mailto:{booking.invitee_email}",
        "STATUS:CONFIRMED",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"
