"""E2E: dismissable / stale dashboard cards (issue #1129).

Covers the user-visible contract for the three returning-member dashboard
cleanups, all rendered from ``templates/content/dashboard.html``:

- Part 1: the onboarding nudge has a dismiss control; dismissing it removes
  the card without a navigation and it stays gone across reloads/sessions
  (persisted server-side, not localStorage).
- Part 2: the Join-Slack card has a dismiss control ONLY on the dashboard;
  dismissing it does not touch the /account/ Slack surface.
- Part 3: a plan on an ended sprint is framed as a past plan (``Ended``
  label, ``Your latest sprint plan`` heading), while an active-sprint plan
  keeps the live framing.

Screenshots land in ``.tmp/aisl-issue-1129-screenshots`` for tester review.
"""

import datetime
import os
from pathlib import Path

import pytest

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_user as _create_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = pytest.mark.local_only

SCREENSHOT_DIR = (
    Path(__file__).parent.parent / ".tmp" / "aisl-issue-1129-screenshots"
)
SLACK_INVITE_URL = "https://join.slack.com/t/test-1129/shared_invite/zz"


def _shot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=True)


def _set_dismissals(email, keys):
    from accounts.models import User

    user = User.objects.get(email=email)
    user.dashboard_dismissals = keys
    user.save(update_fields=["dashboard_dismissals"])
    connection.close()


def _get_dismissals(email):
    from accounts.models import User

    keys = User.objects.get(email=email).dashboard_dismissals
    connection.close()
    return keys


def _create_plan(email, *, start_offset_days, duration_weeks, status):
    from accounts.models import User
    from plans.models import Plan, Sprint

    user = User.objects.get(email=email)
    start = datetime.date.today() + datetime.timedelta(days=start_offset_days)
    sprint = Sprint.objects.create(
        name="Accountability Sprint",
        slug=f"sprint-{email.split('@')[0]}",
        start_date=start,
        duration_weeks=duration_weeks,
        status=status,
    )
    plan = Plan.objects.create(member=user, sprint=sprint)
    connection.close()
    return plan


@pytest.mark.django_db(transaction=True)
class TestOnboardingDismiss:
    @pytest.mark.core
    def test_dismiss_hides_and_persists(self, django_server, browser):
        _ensure_tiers()
        _create_user("ob-dismiss@test.com", tier_slug="basic")
        ctx = _auth_context(browser, "ob-dismiss@test.com")
        page = ctx.new_page()

        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        prompt = page.locator('[data-testid="onboarding-prompt"]')
        prompt.wait_for(state="visible")
        dismiss = page.locator('[data-testid="onboarding-prompt-dismiss"]')
        assert dismiss.count() == 1
        _shot(page, "onboarding_before_dismiss")

        dismiss.click()
        # Removed without a navigation (URL unchanged).
        prompt.wait_for(state="detached")
        assert page.url.rstrip("/") == django_server.rstrip("/")

        # Persisted server-side: a full reload keeps it gone (this is the
        # actual behaviour under test — persistence across a real reload,
        # not a JS-only hide). Kept config-independent: we do not navigate
        # into the /onboarding/ redirect chain here, whose target depends
        # on global AI-onboarding config that other core tests mutate.
        page.reload(wait_until="domcontentloaded")
        assert page.locator('[data-testid="onboarding-prompt"]').count() == 0
        _shot(page, "onboarding_after_reload")

        # Persisted on the user row (the dismiss only hides this nudge; the
        # member keeps every other onboarding entry point).
        assert "onboarding_prompt" in _get_dismissals("ob-dismiss@test.com")

    @pytest.mark.core
    def test_predismissed_absent_on_first_render(self, django_server, browser):
        _ensure_tiers()
        _create_user("ob-pre@test.com", tier_slug="basic")
        _set_dismissals("ob-pre@test.com", ["onboarding_prompt"])
        ctx = _auth_context(browser, "ob-pre@test.com")
        page = ctx.new_page()

        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        # Absent on the very first render (server-side, not shown-then-hidden).
        assert page.locator('[data-testid="onboarding-prompt"]').count() == 0

    @pytest.mark.core
    def test_not_dismissed_shows_cta_and_dismiss(self, django_server, browser):
        _ensure_tiers()
        _create_user("ob-show@test.com", tier_slug="basic")
        ctx = _auth_context(browser, "ob-show@test.com")
        page = ctx.new_page()

        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        page.locator('[data-testid="onboarding-prompt"]').wait_for(
            state="visible",
        )
        assert page.locator('[data-testid="onboarding-prompt-cta"]').count() == 1
        assert (
            page.locator('[data-testid="onboarding-prompt-dismiss"]').count()
            == 1
        )


