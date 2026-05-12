"""Playwright E2E tests for the Studio New event flow (issue #574).

Five scenarios cover the create button, the publish-and-view flow, the
inline validation, the slug-collision guard, and the Studio vs GitHub
edit gate.

Usage:
    uv run pytest playwright_tests/test_studio_event_create.py -v
"""

import os
import re
from datetime import datetime, timedelta

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


def _reset_event_state():
    """Delete event/group/registration rows for a clean slate."""
    from django.db import connection

    from events.models import Event, EventGroup, EventRegistration

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    EventGroup.objects.all().delete()
    connection.close()


# ---------------------------------------------------------------------------
# Scenario 1: Admin creates a one-off event from the Studio events list
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario1CreateOneOff:
    def test_create_one_off_event(self, django_server, browser):
        from django.db import connection

        from events.models import Event

        _reset_event_state()
        _create_staff_user("staff-ec1@test.com")
        ctx = _auth_context(browser, "staff-ec1@test.com")
        page = ctx.new_page()

        page.goto(
            f"{django_server}/studio/events/", wait_until="domcontentloaded",
        )
        # Both create buttons are visible in the header.
        assert page.locator('[data-testid="event-new-button"]').is_visible()
        assert page.locator(
            '[data-testid="event-series-new-button"]'
        ).is_visible()

        page.locator('[data-testid="event-new-button"]').click()
        page.wait_for_url(re.compile(r".*/studio/events/new$"))
        # Form heading reads "New Event".
        assert page.get_by_role("heading", name="New Event").is_visible()

        page.fill('input[name="title"]', "Office Hours May 21")
        page.fill('input[name="event_date"]', "21/05/2026")
        page.fill('input[name="event_time"]', "18:00")
        # Leave duration blank — defaults to 1 hour.
        page.locator('[data-testid="event-create-submit"]').click()

        page.wait_for_url(re.compile(r".*/studio/events/\d+/edit$"))
        # Sidebar panels render now that an event exists.
        assert page.locator(
            '[data-testid="event-state-panel"]'
        ).is_visible()
        assert page.locator(
            '[data-testid="zoom-meeting-panel"]'
        ).is_visible()

        # Navigate back to the list.
        page.goto(
            f"{django_server}/studio/events/",
            wait_until="domcontentloaded",
        )
        # The new row is present with a Studio origin badge.
        new_event = Event.objects.get(title="Office Hours May 21")
        assert page.locator(
            f'tr:has(a[href="/studio/events/{new_event.pk}/edit"]) '
            f'[data-testid="origin-badge"][data-origin="studio"]'
        ).first.is_visible()
        connection.close()
        ctx.close()


# ---------------------------------------------------------------------------
# Scenario 2: Admin publishes a Studio event and visitors see it on /events
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario2PublishAndView:
    def test_publish_then_view_publicly(self, django_server, browser):
        from django.db import connection

        from events.models import Event

        _reset_event_state()
        _create_staff_user("staff-ec2@test.com")
        ctx = _auth_context(browser, "staff-ec2@test.com")
        page = ctx.new_page()

        # Use a date in the future so it lands on the upcoming filter.
        future = (datetime.now() + timedelta(days=30)).strftime("%d/%m/%Y")
        page.goto(
            f"{django_server}/studio/events/new",
            wait_until="domcontentloaded",
        )
        page.fill('input[name="title"]', "Public Demo Night")
        page.fill('input[name="event_date"]', future)
        page.fill('input[name="event_time"]', "19:00")
        # Switch status to upcoming so the row appears on /events.
        page.select_option('select[name="status"]', "upcoming")
        page.locator('[data-testid="event-create-submit"]').click()
        page.wait_for_url(re.compile(r".*/studio/events/\d+/edit$"))

        event = Event.objects.get(title="Public Demo Night")
        assert event.status == "upcoming"
        assert event.origin == "studio"
        connection.close()

        # Open /events in a fresh anonymous context.
        anon = browser.new_context(viewport={"width": 1280, "height": 720})
        anon_page = anon.new_page()
        anon_page.goto(
            f"{django_server}/events?filter=upcoming",
            wait_until="domcontentloaded",
        )
        assert anon_page.get_by_text("Public Demo Night").first.is_visible()

        # Click into the detail page.
        anon_page.get_by_text("Public Demo Night").first.click()
        anon_page.wait_for_url(re.compile(r".*/events/.*"))
        assert anon_page.locator("h1").first.is_visible()

        anon.close()
        ctx.close()


