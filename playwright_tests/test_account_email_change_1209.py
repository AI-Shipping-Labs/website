"""Playwright coverage for withdrawn requests and legacy confirmations (#1260/#1263)."""

import os
from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.conftest import (
    DEFAULT_PASSWORD,
    auth_context,
    create_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only

GENERIC_CONFIRM_ERROR = (
    "This email change link is no longer valid. "
    "Contact support if you still need help updating your login email."
)
EXPIRED_CONFIRM_ERROR = (
    "This email change link has expired. "
    "Contact support if you still need help updating your login email."
)
SUPPORT_HREF = (
    "mailto:contact@aishippinglabs.com?subject=Login%20email%20help"
)


def _login_with_password(page, base_url, email, password=DEFAULT_PASSWORD):
    page.goto(f"{base_url}/accounts/login/", wait_until="domcontentloaded")
    page.fill("#login-email", email)
    page.fill("#login-password", password)
    page.click("#login-submit")


def _seed_member(email, *, tier_slug="basic"):
    from django.db import connection

    user = create_user(email, tier_slug=tier_slug, password=DEFAULT_PASSWORD)
    user.account_activated = True
    user.email_verified = True
    user.save(update_fields=["account_activated", "email_verified"])
    connection.close()
    return user


@pytest.mark.django_db(transaction=True)
class TestRemovedMemberEmailChangeSurface:
    @pytest.mark.core
    def test_desktop_settings_omits_card_and_preferences_still_work(
        self, django_server, django_db_blocker, browser
    ):
        with django_db_blocker.unblock():
            _seed_member("old-member-1209@test.com")

        context = auth_context(browser, "old-member-1209@test.com")
        try:
            page = context.new_page()
            page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
            expect(page.locator("#membership-section")).to_be_visible()
            expect(page.locator("#email-preferences-section")).to_be_visible()
            expect(page.locator("#login-email-section")).to_have_count(0)
            expect(page.locator("#change-email-form")).to_have_count(0)
            expect(page.get_by_text("Current login email", exact=True)).to_have_count(0)
            expect(page.get_by_text("New login email", exact=True)).to_have_count(0)
            expect(page.get_by_text("Send verification link", exact=True)).to_have_count(0)
            assert page.evaluate(
                """() => Boolean(
                    document.querySelector('#membership-section')
                      .compareDocumentPosition(
                        document.querySelector('#email-preferences-section')
                      ) & Node.DOCUMENT_POSITION_FOLLOWING
                )"""
            )

            page.click("#newsletter-toggle")
            expect(page.locator("#newsletter-status")).to_contain_text(
                "Newsletter updates turned"
            )
            artifacts = Path(".tmp/issue-1260")
            artifacts.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=artifacts / "account-desktop.png", full_page=True)
        finally:
            context.close()

    def test_mobile_settings_has_no_gap_or_overflow(
        self, django_server, django_db_blocker, browser
    ):
        with django_db_blocker.unblock():
            _seed_member("mobile-member-1209@test.com", tier_slug="main")

        context = auth_context(browser, "mobile-member-1209@test.com")
        try:
            page = context.new_page()
            page.set_viewport_size({"width": 390, "height": 844})
            page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
            expect(page.locator("#membership-section")).to_be_visible()
            expect(page.locator("#email-preferences-section")).to_be_visible()
            expect(page.locator("#login-email-section")).to_have_count(0)
            expect(page.locator("#change-password-section")).to_be_attached()
            expect(page.locator("#profile-section")).to_be_attached()
            expect(page.locator("#privacy-data-section")).to_be_attached()
            assert page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
            )
            page.locator("#privacy-data-section").scroll_into_view_if_needed()
            expect(page.locator("#privacy-data-section")).to_be_visible()
            artifacts = Path(".tmp/issue-1260")
            artifacts.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=artifacts / "account-mobile.png", full_page=True)
        finally:
            context.close()

    def test_request_route_returns_404_without_side_effects(
        self, django_server, django_db_blocker, browser
    ):
        with django_db_blocker.unblock():
            _seed_member("withdrawn-route-1209@test.com")

        context = auth_context(browser, "withdrawn-route-1209@test.com")
        try:
            page = context.new_page()
            page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
            for method in ("GET", "POST"):
                status = page.evaluate(
                    """async ({method}) => {
                        const csrf = document.cookie
                          .split('; ')
                          .find((row) => row.startsWith('csrftoken='))
                          ?.split('=')[1] || '';
                        const response = await fetch(
                          '/account/api/change-email/request',
                          {
                            method,
                            headers: {
                              'Content-Type': 'application/json',
                              'X-CSRFToken': csrf,
                            },
                            body: method === 'POST'
                              ? JSON.stringify({
                                  new_email: 'new-withdrawn-1209@test.com',
                                  current_password: 'TestPassword123!',
                                })
                              : undefined,
                          }
                        );
                        return response.status;
                    }""",
                    {"method": method},
                )
                assert status == 404
        finally:
            context.close()

        anonymous = browser.new_context()
        try:
            page = anonymous.new_page()
            page.goto(
                f"{django_server}/accounts/login/",
                wait_until="domcontentloaded",
            )
            for method in ("GET", "POST"):
                status = page.evaluate(
                    """async ({method}) => {
                        const csrf = document.cookie
                          .split('; ')
                          .find((row) => row.startsWith('csrftoken='))
                          ?.split('=')[1] || '';
                        const response = await fetch(
                          '/account/api/change-email/request',
                          {
                            method,
                            headers: {
                              'Content-Type': 'application/json',
                              'X-CSRFToken': csrf,
                            },
                            body: method === 'POST'
                              ? JSON.stringify({
                                  new_email: 'anonymous-new-1209@test.com',
                                })
                              : undefined,
                          }
                        );
                        return response.status;
                    }""",
                    {"method": method},
                )
                assert status == 404
        finally:
            anonymous.close()

        with django_db_blocker.unblock():
            from accounts.models import EmailChangeRequest, User
            from email_app.models import EmailLog

            user = User.objects.get(email="withdrawn-route-1209@test.com")
            assert user.email == "withdrawn-route-1209@test.com"
            assert not EmailChangeRequest.objects.filter(user=user).exists()
            assert not EmailLog.objects.filter(
                email_type="account_email_change_confirm",
                user=user,
            ).exists()


