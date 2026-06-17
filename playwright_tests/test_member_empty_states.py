"""Playwright coverage for shared member-facing empty states (#1008)."""

import datetime
import os

import pytest

from playwright_tests.conftest import auth_context, create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.db import connection  # noqa: E402

pytestmark = pytest.mark.local_only


def _clear_public_catalog_data():
    from content.models import Article, Course, Workshop, WorkshopPage

    Article.objects.all().delete()
    Course.objects.all().delete()
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    connection.close()


def _seed_public_catalog_items():
    from content.models import Article, Course, Workshop

    Article.objects.create(
        title="Agents Article",
        slug="agents-article",
        description="About agents",
        date=datetime.date.today(),
        tags=["agents"],
        published=True,
    )
    Course.objects.create(
        title="Agents Course",
        slug="agents-course",
        status="published",
        tags=["agents"],
    )
    Workshop.objects.create(
        title="Agents Workshop",
        slug="agents-workshop",
        status="published",
        date=datetime.date.today(),
        pages_required_level=0,
        recording_required_level=0,
        tags=["agents"],
    )
    connection.close()


@pytest.mark.django_db(transaction=True)
def test_public_catalog_filter_empty_states_clear_to_unfiltered_lists(
    django_server, page
):
    _clear_public_catalog_data()
    _seed_public_catalog_items()

    checks = [
        (
            "/blog?tag=missing-topic",
            "No articles found with the selected tags.",
            "View all articles",
            "/blog",
            "Agents Article",
        ),
        (
            "/courses?tag=missing-topic",
            "No courses found with the selected tags.",
            "View all courses",
            "/courses",
            "Agents Course",
        ),
        (
            "/workshops?tag=missing-topic",
            "No workshops found with the selected tags.",
            "View all workshops",
            "/workshops",
            "Agents Workshop",
        ),
    ]

    for path, message, cta, clear_path, visible_title in checks:
        page.goto(f"{django_server}{path}", wait_until="domcontentloaded")

        empty_state = page.locator('[data-testid="member-empty-state"]')
        assert empty_state.count() >= 1
        assert empty_state.first.get_attribute("data-empty-kind") == "filter"
        assert message in page.content()

        page.locator(f'a:has-text("{cta}")').first.click()
        page.wait_for_load_state("domcontentloaded")

        assert page.url.rstrip("/") == f"{django_server}{clear_path}"
        assert "missing-topic" not in page.url
        assert visible_title in page.content()


@pytest.mark.django_db(transaction=True)
def test_notifications_empty_state_uses_member_component(django_server, browser):
    from notifications.models import Notification

    Notification.objects.all().delete()
    create_user("empty-notifications@test.com", tier_slug="free")

    context = auth_context(browser, "empty-notifications@test.com")
    page = context.new_page()
    page.goto(f"{django_server}/notifications", wait_until="domcontentloaded")

    empty_state = page.locator('[data-testid="member-empty-state"]')
    assert empty_state.count() == 1
    assert empty_state.get_attribute("data-empty-kind") == "fresh"
    assert "No notifications yet" in page.content()
    assert "When there is something new for your account" in page.content()
    assert page.locator("a.block.rounded-lg").count() == 0


@pytest.mark.django_db(transaction=True)
def test_tutorials_fresh_empty_state_uses_member_component(django_server, page):
    from content.models import Tutorial

    Tutorial.objects.all().delete()
    connection.close()

    page.goto(f"{django_server}/tutorials", wait_until="domcontentloaded")

    empty_state = page.locator('[data-testid="member-empty-state"]')
    assert empty_state.count() == 1
    assert empty_state.get_attribute("data-empty-kind") == "fresh"
    assert "No tutorials yet" in page.content()
    assert page.locator("article").count() == 0
