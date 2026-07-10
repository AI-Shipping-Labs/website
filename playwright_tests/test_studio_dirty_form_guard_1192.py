"""Playwright coverage for Studio dirty-form guard (#1192)."""

import os
import re
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only


def _reset_content():
    from content.models import Course
    from events.models import Event, EventRegistration
    from integrations.models import Redirect

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    Course.objects.all().delete()
    Redirect.objects.all().delete()
    connection.close()


def _create_event(**kwargs):
    from events.models import Event

    start = timezone.now() + timedelta(days=14)
    defaults = {
        "title": "Dirty Guard Event",
        "slug": "dirty-guard-event",
        "start_datetime": start,
        "end_datetime": start + timedelta(hours=1),
        "status": "upcoming",
        "origin": "studio",
    }
    defaults.update(kwargs)
    event = Event.objects.create(**defaults)
    connection.close()
    return event


def _create_course(**kwargs):
    from content.models import Course

    defaults = {
        "title": "Dirty Guard Course",
        "slug": "dirty-guard-course",
        "description": "Course description",
        "status": "draft",
        "required_level": 0,
    }
    defaults.update(kwargs)
    course = Course.objects.create(**defaults)
    connection.close()
    return course


def _create_redirect(**kwargs):
    from integrations.models import Redirect

    defaults = {
        "source_path": "/old-dirty",
        "target_path": "/new-dirty",
        "redirect_type": "301",
        "is_active": True,
    }
    defaults.update(kwargs)
    redirect = Redirect.objects.create(**defaults)
    connection.close()
    return redirect


def _staff_page(django_server, browser):
    _ensure_tiers()
    _create_staff_user("studio-dirty-guard@test.com")
    context = _auth_context(browser, "studio-dirty-guard@test.com")
    page = context.new_page()
    return context, page


def _accept_missing_meeting_warning(page):
    page.once(
        "dialog",
        lambda dialog: dialog.accept()
        if "not creating a Zoom event" in dialog.message
        else dialog.dismiss(),
    )


def _show_courses_nav(page):
    link = page.locator('a[href="/studio/courses/"]').first
    if not link.is_visible():
        page.locator('[aria-controls="studio-section-content"]').click()
    return link


@pytest.mark.django_db(transaction=True)
def test_dirty_event_sidebar_navigation_can_be_cancelled(django_server, browser):
    _reset_content()
    event = _create_event()
    context, page = _staff_page(django_server, browser)

    page.goto(f"{django_server}/studio/events/{event.pk}/edit", wait_until="domcontentloaded")
    page.locator('input[name="title"]').fill("Unsaved event title")
    assert page.locator('[data-testid="sticky-save-status"]').inner_text() == "Unsaved changes"

    page.once("dialog", lambda dialog: dialog.dismiss())
    _show_courses_nav(page).click()
    page.wait_for_timeout(200)

    assert re.search(rf"/studio/events/{event.pk}/edit$", page.url)
    assert page.locator('input[name="title"]').input_value() == "Unsaved event title"
    context.close()


@pytest.mark.django_db(transaction=True)
def test_dirty_event_sidebar_navigation_can_be_confirmed(django_server, browser):
    _reset_content()
    event = _create_event()
    context, page = _staff_page(django_server, browser)

    page.goto(f"{django_server}/studio/events/{event.pk}/edit", wait_until="domcontentloaded")
    page.locator('input[name="title"]').fill("Abandoned event title")

    page.once("dialog", lambda dialog: dialog.accept())
    _show_courses_nav(page).click()
    page.wait_for_url(re.compile(r".*/studio/courses/?$"))

    page.goto(f"{django_server}/studio/events/{event.pk}/edit", wait_until="domcontentloaded")
    assert page.locator('input[name="title"]').input_value() == "Dirty Guard Event"
    context.close()


@pytest.mark.django_db(transaction=True)
def test_saved_event_can_navigate_without_dirty_prompt(django_server, browser):
    _reset_content()
    event = _create_event()
    context, page = _staff_page(django_server, browser)

    page.goto(f"{django_server}/studio/events/{event.pk}/edit", wait_until="domcontentloaded")
    page.locator('input[name="location"]').fill("Saved Room")
    _accept_missing_meeting_warning(page)
    page.locator('[data-testid="sticky-save-action"]').click()
    page.wait_for_url(re.compile(rf".*/studio/events/{event.pk}/edit$"))
    assert page.locator('[data-testid="sticky-save-status"]').inner_text() == "No unsaved changes"

    dialogs = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
    _show_courses_nav(page).click()
    page.wait_for_url(re.compile(r".*/studio/courses/?$"))
    assert dialogs == []

    event.refresh_from_db()
    assert event.location == "Saved Room"
    context.close()


