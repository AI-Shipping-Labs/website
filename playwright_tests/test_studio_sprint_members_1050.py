"""Playwright coverage for merged Studio sprint members and view-as-member."""

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
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = pytest.mark.local_only


def _clear_plans_data():
    from accounts.models import Token
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
    )

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
    Token.objects.filter(
        name__in=["studio-plan-editor", "member-plan-editor"],
    ).delete()
    connection.close()


def _seed_sprint_members():
    from accounts.models import User
    from plans.models import Plan, Sprint, SprintEnrollment

    staff = User.objects.get(email="staff@test.com")
    with_plan = User.objects.get(email="with-plan@test.com")
    no_plan = User.objects.get(email="no-plan@test.com")
    plan_only = User.objects.get(email="plan-only@test.com")
    with_plan.first_name = "With"
    with_plan.last_name = "Plan"
    with_plan.save(update_fields=["first_name", "last_name"])
    sprint = Sprint.objects.create(
        name="June Studio Sprint",
        slug="june-studio",
        start_date=datetime.date(2026, 6, 1),
        duration_weeks=4,
        status="active",
    )
    SprintEnrollment.objects.create(
        sprint=sprint,
        user=with_plan,
        enrolled_by=staff,
    )
    SprintEnrollment.objects.create(sprint=sprint, user=no_plan)
    shared_plan = Plan.objects.create(
        sprint=sprint,
        member=with_plan,
        visibility="cohort",
    )
    shared_plan.mark_shared()
    plan_only_plan = Plan.objects.create(
        sprint=sprint,
        member=plan_only,
        visibility="private",
    )
    SprintEnrollment.objects.filter(sprint=sprint, user=plan_only).delete()
    data = {
        "sprint_id": sprint.pk,
        "sprint_slug": sprint.slug,
        "shared_plan_id": shared_plan.pk,
        "plan_only_id": plan_only_plan.pk,
    }
    connection.close()
    return data


@pytest.mark.django_db(transaction=True)
class TestStudioSprintMembersTable:
    @pytest.mark.core
    def test_staff_scans_enrollments_and_plans_in_one_table(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        _create_user("with-plan@test.com", tier_slug="free")
        _create_user("no-plan@test.com", tier_slug="free")
        _create_user("plan-only@test.com", tier_slug="free")
        data = _seed_sprint_members()

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/sprints/{data['sprint_id']}/",
            wait_until="domcontentloaded",
        )

        page.get_by_role("heading", name="Sprint members").wait_for(
            state="visible"
        )
        assert "2 enrolled" in page.get_by_test_id("sprint-members-counts").inner_text()
        assert "2 plans" in page.get_by_test_id("sprint-members-counts").inner_text()
        assert page.get_by_text("Plans in this sprint").count() == 0
        assert page.get_by_text("Enrolled members").count() == 0

        with_plan = page.locator('[data-user-email="with-plan@test.com"]')
        assert "With Plan" in with_plan.inner_text()
        assert "by staff@test.com" in with_plan.inner_text()
        assert "Cohort" in with_plan.inner_text()
        assert "Shared" in with_plan.inner_text()
        with_plan.get_by_role("link", name="View plan").wait_for(state="visible")

        no_plan = page.locator('[data-user-email="no-plan@test.com"]')
        assert "Self-joined" in no_plan.inner_text()
        assert "No plan yet" in no_plan.inner_text()
        create_link = no_plan.get_by_test_id("sprint-member-create-plan-link")
        assert "/studio/plans/new" in create_link.get_attribute("href")

        plan_only = page.locator('[data-user-email="plan-only@test.com"]')
        assert "Not enrolled" in plan_only.inner_text()
        assert "Private" in plan_only.inner_text()
        assert plan_only.get_by_test_id("sprint-unenroll-button").count() == 0

        page.get_by_test_id("sprint-feedback-section").wait_for(state="visible")
        page.get_by_test_id("sprint-bulk-enroll-link").wait_for(state="visible")
        page.get_by_test_id("sprint-add-member-details").wait_for(state="visible")
        page.get_by_test_id("sprint-danger-zone").wait_for(state="visible")
        context.close()


@pytest.mark.django_db(transaction=True)
class TestStudioPlanViewAsMember:
    @pytest.mark.core
    def test_staff_views_plan_as_member_and_returns(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        _create_staff_user("staff@test.com")
        _create_user("with-plan@test.com", tier_slug="free")
        _create_user("no-plan@test.com", tier_slug="free")
        _create_user("plan-only@test.com", tier_slug="free")
        data = _seed_sprint_members()

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/plans/{data['shared_plan_id']}/",
            wait_until="domcontentloaded",
        )
        page.get_by_test_id("studio-plan-view-as-member").click()
        page.wait_for_url(
            f"{django_server}/sprints/{data['sprint_slug']}/plan/"
            f"{data['shared_plan_id']}",
        )

        page.locator("#impersonation-banner").wait_for(state="visible")
        assert "with-plan@test.com" in page.locator("#impersonation-banner").inner_text()
        assert "/studio/" not in page.url
        assert "/plans/" not in page.url

        page.get_by_role("button", name="Return to your account").click()
        page.wait_for_url(f"{django_server}/studio/users/")
        page.goto(
            f"{django_server}/studio/plans/{data['shared_plan_id']}/",
            wait_until="domcontentloaded",
        )
        page.get_by_test_id("studio-plan-view-as-member").wait_for(state="visible")
        context.close()
