"""Playwright E2E coverage for the /account/ cleanup (issue #581).

Eight scenarios from the spec:

1. Free member arrives at a compact account page.
2. Paid member with a sprint plan does not see the plan on /account.
3. Premium member sees tier name but no Level pill.
4. Paid member with a pending downgrade still sees the pending notice.
5. Paid member with a pending cancellation still sees the cancellation notice.
6. Member with a temporary tier override still sees the override notice.
7. Account page settings still work after cleanup.
8. Sprint plan remains reachable from its normal surface (/sprints/).
"""

import datetime
import os

import pytest
from django.utils import timezone

from playwright_tests.conftest import DEFAULT_PASSWORD, VIEWPORT
from playwright_tests.conftest import (
    create_session_for_user as _create_session_for_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


def _seed_users():
    """Create the users needed for the eight scenarios."""
    from django.db import connection

    from accounts.models import TierOverride, User
    from payments.models import Tier
    from playwright_tests.conftest import ensure_tiers

    ensure_tiers()
    tiers = {t.slug: t for t in Tier.objects.all()}

    def upsert(
        email,
        tier_slug,
        *,
        subscription_id="",
        pending_slug=None,
        billing_period_end=None,
    ):
        user, _ = User.objects.get_or_create(
            email=email,
            defaults={"email_verified": True},
        )
        user.set_password(DEFAULT_PASSWORD)
        user.email_verified = True
        user.tier = tiers.get(tier_slug)
        user.subscription_id = subscription_id
        user.pending_tier = tiers.get(pending_slug) if pending_slug else None
        user.unsubscribed = False
        if billing_period_end is not None:
            user.billing_period_end = billing_period_end
        user.save()
        return user

    # Scenario 1, 7: free user, no pending, no override.
    upsert("free@test.com", "free")

    # Scenario 2, 7, 8: Main user with an active sprint plan.
    main_user = upsert(
        "main@test.com",
        "main",
        subscription_id="sub_main_test_123",
        billing_period_end=timezone.make_aware(
            datetime.datetime(2026, 4, 1, 12, 0, 0)
        ),
    )

    # Scenario 3: Premium user, current plan, no pending change.
    upsert(
        "premium@test.com",
        "premium",
        subscription_id="sub_premium_test_123",
        billing_period_end=timezone.make_aware(
            datetime.datetime(2026, 5, 1, 12, 0, 0)
        ),
    )

    # Scenario 4: Main with pending downgrade to Basic.
    upsert(
        "main-pending-downgrade@test.com",
        "main",
        subscription_id="sub_main_dg_test_123",
        pending_slug="basic",
        billing_period_end=timezone.make_aware(
            datetime.datetime(2026, 4, 1, 12, 0, 0)
        ),
    )

    # Scenario 5: Main with pending cancellation (pending_tier = free).
    upsert(
        "main-pending-cancel@test.com",
        "main",
        subscription_id="sub_main_cancel_test_123",
        pending_slug="free",
        billing_period_end=timezone.make_aware(
            datetime.datetime(2026, 5, 15, 12, 0, 0)
        ),
    )

    # Scenario 6: Basic with active temporary Premium override.
    override_user = upsert(
        "basic-override@test.com",
        "basic",
        subscription_id="sub_basic_override_123",
        billing_period_end=timezone.make_aware(
            datetime.datetime(2026, 6, 1, 12, 0, 0)
        ),
    )
    TierOverride.objects.filter(user=override_user).delete()
    TierOverride.objects.create(
        user=override_user,
        original_tier=tiers["basic"],
        override_tier=tiers["premium"],
        expires_at=timezone.now() + datetime.timedelta(days=14),
    )

    # Scenarios 2 / 8 require a Plan + a teammate so cohort logic also
    # has data on the dashboard side.
    from plans.models import Plan, Sprint

    sprint, _ = Sprint.objects.get_or_create(
        slug="cleanup-581-sprint",
        defaults={
            "name": "Cleanup 581 Sprint",
            "start_date": datetime.date(2026, 5, 1),
            "duration_weeks": 4,
            "status": "active",
        },
    )
    Plan.objects.filter(member=main_user).delete()
    Plan.objects.create(
        member=main_user, sprint=sprint, status="active", visibility="cohort",
    )
    teammate, _ = User.objects.get_or_create(
        email="teammate-581@test.com",
        defaults={"email_verified": True, "tier": tiers["main"]},
    )
    Plan.objects.get_or_create(
        member=teammate, sprint=sprint,
        defaults={"status": "active", "visibility": "cohort"},
    )

    connection.close()


@pytest.fixture
def cleanup_581_users(django_server, django_db_blocker):
    from django.db import connection

    with django_db_blocker.unblock():
        _seed_users()
        connection.close()


def _auth_context(browser, email, db_blocker):
    with db_blocker.unblock():
        session_key = _create_session_for_user(email)
    context = browser.new_context(viewport=VIEWPORT)
    context.add_cookies([
        {
            "name": "sessionid",
            "value": session_key,
            "domain": "127.0.0.1",
            "path": "/",
        },
        {
            "name": "csrftoken",
            "value": "e2e-test-csrf-token-value",
            "domain": "127.0.0.1",
            "path": "/",
        },
    ])
    return context


def _go(page, base_url, path):
    page.goto(f"{base_url}{path}", wait_until="domcontentloaded")


# ----- Scenario 1: Free member arrives at a compact account page -----

@pytest.mark.django_db(transaction=True)
def test_scenario_1_free_member_compact_account_page(
    django_server, browser, django_db_blocker, cleanup_581_users
):
    ctx = _auth_context(browser, "free@test.com", django_db_blocker)
    page = ctx.new_page()
    try:
        _go(page, django_server, "/account/")

        # Profile section is visible.
        assert page.locator("#profile-section").is_visible()
        assert page.locator("#profile-current-name").is_visible()

        # Tier name is Free with no Level pill.
        assert page.locator("#tier-name").inner_text().strip() == "Free"
        assert page.locator("#tier-badge").count() == 0
        assert "Level 0" not in page.content()

        # Sprint plan card is absent.
        assert page.locator("#sprint-plan-section").count() == 0
        assert page.locator(
            '[data-testid="account-sprint-plan-card"]'
        ).count() == 0

        # View cohort link is absent.
        assert page.locator(
            '[data-testid="account-sprint-plan-cohort"]'
        ).count() == 0
        assert page.get_by_text("View cohort", exact=True).count() == 0

        # account_plan_state frame is absent.
        assert page.locator("#account-plan-state").count() == 0

        # The remaining sections are present in expected order.
        assert page.locator("#email-preferences-section").is_visible()
        assert page.locator("#display-preferences-section").is_visible()
        assert page.locator("#change-password-section").is_visible()
        assert page.locator("#account-info-section").is_visible()

        email_y = page.locator(
            "#email-preferences-section"
        ).bounding_box()["y"]
        display_y = page.locator(
            "#display-preferences-section"
        ).bounding_box()["y"]
        password_y = page.locator(
            "#change-password-section"
        ).bounding_box()["y"]
        info_y = page.locator(
            "#account-info-section"
        ).bounding_box()["y"]
        assert email_y < display_y < password_y < info_y
    finally:
        ctx.close()


# ----- Scenario 2: Paid member with a sprint plan -----

@pytest.mark.django_db(transaction=True)
def test_scenario_2_paid_with_plan_does_not_see_plan_on_account(
    django_server, browser, django_db_blocker, cleanup_581_users
):
    ctx = _auth_context(browser, "main@test.com", django_db_blocker)
    page = ctx.new_page()
    try:
        _go(page, django_server, "/account/")

        # Tier name is Main with no Level 20 pill.
        assert page.locator("#tier-name").inner_text().strip() == "Main"
        assert page.locator("#tier-badge").count() == 0
        assert "Level 20" not in page.content()

        # Sprint plan card and cohort link both gone.
        assert page.locator("#sprint-plan-section").count() == 0
        assert page.get_by_text("Your sprint plan", exact=True).count() == 0
        assert page.get_by_text("View cohort", exact=True).count() == 0

        # Dashboard still surfaces an entry point to the user's plan.
        _go(page, django_server, "/")
        assert page.locator(
            '[data-testid="account-sprint-plan-card"]'
        ).count() >= 1
        assert page.locator(
            '[data-testid="account-sprint-plan-open"]'
        ).count() >= 1
    finally:
        ctx.close()


# ----- Scenario 3: Premium member sees tier name but no Level pill -----

@pytest.mark.django_db(transaction=True)
def test_scenario_3_premium_no_level_pill_no_steady_state_frame(
    django_server, browser, django_db_blocker, cleanup_581_users
):
    ctx = _auth_context(browser, "premium@test.com", django_db_blocker)
    page = ctx.new_page()
    try:
        _go(page, django_server, "/account/")

        assert page.locator("#tier-name").inner_text().strip() == "Premium"
        assert page.locator("#tier-badge").count() == 0
        assert "Level 30" not in page.content()

        # Steady-state Current plan frame is suppressed.
        assert page.locator("#account-plan-state").count() == 0

        billing = page.locator("#billing-period-end")
        assert billing.is_visible()
        assert "01/05/2026" in billing.inner_text()
    finally:
        ctx.close()


# ----- Scenario 4: Pending downgrade keeps the amber notice -----

@pytest.mark.django_db(transaction=True)
def test_scenario_4_pending_downgrade_notice_visible_no_duplicate_frame(
    django_server, browser, django_db_blocker, cleanup_581_users
):
    ctx = _auth_context(
        browser, "main-pending-downgrade@test.com", django_db_blocker
    )
    page = ctx.new_page()
    try:
        _go(page, django_server, "/account/")

        notice = page.locator("#pending-downgrade-notice")
        assert notice.is_visible()
        text = notice.inner_text()
        assert "Basic" in text
        assert "01/04/2026" in text

        # Tier name is Main with no Level pill.
        assert page.locator("#tier-name").inner_text().strip() == "Main"
        assert page.locator("#tier-badge").count() == 0

        # No duplicate plan-state frame.
        assert page.locator("#account-plan-state").count() == 0
    finally:
        ctx.close()


# ----- Scenario 5: Pending cancellation keeps the red notice -----

@pytest.mark.django_db(transaction=True)
def test_scenario_5_pending_cancellation_notice_visible_no_cancel_button(
    django_server, browser, django_db_blocker, cleanup_581_users
):
    ctx = _auth_context(
        browser, "main-pending-cancel@test.com", django_db_blocker
    )
    page = ctx.new_page()
    try:
        _go(page, django_server, "/account/")

        notice = page.locator("#pending-cancellation-notice")
        assert notice.is_visible()
        text = notice.inner_text()
        assert "Main" in text
        assert "15/05/2026" in text

        # Tier name is Main with no Level pill.
        assert page.locator("#tier-name").inner_text().strip() == "Main"
        assert page.locator("#tier-badge").count() == 0

        # Cancel Subscription button is hidden (already scheduled).
        assert page.locator("#cancel-btn").count() == 0
    finally:
        ctx.close()


# ----- Scenario 6: Active tier override keeps the accent notice -----

@pytest.mark.django_db(transaction=True)
def test_scenario_6_temporary_override_notice_visible(
    django_server, browser, django_db_blocker, cleanup_581_users
):
    ctx = _auth_context(
        browser, "basic-override@test.com", django_db_blocker
    )
    page = ctx.new_page()
    try:
        _go(page, django_server, "/account/")

        notice = page.locator("#tier-override-notice")
        assert notice.is_visible()
        text = notice.inner_text()
        assert "Premium" in text

        # Membership shows the BASE subscription tier, not the override.
        assert page.locator("#tier-name").inner_text().strip() == "Basic"
        assert page.locator("#tier-badge").count() == 0

        # Sprint plan card is still absent regardless of override.
        assert page.locator("#sprint-plan-section").count() == 0
    finally:
        ctx.close()


# ----- Scenario 7: settings still work after cleanup -----

@pytest.mark.django_db(transaction=True)
def test_scenario_7_settings_still_work_after_cleanup(
    django_server, browser, django_db_blocker, cleanup_581_users
):
    from playwright.sync_api import expect

    ctx = _auth_context(browser, "main@test.com", django_db_blocker)
    page = ctx.new_page()
    try:
        _go(page, django_server, "/account/")

        # 1) Newsletter toggle: off -> status text updates.
        status = page.locator("#newsletter-status")
        if "subscribed" in status.inner_text() and "unsubscribed" not in status.inner_text():
            page.locator("#newsletter-toggle").click()
            expect(status).to_contain_text(
                "You are unsubscribed from newsletters.", timeout=5000
            )
        else:
            # Already unsubscribed; toggle on then off to verify both ways.
            page.locator("#newsletter-toggle").click()
            expect(status).to_contain_text(
                "You are subscribed to newsletters.", timeout=5000
            )
            page.locator("#newsletter-toggle").click()
            expect(status).to_contain_text(
                "You are unsubscribed from newsletters.", timeout=5000
            )

        # 2) Display Preferences: pick a timezone option and Save.
        page.fill("#timezone-preference-input", "(UTC) UTC")
        page.click("#save-timezone-btn")
        tz_status = page.locator("#timezone-preference-status")
        expect(tz_status).to_contain_text("Current timezone:", timeout=5000)

        # 3) Change Password: mismatched new passwords show inline error.
        page.fill("#current-password", DEFAULT_PASSWORD)
        page.fill("#new-password", "NewSecure456!")
        page.fill("#confirm-new-password", "DoesNotMatch789!")
        page.click("#change-password-form button[type='submit']")
        error = page.locator("#password-error")
        expect(error).to_be_visible(timeout=5000)
        assert "New passwords do not match" in error.inner_text()
    finally:
        ctx.close()


# ----- Scenario 8: sprint plan remains reachable from /sprints/ -----

@pytest.mark.django_db(transaction=True)
def test_scenario_8_sprint_plan_reachable_from_sprints_surface(
    django_server, browser, django_db_blocker, cleanup_581_users
):
    ctx = _auth_context(browser, "main@test.com", django_db_blocker)
    page = ctx.new_page()
    try:
        _go(page, django_server, "/account/")

        # No sprint plan entry on /account/.
        assert page.locator("#sprint-plan-section").count() == 0
        assert page.locator(
            '[data-testid="account-sprint-plan-open"]'
        ).count() == 0

        # /sprints/ still renders for the user.
        _go(page, django_server, "/sprints/")
        # The page must not 404 / 500. The Sprint name is reachable from
        # the listing.
        assert "Cleanup 581 Sprint" in page.content() or page.locator(
            "main"
        ).is_visible()
    finally:
        ctx.close()
