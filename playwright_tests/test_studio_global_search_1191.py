"""Playwright coverage for Studio global search and shortcuts (#1191)."""

import datetime
import os
import re

import pytest

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import create_user as _create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection
from django.utils import timezone

pytestmark = pytest.mark.local_only


def _reset_search_data():
    from content.models import Article, Course
    from email_app.models import EmailCampaign
    from events.models import Event

    EmailCampaign.objects.all().delete()
    Article.objects.all().delete()
    Course.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _seed_search_data():
    from content.models import Article, Course
    from email_app.models import EmailCampaign
    from events.models import Event

    article = Article.objects.create(
        title="Studio Global Search Article",
        slug="studio-global-search-article",
        date=datetime.date.today(),
        published=True,
    )
    course = Course.objects.create(
        title="Studio Global Search Course",
        slug="studio-global-search-course",
        status="published",
    )
    event = Event.objects.create(
        title="Studio Global Search Event",
        slug="studio-global-search-event",
        status="upcoming",
        start_datetime=timezone.now() + datetime.timedelta(days=3),
    )
    campaign = EmailCampaign.objects.create(
        subject="Studio Global Search Campaign",
        body="Short campaign body",
        status="draft",
    )
    connection.close()
    return {
        "article_id": article.pk,
        "course_id": course.pk,
        "event_id": event.pk,
        "campaign_id": campaign.pk,
    }


def _open_global_result(page, query, expected_text):
    search = page.get_by_test_id("studio-global-search-input")
    search.fill(query)
    result = page.get_by_role("option", name=re.compile(expected_text))
    result.wait_for(state="visible")
    result.click()


@pytest.mark.django_db(transaction=True)
@pytest.mark.core
def test_staff_global_search_navigates_to_users_content_events_and_campaigns(
    django_server,
    browser,
):
    _reset_search_data()
    _create_staff_user("studio-global-search-admin@test.com")
    member = _create_user(
        "global-search-member-1191@test.com",
        email_verified=True,
    )
    ids = _seed_search_data()

    context = _auth_context(browser, "studio-global-search-admin@test.com")
    page = context.new_page()

    page.goto(f"{django_server}/studio/events/", wait_until="domcontentloaded")
    _open_global_result(page, "global-search-member-1191", "global-search-member-1191")
    assert page.url.startswith(f"{django_server}/studio/users/{member.id}/")

    page.goto(f"{django_server}/studio/events/", wait_until="domcontentloaded")
    _open_global_result(page, "Studio Global Search Article", "Studio Global Search Article")
    assert page.url.startswith(f"{django_server}/studio/articles/{ids['article_id']}/edit")

    page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")
    _open_global_result(page, "Studio Global Search Event", "Studio Global Search Event")
    assert page.url.startswith(f"{django_server}/studio/events/{ids['event_id']}/edit")

    page.goto(f"{django_server}/studio/events/", wait_until="domcontentloaded")
    _open_global_result(page, "Studio Global Search Campaign", "Studio Global Search Campaign")
    assert page.url.startswith(f"{django_server}/studio/campaigns/{ids['campaign_id']}/")

    context.close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.core
def test_staff_global_search_no_results_state(django_server, browser):
    _reset_search_data()
    _create_staff_user("studio-global-search-empty@test.com")

    context = _auth_context(browser, "studio-global-search-empty@test.com")
    page = context.new_page()
    page.goto(f"{django_server}/studio/events/", wait_until="domcontentloaded")

    page.get_by_test_id("studio-global-search-input").fill("zz-no-match-1191")
    page.get_by_test_id("studio-global-search-results").get_by_text(
        "No results",
    ).wait_for(state="visible")
    assert page.url.startswith(f"{django_server}/studio/events/")

    context.close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.core
def test_dashboard_shortcuts_and_user_lookup(django_server, browser):
    _reset_search_data()
    _create_staff_user("studio-dashboard-shortcuts-1191@test.com")
    member = _create_user(
        "dashboard-lookup-member-1191@test.com",
        email_verified=True,
    )

    context = _auth_context(browser, "studio-dashboard-shortcuts-1191@test.com")
    page = context.new_page()
    page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

    page.get_by_role("link", name=re.compile("New event")).click()
    assert page.url.startswith(f"{django_server}/studio/events/new")

    page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")
    page.get_by_role("link", name=re.compile("New campaign")).click()
    assert page.url.startswith(f"{django_server}/studio/campaigns/new")

    page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")
    page.get_by_role("link", name=re.compile("New plan")).click()
    assert page.url.startswith(f"{django_server}/studio/plans/new")

    page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")
    lookup = page.get_by_test_id("studio-dashboard-user-lookup-input")
    lookup.fill("dashboard-lookup-member-1191")
    page.get_by_test_id("studio-dashboard-user-lookup-results").get_by_role(
        "option",
        name=re.compile("dashboard-lookup-member-1191"),
    ).click()
    assert page.url.startswith(f"{django_server}/studio/users/{member.id}/")

    context.close()
