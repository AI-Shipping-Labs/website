"""Playwright coverage for preserving auth return context (#485)."""

import os
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from content.access import LEVEL_OPEN, LEVEL_REGISTERED
from playwright_tests.conftest import DEFAULT_PASSWORD, create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


def _reset_fixtures():
    from content.models import Course, Module, Unit
    from events.models import Event

    Unit.objects.filter(module__course__slug__startswith="return-ctx").delete()
    Module.objects.filter(course__slug__startswith="return-ctx").delete()
    Course.objects.filter(slug__startswith="return-ctx").delete()
    Event.objects.filter(slug__startswith="return-ctx").delete()
    connection.close()


def _seed_user():
    return create_user(
        "return-context@test.com",
        password=DEFAULT_PASSWORD,
        email_verified=True,
    )


def _seed_event():
    from events.models import Event

    event = Event.objects.create(
        title="Return Context Event",
        slug="return-ctx-event",
        description="Event used to verify post-login return context.",
        start_datetime=timezone.now() + timedelta(days=7),
        status="upcoming",
        required_level=LEVEL_OPEN,
        published=True,
    )
    connection.close()
    return event


def _seed_registered_unit_course(slug="return-ctx-course", buyable=False):
    from content.models import Course, Module, Unit

    course = Course.objects.create(
        title="Return Context Course",
        slug=slug,
        status="published",
        required_level=20 if buyable else LEVEL_OPEN,
        default_unit_required_level=LEVEL_REGISTERED,
        description="Course used to verify post-login return context.",
        individual_price_eur=29 if buyable else None,
        stripe_price_id="price_return_ctx" if buyable else "",
    )
    module = Module.objects.create(
        course=course,
        title="Intro",
        slug="intro",
        sort_order=1,
    )
    unit = Unit.objects.create(
        module=module,
        title="Return Context Lesson",
        slug="lesson",
        sort_order=1,
        body="# Return Context Lesson\n\nThe lesson body is visible after login.",
    )
    connection.close()
    return course, unit


def _login(page, email="return-context@test.com"):
    page.fill("#login-email", email)
    page.fill("#login-password", DEFAULT_PASSWORD)
    page.click("#login-submit")


@pytest.mark.django_db(transaction=True)
class TestAuthReturnContext:
    def test_event_login_returns_to_event_and_registration_cta(
        self, django_server, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _reset_fixtures()
            _seed_user()
            _seed_event()

        page.goto(
            f"{django_server}/accounts/login/?next=/events/return-ctx-event",
            wait_until="domcontentloaded",
        )
        _login(page)

        page.wait_for_url(f"{django_server}/events/return-ctx-event", timeout=10000)
        assert "Return Context Event" in page.content()
        assert "Register for this event" in page.content()

    def test_registered_course_unit_login_returns_to_same_lesson(
        self, django_server, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _reset_fixtures()
            _seed_user()
            _seed_registered_unit_course()

        page.goto(
            f"{django_server}/courses/return-ctx-course/intro/lesson",
            wait_until="domcontentloaded",
        )
        assert "Sign in to read this lesson" in page.content()
        page.click('[data-testid="teaser-upgrade-cta"]')
        _login(page)

        page.wait_for_url(
            f"{django_server}/courses/return-ctx-course/intro/lesson",
            timeout=10000,
        )
        assert "The lesson body is visible after login." in page.content()

    def test_pricing_checkout_login_returns_to_selected_tier_context(
        self, django_server, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _reset_fixtures()
            _seed_user()

        page.goto(f"{django_server}/pricing", wait_until="domcontentloaded")
        page.click('[data-tier="main"]')
        page.wait_for_url(
            f"{django_server}/accounts/login/?next=%2Fpricing%3Ftier%3Dmain%26billing%3Dmonthly",
            timeout=10000,
        )
        _login(page)

        page.wait_for_url(
            f"{django_server}/pricing?tier=main&billing=monthly",
            timeout=10000,
        )
        assert "Choose your level of engagement" in page.content()

    def test_individual_course_purchase_login_returns_to_course(
        self, django_server, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _reset_fixtures()
            _seed_user()
            _seed_registered_unit_course(
                slug="return-ctx-buy-course",
                buyable=True,
            )

        page.goto(
            f"{django_server}/courses/return-ctx-buy-course",
            wait_until="domcontentloaded",
        )
        page.click("#buy-course-btn")
        page.wait_for_url(
            f"{django_server}/accounts/login/?next=%2Fcourses%2Freturn-ctx-buy-course",
            timeout=10000,
        )
        _login(page)

        page.wait_for_url(
            f"{django_server}/courses/return-ctx-buy-course",
            timeout=10000,
        )
        assert "Return Context Course" in page.content()

    def test_malicious_next_login_does_not_redirect_off_site(
        self, django_server, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _reset_fixtures()
            _seed_user()

        page.goto(
            f"{django_server}/accounts/login/?next=https://example.com/phish",
            wait_until="domcontentloaded",
        )
        _login(page)

        page.wait_for_url(f"{django_server}/", timeout=10000)
        assert page.url == f"{django_server}/"
