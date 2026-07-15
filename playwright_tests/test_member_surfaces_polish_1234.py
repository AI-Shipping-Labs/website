"""Focused member-surface journeys for issue #1234.

Visual contracts are isolated under ``visual_regression``. Existing
authoritative tests continue to own checkpoint editing, teammate read-only
boundaries, and successful onboarding form submission.
"""

import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_user as _create_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers
from playwright_tests.test_cohort_board import (
    _clear_plans_data,
    _create_plan,
    _create_sprint,
    _set_user_name,
)
from playwright_tests.test_onboarding_ai_804 import _llm_enabled, _reset
from playwright_tests.test_sprint_plan_unification_583 import (
    _clear_plan_data,
    _seed_workspace,
)

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

from django.utils import timezone  # noqa: E402

pytestmark = pytest.mark.local_only


@pytest.mark.django_db(transaction=True)
@pytest.mark.visual_regression
def test_owner_and_teammate_share_equal_weekly_work_hierarchy(
    django_server, browser,
):
    """Both routes render the same shared partial and heading hierarchy."""
    _ensure_tiers()
    _clear_plan_data()
    _create_user('polish-owner@test.com', tier_slug='free', email_verified=True)
    _create_user(
        'polish-teammate@test.com', tier_slug='free', email_verified=True,
    )
    data = _seed_workspace(
        owner_email='polish-owner@test.com',
        teammate_email='polish-teammate@test.com',
        owner_visibility='cohort',
    )

    expected_classes = 'text-2xl font-semibold tracking-tight text-foreground'
    for email, route in (
        (
            'polish-owner@test.com',
            f"/sprints/{data['sprint_slug']}/plan/{data['plan_id']}",
        ),
        (
            'polish-teammate@test.com',
            f"/sprints/{data['sprint_slug']}/plans/{data['plan_id']}",
        ),
    ):
        context = _auth_context(browser, email)
        try:
            page = context.new_page()
            page.goto(f'{django_server}{route}', wait_until='domcontentloaded')
            weeks = page.locator('[data-testid="plan-weeks"]')
            expect(weeks).to_be_visible()
            expect(
                weeks.get_by_role('heading', name='Weekly work'),
            ).to_have_attribute('class', expected_classes)
            assert weeks.get_by_text('Timeline', exact=True).count() == 0
            expect(
                page.get_by_role('heading', name='Resources'),
            ).to_have_attribute('class', expected_classes)
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
def test_cohort_board_marks_only_the_signed_in_member_current(
    django_server, browser,
):
    """The current-row semantic survives owner and teammate navigation."""
    _ensure_tiers()
    _clear_plans_data()
    _create_user('polish-viewer@test.com', tier_slug='free')
    _create_user('polish-peer@test.com', tier_slug='free')
    _set_user_name('polish-viewer@test.com', 'Current', 'Member')
    _set_user_name('polish-peer@test.com', 'Peer', 'Member')
    sprint = _create_sprint(
        'member-polish-sprint',
        'Member Polish Sprint',
        timezone.localdate(),
    )
    viewer_plan = _create_plan(
        member_email='polish-viewer@test.com',
        sprint_slug=sprint.slug,
        visibility='cohort',
    )
    peer_plan = _create_plan(
        member_email='polish-peer@test.com',
        sprint_slug=sprint.slug,
        visibility='cohort',
    )

    context = _auth_context(browser, 'polish-viewer@test.com')
    try:
        page = context.new_page()
        board_url = f'{django_server}/sprints/{sprint.slug}/board'
        page.goto(board_url, wait_until='domcontentloaded')
        self_row = page.locator(
            f'[data-testid="progress-row-{viewer_plan.member_id}"]',
        )
        peer_row = page.locator(
            f'[data-testid="progress-row-{peer_plan.member_id}"]',
        )
        expect(self_row).to_have_attribute('aria-current', 'true')
        expect(peer_row).not_to_have_attribute('aria-current', 'true')
        assert page.locator('tr[aria-current="true"]').count() == 1

        peer_row.locator('[data-row-title-link="cohort"]').click()
        page.wait_for_url(
            f'{django_server}/sprints/{sprint.slug}/plans/{peer_plan.pk}',
        )
        expect(page.locator('[data-testid="plan-weeks"]')).to_be_visible()

        page.goto(board_url, wait_until='domcontentloaded')
        page.locator(
            f'[data-testid="progress-row-{viewer_plan.member_id}"] '
            '[data-row-title-link="owner"]',
        ).click()
        page.wait_for_url(
            f'{django_server}/sprints/{sprint.slug}/plan/{viewer_plan.pk}',
        )
    finally:
        context.close()


@pytest.mark.django_db(transaction=True)
def test_onboarding_fallback_sentence_and_questions_handoff(
    django_server, browser,
):
    """The polished sentence hands off to the existing questions form.

    Successful form submission remains authoritatively covered by
    ``TestSwitchToForm.test_member_switches_to_form`` in
    ``test_onboarding_ai_804.py``.
    """
    _ensure_tiers()
    _reset()
    _create_user(
        'polish-onboarding@test.com', tier_slug='main', email_verified=True,
    )

    context = _auth_context(browser, 'polish-onboarding@test.com')
    try:
        page = context.new_page()
        with _llm_enabled():
            page.goto(
                f'{django_server}/onboarding/chat',
                wait_until='domcontentloaded',
            )
            link = page.locator('[data-testid="onboarding-switch-to-form"]')
            expect(link).to_have_text('Switch to the questions')
            expect(link.locator('xpath=..')).to_have_text(
                'Prefer a form? Switch to the questions.',
            )
            assert link.evaluate(
                'element => element.nextSibling && element.nextSibling.data',
            ).startswith('.')

            link.click()
            expect(
                page.locator('[data-testid="questionnaire-response-form"]'),
            ).to_be_visible()
            assert page.url == f'{django_server}/onboarding/questions'
    finally:
        context.close()
