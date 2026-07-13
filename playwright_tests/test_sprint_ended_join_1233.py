"""Member self-join closes when a sprint has ended (#1233)."""

import datetime
import os

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_user as _create_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.local_only, pytest.mark.core]
ISSUE_PREFIX = "issue-1233"
ENDED_COPY = "This sprint has ended and is no longer open to join."


def _reset():
    from django.db import connection

    from events.models import Event, EventSeries
    from plans.models import Plan, Sprint, SprintEnrollment

    sprints = Sprint.objects.filter(slug__startswith=ISSUE_PREFIX)
    Plan.objects.filter(sprint__in=sprints).delete()
    SprintEnrollment.objects.filter(sprint__in=sprints).delete()
    sprints.delete()
    Event.objects.filter(slug__startswith=ISSUE_PREFIX).delete()
    EventSeries.objects.filter(slug__startswith=ISSUE_PREFIX).delete()
    connection.close()


def _create_sprint(
    suffix,
    *,
    ends_in_days=0,
    status="active",
    min_tier_level=30,
    with_past_call=False,
):
    from django.db import connection

    from events.models import Event, EventSeries
    from plans.models import Sprint

    today = timezone.localdate()
    series = None
    if with_past_call:
        series = EventSeries.objects.create(
            name="Ended sprint calls",
            slug=f"{ISSUE_PREFIX}-{suffix}-calls",
            cadence="weekly",
            day_of_week=2,
            start_time=datetime.time(18, 0),
            timezone="Europe/Berlin",
        )
    sprint = Sprint.objects.create(
        name=f"Issue 1233 {suffix.title()} Sprint",
        slug=f"{ISSUE_PREFIX}-{suffix}",
        start_date=today - datetime.timedelta(days=42 - ends_in_days),
        duration_weeks=6,
        status=status,
        min_tier_level=min_tier_level,
        event_series=series,
    )
    if series:
        Event.objects.create(
            title="Past cohort closing call",
            slug=f"{ISSUE_PREFIX}-{suffix}-closing-call",
            description="The cohort's final call remains visible.",
            kind="standard",
            platform="zoom",
            start_datetime=datetime.datetime.combine(
                today - datetime.timedelta(days=2),
                datetime.time(18, 0),
                tzinfo=datetime.timezone.utc,
            ),
            end_datetime=datetime.datetime.combine(
                today - datetime.timedelta(days=2),
                datetime.time(19, 0),
                tzinfo=datetime.timezone.utc,
            ),
            timezone="Europe/Berlin",
            status="completed",
            origin="studio",
            event_series=series,
            location="Zoom",
            published=True,
        )
    connection.close()
    return sprint


def _enroll_with_plan(sprint, email):
    from django.db import connection

    from accounts.models import User
    from plans.models import Plan

    plan = Plan.objects.create(
        sprint=sprint,
        member=User.objects.get(email=email),
        goal="Revisit the work from this cohort",
    )
    connection.close()
    return plan


def _is_enrolled(sprint, email):
    from django.db import connection

    from plans.models import SprintEnrollment

    exists = SprintEnrollment.objects.filter(
        sprint=sprint,
        user__email=email,
    ).exists()
    connection.close()
    return exists


def _assert_only_ended_action(page):
    ended = page.get_by_test_id("sprint-cta-ended")
    expect(ended).to_have_text(ENDED_COPY)
    assert page.get_by_test_id("sprint-cta-login").count() == 0
    assert page.get_by_test_id("sprint-cta-upgrade").count() == 0
    assert page.get_by_test_id("sprint-cta-join").count() == 0
    assert page.locator('form[action$="/join"]').count() == 0


@pytest.mark.django_db(transaction=True)
def test_eligible_member_sees_closed_action_and_past_call(
    django_server,
    browser,
):
    _reset()
    _ensure_tiers()
    email = f"{ISSUE_PREFIX}-main@test.com"
    _create_user(email, tier_slug="premium")
    sprint = _create_sprint("ended", with_past_call=True)

    context = _auth_context(browser, email)
    page = context.new_page()
    page.goto(f"{django_server}{sprint.get_absolute_url()}", wait_until="domcontentloaded")

    # The lifecycle badge has its own shipped display boundary (#979), while
    # participation closes through ``Sprint.has_ended()`` on the hand-off day.
    expect(page.get_by_test_id("sprint-status-badge")).to_be_visible()
    _assert_only_ended_action(page)
    expect(page.get_by_text("Past cohort closing call")).to_be_visible()
    context.close()


