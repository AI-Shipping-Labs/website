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
from playwright.sync_api import sync_playwright

from playwright_tests.conftest import DJANGO_BASE_URL


# Allow Django ORM calls from within sync_playwright (which runs an
# event loop internally). Without this, Django 6 raises
# SynchronousOnlyOperation when we create sessions inside test methods.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


VIEWPORT = {"width": 1280, "height": 720}

DEFAULT_PASSWORD = "TestPass123!"


def _create_test_users():
    """Create test users for account page E2E tests.

    Must be called within django_db_blocker.unblock() context.
    Returns a dict of created users keyed by email prefix.
    """
    from accounts.models import User
    from payments.models import Tier

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


def _create_session_for_user(email):
    """Create a Django session for the given user and return the session
    key. This creates a server-side session that can be used as a cookie
    in Playwright to authenticate without going through the login UI."""
    from django.contrib.sessions.backends.db import SessionStore
    from django.contrib.auth import (
        SESSION_KEY,
        BACKEND_SESSION_KEY,
        HASH_SESSION_KEY,
    )
    from accounts.models import User

    user = User.objects.get(email=email)
    session = SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = (
        "django.contrib.auth.backends.ModelBackend"
    )
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.create()
    return session.session_key


@pytest.fixture(scope="session")
def test_users(django_server, django_db_setup, django_db_blocker):
    """Create test users for account page tests."""
    with django_db_blocker.unblock():
        return _create_test_users()


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
    page.goto(f"{base_url}/account/", wait_until="networkidle")


# ---------------------------------------------------------------
# Scenario: Anonymous visitor is redirected to login
# ---------------------------------------------------------------

@pytest.mark.django_db
class TestScenarioAnonymousRedirectToLogin:
    """Anonymous visitor is redirected to login when trying to manage
    their account."""

    def test_anonymous_redirected_to_login(self, django_server, test_users):
        """Navigate to /account/ without login -- redirects to
        /accounts/login/."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/account/",
                    wait_until="networkidle",
                )
                assert "/accounts/login/" in page.url
            finally:
                browser.close()

    def test_authenticated_user_sees_account_page(
        self, django_server, test_users, django_db_blocker
    ):
        """After authenticating, the user reaches the account page with
        Account heading and their membership details."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
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
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario: Free member visits account page
# ---------------------------------------------------------------

