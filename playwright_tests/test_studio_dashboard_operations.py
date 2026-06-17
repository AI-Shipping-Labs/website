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

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only


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


def _create_event(**overrides):
    from events.models import Event

    defaults = {
        "title": "Studio Zoom Session",
        "slug": "studio-zoom-session",
        "status": "upcoming",
        "platform": "zoom",
        "start_datetime": timezone.now() + datetime.timedelta(days=2),
    }
    defaults.update(overrides)
    event = Event.objects.create(**defaults)
    connection.close()
    return event


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
@pytest.mark.core
def test_studio_dashboard_warns_for_missing_zoom_join_url(django_server, browser):
    _reset_dashboard_data()
    _create_staff_user("studio-dashboard-zoom-missing@test.com")
    _create_event(slug="studio-zoom-missing", zoom_join_url="")

    context = _auth_context(browser, "studio-dashboard-zoom-missing@test.com")
    page = context.new_page()

    page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

    attention = page.locator('[data-testid="studio-dashboard-attention"]')
    item = attention.locator(
        '[data-testid="studio-dashboard-attention-item"]',
    ).filter(has_text="Missing Zoom links")
    assert item.count() == 1
    assert item.get_by_text("1", exact=True).is_visible()
    assert item.get_by_text("Add or check missing Zoom join links").is_visible()

    item.click()
    assert page.url.startswith(f"{django_server}/studio/events/")

    context.close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.core
def test_studio_dashboard_no_zoom_warning_when_zoom_event_has_link(
    django_server,
    browser,
):
    _reset_dashboard_data()
    _create_staff_user("studio-dashboard-zoom-linked@test.com")
    _create_event(
        slug="studio-zoom-linked",
        zoom_join_url="https://zoom.us/j/123456",
    )

    context = _auth_context(browser, "studio-dashboard-zoom-linked@test.com")
    page = context.new_page()

    page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

    attention_text = page.locator(
        '[data-testid="studio-dashboard-attention"]',
    ).inner_text()
    assert "Missing Zoom links" not in attention_text
    assert "Check Zoom links" not in attention_text

    events_card_text = page.locator(
        'section[aria-labelledby="summary-heading"] .bg-card',
    ).filter(has_text="Events").first.inner_text()
    events_card_text = "\n".join(
        line.strip() for line in events_card_text.splitlines() if line.strip()
    )
    assert events_card_text == "Events\n1\n1 total"

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
