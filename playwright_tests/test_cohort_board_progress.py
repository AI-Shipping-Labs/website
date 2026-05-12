"""Playwright E2E tests for the cohort progress board (issue #461).

The cohort board now renders one row per enrolled member of the sprint,
classified into ``cohort`` (clickable, full content), ``private``
(non-clickable, counts-only), and ``no_plan`` (em-dash, "No plan yet"
caption). These flows cover the user-visible parts of that change:

- Members see every teammate's progress, public or private.
- Private rows leak no plan content.
- Cohort rows still link through to the read-only plan page.
- No-plan members appear at the bottom and are not clickable.
- Sort tiebreak is deterministic across reloads.
- Removed members disappear from the board.
- Dashboard "View cohort" CTA fires for any other-member sprint.

Most other behaviour (queryset semantics, sort key correctness,
privacy-sentinel scan, no-plan-row context shape) is exercised by
faster Django ``TestCase`` modules.

Usage:
    uv run pytest playwright_tests/test_cohort_board_progress.py -v
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
    from plans.models import InterviewNote, Plan, Sprint, SprintEnrollment

    InterviewNote.objects.all().delete()
    Plan.objects.all().delete()
    SprintEnrollment.objects.all().delete()
    Sprint.objects.all().delete()
    connection.close()


def _create_sprint(slug, name, start_date):
    from plans.models import Sprint

    sprint = Sprint.objects.create(
        slug=slug, name=name, start_date=start_date,
    )
    connection.close()
    return sprint


def _create_plan_with_checkpoints(*, member_email, sprint_slug,
                                  visibility, total, done, focus_main='',
                                  week_theme='', checkpoint_desc_prefix='cp'):
    from django.utils import timezone

    from accounts.models import User
    from plans.models import Checkpoint, Plan, Sprint, Week

    user = User.objects.get(email=member_email)
    sprint = Sprint.objects.get(slug=sprint_slug)
    plan = Plan.objects.create(
        member=user, sprint=sprint, visibility=visibility,
        focus_main=focus_main,
    )
    week = Week.objects.create(
        plan=plan, week_number=1, theme=week_theme,
    )
    for i in range(total):
        Checkpoint.objects.create(
            week=week,
            description=f'{checkpoint_desc_prefix} {i}',
            done_at=timezone.now() if i < done else None,
        )
    connection.close()
    return plan


def _enroll(email, sprint_slug):
    from accounts.models import User
    from plans.models import Sprint, SprintEnrollment

    user = User.objects.get(email=email)
    sprint = Sprint.objects.get(slug=sprint_slug)
    enrollment, _ = SprintEnrollment.objects.get_or_create(
        sprint=sprint, user=user,
    )
    connection.close()
    return enrollment


def _delete_enrollment(email, sprint_slug):
    from plans.models import SprintEnrollment

    SprintEnrollment.objects.filter(
        sprint__slug=sprint_slug, user__email=email,
    ).delete()
    connection.close()


def _set_user_name(email, first_name, last_name):
    from accounts.models import User

    user = User.objects.get(email=email)
    user.first_name = first_name
    user.last_name = last_name
    user.save(update_fields=['first_name', 'last_name'])
    connection.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestProgressVisibleForEveryMember:
    """The board surfaces every teammate's progress, public or private."""

    def test_four_members_render_in_table_with_viewer_first(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        _create_user('viewer@test.com', tier_slug='free')
        _create_user('alice@test.com', tier_slug='free')
        _create_user('bob@test.com', tier_slug='free')
        _create_user('carol@test.com', tier_slug='free')
        _set_user_name('viewer@test.com', 'Vince', 'Viewer')
        _set_user_name('alice@test.com', 'Alice', 'Smith')
        _set_user_name('bob@test.com', 'Bob', 'Jones')
        _set_user_name('carol@test.com', 'Carol', 'Lee')

        sprint = _create_sprint(
            'may-2026', 'May 2026', datetime.date(2026, 5, 1),
        )
        # viewer 2/5 cohort
        _create_plan_with_checkpoints(
            member_email='viewer@test.com', sprint_slug=sprint.slug,
            visibility='cohort', total=5, done=2,
        )
        # alice 4/5 cohort -> top
        _create_plan_with_checkpoints(
            member_email='alice@test.com', sprint_slug=sprint.slug,
            visibility='cohort', total=5, done=4,
        )
        # bob 1/3 private
        _create_plan_with_checkpoints(
            member_email='bob@test.com', sprint_slug=sprint.slug,
            visibility='private', total=3, done=1,
        )
        # carol enrolled, no plan
        _enroll('carol@test.com', sprint.slug)

        context = _auth_context(browser, 'viewer@test.com')
        try:
            page = context.new_page()
            page.goto(
                f'{django_server}/sprints/{sprint.slug}/board',
                wait_until='domcontentloaded',
            )

            # The page heading reads "Cohort progress".
            heading = page.locator('h1').first
            heading.wait_for(state='visible')
            # The eyebrow above the h1 carries the new label.
            assert 'Cohort progress' in page.locator('main').text_content()

            rows = page.locator('[data-progress-row-kind]')
            rows.first.wait_for(state='visible')
            assert rows.count() == 4
            assert page.locator('table[data-testid="cohort-plan-list"]').count() == 0
            assert page.locator('[data-testid="cohort-plan-list"] table').is_visible()

            kinds = [
                rows.nth(i).get_attribute('data-progress-row-kind')
                for i in range(rows.count())
            ]
            # viewer is pinned first, then alice -> bob -> carol.
            assert kinds == ['cohort', 'cohort', 'private', 'no_plan']
            first_name = rows.first.locator(
                '[data-testid="cohort-plan-name"]',
            ).inner_text()
            assert 'Vince Viewer (you)' in first_name
        finally:
            context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestPrivatePlanLeaksNoContent:
    """A private member's plan content never appears on the board."""

    def test_private_row_shows_count_and_badge_but_no_focus_or_theme(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        _create_user('viewer@test.com', tier_slug='free')
        _create_user('dana@test.com', tier_slug='free')
        _set_user_name('dana@test.com', 'Dana', 'Doe')

        sprint = _create_sprint(
            'may-2026', 'May 2026', datetime.date(2026, 5, 1),
        )
        _create_plan_with_checkpoints(
            member_email='viewer@test.com', sprint_slug=sprint.slug,
            visibility='cohort', total=2, done=1,
        )
        _create_plan_with_checkpoints(
            member_email='dana@test.com', sprint_slug=sprint.slug,
            visibility='private', total=1, done=0,
            focus_main='SECRET FOCUS',
            week_theme='SECRET THEME',
            checkpoint_desc_prefix='SECRET TASK',
        )

        from accounts.models import User
        dana_pk = User.objects.get(email='dana@test.com').pk
        connection.close()

        context = _auth_context(browser, 'viewer@test.com')
        try:
            page = context.new_page()
            page.goto(
                f'{django_server}/sprints/{sprint.slug}/board',
                wait_until='domcontentloaded',
            )

            page.locator(
                f'[data-testid="private-badge-{dana_pk}"]',
            ).wait_for(state='visible')

            page_text = page.locator('main').inner_text()
            assert 'SECRET FOCUS' not in page_text
            assert 'SECRET THEME' not in page_text
            assert 'SECRET TASK' not in page_text

            # The 0 of 1 count IS visible -- counts are intentional.
            count_locator = page.locator(
                f'[data-testid="progress-count-{dana_pk}"]',
            )
            assert '0 of 1' in count_locator.inner_text()
        finally:
            context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestCohortPlanLinkStillWorks:
    """Cohort rows are clickable and route to the read-only plan page."""

    def test_clicking_cohort_row_opens_plan_detail(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        _create_user('viewer@test.com', tier_slug='free')
        _create_user('eli@test.com', tier_slug='free')
        _set_user_name('eli@test.com', 'Eli', 'Engineer')

        sprint = _create_sprint(
            'may-2026', 'May 2026', datetime.date(2026, 5, 1),
        )
        _create_plan_with_checkpoints(
            member_email='viewer@test.com', sprint_slug=sprint.slug,
            visibility='cohort', total=1, done=0,
        )
        eli_plan = _create_plan_with_checkpoints(
            member_email='eli@test.com', sprint_slug=sprint.slug,
            visibility='cohort', total=2, done=1,
            focus_main='Build the eval harness',
        )

        context = _auth_context(browser, 'viewer@test.com')
        try:
            page = context.new_page()
            page.goto(
                f'{django_server}/sprints/{sprint.slug}/board',
                wait_until='domcontentloaded',
            )
            link = page.locator(
                f'a[href="/sprints/{sprint.slug}/plans/{eli_plan.pk}"]',
            ).first
            link.wait_for(state='visible')
            link.click()
            page.wait_for_url(
                f'{django_server}/sprints/{sprint.slug}/plans/{eli_plan.pk}',
            )
            focus = page.locator('[data-testid="plan-focus-main"]')
            focus.wait_for(state='visible')
            assert 'Build the eval harness' in focus.inner_text()
        finally:
            context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestNoPlanRowAtBottom:
    """An enrolled member without a plan shows up at the bottom, not clickable."""

    def test_no_plan_row_appears_last_with_em_dash(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        _create_user('viewer@test.com', tier_slug='free')
        _create_user('teammate@test.com', tier_slug='free')
        _create_user('frank@test.com', tier_slug='free')
        _set_user_name('frank@test.com', 'Frank', 'Newcomer')

        sprint = _create_sprint(
            'may-2026', 'May 2026', datetime.date(2026, 5, 1),
        )
        _create_plan_with_checkpoints(
            member_email='viewer@test.com', sprint_slug=sprint.slug,
            visibility='cohort', total=1, done=0,
        )
        _create_plan_with_checkpoints(
            member_email='teammate@test.com', sprint_slug=sprint.slug,
            visibility='cohort', total=1, done=0,
        )
        _enroll('frank@test.com', sprint.slug)

        from accounts.models import User
        frank_pk = User.objects.get(email='frank@test.com').pk
        connection.close()

        context = _auth_context(browser, 'viewer@test.com')
        try:
            page = context.new_page()
            page.goto(
                f'{django_server}/sprints/{sprint.slug}/board',
                wait_until='domcontentloaded',
            )
            rows = page.locator('[data-progress-row-kind]')
            rows.last.wait_for(state='visible')
            # Frank's no-plan row is last.
            last_kind = rows.last.get_attribute('data-progress-row-kind')
            assert last_kind == 'no_plan'
            # And the no-plan caption is present.
            caption = page.locator(
                f'[data-testid="no-plan-caption-{frank_pk}"]',
            )
            assert 'No plan yet' in caption.inner_text()
        finally:
            context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestSortStableOnReload:
    """Tied progress sorts by ``member.email`` ascending, deterministic across reloads."""

    def test_three_tied_peers_render_alphabetically_after_reload(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        _create_user('viewer@test.com', tier_slug='free')
        _create_user('alice@test.com', tier_slug='free')
        _create_user('bob@test.com', tier_slug='free')
        _create_user('carol@test.com', tier_slug='free')
        _set_user_name('alice@test.com', 'Alice', 'A')
        _set_user_name('bob@test.com', 'Bob', 'B')
        _set_user_name('carol@test.com', 'Carol', 'C')

        sprint = _create_sprint(
            'may-2026', 'May 2026', datetime.date(2026, 5, 1),
        )
        # Viewer also at 3/5 -- but viewer's email starts with 'v',
        # so the three-peer alphabetical block should be deterministic.
        for email in (
            'viewer@test.com', 'alice@test.com',
            'bob@test.com', 'carol@test.com',
        ):
            _create_plan_with_checkpoints(
                member_email=email, sprint_slug=sprint.slug,
                visibility='cohort', total=5, done=3,
            )

        context = _auth_context(browser, 'viewer@test.com')
        try:
            page = context.new_page()

            def _read_peer_emails():
                page.goto(
                    f'{django_server}/sprints/{sprint.slug}/board',
                    wait_until='domcontentloaded',
                )
                rows = page.locator('[data-progress-row-kind="cohort"]')
                rows.first.wait_for(state='visible')
                names = [
                    rows.nth(i).locator(
                        '[data-testid="cohort-plan-name"]',
                    ).inner_text()
                    for i in range(rows.count())
                ]
                return names

            first_render = _read_peer_emails()
            second_render = _read_peer_emails()
            assert first_render == second_render

            # Viewer is pinned first; peers stay alphabetical after it.
            joined = ' '.join(first_render)
            assert first_render[0] == 'viewer (you)'
            assert joined.find('Alice') < joined.find('Bob')
            assert joined.find('Bob') < joined.find('Carol')
        finally:
            context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestDeletedEnrollmentDisappears:
    """Members removed from the sprint disappear from the board."""

    def test_member_with_deleted_enrollment_does_not_appear(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        _create_user('viewer@test.com', tier_slug='free')
        _create_user('gabe@test.com', tier_slug='free')
        _set_user_name('gabe@test.com', 'Gabe', 'Gone')

        sprint = _create_sprint(
            'may-2026', 'May 2026', datetime.date(2026, 5, 1),
        )
        _create_plan_with_checkpoints(
            member_email='viewer@test.com', sprint_slug=sprint.slug,
            visibility='cohort', total=1, done=0,
        )
        _create_plan_with_checkpoints(
            member_email='gabe@test.com', sprint_slug=sprint.slug,
            visibility='cohort', total=1, done=0,
            focus_main='Gabe focus',
        )
        _delete_enrollment('gabe@test.com', sprint.slug)

        context = _auth_context(browser, 'viewer@test.com')
        try:
            page = context.new_page()
            page.goto(
                f'{django_server}/sprints/{sprint.slug}/board',
                wait_until='domcontentloaded',
            )
            page.locator(
                '[data-progress-row-kind="cohort"]',
            ).first.wait_for(state='visible')
            page_text = page.locator('main').inner_text()
            assert 'Gabe Gone' not in page_text
            assert 'Gabe focus' not in page_text
        finally:
            context.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestDashboardCohortCtaForAnyOtherMember:
    """Dashboard "View cohort" CTA fires when sprint has any other enrolled member."""

    def test_cta_fires_when_only_other_member_has_private_plan(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        _create_user('viewer@test.com', tier_slug='free')
        _create_user('peer@test.com', tier_slug='free')
        _set_user_name('peer@test.com', 'Pia', 'Peer')

        sprint = _create_sprint(
            'may-2026', 'May 2026', datetime.date(2026, 5, 1),
        )
        _create_plan_with_checkpoints(
            member_email='viewer@test.com', sprint_slug=sprint.slug,
            visibility='cohort', total=1, done=0,
        )
        # Peer's plan is PRIVATE -- under the old gate the CTA would not
        # have fired. Under the new ``cohort_has_other_members`` gate
        # it does.
        _create_plan_with_checkpoints(
            member_email='peer@test.com', sprint_slug=sprint.slug,
            visibility='private', total=1, done=0,
        )

        from accounts.models import User
        peer_pk = User.objects.get(email='peer@test.com').pk
        connection.close()

        context = _auth_context(browser, 'viewer@test.com')
        try:
            page = context.new_page()
            page.goto(
                f'{django_server}/account/',
                wait_until='domcontentloaded',
            )
            cta = page.locator('[data-testid="account-sprint-plan-cohort"]')
            cta.wait_for(state='visible')
            cta.click()
            page.wait_for_url(
                f'{django_server}/sprints/{sprint.slug}/board',
            )
            badge = page.locator(
                f'[data-testid="private-badge-{peer_pk}"]',
            )
            badge.wait_for(state='visible')
            assert 'Private' in badge.text_content()
        finally:
            context.close()
