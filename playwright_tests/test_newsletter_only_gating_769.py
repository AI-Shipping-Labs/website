"""Playwright E2E tests for newsletter-only UI gating (issue #769).

The 9 scenarios from the groomed spec:

1. Newsletter-only reader sees a focused account page instead of the
   full member dashboard (redirect + banner + trimmed cards).
2. Newsletter-only reader's navbar hides platform affordances (no bell,
   no Profile or Plan items).
3. Newsletter reader activates by setting a password and unlocks the
   dashboard.
4. Signed-up but never-activated user still sees the full UI.
5. Active member's UI is unchanged.
6. Newsletter subscriber who later activated sees the full UI even
   though signup_source stays "newsletter".
7. Anonymous visitor is unaffected by the gating.
8. Staff member with anomalous newsletter source still sees Studio.
9. Newsletter-only user can still adjust newsletter preferences.

Run:
    uv run pytest playwright_tests/test_newsletter_only_gating_769.py -v
"""

import os

import pytest

from playwright_tests.conftest import (
    DEFAULT_PASSWORD,
    VIEWPORT,
)
from playwright_tests.conftest import (
    create_session_for_user as _create_session_for_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

# Local-only: uses DB seeding + cookie-injection session helpers that
# don't work against the dev environment.
pytestmark = pytest.mark.local_only


def _seed_users():
    """Create the four test users defined in the spec.

    Called within ``django_db_blocker.unblock()``. Closes the DB
    connection so the in-process server thread doesn't deadlock against
    a stray SQLite lock.
    """
    from django.db import connection

    from accounts.models import User
    from payments.models import Tier
    from playwright_tests.conftest import ensure_tiers

    ensure_tiers()
    tiers = {t.slug: t for t in Tier.objects.all()}
    users = {}

    # 1. Newsletter-only verified subscriber (the gated state).
    nl, _ = User.objects.get_or_create(
        email="newsletter@test.com",
        defaults={
            "email_verified": True,
            "signup_source": "newsletter",
            "account_activated": False,
            "tier": tiers["free"],
            "unsubscribed": False,
        },
    )
    nl.set_password(DEFAULT_PASSWORD)
    nl.signup_source = "newsletter"
    nl.account_activated = False
    nl.email_verified = True
    nl.tier = tiers["free"]
    nl.unsubscribed = False
    nl.save()
    users["newsletter"] = nl

    # 2. Signed-up-but-never-activated (NOT gated — gets the full UI).
    su, _ = User.objects.get_or_create(
        email="signup@test.com",
        defaults={
            "email_verified": True,
            "signup_source": "signup",
            "account_activated": False,
            "tier": tiers["free"],
            "unsubscribed": False,
        },
    )
    su.set_password(DEFAULT_PASSWORD)
    su.signup_source = "signup"
    su.account_activated = False
    su.email_verified = True
    su.tier = tiers["free"]
    su.save()
    users["signup"] = su

    # 3. Active main-tier member.
    mb, _ = User.objects.get_or_create(
        email="member@test.com",
        defaults={
            "email_verified": True,
            "signup_source": "signup",
            "account_activated": True,
            "tier": tiers["main"],
            "subscription_id": "sub_member_test_123",
            "unsubscribed": False,
        },
    )
    mb.set_password(DEFAULT_PASSWORD)
    mb.signup_source = "signup"
    mb.account_activated = True
    mb.email_verified = True
    mb.tier = tiers["main"]
    mb.subscription_id = "sub_member_test_123"
    mb.save()
    users["member"] = mb

    # 4. Newsletter subscriber who later activated (NOT gated).
    anl, _ = User.objects.get_or_create(
        email="activated_newsletter@test.com",
        defaults={
            "email_verified": True,
            "signup_source": "newsletter",
            "account_activated": True,
            "tier": tiers["free"],
            "unsubscribed": False,
        },
    )
    anl.set_password(DEFAULT_PASSWORD)
    anl.signup_source = "newsletter"
    anl.account_activated = True
    anl.email_verified = True
    anl.save()
    users["activated_newsletter"] = anl

    # 5. Staff edge case: staff with the anomalous newsletter source.
    st, _ = User.objects.get_or_create(
        email="staff_nl@test.com",
        defaults={
            "email_verified": True,
            "signup_source": "newsletter",
            "account_activated": False,
            "is_staff": True,
            "tier": tiers["free"],
        },
    )
    st.set_password(DEFAULT_PASSWORD)
    st.signup_source = "newsletter"
    st.account_activated = False
    st.is_staff = True
    st.email_verified = True
    st.save()
    users["staff_nl"] = st

    connection.close()
    return users


@pytest.fixture
def gating_users(django_server, django_db_blocker):
    from django.db import connection

    with django_db_blocker.unblock():
        users = _seed_users()
        connection.close()
    return users


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


# ---------------------------------------------------------------
# Scenario 1: Newsletter-only reader sees a focused account page
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestNewsletterOnlyRedirectAndTrimmedAccount:
    """``/`` redirects to ``/account/`` with a banner; account page is trimmed."""

    def test_root_redirects_to_account(
        self, django_server, gating_users, django_db_blocker, browser
    ):
        ctx = _auth_context(browser, "newsletter@test.com", django_db_blocker)
        page = ctx.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        assert page.url.endswith("/account/")
        ctx.close()

    def test_info_banner_explains_why(
        self, django_server, gating_users, django_db_blocker, browser
    ):
        ctx = _auth_context(browser, "newsletter@test.com", django_db_blocker)
        page = ctx.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        # The /account/ template renders messages inline in its own
        # ``account-messages-region`` so the banner is visible above
        # the heading (not hidden behind the fixed header).
        messages = page.locator('[data-testid="account-messages-region"]')
        assert "newsletter" in messages.inner_text().lower()
        assert "password" in messages.inner_text().lower()
        ctx.close()

    def test_email_preferences_section_visible(
        self, django_server, gating_users, django_db_blocker, browser
    ):
        ctx = _auth_context(browser, "newsletter@test.com", django_db_blocker)
        page = ctx.new_page()
        page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
        assert page.locator("#email-preferences-section").count() == 1
        assert page.locator("#newsletter-toggle").count() == 1
        ctx.close()

    def test_profile_membership_slack_password_timezone_hidden(
        self, django_server, gating_users, django_db_blocker, browser
    ):
        ctx = _auth_context(browser, "newsletter@test.com", django_db_blocker)
        page = ctx.new_page()
        page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
        assert page.locator("#profile-section").count() == 0
        assert page.locator("#change-password-section").count() == 0
        assert page.locator("#display-preferences-section").count() == 0
        assert page.locator("#tier-name").count() == 0
        ctx.close()

    def test_set_password_cta_visible(
        self, django_server, gating_users, django_db_blocker, browser
    ):
        ctx = _auth_context(browser, "newsletter@test.com", django_db_blocker)
        page = ctx.new_page()
        page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
        cta = page.locator('[data-testid="newsletter-only-cta"]')
        assert cta.count() == 1
        btn = page.locator('[data-testid="newsletter-only-set-password-btn"]')
        assert btn.count() == 1
        href = btn.get_attribute("href")
        assert href.startswith("/accounts/password-reset-request?email=")
        assert "newsletter%40test.com" in href
        ctx.close()


# ---------------------------------------------------------------
# Scenario 2: Newsletter-only reader's navbar hides platform affordances
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestNewsletterOnlyNavbarTrimmed:

    def test_no_notification_bell(
        self, django_server, gating_users, django_db_blocker, browser
    ):
        ctx = _auth_context(browser, "newsletter@test.com", django_db_blocker)
        page = ctx.new_page()
        page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
        assert page.locator("#notification-bell-btn").count() == 0
        ctx.close()

    def test_account_dropdown_no_profile_no_plan(
        self, django_server, gating_users, django_db_blocker, browser
    ):
        ctx = _auth_context(browser, "newsletter@test.com", django_db_blocker)
        page = ctx.new_page()
        page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
        # Open the account menu.
        page.click('[data-testid="account-menu-trigger"]')
        dropdown = page.locator('[data-testid="account-menu-dropdown"]')
        text = dropdown.inner_text()
        assert "Account" in text
        assert "Log out" in text
        # Profile and Plan items must be absent.
        assert dropdown.locator(
            'a[href="/account/#profile"]'
        ).count() == 0
        assert dropdown.locator(
            '[data-testid="header-plan-link"]'
        ).count() == 0
        ctx.close()


# ---------------------------------------------------------------
# Scenario 3: Newsletter reader activates by setting a password
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestActivationUnlocksFullUI:

    def test_set_password_link_lands_on_prefilled_form(
        self, django_server, gating_users, django_db_blocker, browser
    ):
        ctx = _auth_context(browser, "newsletter@test.com", django_db_blocker)
        page = ctx.new_page()
        page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
        page.click('[data-testid="newsletter-only-set-password-btn"]')
        page.wait_for_url(
            "**/accounts/password-reset-request?email=*"
        )
        email_input = page.locator('#password-reset-email')
        assert email_input.input_value() == "newsletter@test.com"
        ctx.close()

    def test_dashboard_unlocks_after_mark_activated(
        self, django_server, gating_users, django_db_blocker, browser
    ):
        """Simulates the post-set-password state by flipping
        ``account_activated`` directly. The very next request to ``/``
        renders the dashboard instead of the redirect."""
        ctx = _auth_context(browser, "newsletter@test.com", django_db_blocker)
        page = ctx.new_page()
        # Pre-activation: redirected to /account/.
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        assert page.url.endswith("/account/")

        # Out-of-band activation.
        from accounts.utils.activation import mark_activated
        with django_db_blocker.unblock():
            from django.db import connection

            from accounts.models import User
            user = User.objects.get(email="newsletter@test.com")
            mark_activated(user)
            connection.close()

        # Post-activation: dashboard renders.
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        assert not page.url.endswith("/account/")
        # Notification bell is back.
        assert page.locator("#notification-bell-btn").count() == 1
        # Account dropdown now lists Profile.
        page.click('[data-testid="account-menu-trigger"]')
        dropdown = page.locator('[data-testid="account-menu-dropdown"]')
        assert dropdown.locator('a[href="/account/#profile"]').count() == 1
        ctx.close()


# ---------------------------------------------------------------
# Scenario 4: Signed-up-but-never-activated user still sees the full UI
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestSignupSourceUserFullUI:

    def test_dashboard_renders_without_redirect(
        self, django_server, gating_users, django_db_blocker, browser
    ):
        ctx = _auth_context(browser, "signup@test.com", django_db_blocker)
        page = ctx.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        assert page.url.rstrip("/").endswith(django_server.rstrip("/"))
        # Notification bell visible.
        assert page.locator("#notification-bell-btn").count() == 1
        ctx.close()

    def test_account_page_has_all_sections(
        self, django_server, gating_users, django_db_blocker, browser
    ):
        ctx = _auth_context(browser, "signup@test.com", django_db_blocker)
        page = ctx.new_page()
        page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
        assert page.locator("#profile-section").count() == 1
        assert page.locator("#change-password-section").count() == 1
        assert page.locator("#display-preferences-section").count() == 1
        ctx.close()


# ---------------------------------------------------------------
# Scenario 5: Active member's UI is unchanged
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestActiveMemberFullUI:

    def test_dashboard_then_account_full(
        self, django_server, gating_users, django_db_blocker, browser
    ):
        ctx = _auth_context(browser, "member@test.com", django_db_blocker)
        page = ctx.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        assert page.locator("#notification-bell-btn").count() == 1

        page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
        assert page.locator("#profile-section").count() == 1
        assert page.locator("#change-password-section").count() == 1
        assert page.locator("#display-preferences-section").count() == 1
        assert page.locator("#tier-name").count() == 1
        ctx.close()


# ---------------------------------------------------------------
# Scenario 6: Newsletter subscriber who activated sees the full UI
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestActivatedNewsletterFullUI:

    def test_dashboard_renders(
        self, django_server, gating_users, django_db_blocker, browser
    ):
        ctx = _auth_context(
            browser, "activated_newsletter@test.com", django_db_blocker,
        )
        page = ctx.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        assert not page.url.endswith("/account/")
        assert page.locator("#notification-bell-btn").count() == 1
        ctx.close()

    def test_account_page_renders_full(
        self, django_server, gating_users, django_db_blocker, browser
    ):
        ctx = _auth_context(
            browser, "activated_newsletter@test.com", django_db_blocker,
        )
        page = ctx.new_page()
        page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
        assert page.locator("#profile-section").count() == 1
        assert page.locator(
            '[data-testid="newsletter-only-cta"]'
        ).count() == 0
        ctx.close()


# ---------------------------------------------------------------
# Scenario 7: Anonymous visitor is unaffected
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestAnonymousUnaffected:

    def test_root_renders_public_homepage(
        self, django_server, gating_users, browser
    ):
        ctx = browser.new_context(viewport=VIEWPORT)
        page = ctx.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        # No redirect to /account/.
        assert not page.url.endswith("/account/")
        # Sign in CTA visible (anonymous header signature).
        assert page.locator('a:has-text("Sign in")').count() >= 1
        ctx.close()

    def test_account_redirects_to_login(
        self, django_server, gating_users, browser
    ):
        ctx = browser.new_context(viewport=VIEWPORT)
        page = ctx.new_page()
        page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
        assert "/accounts/login/" in page.url
        ctx.close()


# ---------------------------------------------------------------
# Scenario 8: Staff with newsletter source still sees Studio
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestStaffOverride:

    def test_studio_link_survives_gating(
        self, django_server, gating_users, django_db_blocker, browser
    ):
        ctx = _auth_context(browser, "staff_nl@test.com", django_db_blocker)
        page = ctx.new_page()
        page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
        # Predicate fires: trimmed view (no notification bell).
        assert page.locator("#notification-bell-btn").count() == 0
        # But Studio is still in the account dropdown.
        page.click('[data-testid="account-menu-trigger"]')
        dropdown = page.locator('[data-testid="account-menu-dropdown"]')
        assert "Studio" in dropdown.inner_text()
        ctx.close()


# ---------------------------------------------------------------
# Scenario 9: Newsletter-only user can still adjust newsletter prefs
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestNewsletterOnlyCanTogglePrefs:

    def test_toggle_newsletter_then_reload_stays_trimmed(
        self, django_server, gating_users, django_db_blocker, browser
    ):
        ctx = _auth_context(browser, "newsletter@test.com", django_db_blocker)
        page = ctx.new_page()
        page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
        # The toggle button drives a fetch -- click it and wait for the
        # status text to flip.
        page.click("#newsletter-toggle")
        page.wait_for_function(
            "() => document.querySelector('#newsletter-status').innerText "
            ".includes('Newsletter updates turned off.')"
        )

        # Reload: still trimmed (no Profile / Membership sections).
        page.reload(wait_until="domcontentloaded")
        assert page.locator("#profile-section").count() == 0
        assert page.locator("#email-preferences-section").count() == 1
        ctx.close()
