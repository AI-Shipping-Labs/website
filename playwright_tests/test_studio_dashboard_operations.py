"""Playwright coverage for the operational Studio dashboard (#488)."""

import datetime
import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection
from django.utils import timezone


def _reset_dashboard_data():
    from content.models import Article, Course, Project
    from events.models import Event

    Project.objects.all().delete()
    Article.objects.all().delete()
    Course.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _seed_dashboard_data():
    from content.models import Article, Project
    from events.models import Event

    Project.objects.create(
        title="Project Needs Review",
        slug="project-needs-review",
        date=datetime.date.today(),
        status="pending_review",
        published=False,
        author="Avery",
    )
    Article.objects.create(
        title="Recently Changed Article",
        slug="recently-changed-article",
        date=datetime.date.today(),
        published=True,
    )
    Event.objects.create(
        title="Upcoming Studio Session",
        slug="upcoming-studio-session",
        status="upcoming",
        start_datetime=timezone.now() + datetime.timedelta(days=2),
    )
    connection.close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.core
def test_studio_dashboard_operational_sections_and_links(django_server, browser):
    _reset_dashboard_data()
    _create_staff_user("studio-dashboard-admin@test.com")
    _create_user("new-member-dashboard@test.com", email_verified=True)
    _seed_dashboard_data()

    context = _auth_context(browser, "studio-dashboard-admin@test.com")
    page = context.new_page()

    page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

    assert page.get_by_role("heading", name="Attention").is_visible()
    assert page.get_by_role("heading", name="Recent activity").is_visible()
    assert page.get_by_role("heading", name="Quick actions").is_visible()
    assert page.get_by_text("Recently Changed Article").is_visible()
    assert page.get_by_text("new-member-dashboard@test.com").is_visible()

    quick_actions = page.locator('[data-testid="studio-dashboard-quick-action"]')
    assert quick_actions.count() >= 6
    for index in range(quick_actions.count()):
        href = quick_actions.nth(index).get_attribute("href")
        assert href
        response = page.goto(f"{django_server}{href}", wait_until="domcontentloaded")
        assert response is not None
        assert response.status != 404
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

    context.close()


@pytest.mark.django_db(transaction=True)
def test_studio_dashboard_mobile_no_horizontal_overflow(django_server, browser):
    _reset_dashboard_data()
    _create_staff_user("studio-dashboard-mobile@test.com")
    _create_user("mobile-member-dashboard@test.com", email_verified=True)
    _seed_dashboard_data()

    context = _auth_context(browser, "studio-dashboard-mobile@test.com")
    page = context.new_page()
    page.set_viewport_size({"width": 390, "height": 844})

    page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

    assert page.locator('[data-testid="dashboard-worker-panel"]').is_visible()
    assert page.locator('[data-testid="studio-dashboard-attention"]').is_visible()
    assert page.locator('[data-testid="studio-dashboard-quick-actions"]').is_visible()
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - window.innerWidth"
    )
    assert overflow <= 1

    first_action = page.locator('[data-testid="studio-dashboard-quick-action"]').first
    box = first_action.bounding_box()
    assert box is not None
    assert box["height"] >= 44

    context.close()
