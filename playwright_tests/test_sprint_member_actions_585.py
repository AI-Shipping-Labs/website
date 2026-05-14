"""Playwright E2E tests for sprint-member actions (issue #585).

Three sprint-membership UX gaps are exercised end-to-end here:

1. Pending member asks the team to plan with them (button -> success
   message -> server-rendered disabled state that survives reload).
2. Member leaves a sprint via the cohort board (browser confirm
   dialog -> redirect -> board returns 404 to the leaver -> plan row
   preserved as private).
3. Staff sees the per-row admin link on the cohort board AND on the
   plan views; non-staff never see any admin link.

Most server-side enforcement (rate limiting, 404 vs login redirect,
notification fan-out, email content) lives in
``plans/tests/test_views_ask_team.py`` -- those checks are faster and
more reliable as Django ``TestCase`` modules. These E2E tests cover
the JS confirm dialog and the cross-page user flow that the test
client cannot reproduce.

Usage:
    uv run pytest playwright_tests/test_sprint_member_actions_585.py -v
"""

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

# --------------------------------------------------------------------------
# Test fixtures
# --------------------------------------------------------------------------


def _clear_plans_data():
    from notifications.models import Notification
    from plans.models import (
        InterviewNote,
        Plan,
        PlanRequest,
        Sprint,
        SprintEnrollment,
    )

    Notification.objects.filter(notification_type='plan_request').delete()
    PlanRequest.objects.all().delete()
    InterviewNote.objects.all().delete()
    Plan.objects.all().delete()
    SprintEnrollment.objects.all().delete()
    Sprint.objects.all().delete()
    connection.close()


def _create_sprint(slug, name, start_date):
    from plans.models import Sprint

    sprint = Sprint.objects.create(
        slug=slug, name=name, start_date=start_date,
        status='active', min_tier_level=0,
    )
    connection.close()
    return sprint


def _enroll(email, sprint_slug):
    from accounts.models import User
    from plans.models import Sprint, SprintEnrollment

    user = User.objects.get(email=email)
    sprint = Sprint.objects.get(slug=sprint_slug)
    SprintEnrollment.objects.get_or_create(sprint=sprint, user=user)
    connection.close()


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


def _set_user_name(email, first_name, last_name):
    from accounts.models import User

    user = User.objects.get(email=email)
    user.first_name = first_name
    user.last_name = last_name
    user.save(update_fields=['first_name', 'last_name'])
    connection.close()


def _create_plan_request(member_email, sprint_slug):
    from accounts.models import User
    from plans.models import PlanRequest, Sprint

    user = User.objects.get(email=member_email)
    sprint = Sprint.objects.get(slug=sprint_slug)
    pr = PlanRequest.objects.create(sprint=sprint, member=user)
    connection.close()
    return pr


def _count_plan_requests(sprint_slug, member_email):
    from accounts.models import User
    from plans.models import PlanRequest, Sprint

    user = User.objects.get(email=member_email)
    sprint = Sprint.objects.get(slug=sprint_slug)
    n = PlanRequest.objects.filter(sprint=sprint, member=user).count()
    connection.close()
    return n


def _plan_visibility(plan_pk):
    from plans.models import Plan

    visibility = Plan.objects.get(pk=plan_pk).visibility
    connection.close()
    return visibility


