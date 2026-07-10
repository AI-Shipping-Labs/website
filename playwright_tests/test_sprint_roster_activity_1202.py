"""Playwright coverage for Studio sprint roster activity (issue #1202)."""

import datetime
import os

import pytest

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import create_user as _create_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.local_only]


def _clear_roster_activity_data():
    from accounts.models import Token
    from crm.models import (
        AppliedProgressChange,
        IngestedProgressEvent,
        SlackChannelIngest,
        SlackMessage,
        SlackThread,
    )
    from plans.models import (
        Checkpoint,
        Deliverable,
        InterviewNote,
        NextStep,
        Plan,
        PlanRequest,
        Resource,
        Sprint,
        SprintEnrollment,
        Week,
        WeekNote,
    )

    AppliedProgressChange.objects.all().delete()
    IngestedProgressEvent.objects.all().delete()
    SlackMessage.objects.all().delete()
    SlackThread.objects.all().delete()
    SlackChannelIngest.objects.all().delete()
    WeekNote.objects.all().delete()
    Checkpoint.objects.all().delete()
    Week.objects.all().delete()
    Resource.objects.all().delete()
    Deliverable.objects.all().delete()
    NextStep.objects.all().delete()
    InterviewNote.objects.all().delete()
    PlanRequest.objects.all().delete()
    Plan.objects.all().delete()
    SprintEnrollment.objects.all().delete()
    Sprint.objects.all().delete()
    Token.objects.filter(name__in=["pw-roster-activity"]).delete()
    connection.close()


def _seed_studio_roster():
    from accounts.models import User
    from crm.models import SlackMessage, SlackThread
    from plans.models import Checkpoint, Deliverable, Plan, Sprint, SprintEnrollment, Week

    staff = User.objects.get(email="pw-roster-staff@test.com")
    updated = User.objects.get(email="pw-updated@test.com")
    stale = User.objects.get(email="pw-stale@test.com")
    no_plan = User.objects.get(email="pw-no-plan@test.com")
    plan_only = User.objects.get(email="pw-plan-only@test.com")

    sprint = Sprint.objects.create(
        name="Roster Activity Sprint",
        slug="pw-roster-activity",
        start_date=timezone.localdate() - datetime.timedelta(days=3),
        duration_weeks=4,
        status="active",
    )
    for member in [updated, stale, no_plan]:
        SprintEnrollment.objects.create(
            sprint=sprint,
            user=member,
            enrolled_by=staff if member == updated else None,
        )

    updated_plan = Plan.objects.create(sprint=sprint, member=updated)
    updated_week = Week.objects.create(plan=updated_plan, week_number=1)
    for index in range(5):
        Checkpoint.objects.create(
            week=updated_week,
            description=f"Checkpoint {index}",
            done_at=(
                timezone.now() - datetime.timedelta(hours=1)
                if index < 3 else None
            ),
        )
    thread = SlackThread.objects.create(
        channel_id="C_PLAN_SPRINTS",
        thread_ts="1770000000.000100",
        member=updated,
        plan=updated_plan,
        posted_at=timezone.now() - datetime.timedelta(minutes=45),
    )
    SlackMessage.objects.create(
        thread=thread,
        ts=thread.thread_ts,
        author_display="Member",
        text="Shipped progress",
        posted_at=timezone.now() - datetime.timedelta(minutes=45),
        is_root=True,
    )

    stale_plan = Plan.objects.create(sprint=sprint, member=stale)
    Week.objects.create(plan=stale_plan, week_number=1)
    Deliverable.objects.create(
        plan=stale_plan,
        description="Old progress",
        done_at=timezone.now() - datetime.timedelta(days=10),
    )

    plan_only_plan = Plan.objects.create(sprint=sprint, member=plan_only)
    Week.objects.create(plan=plan_only_plan, week_number=1)
    Deliverable.objects.create(
        plan=plan_only_plan,
        description="Plan-only old progress",
        done_at=timezone.now() - datetime.timedelta(days=8),
    )
    SprintEnrollment.objects.filter(sprint=sprint, user=plan_only).delete()
    data = {"sprint_id": sprint.pk, "sprint_slug": sprint.slug}
    connection.close()
    return data