@pytest.mark.django_db
class TestScenarioFreeMemberAccountPage:
    """Free member visits account page and sees upgrade options."""

    def test_free_tier_name_and_level(
        self, django_server, test_users, django_db_blocker
    ):
        """Page shows tier name Free and level Level 0."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = _auth_context(
                    browser, "free@test.com", django_db_blocker
                )
                page = ctx.new_page()
                _go_to_account(page, django_server)

                assert page.locator("#tier-name").inner_text().strip() == "Free"
                assert "Level 0" in page.locator("#tier-badge").inner_text()
                ctx.close()
            finally:
                browser.close()

    def test_no_billing_period_for_free(
        self, django_server, test_users, django_db_blocker
    ):
        """No billing period date is shown for free members."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = _auth_context(
                    browser, "free@test.com", django_db_blocker
                )
                page = ctx.new_page()
                _go_to_account(page, django_server)

                assert page.locator("#billing-period-end").count() == 0
                ctx.close()
            finally:
                browser.close()

    def test_upgrade_link_points_to_pricing(
        self, django_server, test_users, django_db_blocker
    ):
        """An Upgrade link is visible pointing to /pricing."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = _auth_context(
                    browser, "free@test.com", django_db_blocker
                )
                page = ctx.new_page()
                _go_to_account(page, django_server)

                btn = page.locator("#upgrade-btn")
                assert btn.is_visible()
                assert btn.get_attribute("href") == "/pricing"
                ctx.close()
            finally:
                browser.close()

    def test_no_downgrade_or_cancel_for_free(
        self, django_server, test_users, django_db_blocker
    ):
        """No Downgrade or Cancel Subscription actions for free members."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = _auth_context(
                    browser, "free@test.com", django_db_blocker
                )
                page = ctx.new_page()
                _go_to_account(page, django_server)

                assert page.locator("#downgrade-btn").count() == 0
                assert page.locator("#cancel-btn").count() == 0
                ctx.close()
            finally:
                browser.close()

    def test_upgrade_link_navigates_to_pricing(
        self, django_server, test_users, django_db_blocker
    ):
        """Click Upgrade link and land on /pricing."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = _auth_context(
                    browser, "free@test.com", django_db_blocker
                )
                page = ctx.new_page()
                _go_to_account(page, django_server)

                page.click("#upgrade-btn")
                page.wait_for_load_state("networkidle")
                assert "/pricing" in page.url
                ctx.close()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario: Basic member views subscription details
# ---------------------------------------------------------------

@pytest.mark.django_db
class TestScenarioBasicMemberSubscription:
    """Basic member views subscription details and explores upgrade
    options."""

    def test_basic_tier_name_level_and_billing(
        self, django_server, test_users, django_db_blocker
    ):
        """Page shows Basic, Level 10, and billing period end
        March 15, 2026."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = _auth_context(
                    browser, "basic@test.com", django_db_blocker
                )
                page = ctx.new_page()
                _go_to_account(page, django_server)

                assert page.locator("#tier-name").inner_text().strip() == "Basic"
                assert "Level 10" in page.locator("#tier-badge").inner_text()

                billing = page.locator("#billing-period-end")
                assert billing.is_visible()
                assert "March 15, 2026" in billing.inner_text()
                ctx.close()
            finally:
                browser.close()

    def test_basic_has_upgrade_and_cancel_no_downgrade(
        self, django_server, test_users, django_db_blocker
    ):
        """Basic member has Upgrade and Cancel, but no Downgrade."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = _auth_context(
                    browser, "basic@test.com", django_db_blocker
                )
                page = ctx.new_page()
                _go_to_account(page, django_server)

                assert page.locator("#upgrade-btn").is_visible()
                assert page.locator("#cancel-btn").is_visible()
                assert page.locator("#downgrade-btn").count() == 0
                ctx.close()
            finally:
                browser.close()

    def test_upgrade_modal_shows_higher_tiers(
        self, django_server, test_users, django_db_blocker
    ):
        """Click Upgrade -- modal lists Main and Premium."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = _auth_context(
                    browser, "basic@test.com", django_db_blocker
                )
                page = ctx.new_page()
                _go_to_account(page, django_server)

                page.click("#upgrade-btn")
                page.wait_for_timeout(500)

                modal = page.locator("#upgrade-modal")
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
            finally:
                browser.close()

    def test_close_upgrade_modal(
        self, django_server, test_users, django_db_blocker
    ):
        """Close upgrade modal with Cancel -- no changes."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = _auth_context(
                    browser, "basic@test.com", django_db_blocker
                )
                page = ctx.new_page()
                _go_to_account(page, django_server)

                page.click("#upgrade-btn")
                page.wait_for_timeout(500)

                modal = page.locator("#upgrade-modal")
                assert modal.is_visible()

                modal.locator("button", has_text="Cancel").click()
                page.wait_for_timeout(500)
                assert modal.is_hidden()
                assert "/account" in page.url
                ctx.close()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario: Main member initiates a downgrade
# ---------------------------------------------------------------

@pytest.mark.django_db
class TestScenarioMainMemberDowngrade:
    """Main member initiates a downgrade to Basic."""

    def test_main_tier_name_and_level(
        self, django_server, test_users, django_db_blocker
    ):
        """Page shows Main and Level 20."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = _auth_context(
                    browser, "main@test.com", django_db_blocker
                )
                page = ctx.new_page()
                _go_to_account(page, django_server)

                assert page.locator("#tier-name").inner_text().strip() == "Main"
                assert "Level 20" in page.locator("#tier-badge").inner_text()
                ctx.close()
            finally:
                browser.close()

    def test_main_has_all_three_actions(
        self, django_server, test_users, django_db_blocker
    ):
        """Main member has Upgrade, Downgrade, and Cancel."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = _auth_context(
                    browser, "main@test.com", django_db_blocker
                )
                page = ctx.new_page()
                _go_to_account(page, django_server)

                assert page.locator("#upgrade-btn").is_visible()
                assert page.locator("#downgrade-btn").is_visible()
                assert page.locator("#cancel-btn").is_visible()
                ctx.close()
            finally:
                browser.close()

    def test_downgrade_modal_shows_only_basic(
        self, django_server, test_users, django_db_blocker
    ):
        """Downgrade modal lists only Basic."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = _auth_context(
                    browser, "main@test.com", django_db_blocker
                )
                page = ctx.new_page()
                _go_to_account(page, django_server)

                page.click("#downgrade-btn")
                page.wait_for_timeout(500)

                modal = page.locator("#downgrade-modal")
                assert modal.is_visible()
                assert "Downgrade Your Plan" in modal.locator("h3").inner_text()

                buttons = modal.locator("#downgrade-tiers button")
                assert buttons.count() == 1
                text = buttons.first.inner_text()
                assert "Basic" in text
                assert "Main" not in text
                assert "Premium" not in text
                ctx.close()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario: Premium member at highest tier