@pytest.mark.django_db(transaction=True)
def test_anonymous_and_under_tier_viewers_are_not_misdirected(
    django_server,
    browser,
):
    _reset()
    _ensure_tiers()
    free_email = f"{ISSUE_PREFIX}-free@test.com"
    _create_user(free_email, tier_slug="free")
    sprint = _create_sprint("closed-premium")

    anonymous = browser.new_context()
    anonymous_page = anonymous.new_page()
    anonymous_page.goto(
        f"{django_server}{sprint.get_absolute_url()}",
        wait_until="domcontentloaded",
    )
    _assert_only_ended_action(anonymous_page)
    anonymous.close()

    free_context = _auth_context(browser, free_email)
    free_page = free_context.new_page()
    free_page.goto(
        f"{django_server}{sprint.get_absolute_url()}",
        wait_until="domcontentloaded",
    )
    _assert_only_ended_action(free_page)
    assert "Upgrade to Premium" not in free_page.locator("body").inner_text()
    free_context.close()


@pytest.mark.django_db(transaction=True)
def test_enrolled_member_opens_existing_plan_after_sprint_ends(
    django_server,
    browser,
):
    _reset()
    _ensure_tiers()
    email = f"{ISSUE_PREFIX}-enrolled@test.com"
    _create_user(email, tier_slug="premium")
    sprint = _create_sprint("enrolled-ended")
    plan = _enroll_with_plan(sprint, email)

    context = _auth_context(browser, email)
    page = context.new_page()
    page.goto(f"{django_server}{sprint.get_absolute_url()}", wait_until="domcontentloaded")

    expect(page.get_by_test_id("sprint-cta-enrolled")).to_be_visible()
    assert page.get_by_test_id("sprint-cta-ended").count() == 0
    assert page.get_by_test_id("sprint-cta-join").count() == 0
    page.get_by_test_id("sprint-cta-open-plan").click()
    page.wait_for_url(f"**/sprints/{sprint.slug}/plan/{plan.pk}")
    expect(page.get_by_test_id("my-plan-sprint-dates")).to_be_visible()
    context.close()


@pytest.mark.django_db(transaction=True)
def test_direct_post_cannot_create_late_enrollment(django_server, browser):
    _reset()
    _ensure_tiers()
    email = f"{ISSUE_PREFIX}-direct-post@test.com"
    _create_user(email, tier_slug="premium")
    sprint = _create_sprint("direct-post")

    context = _auth_context(browser, email)
    page = context.new_page()
    page.goto(f"{django_server}{sprint.get_absolute_url()}", wait_until="domcontentloaded")
    csrf = next(cookie["value"] for cookie in context.cookies() if cookie["name"] == "csrftoken")
    response = page.request.post(
        f"{django_server}/sprints/{sprint.slug}/join",
        headers={"X-CSRFToken": csrf, "Referer": page.url},
        max_redirects=0,
    )

    assert response.status == 302
    assert response.headers["location"] == sprint.get_absolute_url()
    assert not _is_enrolled(sprint, email)
    page.goto(f"{django_server}{sprint.get_absolute_url()}", wait_until="domcontentloaded")
    _assert_only_ended_action(page)
    expect(page.get_by_test_id("sprint-messages")).to_contain_text(ENDED_COPY)
    board_response = page.request.get(f"{django_server}/sprints/{sprint.slug}/board")
    assert board_response.status == 404
    context.close()


@pytest.mark.django_db(transaction=True)
def test_participation_closes_on_exact_end_date(django_server, browser):
    _reset()
    _ensure_tiers()
    email = f"{ISSUE_PREFIX}-boundary@test.com"
    _create_user(email, tier_slug="premium")
    ended = _create_sprint("ends-today", ends_in_days=0)
    current = _create_sprint("ends-tomorrow", ends_in_days=1)

    context = _auth_context(browser, email)
    page = context.new_page()
    page.goto(f"{django_server}{current.get_absolute_url()}", wait_until="domcontentloaded")
    expect(page.get_by_test_id("sprint-cta-join")).to_be_visible()
    page.get_by_test_id("sprint-cta-join").click()
    page.wait_for_url(f"**/sprints/{current.slug}/board")
    assert _is_enrolled(current, email)

    page.goto(f"{django_server}{ended.get_absolute_url()}", wait_until="domcontentloaded")
    _assert_only_ended_action(page)
    assert not _is_enrolled(ended, email)
    context.close()


@pytest.mark.django_db(transaction=True)
def test_cancelled_sprint_keeps_distinct_closure(django_server, browser):
    _reset()
    _ensure_tiers()
    sprint = _create_sprint("cancelled-ended", status="cancelled")
    page = browser.new_page()
    page.goto(f"{django_server}{sprint.get_absolute_url()}", wait_until="domcontentloaded")

    expect(page.get_by_test_id("sprint-cta-cancelled")).to_have_text(
        "This sprint has been cancelled and is no longer open to join."
    )
    assert page.get_by_test_id("sprint-cta-ended").count() == 0
    assert page.get_by_test_id("sprint-cta-join").count() == 0
    page.context.close()
