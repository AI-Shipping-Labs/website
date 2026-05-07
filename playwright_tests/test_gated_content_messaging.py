"""Playwright coverage for standardized gated content messaging (#402)."""

import os
from pathlib import Path

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

SCREENSHOT_DIR = Path("/tmp/aisl-issue-402")


def _reset_content():
    from content.models import Course, Workshop, WorkshopPage
    from events.models import Event

    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    Course.objects.all().delete()
    connection.close()


def _create_course_fixture():
    from content.models import Course, Module, Unit

    course = Course.objects.create(
        title="Gated Messaging Course",
        slug="gated-messaging-course",
        status="published",
        required_level=20,
        description="Course for gated messaging tests.",
    )
    module = Module.objects.create(
        course=course,
        title="Module One",
        slug="module-one",
        sort_order=1,
    )
    Unit.objects.create(
        module=module,
        title="Lesson With Preview",
        slug="lesson-with-preview",
        sort_order=1,
        body=(
            "# Previewed lesson\n\n"
            "This visible preview explains the lesson value before the gate. "
            "The full lesson continues with implementation details."
        ),
    )
    connection.close()


def _create_workshop_fixture():
    import datetime

    from django.utils import timezone

    from content.models import Workshop, WorkshopPage
    from events.models import Event

    event = Event.objects.create(
        slug="gated-workshop-event",
        title="Gated Messaging Workshop",
        start_datetime=timezone.now(),
        status="completed",
        kind="workshop",
        recording_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        materials=[{"title": "Slides", "url": "https://example.com/slides.pdf"}],
        published=True,
    )
    workshop = Workshop.objects.create(
        slug="gated-workshop",
        title="Gated Messaging Workshop",
        status="published",
        date=datetime.date(2026, 4, 21),
        landing_required_level=0,
        pages_required_level=10,
        recording_required_level=20,
        description="Workshop overview for gated messaging.",
        event=event,
    )
    WorkshopPage.objects.create(
        workshop=workshop,
        slug="intro",
        title="Workshop Tutorial Intro",
        sort_order=1,
        body="# Tutorial body\n\nOnly Basic members can read this page.",
    )
    connection.close()


def _capture_responsive(page, basename):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.screenshot(path=SCREENSHOT_DIR / f"{basename}_desktop.png", full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=SCREENSHOT_DIR / f"{basename}_mobile.png", full_page=True)
    page.set_viewport_size({"width": 1280, "height": 720})


@pytest.mark.django_db(transaction=True)
class TestGatedContentMessaging:
    def test_course_gate_and_sufficient_tier(self, browser, django_server):
        _reset_content()
        _create_course_fixture()
        _create_user("free-gate@test.com", tier_slug="free")
        _create_user("main-gate@test.com", tier_slug="main")

        free_ctx = _auth_context(browser, "free-gate@test.com")
        page = free_ctx.new_page()
        page.goto(
            f"{django_server}/courses/gated-messaging-course/module-one/lesson-with-preview",
            wait_until="domcontentloaded",
        )
        body = page.content()
        assert "Lesson With Preview" in body
        assert "This visible preview explains the lesson value" in body
        # Issue #481: paywall pill reads "Main or above required".
        assert "Main or above required" in body
        assert "Main+ required" not in body
        assert "Current access: Free member" in body
        assert body.count('data-testid="teaser-upgrade-cta"') == 1
        assert page.locator('[data-testid="teaser-upgrade-cta"]').get_attribute("href") == "/pricing"
        _capture_responsive(page, "course_unit_gate")
        free_ctx.close()

        main_ctx = _auth_context(browser, "main-gate@test.com")
        page = main_ctx.new_page()
        page.goto(
            f"{django_server}/courses/gated-messaging-course/module-one/lesson-with-preview",
            wait_until="domcontentloaded",
        )
        body = page.content()
        assert "Tutorial" not in body
        assert 'data-testid="teaser-cta"' not in body
        assert "Previewed lesson" in body
        main_ctx.close()

    def test_workshop_page_and_recording_gates(self, browser, django_server):
        _reset_content()
        _create_workshop_fixture()
        _create_user("free-workshop-gate@test.com", tier_slug="free")
        _create_user("basic-workshop-gate@test.com", tier_slug="basic")
        _create_user("main-workshop-gate@test.com", tier_slug="main")

        free_ctx = _auth_context(browser, "free-workshop-gate@test.com")
        page = free_ctx.new_page()
        page.goto(
            f"{django_server}/workshops/gated-workshop/tutorial/intro",
            wait_until="domcontentloaded",
        )
        body = page.content()
        assert "Workshop Tutorial Intro" in body
        # Issue #481: paywall pill reads "Basic or above required".
        assert "Basic or above required" in body
        assert "Basic+ required" not in body
        assert "Current access: Free member" in body
        assert "recording access" not in body.lower()
        assert body.count('data-testid="page-upgrade-cta"') == 1
        _capture_responsive(page, "workshop_page_gate")
        free_ctx.close()

        basic_ctx = _auth_context(browser, "basic-workshop-gate@test.com")
        page = basic_ctx.new_page()
        page.goto(
            f"{django_server}/workshops/gated-workshop/tutorial/intro",
            wait_until="domcontentloaded",
        )
        body = page.content()
        assert 'data-testid="page-paywall"' not in body
        assert 'data-testid="page-body"' in body

        page.goto(
            f"{django_server}/workshops/gated-workshop/video",
            wait_until="domcontentloaded",
        )
        body = page.content()
        # Issue #481: paywall pill reads "Main or above required".
        assert "Main or above required" in body
        assert "Main+ required" not in body
        assert "Current access: Basic member" in body
        assert body.count('data-testid="video-upgrade-cta"') == 1
        _capture_responsive(page, "workshop_recording_gate")
        basic_ctx.close()

        main_ctx = _auth_context(browser, "main-workshop-gate@test.com")
        page = main_ctx.new_page()
        page.goto(
            f"{django_server}/workshops/gated-workshop/video",
            wait_until="domcontentloaded",
        )
        body = page.content()
        assert 'data-testid="video-paywall"' not in body
        assert 'data-testid="video-player"' in body
        main_ctx.close()
