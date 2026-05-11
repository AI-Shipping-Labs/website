"""Playwright E2E for issue #572: surfacing external events.

Covers the eight scenarios in the groomed issue body:

1. Anonymous visitor sees a "Hosted on Maven" pill on the events list
   alongside a community event; the Maven detail page surfaces the
   external Join button (no email-only registration form) and the
   ``href`` matches the partner URL with ``target="_blank"`` and
   ``rel="noopener noreferrer"``.
2. Free member visiting a ``required_level=20`` external event sees no
   upgrade gate; the Join button is rendered and clickable.
3. Past recordings list distinguishes a community workshop and an
   external Luma meetup via the pill.
4. Staff adds a new external event via Studio; saving persists the
   field, and the public detail page surfaces the new pill + Join
   button pointing at the partner URL.
5. Clearing the External host in Studio reverts the event to the
   community registration card.
6. External event with empty ``zoom_join_url`` shows a polite
   "Link coming soon" placeholder instead of breaking.
7. Tag filtering on ``/events?filter=past&tag=X`` keeps external events
   that match the tag and drops external events that do not.
8. Anonymous visitor on a free external event does NOT see the email-
   only registration form, and the in-app "Sign in to register" link
   is absent in the registration area.

Usage:
    uv run pytest playwright_tests/test_external_events.py -v
"""

import datetime
import os
from datetime import timedelta

import pytest
from django.utils import timezone

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
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
    external_host="",
    zoom_join_url="",
    required_level=0,
    status="upcoming",
    tags=None,
    recording_url="",
    start_datetime=None,
    published=True,
):
    """Create an event with sane defaults for E2E tests."""
    from events.models import Event

    if start_datetime is None:
        start_datetime = timezone.now() + datetime.timedelta(days=7)
    event = Event.objects.create(
        slug=slug,
        title=title,
        description=f"Description for {title}.",
        start_datetime=start_datetime,
        status=status,
        external_host=external_host,
        zoom_join_url=zoom_join_url,
        required_level=required_level,
        tags=tags or [],
        recording_url=recording_url,
        published=published,
    )
    connection.close()
    return event


# --- Scenario 1 ---------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestAnonymousSpotsMavenAndRegistersOffPlatform:
    """Anonymous visitor browses /events, identifies the Maven cohort by
    its pill, drills into the detail page, and is sent off-platform via
    a properly-attributed external Join button.
    """

    def test_anonymous_sees_pill_and_external_join_button(
        self, django_server, page,
    ):
        _clear_events()
        _ensure_tiers()
        _create_event(
            slug="community-live", title="Live coding session",
        )
        _create_event(
            slug="llm-eng-cohort",
            title="LLM Engineering Cohort",
            external_host="Maven",
            zoom_join_url="https://maven.com/aisl/llm-eng",
        )

        response = page.goto(
            f"{django_server}/events?filter=upcoming",
            wait_until="domcontentloaded",
        )
        assert response.status == 200

        # Both events appear in the list. We check `inner_text` of the
        # listing region rather than relying on text-locator counts —
        # Playwright's `get_by_text` does case-insensitive substring
        # matching and may match nested text nodes more than once.
        body_text = page.locator("main").inner_text()
        assert "LLM Engineering Cohort" in body_text
        assert "Live coding session" in body_text

        # Exactly one external pill in the list (the Maven event).
        pills = page.locator('[data-testid="event-card-external-badge"]')
        assert pills.count() == 1
        assert "Hosted on Maven" in pills.first.inner_text()

        # Drill into the Maven event detail page.
        page.goto(
            f"{django_server}/events/llm-eng-cohort",
            wait_until="domcontentloaded",
        )

        # Header pill present alongside status pill.
        header_pill = page.locator(
            '[data-testid="event-detail-external-badge"]',
        )
        assert header_pill.count() == 1
        assert "Hosted on Maven" in header_pill.inner_text()

        # The email-only registration form is gone for external events.
        assert page.locator(
            '[data-testid="event-anonymous-email-form"]',
        ).count() == 0

        # Join button is present and points to the partner URL with
        # blank target + noopener/noreferrer.
        join = page.locator('[data-testid="event-external-join-link"]')
        assert join.count() == 1
        assert "Join on Maven" in join.inner_text()
        assert join.get_attribute("href") == "https://maven.com/aisl/llm-eng"
        assert join.get_attribute("target") == "_blank"
        rel = join.get_attribute("rel") or ""
        assert "noopener" in rel
        assert "noreferrer" in rel


