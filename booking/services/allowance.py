"""Booking allowance, gating, and call request notification service."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone as dj_timezone

from booking.models import (
    STATUS_APPROVED,
    STATUS_CONFIRMED,
    STATUS_DECLINED,
    STATUS_PENDING,
    Booking,
    CallRequest,
)
from content.access import LEVEL_TO_TIER_NAME, get_user_level
from integrations.config import get_config, site_base_url
from questionnaires.onboarding import can_access_onboarding, has_completed_onboarding

if TYPE_CHECKING:
    from accounts.models import User

logger = logging.getLogger(__name__)

DEFAULT_NOTIFICATION_EMAIL = "alexey@datatalks.club"


@dataclass
class MemberAllowance:
    is_authenticated: bool
    is_onboarded: bool
    can_access_onboarding: bool
    base_allowance: int
    approved_requests_count: int
    total_allowance: int
    confirmed_bookings_count: int
    remaining_allowance: int
    active_booking: Booking | None
    has_active_booking: bool
    pending_request: CallRequest | None
    has_pending_request: bool
    declined_request: CallRequest | None
    has_declined_request: bool

    @property
    def can_book(self) -> bool:
        return (
            self.is_authenticated
            and self.is_onboarded
            and not self.has_active_booking
            and self.remaining_allowance > 0
        )

    @property
    def can_request_call(self) -> bool:
        return (
            self.is_authenticated
            and self.is_onboarded
            and not self.has_active_booking
            and self.remaining_allowance <= 0
            and not self.has_pending_request
        )


def get_member_allowance(user: User | None, *, now: datetime | None = None) -> MemberAllowance:
    """Calculate the booking allowance and state for a member."""
    if user is None or not getattr(user, "is_authenticated", False):
        return MemberAllowance(
            is_authenticated=False,
            is_onboarded=False,
            can_access_onboarding=False,
            base_allowance=0,
            approved_requests_count=0,
            total_allowance=0,
            confirmed_bookings_count=0,
            remaining_allowance=0,
            active_booking=None,
            has_active_booking=False,
            pending_request=None,
            has_pending_request=False,
            declined_request=None,
            has_declined_request=False,
        )

    now_utc = now or dj_timezone.now()
    is_onboarded = has_completed_onboarding(user)
    can_onboard = can_access_onboarding(user)
    base_allowance = 1 if is_onboarded else 0

    approved_requests_count = CallRequest.objects.filter(
        member=user,
        status=STATUS_APPROVED,
    ).count()

    total_allowance = base_allowance + approved_requests_count

    # Only confirmed bookings count against the allowance; canceled bookings do not.
    confirmed_bookings_count = Booking.objects.filter(
        member=user,
        status=STATUS_CONFIRMED,
    ).count()

    remaining_allowance = max(0, total_allowance - confirmed_bookings_count)

    # Active booking: confirmed with end_datetime > now
    active_booking = (
        Booking.objects.filter(
            member=user,
            status=STATUS_CONFIRMED,
            end_datetime__gt=now_utc,
        )
        .order_by("start_datetime")
        .first()
    )

    pending_request = (
        CallRequest.objects.filter(
            member=user,
            status=STATUS_PENDING,
        )
        .order_by("-created_at")
        .first()
    )

    declined_request = (
        CallRequest.objects.filter(
            member=user,
            status=STATUS_DECLINED,
        )
        .order_by("-created_at")
        .first()
    )

    return MemberAllowance(
        is_authenticated=True,
        is_onboarded=is_onboarded,
        can_access_onboarding=can_onboard,
        base_allowance=base_allowance,
        approved_requests_count=approved_requests_count,
        total_allowance=total_allowance,
        confirmed_bookings_count=confirmed_bookings_count,
        remaining_allowance=remaining_allowance,
        active_booking=active_booking,
        has_active_booking=active_booking is not None,
        pending_request=pending_request,
        has_pending_request=pending_request is not None,
        declined_request=declined_request,
        has_declined_request=declined_request is not None,
    )


def _member_display_name(member: User) -> str:
    name = getattr(member, "name", "") or ""
    if not name and hasattr(member, "get_full_name"):
        name = member.get_full_name() or ""
    return name.strip() or getattr(member, "email", "Member")


def _member_tier_name(member: User) -> str:
    try:
        level = get_user_level(member)
        return LEVEL_TO_TIER_NAME.get(level, "")
    except Exception:
        return ""


def send_call_request_notification(call_request: CallRequest) -> bool:
    """Send an email notification to Alexey regarding a new call request."""
    member = call_request.member
    member_name = _member_display_name(member)
    member_email = getattr(member, "email", "")
    tier_name = _member_tier_name(member)

    # Recipient is the host email or configured default
    host = call_request.host
    recipient = (host.email if host and host.email else "").strip()
    if not recipient:
        recipient = DEFAULT_NOTIFICATION_EMAIL

    base = site_base_url()
    studio_url = f"{base}/studio/call-requests/{call_request.pk}/"
    crm_url = f"{base}/studio/crm/{member.pk}/"

    subject = f"New call request from {member_name} ({member_email})"

    lines = [
        f"A new 1:1 call request has been submitted by {member_name} ({member_email}).",
        "",
        f"Topic: {call_request.topic}",
    ]
    if tier_name:
        lines.append(f"Member Tier: {tier_name}")
    lines.extend([
        "",
        "Message:",
        call_request.message,
        "",
        f"Review in Studio: {studio_url}",
        f"Member CRM Record: {crm_url}",
    ])
    body = "\n".join(lines)

    from_email = get_config("SES_FROM_EMAIL", getattr(settings, "DEFAULT_FROM_EMAIL", "community@aishippinglabs.com"))

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=from_email,
            recipient_list=[recipient],
            fail_silently=True,
        )
        return True
    except Exception:
        logger.exception("Failed to send call request notification email for request %s", call_request.pk)
        return False