@pytest.mark.django_db(transaction=True)
class TestMemberConfirmsEmailChange:
    @pytest.mark.core
    def test_member_confirms_new_email_and_sees_account_updated(
        self, django_server, django_db_blocker, browser
    ):
        with django_db_blocker.unblock():
            user = _seed_member("confirm-old-1209@test.com")
            from django.db import connection

            from accounts.services.email_change import request_email_change

            _request_obj, token = request_email_change(
                user,
                "confirm-new-1209@test.com",
                current_password=DEFAULT_PASSWORD,
                send=False,
            )
            connection.close()

        page = browser.new_page()
        response = page.goto(
            f"{django_server}/account/change-email/confirm?token={token}",
            wait_until="domcontentloaded",
        )
        assert response is not None and response.status == 200
        expect(page.locator("[data-testid='email-change-result']")).to_be_visible()
        expect(page.get_by_role("heading", name="Email changed")).to_be_visible()
        expect(page.locator("[data-testid='email-change-result-message']")).to_contain_text(
            "Your account email was changed successfully."
        )
        expect(page.locator("[data-testid='email-change-result-cta']")).to_contain_text(
            "Sign In"
        )
        expect(page.locator("[data-testid='email-change-result-cta']")).to_have_attribute(
            "href", "/accounts/login/"
        )
        expect(
            page.locator("[data-testid='email-change-result-cta'] [data-lucide='arrow-right']")
        ).to_be_visible()

        _login_with_password(page, django_server, "confirm-new-1209@test.com")
        page.wait_for_url(f"{django_server}/")

        context = auth_context(browser, "confirm-new-1209@test.com")
        try:
            account_page = context.new_page()
            account_page.goto(
                f"{django_server}/account/",
                wait_until="domcontentloaded",
            )
            expect(account_page.locator("#membership-section")).to_be_visible()
            expect(account_page.locator("#email-preferences-section")).to_be_visible()
            expect(account_page.locator("#login-email-section")).to_have_count(0)
            expect(account_page.locator("#change-email-form")).to_have_count(0)
        finally:
            context.close()

        with django_db_blocker.unblock():
            from accounts.models import EmailAlias, User
            from email_app.models import EmailLog

            changed = User.objects.get(email="confirm-new-1209@test.com")
            assert EmailAlias.objects.filter(
                user=changed,
                email="confirm-old-1209@test.com",
                source=EmailAlias.SOURCE_ACCOUNT_CHANGE,
            ).exists()
            assert EmailLog.objects.filter(
                user=changed,
                recipient_email="confirm-old-1209@test.com",
                email_type="account_email_changed_notice",
            ).exists()

    def test_expired_and_superseded_links_show_recovery_path(
        self, django_server, django_db_blocker, browser
    ):
        with django_db_blocker.unblock():
            expired_user = _seed_member("expired-link-1209@test.com")
            superseded_user = _seed_member("superseded-link-1209@test.com")
            from django.core.cache import cache
            from django.db import connection

            from accounts.models import EmailChangeRequest
            from accounts.services.email_change import request_email_change

            expired_request, expired_token = request_email_change(
                expired_user,
                "expired-1209@test.com",
                current_password=DEFAULT_PASSWORD,
                send=False,
            )
            EmailChangeRequest.objects.filter(pk=expired_request.pk).update(
                expires_at=timezone.now() - timedelta(minutes=5)
            )
            cache.clear()
            first, first_token = request_email_change(
                superseded_user,
                "first-1209@test.com",
                current_password=DEFAULT_PASSWORD,
                send=False,
            )
            cache.clear()
            request_email_change(
                superseded_user,
                "second-1209@test.com",
                current_password=DEFAULT_PASSWORD,
                send=False,
            )
            first.refresh_from_db()
            assert first.invalidated_at is not None
            connection.close()

        page = browser.new_page()
        response = page.goto(
            f"{django_server}/account/change-email/confirm?token={expired_token}",
            wait_until="domcontentloaded",
        )
        assert response is not None and response.status == 400
        expect(page.locator("[data-testid='email-change-result-message']")).to_have_text(
            EXPIRED_CONFIRM_ERROR
        )
        expect(page.locator("[data-testid='email-change-result-cta']")).to_contain_text(
            "Contact support"
        )
        expect(page.locator("[data-testid='email-change-result-cta']")).to_have_attribute(
            "href", SUPPORT_HREF
        )
        expect(
            page.locator("[data-testid='email-change-result-cta'] [data-lucide='mail']")
        ).to_be_visible()
        expect(page.get_by_text("request a new link", exact=False)).to_have_count(0)

        context = auth_context(browser, "superseded-link-1209@test.com")
        try:
            authenticated_page = context.new_page()
            response = authenticated_page.goto(
                f"{django_server}/account/change-email/confirm?token={first_token}",
                wait_until="domcontentloaded",
            )
            assert response is not None and response.status == 400
            expect(
                authenticated_page.locator(
                    "[data-testid='email-change-result-message']"
                )
            ).to_have_text(GENERIC_CONFIRM_ERROR)
            expect(
                authenticated_page.locator("[data-testid='email-change-result-cta']")
            ).to_contain_text("Contact support")
            expect(
                authenticated_page.locator("[data-testid='email-change-result-cta']")
            ).to_have_attribute("href", SUPPORT_HREF)
            expect(
                authenticated_page.locator(
                    "[data-testid='email-change-result-cta'] [data-lucide='mail']"
                )
            ).to_be_visible()
        finally:
            context.close()

        with django_db_blocker.unblock():
            from accounts.models import User

            assert User.objects.get(email="expired-link-1209@test.com")
            assert User.objects.get(email="superseded-link-1209@test.com")
            assert not User.objects.filter(email="first-1209@test.com").exists()

    def test_malformed_link_uses_generic_support_without_side_effects(
        self, django_server, django_db_blocker, browser
    ):
        page = browser.new_page()

        response = page.goto(
            f"{django_server}/account/change-email/confirm?token=unknown-1263",
            wait_until="domcontentloaded",
        )

        assert response is not None and response.status == 400
        expect(page.get_by_role("heading", name="Email change link unavailable")).to_be_visible()
        expect(page.locator("[data-testid='email-change-result-message']")).to_have_text(
            GENERIC_CONFIRM_ERROR
        )
        expect(page.locator("[data-testid='email-change-result-cta']")).to_contain_text(
            "Contact support"
        )
        expect(page.locator("[data-testid='email-change-result-cta']")).to_have_attribute(
            "href", SUPPORT_HREF
        )
        expect(
            page.locator("[data-testid='email-change-result-cta'] [data-lucide='mail']")
        ).to_be_visible()
        expect(page.get_by_text("request a new link", exact=False)).to_have_count(0)

        with django_db_blocker.unblock():
            from accounts.models import EmailAlias, EmailChangeRequest
            from email_app.models import EmailLog

            assert not EmailChangeRequest.objects.exists()
            assert not EmailAlias.objects.exists()
            assert not EmailLog.objects.filter(
                email_type__in=[
                    "account_email_change_confirm",
                    "account_email_changed_notice",
                ]
            ).exists()

    def test_same_user_alias_promotion_preserves_billing_fields(
        self, django_server, django_db_blocker, browser
    ):
        with django_db_blocker.unblock():
            user = _seed_member("alias-primary-1209@test.com")
            user.stripe_customer_id = "cus_alias_1209"
            user.subscription_id = "sub_alias_1209"
            user.save(update_fields=["stripe_customer_id", "subscription_id"])
            from django.db import connection

            from accounts.models import EmailAlias
            from accounts.services.email_change import request_email_change

            EmailAlias.objects.create(
                user=user,
                email="billing-alias-1209@test.com",
            )
            _request_obj, token = request_email_change(
                user,
                "billing-alias-1209@test.com",
                current_password=DEFAULT_PASSWORD,
                send=False,
            )
            connection.close()

        page = browser.new_page()
        page.goto(
            f"{django_server}/account/change-email/confirm?token={token}",
            wait_until="domcontentloaded",
        )
        expect(page.locator("[data-testid='email-change-result-message']")).to_contain_text(
            "account email was changed successfully"
        )

        with django_db_blocker.unblock():
            from accounts.models import EmailAlias, User

            changed = User.objects.get(email="billing-alias-1209@test.com")
            assert changed.stripe_customer_id == "cus_alias_1209"
            assert changed.subscription_id == "sub_alias_1209"
            assert not EmailAlias.objects.filter(
                email="billing-alias-1209@test.com"
            ).exists()
            assert EmailAlias.objects.filter(
                user=changed,
                email="alias-primary-1209@test.com",
            ).exists()

    def test_slack_state_is_marked_stale_without_disconnect_ui(
        self, django_server, django_db_blocker, browser
    ):
        with django_db_blocker.unblock():
            user = _seed_member("slack-change-1209@test.com", tier_slug="main")
            user.slack_member = True
            user.slack_user_id = "U1209"
            user.slack_checked_at = timezone.now()
            user.save(
                update_fields=[
                    "slack_member",
                    "slack_user_id",
                    "slack_checked_at",
                ]
            )
            from django.db import connection

            from accounts.services.email_change import request_email_change

            _request_obj, token = request_email_change(
                user,
                "slack-new-1209@test.com",
                current_password=DEFAULT_PASSWORD,
                send=False,
            )
            connection.close()

        page = browser.new_page()
        page.goto(
            f"{django_server}/account/change-email/confirm?token={token}",
            wait_until="domcontentloaded",
        )
        expect(page.locator("[data-testid='email-change-result-message']")).to_contain_text(
            "account email was changed successfully"
        )

        context = auth_context(browser, "slack-new-1209@test.com")
        try:
            account_page = context.new_page()
            account_page.goto(
                f"{django_server}/account/",
                wait_until="domcontentloaded",
            )
            expect(
                account_page.locator("[data-testid='slack-account-card']")
            ).to_have_count(0)
        finally:
            context.close()

        with django_db_blocker.unblock():
            from accounts.models import User

            changed = User.objects.get(email="slack-new-1209@test.com")
            assert changed.slack_member is True
            assert changed.slack_user_id == "U1209"
            assert changed.slack_checked_at is None


@pytest.mark.django_db(transaction=True)
class TestNewsletterOnlyEmailChangeGating:
    def test_newsletter_only_account_keeps_trimmed_page(self, django_server, django_db_blocker, browser):
        with django_db_blocker.unblock():
            from django.db import connection

            from accounts.models.user import SIGNUP_SOURCE_NEWSLETTER

            user = create_user(
                "newsletter-only-1209@test.com",
                tier_slug="free",
                password=DEFAULT_PASSWORD,
            )
            user.signup_source = SIGNUP_SOURCE_NEWSLETTER
            user.account_activated = False
            user.email_verified = True
            user.save(
                update_fields=[
                    "signup_source",
                    "account_activated",
                    "email_verified",
                ]
            )
            connection.close()

        for width in (1280, 390):
            context = auth_context(browser, "newsletter-only-1209@test.com")
            try:
                page = context.new_page()
                page.set_viewport_size({"width": width, "height": 844})
                page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
                expect(page.locator("#newsletter-only-cta")).to_be_visible()
                expect(page.locator("#email-preferences-section")).to_be_visible()
                expect(page.locator("#login-email-section")).to_have_count(0)
                expect(page.locator("#change-email-form")).to_have_count(0)
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
                )
            finally:
                context.close()
