"""Playwright coverage for short plan goals and private details (#584)."""

import datetime
import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import create_user as _create_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
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
    Token.objects.filter(name__in=['member-plan-editor', 'studio-plan-editor']).delete()
    connection.close()


def _seed_goal_plan(*, goal='Ship one project', visibility='cohort'):
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
        name='Goal Sprint',
        slug='goal-sprint',
        start_date=datetime.date(2026, 5, 1),
        duration_weeks=2,
    )
    owner = User.objects.get(email='member@test.com')
    teammate = User.objects.get(email='teammate@test.com')
    SprintEnrollment.objects.create(sprint=sprint, user=owner)
    SprintEnrollment.objects.create(sprint=sprint, user=teammate)
    plan = Plan.objects.create(
        member=owner,
        sprint=sprint,
        status='shared',
        visibility=visibility,
        goal=goal,
        summary_goal='Long reflection...',
        summary_main_gap='Need ML chops',
    )
    week = Week.objects.create(plan=plan, week_number=1, position=0)
    Checkpoint.objects.create(week=week, description='Build skeleton', position=0)
    Deliverable.objects.create(plan=plan, description='Record demo', position=0)
    NextStep.objects.create(plan=plan, description='Book review', position=0)
    connection.close()
    return {'sprint_slug': sprint.slug, 'plan_id': plan.pk}


@pytest.mark.django_db(transaction=True)
class TestSprintPlanGoal584:
    def test_owner_sees_goal_and_details(self, django_server, browser):
        _ensure_tiers()
        _clear_plan_data()
        _create_user('member@test.com', tier_slug='free', email_verified=True)
        _create_user('teammate@test.com', tier_slug='free', email_verified=True)
        data = _seed_goal_plan(visibility='private')

        context = _auth_context(browser, 'member@test.com')
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/sprints/{data['sprint_slug']}/plan/{data['plan_id']}",
                wait_until='domcontentloaded',
            )

            goal = page.locator('[data-testid="plan-goal"]')
            weeks = page.locator('[data-testid="plan-weeks"]')
            details = page.locator('[data-testid="plan-details"]')
            expect(goal).to_be_visible()
            expect(goal).to_contain_text('Ship one project')
            assert goal.bounding_box()['y'] < weeks.bounding_box()['y']
            expect(details).to_be_visible()
            expect(details).to_contain_text('Goal (long-form)')
            expect(details).to_contain_text(
                "Only you can see this section. Use it for personal context that doesn't need to be shared.",
            )
        finally:
            context.close()

    def test_teammate_sees_goal_not_details_on_shared_plan(self, django_server, browser):
        _ensure_tiers()
        _clear_plan_data()
        _create_user('member@test.com', tier_slug='free', email_verified=True)
        _create_user('teammate@test.com', tier_slug='free', email_verified=True)
        data = _seed_goal_plan(visibility='cohort')

        context = _auth_context(browser, 'teammate@test.com')
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/sprints/{data['sprint_slug']}/plans/{data['plan_id']}",
                wait_until='domcontentloaded',
            )

            expect(page.locator('[data-testid="plan-goal"]')).to_contain_text(
                'Ship one project',
            )
            assert page.locator('[data-testid="plan-details"]').count() == 0
            assert page.get_by_text('Need ML chops').count() == 0
            assert page.get_by_text('Long reflection...').count() == 0
        finally:
            context.close()

    def test_owner_edits_goal_inline_and_it_persists(self, django_server, browser):
        _ensure_tiers()
        _clear_plan_data()
        _create_user('member@test.com', tier_slug='free', email_verified=True)
        _create_user('teammate@test.com', tier_slug='free', email_verified=True)
        data = _seed_goal_plan(goal='Old goal', visibility='private')

        context = _auth_context(browser, 'member@test.com')
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/sprints/{data['sprint_slug']}/plan/{data['plan_id']}",
                wait_until='domcontentloaded',
            )
            goal = page.locator('[data-testid="plan-goal"]')
            expect(goal).to_contain_text('Old goal')
            goal.locator('[data-testid="plan-goal-edit"]').click()
            goal.locator('[data-testid="plan-goal-input"]').fill(
                'Ship one shipped project this sprint',
            )
            with page.expect_response('**/goal') as response_info:
                goal.locator('[data-testid="plan-goal-save"]').click()
            assert response_info.value.ok
            expect(goal.locator('[data-testid="plan-goal-text"]')).to_contain_text(
                'Ship one shipped project this sprint',
            )
            page.reload(wait_until='domcontentloaded')
            expect(page.locator('[data-testid="plan-goal-text"]')).to_contain_text(
                'Ship one shipped project this sprint',
            )
        finally:
            context.close()

    def test_empty_goal_is_owner_placeholder_but_hidden_from_teammate(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plan_data()
        _create_user('member@test.com', tier_slug='free', email_verified=True)
        _create_user('teammate@test.com', tier_slug='free', email_verified=True)
        data = _seed_goal_plan(goal='', visibility='cohort')

        owner_context = _auth_context(browser, 'member@test.com')
        try:
            page = owner_context.new_page()
            page.goto(
                f"{django_server}/sprints/{data['sprint_slug']}/plan/{data['plan_id']}",
                wait_until='domcontentloaded',
            )
            expect(page.locator('[data-testid="plan-goal"]')).to_contain_text(
                "Add a one-sentence goal so teammates know what you're shipping this sprint.",
            )
        finally:
            owner_context.close()

        teammate_context = _auth_context(browser, 'teammate@test.com')
        try:
            page = teammate_context.new_page()
            page.goto(
                f"{django_server}/sprints/{data['sprint_slug']}/plans/{data['plan_id']}",
                wait_until='domcontentloaded',
            )
            assert page.locator('[data-testid="plan-goal"]').count() == 0
            expect(page.locator('[data-testid="plan-weeks"]')).to_be_visible()
        finally:
            teammate_context.close()

    def test_staff_edits_goal_from_studio(self, django_server, browser):
        _ensure_tiers()
        _clear_plan_data()
        _create_user('member@test.com', tier_slug='free', email_verified=True)
        _create_user('teammate@test.com', tier_slug='free', email_verified=True)
        staff = _create_staff_user('staff@test.com')
        data = _seed_goal_plan(goal='Old goal', visibility='private')

        context = _auth_context(browser, staff.email)
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/studio/plans/{data['plan_id']}/edit/",
                wait_until='domcontentloaded',
            )
            goal_input = page.locator('[data-field="goal"]')
            expect(goal_input).to_have_value('Old goal')
            with page.expect_response('**/api/plans/*') as response_info:
                goal_input.fill('Staff-curated sprint goal')
                goal_input.blur()
            assert response_info.value.ok

            owner_context = _auth_context(browser, 'member@test.com')
            try:
                owner_page = owner_context.new_page()
                owner_page.goto(
                    f"{django_server}/sprints/{data['sprint_slug']}/plan/{data['plan_id']}",
                    wait_until='domcontentloaded',
                )
                expect(owner_page.locator('[data-testid="plan-goal-text"]')).to_contain_text(
                    'Staff-curated sprint goal',
                )
            finally:
                owner_context.close()
        finally:
            context.close()
