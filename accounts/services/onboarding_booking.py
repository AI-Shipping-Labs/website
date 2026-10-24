"""Founder booking URL resolution for onboarding and member emails."""

from django.urls import reverse

from booking.models import BookingHost
from community.models import CallHost
from integrations.config import site_base_url


def get_founder_booking_url(slug: str, *, absolute: bool = False) -> str:
    """Return the booking URL for a founder slug (e.g. 'alexey', 'valeria').

    Prefers the native booking page (reverse('booking_host', slug=slug) ->
    /book/<slug>) when an active booking.BookingHost exists for that founder.
    Falls back to CallHost.usable_booking_url (e.g. Calendly) only when no
    active native host exists. Returns '' if neither is active/available.
    """
    if BookingHost.objects.filter(slug=slug, is_active=True).exists():
        path = reverse("booking_host", kwargs={"slug": slug})
        if absolute:
            return f"{site_base_url().rstrip('/')}{path}"
        return path

    call_host = CallHost.objects.filter(slug=slug, is_active=True).first()
    if call_host and call_host.is_available:
        return call_host.usable_booking_url
    return ""


def get_founder_booking_urls() -> dict[str, str]:
    """Return booking URLs for the onboarding complete screen."""
    return {
        "valeria_booking_url": get_founder_booking_url("valeria"),
        "alexey_booking_url": get_founder_booking_url("alexey"),
    }