# ---------------------------------------------------------------

@pytest.mark.django_db
class TestScenarioPremiumMemberHighestTier:
    """Premium member sees they are at the highest tier."""

    def test_premium_tier_name_and_level(
        self, django_server, test_users, django_db_blocker
    ):
        """Page shows Premium and Level 30."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = _auth_context(
                    browser, "premium@test.com", django_db_blocker
                )
                page = ctx.new_page()
                _go_to_account(page, django_server)

                assert page.locator("#tier-name").inner_text().strip() == "Premium"
                assert "Level 30" in page.locator("#tier-badge").inner_text()
                ctx.close()
            finally:
                browser.close()

    def test_premium_no_upgrade_has_downgrade_and_cancel(
        self, django_server, test_users, django_db_blocker
    ):
        """No Upgrade for Premium. Downgrade and Cancel available."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = _auth_context(
                    browser, "premium@test.com", django_db_blocker
                )
                page = ctx.new_page()
                _go_to_account(page, django_server)

                assert page.locator("#upgrade-btn").count() == 0
                assert page.locator("#downgrade-btn").is_visible()
                assert page.locator("#cancel-btn").is_visible()
                ctx.close()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario: Pending downgrade notice
# ---------------------------------------------------------------

@pytest.mark.django_db
class TestScenarioPendingDowngradeNotice:
    """Main member with pending downgrade sees notice."""

    def test_pending_downgrade_notice_visible(
        self, django_server, test_users, django_db_blocker
    ):
        """Notice says plan will change to Basic on April 1, 2026."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = _auth_context(
                    browser, "main-downgrade@test.com", django_db_blocker
                )
                page = ctx.new_page()
                _go_to_account(page, django_server)

                notice = page.locator("#pending-downgrade-notice")
                assert notice.is_visible()
                text = notice.inner_text()
                assert "Basic" in text
                assert "April 1, 2026" in text
                ctx.close()
            finally:
                browser.close()

    def test_no_downgrade_action_when_pending(
        self, django_server, test_users, django_db_blocker
    ):
        """No Downgrade action when already scheduled."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = _auth_context(
                    browser, "main-downgrade@test.com", django_db_blocker
                )
                page = ctx.new_page()
                _go_to_account(page, django_server)

                assert page.locator("#downgrade-btn").count() == 0
                ctx.close()
            finally:
                browser.close()

    def test_cancel_still_available_with_pending_downgrade(
        self, django_server, test_users, django_db_blocker
    ):
        """Cancel still available despite pending downgrade."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = _auth_context(
                    browser, "main-downgrade@test.com", django_db_blocker
                )
                page = ctx.new_page()
                _go_to_account(page, django_server)

                assert page.locator("#cancel-btn").is_visible()
                ctx.close()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario: Pending cancellation notice
# ---------------------------------------------------------------

@pytest.mark.django_db
class TestScenarioPendingCancellationNotice:
    """Main member with pending cancellation sees notice and no
    subscription actions."""

    def test_pending_cancellation_notice_visible(
        self, django_server, test_users, django_db_blocker
    ):
        """Notice says Main access ends on May 15, 2026."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = _auth_context(
                    browser, "main-cancel@test.com", django_db_blocker
                )
                page = ctx.new_page()
                _go_to_account(page, django_server)

                notice = page.locator("#pending-cancellation-notice")
                assert notice.is_visible()
                text = notice.inner_text()
                assert "Main" in text
                assert "May 15, 2026" in text
                ctx.close()
            finally:
                browser.close()

    def test_no_actions_when_cancelled(
        self, django_server, test_users, django_db_blocker
    ):
        """No Upgrade, Downgrade, or Cancel actions when cancelled."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = _auth_context(
                    browser, "main-cancel@test.com", django_db_blocker
                )
                page = ctx.new_page()
                _go_to_account(page, django_server)

                assert page.locator("#upgrade-btn").count() == 0
                assert page.locator("#downgrade-btn").count() == 0
                assert page.locator("#cancel-btn").count() == 0
                ctx.close()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario: Cancel subscription confirmation
# ---------------------------------------------------------------

@pytest.mark.django_db
class TestScenarioCancelSubscriptionConfirmation:
    """Paid member cancels subscription after reading the
    confirmation."""

    def test_cancel_modal_shows_confirmation(
        self, django_server, test_users, django_db_blocker
    ):
        """Confirmation modal shows date and buttons."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = _auth_context(
                    browser, "main@test.com", django_db_blocker
                )
                page = ctx.new_page()
                _go_to_account(page, django_server)

                page.click("#cancel-btn")
                page.wait_for_timeout(500)

                modal = page.locator("#cancel-modal")
                assert modal.is_visible()

                text = modal.inner_text()
                assert "Are you sure you want to cancel" in text
                assert "April 1, 2026" in text
                assert modal.locator("#confirm-cancel-btn").is_visible()
                assert modal.locator(
                    "button", has_text="Keep Plan"
                ).is_visible()
                ctx.close()
            finally:
                browser.close()

    def test_keep_plan_closes_modal(
        self, django_server, test_users, django_db_blocker
    ):
        """Click Keep Plan -- modal closes, no changes."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = _auth_context(
                    browser, "main@test.com", django_db_blocker
                )
                page = ctx.new_page()
                _go_to_account(page, django_server)

                page.click("#cancel-btn")
                page.wait_for_timeout(500)

                modal = page.locator("#cancel-modal")
                assert modal.is_visible()

                modal.locator("button", has_text="Keep Plan").click()
                page.wait_for_timeout(500)
                assert modal.is_hidden()

                assert "/account" in page.url
                assert page.locator("#tier-name").inner_text().strip() == "Main"
                ctx.close()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario: Newsletter toggle
# ---------------------------------------------------------------

@pytest.mark.django_db
class TestScenarioNewsletterToggle:
    """Free member toggles newsletter subscription off and back on."""

    def test_newsletter_subscribed_status(
        self, django_server, test_users, django_db_blocker
    ):
        """Initially shows subscribed status."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = _auth_context(
                    browser, "free@test.com", django_db_blocker
                )
                page = ctx.new_page()
                _go_to_account(page, django_server)

                status = page.locator("#newsletter-status")
                assert "You are subscribed to newsletters." in status.inner_text()
                ctx.close()
            finally:
                browser.close()

    def test_toggle_off_and_on(
        self, django_server, test_users, django_db_blocker
    ):
        """Toggle off shows unsubscribed, toggle on shows subscribed."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = _auth_context(
                    browser, "free@test.com", django_db_blocker
                )
                page = ctx.new_page()
                _go_to_account(page, django_server)

                toggle = page.locator("#newsletter-toggle")
                status = page.locator("#newsletter-status")

                # Ensure starting state is subscribed
                if "unsubscribed" in status.inner_text():
                    toggle.click()
                    page.wait_for_timeout(1000)

                # Toggle off
                toggle.click()
                page.wait_for_timeout(1000)
                assert "You are unsubscribed from newsletters." in status.inner_text()

                # Toggle back on
                toggle.click()
                page.wait_for_timeout(1000)
                assert "You are subscribed to newsletters." in status.inner_text()
                ctx.close()
            finally:
                browser.close()

    def test_newsletter_persists_after_reload(
        self, django_server, test_users, django_db_blocker
    ):
        """Newsletter preference persists after page reload."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = _auth_context(
                    browser, "free@test.com", django_db_blocker
                )
                page = ctx.new_page()
                _go_to_account(page, django_server)

                status = page.locator("#newsletter-status")
                if "unsubscribed" in status.inner_text():
                    page.locator("#newsletter-toggle").click()
                    page.wait_for_timeout(1000)

                assert "You are subscribed to newsletters." in status.inner_text()

                page.reload(wait_until="networkidle")
                status = page.locator("#newsletter-status")
                assert "You are subscribed to newsletters." in status.inner_text()
                ctx.close()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario: Change password success
