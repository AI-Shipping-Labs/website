"""Member-facing Request a call page (#870, #1404, #1953)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from booking.models import STATUS_PENDING, BookingHost, CallRequest
from booking.services.allowance import (
    get_member_allowance,
    send_call_request_notification,
)
from community.models import CallHost

TOPIC_CHOICES = [
    "AI Project Architecture & Feedback",
    "Course / Workshop Question",
    "Sprint & Career Strategy",
    "AI Shipping Labs Membership",
    "Other",
]


@login_required
@require_http_methods(["GET", "POST"])
def request_a_call(request):
    """Render the request-a-call page or handle call request submission.

    Gating:
    - Anonymous -> ``login_required`` redirects to login.
    - Authenticated but onboarding NOT complete -> show onboarding gate.
    - Authenticated with active booking -> show active booking banner.
    - Authenticated with pending request -> show pending review banner.
    - Authenticated with 0 allowance -> show call request form.
    - Authenticated with available allowance -> show host profiles.
    """
    allowance = get_member_allowance(request.user)

    if request.method == "POST":
        if not allowance.is_onboarded:
            messages.error(request, "Finish onboarding before requesting a call.")
            return redirect("request_a_call")

        if allowance.has_pending_request:
            messages.warning(request, "You already have a call request under review.")
            return redirect("request_a_call")

        topic = request.POST.get("topic", "").strip()
        message = request.POST.get("message", "").strip()

        if not topic or not message:
            messages.error(request, "Please provide a topic and message for your call request.")
            return redirect("request_a_call")

        host = BookingHost.objects.filter(is_active=True).first()
        if not host:
            host, _ = BookingHost.objects.get_or_create(
                slug="alexey",
                defaults={
                    "name": "Alexey Grigorev",
                    "email": "alexey@datatalks.club",
                    "timezone": "Europe/Berlin",
                    "is_active": True,
                },
            )

        call_request = CallRequest.objects.create(
            member=request.user,
            host=host,
            topic=topic,
            message=message,
            status=STATUS_PENDING,
        )

        send_call_request_notification(call_request)

        messages.success(
            request,
            "Your call request has been submitted to Alexey for review.",
        )
        next_url = request.POST.get("next")
        if next_url and next_url.startswith("/"):
            return redirect(next_url)
        return redirect("request_a_call")

    hosts = []
    if allowance.can_book:
        hosts = list(
            host
            for host in CallHost.objects.filter(is_active=True).order_by("order", "name")
            if host.is_available
        )

    context = {
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
        "topic_choices": TOPIC_CHOICES,
        "hosts": hosts,
    }
    return render(request, "content/request_a_call.html", context)