# --- Scenario 2 ---------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestFreeMemberSeesNoPaywallOnExternalEvent:
    """A free-tier member visiting an external event marked Main+ must
    still see the Join button. external_host bypasses required_level
    for visibility AND for the Join action.
    """

    def test_free_user_sees_join_not_paywall(self, django_server, browser):
        _clear_events()
        _ensure_tiers()
        _create_user("free572@test.com", tier_slug="free")
        _create_event(
            slug="dtc-live",
            title="DataTalksClub Live",
            external_host="DataTalksClub",
            zoom_join_url="https://datatalksclub.com/live",
            required_level=20,
        )

        ctx = _auth_context(browser, "free572@test.com")
        page = ctx.new_page()

        # Listing shows the pill on the gated external card.
        page.goto(
            f"{django_server}/events?filter=upcoming",
            wait_until="domcontentloaded",
        )
        pill = page.locator('[data-testid="event-card-external-badge"]')
        assert pill.count() == 1
        assert "Hosted on DataTalksClub" in pill.first.inner_text()

        # Detail page does NOT show the upgrade gate.
        page.goto(
            f"{django_server}/events/dtc-live",
            wait_until="domcontentloaded",
        )
        # The Main+ paywall normally renders the text "Upgrade to Main".
        body_text = page.locator("body").inner_text()
        assert "Upgrade to Main" not in body_text
        assert page.locator(
            '[data-testid="event-required-tier-label"]',
        ).count() == 0

        # Join button is rendered and points to the partner URL.
        join = page.locator('[data-testid="event-external-join-link"]')
        assert join.count() == 1
        assert (
            join.get_attribute("href")
            == "https://datatalksclub.com/live"
        )


# --- Scenario 3 ---------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestPastListDistinguishesExternalAndCommunity:
    """The past-recordings list shows the external pill only on the
    Luma card, not on the community workshop card.
    """

    def test_past_list_shows_pill_only_on_external_card(
        self, django_server, browser,
    ):
        _clear_events()
        _ensure_tiers()
        _create_user("main572@test.com", tier_slug="main")
        past_start = timezone.now() - timedelta(days=14)
        _create_event(
            slug="community-workshop",
            title="Community Workshop Recap",
            status="completed",
            recording_url="https://example.com/recap.mp4",
            start_datetime=past_start,
        )
        _create_event(
            slug="luma-meetup",
            title="Luma Meetup",
            external_host="Luma",
            status="completed",
            recording_url="https://lu.ma/replay/abc",
            start_datetime=past_start + timedelta(days=1),
        )

        ctx = _auth_context(browser, "main572@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/events?filter=past",
            wait_until="domcontentloaded",
        )

        # Both cards are present in the listing area.
        body_text = page.locator("main").inner_text()
        assert "Community Workshop Recap" in body_text
        assert "Luma Meetup" in body_text

        # Exactly one external pill (the Luma card).
        pills = page.locator('[data-testid="event-card-external-badge"]')
        assert pills.count() == 1
        assert "Hosted on Luma" in pills.first.inner_text()


# --- Scenario 4 ---------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestStaffAddsExternalEventViaStudio:
    """A staff user types a new host into the Studio form, saves, and
    sees the public detail page surface the new pill + Join button.
    """

    def test_staff_can_set_external_host_in_studio(
        self, django_server, browser,
    ):
        _clear_events()
        _ensure_tiers()
        _create_staff_user("staff572@test.com")
        event = _create_event(
            slug="luma-meetup-jan",
            title="Luma Meetup January",
            status="draft",
        )

        ctx = _auth_context(browser, "staff572@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/events/{event.pk}/edit",
            wait_until="domcontentloaded",
        )

        # Fill the External host input and the Custom URL field.
        page.locator('[data-testid="studio-event-external-host"]').fill(
            "Luma",
        )
        # Switch platform to Custom URL so the custom URL section
        # surfaces.
        page.locator('#platform-select').select_option("custom")
        page.locator('#custom-url-input').fill(
            "https://lu.ma/aisl-meetup-jan",
        )
        # Set status to upcoming so the event becomes publicly visible.
        page.locator('select[name="status"]').select_option("upcoming")

        # Submit by clicking the sticky save button. The form has a
        # ``button[type="submit"]`` inside the sticky action bar, and a
        # real click triggers Playwright's navigation tracking cleanly.
        with page.expect_navigation(wait_until="domcontentloaded"):
            page.locator('button[type="submit"]').first.click()

        # After save the form reloads with Luma populated.
        host_input = page.locator(
            '[data-testid="studio-event-external-host"]',
        )
        assert host_input.input_value() == "Luma"

        # Public detail page shows the pill + Join button.
        page.goto(
            f"{django_server}/events/luma-meetup-jan",
            wait_until="domcontentloaded",
        )
        pill = page.locator(
            '[data-testid="event-detail-external-badge"]',
        )
        assert pill.count() == 1
        assert "Hosted on Luma" in pill.inner_text()

        join = page.locator(
            '[data-testid="event-external-join-link"]',
        )
        assert join.count() == 1
        assert "Join on Luma" in join.inner_text()
        assert (
            join.get_attribute("href")
            == "https://lu.ma/aisl-meetup-jan"
        )


