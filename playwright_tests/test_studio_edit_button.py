"""Playwright E2E for the staff "Edit in Studio" button (issue #667).

Three scenarios on a single representative surface — the event detail
page:

1. A staff user sees the floating button and clicking it lands on the
   Studio editor for that exact event (URL matches the event's
   ``get_studio_edit_url()``).
2. An anonymous visitor never sees the button and the page source has
   no ``data-testid="studio-edit-button"`` and no ``/studio/`` link.
3. A free authenticated user has the same anonymous-visibility contract.

Per-model server-side coverage for the other in-scope surfaces (article,
project, course, unit, workshop, event-series, sprint) lives in Django
view tests; Playwright covers the end-to-end click-through on the one
representative event surface.
"""

import datetime
import os

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


def _create_event(*, slug, title):
    from events.models import Event

    event = Event.objects.create(
        slug=slug,
        title=title,
        description="An event to demonstrate the staff edit affordance.",
        start_datetime=timezone.now() + datetime.timedelta(days=7),
        status="upcoming",
    )
    connection.close()
    return event


@pytest.mark.django_db(transaction=True)
class TestStudioEditButtonOnEventDetail:
    """Issue #667: staff jump from a public event page into the Studio editor."""

    @pytest.mark.core
    def test_staff_sees_button_and_can_jump_to_studio(
        self, django_server, browser,
    ):
        _clear_events()
        _ensure_tiers()
        _create_staff_user(email="staff@test.com")
        event = _create_event(
            slug="event-with-typo", title="Event With Typo",
        )

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/events/event-with-typo",
            wait_until="domcontentloaded",
        )

        button = page.locator('[data-testid="studio-edit-button"]')
        assert button.count() == 1, "Staff should see exactly one button"
        assert button.is_visible()
        href = button.get_attribute("href")
        assert href == f"/studio/events/{event.pk}/edit"
        assert "Edit in Studio" in button.inner_text()

        # Clicking the button navigates to the Studio editor for that
        # exact event.
        button.click()
        page.wait_for_url(f"**/studio/events/{event.pk}/edit", timeout=10000)
        assert page.url.endswith(f"/studio/events/{event.pk}/edit")

        # The Studio edit form is rendered for that exact event — the
        # title input is pre-filled with the event title.
        title_input = page.locator('input[name="title"]')
        assert title_input.count() == 1
        assert title_input.input_value() == "Event With Typo"

    @pytest.mark.core
    def test_anonymous_does_not_see_button(self, django_server, page):
        _clear_events()
        _ensure_tiers()
        _create_event(
            slug="event-with-typo", title="Event With Typo",
        )

        response = page.goto(
            f"{django_server}/events/event-with-typo",
            wait_until="domcontentloaded",
        )
        assert response.status == 200
        assert (
            page.locator('[data-testid="studio-edit-button"]').count() == 0
        )

        # The Studio URL is not leaked anywhere in the HTML source for
        # anonymous visitors (CSS-hide is rejected by the issue spec).
        html = page.content()
        assert 'data-testid="studio-edit-button"' not in html
        assert "/studio/" not in html

    @pytest.mark.core
    def test_free_user_does_not_see_button(self, django_server, browser):
        _clear_events()
        _ensure_tiers()
        _create_user("free@test.com", tier_slug="free")
        _create_event(
            slug="event-with-typo", title="Event With Typo",
        )

        context = _auth_context(browser, "free@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/events/event-with-typo",
            wait_until="domcontentloaded",
        )

        assert (
            page.locator('[data-testid="studio-edit-button"]').count() == 0
        )
        html = page.content()
        assert 'data-testid="studio-edit-button"' not in html
        assert "/studio/" not in html
