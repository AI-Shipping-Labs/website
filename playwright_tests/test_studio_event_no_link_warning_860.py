"""Playwright E2E tests for issue #860.

Warn (but do not block) before saving a Studio event that has no meeting
link: no Zoom meeting created, no custom URL, and no external host. The
guard is a ``window.confirm`` wired to the ``#event-edit-form`` submit, so
it covers the inline "Create event" / "Save Changes" button and the sticky
action-bar Save button. Confirming always lets the save through.

Usage:
    uv run pytest playwright_tests/test_studio_event_no_link_warning_860.py -v
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

# Local-only: DB seeding + session-cookie injection fixtures.
pytestmark = pytest.mark.local_only

CONFIRM_TEXT = (
    "You are not creating a Zoom event or setting any other URL — "
    "are you sure you want to save now?"
)


def _reset_event_state():
    from django.db import connection

    from events.models import Event, EventRegistration, EventSeries

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    connection.close()


def _future_date():
    return (datetime.now() + timedelta(days=30)).strftime("%d/%m/%Y")


@pytest.mark.django_db(transaction=True)
class TestCreateWarning:
    def test_zoom_no_meeting_warns_and_cancel_keeps_form(
        self, django_server, browser
    ):
        from django.db import connection

        from events.models import Event

        _reset_event_state()
        _create_staff_user("staff-860a@test.com")
        ctx = _auth_context(browser, "staff-860a@test.com")
        page = ctx.new_page()

        page.goto(
            f"{django_server}/studio/events/new",
            wait_until="domcontentloaded",
        )
        page.fill('input[name="title"]', "Zoomless Office Hours")
        page.fill('input[name="event_date"]', _future_date())
        page.fill('input[name="event_time"]', "18:00")
        # Platform stays Zoom, no external host, no custom URL.

        # Capture and DISMISS the confirm dialog.
        dialog_messages = []

        def _on_dialog(dialog):
            dialog_messages.append(dialog.message)
            dialog.dismiss()

        page.on("dialog", _on_dialog)
        page.locator('[data-testid="event-create-submit"]').click()

        # The confirm dialog fired with the exact message.
        assert dialog_messages == [CONFIRM_TEXT]
        # Cancelling kept us on the create form with the title preserved and
        # nothing saved.
        page.wait_for_load_state("domcontentloaded")
        assert "/studio/events/new" in page.url
        assert (
            page.locator('input[name="title"]').input_value()
            == "Zoomless Office Hours"
        )
        assert Event.objects.count() == 0
        connection.close()
        ctx.close()

    @pytest.mark.core
    def test_zoom_no_meeting_confirm_saves(self, django_server, browser):
        from django.db import connection

        from events.models import Event

        _reset_event_state()
        _create_staff_user("staff-860b@test.com")
        ctx = _auth_context(browser, "staff-860b@test.com")
        page = ctx.new_page()

        page.goto(
            f"{django_server}/studio/events/new",
            wait_until="domcontentloaded",
        )
        page.fill('input[name="title"]', "Confirmed Office Hours")
        page.fill('input[name="event_date"]', _future_date())
        page.fill('input[name="event_time"]', "18:00")

        dialog_messages = []

        def _on_dialog(dialog):
            dialog_messages.append(dialog.message)
            dialog.accept()

        page.on("dialog", _on_dialog)
        page.locator('[data-testid="event-create-submit"]').click()

        # Confirming lets the save through — we land on the edit page.
        page.wait_for_url(re.compile(r".*/studio/events/\d+/edit$"))
        assert dialog_messages == [CONFIRM_TEXT]
        assert Event.objects.filter(title="Confirmed Office Hours").count() == 1
        connection.close()
        ctx.close()

    def test_custom_url_saves_without_warning(self, django_server, browser):
        from django.db import connection

        from events.models import Event

        _reset_event_state()
        _create_staff_user("staff-860c@test.com")
        ctx = _auth_context(browser, "staff-860c@test.com")
        page = ctx.new_page()

        page.goto(
            f"{django_server}/studio/events/new",
            wait_until="domcontentloaded",
        )
        page.fill('input[name="title"]', "Custom URL Event")
        page.fill('input[name="event_date"]', _future_date())
        page.fill('input[name="event_time"]', "18:00")
        page.select_option('select[name="platform"]', "custom")
        page.fill('input[name="custom_url"]', "https://youtube.com/live/abc")

        dialog_fired = []
        page.on("dialog", lambda d: (dialog_fired.append(d.message), d.accept()))

        page.locator('[data-testid="event-create-submit"]').click()

        # No dialog — the event is created directly.
        page.wait_for_url(re.compile(r".*/studio/events/\d+/edit$"))
        assert dialog_fired == []
        assert Event.objects.filter(title="Custom URL Event").count() == 1
        connection.close()
        ctx.close()

    def test_external_host_saves_without_warning(self, django_server, browser):
        from django.db import connection

        from events.models import Event

        _reset_event_state()
        _create_staff_user("staff-860d@test.com")
        ctx = _auth_context(browser, "staff-860d@test.com")
        page = ctx.new_page()

        page.goto(
            f"{django_server}/studio/events/new",
            wait_until="domcontentloaded",
        )
        page.fill('input[name="title"]', "Partner Hosted Event")
        page.fill('input[name="event_date"]', _future_date())
        page.fill('input[name="event_time"]', "18:00")
        # Pick the first non-blank external host option.
        options = page.locator('#external-host-input option')
        chosen = None
        for i in range(options.count()):
            value = options.nth(i).get_attribute("value")
            if value:
                chosen = value
                break
        assert chosen, "expected at least one non-blank external host option"
        page.select_option('#external-host-input', chosen)
        # Platform stays Zoom with no Zoom meeting.

        dialog_fired = []
        page.on("dialog", lambda d: (dialog_fired.append(d.message), d.accept()))

        page.locator('[data-testid="event-create-submit"]').click()

        page.wait_for_url(re.compile(r".*/studio/events/\d+/edit$"))
        assert dialog_fired == []
        created = Event.objects.get(title="Partner Hosted Event")
        assert created.external_host == chosen
        connection.close()
        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestEditWarning:
    def _make_linkless_event(self):
        from django.db import connection

        from events.models import Event

        event = Event(
            title="Linkless Edit Event",
            slug="linkless-edit-event-860",
            start_datetime=datetime(2026, 7, 1, 18, 0),
            origin="studio",
            platform="zoom",
        )
        event.save()
        pk = event.pk
        connection.close()
        return pk

    def test_warns_on_every_save(self, django_server, browser):
        from django.db import connection

        from events.models import Event

        _reset_event_state()
        pk = self._make_linkless_event()
        _create_staff_user("staff-860e@test.com")
        ctx = _auth_context(browser, "staff-860e@test.com")
        page = ctx.new_page()

        page.goto(
            f"{django_server}/studio/events/{pk}/edit",
            wait_until="domcontentloaded",
        )

        dialog_messages = []
        page.on(
            "dialog",
            lambda d: (dialog_messages.append(d.message), d.accept()),
        )

        # First save.
        page.fill('input[name="title"]', "Linkless Renamed Once")
        page.locator("button:has-text('Save Changes')").first.click()
        page.wait_for_url(re.compile(rf".*/studio/events/{pk}/edit$"))
        event = Event.objects.get(pk=pk)
        assert event.title == "Linkless Renamed Once"
        connection.close()

        # Second save — the dialog must fire AGAIN (no one-time dismissal).
        page.fill('input[name="title"]', "Linkless Renamed Twice")
        page.locator("button:has-text('Save Changes')").first.click()
        page.wait_for_url(re.compile(rf".*/studio/events/{pk}/edit$"))
        event = Event.objects.get(pk=pk)
        assert event.title == "Linkless Renamed Twice"

        # Two confirm dialogs, both with the exact message.
        assert dialog_messages == [CONFIRM_TEXT, CONFIRM_TEXT]
        connection.close()
        ctx.close()

    def test_event_with_zoom_meeting_saves_silently(
        self, django_server, browser
    ):
        from django.db import connection

        from events.models import Event

        _reset_event_state()
        event = Event(
            title="Has Zoom Event",
            slug="has-zoom-event-860",
            start_datetime=datetime(2026, 7, 1, 18, 0),
            origin="studio",
            platform="zoom",
            zoom_meeting_id="9988776655",
            zoom_join_url="https://zoom.us/j/9988776655",
        )
        event.save()
        pk = event.pk
        connection.close()

        _create_staff_user("staff-860f@test.com")
        ctx = _auth_context(browser, "staff-860f@test.com")
        page = ctx.new_page()

        page.goto(
            f"{django_server}/studio/events/{pk}/edit",
            wait_until="domcontentloaded",
        )
        # The Zoom panel shows the Meeting ID.
        assert page.get_by_text("9988776655").first.is_visible()

        dialog_fired = []
        page.on("dialog", lambda d: (dialog_fired.append(d.message), d.accept()))

        page.fill('input[name="title"]', "Has Zoom Event Renamed")
        page.locator("button:has-text('Save Changes')").first.click()
        page.wait_for_url(re.compile(rf".*/studio/events/{pk}/edit$"))

        assert dialog_fired == []
        event = Event.objects.get(pk=pk)
        assert event.title == "Has Zoom Event Renamed"
        connection.close()
        ctx.close()

    def test_sticky_save_bar_triggers_warning(self, django_server, browser):
        from django.db import connection

        from events.models import Event

        _reset_event_state()
        pk = self._make_linkless_event()
        _create_staff_user("staff-860g@test.com")
        ctx = _auth_context(browser, "staff-860g@test.com")
        page = ctx.new_page()

        page.goto(
            f"{django_server}/studio/events/{pk}/edit",
            wait_until="domcontentloaded",
        )

        dialog_messages = []
        page.on(
            "dialog",
            lambda d: (dialog_messages.append(d.message), d.accept()),
        )

        page.fill('input[name="title"]', "Sticky Save Rename")
        # The sticky action-bar Save button (data-testid sticky-save-action)
        # submits the same form via form="event-edit-form".
        page.locator('[data-testid="sticky-save-action"]').click()
        page.wait_for_url(re.compile(rf".*/studio/events/{pk}/edit$"))

        assert dialog_messages == [CONFIRM_TEXT]
        event = Event.objects.get(pk=pk)
        assert event.title == "Sticky Save Rename"
        connection.close()
        ctx.close()