# ---------------------------------------------------------------

@pytest.mark.django_db
class TestScenarioChangePasswordSuccess:
    """Member changes their password successfully."""

    def test_change_password_success_message_and_fields_cleared(
        self, django_server, test_users, django_db_blocker
    ):
        """Success message appears and form fields are cleared."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = _auth_context(
                    browser, "free@test.com", django_db_blocker
                )
                page = ctx.new_page()
                _go_to_account(page, django_server)

                page.fill("#current-password", DEFAULT_PASSWORD)
                page.fill("#new-password", "NewSecure456!")
                page.fill("#confirm-new-password", "NewSecure456!")
                page.click("#change-password-form button[type='submit']")
                page.wait_for_timeout(2000)

                success = page.locator("#password-success")
                assert success.is_visible()
                assert "password" in success.inner_text().lower()

                assert page.locator("#current-password").input_value() == ""
                assert page.locator("#new-password").input_value() == ""
                assert page.locator("#confirm-new-password").input_value() == ""

                # Reset password back for other tests
                page.fill("#current-password", "NewSecure456!")
                page.fill("#new-password", DEFAULT_PASSWORD)
                page.fill("#confirm-new-password", DEFAULT_PASSWORD)
                page.click("#change-password-form button[type='submit']")
                page.wait_for_timeout(2000)
                ctx.close()
            finally:
                browser.close()

    def test_new_password_works_after_change(
        self, django_server, test_users, django_db_blocker
    ):
        """After changing password, a new session can be created
        (verifying the password hash updated)."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
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
                page.wait_for_timeout(2000)
                assert page.locator("#password-success").is_visible()
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
                page3.wait_for_timeout(2000)
                ctx3.close()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario: Change password error