# --- Scenario 5 ---------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestClearingExternalHostRevertsToCommunityFlow:
    """Clearing the External host in Studio puts the event back on the
    community registration flow on the public detail page.
    """

    def test_cleared_external_host_restores_registration_card(
        self, django_server, browser,
    ):
        _clear_events()
        _ensure_tiers()
        _create_staff_user("staff572b@test.com")
        event = _create_event(
            slug="was-maven",
            title="Was a Maven event",
            external_host="Maven",
            zoom_join_url="https://maven.com/old",
        )

        ctx = _auth_context(browser, "staff572b@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/events/{event.pk}/edit",
            wait_until="domcontentloaded",
        )

        # Clear the External host field.
        page.locator('[data-testid="studio-event-external-host"]').fill("")

        # Submit the form via the sticky save button.
        with page.expect_navigation(wait_until="domcontentloaded"):
            page.locator('button[type="submit"]').first.click()

        # Public detail: external pill is gone and the community
        # registration card is back.
        page.goto(
            f"{django_server}/events/was-maven",
            wait_until="domcontentloaded",
        )
        assert page.locator(
            '[data-testid="event-detail-external-badge"]',
        ).count() == 0
        assert page.locator(
            '[data-testid="event-external-join-card"]',
        ).count() == 0
        assert page.locator(
            '[data-testid="event-registration-card"]',
        ).count() == 1


# --- Scenario 6 ---------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestExternalEventWithoutJoinUrlShowsComingSoon:
    """An external event with an empty ``zoom_join_url`` must render a
    polite placeholder and return 200 — no 500 from a missing href.
    """

    def test_empty_join_url_renders_link_coming_soon(
        self, django_server, page,
    ):
        _clear_events()
        _ensure_tiers()
        _create_event(
            slug="maven-tba",
            title="Maven event TBA",
            external_host="Maven",
            zoom_join_url="",
        )

        response = page.goto(
            f"{django_server}/events/maven-tba",
            wait_until="domcontentloaded",
        )
        assert response.status == 200

        # Pill is rendered.
        assert page.locator(
            '[data-testid="event-detail-external-badge"]',
        ).count() == 1

        # Placeholder is rendered; no Join link.
        assert page.locator(
            '[data-testid="event-external-join-coming-soon"]',
        ).count() == 1
        assert page.locator(
            '[data-testid="event-external-join-link"]',
        ).count() == 0


# --- Scenario 7 ---------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestPastTagFilterKeepsMatchingExternalEvents:
    """Filtering /events?filter=past by ``tag=python`` keeps the
    DataTalksClub event (tag matches) and drops the Maven cohort
    (tag does not match).
    """

    def test_tag_filter_keeps_matching_external(self, django_server, page):
        _clear_events()
        _ensure_tiers()
        past_start = timezone.now() - timedelta(days=14)
        _create_event(
            slug="community-py",
            title="Community Python recap",
            status="completed",
            recording_url="https://example.com/cpy.mp4",
            tags=["python"],
            start_datetime=past_start,
        )
        _create_event(
            slug="dtc-py",
            title="DataTalksClub Python livestream",
            external_host="DataTalksClub",
            status="completed",
            recording_url="https://datatalksclub.com/py",
            tags=["python"],
            start_datetime=past_start + timedelta(days=1),
        )
        _create_event(
            slug="maven-genai",
            title="Maven GenAI cohort",
            external_host="Maven",
            status="completed",
            recording_url="https://maven.com/genai",
            tags=["genai"],
            start_datetime=past_start + timedelta(days=2),
        )

        page.goto(
            f"{django_server}/events?filter=past&tag=python",
            wait_until="domcontentloaded",
        )

        body = page.locator("body").inner_text()
        assert "Community Python recap" in body
        assert "DataTalksClub Python livestream" in body
        assert "Maven GenAI cohort" not in body

        # Exactly one external pill in the filtered list (DTC).
        pills = page.locator('[data-testid="event-card-external-badge"]')
        assert pills.count() == 1
        assert "Hosted on DataTalksClub" in pills.first.inner_text()


# --- Scenario 8 ---------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestExternalAnonymousNoEmailRegistrationForm:
    """An anonymous visitor on a free upcoming external event must see
    the Join button but NOT the email-only registration form or the
    "Sign in to register" link.
    """

    def test_anonymous_no_email_form_on_external_event(
        self, django_server, page,
    ):
        _clear_events()
        _ensure_tiers()
        _create_event(
            slug="luma-anon",
            title="Luma meetup anonymous",
            external_host="Luma",
            zoom_join_url="https://lu.ma/aisl-anon",
            required_level=0,
        )

        response = page.goto(
            f"{django_server}/events/luma-anon",
            wait_until="domcontentloaded",
        )
        assert response.status == 200

        # Join button is visible.
        join = page.locator(
            '[data-testid="event-external-join-link"]',
        )
        assert join.count() == 1
        assert "Join on Luma" in join.inner_text()

        # No email-only registration form.
        assert page.locator(
            '[data-testid="event-anonymous-email-form"]',
        ).count() == 0

        # No "Sign in to register" link (anonymous CTA suppressed).
        # The phrase only appears in the suppressed CTA branch; the
        # detail page's "Back to Events" link is the only nav link
        # back to the listing.
        body_text = page.locator("body").inner_text()
        assert "Sign in to register" not in body_text
