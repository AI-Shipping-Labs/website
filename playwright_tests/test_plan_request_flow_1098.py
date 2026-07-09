"""Playwright coverage for the plan-request preparation flow (#1098)."""

import datetime
import os
from pathlib import Path

import pytest
from django.utils import timezone

from playwright_tests.conftest import auth_context, create_staff_user, create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.local_only]

SCREENSHOT_DIR = (
    Path(__file__).resolve().parent.parent
    / ".tmp"
    / "aisl-issue-1098-screenshots"
)


def _shot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=True)


def _clear_data():
    from django.db import connection

    from plans.models import Plan, PlanRequest, Sprint, SprintEnrollment
    from questionnaires.models import Response

    PlanRequest.objects.all().delete()
    Plan.objects.all().delete()
    SprintEnrollment.objects.all().delete()
    Sprint.objects.all().delete()
    Response.objects.all().delete()
    connection.close()


def _seed_requested_member():
    from django.db import connection

    from accounts.models import User
    from plans.models import Sprint, SprintEnrollment
    from questionnaires.models import Questionnaire, Response

    member = User.objects.get(email="request-member-1098@test.com")
    staff = User.objects.get(email="staff-1098@test.com")
    member.first_name = "Request"
    member.last_name = "Member"
    member.save(update_fields=["first_name", "last_name"])

    sprint = Sprint.objects.create(
        name="Request Prep Sprint",
        slug="request-prep-sprint",
        start_date=timezone.localdate() - datetime.timedelta(days=7),
        duration_weeks=4,
        status="active",
        min_tier_level=0,
    )
    SprintEnrollment.objects.create(sprint=sprint, user=member, enrolled_by=staff)

    q, _ = Questionnaire.objects.get_or_create(
        slug="onboarding-general",
        defaults={
            "title": "General onboarding",
            "purpose": "onboarding",
            "is_active": True,
        },
    )
    Response.objects.create(questionnaire=q, respondent=member, status="submitted")

    data = {
        "member_pk": member.pk,
        "sprint_pk": sprint.pk,
        "sprint_slug": sprint.slug,
    }
    connection.close()
    return data


def test_member_request_and_staff_prepare_flow(django_server, browser):
    _clear_data()
    create_staff_user("staff-1098@test.com")
    create_user("request-member-1098@test.com", tier_slug="main")
    data = _seed_requested_member()

    member_context = auth_context(browser, "request-member-1098@test.com")
    member_page = member_context.new_page()
    member_page.goto(
        f"{django_server}/sprints/{data['sprint_slug']}/board",
        wait_until="domcontentloaded",
    )
    member_page.get_by_test_id("ask-team-button").first.click()
    member_page.wait_for_url(f"**/sprints/{data['sprint_slug']}/board")
    assert member_page.get_by_text("Asked the team").count() >= 1
    assert member_page.get_by_text("Pinged the team").count() >= 1
    _shot(member_page, "member-board-requested")
    member_context.close()

    staff_context = auth_context(browser, "staff-1098@test.com")
    staff_page = staff_context.new_page()
    staff_page.goto(
        f"{django_server}/studio/sprints/{data['sprint_pk']}/",
        wait_until="domcontentloaded",
    )
    assert staff_page.get_by_test_id("sprint-pending-request-onboarding").inner_text()
    staff_page.get_by_test_id("sprint-pending-request-prepare-link").click()
    staff_page.wait_for_url(
        f"**/studio/sprints/{data['sprint_pk']}/plan-requests/"
        f"{data['member_pk']}/prepare/"
    )
    staff_page.get_by_test_id("plan-request-summary").wait_for(state="visible")
    assert "Request Member" in staff_page.get_by_test_id("request-member-locked").inner_text()
    assert "Request Prep Sprint" in staff_page.get_by_test_id("request-sprint-locked").inner_text()
    assert "Submitted" in staff_page.get_by_test_id("request-onboarding-state").inner_text()
    staff_page.get_by_test_id("plan-request-create-button").wait_for(state="visible")
    _shot(staff_page, "staff-request-prepare")
    staff_context.close()
