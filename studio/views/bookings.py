"""Studio views for member Booking review and cancellation (#2018)."""

import logging

from django.conf import settings
from django.contrib import messages
from django.core.mail import send_mail
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from booking.models import (
    STATUS_CANCELED,
    STATUS_CONFIRMED,
    STATUS_RESCHEDULED,
    Booking,
)
from booking.services.booking_service import cancel_booking
from content.access import LEVEL_TO_TIER_NAME, get_user_level
from crm.models import CRMRecord
from integrations.config import get_config, site_base_url
from studio.decorators import staff_required

logger = logging.getLogger(__name__)

BOOKING_STATUS_FILTERS = (STATUS_CONFIRMED, STATUS_CANCELED, STATUS_RESCHEDULED)


def _member_tier_name(user):
    try:
        level = get_user_level(user)
        return LEVEL_TO_TIER_NAME.get(level, "Free")
    except Exception:
        return ""


@staff_required
@require_GET
def studio_booking_list(request):
    """List member bookings, newest start first, with status filters."""
    status_filter = request.GET.get("status", "all").strip().lower()

    qs = Booking.objects.select_related("host", "member").order_by("-start_datetime")

    counts = {
        "all": qs.count(),
        "confirmed": qs.filter(status=STATUS_CONFIRMED).count(),
        "canceled": qs.filter(status=STATUS_CANCELED).count(),
        "rescheduled": qs.filter(status=STATUS_RESCHEDULED).count(),
    }

    if status_filter in BOOKING_STATUS_FILTERS:
        bookings = qs.filter(status=status_filter)
    else:
        status_filter = "all"
        bookings = qs

    context = {
        "bookings": bookings,
        "counts": counts,
        "active_status": status_filter,
    }
    return render(request, "studio/bookings/list.html", context)


@staff_required
@require_GET
def studio_booking_detail(request, booking_id):
    """Show booking details with member context and the cancel action."""
    booking = get_object_or_404(
        Booking.objects.select_related("host", "member", "booked_call"),
        pk=booking_id,
    )

    crm_record = None
    tier_name = ""
    if booking.member:
        tier_name = _member_tier_name(booking.member)
        crm_record = CRMRecord.objects.filter(user=booking.member).first()

    context = {
        "booking": booking,
        "member": booking.member,
        "tier_name": tier_name,
        "crm_record": crm_record,
    }
    return render(request, "studio/bookings/detail.html", context)


@staff_required
@require_POST
def studio_booking_cancel(request, booking_id):
    """Cancel a confirmed booking through the shared booking service."""
    booking = get_object_or_404(
        Booking.objects.select_related("host", "member", "booked_call"),
        pk=booking_id,
    )

    if booking.status == STATUS_CANCELED:
        # Idempotency guard: never re-run the service (it would re-delete
        # Zoom/Google entries and re-stamp canceled_at) for a double POST.
        messages.info(request, "This booking is already canceled.")
        return redirect("studio_booking_detail", booking_id=booking.pk)

    notify_member = request.POST.get("notify_member") == "on"

    cancel_booking(booking)

    if notify_member and not _send_cancellation_notice(booking):
        messages.warning(
            request,
            "Booking canceled, but the notification email could not be sent.",
        )

    messages.success(request, f"Booking for {booking.invitee_email} canceled.")
    return redirect("studio_booking_detail", booking_id=booking.pk)


def _send_cancellation_notice(booking):
    """Send the opt-in cancellation email to the invitee.

    Returns True when the email went out. A send failure must never roll
    back or block the already-committed cancellation, but it is logged and
    surfaced as a warning, never swallowed silently.
    """
    host = booking.host
    start_utc = booking.start_datetime.strftime("%Y-%m-%d %H:%M")
    date_utc = booking.start_datetime.strftime("%Y-%m-%d")
    rebook_url = f"{site_base_url()}/book/{host.slug}"

    subject = f"Your call with {host.name} on {date_utc} was canceled"
    body = "\n".join([
        f"Hi {booking.invitee_name},",
        "",
        f"Your call with {host.name} scheduled for {start_utc} UTC was canceled.",
        "",
        f"You can pick a new time here: {rebook_url}",
        "",
        "— AI Shipping Labs",
    ])

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=get_config(
                "RELAY_TRANSACTIONAL_FROM_EMAIL",
                getattr(settings, "DEFAULT_FROM_EMAIL", "community@aishippinglabs.com"),
            ),
            recipient_list=[booking.invitee_email],
            fail_silently=False,
        )
        return True
    except Exception:
        # External boundary: the cancellation is already committed and must
        # stand even when the Relay send fails, so this cannot propagate.
        logger.exception(
            "Failed to send cancellation notice for booking %s", booking.pk,
        )
        return False
