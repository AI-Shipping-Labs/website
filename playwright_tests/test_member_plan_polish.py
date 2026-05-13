"""Playwright coverage for issue #460 member plan polish."""

import datetime
import os

import pytest

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import create_user as _create_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402


def _clear_plan_data():
    from accounts.models import Token
    from plans.models import (
        Checkpoint,
        Deliverable,
        InterviewNote,
        NextStep,
        Plan,
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
    Plan.objects.all().delete()
    SprintEnrollment.objects.all().delete()
    Sprint.objects.all().delete()
    Token.objects.filter(name="member-plan-editor").delete()
    connection.close()


def _seed_polish_plan(owner_email="member@test.com", teammate_email="teammate@test.com"):
    from accounts.models import User
    from plans.models import (
        Checkpoint,
        Deliverable,
        NextStep,
        Plan,
        Sprint,
        SprintEnrollment,
        Week,
    )

    sprint = Sprint.objects.create(
        name="Plan Polish Sprint",
        slug="plan-polish",
        start_date=datetime.date(2026, 5, 1),
        duration_weeks=6,
    )
    owner = User.objects.get(email=owner_email)
    teammate = User.objects.get(email=teammate_email)
    SprintEnrollment.objects.create(sprint=sprint, user=owner)
    SprintEnrollment.objects.create(sprint=sprint, user=teammate)

    plan = Plan.objects.create(
        member=owner,
        sprint=sprint,
        status="shared",
        visibility="cohort",
        summary_goal="Ship **markdown** safely",
    )
    Week.objects.create(plan=plan, week_number=1, position=0)
    week = plan.weeks.get(week_number=1)
    checkpoint = Checkpoint.objects.create(
        week=week,
        description="Build prototype",
        position=0,
    )
    deliverable = Deliverable.objects.create(
        plan=plan,
        description="Record demo",
        position=0,
    )
    next_step = NextStep.objects.create(
        plan=plan,
        description="Book review",
        position=0,
    )

    teammate_plan = Plan.objects.create(
        member=teammate,
        sprint=sprint,
        visibility="private",
    )
    connection.close()
    return {
        "sprint_id": sprint.pk,
        "sprint_slug": sprint.slug,
        "plan_id": plan.pk,
        "checkpoint_id": checkpoint.pk,
        "deliverable_id": deliverable.pk,
        "next_step_id": next_step.pk,
        "teammate_plan_id": teammate_plan.pk,
    }


@pytest.mark.django_db(transaction=True)
class TestMemberPlanPolish:
    def test_owner_toggles_and_edits_from_readable_plan(self, django_server, browser):
        _ensure_tiers()
        _clear_plan_data()
        _create_user("member@test.com", tier_slug="free", email_verified=True)
        _create_user("teammate@test.com", tier_slug="free", email_verified=True)
        data = _seed_polish_plan()

        context = _auth_context(browser, "member@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/sprints/{data['sprint_slug']}/plan/{data['plan_id']}",
            wait_until="domcontentloaded",
        )

        assert page.locator('[data-testid="header-plan-link"]').get_attribute("href") == (
            f"/sprints/{data['sprint_slug']}/plan/{data['plan_id']}"
        )
        assert page.locator('[data-testid="mobile-header-plan-link"]').get_attribute("href") == (
            f"/sprints/{data['sprint_slug']}/plan/{data['plan_id']}"
        )
        # Issue #583 removed the "Edit workspace" CTA -- the page IS the editor.
        assert page.locator('[data-testid="my-plan-edit-cta"]').count() == 0

        item = page.locator('[data-testid="plan-checkpoint"]').first
        with page.expect_response("**/api/checkpoints/*") as checkpoint_response:
            item.locator('[data-testid="plan-row-done-toggle"]').check()
        assert checkpoint_response.value.ok
        assert item.locator('[data-testid="plan-item-save-status"]').inner_text() == "Saved"

        item.locator('[data-testid="plan-item-edit"]').click()
        item.locator('[data-testid="plan-item-markdown-input"]').fill(
            "Build **RAG** prototype"
        )
        with page.expect_response("**/api/checkpoints/*") as edit_response:
            item.locator('[data-testid="plan-item-save"]').click()
        assert edit_response.value.ok
        assert item.locator("strong").inner_text() == "RAG"

        for testid, pattern in [
            ("plan-deliverable", "**/api/deliverables/*"),
            ("plan-next-step", "**/api/next-steps/*"),
        ]:
            row = page.locator(f'[data-testid="{testid}"]').first
            with page.expect_response(pattern) as response_info:
                row.locator('[data-testid="plan-row-done-toggle"]').check()
            assert response_info.value.ok
            assert row.locator('[data-testid="plan-item-save-status"]').inner_text() == "Saved"

        page.reload(wait_until="domcontentloaded")
        assert page.locator('[data-testid="plan-checkpoint"]').first.locator(
            '[data-testid="plan-row-done-toggle"]'
        ).is_checked()
        assert page.locator('[data-testid="plan-deliverable"]').first.locator(
            '[data-testid="plan-row-done-toggle"]'
        ).is_checked()
        assert page.locator('[data-testid="plan-next-step"]').first.locator(
            '[data-testid="plan-row-done-toggle"]'
        ).is_checked()
        assert page.locator('[data-testid="plan-checkpoint"]').first.locator(
            "strong"
        ).inner_text() == "RAG"
        context.close()

    def test_teammate_plan_page_is_read_only(self, django_server, browser):
        _ensure_tiers()
        _clear_plan_data()
        _create_user("member@test.com", tier_slug="free", email_verified=True)
        _create_user("teammate@test.com", tier_slug="free", email_verified=True)
        data = _seed_polish_plan()

        context = _auth_context(browser, "teammate@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/sprints/{data['sprint_slug']}/plans/{data['plan_id']}",
            wait_until="domcontentloaded",
        )

        assert page.locator("strong", has_text="markdown").count() >= 1
        assert page.locator('[data-testid="plan-row-done-toggle"]').count() == 0
        assert page.locator('[data-testid="plan-item-edit"]').count() == 0
        assert page.locator('[data-testid="plan-item-markdown-input"]').count() == 0
        context.close()

    def test_mobile_visibility_control_fits_390px_workspace(
        self, django_server, browser, tmp_path,
    ):
        _ensure_tiers()
        _clear_plan_data()
        _create_user("member@test.com", tier_slug="free", email_verified=True)
        _create_user("teammate@test.com", tier_slug="free", email_verified=True)
        data = _seed_polish_plan()

        context = _auth_context(browser, "member@test.com")
        page = context.new_page()
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(
            f"{django_server}/sprints/{data['sprint_slug']}/plan/{data['plan_id']}",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="plan-visibility-form"]').wait_for(
            state="visible",
        )

        page.screenshot(
            path=str(tmp_path / "owner-workspace-visibility-390.png"),
            full_page=True,
        )
        assert page.evaluate(
            "() => document.documentElement.scrollWidth"
            " <= document.documentElement.clientWidth"
        )
        viewport_width = page.viewport_size["width"]
        # Issue #583: the legacy <select> + Save button were replaced by a
        # single toggle switch. Assert the new control still fits inside
        # the mobile viewport (the regression this test originally fixed).
        for selector in [
            '[data-testid="plan-visibility-form"]',
            '[data-testid="plan-visibility-toggle"]',
        ]:
            box = page.locator(selector).bounding_box()
            assert box is not None
            assert box["x"] >= 0
            assert box["x"] + box["width"] <= viewport_width

        context.close()

    def test_studio_participant_links_open_user_detail(self, django_server, browser):
        _ensure_tiers()
        _clear_plan_data()
        _create_user("member@test.com", tier_slug="free", email_verified=True)
        _create_user("teammate@test.com", tier_slug="free", email_verified=True)
        staff = _create_staff_user("staff@test.com")
        data = _seed_polish_plan()

        context = _auth_context(browser, staff.email)
        page = context.new_page()
        page.goto(f"{django_server}/studio/plans/", wait_until="domcontentloaded")
        page.get_by_role("link", name="member@test.com").click()
        page.wait_for_url(f"{django_server}/studio/users/*/")
        assert "/studio/users/" in page.url

        page.goto(f"{django_server}/studio/plans/", wait_until="domcontentloaded")
        page.locator("tr", has_text="member@test.com").get_by_role(
            "link", name="View plan",
        ).click()
        page.wait_for_url(f"{django_server}/studio/plans/{data['plan_id']}/")

        page.goto(
            f"{django_server}/studio/sprints/{data['sprint_id']}/",
            wait_until="domcontentloaded",
        )
        page.get_by_role("link", name="member@test.com").click()
        page.wait_for_url(f"{django_server}/studio/users/*/")
        context.close()
