"""Playwright E2E tests for the cohort board (issue #440).

Three flows that cross multiple pages and matter for the user story:

1. Member opts in to cohort visibility from the toggle, then their
   teammate's plan is what they see on the board (their own plan is
   excluded from the main grid).
2. A member of a different sprint cannot reach the board (404), even
   when the target plan is cohort-visible.
3. Visiting another member's read-only plan page from the board
   renders the focus text and does NOT render any visibility toggle
   or interview-note section.

Most other behaviours (queryset visibility rules, 404 vs redirect
semantics, side-effect-free rejection of bad POSTs, display-name
fallbacks, regression scan for visibility literals in views) are
faster and more reliable as Django ``TestCase`` modules.

Usage:
    uv run pytest playwright_tests/test_cohort_board.py -v
"""

import datetime
import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402


def _clear_plans_data():
    from plans.models import InterviewNote, Plan, Sprint

    InterviewNote.objects.all().delete()
    Plan.objects.all().delete()
    Sprint.objects.all().delete()
    connection.close()


def _create_sprint(slug, name, start_date):
    from plans.models import Sprint

    sprint = Sprint.objects.create(
        slug=slug, name=name, start_date=start_date,
    )
    connection.close()
    return sprint


def _create_plan(*, member_email, sprint_slug, visibility, focus_main=''):
    from accounts.models import User
    from plans.models import Plan, Sprint

    user = User.objects.get(email=member_email)
    sprint = Sprint.objects.get(slug=sprint_slug)
    plan = Plan.objects.create(
        member=user, sprint=sprint, visibility=visibility,
        focus_main=focus_main,
    )
    connection.close()
    return plan


def _create_plan_with_checkpoints(*, member_email, sprint_slug,
                                  visibility, total, done, focus_main=''):
    from django.utils import timezone

    from accounts.models import User
    from plans.models import Checkpoint, Plan, Sprint, Week

    user = User.objects.get(email=member_email)
    sprint = Sprint.objects.get(slug=sprint_slug)
    plan = Plan.objects.create(
        member=user, sprint=sprint, visibility=visibility,
        focus_main=focus_main,
    )
    week = Week.objects.create(plan=plan, week_number=1)
    for i in range(total):
        Checkpoint.objects.create(
            week=week,
            description=f'cp {i}',
            done_at=timezone.now() if i < done else None,
        )
    connection.close()
    return plan


