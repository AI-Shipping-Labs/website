"""Playwright coverage for inline event feedback reveal (issue #1038)."""

import datetime
import os

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.conftest import VIEWPORT
from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_session_for_user as _create_session
from playwright_tests.conftest import create_user as _create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = pytest.mark.local_only


def _clear_event_feedback_data():
    from events.models import Event, EventFeedback, EventRegistration

    EventFeedback.objects.all().delete()
    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_event(*, slug, title, past=True):
    from events.models import Event

    now = timezone.now()
    if past:
        start = now - datetime.timedelta(hours=3)
        end = now - datetime.timedelta(hours=1)
        status = "completed"
    else:
        start = now + datetime.timedelta(hours=1)
        end = now + datetime.timedelta(hours=2)
        status = "upcoming"

    event = Event.objects.create(
        slug=slug,
        title=title,
        description=f"{title} description.",
        start_datetime=start,
        end_datetime=end,
        status=status,
        published=True,
    )
    connection.close()
    return event


def _register(user, event):
    from events.models import EventRegistration

    EventRegistration.objects.create(user=user, event=event)
    connection.close()


def _create_feedback(event, user, *, rating=None, comment="", would_change=""):
    from events.models import EventFeedback

    feedback = EventFeedback.objects.create(
        event=event,
        user=user,
        rating=rating,
        comment=comment,
        would_change=would_change,
    )
    connection.close()
    return feedback


def _feedback_count(event, user):
    from events.models import EventFeedback

    count = EventFeedback.objects.filter(event=event, user=user).count()
    connection.close()
    return count


def _feedback_values(event, user):
    from events.models import EventFeedback

    feedback = EventFeedback.objects.get(event=event, user=user)
    values = (feedback.rating, feedback.comment, feedback.would_change)
    connection.close()
    return values


def _no_js_context(browser, email):
    session_key = _create_session(email)
    context = browser.new_context(
        viewport=VIEWPORT,
        java_script_enabled=False,
    )
    context.add_cookies([
        {
            "name": "sessionid",
            "value": session_key,
            "domain": "127.0.0.1",
            "path": "/",
        },
        {
            "name": "csrftoken",
            "value": "e2e-test-csrf-token-value",
            "domain": "127.0.0.1",
            "path": "/",
        },
    ])
    return context