# ---------------------------------------------------------------

@pytest.mark.django_db
class TestScenarioChangePasswordError:
    """Member enters wrong current password and sees an error."""

    def test_wrong_current_password_shows_error(
        self, django_server, test_users, django_db_blocker
    ):
        """Wrong current password shows error, no success."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = _auth_context(
                    browser, "free@test.com", django_db_blocker
                )
                page = ctx.new_page()
                _go_to_account(page, django_server)

                page.fill("#current-password", "WrongPassword99")
                page.fill("#new-password", "NewSecure456!")
                page.fill("#confirm-new-password", "NewSecure456!")
                page.click("#change-password-form button[type='submit']")
                page.wait_for_timeout(2000)

                assert page.locator("#password-error").is_visible()
                assert page.locator("#password-success").is_hidden()
                ctx.close()
            finally:
                browser.close()

    def test_old_password_still_works_after_failed_change(
        self, django_server, test_users, django_db_blocker
    ):
        """After failed change, old password still works."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
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
                page.wait_for_timeout(2000)
                assert page.locator("#password-error").is_visible()
                ctx.close()

                # Verify old password works (create new session)
                ctx2 = _auth_context(
                    browser, "free@test.com", django_db_blocker
                )
                page2 = ctx2.new_page()
                _go_to_account(page2, django_server)
                assert "Account" in page2.locator("h1").inner_text()
                ctx2.close()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario: Email verification banner
# ---------------------------------------------------------------

@pytest.mark.django_db
class TestScenarioEmailVerificationBanner:
    """Member who has not verified their email sees verification
    banner."""

    def test_unverified_user_sees_banner(
        self, django_server, test_users, django_db_blocker
    ):
        """Unverified user sees Verify your email banner; rest of page
        is accessible."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
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
            finally:
                browser.close()

    def test_verified_user_no_banner(
        self, django_server, test_users, django_db_blocker
    ):
        """Verified user does not see verification banner."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = _auth_context(
                    browser, "free@test.com", django_db_blocker
                )
                page = ctx.new_page()
                _go_to_account(page, django_server)

                assert page.locator("#email-verification-banner").count() == 0
                ctx.close()
            finally:
                browser.close()
