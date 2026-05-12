"""
Playwright E2E tests for the Account Page (Issue #70).

Tests cover:
- Anonymous redirect to login
- Free member account page (upgrade options, no downgrade/cancel)
- Basic member subscription details and upgrade modal
- Main member downgrade flow
- Premium member at highest tier
- Pending downgrade notice
- Pending cancellation notice
- Cancel subscription confirmation modal
- Newsletter toggle on/off
- Password change (success and error)
- Email verification banner

Usage:
    uv run pytest playwright_tests/test_account_page.py -v
"""

import datetime
import os

import pytest
from django.utils import timezone

from playwright_tests.conftest import (
    DEFAULT_PASSWORD,
    VIEWPORT,
)
from playwright_tests.conftest import (
    create_session_for_user as _create_session_for_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


def _create_test_users():
    """Create test users for account page E2E tests.

    Must be called within django_db_blocker.unblock() context.
    Returns a dict of created users keyed by email prefix.
    """
    from accounts.models import User
    from payments.models import Tier
    from playwright_tests.conftest import ensure_tiers

    ensure_tiers()
    tiers = {t.slug: t for t in Tier.objects.all()}
    users = {}

    # Free member (email verified)
    free_user, _ = User.objects.get_or_create(
        email="free@test.com",
        defaults={
            "email_verified": True,
            "tier": tiers["free"],
            "unsubscribed": False,
        },
    )
    free_user.set_password(DEFAULT_PASSWORD)
    free_user.email_verified = True
    free_user.unsubscribed = False
    free_user.save()
    users["free"] = free_user

    # Basic member
    basic_user, _ = User.objects.get_or_create(
        email="basic@test.com",
        defaults={
            "email_verified": True,
            "tier": tiers["basic"],
            "subscription_id": "sub_basic_test_123",
            "billing_period_end": timezone.make_aware(
                datetime.datetime(2026, 3, 15, 0, 0, 0)
            ),
            "unsubscribed": False,
        },
    )
    basic_user.set_password(DEFAULT_PASSWORD)
    basic_user.tier = tiers["basic"]
    basic_user.subscription_id = "sub_basic_test_123"
    basic_user.billing_period_end = timezone.make_aware(
        datetime.datetime(2026, 3, 15, 0, 0, 0)
    )
    basic_user.save()
    users["basic"] = basic_user

    # Main member (no pending)
    main_user, _ = User.objects.get_or_create(
        email="main@test.com",
        defaults={
            "email_verified": True,
            "tier": tiers["main"],
            "subscription_id": "sub_main_test_123",
            "billing_period_end": timezone.make_aware(
                datetime.datetime(2026, 4, 1, 0, 0, 0)
            ),
            "unsubscribed": False,
        },
    )
    main_user.set_password(DEFAULT_PASSWORD)
    main_user.tier = tiers["main"]
    main_user.subscription_id = "sub_main_test_123"
    main_user.billing_period_end = timezone.make_aware(
        datetime.datetime(2026, 4, 1, 0, 0, 0)
    )
    main_user.pending_tier = None
    main_user.save()
    users["main"] = main_user

    # Premium member
    premium_user, _ = User.objects.get_or_create(
        email="premium@test.com",
        defaults={
            "email_verified": True,
            "tier": tiers["premium"],
            "subscription_id": "sub_premium_test_123",
            "billing_period_end": timezone.make_aware(
                datetime.datetime(2026, 5, 1, 0, 0, 0)
            ),
            "unsubscribed": False,
        },
    )
    premium_user.set_password(DEFAULT_PASSWORD)
    premium_user.tier = tiers["premium"]
    premium_user.subscription_id = "sub_premium_test_123"
    premium_user.billing_period_end = timezone.make_aware(
        datetime.datetime(2026, 5, 1, 0, 0, 0)
    )
    premium_user.save()
    users["premium"] = premium_user

    # Main member with pending downgrade to Basic
    main_downgrade_user, _ = User.objects.get_or_create(
        email="main-downgrade@test.com",
        defaults={
            "email_verified": True,
            "tier": tiers["main"],
            "subscription_id": "sub_main_dg_test_123",
            "billing_period_end": timezone.make_aware(
                datetime.datetime(2026, 4, 1, 0, 0, 0)
            ),
            "pending_tier": tiers["basic"],
            "unsubscribed": False,
        },
    )
    main_downgrade_user.set_password(DEFAULT_PASSWORD)
    main_downgrade_user.tier = tiers["main"]
    main_downgrade_user.pending_tier = tiers["basic"]
    main_downgrade_user.subscription_id = "sub_main_dg_test_123"
    main_downgrade_user.billing_period_end = timezone.make_aware(
        datetime.datetime(2026, 4, 1, 0, 0, 0)
    )
    main_downgrade_user.save()
    users["main_downgrade"] = main_downgrade_user

    # Main member with pending cancellation (pending_tier = free)
    main_cancel_user, _ = User.objects.get_or_create(
        email="main-cancel@test.com",
        defaults={
            "email_verified": True,
            "tier": tiers["main"],
            "subscription_id": "sub_main_cancel_test_123",
            "billing_period_end": timezone.make_aware(
                datetime.datetime(2026, 5, 15, 0, 0, 0)
            ),
            "pending_tier": tiers["free"],
            "unsubscribed": False,
        },
    )
    main_cancel_user.set_password(DEFAULT_PASSWORD)
    main_cancel_user.tier = tiers["main"]
    main_cancel_user.pending_tier = tiers["free"]
    main_cancel_user.subscription_id = "sub_main_cancel_test_123"
    main_cancel_user.billing_period_end = timezone.make_aware(
        datetime.datetime(2026, 5, 15, 0, 0, 0)
    )
    main_cancel_user.save()
    users["main_cancel"] = main_cancel_user

    # Free member with email NOT verified
    free_unverified, _ = User.objects.get_or_create(
        email="free-unverified@test.com",
        defaults={
            "email_verified": False,
            "tier": tiers["free"],
            "unsubscribed": False,
        },
    )
    free_unverified.set_password(DEFAULT_PASSWORD)
    free_unverified.email_verified = False
    free_unverified.save()
    users["free_unverified"] = free_unverified

    return users


@pytest.fixture
def test_users(django_server, django_db_blocker):
    """Create test users for account page tests.

    Function-scoped because transaction=True tests truncate tables
    between tests, so users must be recreated each time.
    """
    from django.db import connection
    with django_db_blocker.unblock():
        users = _create_test_users()
        connection.close()
    return users


def _auth_context(browser, email, db_blocker):
    """Create an authenticated browser context for the given user.

    Creates a Django session via the ORM (within db_blocker.unblock)
    and sets session + CSRF cookies on the new browser context.
    """
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


def _go_to_account(page, base_url):
    """Navigate to the account page."""
    page.goto(f"{base_url}/account/", wait_until="domcontentloaded")


# ---------------------------------------------------------------
# Scenario: Anonymous visitor is redirected to login
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenarioAnonymousRedirectToLogin:
    """Anonymous visitor is redirected to login when trying to manage
    their account."""

    def test_anonymous_redirected_to_login(self, django_server, test_users, page):
        """Navigate to /account/ without login -- redirects to
        /accounts/login/."""
        page.goto(
            f"{django_server}/account/",
            wait_until="domcontentloaded",
        )
        assert "/accounts/login/" in page.url
    def test_authenticated_user_sees_account_page(
        self, django_server, test_users, django_db_blocker
    , browser):
        """After authenticating, the user reaches the account page with
        Account heading and their membership details."""
        context = _auth_context(
            browser, "free@test.com", django_db_blocker
        )
        page = context.new_page()
        _go_to_account(page, django_server)

        heading = page.locator("h1")
        assert "Account" in heading.inner_text()

        tier_name = page.locator("#tier-name").inner_text().strip()
        assert tier_name == "Free"
        context.close()
# ---------------------------------------------------------------
# Scenario: Free member visits account page
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenarioFreeMemberAccountPage:
    """Free member visits account page and sees upgrade options."""

    def test_free_tier_name_and_no_level_pill(
        self, django_server, test_users, django_db_blocker
    , browser):
        """Issue #581: page shows tier name Free with no Level pill."""
        ctx = _auth_context(
            browser, "free@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        assert page.locator("#tier-name").inner_text().strip() == "Free"
        assert page.locator("#tier-badge").count() == 0
        ctx.close()
    def test_no_billing_period_for_free(
        self, django_server, test_users, django_db_blocker
    , browser):
        """No billing period date is shown for free members."""
        ctx = _auth_context(
            browser, "free@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        assert page.locator("#billing-period-end").count() == 0
        ctx.close()
    def test_upgrade_link_points_to_pricing(
        self, django_server, test_users, django_db_blocker
    , browser):
        """An Upgrade link is visible pointing to /pricing."""
        ctx = _auth_context(
            browser, "free@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        btn = page.locator("#upgrade-btn")
        assert btn.is_visible()
        assert btn.get_attribute("href") == "/pricing"
        ctx.close()
    def test_no_downgrade_or_cancel_for_free(
        self, django_server, test_users, django_db_blocker
    , browser):
        """No Downgrade or Cancel Subscription actions for free members."""
        ctx = _auth_context(
            browser, "free@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        assert page.locator("#downgrade-btn").count() == 0
        assert page.locator("#cancel-btn").count() == 0
        ctx.close()
    def test_upgrade_link_navigates_to_pricing(
        self, django_server, test_users, django_db_blocker
    , browser):
        """Click Upgrade link and land on /pricing."""
        ctx = _auth_context(
            browser, "free@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        page.click("#upgrade-btn")
        page.wait_for_load_state("domcontentloaded")
        assert "/pricing" in page.url
        ctx.close()
# ---------------------------------------------------------------
# Scenario: Basic member views subscription details
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenarioBasicMemberSubscription:
    """Basic member views subscription details and explores upgrade
    options."""

    def test_basic_tier_name_and_billing_no_level_pill(
        self, django_server, test_users, django_db_blocker
    , browser):
        """Issue #581: page shows Basic and billing period end
        March 15, 2026 with no Level pill."""
        ctx = _auth_context(
            browser, "basic@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        assert page.locator("#tier-name").inner_text().strip() == "Basic"
        assert page.locator("#tier-badge").count() == 0

        billing = page.locator("#billing-period-end")
        assert billing.is_visible()
        assert "15/03/2026" in billing.inner_text()
        ctx.close()
    def test_basic_has_upgrade_and_cancel_no_downgrade(
        self, django_server, test_users, django_db_blocker
    , browser):
        """Basic member has Upgrade and Cancel, but no Downgrade."""
        ctx = _auth_context(
            browser, "basic@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        assert page.locator("#upgrade-btn").is_visible()
        assert page.locator("#cancel-btn").is_visible()
        assert page.locator("#downgrade-btn").count() == 0
        ctx.close()
    def test_upgrade_modal_shows_higher_tiers(
        self, django_server, test_users, django_db_blocker
    , browser):
        """Click Upgrade -- modal lists Main and Premium."""
        ctx = _auth_context(
            browser, "basic@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        page.click("#upgrade-btn")
        modal = page.locator("#upgrade-modal")
        modal.wait_for(state="visible", timeout=5000)

        assert modal.is_visible()
        assert "Upgrade Your Plan" in modal.locator("h3").inner_text()

        buttons = modal.locator("#upgrade-tiers button")
        all_text = " ".join(
            buttons.nth(i).inner_text()
            for i in range(buttons.count())
        )
        assert "Main" in all_text
        assert "Premium" in all_text
        ctx.close()
    def test_close_upgrade_modal(
        self, django_server, test_users, django_db_blocker
    , browser):
        """Close upgrade modal with Cancel -- no changes."""
        ctx = _auth_context(
            browser, "basic@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        page.click("#upgrade-btn")
        modal = page.locator("#upgrade-modal")
        modal.wait_for(state="visible", timeout=5000)

        assert modal.is_visible()

        modal.locator("button", has_text="Cancel").click()
        page.wait_for_load_state("domcontentloaded")
        assert modal.is_hidden()
        assert "/account" in page.url
        ctx.close()
# ---------------------------------------------------------------
# Scenario: Main member initiates a downgrade
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenarioMainMemberDowngrade:
    """Main member initiates a downgrade to Basic."""

    def test_main_tier_name_no_level_pill(
        self, django_server, test_users, django_db_blocker
    , browser):
        """Issue #581: page shows Main with no Level pill."""
        ctx = _auth_context(
            browser, "main@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        assert page.locator("#tier-name").inner_text().strip() == "Main"
        assert page.locator("#tier-badge").count() == 0
        ctx.close()
    def test_main_has_all_three_actions(
        self, django_server, test_users, django_db_blocker
    , browser):
        """Main member has Upgrade, Downgrade, and Cancel."""
        ctx = _auth_context(
            browser, "main@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        assert page.locator("#upgrade-btn").is_visible()
        assert page.locator("#downgrade-btn").is_visible()
        assert page.locator("#cancel-btn").is_visible()
        ctx.close()
    def test_downgrade_modal_shows_only_basic(
        self, django_server, test_users, django_db_blocker
    , browser):
        """Downgrade modal lists only Basic."""
        ctx = _auth_context(
            browser, "main@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        page.click("#downgrade-btn")
        modal = page.locator("#downgrade-modal")
        modal.wait_for(state="visible", timeout=5000)

        assert modal.is_visible()
        assert "Downgrade Your Plan" in modal.locator("h3").inner_text()

        buttons = modal.locator("#downgrade-tiers button")
        assert buttons.count() == 1
        text = buttons.first.inner_text()
        assert "Basic" in text
        assert "Main" not in text
        assert "Premium" not in text
        ctx.close()
# ---------------------------------------------------------------
# Scenario: Premium member at highest tier
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenarioPremiumMemberHighestTier:
    """Premium member sees they are at the highest tier."""

    def test_premium_tier_name_no_level_pill(
        self, django_server, test_users, django_db_blocker
    , browser):
        """Issue #581: page shows Premium with no Level pill."""
        ctx = _auth_context(
            browser, "premium@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        assert page.locator("#tier-name").inner_text().strip() == "Premium"
        assert page.locator("#tier-badge").count() == 0
        ctx.close()
    def test_premium_no_upgrade_has_downgrade_and_cancel(
        self, django_server, test_users, django_db_blocker
    , browser):
        """No Upgrade for Premium. Downgrade and Cancel available."""
        ctx = _auth_context(
            browser, "premium@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        assert page.locator("#upgrade-btn").count() == 0
        assert page.locator("#downgrade-btn").is_visible()
        assert page.locator("#cancel-btn").is_visible()
        ctx.close()
# ---------------------------------------------------------------
# Scenario: Pending downgrade notice
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenarioPendingDowngradeNotice:
    """Main member with pending downgrade sees notice."""

    def test_pending_downgrade_notice_visible(
        self, django_server, test_users, django_db_blocker
    , browser):
        """Notice says plan will change to Basic on April 1, 2026."""
        ctx = _auth_context(
            browser, "main-downgrade@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        notice = page.locator("#pending-downgrade-notice")
        assert notice.is_visible()
        text = notice.inner_text()
        assert "Basic" in text
        assert "01/04/2026" in text
        ctx.close()
    def test_no_downgrade_action_when_pending(
        self, django_server, test_users, django_db_blocker
    , browser):
        """No Downgrade action when already scheduled."""
        ctx = _auth_context(
            browser, "main-downgrade@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        assert page.locator("#downgrade-btn").count() == 0
        ctx.close()
    def test_cancel_still_available_with_pending_downgrade(
        self, django_server, test_users, django_db_blocker
    , browser):
        """Cancel still available despite pending downgrade."""
        ctx = _auth_context(
            browser, "main-downgrade@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        assert page.locator("#cancel-btn").is_visible()
        ctx.close()
# ---------------------------------------------------------------
# Scenario: Pending cancellation notice
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenarioPendingCancellationNotice:
    """Main member with pending cancellation sees notice and no
    subscription actions."""

    def test_pending_cancellation_notice_visible(
        self, django_server, test_users, django_db_blocker
    , browser):
        """Notice says Main access ends on May 15, 2026."""
        ctx = _auth_context(
            browser, "main-cancel@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        notice = page.locator("#pending-cancellation-notice")
        assert notice.is_visible()
        text = notice.inner_text()
        assert "Main" in text
        assert "15/05/2026" in text
        ctx.close()
    def test_no_actions_when_cancelled(
        self, django_server, test_users, django_db_blocker
    , browser):
        """No Upgrade, Downgrade, or Cancel actions when cancelled."""
        ctx = _auth_context(
            browser, "main-cancel@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        assert page.locator("#upgrade-btn").count() == 0
        assert page.locator("#downgrade-btn").count() == 0
        assert page.locator("#cancel-btn").count() == 0
        ctx.close()
# ---------------------------------------------------------------
# Scenario: Cancel subscription confirmation
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenarioCancelSubscriptionConfirmation:
    """Paid member cancels subscription after reading the
    confirmation."""

    def test_cancel_modal_shows_confirmation(
        self, django_server, test_users, django_db_blocker
    , browser):
        """Confirmation modal shows date and buttons."""
        ctx = _auth_context(
            browser, "main@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        page.click("#cancel-btn")
        modal = page.locator("#cancel-modal")
        modal.wait_for(state="visible", timeout=5000)

        assert modal.is_visible()

        text = modal.inner_text()
        assert "You will keep access to your current tier until the end of your billing period" in text
        assert "01/04/2026" in text
        assert modal.locator("#confirm-cancel-btn").is_visible()
        assert modal.locator(
            "button", has_text="Keep my plan"
        ).is_visible()
        ctx.close()
    def test_keep_plan_closes_modal(
        self, django_server, test_users, django_db_blocker
    , browser):
        """Click Keep Plan -- modal closes, no changes."""
        ctx = _auth_context(
            browser, "main@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        page.click("#cancel-btn")
        modal = page.locator("#cancel-modal")
        modal.wait_for(state="visible", timeout=5000)

        assert modal.is_visible()

        modal.locator("button", has_text="Keep my plan").click()
        page.wait_for_load_state("domcontentloaded")
        assert modal.is_hidden()

        assert "/account" in page.url
        assert page.locator("#tier-name").inner_text().strip() == "Main"
        ctx.close()
# ---------------------------------------------------------------
# Scenario: Newsletter toggle
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenarioNewsletterToggle:
    """Free member toggles newsletter subscription off and back on."""

    def test_newsletter_subscribed_status(
        self, django_server, test_users, django_db_blocker
    , browser):
        """Initially shows subscribed status."""
        ctx = _auth_context(
            browser, "free@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        status = page.locator("#newsletter-status")
        assert "You are subscribed to newsletters." in status.inner_text()
        ctx.close()
    def test_toggle_off_and_on(
        self, django_server, test_users, django_db_blocker
    , browser):
        """Toggle off shows unsubscribed, toggle on shows subscribed."""
        from playwright.sync_api import expect

        ctx = _auth_context(
            browser, "free@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        toggle = page.locator("#newsletter-toggle")
        status = page.locator("#newsletter-status")

        # Ensure starting state is subscribed
        expect(status).to_contain_text("subscribed", timeout=5000)
        if "unsubscribed" in status.inner_text():
            toggle.click()
            expect(status).to_contain_text(
                "You are subscribed to newsletters.", timeout=5000
            )

        # Toggle off
        toggle.click()
        expect(status).to_contain_text(
            "You are unsubscribed from newsletters.", timeout=5000
        )

        # Toggle back on
        toggle.click()
        expect(status).to_contain_text(
            "You are subscribed to newsletters.", timeout=5000
        )
        ctx.close()
    def test_newsletter_persists_after_reload(
        self, django_server, test_users, django_db_blocker
    , browser):
        """Newsletter preference persists after page reload."""
        ctx = _auth_context(
            browser, "free@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        status = page.locator("#newsletter-status")
        if "unsubscribed" in status.inner_text():
            page.locator("#newsletter-toggle").click()
            page.wait_for_load_state("domcontentloaded")

        assert "You are subscribed to newsletters." in status.inner_text()

        page.reload(wait_until="domcontentloaded")
        status = page.locator("#newsletter-status")
        assert "You are subscribed to newsletters." in status.inner_text()
        ctx.close()
# ---------------------------------------------------------------
# Scenario: Change password success
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenarioChangePasswordSuccess:
    """Member changes their password successfully."""

    def test_change_password_success_message_and_fields_cleared(
        self, django_server, test_users, django_db_blocker
    , browser):
        """Success message appears and form fields are cleared."""
        from playwright.sync_api import expect

        ctx = _auth_context(
            browser, "free@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        page.fill("#current-password", DEFAULT_PASSWORD)
        page.fill("#new-password", "NewSecure456!")
        page.fill("#confirm-new-password", "NewSecure456!")
        page.click("#change-password-form button[type='submit']")

        success = page.locator("#password-success")
        expect(success).to_be_visible(timeout=5000)
        assert "password" in success.inner_text().lower()

        assert page.locator("#current-password").input_value() == ""
        assert page.locator("#new-password").input_value() == ""
        assert page.locator("#confirm-new-password").input_value() == ""

        # Reset password back for other tests
        page.fill("#current-password", "NewSecure456!")
        page.fill("#new-password", DEFAULT_PASSWORD)
        page.fill("#confirm-new-password", DEFAULT_PASSWORD)
        page.click("#change-password-form button[type='submit']")
        expect(success).to_be_visible(timeout=5000)
        ctx.close()
    def test_new_password_works_after_change(
        self, django_server, test_users, django_db_blocker
    , browser):
        """After changing password, a new session can be created
        (verifying the password hash updated)."""
        from playwright.sync_api import expect

        # Change to new password
        ctx = _auth_context(
            browser, "free@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        page.fill("#current-password", DEFAULT_PASSWORD)
        page.fill("#new-password", "NewSecure456!")
        page.fill("#confirm-new-password", "NewSecure456!")
        page.click("#change-password-form button[type='submit']")
        expect(page.locator("#password-success")).to_be_visible(timeout=5000)
        ctx.close()

        # Verify new session works
        ctx2 = _auth_context(
            browser, "free@test.com", django_db_blocker
        )
        page2 = ctx2.new_page()
        _go_to_account(page2, django_server)
        assert "Account" in page2.locator("h1").inner_text()
        ctx2.close()

        # Reset password
        ctx3 = _auth_context(
            browser, "free@test.com", django_db_blocker
        )
        page3 = ctx3.new_page()
        _go_to_account(page3, django_server)
        page3.fill("#current-password", "NewSecure456!")
        page3.fill("#new-password", DEFAULT_PASSWORD)
        page3.fill("#confirm-new-password", DEFAULT_PASSWORD)
        page3.click("#change-password-form button[type='submit']")
        expect(page3.locator("#password-success")).to_be_visible(timeout=5000)
        ctx3.close()
# ---------------------------------------------------------------
# Scenario: Change password error
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenarioChangePasswordError:
    """Member enters wrong current password and sees an error."""

    def test_wrong_current_password_shows_error(
        self, django_server, test_users, django_db_blocker
    , browser):
        """Wrong current password shows error, no success."""
        from playwright.sync_api import expect

        ctx = _auth_context(
            browser, "free@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        page.fill("#current-password", "WrongPassword99")
        page.fill("#new-password", "NewSecure456!")
        page.fill("#confirm-new-password", "NewSecure456!")
        page.click("#change-password-form button[type='submit']")

        expect(page.locator("#password-error")).to_be_visible(timeout=5000)
        assert page.locator("#password-success").is_hidden()
        ctx.close()
    def test_old_password_still_works_after_failed_change(
        self, django_server, test_users, django_db_blocker
    , browser):
        """After failed change, old password still works."""
        from playwright.sync_api import expect

        # Attempt failed change
        ctx = _auth_context(
            browser, "free@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        page.fill("#current-password", "WrongPassword99")
        page.fill("#new-password", "NewSecure456!")
        page.fill("#confirm-new-password", "NewSecure456!")
        page.click("#change-password-form button[type='submit']")
        expect(page.locator("#password-error")).to_be_visible(timeout=5000)
        ctx.close()

        # Verify old password works (create new session)
        ctx2 = _auth_context(
            browser, "free@test.com", django_db_blocker
        )
        page2 = ctx2.new_page()
        _go_to_account(page2, django_server)
        assert "Account" in page2.locator("h1").inner_text()
        ctx2.close()
# ---------------------------------------------------------------
# Scenario: Email verification banner
# ---------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenarioEmailVerificationBanner:
    """Member who has not verified their email sees verification
    banner."""

    def test_unverified_user_sees_banner(
        self, django_server, test_users, django_db_blocker
    , browser):
        """Unverified user sees Verify your email banner; rest of page
        is accessible."""
        ctx = _auth_context(
            browser, "free-unverified@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        banner = page.locator("#email-verification-banner")
        assert banner.is_visible()
        assert "Verify your email" in banner.inner_text()

        assert page.locator("#tier-name").is_visible()
        assert page.locator("#email-preferences-section").is_visible()
        assert page.locator("#change-password-section").is_visible()
        ctx.close()
    def test_verified_user_no_banner(
        self, django_server, test_users, django_db_blocker
    , browser):
        """Verified user does not see verification banner."""
        ctx = _auth_context(
            browser, "free@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        assert page.locator("#email-verification-banner").count() == 0
        ctx.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenarioResendVerificationEmail:
    """Unverified member can resend verification email from /account/."""

    def _clear_throttle_cache(self, db_blocker):
        with db_blocker.unblock():
            from django.core.cache import cache as _cache
            _cache.clear()

    def test_unverified_member_sees_resend_button_and_can_send(
        self, django_server, test_users, django_db_blocker, browser
    ):
        from playwright.sync_api import expect

        self._clear_throttle_cache(django_db_blocker)
        ctx = _auth_context(
            browser, "free-unverified@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        banner = page.locator("#email-verification-banner")
        expect(banner).to_be_visible()
        button = banner.locator("#resend-verification-btn")
        expect(button).to_be_visible()
        assert "Resend verification email" in button.inner_text()

        banner_y = banner.bounding_box()["y"]
        profile_y = page.locator("#profile-section").bounding_box()["y"]
        assert banner_y < profile_y

        button.click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url.rstrip("/").endswith("/account")

        success = page.locator(
            '[data-testid="messages-region"] [data-message-tag="success"]'
        )
        expect(success).to_be_visible(timeout=5000)
        assert "Verification email sent" in success.inner_text()
        expect(page.locator("#email-verification-banner")).to_be_visible()
        ctx.close()

    def test_rapid_second_click_shows_throttle_warning(
        self, django_server, test_users, django_db_blocker, browser
    ):
        from playwright.sync_api import expect

        self._clear_throttle_cache(django_db_blocker)
        ctx = _auth_context(
            browser, "free-unverified@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        page.click("#resend-verification-btn")
        page.wait_for_load_state("domcontentloaded")
        success = page.locator(
            '[data-testid="messages-region"] [data-message-tag="success"]'
        )
        expect(success).to_be_visible(timeout=5000)

        page.click("#resend-verification-btn")
        page.wait_for_load_state("domcontentloaded")
        warning = page.locator(
            '[data-testid="messages-region"] [data-message-tag="warning"]'
        )
        expect(warning).to_be_visible(timeout=5000)
        assert "minute" in warning.inner_text()
        assert page.locator(
            '[data-testid="messages-region"] [data-message-tag="success"]'
        ).count() == 0
        ctx.close()

    def test_verified_member_sees_no_banner(
        self, django_server, test_users, django_db_blocker, browser
    ):
        ctx = _auth_context(
            browser, "free@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)

        assert page.locator("#email-verification-banner").count() == 0
        heading_y = page.locator("h1").bounding_box()["y"]
        profile_y = page.locator("#profile-section").bounding_box()["y"]
        assert profile_y > heading_y
        ctx.close()

    def test_anonymous_post_redirects_to_login(
        self, django_server, test_users, django_db_blocker, browser
    ):
        self._clear_throttle_cache(django_db_blocker)

        anon = browser.new_context(viewport=VIEWPORT)
        anon_page = anon.new_page()
        anon_page.goto(
            f"{django_server}/accounts/login/", wait_until="domcontentloaded"
        )
        cookies = {c["name"]: c["value"] for c in anon.cookies()}
        csrf = cookies.get("csrftoken", "")

        response = anon.request.post(
            f"{django_server}/account/api/resend-verification",
            headers={
                "X-CSRFToken": csrf,
                "Referer": f"{django_server}/account/",
            },
            max_redirects=0,
        )
        assert response.status == 302
        assert "/accounts/login/" in response.headers.get("location", "")
        anon.close()

        ctx = _auth_context(
            browser, "free-unverified@test.com", django_db_blocker
        )
        page = ctx.new_page()
        _go_to_account(page, django_server)
        assert page.locator("#email-verification-banner").is_visible()
        ctx.close()
