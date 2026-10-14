"""Public booking views."""

import urllib.parse
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib import messages
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from booking.models import (
    STATUS_CANCELED,
    Booking,
    BookingHost,
)
from booking.services.allowance import get_member_allowance
from booking.services.booking_service import (
    SlotUnavailableError,
    cancel_booking,
    create_booking,
    generate_ics,
    get_available_slots,
    get_host_by_slug,
    reschedule_booking,
)

COMMON_TIMEZONES = [
    "UTC",
    "Europe/Berlin",
    "Europe/London",
    "Europe/Paris",
    "Europe/Amsterdam",
    "Europe/Madrid",
    "Europe/Rome",
    "Europe/Warsaw",
    "Europe/Kyiv",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Toronto",
    "America/Vancouver",
    "America/Sao_Paulo",
    "Asia/Dubai",
    "Asia/Kolkata",
    "Asia/Singapore",
    "Asia/Tokyo",
    "Asia/Shanghai",
    "Australia/Sydney",
    "Australia/Melbourne",
    "Pacific/Auckland",
]

TOPIC_CHOICES = [
    "AI Project Architecture & Feedback",
    "Course / Workshop Question",
    "Sprint & Career Strategy",
    "AI Shipping Labs Membership",
    "Other",
]


def booking_index(request):
    """Default entry point redirects to Alexey's booking page."""
    if not request.user.is_authenticated:
        return redirect(f"/accounts/login/?next={request.path}")

    host = BookingHost.objects.filter(is_active=True).first()
    slug = host.slug if host else "alexey"
    return redirect("booking_host", slug=slug)


def host_booking(request, slug):
    """Main public booking page showing interactive calendar and details form."""
    if not request.user.is_authenticated:
        return redirect(f"/accounts/login/?next={request.path}")

    host = get_host_by_slug(slug)
    if not host:
        raise Http404("Booking host not found.")

    allowance = get_member_allowance(request.user)

    initial_tz = request.GET.get("tz") or host.timezone
    try:
        ZoneInfo(initial_tz)
    except (ZoneInfoNotFoundError, ValueError):
        initial_tz = host.timezone

    context = {
        "host": host,
        "allowance": allowance,
        "onboarded": allowance.is_onboarded,
        "can_access_onboarding": allowance.can_access_onboarding,
        "active_booking": allowance.active_booking,
        "has_active_booking": allowance.has_active_booking,
        "pending_request": allowance.pending_request,
        "has_pending_request": allowance.has_pending_request,
        "declined_request": allowance.declined_request,
        "has_declined_request": allowance.has_declined_request,
        "remaining_allowance": allowance.remaining_allowance,
        "can_book": allowance.can_book,
        "can_request_call": allowance.can_request_call,
        "initial_tz": initial_tz,
        "common_timezones": COMMON_TIMEZONES,
        "topic_choices": TOPIC_CHOICES,
        "today_iso": date.today().isoformat(),
    }
    return render(request, "booking/book.html", context)


@require_GET
def host_slots(request, slug):
    """JSON API returning available slots for a date range in the viewer timezone."""
    host = get_host_by_slug(slug)
    if not host:
        return JsonResponse({"error": "Host not found"}, status=404)

    viewer_tz = request.GET.get("tz", host.timezone)
    from_date_str = request.GET.get("from")
    to_date_str = request.GET.get("to")

    try:
        from_date = date.fromisoformat(from_date_str) if from_date_str else date.today()
        to_date = date.fromisoformat(to_date_str) if to_date_str else (from_date + timedelta(days=30))
    except ValueError:
        return JsonResponse({"error": "Invalid date format, expected YYYY-MM-DD"}, status=400)

    if (to_date - from_date).days > 62 or to_date < from_date:
        return JsonResponse({"error": "Date range must be between 0 and 62 days"}, status=400)

    exclude_id = request.GET.get("exclude_booking_id")
    exclude_booking_id = int(exclude_id) if exclude_id and exclude_id.isdigit() else None

    slots = get_available_slots(
        host,
        from_date=from_date,
        to_date=to_date,
        viewer_timezone=viewer_tz,
        exclude_booking_id=exclude_booking_id,
    )

    days = sorted(list({s["date"] for s in slots}))
    return JsonResponse({"slots": slots, "days": days, "count": len(slots)})


