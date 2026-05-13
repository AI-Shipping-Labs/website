"""Playwright E2E for issue #484: improved event detail + registration confirmation.

Covers the user-visible improvements:

1. Anonymous visitors see the rewritten registration card explaining that a
   free account is required and that registration implies email + newsletter
   updates, with login + signup links that preserve the event slug.
2. Authenticated unregistered users see the standard register button.
3. After registering, the page reloads with the post-registration
   confirmation surface: "You're registered!", an "Add to calendar" button
   linking to the .ics download, the explicit "check email" / "join 15 min
   before" next-step list, and the cancel-registration affordance.
4. The .ics download URL responds with a valid VCALENDAR file.

Usage:
    uv run pytest playwright_tests/test_event_detail_registration.py -v
"""

import datetime
import os
import urllib.request
from datetime import timedelta

import pytest
from django.utils import timezone

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402


def _clear_events():
    from events.models import Event, EventRegistration

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_event(
    *,
    slug,
    title,
    cover_image_url="",
    start_datetime=None,
    status="upcoming",
):
    from events.models import Event

    if start_datetime is None:
        start_datetime = timezone.now() + datetime.timedelta(days=7)
    event = Event.objects.create(
        slug=slug,
        title=title,
        description="A workshop run by the community.",
        start_datetime=start_datetime,
        status=status,
        cover_image_url=cover_image_url,
    )
    connection.close()
    return event


@pytest.mark.django_db(transaction=True)
class TestAnonymousRegistrationCopy:
    """Issue #513: anonymous CTA on a free upcoming event is the inline
    email-only registration form. The form copy discloses that a free
    account will be created and that the user can unsubscribe at any
    time. The legacy "Sign in / Create free account" button pair is
    replaced; the "Already have an account? Sign in" link below the form
    preserves the return URL for returning users.
    """

    @pytest.mark.core
    def test_anonymous_sees_email_only_form(self, django_server, page):
        _clear_events()
        _ensure_tiers()
        _create_event(slug="anon-evt", title="Anon Event")

        response = page.goto(
            f"{django_server}/events/anon-evt",
            wait_until="domcontentloaded",
        )
        assert response.status == 200

        form_card = page.locator('[data-testid="event-anonymous-email-form"]')
        assert form_card.count() == 1
        text = form_card.inner_text()
        assert "Register for this event" in text
        assert "free account" in text
        assert "unsubscribe" in text

        # Email input + submit button are both present.
        assert page.locator('#event-anon-email').count() == 1
        assert page.locator('#event-anon-submit-btn').count() == 1

        # Returning-user sign-in link preserves the event slug.
        login = page.locator(
            'a[href="/accounts/login/?next=/events/anon-evt"]'
        )
        assert login.count() == 1
        # The legacy "Create free account" button is gone for free events.
        assert page.locator(
            'a[href="/accounts/signup/?next=/events/anon-evt"]'
        ).count() == 0


@pytest.mark.django_db(transaction=True)
class TestPostRegistrationConfirmation:
    """Issue #484: post-registration surface shows email + ICS + next steps."""

    @pytest.mark.core
    def test_registered_state_shows_full_confirmation(
        self, django_server, browser
    ):
        _clear_events()
        _ensure_tiers()
        _create_user("reg484@test.com", tier_slug="free")

        event = _create_event(slug="reg484-evt", title="Reg 484 Event")

        context = _auth_context(browser, "reg484@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/events/reg484-evt",
            wait_until="domcontentloaded",
        )

        # Pre-register state: standard register button
        register_btn = page.locator("#register-btn")
        assert register_btn.count() == 1
        register_btn.click()

        # The JS reloads the page after the API call. Wait for the
        # confirmation surface to appear.
        page.wait_for_selector(
            '[data-testid="event-registered-confirmation"]',
            timeout=10000,
        )

        # 1. "You're registered!" headline
        confirmation = page.locator(
            '[data-testid="event-registered-confirmation"]'
        )
        assert "You're registered!" in confirmation.inner_text()

        # 2. Add to calendar button points at the .ics download
        add_to_cal = page.locator('[data-testid="event-add-to-calendar"]')
        assert add_to_cal.count() == 1
        href = add_to_cal.get_attribute("href")
        assert href == f"/events/{event.slug}/calendar.ics"
        assert "Add to calendar" in add_to_cal.inner_text()

        # 3. Next-step list mentions email + 15 minutes before start
        next_steps = page.locator('[data-testid="event-next-steps"]')
        assert next_steps.count() == 1
        steps_text = next_steps.inner_text()
        assert "email" in steps_text.lower()
        assert "15 minutes" in steps_text

        # 4. Cancel registration is still available, but moved below the
        #    next-step block.
        cancel = page.locator("#unregister-btn")
        assert cancel.count() == 1
        assert "Cancel registration" in cancel.inner_text()

    def test_ics_download_returns_vcalendar(self, django_server):
        _clear_events()
        _create_event(slug="ics-evt", title="ICS Event")

        # Public download — no auth needed for non-draft events.
        response = urllib.request.urlopen(
            f"{django_server}/events/ics-evt/calendar.ics", timeout=5,
        )
        body = response.read().decode("utf-8")
        assert response.status == 200
        assert "text/calendar" in response.headers.get("Content-Type", "")
        assert "BEGIN:VCALENDAR" in body
        assert "END:VCALENDAR" in body
        assert "SUMMARY:ICS Event" in body


@pytest.mark.django_db(transaction=True)
class TestEventDetailCoverImage:
    """Issue #484: cover image renders when set, falls back otherwise."""

    def test_cover_image_renders_when_set(self, django_server, page):
        _clear_events()
        _create_event(
            slug="img-evt",
            title="Image Event",
            cover_image_url="https://cdn.example.com/cover.jpg",
        )
        page.goto(
            f"{django_server}/events/img-evt",
            wait_until="domcontentloaded",
        )
        assert page.locator(
            '[data-testid="event-cover-image"]'
        ).count() == 1
        assert page.locator(
            '[data-testid="event-cover-fallback"]'
        ).count() == 0

    def test_decorative_fallback_when_no_cover(self, django_server, page):
        _clear_events()
        _create_event(slug="nocov-evt", title="No Cover Event")
        page.goto(
            f"{django_server}/events/nocov-evt",
            wait_until="domcontentloaded",
        )
        assert page.locator(
            '[data-testid="event-cover-fallback"]'
        ).count() == 1
        assert page.locator(
            '[data-testid="event-cover-image"]'
        ).count() == 0


# Suppress unused-import warnings for the import-only modules above.
_ = timedelta