@pytest.mark.django_db(transaction=True)
class TestEventFeedbackReveal1038:
    @pytest.mark.core
    def test_registered_attendee_reveals_and_submits_feedback(
        self, django_server, browser,
    ):
        _clear_event_feedback_data()
        attendee = _create_user("feedback-1038@test.com", tier_slug="free")
        event = _create_event(
            slug="feedback-reveal-1038",
            title="Feedback Reveal 1038",
        )
        _register(attendee, event)

        context = _auth_context(browser, "feedback-1038@test.com")
        page = context.new_page()
        page.goto(f"{django_server}{event.get_absolute_url()}", wait_until="domcontentloaded")

        section = page.locator('[data-testid="event-feedback-section"]')
        reveal = page.locator('[data-testid="event-feedback-reveal"]')
        form = page.locator('[data-testid="event-feedback-form"]')
        expect(section).to_be_visible()
        expect(reveal).to_have_text("Submit feedback")
        expect(reveal).to_have_attribute("aria-expanded", "false")
        expect(form).not_to_be_visible()

        reveal.click()
        expect(reveal).to_have_attribute("aria-expanded", "true")
        expect(form).to_be_visible()
        page.locator('[data-testid="event-feedback-rating-5"]').check()
        page.locator('[data-testid="event-feedback-comment"]').fill("Great session")
        page.locator('[data-testid="event-feedback-submit"]').click()

        page.wait_for_url("**?feedback=thanks")
        expect(page.locator('[data-testid="event-feedback-thanks"]')).to_be_visible()
        expect(page.locator('[data-testid="event-feedback-reveal"]')).to_have_text(
            "Update feedback"
        )
        expect(page.locator('[data-testid="event-feedback-reveal"]')).to_have_attribute(
            "aria-expanded", "false"
        )
        expect(page.locator('[data-testid="event-feedback-form"]')).not_to_be_visible()
        assert _feedback_count(event, attendee) == 1
        assert _feedback_values(event, attendee) == (5, "Great session", "")
        context.close()

    @pytest.mark.core
    def test_existing_feedback_update_state_is_collapsed_and_prefilled(
        self, django_server, browser,
    ):
        _clear_event_feedback_data()
        attendee = _create_user("feedback-update-1038@test.com", tier_slug="free")
        event = _create_event(
            slug="feedback-update-1038",
            title="Feedback Update 1038",
        )
        _register(attendee, event)
        _create_feedback(
            event,
            attendee,
            rating=3,
            comment="Good",
            would_change="More examples",
        )

        context = _auth_context(browser, "feedback-update-1038@test.com")
        page = context.new_page()
        page.goto(f"{django_server}{event.get_absolute_url()}", wait_until="domcontentloaded")

        reveal = page.locator('[data-testid="event-feedback-reveal"]')
        form = page.locator('[data-testid="event-feedback-form"]')
        expect(reveal).to_have_text("Update feedback")
        expect(reveal).to_have_attribute("aria-expanded", "false")
        expect(form).not_to_be_visible()

        reveal.click()
        expect(form).to_be_visible()
        expect(page.locator('[data-testid="event-feedback-rating-3"]')).to_be_checked()
        expect(page.locator('[data-testid="event-feedback-comment"]')).to_have_value("Good")
        expect(page.locator('[data-testid="event-feedback-would-change"]')).to_have_value(
            "More examples"
        )

        page.locator('[data-testid="event-feedback-rating-5"]').check()
        page.locator('[data-testid="event-feedback-comment"]').fill("Excellent")
        page.locator('[data-testid="event-feedback-would-change"]').fill("More Q&A")
        page.locator('[data-testid="event-feedback-submit"]').click()

        page.wait_for_url("**?feedback=thanks")
        expect(page.locator('[data-testid="event-feedback-thanks"]')).to_be_visible()
        page.locator('[data-testid="event-feedback-reveal"]').click()
        expect(page.locator('[data-testid="event-feedback-rating-5"]')).to_be_checked()
        expect(page.locator('[data-testid="event-feedback-comment"]')).to_have_value(
            "Excellent"
        )
        expect(page.locator('[data-testid="event-feedback-would-change"]')).to_have_value(
            "More Q&A"
        )
        assert _feedback_count(event, attendee) == 1
        assert _feedback_values(event, attendee) == (5, "Excellent", "More Q&A")
        context.close()

    @pytest.mark.core
    def test_access_restrictions_keep_feedback_controls_hidden(
        self, django_server, browser, page,
    ):
        _clear_event_feedback_data()
        rater = _create_user("feedback-rater-1038@test.com", tier_slug="free")
        _create_user("feedback-non-attendee-1038@test.com", tier_slug="free")
        past = _create_event(
            slug="feedback-access-1038",
            title="Feedback Access 1038",
        )
        _create_feedback(past, rater, rating=5)

        page.goto(f"{django_server}{past.get_absolute_url()}", wait_until="domcontentloaded")
        expect(page.locator('[data-testid="event-feedback-aggregate"]')).to_be_visible()
        expect(page.locator('[data-testid="event-feedback-reveal"]')).to_have_count(0)
        expect(page.locator('[data-testid="event-feedback-form"]')).to_have_count(0)

        context = _auth_context(browser, "feedback-non-attendee-1038@test.com")
        authed_page = context.new_page()
        authed_page.goto(
            f"{django_server}{past.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        expect(authed_page.locator('[data-testid="event-feedback-aggregate"]')).to_be_visible()
        expect(authed_page.locator('[data-testid="event-feedback-reveal"]')).to_have_count(0)
        expect(authed_page.locator('[data-testid="event-feedback-form"]')).to_have_count(0)
        context.close()

        attendee = _create_user("feedback-upcoming-1038@test.com", tier_slug="free")
        upcoming = _create_event(
            slug="feedback-upcoming-1038",
            title="Feedback Upcoming 1038",
            past=False,
        )
        _register(attendee, upcoming)
        upcoming_context = _auth_context(browser, "feedback-upcoming-1038@test.com")
        upcoming_page = upcoming_context.new_page()
        upcoming_page.goto(
            f"{django_server}{upcoming.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        expect(upcoming_page.locator('[data-testid="event-feedback-section"]')).to_have_count(0)
        expect(upcoming_page.get_by_text("Submit feedback")).to_have_count(0)
        upcoming_context.close()

    @pytest.mark.core
    def test_keyboard_activation_reveals_form_and_moves_focus(
        self, django_server, browser,
    ):
        _clear_event_feedback_data()
        attendee = _create_user("feedback-keyboard-1038@test.com", tier_slug="free")
        event = _create_event(
            slug="feedback-keyboard-1038",
            title="Feedback Keyboard 1038",
        )
        _register(attendee, event)

        context = _auth_context(browser, "feedback-keyboard-1038@test.com")
        page = context.new_page()
        page.goto(f"{django_server}{event.get_absolute_url()}", wait_until="domcontentloaded")

        reveal = page.locator('[data-testid="event-feedback-reveal"]')
        reveal.focus()
        page.keyboard.press("Enter")
        expect(page.locator('[data-testid="event-feedback-form"]')).to_be_visible()
        expect(reveal).to_have_attribute("aria-expanded", "true")
        page.wait_for_function(
            "() => document.activeElement?.getAttribute('data-testid') === 'event-feedback-rating-1'"
        )
        context.close()

    @pytest.mark.core
    def test_no_javascript_attendee_can_open_and_submit_inline_form(
        self, django_server, browser,
    ):
        _clear_event_feedback_data()
        attendee = _create_user("feedback-no-js-1038@test.com", tier_slug="free")
        event = _create_event(
            slug="feedback-no-js-1038",
            title="Feedback No JS 1038",
        )
        _register(attendee, event)

        context = _no_js_context(browser, "feedback-no-js-1038@test.com")
        page = context.new_page()
        page.goto(f"{django_server}{event.get_absolute_url()}", wait_until="domcontentloaded")

        expect(page.locator('[data-testid="event-feedback-form"]')).not_to_be_visible()
        reveal = page.locator('[data-testid="event-feedback-reveal"]')
        reveal.focus()
        page.keyboard.press("Enter")
        expect(page.locator('[data-testid="event-feedback-form"]')).to_be_visible()
        rating = page.locator('[data-testid="event-feedback-rating-4"]')
        rating.focus()
        page.keyboard.press("Space")
        expect(rating).to_be_checked()
        page.locator('[data-testid="event-feedback-comment"]').focus()
        page.keyboard.type("Works without JS")
        page.locator('[data-testid="event-feedback-submit"]').focus()
        page.keyboard.press("Enter")

        page.wait_for_url("**?feedback=thanks")
        expect(page.locator('[data-testid="event-feedback-thanks"]')).to_be_visible()
        assert _feedback_values(event, attendee) == (4, "Works without JS", "")
        context.close()