@pytest.mark.django_db(transaction=True)
def test_event_validation_error_status_survives_guard_initialization(
    django_server, browser,
):
    _reset_content()
    event = _create_event()
    context, page = _staff_page(django_server, browser)

    page.goto(f"{django_server}/studio/events/{event.pk}/edit", wait_until="domcontentloaded")
    page.locator('select[name="timezone"]').evaluate(
        """select => {
          const option = document.createElement('option');
          option.value = 'Not/AZone';
          option.textContent = 'Not/AZone';
          select.appendChild(option);
          select.value = 'Not/AZone';
          select.dispatchEvent(new Event('change', { bubbles: true }));
        }"""
    )
    _accept_missing_meeting_warning(page)
    page.locator('[data-testid="sticky-save-action"]').click()
    page.locator('[data-testid="error-timezone"]').wait_for()

    status = page.locator('[data-testid="sticky-save-status"]')
    assert status.inner_text() == "Save failed - fix errors"
    assert status.get_attribute("data-studio-dirty-status-state") == "error"

    page.locator('input[name="title"]').fill("Fixed event title")
    assert status.inner_text() == "Unsaved changes"
    assert status.get_attribute("data-studio-dirty-status-state") == "dirty"
    context.close()


@pytest.mark.django_db(transaction=True)
def test_dirty_course_status_and_save_clear_guard(django_server, browser):
    _reset_content()
    course = _create_course()
    context, page = _staff_page(django_server, browser)

    page.goto(f"{django_server}/studio/courses/{course.pk}/edit", wait_until="domcontentloaded")
    assert page.locator('[data-testid="sticky-save-status"]').inner_text() == "No unsaved changes"
    page.locator('textarea[name="description"]').fill("Updated course description")
    assert page.locator('[data-testid="sticky-save-status"]').inner_text() == "Unsaved changes"

    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator('[data-testid="sticky-cancel-action"]').click()
    page.wait_for_timeout(200)
    assert re.search(rf"/studio/courses/{course.pk}/edit$", page.url)
    assert page.locator('textarea[name="description"]').input_value() == "Updated course description"

    page.locator('[data-testid="sticky-save-action"]').click()
    page.wait_for_url(re.compile(rf".*/studio/courses/{course.pk}/edit$"))
    assert page.locator('[data-testid="sticky-save-status"]').inner_text() == "No unsaved changes"
    course.refresh_from_db()
    assert course.description == "Updated course description"
    context.close()


@pytest.mark.django_db(transaction=True)
def test_shared_guard_protects_redirect_editor_cancel_link(django_server, browser):
    _reset_content()
    redirect = _create_redirect()
    context, page = _staff_page(django_server, browser)

    page.goto(f"{django_server}/studio/redirects/{redirect.pk}/edit", wait_until="domcontentloaded")
    page.locator('input[name="target_path"]').fill("/changed-target")
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator('[data-testid="sticky-cancel-action"]').click()
    page.wait_for_timeout(200)

    assert re.search(rf"/studio/redirects/{redirect.pk}/edit$", page.url)
    assert page.locator('input[name="target_path"]').input_value() == "/changed-target"
    context.close()


@pytest.mark.django_db(transaction=True)
def test_synced_event_operational_fields_are_guarded(django_server, browser):
    _reset_content()
    event = _create_event(
        title="Synced Dirty Event",
        slug="synced-dirty-event",
        origin="github",
        source_repo="AI-Shipping-Labs/content",
        source_path="events/synced-dirty-event.yaml",
    )
    context, page = _staff_page(django_server, browser)

    page.goto(f"{django_server}/studio/events/{event.pk}/edit", wait_until="domcontentloaded")
    assert page.locator('input[name="title"]').is_disabled()
    assert page.locator('[data-testid="sticky-save-status"]').inner_text() == "No unsaved changes"
    page.locator('input[name="host_email"]').fill("host@example.com")
    assert page.locator('[data-testid="sticky-save-status"]').inner_text() == "Unsaved changes"

    page.once("dialog", lambda dialog: dialog.dismiss())
    _show_courses_nav(page).click()
    page.wait_for_timeout(200)
    assert re.search(rf"/studio/events/{event.pk}/edit$", page.url)
    assert page.locator('input[name="host_email"]').input_value() == "host@example.com"
    context.close()


@pytest.mark.django_db(transaction=True)
def test_dirty_editor_beforeunload_is_cancelable(django_server, browser):
    _reset_content()
    event = _create_event()
    context, page = _staff_page(django_server, browser)

    page.goto(f"{django_server}/studio/events/{event.pk}/edit", wait_until="domcontentloaded")
    clean_canceled = page.evaluate(
        "() => { const e = new Event('beforeunload', { cancelable: true });"
        " return !window.dispatchEvent(e); }"
    )
    assert clean_canceled is False

    page.locator('input[name="title"]').fill("Reload protected title")
    dirty_canceled = page.evaluate(
        "() => { const e = new Event('beforeunload', { cancelable: true });"
        " return !window.dispatchEvent(e); }"
    )
    assert dirty_canceled is True
    assert page.locator('input[name="title"]').input_value() == "Reload protected title"
    context.close()