def _set_user_name(email, first_name, last_name):
    from accounts.models import User

    user = User.objects.get(email=email)
    user.first_name = first_name
    user.last_name = last_name
    user.save(update_fields=['first_name', 'last_name'])
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestMemberOptsInToCohortVisibility:
    """Member toggles their plan to cohort and sees the board reflect it."""

    def test_visibility_toggle_makes_plan_appear_on_others_boards(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        _create_user('alice@test.com', tier_slug='free')
        _create_user('viewer@test.com', tier_slug='free')
        _set_user_name('alice@test.com', 'Alice', 'Smith')

        sprint = _create_sprint(
            'may-2026', 'May 2026', datetime.date(2026, 5, 1),
        )
        # Alice already cohort-visible with progress.
        _create_plan_with_checkpoints(
            member_email='alice@test.com',
            sprint_slug=sprint.slug,
            visibility='cohort',
            total=12,
            done=6,
            focus_main='Ship the SME agent prototype',
        )
        # Viewer starts private with no checkpoints.
        viewer_plan = _create_plan(
            member_email='viewer@test.com',
            sprint_slug=sprint.slug,
            visibility='private',
        )

        context = _auth_context(browser, 'viewer@test.com')
        try:
            page = context.new_page()

            # Step 1: viewer lands on the cohort board. Alice's cohort
            # card is visible alongside the viewer's own private row,
            # and the private callout points at the visibility toggle.
            page.goto(
                f'{django_server}/sprints/{sprint.slug}/board',
                wait_until='domcontentloaded',
            )
            cohort_cards = page.locator('[data-progress-row-kind="cohort"]')
            cohort_cards.first.wait_for(state='visible')
            assert cohort_cards.count() == 1
            assert 'Alice Smith' in cohort_cards.first.inner_text()
            # Alice's progress reads ``6 of 12`` somewhere on her card.
            assert '6 of 12' in cohort_cards.first.inner_text()

            callout = page.locator('[data-testid="viewer-plan-callout"]')
            callout.wait_for(state='visible')
            assert 'private' in callout.inner_text().lower()

            # Step 2: click the callout link to /account/plan/<id>/.
            callout.locator('a').first.click()
            page.wait_for_url(f'{django_server}/account/plan/{viewer_plan.pk}')

            # Step 3: switch the visibility selector to ``cohort`` and save.
            select = page.locator('[data-testid="visibility-select"]')
            select.select_option('cohort')
            page.locator('[data-testid="visibility-save"]').click()
            page.wait_for_url(
                f'{django_server}/account/plan/{viewer_plan.pk}',
            )

            # Step 4: success message rendered, selector now reads cohort.
            messages = page.locator('[data-testid="plan-message"]')
            messages.first.wait_for(state='visible')
            assert 'updated' in messages.first.inner_text().lower()
            assert page.locator(
                '[data-testid="visibility-select"]',
            ).input_value() == 'cohort'

            # Step 5: navigate back to the board. The viewer's row now
            # renders as a cohort kind too, so there are two cohort
            # cards. Alice's is still there.
            page.goto(
                f'{django_server}/sprints/{sprint.slug}/board',
                wait_until='domcontentloaded',
            )
            page.locator(
                '[data-progress-row-kind="cohort"]',
            ).first.wait_for(state='visible')
            cohort_after = page.locator('[data-progress-row-kind="cohort"]')
            assert cohort_after.count() == 2
            names = [
                cohort_after.nth(i).locator(
                    '[data-testid="cohort-plan-name"]',
                ).inner_text()
                for i in range(cohort_after.count())
            ]
            assert any('Alice Smith' in n for n in names)
            # Viewer's own cohort row carries the "(you)" suffix.
            assert any('(you)' in n for n in names)
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestOutsiderCannotReachBoard:
    """A member of a different sprint sees 404, regardless of visibility."""

    def test_outsider_gets_404_on_board_and_plan_pages(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        _create_user('alice@test.com', tier_slug='free')
        _create_user('outsider@test.com', tier_slug='free')

        may_sprint = _create_sprint(
            'may-2026', 'May 2026', datetime.date(2026, 5, 1),
        )
        june_sprint = _create_sprint(
            'june-2026', 'June 2026', datetime.date(2026, 6, 1),
        )
        alice_plan = _create_plan(
            member_email='alice@test.com',
            sprint_slug=may_sprint.slug,
            visibility='cohort',
        )
        # Outsider is enrolled only in the June sprint.
        _create_plan(
            member_email='outsider@test.com',
            sprint_slug=june_sprint.slug,
            visibility='cohort',
        )

        context = _auth_context(browser, 'outsider@test.com')
        try:
            page = context.new_page()
            board_response = page.goto(
                f'{django_server}/sprints/{may_sprint.slug}/board',
                wait_until='domcontentloaded',
            )
            assert board_response is not None
            assert board_response.status == 404

            plan_response = page.goto(
                f'{django_server}/sprints/{may_sprint.slug}/plans/'
                f'{alice_plan.pk}',
                wait_until='domcontentloaded',
            )
            assert plan_response is not None
            assert plan_response.status == 404
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestReadOnlyPlanPage:
    """Visiting another member's plan from the board: focus + no toggle."""

    def test_teammate_plan_renders_without_toggle_or_notes(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        _create_user('alice@test.com', tier_slug='free')
        _create_user('bob@test.com', tier_slug='free')
        _set_user_name('alice@test.com', 'Alice', 'Smith')

        sprint = _create_sprint(
            'may-2026', 'May 2026', datetime.date(2026, 5, 1),
        )
        alice_plan = _create_plan(
            member_email='alice@test.com',
            sprint_slug=sprint.slug,
            visibility='cohort',
            focus_main='Ship the SME agent prototype',
        )
        _create_plan(
            member_email='bob@test.com',
            sprint_slug=sprint.slug,
            visibility='cohort',
        )

        context = _auth_context(browser, 'bob@test.com')
        try:
            page = context.new_page()

            page.goto(
                f'{django_server}/sprints/{sprint.slug}/board',
                wait_until='domcontentloaded',
            )
            page.locator(
                '[data-progress-row-kind="cohort"]',
            ).first.wait_for(state='visible')

            # Click Alice's card.
            page.locator(
                f'a[href="/sprints/{sprint.slug}/plans/{alice_plan.pk}"]',
            ).first.click()
            page.wait_for_url(
                f'{django_server}/sprints/{sprint.slug}/plans/'
                f'{alice_plan.pk}',
            )

            # Focus text rendered.
            assert 'Ship the SME agent prototype' in page.locator(
                '[data-testid="plan-focus-main"]',
            ).inner_text()

            # No visibility toggle.
            assert page.locator(
                '[data-testid="plan-visibility-form"]',
            ).count() == 0

            # No interview notes (in either visibility) -- the partial
            # used by this template explicitly omits them.
            body = page.locator('body').inner_text().lower()
            assert 'interview note' not in body
        finally:
            context.close()