def _seed_api_roster():
    from accounts.models import Token, User
    from plans.models import Checkpoint, Plan, Sprint, SprintEnrollment, Week

    staff = User.objects.create_user(
        email="pw-roster-api-staff@test.com",
        password="pw",
        is_staff=True,
    )
    token = Token.objects.create(user=staff, name="pw-roster-activity")
    sprint = Sprint.objects.create(
        name="Roster API Sprint",
        slug="pw-roster-api",
        start_date=timezone.localdate() - datetime.timedelta(days=2),
        duration_weeks=4,
        status="active",
    )
    updated = User.objects.create_user(email="pw-api-updated@test.com", password="pw")
    no_plan = User.objects.create_user(email="pw-api-no-plan@test.com", password="pw")
    SprintEnrollment.objects.create(sprint=sprint, user=updated)
    SprintEnrollment.objects.create(sprint=sprint, user=no_plan)
    plan = Plan.objects.create(sprint=sprint, member=updated)
    week = Week.objects.create(plan=plan, week_number=1)
    Checkpoint.objects.create(
        week=week,
        description="Done",
        done_at=timezone.now() - datetime.timedelta(hours=1),
    )
    Checkpoint.objects.create(week=week, description="Open")
    key = token.key
    slug = sprint.slug
    connection.close()
    return key, slug


def test_staff_scans_and_filters_sprint_roster_activity(django_server, browser):
    _ensure_tiers()
    _clear_roster_activity_data()
    _create_staff_user("pw-roster-staff@test.com")
    _create_user("pw-updated@test.com", tier_slug="free")
    _create_user("pw-stale@test.com", tier_slug="free")
    _create_user("pw-no-plan@test.com", tier_slug="free")
    _create_user("pw-plan-only@test.com", tier_slug="free")
    data = _seed_studio_roster()

    context = _auth_context(browser, "pw-roster-staff@test.com")
    page = context.new_page()
    page.goto(
        f"{django_server}/studio/sprints/{data['sprint_id']}/",
        wait_until="domcontentloaded",
    )

    page.get_by_role("heading", name="Sprint members").wait_for(state="visible")
    updated = page.locator('[data-user-email="pw-updated@test.com"]')
    assert "3/5 checkpoints" in updated.inner_text()
    assert "Slack update" in updated.inner_text()
    assert "Updated this week" in updated.inner_text()
    updated.get_by_role("link", name="View plan").wait_for(state="visible")
    updated.get_by_role("link", name="Edit plan").wait_for(state="visible")
    updated.get_by_test_id("sprint-unenroll-button").wait_for(state="visible")

    page.get_by_test_id("sprint-members-no-update-filter").click()
    assert "activity=no_update_this_week" in page.url
    assert page.locator('[data-user-email="pw-updated@test.com"]').count() == 0
    visible_rows = page.locator('[data-testid$="member-row"]').evaluate_all(
        "(rows) => rows.map((row) => row.getAttribute('data-user-email'))"
    )
    assert visible_rows == [
        "pw-no-plan@test.com",
        "pw-stale@test.com",
        "pw-plan-only@test.com",
    ]
    assert "No plan" in page.locator('[data-user-email="pw-no-plan@test.com"]').inner_text()
    assert (
        "No update this week"
        in page.locator('[data-user-email="pw-stale@test.com"]').inner_text()
    )

    page.get_by_test_id("sprint-members-clear-activity-filter").click()
    assert "activity=no_update_this_week" not in page.url
    page.locator('[data-user-email="pw-updated@test.com"]').wait_for(state="visible")
    context.close()


def test_staff_token_reads_roster_activity_api(django_server, browser):
    _clear_roster_activity_data()
    token_key, sprint_slug = _seed_api_roster()

    context = browser.new_context()
    response = context.request.get(
        f"{django_server}/api/sprints/{sprint_slug}/roster-activity"
        "?activity=no_update_this_week",
        headers={"Authorization": f"Token {token_key}"},
    )

    assert response.status == 200
    body = response.json()
    assert body["sprint"]["slug"] == sprint_slug
    assert body["current_week"]["active"] is True
    assert body["totals"]["members"] == 2
    assert body["totals"]["no_update_this_week"] == 1
    assert [row["member"]["email"] for row in body["members"]] == [
        "pw-api-no-plan@test.com"
    ]
    assert body["members"][0]["progress"]["label"] == "No plan"
    context.close()