@require_POST
def host_confirm(request, slug):
    """Confirm a selected slot and provision the booking."""
    if not request.user.is_authenticated:
        if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.headers.get("accept") == "application/json":
            return JsonResponse({"error": "Authentication required"}, status=401)
        return redirect(f"/accounts/login/?next=/book/{slug}")

    host = get_host_by_slug(slug)
    if not host:
        raise Http404("Host not found")

    allowance = get_member_allowance(request.user)

    if not allowance.is_onboarded:
        err_msg = "Please finish onboarding before booking a call."
        if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.headers.get("accept") == "application/json":
            return JsonResponse({"error": err_msg}, status=400)
        messages.error(request, err_msg)
        return redirect("booking_host", slug=slug)

    if allowance.has_active_booking:
        err_msg = "You already have an active scheduled meeting. Please reschedule or cancel it first."
        if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.headers.get("accept") == "application/json":
            return JsonResponse({"error": err_msg}, status=400)
        messages.error(request, err_msg)
        return redirect("booking_host", slug=slug)

    if allowance.remaining_allowance <= 0:
        err_msg = "You have 0 remaining booking allowance. Please submit a call request."
        if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.headers.get("accept") == "application/json":
            return JsonResponse({"error": err_msg}, status=400)
        messages.error(request, err_msg)
        return redirect("booking_host", slug=slug)

    start_iso = request.POST.get("start_iso", "").strip()
    invitee_name = request.POST.get("invitee_name", "").strip()
    invitee_email = request.POST.get("invitee_email", "").strip()
    invitee_tz = request.POST.get("invitee_timezone", "UTC").strip()
    discussion_topic = request.POST.get("discussion_topic", "").strip()
    discussion_notes = request.POST.get("discussion_notes", "").strip()
    company = request.POST.get("company", "").strip()

    if not start_iso or not invitee_name or not invitee_email:
        messages.error(request, "Name, email, and meeting time are required.")
        return redirect("booking_host", slug=slug)

    try:
        start_datetime = datetime.fromisoformat(start_iso)
    except ValueError:
        messages.error(request, "Invalid start time format.")
        return redirect("booking_host", slug=slug)

    try:
        booking = create_booking(
            host=host,
            invitee_name=invitee_name,
            invitee_email=invitee_email,
            start_datetime=start_datetime,
            invitee_timezone=invitee_tz,
            discussion_topic=discussion_topic,
            discussion_notes=discussion_notes,
            company=company,
            member=request.user,
        )
    except SlotUnavailableError:
        messages.error(request, "That slot was just booked. Please pick another available time.")
        return redirect("booking_host", slug=slug)

    return redirect("booking_confirmed", token=booking.manage_token)


def booking_confirmed(request, token):
    """View details of a confirmed booking."""
    booking = get_object_or_404(Booking, manage_token=token)

    try:
        invitee_tz = ZoneInfo(booking.invitee_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        invitee_tz = ZoneInfo("UTC")

    try:
        host_tz = ZoneInfo(booking.host.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        host_tz = ZoneInfo("UTC")

    invitee_start = booking.start_datetime.astimezone(invitee_tz)
    invitee_end = booking.end_datetime.astimezone(invitee_tz)
    host_start = booking.start_datetime.astimezone(host_tz)

    # Google Calendar web add link
    gcal_params = {
        "action": "TEMPLATE",
        "text": f"30-min meeting: {booking.invitee_name} and {booking.host.name}",
        "dates": f"{booking.start_datetime.strftime('%Y%m%dT%H%M%SZ')}/{booking.end_datetime.strftime('%Y%m%dT%H%M%SZ')}",
        "details": f"Topic: {booking.discussion_topic}\nZoom Link: {booking.zoom_join_url}",
        "location": booking.zoom_join_url,
    }
    gcal_url = f"https://calendar.google.com/calendar/render?{urllib.parse.urlencode(gcal_params)}"

    context = {
        "booking": booking,
        "invitee_start": invitee_start,
        "invitee_end": invitee_end,
        "host_start": host_start,
        "gcal_url": gcal_url,
    }
    return render(request, "booking/confirmed.html", context)


def booking_reschedule(request, token):
    """Reschedule an existing booking."""
    booking = get_object_or_404(Booking, manage_token=token)

    if booking.status == STATUS_CANCELED:
        messages.warning(request, "This meeting was canceled and cannot be rescheduled.")
        return redirect("booking_confirmed", token=token)

    if request.method == "POST":
        new_start_iso = request.POST.get("start_iso", "").strip()
        if not new_start_iso:
            messages.error(request, "Please choose a new time slot.")
            return redirect("booking_reschedule", token=token)

        try:
            new_start_datetime = datetime.fromisoformat(new_start_iso)
            reschedule_booking(booking, new_start_datetime)
            messages.success(request, "Meeting rescheduled successfully!")
            return redirect("booking_confirmed", token=token)
        except SlotUnavailableError:
            messages.error(request, "Selected time slot is not available. Please choose another.")
            return redirect("booking_reschedule", token=token)

    context = {
        "booking": booking,
        "host": booking.host,
        "initial_tz": booking.invitee_timezone or booking.host.timezone,
        "common_timezones": COMMON_TIMEZONES,
        "today_iso": date.today().isoformat(),
    }
    return render(request, "booking/reschedule.html", context)


def booking_cancel(request, token):
    """Cancel an existing booking."""
    booking = get_object_or_404(Booking, manage_token=token)

    if request.method == "POST":
        if booking.status != STATUS_CANCELED:
            cancel_booking(booking)
            messages.success(request, "Your meeting has been canceled.")
        return redirect("booking_confirmed", token=token)

    context = {
        "booking": booking,
    }
    return render(request, "booking/cancel.html", context)


def booking_ics(request, token):
    """Download standard .ics iCalendar file."""
    booking = get_object_or_404(Booking, manage_token=token)
    content = generate_ics(booking)
    response = HttpResponse(content, content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="meeting-{booking.start_datetime.strftime("%Y%m%d")}.ics"'
    return response
