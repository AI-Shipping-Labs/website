"""Playwright E2E for issue #484: improved event detail + registration confirmation.

Covers the user-visible improvements:

1. Anonymous visitors see the rewritten registration card explaining that a
   free account is required and that registration implies email + newsletter
   updates, with login + signup links that preserve the event slug.
2. Authenticated unregistered users see the standard register button.
3. After registering, the page reloads with the post-registration
   confirmation surface: "You're registered!", an "Add to calendar" button
   linking to the .ics download, the explicit "check email" / "join 5 min
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

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only


def _clear_events():
    from content.models import Workshop, WorkshopPage
    from events.models import Event, EventRegistration

    EventRegistration.objects.all().delete()
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_event(
    *,
    slug,
    title,
    cover_image_url="",
    start_datetime=None,
    status="upcoming",
    required_level=0,
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
        required_level=required_level,
    )
    connection.close()
    return event


def _create_past_freestyle_recording(slug, title, *, days_ago, with_workshop=False):
    from content.models import Workshop
    from events.models import Event

    start_datetime = timezone.now() - datetime.timedelta(days=days_ago)
    event = Event.objects.create(
        slug=slug,
        title=title,
        description="A recorded freestyle session.",
        start_datetime=start_datetime,
        status="completed",
        recording_url=f"https://example.com/{slug}.mp4",
    )
    if with_workshop:
        Workshop.objects.create(
            slug=f"{slug}-writeup",
            title=f"{title} Writeup",
            date=start_datetime.date(),
            status="published",
            landing_required_level=0,
            pages_required_level=5,
            recording_required_level=20,
            event=event,
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
        event = _create_event(slug="anon-evt", title="Anon Event")

        # Issue #673: canonical URL is ``/events/<id>/<slug>``; old
        # slug-only URLs 404 by design.
        event_path = event.get_absolute_url()
        response = page.goto(
            f"{django_server}{event_path}",
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

        # Returning-user sign-in link preserves the canonical event URL.
        login = page.locator(
            f'a[href="/accounts/login/?next={event_path}"]'
        )
        assert login.count() == 1
        # The legacy "Create free account" button is gone for free events.
        assert page.locator(
            f'a[href="/accounts/signup/?next={event_path}"]'
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
        # Issue #673: canonical URL is ``/events/<id>/<slug>``.
        page.goto(
            f"{django_server}{event.get_absolute_url()}",
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

        # 3. Next-step list mentions email + 5 minutes before start
        next_steps = page.locator('[data-testid="event-next-steps"]')
        assert next_steps.count() == 1
        steps_text = next_steps.inner_text()
        assert "email" in steps_text.lower()
        assert "5 minutes" in steps_text
        assert "15 minutes" not in steps_text

        # 4. Cancel registration is still available, but moved below the
        #    next-step block.
        cancel = page.locator("#unregister-btn")
        assert cancel.count() == 1
        assert "Cancel registration" in cancel.inner_text()

    def test_ics_download_returns_vcalendar(self, django_server):
        _clear_events()
        _create_event(slug="ics-evt", title="ICS Event")

        # Public download — no auth needed for non-draft events.
        # Issue #673: ``/events/<slug>/calendar.ics`` (slug-keyed) is the
        # intentional ICS surface — kept on slug for email/.ics emails.
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
    """Issue #484 + #651: cover image renders when set; when missing,
    no hero block (neither image nor fallback) is rendered on the
    detail page."""

    def test_cover_image_renders_when_set(self, django_server, page):
        _clear_events()
        event = _create_event(
            slug="img-evt",
            title="Image Event",
            cover_image_url="https://cdn.example.com/cover.jpg",
        )
        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        assert page.locator(
            '[data-testid="event-cover-image"]'
        ).count() == 1
        assert page.locator(
            '[data-testid="event-cover-fallback"]'
        ).count() == 0

    def test_no_hero_block_when_no_cover(self, django_server, page):
        """Issue #651: empty cover_image_url renders neither image nor
        decorative fallback — the back-link is followed directly by
        the event title."""
        _clear_events()
        event = _create_event(slug="nocov-evt", title="No Cover Event")
        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        assert page.locator(
            '[data-testid="event-cover-fallback"]'
        ).count() == 0
        assert page.locator(
            '[data-testid="event-cover-image"]'
        ).count() == 0


@pytest.mark.django_db(transaction=True)
class TestAnonymousPaidEventCopy:
    """Issue #671: anonymous visitors on a tier-gated event must see
    tier-aware copy that names the required tier and points at /pricing
    BEFORE pushing them to create an account. The misleading "free
    account is required" copy is gone.
    """

    @pytest.mark.core
    def test_anonymous_main_event_shows_tier_aware_copy(
        self, django_server, page
    ):
        """Mirrors the live event at
        /events/solving-a-real-ai-engineer-take-home-assignment-live
        (Main-tier, anonymous visitor)."""
        _clear_events()
        _ensure_tiers()
        event = _create_event(
            slug="solving-a-real-ai-engineer-take-home-assignment-live-fixture",
            title="Solving a real AI engineer take-home assignment (live)",
            required_level=20,  # LEVEL_MAIN
        )

        response = page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        assert response.status == 200

        card = page.locator('[data-testid="event-anonymous-cta"]')
        assert card.count() == 1
        card_text = card.inner_text()

        # Heading names the tier.
        assert "This event is for Main members" in card_text
        # Body sentence names the tier and links to pricing.
        assert "requires a Main membership or above" in card_text
        # The misleading old copy is gone.
        assert "free account" not in card_text.lower()
        assert "create a free account" not in card_text.lower()

        # Primary CTA: "View membership options" -> /pricing
        pricing_cta = card.locator(
            '[data-testid="event-anonymous-pricing-cta"]'
        )
        assert pricing_cta.count() == 1
        assert pricing_cta.get_attribute("href") == "/pricing"
        assert "View membership options" in pricing_cta.inner_text()

        # Secondary CTA: "Sign in" preserving ?next= to event URL.
        signin_cta = card.locator(
            '[data-testid="event-anonymous-signin-cta"]'
        )
        assert signin_cta.count() == 1
        signin_href = signin_cta.get_attribute("href")
        assert signin_href.startswith("/accounts/login/?next=")
        assert (
            "solving-a-real-ai-engineer-take-home-assignment-live-fixture"
            in signin_href
        )
        assert signin_cta.inner_text().strip() == "Sign in"

    def test_clicking_view_membership_options_lands_on_pricing(
        self, django_server, page
    ):
        _clear_events()
        _ensure_tiers()
        event = _create_event(
            slug="paid-main-pricing-flow",
            title="Paid Main Pricing Flow",
            required_level=20,
        )

        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        page.locator(
            '[data-testid="event-anonymous-pricing-cta"]'
        ).click()
        page.wait_for_url(f"{django_server}/pricing")
        # Smoke check: the pricing page renders without an error.
        assert "Pricing" in page.title() or "pricing" in page.url

    def test_paid_freestyle_event_shows_past_freestyle_evidence(
        self, django_server, page
    ):
        _clear_events()
        _ensure_tiers()
        event = _create_event(
            slug="premium-freestyle-build",
            title="Premium Freestyle Build",
            required_level=20,
        )
        _create_past_freestyle_recording(
            "fresh-freestyle",
            "Fresh Freestyle Session",
            days_ago=3,
            with_workshop=True,
        )
        _create_past_freestyle_recording(
            "mid-freestyle",
            "Mid Freestyle Session",
            days_ago=7,
        )
        _create_past_freestyle_recording(
            "older-freestyle",
            "Older Freestyle Session",
            days_ago=14,
        )

        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )

        evidence = page.get_by_test_id("freestyle-evidence-block")
        assert evidence.is_visible()
        links = page.get_by_test_id("freestyle-evidence-link")
        assert links.count() == 3
        assert "Fresh Freestyle Session Writeup" in evidence.inner_text()
        assert "Mid Freestyle Session" in evidence.inner_text()
        assert "Older Freestyle Session" in evidence.inner_text()

    def test_anonymous_free_event_still_shows_email_form(
        self, django_server, page
    ):
        """Regression check: free events keep the inline email-only
        signup form (issue #513) and never show the tier-aware copy.
        """
        _clear_events()
        _ensure_tiers()
        event = _create_event(
            slug="free-fixture-event",
            title="Free Fixture Event",
            required_level=0,
        )

        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        # Email form is present.
        assert page.locator(
            '[data-testid="event-anonymous-email-form"]'
        ).count() == 1
        # Tier-aware copy must NOT appear on a free event.
        assert page.locator(
            '[data-testid="event-anonymous-cta"]'
        ).count() == 0
        # The registration card must not link to /pricing on a free event.
        card = page.locator(
            '[data-testid="event-registration-card"]'
        )
        assert card.locator('a[href="/pricing"]').count() == 0

    def test_anonymous_premium_event_drops_or_above(
        self, django_server, page
    ):
        """Premium is the highest public tier, so the body must NOT say
        "Premium membership or above" — there's nothing higher.
        """
        _clear_events()
        _ensure_tiers()
        event = _create_event(
            slug="premium-fixture-event",
            title="Premium Fixture Event",
            required_level=30,  # LEVEL_PREMIUM
        )

        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        card = page.locator('[data-testid="event-anonymous-cta"]')
        card_text = card.inner_text()

        assert "This event is for Premium members" in card_text
        assert "requires a Premium membership" in card_text
        # The "or above" suffix must be dropped for Premium.
        assert "Premium membership or above" not in card_text


@pytest.mark.django_db(transaction=True)
class TestUnderTierCopyConsistency:
    """Issue #671: authenticated user under the required tier sees the
    same phrasing as the anonymous-on-paid CTA — "Registering for this
    event requires a {tier} membership or above." and the Premium case
    drops "or above".
    """

    @pytest.mark.core
    def test_free_user_on_main_event_sees_consistent_copy(
        self, django_server, browser
    ):
        _clear_events()
        _ensure_tiers()
        _create_user("free671@test.com", tier_slug="free")
        event = _create_event(
            slug="solving-a-real-ai-engineer-take-home-assignment-live-fixture",
            title="Solving a real AI engineer take-home assignment (live)",
            required_level=20,  # LEVEL_MAIN
        )

        context = _auth_context(browser, "free671@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )

        card = page.locator(
            '[data-testid="event-registration-card"]'
        )
        card_text = card.inner_text()

        # Heading: upgrade to the named tier.
        assert "Upgrade to Main to attend" in card_text
        # Body: same "Registering for this event requires…" phrasing as
        # the anonymous-on-paid CTA.
        assert (
            "requires a Main membership or above" in card_text
        )
        # "free account" must not appear anywhere in the under-tier card.
        assert "free account" not in card_text.lower()

        # The View Pricing button is the upgrade CTA.
        pricing_link = card.locator('a[href="/pricing"]')
        assert pricing_link.count() >= 1
        # Click the View Pricing button and land on /pricing.
        pricing_link.first.click()
        page.wait_for_url(f"{django_server}/pricing")
        context.close()

    def test_basic_user_on_premium_event_drops_or_above(
        self, django_server, browser
    ):
        _clear_events()
        _ensure_tiers()
        _create_user("basic671@test.com", tier_slug="basic")
        event = _create_event(
            slug="premium-fixture-event",
            title="Premium Fixture Event",
            required_level=30,
        )

        context = _auth_context(browser, "basic671@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )

        card = page.locator(
            '[data-testid="event-registration-card"]'
        )
        card_text = card.inner_text()

        assert "Upgrade to Premium to attend" in card_text
        assert "requires a Premium membership" in card_text
        # No double-up: Premium is highest, so "or above" must be dropped.
        assert "Premium membership or above" not in card_text
        context.close()


# Suppress unused-import warnings for the import-only modules above.
_ = timedelta