@pytest.mark.django_db(transaction=True)
class TestSlackDismiss:
    @pytest.mark.core
    def test_dashboard_dismiss_only(self, django_server, browser, settings):
        settings.SLACK_INVITE_URL = SLACK_INVITE_URL
        _ensure_tiers()
        _create_user("sl-dismiss@test.com", tier_slug="main")
        ctx = _auth_context(browser, "sl-dismiss@test.com")
        page = ctx.new_page()

        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        join = page.locator('[data-testid="slack-account-card-join"]')
        join.wait_for(state="visible")
        dismiss = page.locator('[data-testid="slack-account-card-dismiss"]')
        assert dismiss.count() == 1
        _shot(page, "slack_before_dismiss")

        dismiss.click()
        page.locator('[data-testid="slack-account-card"]').wait_for(
            state="detached",
        )

        # Gone on dashboard reload.
        page.reload(wait_until="domcontentloaded")
        assert (
            page.locator('[data-testid="slack-account-card-join"]').count() == 0
        )

        # /account/ Slack card is untouched — still shown, no dismiss control.
        page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
        assert (
            page.locator('[data-testid="slack-account-card-join"]').count() == 1
        )
        assert (
            page.locator('[data-testid="slack-account-card-dismiss"]').count()
            == 0
        )
        _shot(page, "slack_account_still_shown")

    @pytest.mark.core
    def test_account_join_card_has_no_dismiss(
        self, django_server, browser, settings,
    ):
        settings.SLACK_INVITE_URL = SLACK_INVITE_URL
        _ensure_tiers()
        _create_user("sl-acct@test.com", tier_slug="main")
        ctx = _auth_context(browser, "sl-acct@test.com")
        page = ctx.new_page()

        page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
        page.locator('[data-testid="slack-account-card-join"]').wait_for(
            state="visible",
        )
        assert (
            page.locator('[data-testid="slack-account-card-dismiss"]').count()
            == 0
        )

    @pytest.mark.core
    def test_connected_member_no_dismiss_control(
        self, django_server, browser, settings,
    ):
        settings.SLACK_INVITE_URL = SLACK_INVITE_URL
        _ensure_tiers()
        user = _create_user("sl-conn@test.com", tier_slug="main")
        user.slack_member = True
        user.slack_user_id = "U0CONN1129"
        user.save(update_fields=["slack_member", "slack_user_id"])
        connection.close()

        ctx = _auth_context(browser, "sl-conn@test.com")
        page = ctx.new_page()
        page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
        assert (
            page.locator('[data-testid="slack-account-card-dismiss"]').count()
            == 0
        )


@pytest.mark.django_db(transaction=True)
class TestSprintPlanLifecycle:
    @pytest.mark.core
    def test_ended_sprint_framed_as_past_plan(self, django_server, browser):
        _ensure_tiers()
        _create_user("sp-ended@test.com", tier_slug="main")
        # Ends ~18 days ago: start 60 days ago, 6-week (42d) window.
        _create_plan(
            "sp-ended@test.com",
            start_offset_days=-60,
            duration_weeks=6,
            status="completed",
        )
        ctx = _auth_context(browser, "sp-ended@test.com")
        page = ctx.new_page()

        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        card = page.locator('[data-testid="account-sprint-plan-card"]')
        card.wait_for(state="visible")
        _shot(page, "sprint_ended")

        heading = page.locator('[data-testid="account-sprint-plan-heading"]')
        assert "Your latest sprint plan" in heading.inner_text()
        status = page.locator('[data-testid="account-sprint-plan-status"]')
        assert "Ended" in status.inner_text()
        assert (
            page.locator('[data-testid="account-sprint-plan-open"]').count() == 1
        )
        # Active/next sprint discovery still present.
        assert page.get_by_text("Sprints & Cohorts").count() >= 1

    @pytest.mark.core
    def test_active_sprint_keeps_live_framing(self, django_server, browser):
        _ensure_tiers()
        _create_user("sp-active@test.com", tier_slug="main")
        # start 14 days ago, 8-week (56d) window -> active.
        _create_plan(
            "sp-active@test.com",
            start_offset_days=-14,
            duration_weeks=8,
            status="active",
        )
        ctx = _auth_context(browser, "sp-active@test.com")
        page = ctx.new_page()

        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        heading = page.locator('[data-testid="account-sprint-plan-heading"]')
        heading.wait_for(state="visible")
        _shot(page, "sprint_active")
        assert "Your sprint plan" in heading.inner_text()
        assert "latest" not in heading.inner_text()
        status = page.locator('[data-testid="account-sprint-plan-status"]')
        assert "Active" in status.inner_text()
        assert (
            page.locator('[data-testid="account-sprint-plan-open"]').count() == 1
        )


@pytest.mark.django_db(transaction=True)
class TestDismissEndpoint:
    @pytest.mark.core
    def test_unknown_card_rejected_and_nothing_hidden(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _create_user("ep-unknown@test.com", tier_slug="basic")
        ctx = _auth_context(browser, "ep-unknown@test.com")
        page = ctx.new_page()

        # Load the dashboard first so a valid CSRF cookie is issued.
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        page.locator('[data-testid="onboarding-prompt"]').wait_for(
            state="visible",
        )

        status = page.evaluate(
            """async () => {
              function getCsrfToken() {
                var cookies = document.cookie.split(';');
                for (var i = 0; i < cookies.length; i++) {
                  var c = cookies[i].trim();
                  if (c.startsWith('csrftoken=')) {
                    return c.substring('csrftoken='.length);
                  }
                }
                return '';
              }
              const resp = await fetch('/account/api/dismiss-card', {
                method: 'POST',
                headers: {
                  'Content-Type': 'application/json',
                  'X-CSRFToken': getCsrfToken(),
                },
                body: JSON.stringify({ card: 'not-a-real-card' }),
              });
              return resp.status;
            }""",
        )
        assert status == 400

        # Nothing was hidden: the onboarding nudge is still there on reload.
        page.reload(wait_until="domcontentloaded")
        assert page.locator('[data-testid="onboarding-prompt"]').count() == 1
        assert _get_dismissals("ep-unknown@test.com") == []