# --------------------------------------------------------------------------
# Sub-feature 1: Ask the team
# --------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestStuckMemberAsksTeamFromBoard:
    """Pending member with no plan asks the team and sees disabled button."""

    def test_ask_team_button_post_then_disabled_state_persists(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        _create_user('alex@test.com', tier_slug='premium')
        _set_user_name('alex@test.com', 'Alex', 'Member')
        # Staff user so the notification fanout has a recipient (also
        # silences the staff-fanout side of the view).
        _create_staff_user('staff-585@test.com')

        sprint = _create_sprint(
            'may-2026', 'May 2026', datetime.date(2026, 5, 1),
        )
        _enroll('alex@test.com', sprint.slug)

        context = _auth_context(browser, 'alex@test.com')
        try:
            page = context.new_page()
            page.goto(
                f'{django_server}/sprints/{sprint.slug}/board',
                wait_until='domcontentloaded',
            )
            ask_buttons = page.locator(
                '[data-testid="ask-team-button"]',
            )
            ask_buttons.first.wait_for(state='visible')

            # The viewer's own row + the viewer-plan-pending callout
            # both render an enabled "Ask the team" button before the
            # ping is sent.
            assert ask_buttons.count() >= 1

            # Click the first one (the callout aside button).
            ask_buttons.first.click()

            # Server redirect lands back on the board with a flash
            # message rendered in the messages area.
            page.wait_for_url(
                f'{django_server}/sprints/{sprint.slug}/board',
            )
            success = page.locator(
                '[data-testid="cohort-board-message"]',
            )
            success.first.wait_for(state='visible')
            assert "Asked the team" in success.first.inner_text()

            # The button is now in disabled state with the "Pinged"
            # caption -- both renders carry the same data-testid.
            disabled_buttons = page.locator(
                '[data-testid="ask-team-button"]',
            )
            disabled_buttons.first.wait_for(state='visible')
            assert disabled_buttons.count() >= 1
            for i in range(disabled_buttons.count()):
                btn = disabled_buttons.nth(i)
                assert btn.is_disabled(), (
                    f'Button {i} expected disabled after ping'
                )
                assert 'Pinged the team' in btn.inner_text()

            # Reload -- disabled state is server-rendered, so it
            # survives the navigation.
            page.reload(wait_until='domcontentloaded')
            disabled_after_reload = page.locator(
                '[data-testid="ask-team-button"]',
            )
            disabled_after_reload.first.wait_for(state='visible')
            assert disabled_after_reload.first.is_disabled()
            assert 'Pinged the team' in (
                disabled_after_reload.first.inner_text()
            )

            # Exactly one PlanRequest row was recorded.
            assert _count_plan_requests(sprint.slug, 'alex@test.com') == 1
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestRateLimitedMember:
    """Pre-existing PlanRequest within 24h => disabled UI; server enforces."""

    def test_disabled_ui_renders_when_recent_ping_exists(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        _create_user('alex@test.com', tier_slug='premium')
        sprint = _create_sprint(
            'may-2026', 'May 2026', datetime.date(2026, 5, 1),
        )
        _enroll('alex@test.com', sprint.slug)
        _create_plan_request('alex@test.com', sprint.slug)

        context = _auth_context(browser, 'alex@test.com')
        try:
            page = context.new_page()
            page.goto(
                f'{django_server}/sprints/{sprint.slug}/board',
                wait_until='domcontentloaded',
            )
            disabled_button = page.locator(
                '[data-testid="ask-team-button"]',
            ).first
            disabled_button.wait_for(state='visible')
            # Server-rendered disabled state.
            assert disabled_button.is_disabled()
            # Still only one PlanRequest row -- viewing the page does
            # not create one.
            assert _count_plan_requests(sprint.slug, 'alex@test.com') == 1
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestMemberWithPlanSeesNoAskButton:
    """Once a plan exists, the ping button is gone everywhere."""

    def test_plan_owner_does_not_see_ask_team_button(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        _create_user('alex@test.com', tier_slug='premium')
        sprint = _create_sprint(
            'may-2026', 'May 2026', datetime.date(2026, 5, 1),
        )
        _create_plan(
            member_email='alex@test.com',
            sprint_slug=sprint.slug,
            visibility='cohort',
        )

        context = _auth_context(browser, 'alex@test.com')
        try:
            page = context.new_page()
            page.goto(
                f'{django_server}/sprints/{sprint.slug}/board',
                wait_until='domcontentloaded',
            )
            page.locator(
                '[data-testid="viewer-plan-callout"]',
            ).wait_for(state='visible')
            assert page.locator(
                '[data-testid="ask-team-button"]',
            ).count() == 0
            # The existing "Manage visibility" link is still there.
            callout_text = page.locator(
                '[data-testid="viewer-plan-callout"]',
            ).inner_text()
            assert 'Manage visibility' in callout_text
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestStaffSeesPlanRequestNotification:
    """Staff sees the in-app notification and clicks through to admin."""

    def test_staff_notification_links_to_admin_user_page(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        from notifications.models import Notification

        Notification.objects.filter(
            notification_type='plan_request',
        ).delete()
        connection.close()

        _create_user('alex@test.com', tier_slug='premium')
        _set_user_name('alex@test.com', 'Alex', 'Member')
        from accounts.models import User as _U
        alex_user = _U.objects.get(email='alex@test.com')
        connection.close()

        _create_staff_user('staff-585@test.com')
        sprint = _create_sprint(
            'may-2026', 'May 2026', datetime.date(2026, 5, 1),
        )
        _enroll('alex@test.com', sprint.slug)

        # Trigger the ping via the real endpoint (so the same fan-out
        # code runs).
        member_context = _auth_context(browser, 'alex@test.com')
        try:
            page = member_context.new_page()
            page.goto(
                f'{django_server}/sprints/{sprint.slug}/board',
                wait_until='domcontentloaded',
            )
            page.locator(
                '[data-testid="ask-team-button"]',
            ).first.click()
            page.wait_for_url(
                f'{django_server}/sprints/{sprint.slug}/board',
            )
        finally:
            member_context.close()

        # Staff visits /notifications and finds the new ping.
        staff_context = _auth_context(browser, 'staff-585@test.com')
        try:
            page = staff_context.new_page()
            page.goto(
                f'{django_server}/notifications',
                wait_until='domcontentloaded',
            )
            body_text = page.locator('body').inner_text()
            assert 'Plan request from' in body_text
            assert 'Alex Member' in body_text

            # Look for the link with the admin user URL pointing at the
            # requesting member's user pk.
            link = page.locator(
                f'a[href*="/admin/accounts/user/{alex_user.pk}/change/"]',
            ).first
            link.wait_for(state='visible')
        finally:
            staff_context.close()


# --------------------------------------------------------------------------
# Sub-feature 2: Leave sprint
# --------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestLeaveFromCohortBoardWithConfirm:
    """Click Leave -> accept dialog -> redirect -> board returns 404."""

    @pytest.mark.core
    def test_leave_after_confirm_unenrolls_and_privates_plan(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        _create_user('alex@test.com', tier_slug='premium')
        sprint = _create_sprint(
            'may-2026', 'May 2026', datetime.date(2026, 5, 1),
        )
        plan = _create_plan(
            member_email='alex@test.com',
            sprint_slug=sprint.slug,
            visibility='cohort',
        )

        context = _auth_context(browser, 'alex@test.com')
        try:
            page = context.new_page()
            page.goto(
                f'{django_server}/sprints/{sprint.slug}/board',
                wait_until='domcontentloaded',
            )
            leave_btn = page.locator(
                '[data-testid="cohort-board-leave-sprint"]',
            ).first
            leave_btn.wait_for(state='visible')

            # Auto-accept the JS confirm() dialog.
            page.once('dialog', lambda dlg: dlg.accept())
            leave_btn.click()

            # After leaving, redirected to the sprint detail page.
            page.wait_for_url(
                f'{django_server}/sprints/{sprint.slug}',
            )
            assert page.locator(
                '[data-testid="sprint-detail-name"]',
            ).inner_text() == 'May 2026'

            # Going back to the board now 404s for the leaver.
            response = page.goto(
                f'{django_server}/sprints/{sprint.slug}/board',
                wait_until='domcontentloaded',
            )
            assert response is not None
            assert response.status == 404

            # Plan row preserved, visibility forced private.
            assert _plan_visibility(plan.pk) == 'private'
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestLeaveCancelDialog:
    """Click Leave -> cancel dialog -> nothing happens."""

    def test_cancel_dialog_keeps_member_enrolled(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        _create_user('alex@test.com', tier_slug='premium')
        sprint = _create_sprint(
            'may-2026', 'May 2026', datetime.date(2026, 5, 1),
        )
        _enroll('alex@test.com', sprint.slug)

        context = _auth_context(browser, 'alex@test.com')
        try:
            page = context.new_page()
            page.goto(
                f'{django_server}/sprints/{sprint.slug}',
                wait_until='domcontentloaded',
            )
            leave_btn = page.locator(
                '[data-testid="sprint-cta-leave"]',
            ).first
            leave_btn.wait_for(state='visible')

            # Dismiss the JS confirm() dialog.
            page.once('dialog', lambda dlg: dlg.dismiss())
            leave_btn.click()

            # Page did not navigate, member still enrolled.
            page.wait_for_timeout(200)
            # The cohort board still returns 200 -- still enrolled.
            response = page.goto(
                f'{django_server}/sprints/{sprint.slug}/board',
                wait_until='domcontentloaded',
            )
            assert response is not None
            assert response.status == 200
        finally:
            context.close()


# --------------------------------------------------------------------------
# Sub-feature 3: user-facing pages hide admin actions
# --------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestStaffDoesNotSeePlanAdminActions:
    """Staff uses Studio for admin actions, not member-facing Plans pages."""

    def test_staff_admin_links_absent_everywhere(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        _create_user('alex@test.com', tier_slug='premium')
        _set_user_name('alex@test.com', 'Alex', 'Member')
        _create_staff_user('staff-585@test.com')

        sprint = _create_sprint(
            'may-2026', 'May 2026', datetime.date(2026, 5, 1),
        )
        plan = _create_plan(
            member_email='alex@test.com',
            sprint_slug=sprint.slug,
            visibility='cohort',
        )
        # Staff also enrolled so they can see the cohort board.
        _enroll('staff-585@test.com', sprint.slug)

        context = _auth_context(browser, 'staff-585@test.com')
        try:
            page = context.new_page()
            page.goto(
                f'{django_server}/sprints/{sprint.slug}/board',
                wait_until='domcontentloaded',
            )
            from accounts.models import User as _U
            alex_pk = _U.objects.get(email='alex@test.com').pk
            connection.close()

            assert page.locator(
                f'[data-testid="cohort-row-admin-link-{alex_pk}"]',
            ).count() == 0
            assert page.locator(
                '[data-testid^="cohort-row-admin-link-"]',
            ).count() == 0

            page.goto(
                f'{django_server}/sprints/{sprint.slug}/plans/{plan.pk}',
                wait_until='domcontentloaded',
            )
            assert page.locator('[data-testid="plan-admin-link"]').count() == 0
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestNonStaffNeverSeesAdminLink:
    """Non-staff sees no admin link on the board, own plan, or teammate plan."""

    def test_non_staff_admin_links_absent_everywhere(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        _create_user('alex@test.com', tier_slug='premium')
        _create_user('bob@test.com', tier_slug='premium')
        _set_user_name('alex@test.com', 'Alex', 'Member')
        _set_user_name('bob@test.com', 'Bob', 'Buddy')
        sprint = _create_sprint(
            'may-2026', 'May 2026', datetime.date(2026, 5, 1),
        )
        alex_plan = _create_plan(
            member_email='alex@test.com',
            sprint_slug=sprint.slug,
            visibility='cohort',
            focus_main='Ship the SME agent prototype',
        )
        bob_plan = _create_plan(
            member_email='bob@test.com',
            sprint_slug=sprint.slug,
            visibility='cohort',
            focus_main='Roll out the assistant',
        )

        context = _auth_context(browser, 'alex@test.com')
        try:
            page = context.new_page()

            # Cohort board.
            page.goto(
                f'{django_server}/sprints/{sprint.slug}/board',
                wait_until='domcontentloaded',
            )
            assert page.locator(
                '[data-testid^="cohort-row-admin-link-"]',
            ).count() == 0

            # Own plan view.
            page.goto(
                f'{django_server}/sprints/{sprint.slug}/plan/{alex_plan.pk}',
                wait_until='domcontentloaded',
            )
            assert page.locator(
                '[data-testid="plan-admin-link"]',
            ).count() == 0

            # Teammate plan read-only view.
            page.goto(
                f'{django_server}/sprints/{sprint.slug}/plans/{bob_plan.pk}',
                wait_until='domcontentloaded',
            )
            assert page.locator(
                '[data-testid="plan-admin-link"]',
            ).count() == 0
        finally:
            context.close()


# --------------------------------------------------------------------------
# Cross-cutting: anonymous + non-enrolled cannot ping
# --------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestAnonymousCannotPing:
    """Anonymous POST -> redirect to login, no PlanRequest created."""

    def test_anonymous_post_redirected_to_login(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        sprint = _create_sprint(
            'may-2026', 'May 2026', datetime.date(2026, 5, 1),
        )

        context = browser.new_context(
            viewport={'width': 1280, 'height': 720},
        )
        csrf = 'a' * 32
        context.add_cookies([{
            'name': 'csrftoken',
            'value': csrf,
            'domain': '127.0.0.1',
            'path': '/',
        }])
        try:
            page = context.new_page()
            page.goto(
                f'{django_server}/sprints/{sprint.slug}',
                wait_until='domcontentloaded',
            )

            response = page.request.post(
                f'{django_server}/sprints/{sprint.slug}/ask-team',
                headers={
                    'X-CSRFToken': csrf,
                    'Referer': f'{django_server}/sprints/{sprint.slug}',
                },
                max_redirects=0,
            )
            assert response.status == 302
            assert '/accounts/login/' in response.headers.get('location', '')

            from notifications.models import Notification
            from plans.models import PlanRequest
            assert PlanRequest.objects.count() == 0
            assert Notification.objects.filter(
                notification_type='plan_request',
            ).count() == 0
            connection.close()
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestNonEnrolledCannotPing:
    """Logged-in non-enrolled member POST -> 404, no PlanRequest created."""

    def test_non_enrolled_post_returns_404(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plans_data()
        _create_user('alex@test.com', tier_slug='premium')
        sprint = _create_sprint(
            'may-2026', 'May 2026', datetime.date(2026, 5, 1),
        )
        # NOT enrolling Alex.

        context = _auth_context(browser, 'alex@test.com')
        try:
            # Get a fresh CSRF token by visiting any page first.
            page = context.new_page()
            page.goto(
                f'{django_server}/sprints/{sprint.slug}',
                wait_until='domcontentloaded',
            )
            csrf = None
            for cookie in context.cookies():
                if cookie['name'] == 'csrftoken':
                    csrf = cookie['value']
                    break
            assert csrf is not None

            response = page.request.post(
                f'{django_server}/sprints/{sprint.slug}/ask-team',
                headers={
                    'X-CSRFToken': csrf,
                    'Referer': f'{django_server}/sprints/{sprint.slug}',
                },
            )
            assert response.status == 404

            from plans.models import PlanRequest
            assert PlanRequest.objects.count() == 0
            connection.close()
        finally:
            context.close()
