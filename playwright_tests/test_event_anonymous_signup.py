"""Playwright E2E for issue #672: email-only event signup extensions.

Covers the two user-visible scenarios from the issue body:

  1. Anonymous visitor registers for a free event with just an email,
     gets a confirmation block, the User row exists with a populated
     ``preferred_timezone`` (browser-detected, NOT empty).
  2. Already-registered visitor resubmits the same email and lands on
     the same confirmation — no duplicate row, no red error.

Usage:
    uv run python -m pytest playwright_tests/test_event_anonymous_signup.py -v
"""

import datetime
import os
import uuid

import pytest
from django.utils import timezone

from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


def _new_email(prefix):
    """Generate a unique email per test run so cache rate-limit slots
    and pre-existing User rows from earlier tests do not interfere.
    """
    return f"{prefix}-{uuid.uuid4().hex[:8]}@test.com"


def _clear_events():
    from django.db import connection

    from events.models import Event, EventRegistration

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _clear_user(email):
    from django.db import connection

    from accounts.models import User

    User.objects.filter(email__iexact=email).delete()
    connection.close()


def _clear_rate_limit_cache():
    """Issue #672 added per-IP / per-email cache rate limits to the
    anonymous register view. Both Django tests and Playwright tests
    share the same process's cache; clear between cases so a previous
    test's IP slot does not trip the next one.
    """
    from django.core.cache import cache

    cache.clear()


def _seed_open_event(slug="anon-672-evt", title="Anon 672 Event"):
    from django.db import connection

    from events.models import Event

    event = Event.objects.create(
        slug=slug,
        title=title,
        description="Used by anonymous-signup E2E.",
        start_datetime=timezone.now() + datetime.timedelta(days=7),
        status="upcoming",
        required_level=0,
    )
    connection.close()
    return event


def _seed_existing_anon_registration(email, event):
    """Mirror what `_register_anonymous` does for the new-user branch
    so the resubmit scenario has a known starting state without
    routing through the API.
    """
    from django.db import connection

    from accounts.models import User
    from events.models import EventRegistration

    user = User.objects.create_user(
        email=email,
        email_verified=False,
    )
    EventRegistration.objects.create(event=event, user=user)
    connection.close()
    return user


@pytest.mark.django_db(transaction=True)
class TestAnonymousEventSignup:
    """End-to-end coverage for the email-only event-signup happy path
    and the idempotent resubmit.
    """

    @pytest.mark.core
    def test_anonymous_signup_creates_user_with_browser_timezone(
        self, django_server, page, django_db_blocker
    ):
        email = _new_email("anon-672")
        with django_db_blocker.unblock():
            _clear_events()
            _clear_rate_limit_cache()
            _ensure_tiers()
            event = _seed_open_event(
                slug="anon-672-evt-tz", title="Anon 672 TZ Event",
            )

        # Issue #673: canonical URL is ``/events/<id>/<slug>``.
        event_path = event.get_absolute_url()
        page.goto(
            f"{django_server}{event_path}",
            wait_until="domcontentloaded",
        )

        # The anonymous email-only form is visible. The legacy "Sign
        # in / Create free account" pair must not appear in its place.
        form = page.locator('[data-testid="event-anonymous-email-form"]')
        assert form.count() == 1
        assert page.locator(
            '[data-testid="event-anonymous-cta"]',
        ).count() == 0

        # The hidden timezone input is present and populated by the
        # JS bind step. Its value is whatever the test browser
        # reports — a non-empty IANA string.
        tz_input = page.locator('#event-anon-timezone')
        assert tz_input.count() == 1
        # Wait for the JS bind to populate the field. The script tag
        # is defer-loaded, so a short retry loop is friendlier than a
        # raw assertion on the first DOM read.
        page.wait_for_function(
            "() => {"
            "  const el = document.getElementById('event-anon-timezone');"
            "  return !!(el && el.value);"
            "}",
            timeout=5000,
        )
        browser_tz = tz_input.input_value()
        assert browser_tz != ""

        page.fill('#event-anon-email', email)
        page.click('#event-anon-submit-btn')

        # The JS redirects with ?registered=<email>. Wait for that.
        page.wait_for_url(
            lambda url: (
                event_path in url
                and "registered=" in url
            ),
            timeout=10000,
        )

        confirmation = page.locator(
            '[data-testid="event-anonymous-registered-confirmation"]',
        )
        confirmation.wait_for(state="visible", timeout=5000)
        confirmation_text = confirmation.inner_text()
        assert email in confirmation_text
        # The copy mentions both the calendar invite and the verify link.
        assert "calendar invite" in confirmation_text
        assert "verification link" in confirmation_text

        # DB-level assertions: User row exists, unverified, with a
        # non-empty preferred_timezone that matches the browser's zone.
        with django_db_blocker.unblock():
            from accounts.models import User
            from events.models import EventRegistration

            user = User.objects.get(email=email)
            assert user.email_verified is False
            assert user.preferred_timezone != ""
            assert user.preferred_timezone == browser_tz
            assert EventRegistration.objects.filter(
                event=event, user=user,
            ).count() == 1

    @pytest.mark.core
    def test_resubmit_same_email_lands_on_confirmation_no_duplicate(
        self, django_server, page, django_db_blocker
    ):
        email = _new_email("repeat-672")
        with django_db_blocker.unblock():
            _clear_events()
            _clear_user(email)
            _clear_rate_limit_cache()
            _ensure_tiers()
            event = _seed_open_event(
                slug="anon-672-evt-repeat",
                title="Anon 672 Repeat Event",
            )
            _seed_existing_anon_registration(email, event)

        # Issue #673: canonical URL is ``/events/<id>/<slug>``.
        event_path = event.get_absolute_url()
        # Use a fresh page (no cookies from the first registration).
        page.goto(
            f"{django_server}{event_path}",
            wait_until="domcontentloaded",
        )

        page.fill('#event-anon-email', email)
        page.click('#event-anon-submit-btn')

        page.wait_for_url(
            lambda url: (
                event_path in url
                and "registered=" in url
            ),
            timeout=10000,
        )

        # The confirmation block renders — NOT a red error message.
        confirmation = page.locator(
            '[data-testid="event-anonymous-registered-confirmation"]',
        )
        confirmation.wait_for(state="visible", timeout=5000)
        assert email in confirmation.inner_text()

        error_el = page.locator('[data-testid="event-anonymous-error"]')
        # The error element exists in the DOM but stays hidden on a
        # successful (or idempotent) submit.
        if error_el.count() > 0:
            assert error_el.is_hidden()

        # DB: exactly one User and one EventRegistration row — no
        # duplicate created by the resubmit.
        with django_db_blocker.unblock():
            from accounts.models import User
            from events.models import EventRegistration

            users = User.objects.filter(email__iexact=email)
            assert users.count() == 1
            assert EventRegistration.objects.filter(
                event=event, user=users.first(),
            ).count() == 1