# ---------------------------------------------------------------------------
# Scenario 3: Inline validation when required fields are missing
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario3Validation:
    def test_required_field_validation(self, django_server, browser):
        from events.models import Event

        _reset_event_state()
        _create_staff_user("staff-ec3@test.com")
        ctx = _auth_context(browser, "staff-ec3@test.com")
        page = ctx.new_page()

        page.goto(
            f"{django_server}/studio/events/new",
            wait_until="domcontentloaded",
        )

        # Submit without filling title — bypass HTML5 required attribute so
        # we exercise the server-side validation branch.
        page.evaluate(
            "document.querySelector('input[name=\"title\"]').removeAttribute('required')"
        )
        page.evaluate(
            "document.querySelector('input[name=\"event_date\"]').removeAttribute('required')"
        )
        page.evaluate(
            "document.querySelector('input[name=\"event_time\"]').removeAttribute('required')"
        )
        page.locator('[data-testid="event-create-submit"]').click()

        page.wait_for_load_state("domcontentloaded")
        assert "/studio/events/new" in page.url
        assert page.locator('[data-testid="error-title"]').is_visible()
        assert Event.objects.count() == 0

        # Fill title, leave event_date blank, submit.
        page.fill('input[name="title"]', "Quick Demo")
        page.evaluate(
            "document.querySelector('input[name=\"event_date\"]').removeAttribute('required')"
        )
        page.fill('input[name="event_time"]', "10:00")
        page.fill('input[name="event_date"]', "")
        page.locator('[data-testid="event-create-submit"]').click()

        page.wait_for_load_state("domcontentloaded")
        assert "/studio/events/new" in page.url
        assert page.locator('[data-testid="error-event-date"]').is_visible()
        # Title is preserved.
        assert page.locator('input[name="title"]').input_value() == "Quick Demo"
        assert Event.objects.count() == 0

        ctx.close()


# ---------------------------------------------------------------------------
# Scenario 4: Cannot create with a slug that already exists
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario4DuplicateSlug:
    def test_duplicate_slug_rejected(self, django_server, browser):
        from django.db import connection

        from events.models import Event

        _reset_event_state()
        existing = Event(
            title="Office Hours",
            slug="office-hours",
            start_datetime=datetime(2026, 6, 1, 18, 0),
            origin="studio",
        )
        existing.save()
        connection.close()

        _create_staff_user("staff-ec4@test.com")
        ctx = _auth_context(browser, "staff-ec4@test.com")
        page = ctx.new_page()

        page.goto(
            f"{django_server}/studio/events/new",
            wait_until="domcontentloaded",
        )
        page.fill('input[name="title"]', "Office Hours")
        page.fill('input[name="slug"]', "office-hours")
        page.fill('input[name="event_date"]', "20/06/2026")
        page.fill('input[name="event_time"]', "18:00")
        page.locator('[data-testid="event-create-submit"]').click()

        page.wait_for_load_state("domcontentloaded")
        assert "/studio/events/new" in page.url
        assert page.locator('[data-testid="error-slug"]').is_visible()
        assert Event.objects.filter(slug="office-hours").count() == 1

        ctx.close()


# ---------------------------------------------------------------------------
# Scenario 5: Studio events stay editable; GitHub events stay read-only
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario5OriginEditGate:
    def test_studio_editable_github_readonly(self, django_server, browser):
        from django.db import connection

        from events.models import Event

        _reset_event_state()
        _create_staff_user("staff-ec5@test.com")

        gh = Event(
            title="GitHub-Synced Event",
            slug="gh-event-ec5",
            start_datetime=datetime(2026, 7, 1, 18, 0),
            origin="github",
            source_repo="AI-Shipping-Labs/content",
            source_path="events/gh-event-ec5.yaml",
        )
        gh.save()
        studio = Event(
            title="Studio Event",
            slug="studio-event-ec5",
            start_datetime=datetime(2026, 7, 8, 18, 0),
            origin="studio",
        )
        studio.save()
        connection.close()

        ctx = _auth_context(browser, "staff-ec5@test.com")
        page = ctx.new_page()

        # GitHub-origin event: title input is disabled.
        page.goto(
            f"{django_server}/studio/events/{gh.pk}/edit",
            wait_until="domcontentloaded",
        )
        assert page.locator('input[name="title"]').first.is_disabled()
        assert page.locator('input[name="slug"]').first.is_disabled()
        assert page.locator(
            'textarea[name="description"]'
        ).first.is_disabled()
        assert page.locator(
            'input[name="event_date"]'
        ).first.is_disabled()
        assert page.locator(
            'input[name="event_time"]'
        ).first.is_disabled()
        assert page.locator('input[name="location"]').first.is_disabled()
        assert page.locator(
            'select[name="required_level"]'
        ).first.is_disabled()
        assert page.locator('input[name="tags"]').first.is_disabled()

        # Studio-origin event: same inputs are editable.
        page.goto(
            f"{django_server}/studio/events/{studio.pk}/edit",
            wait_until="domcontentloaded",
        )
        assert not page.locator('input[name="title"]').first.is_disabled()
        assert not page.locator('input[name="slug"]').first.is_disabled()
        assert not page.locator(
            'textarea[name="description"]'
        ).first.is_disabled()
        assert not page.locator(
            'input[name="event_date"]'
        ).first.is_disabled()
        assert not page.locator(
            'input[name="event_time"]'
        ).first.is_disabled()
        assert not page.locator('input[name="location"]').first.is_disabled()
        assert not page.locator(
            'select[name="required_level"]'
        ).first.is_disabled()
        assert not page.locator('input[name="tags"]').first.is_disabled()

        # Edit the title and save; reload confirms the new title persisted.
        page.fill('input[name="title"]', "Studio Event Renamed")
        page.locator("button:has-text('Save Changes')").first.click()
        page.wait_for_url(
            re.compile(rf".*/studio/events/{studio.pk}/edit$"),
        )

        studio.refresh_from_db()
        assert studio.title == "Studio Event Renamed"

        # The list page shows the new title.
        page.goto(
            f"{django_server}/studio/events/",
            wait_until="domcontentloaded",
        )
        assert page.get_by_text("Studio Event Renamed").first.is_visible()

        ctx.close()
